"""Configurația aplicației, citită din variabile de mediu / fișierul .env.

Toate valorile configurabile (căi, chei, parametri Ollama) trec prin acest
modul. Nimic nu e hardcodat direct în cod.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Setările aplicației Glance.

    Valorile sunt citite din variabile de mediu sau dintr-un fișier `.env`
    aflat în directorul `backend/`. Vezi `.env.example` pentru cheile
    disponibile.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Aplicație
    app_name: str = "Glance"
    debug: bool = False

    # Bază de date
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'glance.db'}"

    # Chroma (vector DB)
    chroma_persist_dir: str = str(BACKEND_DIR / "data" / "chroma")

    # Auth / JWT
    jwt_secret_key: str = "schimba-aceasta-cheie-in-.env"
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
    # Clientul mobil rulează nativ (Expo Go), unde CORS nu se aplică — lista
    # asta contează doar pentru Expo Web și pentru pagina de test din `/dev`.
    # Implicit permitem orice origine, pentru că serverul ascultă doar în
    # rețeaua locală de dezvoltare. Restrânge lista înainte de orice expunere
    # dincolo de LAN.
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    """Returnează instanța (cache-uită) de setări ale aplicației.

    Returns:
        Instanța `Settings` populată din mediu / `.env`.
    """
    return Settings()
