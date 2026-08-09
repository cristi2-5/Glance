"""Pydantic schemas for the authentication flow."""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Request body for registering a new user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """Request body for authentication."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Request body for renewing the access token."""

    refresh_token: str


class TokenResponse(BaseModel):
    """The token pair returned on login, register, or refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
