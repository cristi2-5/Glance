"""Common FastAPI dependencies: database session, current user."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import InvalidCredentials
from app.core.security import JWTError, decode_access_token
from app.db.session import get_db, get_session_factory
from app.models.user import User
from app.services.data_fetcher import BookDataFetcher, build_data_fetcher
from app.services.rag_service import RagService, build_rag_service
from app.services.vision_service import VisionService, build_vision_service

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
VisionServiceDep = Annotated[VisionService, Depends(build_vision_service)]
DataFetcherDep = Annotated[BookDataFetcher, Depends(build_data_fetcher)]
RagServiceDep = Annotated[RagService, Depends(build_rag_service)]


async def current_user(
    db: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """Resolves the authenticated user from the access JWT.

    Args:
        db: The current database session.
        token: The access JWT extracted from the `Authorization: Bearer` header.

    Returns:
        The `User` instance corresponding to the token.

    Raises:
        InvalidCredentials: If the token is missing, invalid, expired, or
            no longer corresponds to an active user.
    """
    if token is None:
        raise InvalidCredentials("Missing access token.")

    try:
        user_id = decode_access_token(token)
    except (JWTError, ValueError) as exc:
        raise InvalidCredentials("Invalid or expired access token.") from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise InvalidCredentials("User is no longer active.")

    return user


CurrentUser = Annotated[User, Depends(current_user)]
