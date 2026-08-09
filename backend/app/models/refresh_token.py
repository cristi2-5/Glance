"""Modelul `RefreshToken` — token opac, stocat hash-uit, pentru reînnoirea sesiunii."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(Base):
    """Reprezintă un refresh token emis unui utilizator.

    Tokenul brut nu se stochează niciodată — doar hash-ul SHA-256 al lui,
    astfel încât o citire a bazei de date să nu expună token-uri valide.
    Rotation: la fiecare `/auth/refresh` reușit, tokenul vechi e marcat
    `revoked` și se emite unul nou.

    Attributes:
        id: Identificator unic.
        token_hash: Hash-ul SHA-256 (hex) al tokenului brut.
        user_id: Utilizatorul căruia îi aparține tokenul.
        expires_at: Momentul (UTC) după care tokenul nu mai e valid.
        revoked: Dacă tokenul a fost invalidat explicit (refresh sau logout).
        created_at: Momentul emiterii (UTC).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")
