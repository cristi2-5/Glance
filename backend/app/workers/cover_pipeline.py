"""The full pipeline triggered when a cover is uploaded.

Module 3 identifies the cover (`VisionService.identify` → `{title,
author, confidence}`). Module 4 then hands that pair to
`BookDataFetcher`, which serves the book from the SQLite cache or fetches
it from the three official sources on a miss.

The data-fetching stage is deliberately **non-fatal**. Recognition can
fail the job — with no title there is nothing to look up — but a slow or
absent catalog cannot: the job still completes, carrying the vision
reading and a `metadata_found=False` flag for the client to render. The
`summary` field stays `None` until Module 5 fills it.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import GlanceError
from app.models.book import Book
from app.models.job import Job, JobStatus
from app.services.data_fetcher import BookDataFetcher
from app.services.vision_service import VisionService

logger = structlog.get_logger(__name__)


async def process_cover(
    job_id: int,
    image_content: bytes,
    session_factory: async_sessionmaker[AsyncSession],
    vision_service: VisionService,
    data_fetcher: BookDataFetcher | None = None,
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
        data_fetcher: The metadata/source fetcher. `None` skips the fetch
            stage entirely and completes the job on vision data alone.
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
            "book_id": None,
            "metadata_found": False,
            "description": None,
            "cover_url": None,
            "categories": [],
            "average_rating": None,
            "ratings_count": None,
            "source_count": 0,
        }

        book = await _fetch_book_data(
            db, data_fetcher, job_id, identification.title, identification.author
        )
        if book is not None:
            result.update(
                {
                    "title": book.title,
                    "author": book.author,
                    "book_id": book.id,
                    "metadata_found": book.metadata_found,
                    "description": book.description,
                    "cover_url": book.cover_url,
                    "categories": book.categories or [],
                    "average_rating": book.average_rating,
                    "ratings_count": book.ratings_count,
                    "source_count": len(book.text_sources),
                }
            )

        job.result = result
        job.status = JobStatus.DONE.value
        await db.commit()


async def _fetch_book_data(
    db: AsyncSession,
    data_fetcher: BookDataFetcher | None,
    job_id: int,
    title: str,
    author: str | None,
) -> Book | None:
    """Fetches cached or fresh book data, swallowing any failure.

    A metadata lookup that fails must not fail the job: the user has a
    recognized cover either way, and an empty description is a state the
    client renders. Errors are logged, never raised.

    Args:
        db: The pipeline's database session.
        data_fetcher: The fetcher, or `None` to skip the stage.
        job_id: The job id, for log correlation.
        title: The recognized title.
        author: The recognized author, when any.

    Returns:
        The cached `Book`, or `None` if fetching was skipped or failed.
    """
    if data_fetcher is None or not title.strip():
        return None

    try:
        return await data_fetcher.fetch(db, title, author)
    except Exception:
        # Deliberately broad: whatever went wrong downstream — a driver
        # error, a source bug — the job still completes with vision data.
        logger.exception("book_data_fetch_failed", job_id=job_id, title=title)
        await db.rollback()
        return None
