"""Modelul `User` — cont de utilizator autentificat."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.refresh_token import RefreshToken


class User(Base):
    """Reprezintă un utilizator înregistrat al aplicației Glance.

    Attributes:
        id: Identificator unic.
        email: Adresa de email, unică, folosită la autentificare.
        hashed_password: Hash-ul bcrypt al parolei (niciodată parola în clar).
        is_active: Dacă utilizatorul poate încă să se autentifice.
        created_at: Momentul creării contului (UTC).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
