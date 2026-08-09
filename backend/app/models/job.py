"""The `Job` model — tracks the state of an asynchronous operation (e.g. cover analysis)."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class JobStatus(enum.StrEnum):
    """The possible states of an asynchronous job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    """Represents a long-running asynchronous operation (e.g. cover analysis).

    Attributes:
        id: Unique identifier.
        user_id: The user who owns the job — only they can read it.
        status: The current state (`pending` → `running` → `done`/`failed`).
        result: The job result, as JSON, available when `status == done`.
        error: The error message, available when `status == failed`.
        created_at: The moment the job was created.
        updated_at: The moment of the last state change.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING.value, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
