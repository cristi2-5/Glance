"""Rute despre utilizatorul curent."""

from fastapi import APIRouter

from app.api.deps import UtilizatorCurent
from app.models.user import User
from app.schemas.user import UserPublic

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
async def citeste_profil_propriu(utilizator_curent: UtilizatorCurent) -> User:
    """Returnează profilul utilizatorului autentificat.

    Args:
        utilizator_curent: Utilizatorul rezolvat din tokenul de acces.

    Returns:
        Profilul public al utilizatorului curent.
    """
    return utilizator_curent
