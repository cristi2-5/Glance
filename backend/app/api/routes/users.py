"""Routes about the current user: profile, library, stats, preferences."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession, RecommendationServiceDep
from app.core.config import get_settings
from app.models.library import LibraryEntry, ReadingStatus
from app.models.user import User
from app.schemas.library import LibraryEntryRead, LibraryPreferences, LibraryStats
from app.schemas.recommendation import RecommendationList
from app.schemas.user import UserPublic
from app.services import library_service

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


@router.get("/me/library", response_model=list[LibraryEntryRead])
async def read_own_library(
    db: DbSession,
    current_user: CurrentUser,
    status: Annotated[ReadingStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LibraryEntry]:
    """Returns the user's scanned and read books, most recent first.

    Args:
        db: The current database session.
        current_user: The authenticated user.
        status: Restrict to one reading status, or omit for all.
        limit: Maximum entries per page.
        offset: How many entries to skip.

    Returns:
        The matching entries. An empty list is the normal state of a new
        account, not an error — the client shows its "scan a book to get
        started" state.
    """
    return await library_service.list_entries(
        db, current_user.id, status=status, limit=limit, offset=offset
    )


@router.get("/me/stats", response_model=LibraryStats)
async def read_own_stats(db: DbSession, current_user: CurrentUser) -> LibraryStats:
    """Returns the profile counters, computed over the whole library.

    Server-side on purpose. The client cannot derive these from the list
    it holds: `/me/library` is paginated, so the length of a page is not
    the number of books, and a header that says "3 books scanned" while
    the account has forty is worse than no header.

    Args:
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The counters for this user.
    """
    return await library_service.compute_stats(db, current_user.id)


@router.get("/me/preferences", response_model=LibraryPreferences)
async def read_own_preferences(db: DbSession, current_user: CurrentUser) -> LibraryPreferences:
    """Returns favorite genres and authors, derived from the library.

    Derived rather than declared — see `library_service.derive_preferences`.
    `based_on=0` means nothing has been rated highly yet, and the lists
    are legitimately empty; the client asks for a rating instead of
    rendering invented tastes.

    Args:
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The derived preferences.
    """
    return await library_service.derive_preferences(db, current_user.id)


@router.get("/me/recommendations", response_model=RecommendationList)
async def read_own_recommendations(
    db: DbSession,
    current_user: CurrentUser,
    recommendation_service: RecommendationServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> RecommendationList:
    """Returns books this reader might like next, most similar first.

    Synchronous, like the summary endpoint and unlike cover analysis. The
    expensive half — asking the catalogs for new candidates and embedding
    them — runs at most once a day per reader and only when their derived
    preferences have moved; every other request is one vector average and
    one similarity search over a local index, which is milliseconds. Behind
    the job queue this would mean polling to learn something that was
    already computed.

    Args:
        db: The current database session.
        current_user: The authenticated user.
        recommendation_service: The profile and ranking service.
        limit: Maximum recommendations. Omitted means the configured
            default — expressed as `None` rather than as a sentinel `0`,
            which would have to be excluded from its own `ge=1` bound and
            would then 422 on the plain, unparameterised request.

    Returns:
        The recommendations, with `based_on` reporting how many liked books
        fed the profile. **An empty list is a normal `200`, never an
        error** — and the two empty cases are distinguishable: `based_on=0`
        means there is nothing to build a profile from yet, anything else
        means the catalogs had nothing new past this reader's own library.

    Raises:
        ExternalServiceUnavailable: If the local embedding model could not
            be reached. Surfaced as a 503 so the client can retry rather
            than rendering an outage as an empty shelf.
    """
    return await recommendation_service.recommend(
        db,
        current_user.id,
        limit=limit if limit is not None else get_settings().recommendation_default_limit,
    )
