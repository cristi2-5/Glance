"""Routes for books: cover analysis (async job) and the RAG summary."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, UploadFile, status

from app.api.deps import (
    CurrentUser,
    DataFetcherDep,
    DbSession,
    RagServiceDep,
    SessionFactory,
    VisionServiceDep,
)
from app.core.config import get_settings
from app.core.exceptions import FileTooLarge, ResourceNotFound, UnsupportedFileType
from app.models.book import Book
from app.models.job import Job, JobStatus
from app.schemas.job import JobCreated
from app.schemas.summary import BookSummary
from app.workers.cover_pipeline import process_cover

router = APIRouter(prefix="/books", tags=["books"])

ACCEPTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif"}


async def _read_and_validate(file: UploadFile) -> bytes:
    """Validates the MIME type and size of an uploaded image.

    Args:
        file: The file received in the multipart request.

    Returns:
        The raw content of the file, if it passes validation.

    Raises:
        UnsupportedFileType: If the MIME type is not JPEG, PNG, or HEIC/HEIF.
        FileTooLarge: If the file exceeds `max_upload_size_bytes`.
    """
    if file.content_type not in ACCEPTED_IMAGE_TYPES:
        raise UnsupportedFileType(
            f"Unsupported file type: {file.content_type!r}. "
            "Accepted: image/jpeg, image/png, image/heic, image/heif."
        )

    max_bytes = get_settings().max_upload_size_bytes
    content = await file.read()
    if len(content) > max_bytes:
        raise FileTooLarge(
            f"File exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MB."
        )
    return content


@router.post("/analyze-cover", status_code=status.HTTP_202_ACCEPTED, response_model=JobCreated)
async def analyze_cover(
    background_tasks: BackgroundTasks,
    db: DbSession,
    session_factory: SessionFactory,
    current_user: CurrentUser,
    vision_service: VisionServiceDep,
    data_fetcher: DataFetcherDep,
    file: Annotated[UploadFile, File()],
) -> JobCreated:
    """Receives a photo of a book cover and starts the asynchronous analysis.

    Creates a job in `pending` state, then hands it off to a background
    task that moves through `running` → `done`. The client immediately
    receives the job id and polls `GET /jobs/{job_id}`.

    Args:
        background_tasks: The FastAPI background task queue.
        db: The current database session.
        session_factory: The session factory passed to the background task.
        current_user: The authenticated user, owner of the job.
        vision_service: The vision service used to identify the cover.
        data_fetcher: The metadata/source fetcher used once the cover is
            recognized.
        file: The cover image (JPEG, PNG, or HEIC/HEIF, max 8 MB).

    Returns:
        The id of the newly created job.

    Raises:
        UnsupportedFileType: If the file type is not accepted.
        FileTooLarge: If the file exceeds the maximum size.
    """
    content = await _read_and_validate(file)

    job = Job(user_id=current_user.id, status=JobStatus.PENDING.value)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(
        process_cover, job.id, content, session_factory, vision_service, data_fetcher
    )
    return JobCreated(job_id=job.id)


@router.get("/{book_id}/summary", response_model=BookSummary)
async def read_book_summary(
    book_id: int,
    db: DbSession,
    current_user: CurrentUser,
    rag_service: RagServiceDep,
) -> BookSummary:
    """Returns the book's generated summary, with a citation per statement.

    Synchronous, unlike cover analysis, and deliberately so. The scan job
    is 30-120 s because it waits on vision and three external catalogs;
    this waits on one retrieval and one generation call, which is seconds
    on Groq. Putting it behind the job queue would mean the client polls
    to learn something it could have awaited — and would deny the result
    screen the thing it actually needs, which is to render the book
    immediately and fill the summary section in when it arrives.

    Generation happens on the first request for a book and is cached on
    the row afterwards. A summary written before the book's passages were
    last re-fetched is regenerated rather than served: its citations point
    at chunk ids that no longer exist.

    Args:
        book_id: The cached book to summarize (`AnalysisResult.book_id`).
        db: The current database session.
        current_user: The authenticated user.
        rag_service: The retrieval and synthesis service.

    Returns:
        The summary. `available=False` is a normal response, not an error:
        it means the book has no passages worth retrieving over, and the
        client falls back to the publisher's blurb.

    Raises:
        ResourceNotFound: If no cached book has this id.
        ExternalServiceUnavailable: If the local embedding model or the
            summary provider could not be reached. Surfaced as a 503 so
            the client can retry, rather than being cached as "no summary".
    """
    book = await db.get(Book, book_id)
    if book is None:
        raise ResourceNotFound("Book was not found.")

    return await rag_service.summarize(db, book)
