"""Rute de autentificare: register, login, refresh, logout."""

from datetime import datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.exceptions import CredentialeInvalide, DateInvalide
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


async def _emite_tokeni(db: DbSession, user: User) -> TokenResponse:
    """Emite o pereche nouă de token-uri (access + refresh) pentru un utilizator.

    Args:
        db: Sesiunea de bază de date curentă.
        user: Utilizatorul pentru care se emit token-urile.

    Returns:
        Perechea de token-uri, gata de trimis clientului.
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
    """Înregistrează un utilizator nou.

    Args:
        payload: Email și parolă în clar.
        db: Sesiunea de bază de date curentă.

    Returns:
        Utilizatorul nou creat (fără parolă).

    Raises:
        DateInvalide: Dacă emailul e deja folosit de alt cont.
    """
    email_existent = await db.scalar(select(User).where(User.email == payload.email))
    if email_existent is not None:
        raise DateInvalide("Emailul este deja înregistrat.")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    """Autentifică un utilizator și emite o pereche de token-uri.

    Args:
        payload: Email și parolă în clar.
        db: Sesiunea de bază de date curentă.

    Returns:
        Perechea de token-uri (access + refresh).

    Raises:
        CredentialeInvalide: Dacă emailul nu există, parola e greșită sau
            contul e dezactivat.
    """
    user = await db.scalar(select(User).where(User.email == payload.email))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise CredentialeInvalide("Email sau parolă incorectă.")

    return await _emite_tokeni(db, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """Reînnoiește perechea de token-uri pe baza unui refresh token valid.

    Aplică rotation: refresh tokenul folosit e marcat revocat, iar clientul
    primește unul nou. Un refresh token deja revocat sau expirat respinge
    cererea.

    Args:
        payload: Refresh tokenul brut, așa cum a fost emis clientului.
        db: Sesiunea de bază de date curentă.

    Returns:
        O pereche nouă de token-uri (access + refresh).

    Raises:
        CredentialeInvalide: Dacă tokenul nu există, e revocat sau a expirat.
    """
    token_hash = hash_refresh_token(payload.refresh_token)
    stored_token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )

    if stored_token is None or stored_token.revoked or stored_token.expires_at < datetime.utcnow():
        raise CredentialeInvalide("Refresh token invalid, revocat sau expirat.")

    user = await db.get(User, stored_token.user_id)
    if user is None or not user.is_active:
        raise CredentialeInvalide("Utilizatorul nu mai este activ.")

    stored_token.revoked = True
    await db.commit()

    return await _emite_tokeni(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Revocă un refresh token, încheind sesiunea asociată lui.

    Idempotentă: dacă tokenul nu există deja (a fost revocat sau nu a
    existat niciodată), operația tot reușește — nu se scurge informație
    despre validitatea tokenului.

    Args:
        payload: Refresh tokenul brut de revocat.
        db: Sesiunea de bază de date curentă.
    """
    token_hash = hash_refresh_token(payload.refresh_token)
    stored_token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if stored_token is not None:
        stored_token.revoked = True
        await db.commit()
