"""The `RefreshToken` model — opaque token, stored hashed, for session renewal."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(Base):
    """Represents a refresh token issued to a user.

    The raw token is never stored — only its SHA-256 hash, so that a
    database read never exposes valid tokens. Rotation: on every successful
    `/auth/refresh`, the old token is marked `revoked` and a new one is
    issued.

    Attributes:
        id: Unique identifier.
        token_hash: The SHA-256 (hex) hash of the raw token.
        user_id: The user this token belongs to.
        expires_at: The moment (UTC) after which the token is no longer valid.
        revoked: Whether the token has been explicitly invalidated (refresh or logout).
        created_at: The moment of issuance (UTC).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")
