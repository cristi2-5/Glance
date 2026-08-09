"""Scheme Pydantic pentru job-uri asincrone."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobCreated(BaseModel):
    """Răspunsul la crearea unui job — doar id-ul, pentru polling pe `GET /jobs/{id}`."""

    job_id: int


class JobPublic(BaseModel):
    """Reprezentarea publică a unui job, așa cum e expusă proprietarului lui."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime
