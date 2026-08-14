"""The full pipeline triggered when a cover is uploaded.

Module 3 identifies the cover (`VisionService.identify` → `{title,
author, confidence}`). Module 4 then hands that pair to
`BookDataFetcher`, which serves the book from the SQLite cache or fetches
it from the three official sources on a miss.

The data-fetching stage is deliberately **non-fatal**. Recognition can
fail the job — with no title there is nothing to look up — but a slow or
absent catalog cannot: the job still completes, carrying the vision
reading and a `metadata_found=False` flag for the client to render.

The Module 5 summary is not produced here. It hangs off the *book*, not
the scan, and is generated on demand by `GET /books/{book_id}/summary` —
which keeps this pipeline as fast as recognition allows and lets the
client render the book before the summary is written.

Module 6 adds one step at the very end: the scan is recorded in the
user's library. It runs *after* the job has been committed and is
swallowed on failure, for the same reason the fetch stage is — the user
waited 30-120 s for a recognition, and a history row that failed to write
must not turn that into a failed scan.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import GlanceError
from app.models.book import Book
from app.models.job import Job, JobStatus
from app.services import library_service
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

        result: dict[str, Any] = {**identification.model_dump(), **EMPTY_BOOK_FIELDS}

        book = await _fetch_book_data(
            db, data_fetcher, job_id, identification.title, identification.author
        )
        if book is not None:
            result.update(book_fields(book))

        job.result = result
        job.status = JobStatus.DONE.value
        await db.commit()

        # After the job is finished, never before: the library entry is a
        # convenience, and failing to write it must not cost the user the
        # scan they waited 30-120 s for.
        if book is not None:
            await _record_in_library(db, job.user_id, book.id, job_id)


async def refresh_book_data(
    job_id: int,
    title: str,
    author: str | None,
    session_factory: async_sessionmaker[AsyncSession],
    data_fetcher: BookDataFetcher,
) -> None:
    """Re-fetches a corrected job's book data and completes the job.

    Runs after `PATCH /jobs/{id}/correction`. When the user overrides the
    recognized title, everything the catalog returned for the *previous*
    title — cover, blurb, categories, rating — describes the wrong book,
    so the correction endpoint clears those fields and schedules this to
    repopulate them.

    The job is left in `running` by the endpoint, so the client's existing
    polling picks the new data up without any special-casing.

    Args:
        job_id: The corrected job.
        title: The corrected title.
        author: The corrected author, when given.
        session_factory: The session factory to open a worker session from.
        data_fetcher: The fetcher to gather the corrected book's data with.
    """
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("job_not_found_in_refresh", job_id=job_id)
            return

        previous_book_id = (job.result or {}).get("book_id")
        book = await _fetch_book_data(db, data_fetcher, job_id, title, author)

        # Reset the catalog half unconditionally before repopulating it,
        # so nothing describing the previously recognized book survives a
        # correction that changed which book this is. `book_id` is part of
        # that reset, which is also what re-points the client at the right
        # book's summary.
        result: dict[str, Any] = {**(job.result or {}), **EMPTY_BOOK_FIELDS}
        if book is not None:
            result.update(book_fields(book))
        # A correction is the user's word: never let the catalog's spelling
        # overwrite what they typed, even when it matched a different book.
        result["title"] = title
        result["author"] = author

        job.result = result
        job.status = JobStatus.DONE.value
        await db.commit()

        # The first scan recorded the *misidentified* book in this user's
        # library. A correction says that book was never in their hands,
        # so it should not stay in their history — nor teach Module 6
        # anything. Only an untouched scan artifact is discarded; if they
        # rated or noted it in the meantime, it is theirs and it stays.
        new_book_id = book.id if book is not None else None
        if isinstance(previous_book_id, int) and previous_book_id != new_book_id:
            await _discard_library_artifact(db, job.user_id, previous_book_id, job_id)
        if book is not None:
            await _record_in_library(db, job.user_id, book.id, job_id)


async def _record_in_library(db: AsyncSession, user_id: int, book_id: int, job_id: int) -> None:
    """Records the scan in the user's library, swallowing any failure.

    Non-fatal by design, like the metadata fetch above it: the job has
    already completed and been committed, and a history row that failed
    to write is a smaller loss than a scan reported as failed.

    Args:
        db: The pipeline's database session.
        user_id: The user who scanned.
        book_id: The book the scan resolved to.
        job_id: The job id, for log correlation.
    """
    try:
        await library_service.record_scan(db, user_id, book_id)
    except Exception:
        logger.exception("library_record_failed", job_id=job_id, book_id=book_id)
        await db.rollback()


async def _discard_library_artifact(
    db: AsyncSession, user_id: int, book_id: int, job_id: int
) -> None:
    """Drops the superseded scan's library entry, swallowing any failure.

    Args:
        db: The pipeline's database session.
        user_id: The user whose library to clean.
        book_id: The previously recognized book.
        job_id: The job id, for log correlation.
    """
    try:
        await library_service.discard_scan_artifact(db, user_id, book_id)
    except Exception:
        logger.exception("library_artifact_discard_failed", job_id=job_id, book_id=book_id)
        await db.rollback()


# The catalog half of a job result, before anything has been fetched.
#
# No `summary` key: the generated summary is not part of a job result. It
# is fetched separately from `GET /books/{book_id}/summary`, keyed on the
# book rather than the scan, so it is shared between everyone who scans the
# same book and survives this job being re-run. A correction therefore
# needs no special handling for it either — the corrected title resolves to
# a different `book_id`, and the client asks for that book's summary.
EMPTY_BOOK_FIELDS: dict[str, Any] = {
    "book_id": None,
    "metadata_found": False,
    "description": None,
    "cover_url": None,
    "categories": [],
    "average_rating": None,
    "ratings_count": None,
    "source_count": 0,
}


def book_fields(book: Book) -> dict[str, Any]:
    """Maps a cached book onto the catalog half of a job result.

    Args:
        book: The cached book.

    Returns:
        The fields to merge into `job.result`. `book_id` is the one the
        client needs most after the visible fields: it is the key for
        `GET /books/{book_id}/summary`.
    """
    return {
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
