"""Application configuration, read from environment variables / the .env file.

All configurable values (paths, keys, Ollama parameters) go through this
module. Nothing is hardcoded directly in the code.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # AI provider (Module 3/5 pivot)
    #
    # "groq": vision (cover title/author extraction) and LLM generation
    # (RAG summaries) run on Groq Cloud. Adopted because the local models
    # that fit in 7.4 GB of RAM were not good enough — Moondream
    # misidentified titles/authors too often and inference was slow on
    # CPU. "ollama": both stay fully local, the project's original design
    # — kept working as a fallback, not deleted, in case Groq access goes
    # away or local accuracy improves enough to matter again. Embeddings
    # (`ollama_embedding_model`) and ChromaDB are unaffected either way —
    # they always run locally.
    ai_provider: Literal["groq", "ollama"] = "groq"

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_vision_model: str = "moondream"
    ollama_llm_model: str = "llama3.2"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_request_timeout_seconds: int = 120
    # Caps the vision model's reply length. Moondream rambles past the two
    # keys we ask for and gets cut mid-token, producing unparsable JSON.
    ollama_vision_num_predict: int = 96

    # Groq Cloud
    #
    # https://console.groq.com/keys — required when ai_provider="groq".
    groq_api_key: str | None = None
    # Multimodal, supports JSON mode — used to extract {"title", "author"}
    # directly from a cover photo. Marked "preview" by Groq: see
    # `groq_client.py` for the retry/error handling this implies.
    groq_vision_model: str = "qwen/qwen3.6-27b"
    # RAG summary generation (Module 5).
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_request_timeout_seconds: int = 60
    groq_max_retries: int = 2
    # Floor on completion tokens for JSON-mode calls (see `groq_client.py`).
    # Groq's models here are reasoning models — even with reasoning turned
    # off for these calls, they need more headroom than a small non-
    # reasoning local model (Ollama's `ollama_vision_num_predict`) would.
    groq_max_tokens: int = 1024

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

    # Content sources / cache (Module 4)
    #
    # Only official APIs are used — see the "Content sources" decision in
    # CLAUDE.md for why no review site is scraped. Wikipedia's API policy
    # requires a descriptive User-Agent identifying the app and a contact
    # point; requests without one are throttled or refused.
    source_user_agent: str = "Glance/0.1 (book summary app; cristian.stoian2005@gmail.com)"
    wikipedia_timeout_seconds: float = 10.0
    wikipedia_language: str = "en"
    # How long a cached book stays fresh before its sources are re-fetched.
    # Book metadata and critical reception change on the scale of months,
    # so a long TTL costs nothing and keeps repeat scans instant.
    book_cache_ttl_days: int = 30
    # A book the catalogs had *nothing* on gets a far shorter TTL. Emptiness
    # is not a durable fact: it is usually a bare catalog record that gets
    # filled in, a Wikipedia article that does not exist yet, or a source
    # that was degraded at the moment we asked. Caching that for the full 30
    # days makes the gap permanent from the user's side — rescanning the
    # book returns the same empty entry and there is no way to force a
    # retry. Short enough to self-heal, long enough that repeated scans in
    # one session don't hammer three APIs.
    empty_book_cache_ttl_hours: int = 6
    # Guards against a pathological Wikipedia article filling SQLite; well
    # above any real Reception section.
    source_max_passage_chars: int = 20_000

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

    @property
    def vision_model(self) -> str:
        """The vision model name for the currently active `ai_provider`."""
        return self.groq_vision_model if self.ai_provider == "groq" else self.ollama_vision_model

    @property
    def llm_model(self) -> str:
        """The summary-generation model name for the currently active `ai_provider`."""
        return self.groq_llm_model if self.ai_provider == "groq" else self.ollama_llm_model


@lru_cache
def get_settings() -> Settings:
    """Returns the (cached) application settings instance.

    Returns:
        The `Settings` instance populated from the environment / `.env`.
    """
    return Settings()
