# CLAUDE.md — Glance

This file is automatically loaded by Claude Code at every session. Don't repeat it in prompts — update it as the project progresses.

## Overview

**Glance** — mobile app + 100% local backend. You photograph a book cover, and the app recognizes the title and author, gathers material about the book from open sources, generates a summary via RAG, and offers personalized recommendations based on reading history.

**Strict rule: no calls to cloud LLM APIs (OpenAI, Anthropic API, etc.) and no Google Colab. All AI models run locally through Ollama.**

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
      ├── RapidOCR (ONNX)          → raw text from the cover               [fast, <1 s]
      ├── Moondream (via Ollama)   → fallback when OCR is unreliable       [slow, 15-40 s]
      ├── Google Books / Open Library / Wikipedia → metadata + text about the book
      ├── SQLite                   → users, jobs, books, sources, reading_history, preferences
      ├── ChromaDB                 → embeddings (nomic-embed-text), power both RAG *and* recommendations
      └── Llama 3.2 (via Ollama)   → summary generated from retrieved context
```

## Decisions made (don't revisit without a new reason)

### Vision: OCR-first, Moondream as fallback

`RapidOCR` (ONNX Runtime, ~10 MB, no torch) reads the text on the cover in under a second. Text candidates are sent to Google Books and matched via fuzzy matching (`rapidfuzz`). Moondream is invoked **only** when OCR returns too little text or the matching score is weak — typically for illustrated covers without clear text.

Reason: on printed text, OCR is more accurate than a 1.8B VLM at proper nouns, and ~30× faster. Moondream stays in the project for cases where visual understanding is actually needed.

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

## Ollama models

| Role | Model | RAM | Fallback if too slow |
|---|---|---|---|
| Vision (fallback) | `moondream` | ~1.7 GB | — |
| Summary LLM | `llama3.2` (3B) | ~2.0 GB | `llama3.2:1b`, then `qwen3:4b` |
| Embeddings | `nomic-embed-text` | ~275 MB | stays loaded permanently (long keep-alive) |

`llama3:latest` (4.7 GB) is too large for this laptop — to be deleted, it's functionally duplicated by `llama3.2`.
`qwen3:4b` is a backup: better structured output, but "thinking mode" costs time on CPU. To be compared in Module 5.

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
| Vision / LLM | `ollama` (Python client) | local |
| Vector DB | `chromadb` (persistent client) | `./data/chroma` folder |
| Embeddings | `nomic-embed-text` via Ollama | 768-dim |
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
│   │   ├── vision_service.py       # Moondream fallback
│   │   ├── ollama_client.py        # shared wrapper with timeout/retry
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
- [ ] **Module 3: Vision** — Pillow preprocessing (EXIF rotation, resize 768 px, JPEG q85), RapidOCR, `OllamaClient`, Moondream fallback with `{title, author, confidence}` output, manual-correction endpoint.
      *Done when:* tests with a fake Ollama client + one `@pytest.mark.slow` test on 3 real covers from `tests/fixtures/`.
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

- No cloud LLM / cloud vision APIs. Everything AI-related runs locally through Ollama.
- No scraping. Only the official sources listed in the table above.
- No PyTorch in dependencies.
- `.env` never in git — only `.env.example` with keys and no real values.
- One module per session, tested and confirmed, then the next.