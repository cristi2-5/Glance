"""Pydantic schemas for cover recognition results and manual correction."""

from typing import Literal

from pydantic import BaseModel, Field


class CoverIdentification(BaseModel):
    """The outcome of identifying a book cover.

    Attributes:
        title: The recognized (or corrected) title.
        author: The recognized (or corrected) author, if any.
        confidence: Recognition confidence, 0-1.
        method: Which path produced this result.
        needs_review: Whether `confidence` is below the configured threshold.
        corrected: Whether the user has manually overridden this result.
    """

    title: str
    author: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    method: Literal["ocr", "vision", "manual"]
    needs_review: bool
    corrected: bool = False


class CorrectionRequest(BaseModel):
    """Body for `PATCH /jobs/{job_id}/correction` — a manual title/author override."""

    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=300)
