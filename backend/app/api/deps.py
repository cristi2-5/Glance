"""Dependențe FastAPI comune: sesiune de bază de date, utilizator curent."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import CredentialeInvalide
from app.core.security import JWTError, decode_access_token
from app.db.session import get_db, get_session_factory
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def utilizator_curent(
    db: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """Rezolvă utilizatorul autentificat din tokenul JWT de acces.

    Args:
        db: Sesiunea de bază de date curentă.
        token: JWT-ul de acces extras din header-ul `Authorization: Bearer`.

    Returns:
        Instanța `User` corespunzătoare tokenului.

    Raises:
        CredentialeInvalide: Dacă tokenul lipsește, e invalid, expirat sau
            nu mai corespunde unui utilizator activ.
    """
    if token is None:
        raise CredentialeInvalide("Token de acces lipsă.")

    try:
        user_id = decode_access_token(token)
    except (JWTError, ValueError) as exc:
        raise CredentialeInvalide("Token de acces invalid sau expirat.") from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise CredentialeInvalide("Utilizatorul nu mai este activ.")

    return user


UtilizatorCurent = Annotated[User, Depends(utilizator_curent)]
