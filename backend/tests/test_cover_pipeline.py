"""Tests for `app.workers.cover_pipeline.process_cover`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import CoverNotRecognized
from app.models.book import SourceKind, SourceName
from app.models.job import Job, JobStatus
from app.models.library import LibraryEntry, ReadingStatus
from app.schemas.library import LibraryEntryUpdate
from app.schemas.vision import CoverIdentification
from app.services import library_service
from app.services.data_fetcher import BookDataFetcher
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult
from app.workers.cover_pipeline import process_cover, refresh_book_data
from tests.fakes import FakeContentSource


class _FakeVisionService:
    """A `VisionService` stand-in that returns or raises a fixed outcome."""

    def __init__(
        self,
        identification: CoverIdentification | None = None,
        error: Exception | None = None,
    ) -> None:
        self._identification = identification
        self._error = error

    async def identify(self, image_bytes: bytes) -> CoverIdentification:
        if self._error is not None:
            raise self._error
        assert self._identification is not None
        return self._identification


async def _create_pending_job(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as db:
        job = Job(user_id=1, status=JobStatus.PENDING.value)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def test_process_cover_marks_job_done_with_identification(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _create_pending_job(db_session_factory)
    identification = CoverIdentification(
        title="Dune",
        author="Frank Herbert",
        confidence=0.95,
        method="ocr",
        needs_review=False,
    )
    vision_service = _FakeVisionService(identification=identification)

    await process_cover(job_id, b"fake-image-bytes", db_session_factory, vision_service)  # type: ignore[arg-type]

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.DONE.value
        assert job.result is not None
        assert job.result["title"] == "Dune"
        assert job.result["author"] == "Frank Herbert"
        assert job.result["confidence"] == 0.95
        # The generated summary is not part of a job result — it hangs off
        # the book and is fetched from `/books/{book_id}/summary`.
        assert "summary" not in job.result
        assert job.result["categories"] == []
        assert job.error is None


async def test_process_cover_marks_job_failed_on_domain_error(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _create_pending_job(db_session_factory)
    vision_service = _FakeVisionService(
        error=CoverNotRecognized("Could not identify the book from the cover image.")
    )

    await process_cover(job_id, b"fake-image-bytes", db_session_factory, vision_service)  # type: ignore[arg-type]

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.error == "Could not identify the book from the cover image."


async def test_process_cover_marks_job_failed_on_unexpected_error(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _create_pending_job(db_session_factory)
    vision_service = _FakeVisionService(error=RuntimeError("boom"))

    await process_cover(job_id, b"fake-image-bytes", db_session_factory, vision_service)  # type: ignore[arg-type]

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.error is not None


# --- Module 4: the data-fetching stage --------------------------------------


def _identified_dune() -> _FakeVisionService:
    """A vision service that always recognizes Dune."""
    return _FakeVisionService(
        identification=CoverIdentification(
            title="Dune",
            author="Frank Herbert",
            confidence=0.95,
            method="ocr",
            needs_review=False,
        )
    )


async def test_process_cover_enriches_the_result_with_fetched_metadata(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _create_pending_job(db_session_factory)
    fetcher = BookDataFetcher(
        sources=[
            FakeContentSource(
                SourceName.GOOGLE_BOOKS,
                SourceResult(
                    source=SourceName.GOOGLE_BOOKS,
                    metadata=BookMetadata(
                        title="Dune",
                        author="Frank Herbert",
                        description="A desert planet.",
                        categories=["Fiction"],
                        cover_url="https://books.test/dune.jpg",
                        average_rating=4.5,
                    ),
                    passages=[
                        SourcePassage(kind=SourceKind.DESCRIPTION, content="A desert planet.")
                    ],
                ),
            )
        ],
        settings=get_settings(),
    )

    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), fetcher  # type: ignore[arg-type]
    )

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        assert job.status == JobStatus.DONE.value
        assert job.result["metadata_found"] is True
        assert job.result["description"] == "A desert planet."
        assert job.result["cover_url"] == "https://books.test/dune.jpg"
        assert job.result["categories"] == ["Fiction"]
        assert job.result["average_rating"] == 4.5
        assert job.result["book_id"] is not None
        assert job.result["source_count"] == 1


async def test_process_cover_completes_when_no_catalog_matched(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The "metadata not found" state: the job must still succeed, carrying
    # the vision reading and a flag the client can render.
    job_id = await _create_pending_job(db_session_factory)
    fetcher = BookDataFetcher(
        sources=[FakeContentSource(SourceName.GOOGLE_BOOKS)], settings=get_settings()
    )

    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), fetcher  # type: ignore[arg-type]
    )

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        assert job.status == JobStatus.DONE.value
        assert job.result["metadata_found"] is False
        assert job.result["description"] is None
        assert job.result["title"] == "Dune", "vision reading is preserved"
        assert job.error is None


async def test_process_cover_survives_a_data_fetcher_that_explodes(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A metadata failure must never fail a job whose cover was recognized.
    class _ExplodingFetcher:
        async def fetch(self, db: AsyncSession, title: str, author: str | None = None) -> None:
            raise RuntimeError("database on fire")

    job_id = await _create_pending_job(db_session_factory)

    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), _ExplodingFetcher()  # type: ignore[arg-type]
    )

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        assert job.status == JobStatus.DONE.value
        assert job.result["title"] == "Dune"
        assert job.result["metadata_found"] is False


def _dune_fetcher() -> BookDataFetcher:
    """A fetcher scripted to resolve Dune from a single catalog."""
    return BookDataFetcher(
        sources=[
            FakeContentSource(
                SourceName.GOOGLE_BOOKS,
                SourceResult(
                    source=SourceName.GOOGLE_BOOKS,
                    metadata=BookMetadata(
                        title="Dune",
                        author="Frank Herbert",
                        description="A desert planet.",
                        categories=["Fiction"],
                    ),
                    passages=[
                        SourcePassage(kind=SourceKind.DESCRIPTION, content="A desert planet.")
                    ],
                ),
            )
        ],
        settings=get_settings(),
    )


async def test_process_cover_records_the_scan_in_the_library(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A completed scan lands in the user's history without being asked.

    Nobody hand-curates a library in an app whose whole purpose is
    photographing covers. If the pipeline does not write this row, the
    history stays empty forever and every counter derived from it reads
    zero on an account that has scanned dozens of books.
    """
    job_id = await _create_pending_job(db_session_factory)

    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), _dune_fetcher()  # type: ignore[arg-type]
    )

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None

        entries = list(await db.scalars(select(LibraryEntry)))
        assert len(entries) == 1
        assert entries[0].user_id == job.user_id
        assert entries[0].book_id == job.result["book_id"]
        assert entries[0].status == ReadingStatus.SCANNED.value
        assert entries[0].scan_count == 1


