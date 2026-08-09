"""Route for querying the state of an asynchronous job."""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import AccessForbidden, ResourceNotFound
from app.models.job import Job
from app.schemas.job import JobPublic

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
    job = await db.get(Job, job_id)
    if job is None:
        raise ResourceNotFound("Job was not found.")
    if job.user_id != current_user.id:
        raise AccessForbidden("You do not have access to this job.")
    return job
