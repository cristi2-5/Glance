"""Pydantic schemas for asynchronous jobs."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobCreated(BaseModel):
    """Response for job creation — just the id, for polling on `GET /jobs/{id}`."""

    job_id: int


class JobPublic(BaseModel):
    """The public representation of a job, as exposed to its owner."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime
