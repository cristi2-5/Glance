"""Primitive de securitate: hashing parole, token-uri JWT de acces, refresh token-uri opace.

Hashing-ul parolelor folosește `bcrypt` direct (nu `passlib` — versiunea
1.7.4 crapă cu `bcrypt>=4.1`, vezi CLAUDE.md).
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
    """Calculează hash-ul bcrypt al unei parole în clar.

    Args:
        password: Parola în clar.

    Returns:
        Hash-ul bcrypt, codificat ca string UTF-8.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """Verifică o parolă în clar față de un hash bcrypt.

    Args:
        password: Parola în clar introdusă de utilizator.
        hashed_password: Hash-ul bcrypt stocat.

    Returns:
        True dacă parola corespunde hash-ului, altfel False.
    """
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    """Creează un JWT de acces, semnat, valabil `access_token_expire_minutes`.

    Args:
        user_id: Id-ul utilizatorului pentru care se emite tokenul.

    Returns:
        JWT-ul semnat, ca string compact.
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
    """Decodează și validează un JWT de acces.

    Args:
        token: JWT-ul primit în header-ul `Authorization`.

    Returns:
        Id-ul utilizatorului extras din claim-ul `sub`.

    Raises:
        JWTError: Dacă tokenul e expirat, semnat greșit sau malformat.
        ValueError: Dacă tokenul e valid dar nu e de tip `access`.
    """
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise ValueError("Tokenul nu este de tip access.")
    return int(payload["sub"])


def generate_refresh_token() -> tuple[str, str]:
    """Generează un refresh token opac nou, împreună cu hash-ul lui.

    Returns:
        Un tuplu `(token_brut, token_hash)`. Doar `token_brut` se trimite
        clientului; doar `token_hash` se stochează în baza de date.
    """
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_refresh_token(raw_token)


def hash_refresh_token(raw_token: str) -> str:
    """Calculează hash-ul SHA-256 (hex) al unui refresh token brut.

    Args:
        raw_token: Tokenul brut, așa cum a fost trimis clientului.

    Returns:
        Hash-ul hex, folosit ca valoare de căutare/stocare în DB.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    """Calculează momentul de expirare pentru un refresh token nou emis.

    Returns:
        Timestamp UTC la care tokenul expiră, conform `refresh_token_expire_days`.
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