async def test_scanning_the_same_cover_twice_leaves_one_history_entry(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two scans of one cover are one book in the history, not two.

    The end-to-end form of the unique constraint: it has to survive the
    pipeline, which opens its own session per run and cannot see the
    previous one's identity map.
    """
    fetcher = _dune_fetcher()

    for _ in range(2):
        job_id = await _create_pending_job(db_session_factory)
        await process_cover(
            job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), fetcher  # type: ignore[arg-type]
        )

    async with db_session_factory() as db:
        entries = list(await db.scalars(select(LibraryEntry)))

    assert len(entries) == 1
    assert entries[0].scan_count == 2


async def test_a_failed_scan_records_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A cover that was never recognized has no book to record."""
    job_id = await _create_pending_job(db_session_factory)
    vision_service = _FakeVisionService(error=CoverNotRecognized("Could not read the cover."))

    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, vision_service, _dune_fetcher()  # type: ignore[arg-type]
    )

    async with db_session_factory() as db:
        assert list(await db.scalars(select(LibraryEntry))) == []


async def test_correction_moves_the_history_to_the_corrected_book(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The misidentified book leaves the library; the corrected one enters it.

    Without this, correcting a title leaves a book the user never held in
    their history — and, in Module 6, in the profile vector that decides
    what they get recommended.
    """
    job_id = await _create_pending_job(db_session_factory)
    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), _dune_fetcher()  # type: ignore[arg-type]
    )

    corrected_fetcher = BookDataFetcher(
        sources=[
            FakeContentSource(
                SourceName.GOOGLE_BOOKS,
                SourceResult(
                    source=SourceName.GOOGLE_BOOKS,
                    metadata=BookMetadata(title="Baltagul", author="Mihail Sadoveanu"),
                    passages=[SourcePassage(kind=SourceKind.DESCRIPTION, content="Un roman.")],
                ),
            )
        ],
        settings=get_settings(),
    )
    await refresh_book_data(
        job_id, "Baltagul", "Mihail Sadoveanu", db_session_factory, corrected_fetcher
    )

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None

        entries = list(await db.scalars(select(LibraryEntry)))
        assert len(entries) == 1
        assert entries[0].book_id == job.result["book_id"]


async def test_correction_keeps_a_book_the_user_already_rated(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A rated book is the user's, even if a correction says the scan was wrong.

    They may have reached it by mistake, but they have since said
    something about it, and deleting that is a data loss the correction
    never asked for.
    """
    job_id = await _create_pending_job(db_session_factory)
    await process_cover(
        job_id, b"fake-image-bytes", db_session_factory, _identified_dune(), _dune_fetcher()  # type: ignore[arg-type]
    )

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        scanned_book_id = job.result["book_id"]
        await library_service.update_entry(
            db, job.user_id, scanned_book_id, LibraryEntryUpdate(rating=5)
        )

    corrected_fetcher = BookDataFetcher(
        sources=[
            FakeContentSource(
                SourceName.GOOGLE_BOOKS,
                SourceResult(
                    source=SourceName.GOOGLE_BOOKS,
                    metadata=BookMetadata(title="Baltagul", author="Mihail Sadoveanu"),
                    passages=[SourcePassage(kind=SourceKind.DESCRIPTION, content="Un roman.")],
                ),
            )
        ],
        settings=get_settings(),
    )
    await refresh_book_data(
        job_id, "Baltagul", "Mihail Sadoveanu", db_session_factory, corrected_fetcher
    )

    async with db_session_factory() as db:
        entries = {entry.book_id: entry for entry in await db.scalars(select(LibraryEntry))}

    assert scanned_book_id in entries
    assert entries[scanned_book_id].rating == 5
    assert len(entries) == 2
