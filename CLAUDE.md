# CLAUDE.md — Glance

This file is automatically loaded by Claude Code at every session. Don't repeat it in prompts — update it as the project progresses.

## Overview

**Glance** — mobile app + hybrid backend (local storage/embeddings, cloud AI inference). You photograph a book cover, and the app recognizes the title and author, gathers material about the book from open sources, generates a summary via RAG, and offers personalized recommendations based on reading history.

**Storage, cache, and embeddings stay 100% local (SQLite, ChromaDB, `nomic-embed-text` via Ollama) — no exceptions.** Vision (cover recognition) and LLM generation (RAG summaries) run on **Groq Cloud**, adopted after the locally-sized models proved insufficient. See "Architecture pivot: local vision/LLM → Groq Cloud" below and the "Strict rules" section for the current, precise boundary.

## Hardware constraints (decisive for design)

Development laptop: **7.4 GB total RAM**, CPU-only, Windows 11, Python 3.11.9, Ollama 0.32.6.

Non-negotiable consequences:

1. **Never load two Ollama models simultaneously.** Set `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=5m`. Reloading costs 2-4 s; swapping would cost minutes.
2. **The full pipeline takes 30-120 s.** So heavy endpoints are **asynchronous**: they return `202 Accepted` + `job_id`, and the client polls `GET /jobs/{id}`. No Celery/Redis — just `BackgroundTasks` and a `jobs` table in SQLite.
3. **We avoid PyTorch.** ~2.5 GB of disk install and hundreds of MB of RAM for something Ollama already does. Embeddings via `nomic-embed-text`, OCR via ONNX Runtime.

## Architecture

```
Client (mobile app)
      │  POST /books/analyze-cover (image + JWT)  →  202 { job_id }
      │  GET  /jobs/{job_id}                        →  poll until status=done
      ▼
FastAPI backend (local, on laptop)
      │
      ├── qwen/qwen3.6-27b (via Groq)  → title/author from the cover, runs first    [cloud, seconds]
      ├── RapidOCR (ONNX)              → fallback when Groq's guess is unconfirmed  [fast, <1 s]
      ├── Google Books / Open Library / Wikipedia → metadata + text about the book
      ├── SQLite                       → users, jobs, books, sources, reading_history, preferences
      ├── ChromaDB                     → embeddings (nomic-embed-text, local via Ollama), power both RAG *and* recommendations
      └── openai/gpt-oss-120b (via Groq) → summary generated from retrieved context

Local fallback (Settings.ai_provider="ollama"): Moondream and Llama 3.2 via
Ollama take over the two Groq rows above, unchanged otherwise — see
"Architecture pivot" below.
```

## Decisions made (don't revisit without a new reason)

### Architecture pivot: local vision/LLM → Groq Cloud

**What changed:** as of this decision, `Settings.ai_provider` (default `"groq"`) selects the backend for the two AI-inference steps that were previously Ollama-only:

- **Vision** (cover title/author extraction): `moondream` (local, ~1.7 GB, 1.8B params) → `qwen/qwen3.6-27b` on Groq, multimodal, called with JSON mode so it returns `{"title": ..., "author": ...}` directly instead of free text that has to be parsed out of a rambling reply.
- **RAG summary generation** (Module 5, not yet built): `llama3.2` (local) → `openai/gpt-oss-120b` on Groq.

**Why:** on this laptop's hardware (7.4 GB RAM, CPU-only — see "Hardware constraints"), the locally-sized models were not good enough to ship. Moondream misidentified titles/authors often enough to undermine the OCR-first pipeline's fallback path, and CPU inference for both vision and summary generation was slow enough (15-40 s per call) to make the async job pipeline feel worse than it needed to. Groq's hosted inference is both faster and more accurate at this model size, at the cost of the original "100% local" guarantee.

**What stayed local:** SQLite, ChromaDB, and embeddings (`nomic-embed-text` via Ollama) are unaffected — this pivot is scoped to vision and LLM-generation inference only. See "Strict rules" for the precise, current boundary.

**How it's wired:** both integrations are config-driven, not hardcoded — `Settings.ai_provider: Literal["groq", "ollama"]` in `core/config.py`, plus `Settings.vision_model` / `Settings.llm_model` computed properties that resolve to the right model name for whichever provider is active. `app/services/groq_client.py` defines `AsyncGroqClient`, which is call-compatible with the existing `OllamaClient` Protocol (`app/services/ollama_client.py`) — same `generate(model, prompt, images, format, options)` shape — so `VisionService` and the future RAG service don't know or care which backend they're talking to. `get_active_ai_client()` is the single switch point; reinstating the local-only setup later means setting `AI_PROVIDER=ollama` in `.env`, no code change. Groq errors (including qwen/qwen3.6-27b's preview-model rate limits and instability) are retried with backoff and surfaced as `AIProviderUnavailable`, never left to propagate raw.

