# Glance

Photograph a book cover. The app recognizes the title and author, gathers material about the book from open sources, generates a summary in which **every sentence is traceable to a cited passage**, and suggests what to read next based on what you rated highly.

Mobile client (React Native + Expo) and a **hybrid** backend (FastAPI): your data stays on your machine, the AI inference does not.

## What runs where

The project began fully local and pivoted deliberately. The line is drawn once and holds everywhere:

| Stays **local**, always | Runs on **Groq Cloud** by default |
|---|---|
| SQLite — users, jobs, books, passages, library, journal | Vision: `qwen/qwen3.6-27b` reads title + author off the cover |
| ChromaDB — the vector store | Generation: `openai/gpt-oss-120b` writes the summary |
| Embeddings — `nomic-embed-text` via Ollama | |

**Why the pivot:** the development laptop has 7.4 GB of RAM and no GPU. Locally-sized models were not good enough to ship — Moondream misread titles often enough to undermine the pipeline, and CPU inference took 15–40 s per call.

**The local path is not dead.** Set `AI_PROVIDER=ollama` in `backend/.env` and the two cloud rows above are served by Moondream and Llama 3.2 instead, with no code change. Both paths are kept working.

No other cloud APIs are used, and nothing is scraped — only official, documented endpoints.

## Architecture

```
Client (Expo, on a physical phone)
      │  POST /books/analyze-cover (image + JWT)  →  202 { job_id }
      │  GET  /jobs/{job_id}                      →  poll until status=done
      │  GET  /books/{id}/summary                 →  cited RAG summary, on demand
      │  PUT  /books/{id}/library                 →  reading status, rating
      │  GET/POST /books/{id}/journal             →  dated reading notes
      │  GET  /users/me/library|stats|preferences →  history, counters, derived tastes
      │  GET  /users/me/recommendations           →  ranked suggestions + why
      ▼
FastAPI backend (on the laptop)
      │
      ├── qwen/qwen3.6-27b (Groq)   → title/author from the cover, runs first
      ├── RapidOCR (ONNX)           → fallback when the catalog doesn't confirm the guess
      ├── Google Books / Open Library / Wikipedia → metadata, subjects, plot, reception
      ├── SQLite                    → users, jobs, books, text_sources, library_entries,
      │                               journal_entries, recommendation_states
      ├── ChromaDB                  → `book_chunks`   — passages, retrieval always filtered by book
      │                               `book_profiles` — one vector per book, queried across books
      └── openai/gpt-oss-120b (Groq) → summary from retrieved context, one citation per claim
```

Heavy work is asynchronous: a scan returns `202` with a `job_id` and the client polls, because the full pipeline takes 30–120 s. No Celery or Redis — `BackgroundTasks` and a `jobs` table.

## A few decisions worth knowing

- **Retrieval is filtered by `book_id` structurally, not by convention.** A summary of *Dune* built partly from *Foundation*'s passages would be fluent, correctly cited, and wrong — and nothing downstream could detect it. The filter is a required parameter, the `where` clause is built inside the vector-store module, and every returned row is re-checked before use.
- **The summary *is* its claims.** The model returns `{text, chunk_ids}` pairs and the prose is joined server-side, so "every sentence cites something" is checkable rather than hoped-for. Claims citing nothing, or citing a chunk that wasn't in the context, are dropped before the client sees them.
- **Recommendation explanations are computed, never generated.** They name the nearest book you actually rated. A model asked to justify a suggestion writes something fluent about a book it was never shown, and a wrong reason beside a right recommendation is worse than no reason.
- **There are no review scrapes.** Every candidate review site disallows its review paths in `robots.txt`. Wikipedia's *Reception* sections are the critical-opinion corpus instead — professional criticism with citations, which is better RAG input anyway.
- **Empty results are honest.** A book the catalogs describe with nothing but its own title is reported as empty rather than dressed up, gets a short cache TTL instead of the full 30 days, and contributes no recommendation vector.

`CLAUDE.md` and `frontend/CLAUDE.md` carry the full reasoning, including the mistakes that produced each rule.

## Stack

- **Backend** — FastAPI, SQLAlchemy 2.0 async + SQLite, ChromaDB, RapidOCR (ONNX, no PyTorch), Groq, Ollama, structlog. Typed with `mypy --strict`.
- **Client** — React Native + Expo SDK 54, strict TypeScript, expo-router, TanStack Query, Zustand, `expo-secure-store` for tokens.

## Getting started

### Backend

Requires Python 3.11, [Ollama](https://ollama.com) (for embeddings, always), and a [Groq](https://groq.com) API key (unless you run `AI_PROVIDER=ollama`).

```bash
ollama pull nomic-embed-text

cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env          # set JWT_SECRET_KEY and GROQ_API_KEY
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 0.0.0.0
```

`--host 0.0.0.0` matters: without it uvicorn only listens on localhost and the phone can't reach it.

Running fully local instead? Set `AI_PROVIDER=ollama`, then `ollama pull moondream && ollama pull llama3.2`. On a small machine also set `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` — swapping models mid-pipeline costs minutes.

### Mobile client

Tested on a physical phone via Expo Go. The Android emulator is deliberately not used — ~1.5 GB of RAM on a laptop that is also running Ollama.

```bash
cd frontend
npm install
npx expo start
# scan the QR code with Expo Go — phone and laptop on the same Wi-Fi network
```

The backend address needs no configuration: the client infers it from the Metro host, so it works on any network. Override with `EXPO_PUBLIC_API_URL` in `frontend/.env` only if the backend runs on a different machine.

### Checks

```bash
cd backend && pytest              # 337 tests; `-m "not slow"` skips the 3 that need Ollama
cd backend && mypy app/ && ruff check . && black --check .
cd frontend && npx tsc --noEmit && npx expo export --platform android
```

## Project status

Built one module at a time, backend and client advancing in lockstep — never one ahead of the other. Both sides are complete through Module 6b.

- [x] **Module 0** — Foundation: config, logging, exceptions, health check
- [x] **Module 1** — Auth: JWT access tokens, rotating opaque refresh tokens
- [x] **Module 2** — API skeleton, async jobs, validated upload
- [x] **Module 3** — Vision: cover preprocessing, RapidOCR, model-first identification, manual correction
- [x] **Module 4** — Data fetcher: three official sources, normalization, TTL cache, cover fallback chain
- [x] **Module 5** — RAG: chunking, local embeddings, per-claim citations, citation verification
- [x] **Module 6a** — Personal library and dated reading journal
- [x] **Module 6b** — Content-based recommendations with computed explanations
- [x] **Module 7** — Mobile client, built in parallel from Module 0 onward

## License

Personal project, no license specified yet. The cover-image fallback to Wikipedia's article image relies on this build being private and undistributed — see the "Cover images" decision in `CLAUDE.md` before publishing or sharing the app. It is switched off with `WIKIPEDIA_COVER_FALLBACK=false`.
