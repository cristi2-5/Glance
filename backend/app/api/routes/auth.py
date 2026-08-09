"""Authentication routes: register, login, refresh, logout."""

from datetime import datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.exceptions import InvalidCredentials, InvalidData
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue_tokens(db: DbSession, user: User) -> TokenResponse:
    """Issues a new pair of tokens (access + refresh) for a user.

    Args:
        db: The current database session.
        user: The user for whom the tokens are issued.

    Returns:
        The token pair, ready to send to the client.
    """
    raw_refresh_token, token_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            token_hash=token_hash,
            user_id=user.id,
            expires_at=refresh_token_expiry(),
        )
    )
    await db.commit()

    access_token = create_access_token(user.id)
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh_token)


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: DbSession) -> User:
    """Registers a new user.

    Args:
        payload: Plaintext email and password.
        db: The current database session.

    Returns:
        The newly created user (without password).

    Raises:
        InvalidData: If the email is already in use by another account.
    """
    existing_email = await db.scalar(select(User).where(User.email == payload.email))
    if existing_email is not None:
        raise InvalidData("Email is already registered.")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    """Authenticates a user and issues a pair of tokens.

    Args:
        payload: Plaintext email and password.
        db: The current database session.

    Returns:
        The token pair (access + refresh).

    Raises:
        InvalidCredentials: If the email doesn't exist, the password is
            wrong, or the account is deactivated.
    """
    user = await db.scalar(select(User).where(User.email == payload.email))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise InvalidCredentials("Incorrect email or password.")

    return await _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """Renews the token pair based on a valid refresh token.

    Applies rotation: the used refresh token is marked revoked, and the
    client receives a new one. An already revoked or expired refresh token
    rejects the request.

    Args:
        payload: The raw refresh token, as issued to the client.
        db: The current database session.

    Returns:
        A new pair of tokens (access + refresh).

    Raises:
        InvalidCredentials: If the token doesn't exist, is revoked, or has expired.
    """
    token_hash = hash_refresh_token(payload.refresh_token)
    stored_token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )

    if stored_token is None or stored_token.revoked or stored_token.expires_at < datetime.utcnow():
        raise InvalidCredentials("Refresh token invalid, revoked, or expired.")

    user = await db.get(User, stored_token.user_id)
    if user is None or not user.is_active:
        raise InvalidCredentials("User is no longer active.")

    stored_token.revoked = True
    await db.commit()

    return await _issue_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Revokes a refresh token, ending its associated session.

    Idempotent: if the token doesn't already exist (it was revoked or
    never existed), the operation still succeeds — no information about
    the token's validity is leaked.

    Args:
        payload: The raw refresh token to revoke.
        db: The current database session.
    """
    token_hash = hash_refresh_token(payload.refresh_token)
    stored_token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if stored_token is not None:
        stored_token.revoked = True
        await db.commit()