### Vision: vision-model-first, OCR as fallback

**Supersedes the original "OCR-first, vision model as fallback" design** (kept below for context — the OCR/catalog matching machinery it describes is unchanged, only the order of operations flipped).

The vision model (Groq by default, Moondream if `ai_provider="ollama"`) now runs on **every** cover, not just when OCR is sparse. `RapidOCR` only runs as a fallback, when the vision model's guess isn't confirmed by the catalog (or fails outright) — and when it does run, `VisionService.identify` keeps whichever of the two results is more confident, so a confirmed OCR reading can still beat an unconfirmed vision guess.

Reason: with Groq now the default (see the "Architecture pivot" decision above), the model is accurate enough to trust first, and the catalog this project confirms against (Google Books / Open Library) doesn't reliably cover the specific books being scanned — so gating the vision model behind a catalog-confirmation step on OCR was routinely paying off less than just asking the model directly. Trade-off accepted knowingly: this calls Groq on every scan (API usage + a few seconds of latency), not just on hard covers.

<details>
<summary>Original reasoning (OCR-first) — superseded, kept for history</summary>

`RapidOCR` (ONNX Runtime, ~10 MB, no torch) reads the text on the cover in under a second. Text candidates are sent to Google Books and matched via fuzzy matching (`rapidfuzz`). The vision model was invoked **only** when OCR returned too little text or the matching score was weak — typically for illustrated covers without clear text. Reason at the time: on printed text, OCR is more accurate than a vision model at proper nouns, and much faster.

</details>

### Content sources: official only, no scraping

**The Google Books API does not return review text** — only `description`, `categories`, `averageRating`, `ratingsCount`. Without a corpus, RAG has nothing to retrieve. The real sources:

| Source | What it provides | License / access |
|---|---|---|
| Google Books API | description, categories, ISBN, average rating | free, no key for public volumes |
| Open Library | subjects, descriptions, editions, ratings | CC0 |
| Wikipedia REST API | plot summary + the *Reception / Critical reception* section — **the main source of critical opinion** | CC BY-SA |

All of them implement a common `ContentSource` `Protocol`. There is also a `ScraperSource` **defined but not implemented**, as an extension point. No scraping is written without an explicit request.

### Dual-purpose embeddings

The vectors generated when a book is ingested serve both RAG (Module 5) and recommendations (Module 6). We don't build two embedding pipelines.

## AI models

| Role | Provider | Model | Notes |
|---|---|---|---|
| Vision (primary, OCR is the fallback) | Groq (default) | `qwen/qwen3.6-27b` | multimodal, JSON mode, preview model — see retry notes in the pivot decision above |
| Vision (primary, OCR is the fallback) | Ollama (`ai_provider="ollama"`) | `moondream` | ~1.7 GB RAM, local |
| Summary LLM (Module 5) | Groq (default) | `openai/gpt-oss-120b` | |
| Summary LLM (Module 5) | Ollama (`ai_provider="ollama"`) | `llama3.2` (3B) | ~2.0 GB RAM, local; fallback `llama3.2:1b`, then `qwen3:4b` |
| Embeddings | Ollama, always | `nomic-embed-text` | ~275 MB RAM, stays loaded permanently (long keep-alive) — never affected by `ai_provider` |

`llama3:latest` (4.7 GB) is too large for this laptop — to be deleted, it's functionally duplicated by `llama3.2`.
`qwen3:4b` is a backup for the Ollama LLM fallback: better structured output, but "thinking mode" costs time on CPU.

## Tech stack

