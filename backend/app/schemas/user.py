"""Scheme Pydantic pentru reprezentarea publică a unui utilizator."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserPublic(BaseModel):
    """Reprezentarea unui utilizator expusă către client (fără parolă)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    created_at: datetime
