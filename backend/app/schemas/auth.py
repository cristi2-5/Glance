"""Scheme Pydantic pentru fluxul de autentificare."""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Corpul cererii de înregistrare a unui utilizator nou."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """Corpul cererii de autentificare."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Corpul cererii de reînnoire a tokenului de acces."""

    refresh_token: str


class TokenResponse(BaseModel):
    """Perechea de token-uri returnată la login, register sau refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
