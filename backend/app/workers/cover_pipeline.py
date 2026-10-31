"""The full pipeline triggered when a cover is uploaded.

Module 3 identifies the cover (`VisionService.identify` → `{title,
author, confidence}`). Module 4 then hands that pair to
`BookDataFetcher`, which serves the book from the database cache or fetches
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

Cloud step 4 moved this module's callers: it no longer runs inside the api
process but in the queue worker (`app/workers/queue_worker.py`), which loads
the photo from Blob Storage and hands the bytes in. The scan photo's
lifecycle is handled here, next to the job state it follows: a `failed` job
deletes its photo, a `done` one hands it to the reader's library entry (see
`library_service.attach_photo` for the rule), and a correction moves it to
the corrected book's entry.
"""

import time
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics, telemetry
from app.core.exceptions import CoverNotRecognized, GlanceError, StorageUnavailable
from app.models.book import Book
from app.models.job import Job, JobStatus
from app.services import library_service
from app.services.data_fetcher import BookDataFetcher
from app.services.storage import BlobStore
from app.services.vision_service import VisionService

logger = structlog.get_logger(__name__)


async def process_cover(
    job_id: int,
    image_content: bytes,
    session_factory: async_sessionmaker[AsyncSession],
    vision_service: VisionService,
    data_fetcher: BookDataFetcher | None = None,
    blob_store: BlobStore | None = None,
) -> None:
    """Runs the cover analysis pipeline and updates the job's state.

    Opens its own database session from `session_factory` — the request
    session that created the job closed long before the queue worker took
    the message.

    Safe to run twice for the same job (the queue delivers at least once):
    the book fetch is cached and the library write is unique per user and
    book.

    Args:
        job_id: The id of the job to update.
        image_content: The (already normalised) cover image bytes.
        session_factory: The session factory to use (lets tests inject an
            isolated database instead of the production one).
        vision_service: The vision service used to identify the cover (lets
            tests inject a fake instead of running RapidOCR/Ollama).
        data_fetcher: The metadata/source fetcher. `None` skips the fetch
            stage entirely and completes the job on vision data alone.
        blob_store: Where the job's photo lives. `None` skips the photo
            lifecycle entirely (jobs without a photo).
    """
    started = time.monotonic()
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
            await fail_job(db, job, exc.message, blob_store)
            # An unreadable or non-book photo is a normal outcome, not a fault:
            # counted apart so the failure-rate alert does not fire on it.
            outcome = "unrecognized" if isinstance(exc, CoverNotRecognized) else "failed"
            metrics.record_scan(outcome, "none", "scan", time.monotonic() - started)
            return
        except Exception:
            logger.exception("cover_pipeline_unexpected_error", job_id=job_id)
            await fail_job(
                db, job, "An unexpected error occurred while analyzing the cover.", blob_store
            )
            metrics.record_scan("failed", "none", "scan", time.monotonic() - started)
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
        metrics.record_scan("done", identification.method, "scan", time.monotonic() - started)

        # After the job is finished, never before: the library entry is a
        # convenience, and failing to write it must not cost the user the
        # scan they waited 30-120 s for.
        #
        # With no resolved book (the catalog stage blew up) there is no entry
        # to own the photo, so it stays on the job: a correction can still
        # attach it to the book the reader names.
        if book is not None:
            await _record_in_library(db, job.user_id, book.id, job_id, job.photo_blob, blob_store)


async def fail_job(
    db: AsyncSession, job: Job, message: str, blob_store: BlobStore | None = None
) -> None:
    """Marks a job `failed` and deletes its photo, unless a library entry owns it.

    A scan that failed has no book to attach its photo to, and nothing
    would ever reference the blob again, so it is removed together with the
    job's pointer. The database change is committed first: a blob that then
    fails to delete is merely an unreferenced file, never a dangling pointer.

    **A pointer never dangles.** If a library entry still points at the same
    blob (a job failing after its photo was already handed over), the blob
    is kept: only the job's own pointer is dropped.

    Args:
        db: The session holding `job`.
        job: The job to fail.
        message: The user-facing error, stored on the job.
        blob_store: Where the photo lives, or `None` to leave storage alone.
    """
    # A failed job returns normally, so without this its trace would read as a
    # success. No-op outside a recording span.
    telemetry.mark_error("job failed")
    photo = job.photo_blob
    job.status = JobStatus.FAILED.value
    job.error = message
    job.photo_blob = None
    await db.commit()
    if photo is not None and await library_service.photo_is_owned(db, photo):
        logger.info("photo_kept_for_library_entry", job_id=job.id, blob=photo)
        return
    await delete_photo_blob(blob_store, photo, job.id)


async def delete_photo_blob(
    blob_store: BlobStore | None, name: str | None, job_id: int | None = None
) -> None:
    """Deletes a photo blob, logging instead of raising when storage is down.

    Cleanup is best-effort by design: the database already stopped pointing
    at the blob, and a storage outage must not fail or re-run a job that
    otherwise finished. The worst case is one orphaned ~50 KB JPEG.

    Args:
        blob_store: The store, or `None` (nothing to do).
        name: The blob to delete, or `None` (nothing to do).
        job_id: The job id, for log correlation (`None` outside a job).
    """
    if blob_store is None or name is None:
        return
    try:
        await blob_store.delete(name)
    except StorageUnavailable:
        logger.warning("photo_blob_cleanup_failed", job_id=job_id, blob=name)


