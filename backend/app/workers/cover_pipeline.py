"""The full pipeline triggered when a cover is uploaded.

Module 3 replaces the Module 2 placeholder with real recognition:
`VisionService.identify` runs OCR (falling back to Moondream) and produces
`{title, author, confidence}`. The `result` dict also carries `summary`,
`cover_url`, `categories`, `average_rating`, and `reviews` as `None`/empty —
already matching the client's `AnalysisResult` shape — for Modules 4-5 to
populate.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import GlanceError
from app.models.job import Job, JobStatus
from app.services.vision_service import VisionService

logger = structlog.get_logger(__name__)


async def process_cover(
    job_id: int,
    image_content: bytes,
    session_factory: async_sessionmaker[AsyncSession],
    vision_service: VisionService,
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
        vision_service: The vision service used to identify the cover (lets
            tests inject a fake instead of running RapidOCR/Ollama).
    """
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("job_not_found_in_pipeline", job_id=job_id)
            return

        job.status = JobStatus.RUNNING.value
        await db.commit()

        try:
            identification = await vision_service.identify(image_content)
        except GlanceError as exc:
            logger.warning("cover_pipeline_failed", job_id=job_id, error=exc.message)
            job.status = JobStatus.FAILED.value
            job.error = exc.message
            await db.commit()
            return
        except Exception:
            logger.exception("cover_pipeline_unexpected_error", job_id=job_id)
            job.status = JobStatus.FAILED.value
            job.error = "An unexpected error occurred while analyzing the cover."
            await db.commit()
            return

        result: dict[str, Any] = {
            **identification.model_dump(),
            "summary": None,
            "cover_url": None,
            "categories": [],
            "average_rating": None,
            "reviews": [],
        }
        job.result = result
        job.status = JobStatus.DONE.value
        await db.commit()
