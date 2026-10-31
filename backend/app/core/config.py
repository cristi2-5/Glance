"""Application configuration, read from environment variables / the .env file.

All configurable values (paths, keys, Ollama parameters) go through this
module. Nothing is hardcoded directly in the code.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

from app.core.exceptions import InsecureConfiguration

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

#: The JWT secret that ships in the code. Anyone can read it in the repository,
#: so a deployment still using it signs tokens anybody can forge.
DEFAULT_JWT_SECRET = "change-this-key-in-.env"
#: Shortest JWT secret production accepts (HS256 wants at least 256 bits).
MIN_JWT_SECRET_LENGTH = 32
#: Azurite's well-known development account. Its name and key are printed in
#: Microsoft's documentation, so they protect nothing in a real deployment.
AZURITE_ACCOUNT_NAME = "devstoreaccount1"
AZURITE_ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)
#: The compose dev database's credentials (`glance` / `glance`).
DEV_DATABASE_USER = "glance"
DEV_DATABASE_PASSWORD = "glance"  # noqa: S105 - the published dev value, detected, not used


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
    # "production" turns on the startup guard (`validate_for_production`) and
    # removes the interactive API docs. Everything else defaults to what a
    # laptop needs.
    app_env: Literal["development", "production"] = "development"

    # Database
    #
    # PostgreSQL with the pgvector extension is required: chunks and profile
    # vectors live in the same database as everything else. The default is
    # the compose dev database (`docker compose up -d postgres`), reached
    # through the host-side port mapping on 127.0.0.1 (not "localhost": on
    # Windows that tries IPv6 first and adds ~2 s per connection).
    database_url: str = "postgresql+asyncpg://glance:glance@127.0.0.1:5433/glance"
    # "password": the password in `database_url`, as always. "entra": no
    # password anywhere -- the app asks its managed identity for a short-lived
    # Entra ID token and hands that to Postgres as the password (cloud step 5).
    # The URL then carries only user and host; the user is the identity's name.
    database_auth: Literal["password", "entra"] = "password"

    # Blob + Queue Storage (cloud step 4)
    #
    # The scan photos (private Blob container) and the job queue live in an
    # Azure Storage account. The default is Azurite's well-known development
    # account: its name and key are published in Microsoft's documentation and
    # protect nothing, so having them here is not a leak. Host-side runs and
    # tests reach the emulator through the compose port mappings
    # (`docker compose up -d azurite`); compose overrides this with the
    # in-network `azurite` hostname. Against real Azure the connection changes
    # (managed identity, below) and nothing else does.
    azure_storage_connection_string: str = (
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
        "K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
        "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    )
    # Managed-identity access to a real storage account (cloud step 5). When
    # the account name is set it wins over the connection string: the clients
    # are built for `https://<name>.blob.core.windows.net` (and `.queue.`) and
    # authenticate with the identity -- no account key exists anywhere.
    azure_storage_account_name: str | None = None
    # Client id of the user-assigned managed identity of *this* container (each
    # Container App / job has its own, created by Bicep in step 7). Used for
    # Storage and for the Postgres Entra token alike: one identity per
    # container, each holding a storage credential and a database credential.
    azure_client_id: str | None = None
    # Azure Container Apps injects this into every container. It is read only
    # to notice "running in Azure" (see `validate_for_production`).
    container_app_name: str | None = None
    # Private container holding the normalised scan photos, one JPEG per scan.
    scan_container: str = "scans"
    # Queue carrying `{"kind", "job_id"}` messages from the api to the queue worker.
    job_queue: str = "cover-jobs"
    # How long a received message stays invisible to other queue workers. Must
    # outlast the pipeline (30-120 s): if it expires mid-job, a second queue
    # worker picks the same job up. If a queue worker dies, this is how long
    # until another one retries.
    job_visibility_timeout_seconds: int = 300
    # A message delivered more often than this is poison: the job is failed
    # and the message dropped instead of crashing a queue worker forever.
    job_max_attempts: int = 3
    # Longest a single message may be worked on. Past it the queue worker
    # reports itself unhealthy (so the platform restarts it) and stops
    # extending the message's visibility (so it reappears and is retried).
    # Without the limit a hung pipeline would hold its message forever.
    job_max_runtime_seconds: int = 600
    # Port of the queue worker's tiny HTTP liveness endpoint (`/health`).
    queue_worker_health_port: int = 8001
    # The polling loop must tick at least this often to count as alive. It has
    # to exceed `queue_idle_backoff_max_seconds` (an idle loop sleeps that
    # long between ticks) -- `Settings` checks that at construction.
    #
    # Generous on purpose (300 s): during a storage or token outage one
    # receive can legitimately block for a few minutes inside the SDK's bounded
    # retries, and a shorter window would restart-loop healthy queue workers.
    queue_worker_loop_stale_seconds: float = 300.0
    # An empty queue is polled with a growing pause (fewer storage
    # transactions, which are billed in Azure), reset by the next message.
    queue_idle_backoff_min_seconds: float = 1.0
    queue_idle_backoff_max_seconds: float = 10.0
    # How long a browser/phone may reuse a downloaded scan photo.
    scan_photo_cache_seconds: int = 3600

    # Auth / JWT
    jwt_secret_key: str = DEFAULT_JWT_SECRET
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
    # (`ollama_embedding_model`) and the vector tables are unaffected either
    # way — embeddings always run locally.
    ai_provider: Literal["groq", "ollama"] = "groq"

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_vision_model: str = "moondream"
    ollama_llm_model: str = "llama3.2"
    ollama_embedding_model: str = "nomic-embed-text"
    # Embeddings are the one model that stays resident. It is ~275 MB, so
    # it never contends with the vision/LLM slot, and reloading it would
    # add seconds to every ingest on a CPU-only machine.
    ollama_embedding_keep_alive: str = "1h"
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
    groq_vision_model: str = "qwen/qwen3.8-27b"
    # RAG summary generation (Module 5).
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_request_timeout_seconds: int = 60
    groq_max_retries: int = 2
    # How much hidden chain-of-thought each Groq model may spend before it
    # emits the answer. Both models here are reasoning models, and we ask
    # them for small structured JSON, not for reasoning — left at their
    # defaults they burn the completion budget thinking and get truncated
    # mid-object (Groq answers 400 json_validate_failed, or the reply
    # simply parses as nothing).
    #
    # **The accepted values differ per model family, and Groq rejects the
    # wrong one with a 400**, which is not retryable and takes down the
    # whole call: Qwen3 accepts `none`/`default`, while gpt-oss accepts
    # only `low`/`medium`/`high` — it cannot turn reasoning off at all, so
    # `low` is the floor there. That is why this is two settings resolved
    # per model (`groq_reasoning_effort_for`) rather than one constant in
    # `groq_client.py`: a single value cannot be correct for both.
    groq_vision_reasoning_effort: Literal["none", "default", "low", "medium", "high"] = "none"
    groq_llm_reasoning_effort: Literal["none", "default", "low", "medium", "high"] = "low"
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
    # Which Wikipedia editions to search, in order, stopping at the first
    # that yields a confident match.
    #
    # English alone was the original setting, and it is wrong for this
    # project's actual library. A Romanian edition is searched under its
    # Romanian title, which en.wikipedia has never heard of: "Căpitan la
    # cincisprezece ani" returns zero results there while ro.wikipedia has
    # the article under exactly that name. Four of five Romanian titles
    # tested returned nothing at all on en.
    #
    # English stays first because its articles are substantially richer —
    # a Reception section with cited criticism, which is the whole point of
    # this source — and an English-language book should resolve there. The
    # Romanian edition falls through to `ro` and gets a shorter article,
    # which still beats no article.
    wikipedia_languages: list[str] = ["en", "ro"]
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
    # Guards against a pathological Wikipedia article filling the database; well
    # above any real Reception section.
    source_max_passage_chars: int = 20_000
    # Last leg of the cover-image fallback: a Wikipedia article's lead
    # image, used when neither Google Books nor Open Library had a cover.
    # On by default *because this build is private and not distributed* —
    # book articles usually illustrate themselves with the publisher's
    # cover scan, uploaded under a fair-use exemption that does not extend
    # to a third-party app. Set to false before the app is published or
    # shared; the other two legs are freely licensed and stay on. See the
    # "Cover images" decision in CLAUDE.md.
    wikipedia_cover_fallback: bool = True

    # RAG (Module 5)
    #
    # Chunk size is a retrieval trade-off, not a model limit: smaller
    # chunks rank more precisely but cite less context, and a cited
    # excerpt too short to read on its own is useless on the client.
    # ~500 tokens is roughly a long paragraph — one coherent point.
    rag_chunk_target_tokens: int = 500
    # Overlap keeps a statement that straddles a boundary intact in at
    # least one chunk, so it can still be retrieved and cited whole.
    rag_chunk_overlap_tokens: int = 50
    # Per-aspect retrieval depth (see `RETRIEVAL_ASPECTS` in
    # `rag_service.py`). Deliberately small: these corpora are a handful
    # of passages, not a library, and asking for more just returns
    # everything with worse ranking.
    rag_retrieval_top_k: int = 4
    # Ceiling on the context sent to the model, across all aspects.
    rag_max_context_chunks: int = 8
    # Output budget for the summary call. Well above what 3-6 cited
    # sentences need, because the JSON envelope and the chunk ids are
    # counted too, and a reply truncated mid-object parses as nothing.
    rag_max_output_tokens: int = 2048

    # Recommendations (Module 6b)
    #
    # Candidates cannot come from the RAG corpus: `book_chunks` holds only
    # books this user has already scanned, and that is exactly the set that
    # must be filtered out. They come from the catalogs instead, queried by
    # the genres and authors derived from books the user rated well.
    #
    # How many derived labels seed that discovery. Deliberately fewer than
    # the profile shows: each seed is one query per catalog, and the sixth
    # favourite genre characterises the reader far less than the first.
    recommendation_seed_genres: int = 3
    recommendation_seed_authors: int = 2
    # How many volumes each individual catalog query asks for, and the cap
    # on how many *new* books one discovery run may persist. The second
    # bounds both the embedding work (one local `nomic-embed-text` pass per
    # new book) and how fast the shared candidate pool grows.
    recommendation_results_per_query: int = 12
    recommendation_max_new_candidates: int = 60
    # Default page size of `GET /users/me/recommendations`.
    recommendation_default_limit: int = 12
    # Floor on cosine similarity to the profile vector. Low on purpose: the
    # pool is already seeded from this reader's own genres and authors, so
    # everything in it is topically plausible and a high floor would empty
    # the list rather than sharpen it. This rejects noise, it does not rank.
    recommendation_min_score: float = 0.20
    # How long a discovery run stays good for. Re-running it is several
    # catalog requests against a quota, and the catalogs do not gain new
    # books by the hour. A change in the reader's derived preferences
    # invalidates this independently of the clock — see `RecommendationState`.
    recommendation_candidate_ttl_hours: int = 24
    # ...but a run during which a source went unavailable gets a far
    # shorter one. Same reasoning as `empty_book_cache_ttl_hours` in Module
    # 4: a degraded answer is not a settled fact, and pinning a pool built
    # from half its queries for a full day means one bad minute costs the
    # reader a day of thin recommendations, with no way to shake it loose.
    recommendation_degraded_ttl_hours: int = 1
    # Pause between two queries to the *same* catalog during discovery.
    #
    # **Measured, not guessed.** Discovery issues one query per seed, and
    # firing them concurrently is what the original implementation did:
    # against Google Books that produced a 503 on 16 of 20 requests, while
    # the identical queries paced one second apart returned 200 on 5 of 5.
    # Google sheds load per key on burst rate, not on query shape — the
    # `printType`/`orderBy` parameters were measured to make no difference
    # at all. The scan path never hit this because it issues exactly one
    # Google Books request at a time; discovery was the first code here to
    # fan out.
    recommendation_query_spacing_seconds: float = 1.0
    # Extra attempts per discovery query, deliberately below
    # `catalog_max_retries`. A scan waits on one lookup and is worth
    # retrying hard; discovery has one query per seed and gives up on a
    # source after its first unavailable answer, so retrying each query
    # three times against a host that is down is how a once-a-day refresh
    # becomes a two-minute request.
    recommendation_discovery_retries: int = 1
    # Floor on how often discovery may start for one reader, whatever the
    # TTL and the preference fingerprint say.
    #
    # Both of those invalidate on a *rating*, and every library write
    # invalidates the client's recommendation query — so rating a book
    # three times in three seconds fired three refetches, each with new
    # derived preferences, each starting its own discovery. Three
    # concurrent runs, a dozen simultaneous catalog requests, Google Books
    # shedding load again, and one of them crashing on the unique key its
    # twin had just inserted. Pacing queries within a run does nothing
    # about several runs.
    #
    # The reader's *ranking* is never stale because of this: the profile
    # vector is recomputed from their ratings on every request, and only
    # the candidate pool lags.
    recommendation_min_discovery_interval_seconds: float = 60.0
    # Ceiling on the description text that goes into a book's profile
    # document. Past a paragraph or two a blurb starts describing the
    # publisher's marketing rather than the book.
    recommendation_document_max_chars: int = 1200

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
    # The mobile client runs natively, where CORS does not apply -- the list
    # only matters for a browser client (Expo Web). So the default allows no
    # origin at all; set CORS_ORIGINS to an explicit list if a web client ever
    # exists. `"*"` is still accepted in development but refused in production.
    cors_origins: list[str] = []

    @property
    def vision_model(self) -> str:
        """The vision model name for the currently active `ai_provider`."""
        return self.groq_vision_model if self.ai_provider == "groq" else self.ollama_vision_model

    @property
    def llm_model(self) -> str:
        """The summary-generation model name for the currently active `ai_provider`."""
        return self.groq_llm_model if self.ai_provider == "groq" else self.ollama_llm_model

    @model_validator(mode="after")
    def _check_health_timing(self) -> "Settings":
        """Rejects a liveness window shorter than the idle polling pause.

        An idle queue worker legitimately sleeps up to
        `queue_idle_backoff_max_seconds` between loop ticks; a stale limit at
        or below that would report a healthy, idle container as frozen.
        """
        if self.queue_worker_loop_stale_seconds <= self.queue_idle_backoff_max_seconds:
            raise ValueError(
                "QUEUE_WORKER_LOOP_STALE_SECONDS must be larger than "
                "QUEUE_IDLE_BACKOFF_MAX_SECONDS, or an idle queue worker looks frozen."
            )
        return self

    def production_problems(self) -> list[str]:
        """Lists every development value that must not reach production.

        Pure and side-effect free, so each rule is unit-testable. Only
        meaningful when `app_env == "production"`; `validate_for_production`
        decides what to do with the list.

        Returns:
            One human-readable line per offending setting; empty when clean.
        """
        problems: list[str] = []

        if self.jwt_secret_key == DEFAULT_JWT_SECRET:
            problems.append("JWT_SECRET_KEY is the default shipped in the code.")
        elif len(self.jwt_secret_key) < MIN_JWT_SECRET_LENGTH:
            problems.append(f"JWT_SECRET_KEY is shorter than {MIN_JWT_SECRET_LENGTH} characters.")

        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS contains '*' (any website may call the api).")

        # No storage key in a deployed container: the managed identity is the
        # only accepted path, so a connection string (Azurite's or a real
        # account key) is never what production runs on.
        if not self.azure_storage_account_name:
            problems.append(
                "AZURE_STORAGE_ACCOUNT_NAME is not set: production must reach storage with the "
                "managed identity, not a connection string (Azurite or an account key)."
            )

        # No database password in a deployed container either.
        url = make_url(self.database_url)
        if self.database_auth != "entra":
            problems.append("DATABASE_AUTH is not 'entra': production must not use a password.")
            if url.username == DEV_DATABASE_USER and url.password == DEV_DATABASE_PASSWORD:
                problems.append("DATABASE_URL uses the compose development credentials (glance).")
        elif url.password:
            problems.append("DATABASE_AUTH=entra but DATABASE_URL still contains a password.")

        if self.ai_provider == "groq" and not self.groq_api_key:
            problems.append("GROQ_API_KEY is missing while AI_PROVIDER=groq.")

        return problems

    def azure_signals(self) -> list[str]:
        """Names the settings that say this process runs in Azure.

        Returns:
            One entry per signal: `CONTAINER_APP_NAME` (injected by Container
            Apps), `AZURE_STORAGE_ACCOUNT_NAME` or `DATABASE_AUTH=entra`.
        """
        signals: list[str] = []
        if self.container_app_name:
            signals.append("CONTAINER_APP_NAME")
        if self.azure_storage_account_name:
            signals.append("AZURE_STORAGE_ACCOUNT_NAME")
        if self.database_auth == "entra":
            signals.append("DATABASE_AUTH=entra")
        return signals

    def validate_for_production(self) -> None:
        """Refuses to continue on development settings where that matters.

        With `app_env == "production"` every rule of `production_problems`
        applies. With `app_env == "development"` the only check is the bypass:
        a container that is evidently in Azure (see `azure_signals`) but was
        started without `APP_ENV=production` would skip every rule, which is
        the easiest misconfiguration to make, so it is refused too. A laptop
        (Azurite, password, no Azure variables) is unaffected.

        Raises:
            InsecureConfiguration: Listing every offending setting at once,
                so one failed start fixes them all.
        """
        if self.app_env != "production":
            signals = self.azure_signals()
            if signals:
                raise InsecureConfiguration(
                    "Running in Azure without APP_ENV=production "
                    f"(seen: {', '.join(signals)}): the production guard would be skipped. "
                    "Set APP_ENV=production."
                )
            return
        problems = self.production_problems()
        if problems:
            raise InsecureConfiguration(
                "Refusing to start with APP_ENV=production:\n- " + "\n- ".join(problems)
            )

    def groq_reasoning_effort_for(self, model: str) -> str | None:
        """The `reasoning_effort` to send for a given Groq model.

        Args:
            model: The Groq model name being called.

        Returns:
            The configured effort for the vision or LLM model, or `None`
            for any other model — meaning "send no `reasoning_effort` at
            all", since we have no way to know which vocabulary an
            unrecognised model accepts, and guessing wrong is a 400.
        """
        if model == self.groq_vision_model:
            return self.groq_vision_reasoning_effort
        if model == self.groq_llm_model:
            return self.groq_llm_reasoning_effort
        return None


@lru_cache
def get_settings() -> Settings:
    """Returns the (cached) application settings instance.

    Returns:
        The `Settings` instance populated from the environment / `.env`.
    """
    return Settings()
