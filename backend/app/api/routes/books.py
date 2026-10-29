"""Routes for books: cover analysis, the RAG summary, the library entry, the journal."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status

from app.api.deps import BlobStoreDep, CurrentUser, DbSession, JobQueueDep, RagServiceDep
from app.core.config import get_settings
from app.core.exceptions import (
    FileTooLarge,
    ResourceNotFound,
    StorageUnavailable,
    UnsupportedFileType,
)
from app.models.book import Book
from app.models.job import Job, JobStatus
from app.models.journal import JournalEntry
from app.models.library import LibraryEntry
from app.schemas.job import JobCreated
from app.schemas.library import (
    JournalEntryRead,
    JournalEntryWrite,
    LibraryEntryRead,
    LibraryEntryUpdate,
)
from app.schemas.summary import BookSummary
from app.services import journal_service, library_service
from app.services.image_preprocessing import preprocess_cover
from app.services.storage import JobKind, JobMessage
from app.workers.cover_pipeline import delete_photo_blob, fail_job

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
    db: DbSession,
    current_user: CurrentUser,
    blob_store: BlobStoreDep,
    job_queue: JobQueueDep,
    file: Annotated[UploadFile, File()],
) -> JobCreated:
    """Receives a photo of a book cover and queues the asynchronous analysis.

    Normalises the photo once (EXIF rotation, 768 px, JPEG — the same
    `preprocess_cover` vision uses, so the stored file displays on every
    phone, unlike a raw HEIC), stores it in the private blob container and
    puts a `{"kind": "scan", "job_id"}` message on the queue. The queue
    worker does the rest; the client immediately receives the job id and
    polls `GET /jobs/{job_id}`.

    **The order is deliberate.** The `pending` job is committed *first*: a
    fast queue worker must never dequeue a message for a job that is not in
    the database yet. Then the upload, then the enqueue — so a message only
    ever points at a photo that exists. If either storage step fails, the
    job is marked `failed` (and the photo removed) before the 503, so
    nothing is left half-done.

    Args:
        db: The current database session.
        current_user: The authenticated user, owner of the job.
        blob_store: Where the photo is stored.
        job_queue: Where the job message is sent.
        file: The cover image (JPEG, PNG, or HEIC/HEIF, max 8 MB).

    Returns:
        The id of the newly created job.

    Raises:
        UnsupportedFileType: If the file type is not accepted.
        FileTooLarge: If the file exceeds the maximum size.
        ImageProcessingFailed: If the file is not a decodable image.
        StorageUnavailable: If the photo could not be stored or the job
            could not be queued (the job is left `failed`).
    """
    content = await _read_and_validate(file)
    # CPU-bound (decode, resize, re-encode): keep it off the event loop.
    prepared = await asyncio.to_thread(preprocess_cover, content)

    # Unguessable and never reused, so a blob name identifies one upload.
    photo_blob = f"{uuid.uuid4().hex}.jpg"
    job = Job(user_id=current_user.id, status=JobStatus.PENDING.value, photo_blob=photo_blob)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        await blob_store.put(photo_blob, prepared.jpeg_bytes)
        await job_queue.send(JobMessage(kind=JobKind.SCAN, job_id=job.id))
    except StorageUnavailable:
        await fail_job(db, job, "The scan could not be queued. Please try again.", blob_store)
        raise
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


@router.get("/{book_id}/library", response_model=LibraryEntryRead)
async def read_library_entry(
    book_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> LibraryEntry:
    """Returns what the current user has recorded about this book.

    Args:
        book_id: The book to look up.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The user's entry for the book.

    Raises:
        ResourceNotFound: If this user has no entry for this book. The
            404 is about *their* entry, never the book — another user
            having one must be indistinguishable from nobody having one.
    """
    return await library_service.get_entry(db, current_user.id, book_id)


@router.get("/{book_id}/library/photo")
async def read_library_photo(
    book_id: int,
    db: DbSession,
    current_user: CurrentUser,
    blob_store: BlobStoreDep,
) -> Response:
    """Returns the current user's own photo of this book, as a JPEG.

    The photo container is private and there are no public or SAS links:
    the api is the only reader, and it resolves the blob **through the
    user's library entry**, the same ownership rule as the journal. Another
    reader's entry for the same book simply does not match, so their photo
    is unreachable and indistinguishable from none.

    Args:
        book_id: The book whose photo to return.
        db: The current database session.
        current_user: The authenticated user.
        blob_store: Where the photo is stored.

    Returns:
        The image, cacheable by this user's client only (`private`).

    Raises:
        ResourceNotFound: If this user has no entry for the book, or the
            entry has no photo.
        StorageUnavailable: If the storage service could not be reached.
    """
    entry = await library_service.get_entry(db, current_user.id, book_id)
    if entry.photo_blob is None:
        raise ResourceNotFound("You have no photo of this book.")

    data = await blob_store.get(entry.photo_blob)
    max_age = get_settings().scan_photo_cache_seconds
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": f"private, max-age={max_age}"},
    )


@router.put("/{book_id}/library", response_model=LibraryEntryRead)
async def upsert_library_entry(
    book_id: int,
    update: LibraryEntryUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> LibraryEntry:
    """Sets the reading status or rating for a book.

    A single endpoint rather than one per field: they are two columns of
    the same row. The reader's own writing is not here — it lives in the
    journal, as dated notes (`POST /books/{id}/journal`).

    Only the fields present in the body are applied — setting a status
    does not clear a rating. Sending `"rating": null` explicitly clears
    it. See `LibraryEntryUpdate`.

    Args:
        book_id: The book to record against.
        update: The fields to change.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The updated entry, created first if the user had none.

    Raises:
        ResourceNotFound: If no cached book has this id.
    """
    return await library_service.update_entry(db, current_user.id, book_id, update)


@router.delete("/{book_id}/library", status_code=status.HTTP_204_NO_CONTENT)
async def remove_library_entry(
    book_id: int,
    db: DbSession,
    current_user: CurrentUser,
    blob_store: BlobStoreDep,
) -> Response:
    """Removes a book from the current user's library.

    The cached `Book` row itself is untouched: it is shared by everyone
    who scans the same cover, and deleting it would throw away the
    fetched sources and generated summary that other libraries point at.
    The reader's own photo of the book is theirs, and goes with the entry.

    Args:
        book_id: The book to remove from this library.
        db: The current database session.
        current_user: The authenticated user.
        blob_store: Where the entry's photo is stored.

    Returns:
        An empty `204` response.

    Raises:
        ResourceNotFound: If this user has no entry for this book.
    """
    photo = await library_service.delete_entry(db, current_user.id, book_id)
    await delete_photo_blob(blob_store, photo)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{book_id}/journal", response_model=list[JournalEntryRead])
async def read_journal(
    book_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> list[JournalEntry]:
    """Returns the reader's notes about this book, oldest first.

    Chronological because this is a journal: its value is watching an
    opinion move between chapter three and the last page.

    Args:
        book_id: The book whose journal to read.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The notes. An empty list is the normal state of a new book.

    Raises:
        ResourceNotFound: If this book is not in the reader's library.
    """
    return await journal_service.list_entries(db, current_user.id, book_id)


@router.post(
    "/{book_id}/journal",
    status_code=status.HTTP_201_CREATED,
    response_model=JournalEntryRead,
)
async def add_journal_entry(
    book_id: int,
    note: JournalEntryWrite,
    db: DbSession,
    current_user: CurrentUser,
) -> JournalEntry:
    """Writes a new note into this book's journal.

    Args:
        book_id: The book being written about.
        note: The note text.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The created note.

    Raises:
        ResourceNotFound: If no cached book has this id.
        InvalidData: If the note is blank once trimmed.
    """
    return await journal_service.add_entry(db, current_user.id, book_id, note.content)


@router.patch("/{book_id}/journal/{entry_id}", response_model=JournalEntryRead)
async def edit_journal_entry(
    book_id: int,
    entry_id: int,
    note: JournalEntryWrite,
    db: DbSession,
    current_user: CurrentUser,
) -> JournalEntry:
    """Rewrites one note. Only `updated_at` moves, never `created_at`.

    Args:
        book_id: The book the note belongs to.
        entry_id: The note to rewrite.
        note: The replacement text.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        The updated note.

    Raises:
        ResourceNotFound: If the book is not in the reader's library, or
            the note does not hang off it.
        InvalidData: If the replacement is blank once trimmed.
    """
    return await journal_service.edit_entry(db, current_user.id, book_id, entry_id, note.content)


@router.delete("/{book_id}/journal/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_journal_entry(
    book_id: int,
    entry_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Removes one note from this book's journal.

    Args:
        book_id: The book the note belongs to.
        entry_id: The note to remove.
        db: The current database session.
        current_user: The authenticated user.

    Returns:
        An empty `204` response.

    Raises:
        ResourceNotFound: If the book is not in the reader's library, or
            the note does not hang off it.
    """
    await journal_service.delete_entry(db, current_user.id, book_id, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
