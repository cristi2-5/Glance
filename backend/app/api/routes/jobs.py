"""Routes for querying and correcting the state of an asynchronous job."""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import AccessForbidden, InvalidData, ResourceNotFound
from app.models.job import Job, JobStatus
from app.schemas.job import JobPublic
from app.schemas.vision import CorrectionRequest

router = APIRouter(prefix="/jobs", tags=["jobs"])


async def _get_owned_job(job_id: int, db: DbSession, current_user: CurrentUser) -> Job:
    """Loads a job and verifies it belongs to the current user.

    Raises:
        ResourceNotFound: If no job exists with this id.
        AccessForbidden: If the job belongs to another user.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise ResourceNotFound("Job was not found.")
    if job.user_id != current_user.id:
        raise AccessForbidden("You do not have access to this job.")
    return job


@router.get("/{job_id}", response_model=JobPublic)
async def read_job(job_id: int, db: DbSession, current_user: CurrentUser) -> Job:
    """Returns the current state of a job, if it belongs to the current user.

    Args:
        job_id: The identifier of the requested job.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The job state (`pending`, `running`, `done`, or `failed`), plus
        the result or error, if available.

    Raises:
        ResourceNotFound: If no job exists with this id.
        AccessForbidden: If the job belongs to another user.
    """
    return await _get_owned_job(job_id, db, current_user)


@router.patch("/{job_id}/correction", response_model=JobPublic)
async def correct_job(
    job_id: int,
    correction: CorrectionRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Job:
    """Applies a manual title/author correction to a finished job's result.

    Used when the cover recognition confidence was low (`needs_review`) and
    the user overrides the guess by hand. Marks the result as `corrected`,
    with full confidence and `method="manual"`.

    Args:
        job_id: The identifier of the job to correct.
        correction: The corrected title/author.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The updated job.

    Raises:
        ResourceNotFound: If no job exists with this id.
        AccessForbidden: If the job belongs to another user.
        InvalidData: If the job has no result yet (not `done`).
    """
    job = await _get_owned_job(job_id, db, current_user)

    if job.status != JobStatus.DONE.value or job.result is None:
        raise InvalidData("Only a finished job's result can be corrected.")

    job.result = {
        **job.result,
        "title": correction.title,
        "author": correction.author,
        "confidence": 1.0,
        "method": "manual",
        "needs_review": False,
        "corrected": True,
    }
    await db.commit()
    await db.refresh(job)
    return job