| Need | Library | Notes |
|---|---|---|
| API framework | `fastapi` + `uvicorn` | async by default |
| Validation / schemas | `pydantic` v2 | all request/response bodies as `BaseModel` |
| Config & secrets | `pydantic-settings` + `python-dotenv` | reads from `.env`, never hardcoded |
| ORM | `sqlalchemy` 2.0 async + `aiosqlite` | `alembic` only once the schema starts evolving |
| Auth | `python-jose[cryptography]` (JWT) + `bcrypt` directly | **not** `passlib` — 1.7.4 breaks with `bcrypt>=4.1`. Clean alternative: `pwdlib[argon2]` |
| OCR | `rapidocr-onnxruntime` | ONNX, no torch |
| Image processing | `pillow` | EXIF rotation, resize, recompression |
| Vision / LLM (default) | `groq` (Python client) | cloud — `GROQ_API_KEY` in `.env` |
| Vision / LLM (local fallback) | `ollama` (Python client) | local, `ai_provider="ollama"` |
| Vector DB | `chromadb` (persistent client) | `./data/chroma` folder, local |
| Embeddings | `nomic-embed-text` via Ollama | 768-dim, always local |
| HTTP client | `httpx` (async) | retry + backoff |
| Fuzzy matching | `rapidfuzz` | title/author normalization |
| Testing | `pytest` + `pytest-asyncio` + `httpx.AsyncClient` + `respx` | zero real network calls in the test suite |
| Formatting / linting | `black` + `ruff` | before every commit |
| Type checking | `mypy` (strict) | no unjustified `Any` |
| Logging | `structlog` | never `print()` in production code |

## Directory structure (backend)

```
backend/
├── app/
│   ├── main.py                     # FastAPI entrypoint
│   ├── core/
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   ├── security.py             # JWT, password hashing
│   │   ├── exceptions.py           # custom exceptions + handlers
│   │   └── logging.py              # structlog setup
│   ├── api/
│   │   ├── deps.py                 # dependencies (current_user, db session)
│   │   └── routes/
│   │       ├── auth.py             # register, login, refresh, logout
│   │       ├── books.py            # /analyze-cover, book details
│   │       ├── jobs.py             # GET /jobs/{id}
│   │       └── users.py            # profile, history, preferences, recommendations
│   ├── models/                     # SQLAlchemy: User, RefreshToken, Job, Book,
│   │                               #   TextSource, ReadingHistory, Preference
│   ├── schemas/                    # Pydantic (request/response)
│   ├── services/
│   │   ├── ocr_service.py          # RapidOCR + image preprocessing
│   │   ├── vision_service.py       # OCR-first, vision-model fallback (Groq or Ollama)
│   │   ├── ollama_client.py        # local backend: shared wrapper with timeout/retry
│   │   ├── groq_client.py          # cloud backend (default): shared wrapper, retry, provider switch
│   │   ├── sources/
│   │   │   ├── base.py             # ContentSource Protocol
│   │   │   ├── google_books.py
│   │   │   ├── open_library.py
│   │   │   └── wikipedia.py
│   │   ├── data_fetcher.py         # source orchestration + normalization + cache
│   │   ├── rag_service.py          # chunking, embeddings, Chroma, synthesis
│   │   └── recommendation_service.py
│   ├── workers/
│   │   └── cover_pipeline.py       # the full job: OCR → fetch → ingest → summary
│   └── db/
│       ├── session.py
│       └── init_db.py
├── tests/
│   ├── conftest.py
│   └── fixtures/                   # test covers, mocked HTTP responses
├── docs/
│   └── module-N-name.md            # summary written at the end of each module
├── data/                           # SQLite + Chroma (in .gitignore)
├── .env.example
├── pyproject.toml
└── CLAUDE.md
```

## Modules — status

One module per session. Don't move on until the tests pass.

- [x] **Module 0: Foundation** — `pyproject.toml`, venv, `config.py`, `exceptions.py`, `logging.py`, `main.py` with `/health`, `conftest.py`, `.env.example`, `.gitignore`, `git init`.
      *Done when:* `pytest` green on the health test, `mypy app/` clean, `ruff` clean.
- [x] **Module 1: Auth** — `User` + `RefreshToken` models, `POST /auth/register|login|refresh|logout`, `GET /users/me`. Opaque refresh token (SHA-256 in DB, not JWT), rotation on every refresh.
      *Done when:* tests for duplicate register, wrong login, expired token, refresh rotation. See `backend/docs/module-1-auth.md` (local, gitignored).
- [x] **Module 2: API skeleton + jobs** — routers, `deps.py`, global exception handlers, `jobs` table, `GET /jobs/{id}`, validated upload (max 8 MB, JPEG/PNG/HEIC).
      *Done when:* a fake job goes through `pending → running → done` and is visible only to its owner. See `backend/docs/module-2-schelet-api.md` (local, gitignored).
- [x] **Module 3: Vision** — Pillow preprocessing (EXIF rotation, resize 768 px, JPEG q85), RapidOCR, `OllamaClient`, Moondream fallback with `{title, author, confidence}` output, manual-correction endpoint (`PATCH /jobs/{id}/correction`).
      *Done when:* tests with a fake Ollama client + one `@pytest.mark.slow` test on 3 real covers from `tests/fixtures/covers/` (fixture images still pending — each test case skips individually until supplied). See `backend/docs/module-3-vision.md` (local, gitignored).
