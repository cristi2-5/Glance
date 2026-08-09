"""The full pipeline triggered when a cover is uploaded.

Module 2 only contains the asynchronous skeleton: a job's state moves
through `pending` → `running` → `done`, with a placeholder result. Modules
3-5 (vision, data fetcher, RAG) will replace the body of `process_cover`
with real OCR, metadata fetching, and synthesis, keeping the same
signature.
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job import Job, JobStatus

logger = structlog.get_logger(__name__)


async def process_cover(
    job_id: int,
    image_content: bytes,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Runs the cover analysis pipeline and updates the job's state.

    Opens its own database session from `session_factory` — the request
    session that created the job has already closed by the time the
    background task starts.

    Args:
        job_id: The id of the job to update.
        image_content: The raw content of the uploaded image.
        session_factory: The session factory to use (lets tests inject an
            isolated database instead of the production one).
    """
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("job_not_found_in_pipeline", job_id=job_id)
            return

        job.status = JobStatus.RUNNING.value
        await db.commit()

        job.result = {
            "message": "Placeholder pipeline — Module 3 adds real OCR and vision.",
            "image_size_bytes": len(image_content),
        }
        job.status = JobStatus.DONE.value
        await db.commit()
