"""Tests for `app.workers.cover_pipeline.process_cover`."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import CoverNotRecognized
from app.models.job import Job, JobStatus
from app.schemas.vision import CoverIdentification
from app.workers.cover_pipeline import process_cover


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
        assert job.result["summary"] is None
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
