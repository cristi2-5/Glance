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

    # Upload
    max_upload_size_bytes: int = 8 * 1024 * 1024  # 8 MB

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
