"""Routes about the current user."""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.models.user import User
from app.schemas.user import UserPublic

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
async def read_own_profile(current_user: CurrentUser) -> User:
    """Returns the profile of the authenticated user.

    Args:
        current_user: The user resolved from the access token.

    Returns:
        The public profile of the current user.
    """
    return current_user