async def refresh_book_data(
    job_id: int,
    title: str,
    author: str | None,
    session_factory: async_sessionmaker[AsyncSession],
    data_fetcher: BookDataFetcher,
    blob_store: BlobStore | None = None,
    previous_book_id: int | None = None,
) -> None:
    """Re-fetches a corrected job's book data and completes the job.

    Runs in the queue worker after `PATCH /jobs/{id}/correction`. When the
    user overrides the recognized title, everything the catalog returned for
    the *previous* title — cover, blurb, categories, rating — describes the
    wrong book, so the correction endpoint clears those fields and enqueues
    this to repopulate them.

    The job is left in `running` by the endpoint, so the client's existing
    polling picks the new data up without any special-casing.

    Args:
        job_id: The corrected job.
        title: The corrected title.
        author: The corrected author, when given.
        session_factory: The session factory to open a queue worker session from.
        data_fetcher: The fetcher to gather the corrected book's data with.
        blob_store: Where the job's photo lives. `None` skips the photo
            lifecycle entirely.
        previous_book_id: The book the scan was first identified as. Passed
            in, not read from `job.result`: the endpoint has already reset
            `book_id` there, which is why reading it here silently never
            discarded the misidentified entry (a bug since Module 6a).
    """
    started = time.monotonic()
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("job_not_found_in_refresh", job_id=job_id)
            return

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
        metrics.record_scan("done", "manual", "refresh", time.monotonic() - started)

        # The first scan recorded the *misidentified* book in this user's
        # library. A correction says that book was never in their hands,
        # so it should not stay in their history — nor teach Module 6
        # anything. Only an untouched scan artifact is discarded; if they
        # rated or noted it in the meantime, it is theirs and it stays.
        #
        # The photo moves with the job: it shows the book in the reader's
        # hands, which the correction says is the *new* book. It leaves the
        # misidentified entry first (without being deleted — it is about to
        # be attached to the corrected entry below), then the artifact entry
        # is discarded.
        new_book_id = book.id if book is not None else None
        if isinstance(previous_book_id, int) and previous_book_id != new_book_id:
            await _discard_library_artifact(
                db, job.user_id, previous_book_id, job_id, job.photo_blob, blob_store
            )
        if book is not None:
            await _record_in_library(db, job.user_id, book.id, job_id, job.photo_blob, blob_store)


async def _record_in_library(
    db: AsyncSession,
    user_id: int,
    book_id: int,
    job_id: int,
    photo_blob: str | None = None,
    blob_store: BlobStore | None = None,
) -> None:
    """Records the scan in the user's library, swallowing any failure.

    Also hands the scan's photo to the entry; a photo it replaces (the
    reader's previous photo of the same book) is deleted from storage.

    Non-fatal by design, like the metadata fetch above it: the job has
    already completed and been committed, and a history row that failed
    to write is a smaller loss than a scan reported as failed.

    Args:
        db: The pipeline's database session.
        user_id: The user who scanned.
        book_id: The book the scan resolved to.
        job_id: The job id, for log correlation.
        photo_blob: The job's photo, to attach to the entry (if any).
        blob_store: Where photos live, for deleting a replaced one.
    """
    try:
        await library_service.record_scan(db, user_id, book_id)
        if photo_blob is not None:
            replaced = await library_service.attach_photo(db, user_id, book_id, photo_blob)
            await delete_photo_blob(blob_store, replaced, job_id)
    except Exception:
        logger.exception("library_record_failed", job_id=job_id, book_id=book_id)
        await db.rollback()


async def _discard_library_artifact(
    db: AsyncSession,
    user_id: int,
    book_id: int,
    job_id: int,
    moving_photo: str | None = None,
    blob_store: BlobStore | None = None,
) -> None:
    """Drops the superseded scan's library entry, swallowing any failure.

    Args:
        db: The pipeline's database session.
        user_id: The user whose library to clean.
        book_id: The previously recognized book.
        job_id: The job id, for log correlation.
        moving_photo: The job's photo, which is moving to the corrected
            book's entry: it is detached from the old entry and never
            deleted here.
        blob_store: Where photos live. If the discarded entry held some
            *other* (older) photo, that one is deleted with it.
    """
    try:
        entry_photo = await library_service.entry_photo(db, user_id, book_id)
        if entry_photo is not None and entry_photo == moving_photo:
            await library_service.detach_photo(db, user_id, book_id, entry_photo)
            entry_photo = None
        discarded = await library_service.discard_scan_artifact(db, user_id, book_id)
        if discarded:
            await delete_photo_blob(blob_store, entry_photo, job_id)
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
        with telemetry.span("scan.fetch_sources", {"glance.job_id": job_id}):
            return await data_fetcher.fetch(db, title, author)
    except Exception:
        # Deliberately broad: whatever went wrong downstream — a driver
        # error, a source bug — the job still completes with vision data.
        logger.exception("book_data_fetch_failed", job_id=job_id, title=title)
        await db.rollback()
        return None
