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
      │  GET  /books/{book_id}/summary              →  cited RAG summary, on demand
      ▼
FastAPI backend (local, on laptop)
      │
      ├── qwen/qwen3.6-27b (via Groq)  → title/author from the cover, runs first    [cloud, seconds]
      ├── RapidOCR (ONNX)              → fallback when Groq's guess is unconfirmed  [fast, <1 s]
      ├── Google Books / Open Library / Wikipedia → metadata + text about the book
      ├── SQLite                       → users, jobs, books, sources, reading_history, preferences
      ├── ChromaDB                     → embeddings (nomic-embed-text, local via Ollama), power both RAG *and* recommendations
      │                                  retrieval is ALWAYS filtered on book_id — see the decision below
      └── openai/gpt-oss-120b (via Groq) → summary generated from retrieved context, one citation per claim

Local fallback (Settings.ai_provider="ollama"): Moondream and Llama 3.2 via
Ollama take over the two Groq rows above, unchanged otherwise — see
"Architecture pivot" below.
```

## Decisions made (don't revisit without a new reason)

### Architecture pivot: local vision/LLM → Groq Cloud

**What changed:** as of this decision, `Settings.ai_provider` (default `"groq"`) selects the backend for the two AI-inference steps that were previously Ollama-only:

- **Vision** (cover title/author extraction): `moondream` (local, ~1.7 GB, 1.8B params) → `qwen/qwen3.6-27b` on Groq, multimodal, called with JSON mode so it returns `{"title": ..., "author": ...}` directly instead of free text that has to be parsed out of a rambling reply.
- **RAG summary generation** (Module 5): `llama3.2` (local) → `openai/gpt-oss-120b` on Groq.

**Why:** on this laptop's hardware (7.4 GB RAM, CPU-only — see "Hardware constraints"), the locally-sized models were not good enough to ship. Moondream misidentified titles/authors often enough to undermine the OCR-first pipeline's fallback path, and CPU inference for both vision and summary generation was slow enough (15-40 s per call) to make the async job pipeline feel worse than it needed to. Groq's hosted inference is both faster and more accurate at this model size, at the cost of the original "100% local" guarantee.

**What stayed local:** SQLite, ChromaDB, and embeddings (`nomic-embed-text` via Ollama) are unaffected — this pivot is scoped to vision and LLM-generation inference only. See "Strict rules" for the precise, current boundary.

**How it's wired:** both integrations are config-driven, not hardcoded — `Settings.ai_provider: Literal["groq", "ollama"]` in `core/config.py`, plus `Settings.vision_model` / `Settings.llm_model` computed properties that resolve to the right model name for whichever provider is active. `app/services/groq_client.py` defines `AsyncGroqClient`, which is call-compatible with the existing `OllamaClient` Protocol (`app/services/ollama_client.py`) — same `generate(model, prompt, images, format, options)` shape — so `VisionService` and `RagService` don't know or care which backend they're talking to. `get_active_ai_client()` is the single switch point; reinstating the local-only setup later means setting `AI_PROVIDER=ollama` in `.env`, no code change. Groq errors (including qwen/qwen3.6-27b's preview-model rate limits and instability) are retried with backoff and surfaced as `AIProviderUnavailable`, never left to propagate raw.

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

**Review scraping was evaluated during Module 4 and rejected on evidence.** The question was which single review site to scrape 10-15 reviews from, per book, respecting robots.txt. Checking the actual `robots.txt` of each candidate showed the two requirements are mutually exclusive:

| Site | `User-agent: *` posture on review pages |
|---|---|
| Goodreads | `Disallow: /book/reviews/`, `/review/show`, `/review/list` — precisely the review paths. API retired Dec 2020, no new keys issued. |
| LibraryThing | Review sections disallowed; only `/work/*/main` and `/author/*/main` allowed. `Crawl-delay: 2`, `Content-signal: ai-train=no`. |
| Amazon | `Disallow: /product-reviews/`; ToS prohibits automated access outright. |
| The StoryGraph | The only one not blocking review paths, but: `Content-Signal: ai-train=no, use=reference`, no public API, named AI crawlers all `Disallow: /`, and user-owned review text with unclear licence for resurfacing in generated summaries. |

So no source permits it. **Wikipedia's Reception sections are the critical-opinion corpus instead** — and for RAG they are the better input anyway: professional criticism with citations, rather than user reviews that are mostly star ratings and one-liners. Its cost is coverage, not licensing: only notable books have articles, and Romanian editions rarely do. That is handled as a normal, non-fatal outcome (fewer passages), not an error.

Consequence for the schema: there is **no `reviews` table**. `TextSource` holds every passage — description, subjects, plot, reception — tagged with `source`, `kind`, `url` and `license`, so Module 5 can cite what it retrieved and honour per-source attribution.

### Cover images: Wikipedia as a last resort, **because this build is private**

A missing cover is the most visible gap in the app — the client has a title, a blurb and a rating, and still renders a grey rectangle. So `cover_url` has a fallback chain, tried in order and stopping at the first hit (`services/sources/cover_fallback.py`):

| # | Source | Licence | When |
|---|---|---|---|
| 1 | Google Books `imageLinks.thumbnail` | Google Books ToS | primary; arrives through the normal source merge |
| 2 | Open Library Covers API, **by ISBN** | CC0 | no cover in the merge, but a catalog reported an ISBN |
| 3 | Wikipedia article lead image (REST `page/summary`) | **usually non-free** | nothing else worked, and the Wikipedia source matched an article |

Step 2 is by ISBN only, deliberately. The *title/author* route into Open Library's covers is already what `OpenLibrarySource.fetch` does, via the search hit's `cover_i`, and its result is merged ahead of this — repeating the search here would re-run the same query and get the same document back. The ISBN route reaches something the search cannot: an edition whose title Open Library's index misses but whose ISBN Google Books supplied. That is the ordinary shape of the problem for Romanian editions.

**Step 3 is the licensing compromise, and it exists only because this project is private and not distributed.** A book article illustrates itself with the publisher's cover scan, and on en.wikipedia that is almost always uploaded *locally* under a fair-use exemption rather than to Wikimedia Commons. Fair use covers the encyclopaedia article; it does not travel to a third-party app that reserves the image somewhere else. The image is used anyway here, on the same reasoning as any other personal-use copy, and the two freely-licensed steps are tried first so it is genuinely a last resort.

Which case a given image is stays visible rather than being guessed at: the hosting path (`/wikipedia/commons/` vs `/wikipedia/en/`) tells the two apart with no extra API call, and every hit logs `cover_fallback_hit` with `on_commons` and a licensing note.

**This decision must be revisited if the app is ever published, shared, or made public in any form** — including a public GitHub repo that ships a populated `data/` directory. The switch is already in place: `Settings.wikipedia_cover_fallback` (`WIKIPEDIA_COVER_FALLBACK` in `.env`) turns step 3 off and leaves steps 1-2, both freely licensed, working. Flipping it does not clear covers already cached in SQLite; those would need deleting separately.

Two smaller notes. Open Library's Covers API answers `200` with a **blank grey placeholder** for an ISBN it has no image for, so the probe passes `?default=false` to turn that into an honest `404`, and confirms the URL with a `HEAD` before storing it — an unverified URL would be a broken image pinned for the whole 30-day TTL, and would count as content in `_has_content`, flagging an otherwise empty book as found. And the chain runs *only on the gap*: a book Google Books already gave a thumbnail for makes zero extra requests.

### Wikipedia is searched in English *and* Romanian, and must prove it found a book

Three bugs here compounded into "the book is on Wikipedia and we found nothing", and they are worth keeping written down because each looked reasonable alone.

1. **The search was pinned to `en`.** A Romanian edition is scanned under its Romanian title, which en.wikipedia has never heard of: "Căpitan la cincisprezece ani" returns *zero* results there, while ro.wikipedia has the article under exactly that name. Four of five Romanian titles tested returned nothing on `en`. `Settings.wikipedia_languages` (`["en", "ro"]`) is now searched **in order, stopping at the first confident match** — not searched in parallel and ranked, because scores are not comparable across editions: a Romanian article about the wrong book scores exactly as well as an English article about the right one. English stays first because its articles carry far more critical reception, which is the point of this source.
2. **The query appended the English word "novel".** Wikipedia ANDs its search terms, so a word no Romanian article contains filtered out every result — `"Căpitan la cincisprezece ani Jules Verne novel"` returned nothing, while dropping the last word made the exact article the first hit. The query is now just title + author.
3. **The heading vocabulary was English-only**, so `ro` articles resolved correctly and then produced **zero passages**. "Baltagul (roman)" is a 32,000-character article with an *Aprecieri critice* section and it yielded nothing. Headings are now accent-folded before tokenizing (a raw `[a-z]+` split turns "Acțiune" into `ac` + `iune`) and matched against both languages' vocabularies. `SourceKind.THEMES` was added along the way: *Teme principale* / *Stil literar* / *Themes and influences* are analysis, distinct from reception, and they feed the "themes" retrieval aspect directly.

**Article kind is a whitelist, not a blacklist.** The disambiguator is stripped before scoring so "Dune (novel)" matches "Dune" — which also makes "Moarte pe Nil (film din 2022)" score 100 against the novel. Blacklisting film/album/song still let "Câmp (river)" match a book called *Câmpul* at 80, because the set of things that are not books is not enumerable. So a title that carries a disambiguator must prove it is a book — a book word in either language, or the author's own name ("Michael Strogoff (Jules Verne)") — and is otherwise discarded. An undisambiguated title is accepted but ranks below a confirmed book, which is what makes "Baltagul (roman)" win over a bare "Baltagul". Same asymmetry as everywhere else in this file: a missing article costs a few passages and is a documented normal outcome, while a wrong one produces a fluent, fully-cited summary of a film.

`record_ref` now carries the language (`"ro:Baltagul (roman)"`), because the cover fallback asks that article for its lead image and the wrong edition returns a 404 or another book's cover.

### Author is the name on the cover, not Google's contributor list

`volumeInfo.authors` is a *contributor* list — translators, illustrators and editors sit in it unlabelled and unordered. Joining it wholesale produced, for a Romanian Jules Verne edition, `Jules Verne, Anghel Ghițulescu, Simona Schileru, H. Meyer`: one author, two translators, one illustrator.

The cover is the better authority, and vision already read it. When the scanned name appears in the list, that entry wins — it is the catalog's spelling of the person the book credits, and everyone else is by elimination not who the cover named. With no scanned author to match against, the list is capped at two, so genuine co-authorship survives and a cast of ten does not.

### Retrieval is filtered on `book_id` — structurally, not by convention

**This is a correctness invariant, not a ranking preference.** A summary of *Dune* built partly from *Foundation*'s reception passages would be fluent, plausible, and wrong — and, because every claim would still carry a real citation, wrong in a way that *looks verified*. Nothing downstream can detect it. The prompt certainly can't: the model has no way to know a passage it was handed belongs to a different book.

The trap is that ChromaDB fails **silently** in exactly the directions that matter:

| What goes wrong | What Chroma does | What you'd see |
|---|---|---|
| `where=None` or `where={}` | returns the whole collection, unfiltered | plausible summaries mixing books |
| misspelled metadata key | matches nothing | every book silently has "no sources" |
| `book_id` stored as `str`, filtered as `int` | matches nothing (exact type match) | same, and only for books written by the older code path |

None of these raise. So `services/vector_store.py` enforces the constraint three independent ways: `book_id` is a **required positional parameter** of `query` (no default, no overload without it); the `where` clause is **built inside that module** from that parameter alone, so callers never pass raw filters and a typo can't reach Chroma from elsewhere; and **every returned row is re-checked** against the requested book, raising `CorpusLeak` on a mismatch. `Chunk.metadata()` pins `book_id` to `int` for the same reason, and chunk ids encode it (`b{book_id}:s{source_id}:{index}`) so a chunk is traceable to its book without a second lookup.

The isolation tests run against a **real ephemeral ChromaDB, not a fake** — a fake would only prove our filter-passing code calls itself correctly, when the whole risk is that Chroma's filter doesn't behave as assumed. The fixture corpus deliberately holds a plausible near-miss (*Foundation* alongside *Dune*: same genre, same era, same shape of award-winning reception), because a filter is only worth testing against a corpus that would genuinely rank against it.

One collection for the whole corpus, partitioned by this filter — not a collection per book. Chroma holds an HNSW index per collection, and thousands of tiny indexes cost far more memory than one, which matters on a 7.4 GB laptop.

### Adding a column needs `init_db`'s reconciliation — `create_all` is not enough

`Base.metadata.create_all` creates tables that don't exist. It will **not**
alter one that does. So adding a column to a model leaves every existing
database one column behind, and the failure lands nowhere near its cause:
`select(Book)` names every mapped column, so a single missing one breaks
*every book fetch*, not just the feature that added it — and the nearest
non-fatal `except` reports it as something vague like "fetching the book
failed".

**The test suite cannot catch this, ever.** Every test builds a fresh
in-memory database from the current metadata, so a drift between the models
and an *older* database is invisible to it by construction. This shipped
once, in Module 5 (`summary_json`, `summary_generated_at`), and the suite
was fully green throughout.

`app/db/init_db.py` therefore also reconciles **added columns** at startup,
logging each one. Deliberately nothing else: it will not drop, rename,
retype or reorder anything, and it won't touch constraints or indexes.
`tests/test_schema_reconciliation.py` covers it by building a genuinely
out-of-date database on disk. A `NOT NULL` column with no server default is
refused rather than guessed at, and logged as needing a real migration —
**that is the signal to bring in Alembic**, not to add another case here.

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
│   │   ├── http_utils.py           # shared GET+retry policy for every external source
│   │   ├── sources/
│   │   │   ├── base.py             # ContentSource Protocol, SourceResult
│   │   │   ├── google_books.py     # metadata: description, categories, ISBN, rating
│   │   │   ├── open_library.py     # CC0 gap-filler: subjects, description, cover by ISBN
│   │   │   ├── wikipedia.py        # plot + Reception — the critical-opinion corpus
│   │   │   └── cover_fallback.py   # cover chain: OL by ISBN → Wikipedia lead image
│   │   ├── data_fetcher.py         # source orchestration + normalization + cache
│   │   ├── chunking.py             # passages → ~500-token, sentence-aligned chunks
│   │   ├── embeddings.py           # nomic-embed-text via Ollama — always local
│   │   ├── vector_store.py         # Chroma + the mandatory book_id filter
│   │   ├── rag_service.py          # ingest, retrieve, synthesize, verify citations
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
- [x] **Module 4: Data fetcher & cache** — *(backend + client, both done)* the three official sources behind `ContentSource`, title+author normalization (accent/case folding for the cache key, `rapidfuzz` similarity floors so a misread cover can't match the wrong book), `Book` + `TextSource` models, TTL cache, lazy per-book fetching wired into `cover_pipeline`. On the client: real covers, description, categories, rating + ratings count, an explicit "no catalog entry" state.
      *Done when:* all HTTP mocked with `respx`, zero network calls in the suite. **Done** — 116 tests green, `mypy app/` clean, `ruff`/`black` clean, `tsc --noEmit` clean, `expo export` bundles. See `backend/docs/module-4-data-fetcher.md` (local, gitignored).
      **Correcting a title now invalidates the fetched metadata.** `PATCH /jobs/{id}/correction` clears the catalog fields, returns the job to `running`, and re-fetches in the background — otherwise the previous book's cover and blurb would sit under the corrected title, which looks authoritative and is false. The client needed no change for this: polling already resumes on a `running` job.
      **`metadata_found` means "there is something to show", not "a catalog has a row".** Google Books and Open Library both hold *bare* records — title and author, no description, cover, subjects or rating — and Romanian editions are almost all like that (observed: `Baltagul`, `Călătorie spre centrul pământului`). Keying the flag on "a source matched" marked those books found and left the client rendering its success state over an empty result, which reads as a broken app rather than an honest gap. Title and author never count as content: they came off the cover, so a book whose only metadata is its own title tells the user nothing they didn't photograph. See `_has_content` in `data_fetcher.py`.
      **An empty result gets a short TTL** (`empty_book_cache_ttl_hours`, 6 h) instead of the full 30 days. Emptiness is rarely durable — a bare record gets filled in, a degraded source recovers — and pinning it for a month made the gap permanent from the user's side, because rescanning just re-served the empty row.
      **A book with no cover now falls back to Open Library by ISBN, then to Wikipedia's article image.** See the "Cover images" decision above — step 3 rests on this build being private, and is switched off by `WIKIPEDIA_COVER_FALLBACK=false`.
      **Catalog title matching uses `token_sort_ratio`, never `token_set_ratio`.** The latter scores on the token *intersection*, so any candidate containing the query scores 100: Open Library really did return *Heretics of Dune* as the match for "Dune", and its cover, blurb and subjects were merged into Dune's entry and stored as Dune's RAG passages. `services/sources/matching.py` compares title *variants* (as-is, parentheticals stripped, subtitle dropped) so a real edition still matches while a sibling in the series doesn't. Note `vision_service.py` still uses `token_set_ratio` deliberately — it scores noisy multi-line OCR against a candidate list, a different job with its own tuning.
- [x] **Module 5: RAG** — *(backend + client, both done)* chunking (~500 tokens, overlap 50, sentence-aligned), local embeddings via `nomic-embed-text`, persistent Chroma, **retrieval mandatorily filtered on `book_id`** (see the decision below), synthesis with `openai/gpt-oss-120b` on Groq + per-claim source citations, anti-hallucination prompt *and* citation verification. `GET /books/{book_id}/summary`. On the client: the summary section fetches itself, each sentence is tappable to the passage it came from, and the publisher's blurb stays as a clearly-labelled fallback.
      *Done when:* on a fixture corpus, every statement in the summary is traceable to a chunk. **Done** — 221 tests green, `mypy app/` clean, `ruff`/`black` clean, `tsc --noEmit` clean, `expo export` bundles. See `backend/docs/module-5-rag.md` (local, gitignored).
      **The summary is not part of the scan job.** It hangs off the *book*, not the scan, and is generated on demand by its own endpoint. Three reasons: it is seconds slower than the catalog metadata and would otherwise hold the whole result screen back; it is shared by everyone who scans the same book; and a correction needs no special handling for it, because the corrected title resolves to a different `book_id` and the client simply asks for that book's summary. `job.result` therefore has **no `summary` key** at all — a field that is always `null` is a trap.
      **The summary *is* its claims.** The model returns a list of `{text, chunk_ids}` and the prose is derived server-side by joining them, rather than returning prose with `[1]` markers the client would have to parse back apart. The two cannot disagree, and "every sentence cites something" becomes checkable instead of hoped-for.
      **The prompt is not the enforcement.** Asking a model not to hallucinate is a request; the failures it doesn't catch are exactly the fluent, plausible ones. So every returned claim is verified against the chunks actually retrieved, and one citing nothing — or citing an id that wasn't in the context — is **dropped before the client sees it**. If nothing survives, the response is `available=false` and the client falls back to the publisher's blurb. What this deliberately does *not* claim is that a claim's text is faithful to the chunk it cites: that is a semantic judgement needing a second model whose errors we could not check either. The honest boundary is traceability, and the client shows the passage so the reader can judge.
      **An unavailable summary is a `200`, not an error.** `available=false` means "nothing to summarize"; a 503 means "the provider was unreachable, try again". The client renders those differently, and collapsing them would either hide a real outage or make an honest gap look broken. A failed generation is also never cached — otherwise a bad moment would deny the book a summary until its sources next expire, up to 30 days.
- [ ] **Module 6: Recommendations** — `ReadingHistory`, `Preference`, profile vector (weighted average by user rating), candidate generation from Chroma, filtering by genre and already-read books, score + explanation ("because you liked X"). Purely content-based — single user, guaranteed cold start, no collaborative filtering.
- [~] **Module 7: Client** — **started early, intentionally.** The mobile client (Expo + React Native + TypeScript) lives in `frontend/`, with its own `frontend/CLAUDE.md`. Testing on a **physical phone via Expo Go** (the Android emulator was rejected: ~1.5 GB RAM on a laptop with 7.4 GB that's also running Ollama). The `/dev` test HTML page is no longer needed — the real app replaces it.
      **The frontend is at parity with Modules 0-4** — caught up in the same session Module 4 landed, so neither side is ahead. See `frontend/docs/module-0-2-paritate-backend.md` (local, gitignored).

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
- **A swallowed exception must still be logged with `logger.exception`, and the traceback must actually render.** Several `except` blocks here are deliberately non-fatal (a catalog outage must not fail a scan), which makes the log line the only record of what went wrong. `core/logging.py` runs `format_exc_info` ahead of `JSONRenderer` for exactly this — without it, structlog serializes the literal `"exc_info": true` and drops the traceback, which is how a schema-drift bug once presented as "nothing, no reason". Covered by `tests/test_logging.py`.
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