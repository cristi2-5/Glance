"""Application configuration, read from environment variables / the .env file.

All configurable values (paths, keys, Ollama parameters) go through this
module. Nothing is hardcoded directly in the code.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Settings for the Glance application.

    Values are read from environment variables or from a `.env` file
    located in the `backend/` directory. See `.env.example` for the
    available keys.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "Glance"
    debug: bool = False

    # Database
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'glance.db'}"

    # Chroma (vector DB)
    chroma_persist_dir: str = str(BACKEND_DIR / "data" / "chroma")

    # Auth / JWT
    jwt_secret_key: str = "change-this-key-in-.env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_vision_model: str = "moondream"
    ollama_llm_model: str = "llama3.2"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_request_timeout_seconds: int = 120
    # Caps the vision model's reply length. Moondream rambles past the two
    # keys we ask for and gets cut mid-token, producing unparsable JSON.
    ollama_vision_num_predict: int = 96

    # Upload
    max_upload_size_bytes: int = 8 * 1024 * 1024  # 8 MB

    # Catalog lookup
    #
    # Without a key, Google Books uses a shared anonymous quota that is
    # routinely exhausted (HTTP 429 on every request). Get a free key at
    # https://console.cloud.google.com/apis/library/books.googleapis.com
    # When unset or exhausted, the lookup falls back to Open Library.
    google_books_api_key: str | None = None
    google_books_timeout_seconds: float = 8.0
    open_library_timeout_seconds: float = 10.0
    # Google Books intermittently answers 503 "Service temporarily
    # unavailable" and succeeds on an immediate retry. Applies to transient
    # failures only (5xx, timeouts) — a 429 quota refusal is not retried.
    catalog_max_retries: int = 2

    # Vision (Module 3)
    image_max_edge_px: int = 768
    image_jpeg_quality: int = 85
    vision_confidence_threshold: float = 0.70
    vision_min_ocr_chars: int = 6
    # Confidence when the vision model produced a title no catalog could
    # confirm. Always below the threshold, so it is offered for review.
    vision_unverified_confidence: float = 0.35
    # Confidence when OCR read the cover well but no catalog could confirm
    # it — common for Romanian editions, which the catalogs cover poorly.
    # Below the threshold (so the user is offered a correction), but well
    # above the vision-model figure: legible cover text beats a 1.8B guess.
    vision_ocr_unconfirmed_confidence: float = 0.55
    ollama_max_retries: int = 2

    # CORS
    #
    # The mobile client runs natively (Expo Go), where CORS does not apply —
    # this list only matters for Expo Web and for the test page in `/dev`.
    # By default we allow any origin, because the server only listens on the
    # local development network. Restrict this list before any exposure
    # beyond the LAN.
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    """Returns the (cached) application settings instance.

    Returns:
        The `Settings` instance populated from the environment / `.env`.
    """
    return Settings()
