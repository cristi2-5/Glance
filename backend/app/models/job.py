"""Modelul `Job` — urmărește starea unei operațiuni asincrone (ex: analiza unei coperte)."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class JobStatus(enum.StrEnum):
    """Stările posibile ale unui job asincron."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    """Reprezintă o operațiune asincronă de lungă durată (ex: analiza unei coperte).

    Attributes:
        id: Identificator unic.
        user_id: Utilizatorul proprietar al job-ului — doar el îl poate citi.
        status: Starea curentă (`pending` → `running` → `done`/`failed`).
        result: Rezultatul job-ului, ca JSON, disponibil când `status == done`.
        error: Mesajul de eroare, disponibil când `status == failed`.
        created_at: Momentul creării.
        updated_at: Momentul ultimei schimbări de stare.
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
