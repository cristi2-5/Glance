"""Security primitives: password hashing, access JWT tokens, opaque refresh tokens.

Password hashing uses `bcrypt` directly (not `passlib` — version 1.7.4
crashes with `bcrypt>=4.1`, see CLAUDE.md).
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()

TOKEN_TYPE_ACCESS = "access"


def hash_password(password: str) -> str:
    """Computes the bcrypt hash of a plaintext password.

    Args:
        password: The plaintext password.

    Returns:
        The bcrypt hash, encoded as a UTF-8 string.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a bcrypt hash.

    Args:
        password: The plaintext password entered by the user.
        hashed_password: The stored bcrypt hash.

    Returns:
        True if the password matches the hash, otherwise False.
    """
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    """Creates a signed access JWT, valid for `access_token_expire_minutes`.

    Args:
        user_id: The id of the user for whom the token is issued.

    Returns:
        The signed JWT, as a compact string.
    """
    now = datetime.utcnow()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": TOKEN_TYPE_ACCESS,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    """Decodes and validates an access JWT.

    Args:
        token: The JWT received in the `Authorization` header.

    Returns:
        The user id extracted from the `sub` claim.

    Raises:
        JWTError: If the token is expired, incorrectly signed, or malformed.
        ValueError: If the token is valid but not of type `access`.
    """
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise ValueError("Token is not of type access.")
    return int(payload["sub"])


def generate_refresh_token() -> tuple[str, str]:
    """Generates a new opaque refresh token, along with its hash.

    Returns:
        A `(raw_token, token_hash)` tuple. Only `raw_token` is sent to the
        client; only `token_hash` is stored in the database.
    """
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_refresh_token(raw_token)


def hash_refresh_token(raw_token: str) -> str:
    """Computes the SHA-256 (hex) hash of a raw refresh token.

    Args:
        raw_token: The raw token, as sent to the client.

    Returns:
        The hex hash, used as the lookup/storage value in the DB.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    """Computes the expiration moment for a newly issued refresh token.

    Returns:
        UTC timestamp at which the token expires, per `refresh_token_expire_days`.
    """
    return datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)


__all__ = [
    "JWTError",
    "create_access_token",
    "decode_access_token",
    "generate_refresh_token",
    "hash_password",
    "hash_refresh_token",
    "refresh_token_expiry",
    "verify_password",
]
