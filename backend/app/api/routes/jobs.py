"""Routes for querying and correcting the state of an asynchronous job."""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession, JobQueueDep
from app.core.exceptions import AccessForbidden, InvalidData, ResourceNotFound, StorageUnavailable
from app.models.job import Job, JobStatus
from app.schemas.job import JobPublic
from app.schemas.vision import CorrectionRequest
from app.services.storage import JobKind, JobMessage
from app.workers.cover_pipeline import EMPTY_BOOK_FIELDS

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
    job_queue: JobQueueDep,
) -> Job:
    """Applies a manual title/author correction to a finished job's result.

    Used when the cover recognition confidence was low (`needs_review`) and
    the user overrides the guess by hand. Marks the result as `corrected`,
    with full confidence and `method="manual"`.

    A correction usually means the recognized book was the *wrong* book, so
    the catalog data fetched for it — cover, blurb, categories, rating — no
    longer describes what the user is holding. Those fields are cleared
    immediately (so the client can never render another book's cover under
    a corrected title) and re-fetched by the queue worker. The job goes back
    to `running` for the duration, which the client's existing polling
    already handles.

    The corrected title and author are written to `job.result` here, so the
    queue message needs only `{"kind": "refresh", "job_id"}`.

    Args:
        job_id: The identifier of the job to correct.
        correction: The corrected title/author.
        db: The current database session.
        current_user: The authenticated user.
        job_queue: Where the refresh message is sent.

    Returns:
        The updated job, in `running` while its metadata is re-fetched.

    Raises:
        ResourceNotFound: If no job exists with this id.
        AccessForbidden: If the job belongs to another user.
        InvalidData: If the job has no result yet (not `done`).
        StorageUnavailable: If the refresh could not be queued. The job is
            put back to `done` (with the corrected title and no catalog
            data, a state the client already renders) so the PATCH can
            simply be retried.
    """
    job = await _get_owned_job(job_id, db, current_user)

    if job.status != JobStatus.DONE.value or job.result is None:
        raise InvalidData("Only a finished job's result can be corrected.")

    # Read before the reset below wipes it: the queue worker needs it to
    # discard the misidentified book's library entry.
    previous = job.result.get("book_id")
    previous_book_id = previous if isinstance(previous, int) else None

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

    try:
        await job_queue.send(
            JobMessage(kind=JobKind.REFRESH, job_id=job_id, previous_book_id=previous_book_id)
        )
    except StorageUnavailable:
        # Nothing will ever pick this job up: do not leave it `running`
        # forever. `done` keeps it correctable, so the client may retry.
        job.status = JobStatus.DONE.value
        await db.commit()
        raise
    return job
