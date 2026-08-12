"""Routes for querying and correcting the state of an asynchronous job."""

from fastapi import APIRouter, BackgroundTasks

from app.api.deps import CurrentUser, DataFetcherDep, DbSession, SessionFactory
from app.core.exceptions import AccessForbidden, InvalidData, ResourceNotFound
from app.models.job import Job, JobStatus
from app.schemas.job import JobPublic
from app.schemas.vision import CorrectionRequest
from app.workers.cover_pipeline import EMPTY_BOOK_FIELDS, refresh_book_data

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
    background_tasks: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
    session_factory: SessionFactory,
    data_fetcher: DataFetcherDep,
) -> Job:
    """Applies a manual title/author correction to a finished job's result.

    Used when the cover recognition confidence was low (`needs_review`) and
    the user overrides the guess by hand. Marks the result as `corrected`,
    with full confidence and `method="manual"`.

    A correction usually means the recognized book was the *wrong* book, so
    the catalog data fetched for it — cover, blurb, categories, rating — no
    longer describes what the user is holding. Those fields are cleared
    immediately (so the client can never render another book's cover under
    a corrected title) and re-fetched in the background. The job goes back
    to `running` for the duration, which the client's existing polling
    already handles.

    Args:
        job_id: The identifier of the job to correct.
        correction: The corrected title/author.
        background_tasks: The FastAPI background task queue.
        db: The current database session.
        current_user: The authenticated user.
        session_factory: The session factory passed to the background task.
        data_fetcher: The fetcher used to re-gather the corrected book.

    Returns:
        The updated job, in `running` while its metadata is re-fetched.

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
        **EMPTY_BOOK_FIELDS,
        "title": correction.title,
        "author": correction.author,
        "confidence": 1.0,
        "method": "manual",
        "needs_review": False,
        "corrected": True,
    }
    job.status = JobStatus.RUNNING.value
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(
        refresh_book_data,
        job_id,
        correction.title,
        correction.author,
        session_factory,
        data_fetcher,
    )
    return job
