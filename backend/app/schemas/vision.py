"""Pydantic schemas for cover recognition results and manual correction."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class LookupRequest(CorrectionRequest):
    """Body for `POST /books/lookup` — a book named by typing, not by photo.

    Same fields and limits as a correction, but typed text arrives with
    stray spaces: both fields are stripped, a title that is blank after
    stripping is rejected (422), and a blank author becomes `None`.
    """

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        """Strips the title and rejects one that is only whitespace."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("The title must not be blank.")
        return stripped

    @field_validator("author")
    @classmethod
    def _strip_author(cls, value: str | None) -> str | None:
        """Strips the author; a blank one means "no author given"."""
        if value is None:
            return None
        return value.strip() or None