- [ ] **Module 4: Data fetcher & cache** — the three official sources, title+author normalization with `rapidfuzz`, `Book` + `TextSource` models, TTL cache.
      *Done when:* all HTTP mocked with `respx`, zero network calls in the suite.
- [ ] **Module 5: RAG** — chunking (~500 tokens, overlap 50), embeddings, persistent Chroma, **retrieval mandatorily filtered on `book_id`**, synthesis with Llama 3.2 + source citations, anti-hallucination prompt.
      *Done when:* on a fixture corpus, every statement in the summary is traceable to a chunk.
- [ ] **Module 6: Recommendations** — `ReadingHistory`, `Preference`, profile vector (weighted average by user rating), candidate generation from Chroma, filtering by genre and already-read books, score + explanation ("because you liked X"). Purely content-based — single user, guaranteed cold start, no collaborative filtering.
- [~] **Module 7: Client** — **started early, intentionally.** The mobile client (Expo + React Native + TypeScript) lives in `frontend/`, with its own `frontend/CLAUDE.md`. Testing on a **physical phone via Expo Go** (the Android emulator was rejected: ~1.5 GB RAM on a laptop with 7.4 GB that's also running Ollama). The `/dev` test HTML page is no longer needed — the real app replaces it.
      The frontend is at parity with Modules 0-2. See `frontend/docs/module-0-2-paritate-backend.md` (local, gitignored).

### Work pace: backend and frontend in parallel

The two parts progress **module by module, in parallel** — not backend-complete-first. After each backend module comes the corresponding frontend part, so every capability can be seen and tested as soon as it exists. Keep the "Modules — status" section in sync across both `CLAUDE.md` files.

## Useful commands

```bash
uvicorn app.main:app --reload      # start the dev server
pytest                             # run the tests
pytest -m "not slow"               # skip tests that call Ollama
black . && ruff check .            # formatting + lint
mypy app/                          # type checking
```

## Code conventions

- Type hints required on all public functions and methods.
- Docstrings (Google style) on every public function, class, and module — what it does, parameters, return value, exceptions it can raise.
- Explicit error handling: custom exceptions in `core/exceptions.py`, never `except Exception: pass`.
- Every external service (Ollama, HTTP, OCR) sits behind a `Protocol`, so it can be swapped for a fake in tests.
- Variable naming: English throughout (`book`, `reviews`, `current_user`), including domain terms — the codebase was translated from an earlier Romanian-first convention; don't reintroduce Romanian identifiers.
- Every new module comes with `pytest` tests before being considered "done".
- At the end of each module, write a summary file in `backend/docs/module-N-name.md`: what was implemented, technical decisions made, what files appeared, how to verify (test commands). See `backend/docs/module-0-fundatie.md` as an example. `backend/docs/` and `frontend/docs/` are gitignored — they're local working notes, not published to GitHub.

## Strict rules

The project is **hybrid**, not 100% local: storage and embeddings stay local; vision and LLM generation default to Groq Cloud. This is a deliberate pivot from the original all-local design — see "Architecture pivot: local vision/LLM → Groq Cloud" above for the reasoning (local model accuracy on this hardware was not good enough, and CPU inference was too slow).

- **Storage, cache, and embeddings must stay 100% local** — SQLite, ChromaDB, and `nomic-embed-text` via Ollama. No exceptions, regardless of `ai_provider`.
- **Vision and LLM generation go through Groq Cloud by default** (`Settings.ai_provider = "groq"`), using `groq.AsyncGroq` with the key in `GROQ_API_KEY`. The local path (`ai_provider = "ollama"`, Moondream + Llama 3.2) must keep working as a fallback — don't let it bit-rot or get deleted.
- **Never hardcode the provider.** Both integrations go through `Settings.ai_provider` / `Settings.vision_model` / `Settings.llm_model` in `core/config.py`, never a direct `if` on a model name scattered through the code.
- No other cloud LLM/vision APIs (OpenAI, Anthropic API, etc.) and no Google Colab — Groq is the one deliberate exception, not a general opening.
- Groq calls must handle rate limits and API errors gracefully (retry with backoff, then raise `AIProviderUnavailable`) — never let a preview-model hiccup propagate as an unhandled exception. See `groq_client.py`.
- No scraping. Only the official sources listed in the table above.
- No PyTorch in dependencies.
- `.env` never in git — only `.env.example` with keys and no real values.
- One module per session, tested and confirmed, then the next.