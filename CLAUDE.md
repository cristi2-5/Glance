# CLAUDE.md — Glance

This file is automatically loaded by Claude Code at every session. Don't repeat it in prompts — update it as the project progresses.

## Overview

**Glance** — mobile app + hybrid backend (our own storage and embeddings, cloud AI inference). You photograph a book cover, and the app recognizes the title and author, gathers material about the book from open sources, generates a summary via RAG, and offers personalized recommendations based on reading history.

**Storage is ours: one PostgreSQL we run ourselves (compose locally, Azure Flexible Server in the cloud) holds the relational data *and* the vectors (pgvector); scan photos and the job queue live in Blob/Queue Storage (Azurite locally, an Azure Storage account in the cloud); embeddings come from our own Ollama container (`nomic-embed-text`) — no third-party data services.** Vision (cover recognition) and LLM generation (RAG summaries) run on **Groq Cloud**, adopted after the locally-sized models proved insufficient. See "Architecture pivot: local vision/LLM → Groq Cloud" below and the "Strict rules" section for the current, precise boundary.

## Hardware constraints (decisive for design)

Development laptop: **7.4 GB total RAM**, CPU-only, Windows 11, Python 3.11.9, Ollama 0.32.6.

Non-negotiable consequences:

1. **Never load two Ollama models simultaneously.** Set `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=5m`. Reloading costs 2-4 s; swapping would cost minutes.
2. **The full pipeline takes 30-120 s.** So heavy endpoints are **asynchronous**: they return `202 Accepted` + `job_id`, and the client polls `GET /jobs/{id}`. No Celery/Redis — the api stores the photo in Blob Storage and enqueues a message on Queue Storage, a separate **queue worker** container runs the pipeline, and a `jobs` table in the database holds the state (see "Blob + queue + queue worker" below).
3. **We avoid PyTorch.** ~2.5 GB of disk install and hundreds of MB of RAM for something Ollama already does. Embeddings via `nomic-embed-text`, OCR via ONNX Runtime.

## Architecture

```
Client (mobile app)
      │  POST /books/analyze-cover (image + JWT)  →  202 { job_id }
      │  GET  /jobs/{job_id}                        →  poll until status=done
      │  GET  /books/{book_id}/summary              →  cited RAG summary, on demand
      │  PUT  /books/{book_id}/library              →  reading status, rating
      │  GET/POST /books/{book_id}/journal          →  dated reading notes
      │  GET  /books/{book_id}/library/photo        →  your own photo of the book (owner only)
      │  GET  /users/me/library|stats|preferences   →  history, counters, derived tastes
      │  GET  /users/me/recommendations             →  ranked suggestions + why
      ▼
FastAPI backend (api container)
      │  photo -> Blob Storage (private `scans`), {kind, job_id} -> Queue `cover-jobs`
      │  (Azurite locally, Azure Storage in the cloud)
      ▼
Queue worker (same image, `python -m app.workers.queue_worker`) runs the pipeline:
      │
      ├── qwen/qwen3.8-27b (via Groq)  → title/author from the cover, runs first    [cloud, seconds]
      ├── RapidOCR (ONNX)              → fallback when Groq's guess is unconfirmed  [fast, <1 s]
      ├── Google Books / Open Library / Wikipedia → metadata + text about the book
      ├── PostgreSQL + pgvector        → users, jobs, books, sources, library_entries, journal_entries,
      │                                  recommendation_states, plus the embeddings
      │                                  (nomic-embed-text, local via Ollama) in two tables:
      │                                  `book_chunks`   — one row per passage, retrieval ALWAYS filtered
      │                                                    on book_id (Module 5's invariant, see below)
      │                                  `book_profiles` — one row per whole book, queried ACROSS books
      │                                                    (Module 6b) — separate table, separate rule
      └── openai/gpt-oss-120b (via Groq) → summary generated from retrieved context, one citation per claim

Local fallback (Settings.ai_provider="ollama"): Moondream and Llama 3.2 via
Ollama take over the two Groq rows above, unchanged otherwise — see
"Architecture pivot" below.
```

## Decisions made (don't revisit without a new reason)

### Architecture pivot: local vision/LLM → Groq Cloud

**What changed:** as of this decision, `Settings.ai_provider` (default `"groq"`) selects the backend for the two AI-inference steps that were previously Ollama-only:

- **Vision** (cover title/author extraction): `moondream` (local, ~1.7 GB, 1.8B params) → `qwen/qwen3.8-27b` on Groq, multimodal, called with JSON mode so it returns `{"title": ..., "author": ...}` directly instead of free text that has to be parsed out of a rambling reply.
- **RAG summary generation** (Module 5): `llama3.2` (local) → `openai/gpt-oss-120b` on Groq.

**Why:** on this laptop's hardware (7.4 GB RAM, CPU-only — see "Hardware constraints"), the locally-sized models were not good enough to ship. Moondream misidentified titles/authors often enough to undermine the OCR-first pipeline's fallback path, and CPU inference for both vision and summary generation was slow enough (15-40 s per call) to make the async job pipeline feel worse than it needed to. Groq's hosted inference is both faster and more accurate at this model size, at the cost of the original "100% local" guarantee.

**What stayed local:** PostgreSQL (with the vectors) and embeddings (`nomic-embed-text` via Ollama) are unaffected — this pivot is scoped to vision and LLM-generation inference only. See "Strict rules" for the precise, current boundary.

**How it's wired:** both integrations are config-driven, not hardcoded — `Settings.ai_provider: Literal["groq", "ollama"]` in `core/config.py`, plus `Settings.vision_model` / `Settings.llm_model` computed properties that resolve to the right model name for whichever provider is active. `app/services/groq_client.py` defines `AsyncGroqClient`, which is call-compatible with the existing `OllamaClient` Protocol (`app/services/ollama_client.py`) — same `generate(model, prompt, images, format, options)` shape — so `VisionService` and `RagService` don't know or care which backend they're talking to. `get_active_ai_client()` is the single switch point; reinstating the local-only setup later means setting `AI_PROVIDER=ollama` in `.env`, no code change. Groq errors (including the vision model's preview-model rate limits and instability) are retried with backoff and surfaced as `AIProviderUnavailable`, never left to propagate raw.

**`reasoning_effort` is per-model, and the wrong value is a 400 on every call.** Both Groq models here are reasoning models, and we ask them for small structured JSON — left at their defaults they spend the completion budget on hidden chain-of-thought and get truncated mid-object. So it is turned down as far as each allows. But *how far* differs by family and Groq validates it server-side: Qwen3 accepts `none`/`default`, while `openai/gpt-oss-120b` cannot turn reasoning off at all and rejects anything but `low`/`medium`/`high`. A single constant in `groq_client.py` therefore cannot be right for both — it shipped as `"none"`, which was correct for vision and made **every** RAG summary fail with a 400 while the vision path stayed green. The value now comes from `Settings.groq_vision_reasoning_effort` / `groq_llm_reasoning_effort`, resolved by `Settings.groq_reasoning_effort_for(model)`, which returns `None` for any unrecognised model so nothing is guessed. **Changing a Groq model name means checking its reasoning vocabulary too.** Note the 400 is deliberately not retried (it is a request-shaping bug, not a transient one), which is why it surfaced instantly rather than degrading — the right trade-off, but it means a shape error is total.

**Groq retires preview models, and a retired vision model fails quietly.** `qwen/qwen3.6-27b` was shut down on 2026-09-14 in favour of `qwen/qwen3.8-27b` (see console.groq.com/docs/deprecations). Every vision call then gets a non-retryable error, which becomes `AIProviderUnavailable`, which `VisionService` treats as "the model failed" and hands over to OCR — so scans keep completing, just with noticeably worse recognition. Nothing is red. Qwen 3.8 accepts the same `reasoning_effort` vocabulary (`none`/`default`/`low`/`medium`/`high`), so the swap was a name change only. Note that `.env` sets `GROQ_VISION_MODEL` explicitly and overrides the code default, so a model swap has to be made in **both**. Check the deprecations page whenever recognition quality drops for no visible reason.

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

**This decision must be revisited if the app is ever published, shared, or made public in any form** — including a public GitHub repo that ships a populated database dump. The switch is already in place: `Settings.wikipedia_cover_fallback` (`WIKIPEDIA_COVER_FALLBACK` in `.env`) turns step 3 off and leaves steps 1-2, both freely licensed, working. Flipping it does not clear covers already cached in the database; those would need deleting separately.

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

History: the chunks used to live in ChromaDB, which failed **silently** in exactly the directions that matter (`where=None` / `where={}` returned the whole collection, a misspelled metadata key matched nothing, a `str` `book_id` filtered as `int` matched nothing — none of them raised). Since cloud step 3 they are rows in `book_chunks` (PostgreSQL + pgvector), and the same risks have SQL spellings: **a refactor that drops the `WHERE`** (the query still succeeds and ranks the whole corpus), and **an ANN index** (see below). The constraint is therefore enforced in four independent ways in code, plus one by the database:

1. `book_id` is a **required positional parameter** of `VectorStore.query` (no default, no overload without it).
2. The `WHERE book_chunks.book_id = :book_id` is **built inside `services/vector_store.py`** from that parameter alone — callers pass no filters and no `select`.
3. **Every returned row is re-checked** against the requested book, raising `CorpusLeak` on a mismatch.
4. **Only `vector_store.py` (and the models package) may reference the `BookChunk` model** — `tests/test_vector_store.py` scans `app/` and fails otherwise, so nobody can write a second, unfiltered chunk query next to the guarded one. A second scan (AST) fails on any non-docstring string naming the `book_chunks` table outside the store and its model, which catches the bypasses the model check cannot see: `text("... FROM book_chunks")`, `table("book_chunks")`, `metadata.tables["book_chunks"]`.
5. **Foreign keys:** `book_chunks.book_id` → `books` and `book_chunks.text_source_id` → `text_sources`, both `ON DELETE CASCADE`. A chunk of a book that doesn't exist, or of a source row that was replaced, cannot persist. This is also what fixed a latent bug: a Module 4 refresh replaces a book's `TextSource` rows (delete-orphan), but the old Chroma chunks survived, so `count_for_book` stayed above zero, the next summary never re-ingested, and retrieval cited passages whose sources were gone. Now the refresh deletes its chunks in the same transaction and the next summary rebuilds the corpus.

**There is deliberately no HNSW/IVFFlat index on `book_chunks.embedding`.** pgvector applies a `WHERE` *after* the approximate scan: an HNSW index yields its nearest ~40 candidates across *all* books and only then filters them, so a small book surrounded by other books' nearer chunks silently returns fewer rows than it has — or none, which reads as "this book has no passages". Instead there is a b-tree on `book_id` and an exact distance sort over that one book's ~10-60 chunks: correct by construction and fast. `book_profiles` is queried across books but holds hundreds to low thousands of rows, so it is an exact scan too. If either ever outgrows that, add HNSW *together with* `hnsw.iterative_scan` and keep `test_a_small_book_among_many_far_away_ones_still_returns_all_its_chunks` and `test_vector_tables_exist_and_have_no_approximate_index` honest — they exist to fail when someone adds an index without thinking about this.

**The stores run on the caller's session and never commit** (`PgVectorStore`, `PgProfileStore`: every Protocol method takes `db: AsyncSession` first). A store with a session of its own would not see rows the caller has written but not committed (e.g. a just-persisted candidate `Book`), and with the foreign key Postgres would make its insert wait on the caller's open transaction — the same coroutine, so a hang. The callers commit where it matters: `RagService.summarize` right after ingesting (so a failed model call doesn't throw the embedding work away) and `RecommendationService._ensure_profiles` after writing vectors.

The isolation tests run against the **real PostgreSQL + pgvector, not a fake** — a fake would only prove our filter-passing code calls itself correctly, when the risks are in the database's behaviour. The fixture corpus deliberately holds a plausible near-miss (*Foundation* alongside *Dune*: same genre, same era, same shape of award-winning reception), because a filter is only worth testing against a corpus that would genuinely rank against it.

One table for the whole corpus, partitioned by this filter — not a table per book.

**Azure note (step 7):** Flexible Server only allows `CREATE EXTENSION vector` once `VECTOR` is in the `azure.extensions` server parameter (the allow-list); migration `0002_pgvector` runs `CREATE EXTENSION IF NOT EXISTS vector` first and fails without it.

### Schema changes go through Alembic (supersedes `init_db`'s column reconciliation)

`create_all` never alters an existing table, so a model gaining a column
once left every older database one column behind: `select(Book)` named the
missing column and *every* book fetch failed, reported by the nearest
`except` as something vague. The suite stayed green because it always built
its database from the current metadata. `init_db` used to patch that by
adding missing columns; it handled only that one case and said Alembic was
the answer for anything else. It now is: see the next decision.

### Containers (cloud step 1)

**The backend runs as two containers, `api` and `ollama`, and no application code changed to make that happen.** The point is that a cloud platform runs images, not a venv on a laptop; if it runs under `docker compose` here, later steps only change where data lives, not how the app starts. Both images run as non-root users.

**`backend/Dockerfile` installs from `backend/requirements.lock`, not from `pyproject.toml`'s ranges.** The ranges re-resolve on every fresh build, so the first container build silently got newer libraries than the venv the suite had been passing on (`chromadb` 0.6 vs 0.5, `numpy` 2.x). The lock is copied alone before the install, so the ~770 MB dependency layer is cached until the lock itself changes. Its header holds the regenerate command. A `test` stage (`docker compose --profile test run --rm test`, which also starts `postgres` and points `TEST_DATABASE_URL` at its `glance_test` database) runs the suite **inside Linux on exactly those versions** — the laptop is Windows, the cloud is not — and is never part of the shipped image.

**OpenCV is swapped for `opencv-python-headless` at the same version, inside the build.** `rapidocr-onnxruntime` depends on the GUI build, which links libGL, X11 and Qt and needed ~220 MB of system packages; a server never opens a window, and both builds install the same `cv2` module. The lock therefore names `opencv-python`, and the swap happens after the install — which is why the regenerate command rewrites `-headless` back.

**`app/` is copied once, into `/app`, and never `pip install`ed.** An installed second copy in site-packages would win whenever the process starts outside `/app`, moving `BACKEND_DIR` and with it the paths the app derives from it. Compose sets `DATABASE_URL` explicitly for the same reason.

**The one-uvicorn-worker rule is lifted (step 4).** Vectors live in Postgres (step 3) and scans run in the queue worker container fed by Queue Storage, so nothing that matters lives in the api process. The Dockerfile no longer passes `--workers`; uvicorn defaults to 1 and honours `WEB_CONCURRENCY`. In Azure the scaling unit is replicas. The `worker` compose service runs the same image with `python -m app.workers.queue_worker` and no ports; `azurite` (pinned tag) emulates Blob + Queue, volume `azurite-data`, ports bound to 127.0.0.1 for host-side pytest.

**`nomic-embed-text` is baked into the `ollama` image at build time, pinned to `v1.5`.** Containers are restarted freely in the cloud; if the model were pulled on first start, every fresh container would spend its first minutes downloading ~275 MB while `api` waited on it. It is pinned because a moved `latest` tag would make new vectors stop matching the ones already stored; the build aliases `v1.5` to `nomic-embed-text:latest` so `Settings.ollama_embedding_model` needs no change. The pull runs under `set -e` and the build asserts the model exists, so a network blip fails the build instead of shipping an image with no model.

**The `ollama` image is CPU-only, built from the official one rather than on it.** `ollama/ollama` ships ~4.6 GB of CUDA/MLX/Vulkan libraries that a CPU container never loads — 93% of a 3.65 GB compressed image, which is what the registry stores and every cold start downloads. A multi-stage build copies just the binary, the CPU runner libraries and the model onto `ubuntu:24.04`, and deliberately *not* `/root/.ollama`, which holds an identity key generated by the build-time `ollama serve`. Note Docker Desktop's listed image size adds compressed and uncompressed layers together; quote `docker image inspect --format '{{.Size}}'` instead. The fallback models (`moondream`, `llama3.2`) are deliberately *not* baked in — ~3.7 GB for a path that is off by default. With `AI_PROVIDER=ollama`, pull them into the running container (`docker compose exec ollama ollama pull moondream`).

**`ollama` publishes no port.** Only `api` needs it, and it reaches it by service name on the compose network (`OLLAMA_HOST=http://ollama:11434`). This mirrors an internal-ingress service in the cloud and keeps an unauthenticated model server off the host's network. A native Ollama on the host's 11434 does not conflict for that reason.

**`.env` is injected at run time (`env_file`), never baked into an image.** `.dockerignore` excludes it, so `docker build` cannot copy secrets into a layer. A secret store takes over this role in the cloud. The api container keeps no state on its own disk: the database (relational data and vectors) lives in the `postgres` service's volume `glance-pg`.

### PostgreSQL + Alembic (cloud step 2)

**Why.** The previous store, SQLite, was a library inside the api process writing a file on its disk. In the cloud that breaks three ways: container disks are throwaway (a restart loses every account and library); a file belongs to one machine, so the separate worker (step 4) and extra api replicas cannot share it, and SQLite over a network share (Azure Files/SMB) corrupts under concurrent writes; and `init_db` could only add columns. Postgres is a server many containers connect to: a `postgres` compose service locally (`pgvector/pgvector:pg17`), Postgres Flexible Server in Azure.

**Migrations run as a one-shot `migrate` service, not at api startup.** With several replicas each would try to migrate at the same moment. Same image as `api`, command `alembic upgrade head`; in step 8 it becomes a Container Apps Job. `api` depends on `migrate: service_completed_successfully`. At startup `init_db` only *checks* that the database is at the head revision and otherwise logs `schema_revision_mismatch` (saying whether the database is behind, or *ahead* after a rollback to an older image) and raises `SchemaOutOfDate`, so a stale schema stops the api instead of failing obscurely later.

**The drift test replaces the old safety net.** `tests/test_migrations.py` runs `alembic upgrade head` on an empty database and asserts Alembic's `compare_metadata` finds no difference from the models: the bug class (model changed, no migration) the suite could never see before. It also checks upgrade, downgrade, upgrade. After changing a model: `alembic revision --autogenerate -m "..."`, then review the script by hand.

**`Text`, not `String(n)`, for external free text.** A bounded VARCHAR is enforced by Postgres, so a long Wikipedia URL or title would crash the scan. `title`, `author`, `normalized_key`, `cover_url`, `heading`, `url` and `seed` are `Text`; fields with a natural bound (email, token hash, status, ISBNs, source/kind/licence) stay bounded. Likewise the Boolean server default is `sa.false()`, not `text("0")`, which Postgres rejects.

**Hazard (resolved in step 3): book ids vs the old vector store.** A fresh Postgres restarts ids at 1 while the file-based Chroma store still held chunks for the *old* book 1, 2, 3, so the new book 1 retrieved the old book 1's passages, and the `CorpusLeak` re-check could not catch it because the id matched. Since step 3 chunks are rows in the same database with a foreign key to `books`, so the hazard is gone structurally.

**SQLite support ended in step 3.** pgvector has no SQLite equivalent, and the isolation tests must run against the real store, so Postgres is required everywhere, including `pytest`: `docker compose up -d postgres` first. `aiosqlite` is no longer a dependency, and `Settings.database_url` defaults to the compose dev database (`postgresql+asyncpg://glance:glance@127.0.0.1:5433/glance`). The suite fails at once, with that instruction, if the database is unreachable — it never skips. The schema is built once per session and every test starts with `TRUNCATE ... RESTART IDENTITY CASCADE`; `tests/test_migrations.py` uses a throwaway database per test so it can start from an unmigrated one.

### Blob + queue + queue worker (cloud step 4)

**What changed:** `POST /books/analyze-cover` no longer runs the pipeline in the api process. The api normalises the photo (`preprocess_cover`: EXIF rotation, 768 px, JPEG), stores it in a **private Blob container** (`scans/{uuid4}.jpg`), puts a message on **Queue Storage** (`cover-jobs`) and answers 202. A separate **queue worker** container (same image, command `python -m app.workers.queue_worker`, no ports) takes the message and runs the unchanged `process_cover` / `refresh_book_data`. `PATCH /jobs/{id}/correction` does the same with a `refresh` message. The client is unchanged (202 + polling). Locally both storage services are **Azurite**, Microsoft's official emulator, driven by the real Azure SDK; only the storage settings change in Azure (managed identity, see "Security & identity (cloud step 5)").

**Why:** with `BackgroundTasks` the photo lived in the api's RAM, so a restart or scale-in mid-scan lost it and left the job `running` forever, the heavy work could not leave the api, and it was the last reason for the single uvicorn worker. (Terminology: "queue worker" is this container; "uvicorn worker" is a web-server process.)

**Messages carry ids only** (`{"kind": "scan"|"refresh", "job_id": 7}`, validated with pydantic on receive). The photo's blob name is on the job row and a correction's title/author are already in `job.result`; nothing about the user or the book travels through the queue.

**The job is committed before it is enqueued.** Order: commit the `pending` job, upload the photo, enqueue. A fast queue worker must never dequeue a message for a job that is not in the database yet, and a message only ever points at a photo that exists. If the upload or the enqueue fails, the job is marked `failed`, the photo removed, and the api answers 503. A failed enqueue on a correction puts the job back to `done` so the PATCH can be retried.

**Delivery is at-least-once, so handling is idempotent.** A message is deleted only when its job reached a final state; if the queue worker dies, the message reappears after `job_visibility_timeout_seconds` (300, longer than the pipeline). Job `done`/`failed` on delivery means a duplicate (message deleted, nothing re-run); `running` means a previous queue worker died, so it runs again (the book fetch is cached, the library write is unique per user and book); a missing job is logged and dropped. Unexpected exceptions leave the message on the queue.

`running` is ambiguous on purpose, so the scan check is more than a status check: it means "a scan is in progress or was interrupted" *or* "a correction is waiting for its refresh". A correction leaves the job `running` **with a `result`**, and a scan run writes `result` only when it reaches `done` — so a SCAN message for a job that already has a `result` is treated as settled (otherwise a late redelivery would re-run the scan over the reader's corrected title). A REFRESH message expects `running`. The `refresh` message also carries `previous_book_id` (the misidentified book): the correction endpoint resets `book_id` in `job.result` before queueing, so the message is the only place it survives, and without it the untouched library entry of the wrong book was never discarded.

**Long jobs keep their message (heartbeat).** While a message is handled, a heartbeat task calls `JobQueue.extend` every half visibility timeout, so a slow job is not handed to a second queue worker. Azure issues a **new pop receipt on every update**, so the final delete uses the newest one (`_Lease` in `queue_worker.py`). A failed extension is logged, not fatal. On SIGTERM the queue worker finishes the message in hand; that needs the platform to wait: compose `stop_grace_period: 150s` (set on the `worker` service) and, in step 7, the Container App's termination grace period (`terminationGracePeriodSeconds`, max 600) must be at least the worst-case job. If it is killed anyway nothing is lost: the message reappears once the last extension runs out.

**Accepted gaps (known, deliberately not closed).** (1) The api dying between committing the `pending` job and sending its message leaves a `pending` job nothing will pick up; the client's polling just never sees it finish. A sweeper for old `pending` jobs would close it, and was judged not worth it yet. (2) Jobs left `running` by the old `BackgroundTasks` era (before this step) have no message and never finish. (3) A crash between the `done` commit and `_record_in_library` loses that scan's library entry (the job is `done`, so the redelivered message is a duplicate and does not re-run it); the reader can rescan. All three trade a rare, visible, recoverable loss for not needing a transactional outbox.

**A correction of an old scan only uncounts that scan.** `discard_scan_artifact` removes the misidentified entry only when `scan_count == 1`; with more, the reader scanned the book again on purpose, so the entry and its photo stay and `scan_count` drops by one. `attach_photo` locks the entry (`SELECT ... FOR UPDATE`) so two queue workers cannot lose each other's photo pointer. Failing a job never deletes a photo a library entry still points at (`fail_job` checks), so a pointer never dangles.

**Poison messages.** One that does not parse, or has `dequeue_count > job_max_attempts` (3), is dropped: the job fails with "This scan could not be processed. Please try again." and `job_poisoned` is logged. A pipeline *outcome* (unreadable cover) already ends `failed` on the first delivery and is not retried. A poisoned refresh keeps its photo (the library entry owns it).

**Photo lifecycle (one rule).** A library entry owns at most one photo, the latest of that book; the job also points at it (the queue worker reads it from there, a correction moves it). A `failed` job deletes its photo. On `done`, `attach_photo` points the entry at the job's photo; if that *replaces* an older photo, the old name is erased from every job/entry row and the blob deleted. Removing the library entry deletes its photo. A correction moves the photo to the corrected book's entry (detached from the misidentified one first, so it is never deleted by accident; a discarded artifact entry's other, older photo is deleted). Blob deletion is best-effort and always after the database commit: the worst case is an orphaned ~50 KB file, never a dangling pointer. A scan that resolved no book keeps its photo on the job so a later correction can attach it.

**Photos are served only by the api, never by URL.** The container is private, with no public or SAS links (**it must stay private in Azure**). `GET /books/{book_id}/library/photo` resolves the blob through the user's library entry (same ownership rule as the journal: another reader's entry simply does not match, 404), `Cache-Control: private`. Schemas expose only `has_photo`. The client falls back catalog cover -> your photo -> initial, and shows a "Your photo" thumbnail on the book screen.

**Azurite is required by the test suite**, like Postgres: the adapter tests run against the real emulator (a fake would only restate the queue semantics the design depends on), and conftest fails the session, never skips, if it is down.

**Step 7 note:** scale the queue worker with a KEDA `azure-queue` scale rule (min replicas 0); the visibility timeout must stay above the longest scan (the heartbeat covers overruns). Set the Container App's termination grace period (`terminationGracePeriodSeconds`, max 600) to at least the worst-case job: the default 30 s is shorter than the 30-120 s pipeline, so a scale-in would otherwise kill the queue worker mid-scan.

### Security & identity (cloud step 5)

**Goal:** in Azure, no container holds a storage key or a database password; the few remaining secrets come from Key Vault; a misconfigured production container refuses to start; and the platform can tell a frozen container from a healthy one. This step is **code only** — the identities, roles and Key Vault are created by Bicep in step 7, and the real token flow can only be exercised at deploy time. Locally nothing changes: the default path is still Azurite + a password.

**Managed identity, not keys.** `Settings.azure_storage_account_name` set → Blob and Queue clients are built for `https://<name>.blob|queue.core.windows.net` with a `ManagedIdentityCredential(client_id=azure_client_id)`; unset → the Azurite connection string, exactly as before. `DATABASE_AUTH=entra` → asyncpg's `password` is an async callable returning a token for `https://ossrdbms-aad.database.windows.net/.default` (verified in asyncpg 0.32's `_connect_addr`: a callable password is called, and awaited if awaitable, for **every** new connection — so a pooled replica that lives for days always logs in with a fresh token; `azure-identity` caches and renews it). One `build_engine(url, settings)` in `db/session.py` is used by the app **and** `migrations/env.py`, so the `migrate` job authenticates like the api. `app/core/identity.py` is the only place credentials are created (a module-level factory, replaced by a fake in tests).

**`ManagedIdentityCredential`, never `DefaultAzureCredential`.** The latter walks a chain (env vars, workload identity, Azure CLI, a developer's `az login`...): slow to fall through and able to silently authenticate as whoever is logged in. A production container must use the identity assigned to it or fail loudly.

**What is still a secret, and where it lives**

| Secret | Env var | In Azure |
|---|---|---|
| JWT signing key | `JWT_SECRET_KEY` | Key Vault secret → Container App secret reference → env var |
| Groq key | `GROQ_API_KEY` | same |
| Google Books key | `GOOGLE_BOOKS_API_KEY` | same |

Nothing else: no storage key, no database password (production refuses both). The app already reads these from the environment, so Key Vault needed no code. The Container App reads the vault with its identity.

**RBAC per identity (step 7 creates these)**

| Identity | Storage | Key Vault | PostgreSQL |
|---|---|---|---|
| api | Storage Blob Data Contributor + Storage Queue Data Message Sender **scoped to the `cover-jobs` queue** | Key Vault Secrets User | its own Entra role: DML only |
| queue worker | Storage Blob Data Contributor + **Storage Queue Data Contributor scoped to the `cover-jobs` queue** | Key Vault Secrets User | its own Entra role: DML only |
| migrate job | — | — | its own Entra role that owns the schema (DDL) |

Why Contributor for the queue worker: the heartbeat (`JobQueue.extend`) calls Update Message, which needs `queues/messages/write`; *Storage Queue Data Message Processor* has only read + process (receive, delete) and would make every extension a 403, so any job longer than the visibility timeout would be handed to a second queue worker. Contributor is scoped to the one queue, so it cannot touch other queues. The api only sends, so Message Sender is enough.

**Each container app / job has its own user-assigned identity, hence its own `AZURE_CLIENT_ID`** (the client id of *that* container's identity; `ManagedIdentityCredential(client_id=...)` picks it). Never share one id across containers: it would collapse the table above into the union of all rights. Each container thus holds two credentials, a storage one and a database one, both minted from its own identity.

The adapters never create the container or queue in identity mode (`_ready = account_url is not None`); Bicep creates both up front, because these roles cannot create them.

**Postgres Entra bootstrap (step 7, one-time, by the Entra admin).** Three identities: `<api>`, `<queue-worker>` and `<migrate>` (the migrate job gets its own identity, the one that owns the schema; the other two only do DML). Names are the managed identities' exact, case-sensitive names, and `DATABASE_URL` carries that name as the user and the server host, no password.

1. Connected to the `postgres` database as the Entra admin, for each of the three: `select * from pgaadauth_create_principal('<identity-name>', false, false);`
2. Connected to `glance` as the Entra admin:
   - `CREATE EXTENSION IF NOT EXISTS vector;` (a non-admin principal cannot create it, so the admin does; migration `0002_pgvector`'s `IF NOT EXISTS` then does nothing. `VECTOR` must also be in `azure.extensions`.)
   - `GRANT CREATE, USAGE ON SCHEMA public TO "<migrate>";`
   - `GRANT USAGE ON SCHEMA public TO "<api>", "<queue-worker>";`
   - `ALTER DEFAULT PRIVILEGES FOR ROLE "<migrate>" IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "<api>", "<queue-worker>";`
   - `ALTER DEFAULT PRIVILEGES FOR ROLE "<migrate>" IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "<api>", "<queue-worker>";`

   The default privileges apply to tables the migrate role creates *afterwards*, which is every table (the first migration runs after this bootstrap).

**TLS to Postgres is verified in code.** asyncpg's default `sslmode=prefer` encrypts when the server agrees but neither verifies the certificate nor refuses a downgrade to plaintext, so a man-in-the-middle could read the Entra bearer token sent as the password. `build_engine` therefore passes `connect_args["ssl"] = ssl.create_default_context()` (verifies chain and host name against the system CAs, present in `python:slim`) whenever `database_auth == "entra"` or `app_env == "production"`; local dev with a password stays plain. Not done through `?sslmode=verify-full` in the URL: asyncpg would then look for `~/.postgresql/root.crt`.

**The migrate job is not behind `validate_for_production`.** `migrations/env.py` does not call it, so a migrate job started without `APP_ENV=production` is not refused. Acceptable: the job runs only Alembic's DDL, holds no application secret (no JWT/Groq keys), has no ingress, and authenticates with its own managed identity; TLS verification follows `DATABASE_AUTH=entra`, not `APP_ENV`, so the part that matters (no password, verified TLS) still holds. Give it `DATABASE_AUTH=entra` like the others.

**Production guard.** `APP_ENV=production` makes startup (api lifespan and queue worker `main()`) call `Settings.validate_for_production()`, which raises `InsecureConfiguration` listing **every** problem at once. The rules live in the pure `Settings.production_problems()`: JWT secret is the shipped default or under 32 chars; `cors_origins` contains `"*"`; `AZURE_STORAGE_ACCOUNT_NAME` is not set (production accepts no connection string at all, neither Azurite's nor one with an account key); `DATABASE_AUTH` is not `entra` (no database password in production; the compose `glance`/`glance` credentials are named in the message when present), or, with `DATABASE_AUTH=entra`, the URL carries *any* password; `GROQ_API_KEY` missing while `AI_PROVIDER=groq`. Production also removes `/docs`, `/redoc`, `/openapi.json` (`create_app(settings)` in `main.py`; `app = create_app(get_settings())`). **The guard cannot be bypassed by forgetting `APP_ENV`.** `validate_for_production` also runs with `APP_ENV=development`: if `CONTAINER_APP_NAME` (injected by Container Apps), `AZURE_STORAGE_ACCOUNT_NAME` or `DATABASE_AUTH=entra` is set, startup is refused with "Running in Azure without APP_ENV=production". A laptop (Azurite, password, none of those variables) is unaffected. Wikipedia cover fallback stays ON (private build; user decision 2026-10-29) — see the Cover images decision.

**CORS** defaults to `[]`: the client is a native app and CORS only governs browsers.

**Probes — liveness has no dependencies on purpose.**

| Probe | Endpoint | Checks | On failure |
|---|---|---|---|
| api startup + liveness | `GET /health` | the process answers; nothing else | restart |
| api readiness | `GET /ready` | `SELECT 1` on Postgres, 2 s timeout → 200/503 | traffic withheld, **no restart** |
| queue worker liveness | `GET :8001/health` (stdlib `asyncio.start_server`) | polling loop ticked within `queue_worker_loop_stale_seconds` (300) **or** the message in hand is younger than `job_max_runtime_seconds` (600) | restart |

A liveness probe that checked Postgres would make Azure kill and restart every healthy container during a database outage, in a loop, fixing nothing. Readiness only stops routing. The 300 s loop window must exceed the 10 s idle backoff (an idle queue worker sleeps that long between ticks); `Settings` rejects a configuration where it does not. It is that large because one receive can block inside the SDK's retries during a storage or token outage; the queue worker's storage clients therefore use bounded retries and timeouts (`QUEUE_WORKER_CLIENT_OPTIONS`: `retry_total=3`, 2 s backoff base, 20 s connect/read timeouts, about 3 minutes worst case per call), so an outage fails a receive in finite time and the window cannot expire on a healthy container. The api keeps the SDK defaults. The heartbeat stops extending a message after `job_max_runtime_seconds`, so a hung pipeline turns liveness unhealthy, the container is restarted, and the message reappears and is retried (before, a hung job was extended forever). Compose re-enables the queue worker's healthcheck against `127.0.0.1:8001` with no published port.

**A hung job is cancelled, and the probe timing must respect it.** The handling of one message runs inside `asyncio.timeout(job_max_runtime_seconds)`; on expiry the coroutine is cancelled, `queue_job_timed_out` is logged, and the message stays on the queue (treated like any unexpected failure, so the poison limit eventually applies). Work blocked in a thread (`asyncio.to_thread`, e.g. OCR) cannot be cancelled; there the platform restart is the only stop, so in step 7 the liveness `failureThreshold x periodSeconds` must elapse **before** `job_max_runtime_seconds + (job_visibility_timeout_seconds - heartbeat interval)` (600 + 150 = 750 s with the defaults: the last extension's remaining life), or a second queue worker could run the job while the first is still stuck.

**Honest limit.** Managed identity removes *stored* secrets, not *access*: any code running inside the container (a compromised dependency, an RCE) can ask the local identity endpoint for tokens and use whatever the identity may do. What limits the damage is least privilege (the RBAC table above, one identity per role) and short-lived tokens (nothing worth stealing at rest). It is not a boundary against code execution in the container.

**Client:** `EXPO_PUBLIC_API_URL` already overrides the inferred address; in Azure it is the Container App's `https://…azurecontainerapps.io` (TLS terminated by Azure; uvicorn runs with `--proxy-headers`). Only `frontend/.env.example` changed.

**Step 7 notes:** set the startup probe to `/health`, liveness to `/health` (api) and `:8001/health` (queue worker, a second probe port), readiness to `/ready`; set `APP_ENV=production`, `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_CLIENT_ID`, `DATABASE_AUTH=entra`.

### Dual-purpose embeddings

The vectors generated when a book is ingested serve both RAG (Module 5) and recommendations (Module 6). We don't build two embedding pipelines.

## AI models

| Role | Provider | Model | Notes |
|---|---|---|---|
| Vision (primary, OCR is the fallback) | Groq (default) | `qwen/qwen3.8-27b` | multimodal, JSON mode, preview model — see retry notes in the pivot decision above |
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
| ORM | `sqlalchemy` 2.0 async + `asyncpg` (PostgreSQL) | schema changes via `alembic` migrations |
| Auth | `python-jose[cryptography]` (JWT) + `bcrypt` directly | **not** `passlib` — 1.7.4 breaks with `bcrypt>=4.1`. Clean alternative: `pwdlib[argon2]` |
| OCR | `rapidocr-onnxruntime` | ONNX, no torch |
| Image processing | `pillow` | EXIF rotation, resize, recompression |
| Vision / LLM (default) | `groq` (Python client) | cloud — `GROQ_API_KEY` in `.env` |
| Vision / LLM (local fallback) | `ollama` (Python client) | local, `ai_provider="ollama"` |
| Vector DB | `pgvector` (extension in our PostgreSQL; `pgvector.sqlalchemy.Vector(768)`) | `book_chunks` + `book_profiles` tables, exact cosine search, no ANN index |
| Embeddings | `nomic-embed-text` via Ollama | 768-dim, always local |
| HTTP client | `httpx` (async) | retry + backoff |
| Fuzzy matching | `rapidfuzz` | title/author normalization |
| Azure identity | `azure-identity` | `ManagedIdentityCredential` for Storage and the Postgres Entra token (step 5) |
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
│   │   ├── identity.py             # managed-identity credential factory + Postgres token provider
│   │   └── logging.py              # structlog setup
│   ├── api/
│   │   ├── deps.py                 # dependencies (current_user, db session)
│   │   └── routes/
│   │       ├── auth.py             # register, login, refresh, logout
│   │       ├── books.py            # /analyze-cover, book details
│   │       ├── jobs.py             # GET /jobs/{id}
│   │       └── users.py            # profile, history, preferences, recommendations
│   ├── models/                     # SQLAlchemy: User, RefreshToken, Job, Book,
│   │                               #   TextSource, LibraryEntry, JournalEntry,
│   │                               #   RecommendationState, BookChunk + BookProfileRow (vector.py)
│   ├── schemas/                    # Pydantic (request/response)
│   ├── services/
│   │   ├── ocr_service.py          # RapidOCR + image preprocessing
│   │   ├── vision_service.py       # OCR-first, vision-model fallback (Groq or Ollama)
│   │   ├── ollama_client.py        # local backend: shared wrapper with timeout/retry
│   │   ├── groq_client.py          # cloud backend (default): shared wrapper, retry, provider switch
│   │   ├── http_utils.py           # shared GET+retry policy for every external source
│   │   ├── sources/
│   │   │   ├── base.py             # ContentSource + CandidateSource Protocols, SourceResult
│   │   │   ├── google_books.py     # metadata: description, categories, ISBN, rating
│   │   │   ├── open_library.py     # CC0 gap-filler: subjects, description, cover by ISBN
│   │   │   ├── wikipedia.py        # plot + Reception — the critical-opinion corpus
│   │   │   └── cover_fallback.py   # cover chain: OL by ISBN → Wikipedia lead image
│   │   ├── data_fetcher.py         # source orchestration + normalization + cache
│   │   ├── chunking.py             # passages → ~500-token, sentence-aligned chunks
│   │   ├── embeddings.py           # nomic-embed-text via Ollama — always local
│   │   ├── vector_store.py         # `book_chunks` (pgvector) + the mandatory book_id filter
│   │   ├── profile_store.py        # `book_profiles` — one vector per book, queried across books
│   │   ├── rag_service.py          # ingest, retrieve, synthesize, verify citations
│   │   ├── library_service.py      # history, status, ratings, derived preferences
│   │   ├── journal_service.py      # dated reading notes, owned via the library entry
│   │   └── recommendation_service.py  # profile vector, catalog discovery, ranking, explanations
│   ├── workers/
│   │   ├── cover_pipeline.py       # the full job: OCR → fetch → ingest → summary; photo lifecycle
│   │   └── queue_worker.py         # the queue worker: queue -> pipeline, idempotent, poison handling
│   └── db/
│       ├── session.py
│       └── init_db.py              # startup check: DB at Alembic head, else refuse to start
├── alembic.ini                     # URL comes from Settings, never from here
├── migrations/                     # env.py (async), versions/0001_baseline.py, 0002_pgvector.py, ...
├── tests/
│   ├── conftest.py
│   └── fixtures/                   # test covers, mocked HTTP responses
├── docs/
│   └── module-N-name.md            # summary written at the end of each module
├── .env.example
├── Dockerfile                      # the `api` + queue worker image (multi-stage, non-root, `test` stage)
├── requirements.lock               # exact runtime versions the image installs (regenerate: see its header)
├── .dockerignore                   # keeps .env, .venv, docs/ out of the build context
├── pyproject.toml
└── CLAUDE.md
```

Repo root, next to `backend/` and `frontend/`:

```
docker-compose.yml                  # runs `postgres`, one-shot `migrate`, `api`, `ollama` (+ `test`, profile `test`), like the future cloud layout
postgres/
└── init/01-create-test-db.sql      # creates `glance_test` for host-side pytest
ollama/
└── Dockerfile                      # CPU-only ollama image, nomic-embed-text:v1.5 baked in
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
- [x] **Module 5: RAG** — *(backend + client, both done)* chunking (~500 tokens, overlap 50, sentence-aligned), local embeddings via `nomic-embed-text`, vectors in PostgreSQL (pgvector since cloud step 3; Chroma before), **retrieval mandatorily filtered on `book_id`** (see the decision below), synthesis with `openai/gpt-oss-120b` on Groq + per-claim source citations, anti-hallucination prompt *and* citation verification. `GET /books/{book_id}/summary`. On the client: the summary section fetches itself, each sentence is tappable to the passage it came from, and the publisher's blurb stays as a clearly-labelled fallback.
      *Done when:* on a fixture corpus, every statement in the summary is traceable to a chunk. **Done** — 221 tests green, `mypy app/` clean, `ruff`/`black` clean, `tsc --noEmit` clean, `expo export` bundles. See `backend/docs/module-5-rag.md` (local, gitignored).
      **The summary is not part of the scan job.** It hangs off the *book*, not the scan, and is generated on demand by its own endpoint. Three reasons: it is seconds slower than the catalog metadata and would otherwise hold the whole result screen back; it is shared by everyone who scans the same book; and a correction needs no special handling for it, because the corrected title resolves to a different `book_id` and the client simply asks for that book's summary. `job.result` therefore has **no `summary` key** at all — a field that is always `null` is a trap.
      **The summary *is* its claims.** The model returns a list of `{text, chunk_ids}` and the prose is derived server-side by joining them, rather than returning prose with `[1]` markers the client would have to parse back apart. The two cannot disagree, and "every sentence cites something" becomes checkable instead of hoped-for.
      **The prompt is not the enforcement.** Asking a model not to hallucinate is a request; the failures it doesn't catch are exactly the fluent, plausible ones. So every returned claim is verified against the chunks actually retrieved, and one citing nothing — or citing an id that wasn't in the context — is **dropped before the client sees it**. If nothing survives, the response is `available=false` and the client falls back to the publisher's blurb. What this deliberately does *not* claim is that a claim's text is faithful to the chunk it cites: that is a semantic judgement needing a second model whose errors we could not check either. The honest boundary is traceability, and the client shows the passage so the reader can judge.
      **An unavailable summary is a `200`, not an error.** `available=false` means "nothing to summarize"; a 503 means "the provider was unreachable, try again". The client renders those differently, and collapsing them would either hide a real outage or make an honest gap look broken. A failed generation is also never cached — otherwise a bad moment would deny the book a summary until its sources next expire, up to 30 days.
- [x] **Module 6a: Personal library + reading journal** — *(backend + client, both done)* `LibraryEntry` (one row per user×book: status, rating, scan bookkeeping) auto-recorded by the cover pipeline, plus `JournalEntry` (dated notes hanging off it). `GET/PUT/DELETE /books/{id}/library`, `GET/POST/PATCH/DELETE /books/{id}/journal`, `GET /users/me/library|stats|preferences`. On the client: real profile counters, derived preferences, a tappable history, a status picker on the scan result, and a book screen with rating and journal.
      *Done when:* a rescan doesn't duplicate the history and one reader's library and journal are invisible to another. **Done** — 300 tests green, `mypy app/` clean, `ruff`/`black` clean, `tsc --noEmit` clean, `expo export` bundles. See `backend/docs/module-6a-library.md` (local, gitignored).
      **This had to come before recommendations, because none of their inputs existed.** Module 6 was specified as "profile vector weighted by user rating" — and before this module there was no link at all between a `User` and a `Book`. A `Job` has a `user_id`, but a job is the technical record of one scan: it duplicates on every rescan, holds the vision reading rather than an opinion, and is finished being useful once the client reads it. There were no ratings to weight by.
      **`(user_id, book_id)` is unique, and that is the whole point.** Rescanning a cover is the normal case, not an edge one. Without the constraint the same book appears three times in the history, the counters read three, and the profile vector weighs it three times over.
      **The reading journal is rows, not a column — and that was a correction made on use.** 6a first shipped a single `review` field, asked for on the scan result screen. Wrong twice: it was requested *seconds after the cover was photographed*, the one moment a reader has nothing to say, and it could hold only one opinion, overwritten each time. Notes are now dated `journal_entries` shown **oldest first** — reversing them would put a conclusion above the doubt that produced it, which is the one thing a journal is for. An edit moves `updated_at` only; moving `created_at` would silently reorder the timeline on every typo fix.
      **Capture and reflection are different screens.** The scan result carries only the reading status — the one thing anyone can honestly answer right after a photo. Rating and journal live on the book screen, reached from the profile.
      **Nothing the reader writes enters the RAG corpus.** Module 5 guarantees every claim is traceable to a cited official source; if a journal note became a citable chunk, a summary could quote the reader back at themselves formatted as criticism — fluent, correctly cited, and false.
      **Journal ownership resolves through the library entry, never by note id.** A note id is a small integer and trivially guessable, so `_owned_note` filters on `library_entry_id` as well as `id` — another reader's note simply does not match, rather than being fetched and then judged, which is the shape that keeps working until someone leaves the judgement out.
      **Absent ≠ null in a partial update.** `LibraryEntryUpdate` reads `model_fields_set`, so setting a status does not erase the rating while `{"rating": null}` still clears it. This is what lets the client fire one small `PUT` per control instead of shipping several fields together — the one request shape that can clobber.
      **Preferences are derived from books rated 4+, never declared.** A declared list is friction at signup and stale afterwards, and a profile header contradicting the history under it is exactly the inconsistency this module removes. Nothing rated yet yields empty lists and `based_on=0` — the honest answer, with the client asking for a rating rather than inventing tastes. Ties break alphabetically, or the chips reshuffle between two identical requests and read as a bug.
      **A correction removes the misidentified book from the library** — but only when it is an untouched scan artifact (no rating, no journal note, still `SCANNED`). Once the user has said something about it, it is theirs and it stays.
- [x] **Module 6b: Recommendations** — *(backend + client, both done)* profile vector (rating-weighted average over entries rated ≥4), candidate discovery from the catalogs, filtering by the whole library, score + computed explanation ("because you liked X"). Purely content-based — single user, guaranteed cold start, no collaborative filtering. `GET /users/me/recommendations`. On the client: the recommendations tab runs on real data, with "Want to read" writing straight into the library.
      *Done when:* a reader who has rated nothing gets an honest empty state, and no book already in their library is ever suggested. **Done** — 325 tests green, `mypy app/` clean, `ruff`/`black` clean, `tsc --noEmit` clean, `expo export` bundles. See `backend/docs/module-6b-recommendations.md` (local, gitignored).
      **Candidates cannot come from the chunk table alone.** `book_chunks` holds only books the user has already scanned — that pool is exactly the set that must be filtered out, so the honest answer would always be zero recommendations. Cold start here is not "no ratings", it is "nothing to choose from". Candidates come from the catalog instead: Google Books / Open Library queried by the derived genres and authors, descriptions embedded locally with `nomic-embed-text`, ranked against the profile vector.
      **Recommendations get their own table, `book_profiles` (one vector per book; a Chroma collection until cloud step 3).** They need a query *across* books, which is precisely what `vector_store.py`'s mandatory `book_id` filter forbids — and that filter is Module 5's central correctness invariant, not a preference. It is not weakened, flagged around, or given an exception: two tables, two rules, neither relaxed. `services/profile_store.py` is a separate module for the same reason — a cross-book query function sitting next to the mandatory-filter code would be an invitation.
      **Discovery is a different question from lookup, so it is a different Protocol.** `ContentSource.fetch` resolves one known title and merges everything about it; `CandidateSource.discover` asks an open question and expects many partial answers. Both catalogs implement both, but folding them together would give `fetch` a plural return type it has no use for, and would let a discovery result reach the Module 4 merge — where "first non-`None` wins" assumes every result describes the *same book*.
      **A discovered candidate is persisted as an ordinary `Book` row with `sources_fetched_at = None`.** That gives it a stable id the client can act on, lets its vector be cached instead of recomputed per request, and makes "Want to read" the existing library write rather than a second path. The null timestamp is what keeps it honest: `_is_fresh` reads exactly that field, so scanning the book later runs the real Module 4 fetch instead of serving a search hit as a settled lookup. An existing row is **never** overwritten by a candidate — it may carry gathered passages, a summary and a recovered cover that a bare search hit would flatten.
      **The candidate pool is global; only the filtering is per user.** Two readers who like the same genre rank against the same rows. What is private is the profile vector, the exclusions and the ranking, all computed per request. The only per-user state is `RecommendationState` — `refreshed_at` plus a fingerprint of the derived preferences, because a TTL alone would serve yesterday's pool for a day after the reader first rates a book in a new genre, which is the exact moment their suggestions should change most.
      **Low-rated books feed an exclusion set on author/genre**, never subtracted from the profile vector — negative vector arithmetic produces a direction corresponding to nothing the reader said, and therefore a suggestion that cannot be explained. And a like always beats a dislike: an author on both lists stays, because "I loved one and disliked another" is a fact about those books, not about the author. That override is only expressible because the signal is a set.
      **3 is neither.** `FAVORITE_RATING_FLOOR = 4`, `DISLIKED_RATING_CEILING = 2`. If a 3 counted as a dislike, finishing one indifferent science-fiction novel would delete every science-fiction recommendation the reader could ever get.
      **The explanation is computed from the nearest contributing book, not generated by a model.** A model asked to justify a recommendation writes something fluent about a book it was never shown — Module 5's failure mode, with no citation to check it against. A wrong reason beside a right recommendation is worse than no reason, because the reason is the part the reader trusts.
      **Generic catalog labels are never used as discovery queries.** Google Books tags most novels `Fiction` and most children's books `Juvenile Fiction` — top-level BISAC headings that name the shelf, not the book. `subject:"Fiction"` returns an arbitrary slice of everything ever published, which then ranks badly and crowds out the granular subjects Open Library contributed.
      **A book the catalogs describe with nothing but its own title gets no profile vector at all.** Embedding "Baltagul by Mihail Sadoveanu" encodes little more than the shapes of two proper nouns and would then rank arbitrarily against real candidates. Absent is more honest than arbitrary — and this is why `RecommendationList.based_on` counts the books that actually *fed* the vector, which is deliberately a different number from `LibraryPreferences.based_on`.
      **`based_on` is what makes the two empty results different screens.** `based_on = 0` means there is nothing to build a profile from and the client asks for a rating; `based_on > 0` with an empty list means the catalogs simply had nothing past the reader's own library. Collapsing them into a bare empty array would make the app ask a reader who has rated forty books to please rate a book.
      **Discovery paces its queries; fanning them out is what made Google Books 503.** Measured on the real API after the first run failed in production: fired concurrently, five discovery queries drew a 503 on **16 of 20** requests; paced one second apart, the identical queries returned 200 on **5 of 5**. Google sheds load on *burst rate per key* — the request shape (`printType`, `orderBy`, `maxResults`) was measured to make no difference whatsoever, so the obvious "it must be the new parameters" reading was wrong. Nothing before this module provoked it: the scan path queries three different hosts at once but asks each exactly one question, while discovery was the first code here to ask one host five questions at once. Sources now run in parallel with each other and **sequentially within themselves** (`recommendation_query_spacing_seconds`).
      **A source is dropped for the rest of the run at its first unavailable answer.** By then the HTTP layer has already retried, so it means that catalog is down *now*, and each remaining seed would spend its full timeout rediscovering that. openlibrary.org has extended outages during which every request hangs to the timeout — without this, one of them holds a synchronous request for minutes. Discovery also retries less than a scan does (`recommendation_discovery_retries` below `catalog_max_retries`): a scan waits on one lookup and is worth retrying hard, discovery has five and gives up early.
      **A degraded run gets a short TTL, not the full day.** The first version set `refreshed_at` whenever *anything* answered, so a run during which a catalog died was recorded as complete and its half-built pool pinned for 24 hours — one bad minute costing the reader a day of thin recommendations, with neither rescanning nor re-rating able to shake it loose. `RecommendationState.complete` now selects between `recommendation_candidate_ttl_hours` and `recommendation_degraded_ttl_hours`. Same shape as Module 4's short TTL for an empty book, and for the same reason: a degraded answer is not a settled fact.
      **The Google Books key travels in an `X-Goog-Api-Key` header, never in the query string.** `httpx` logs the full request URL at INFO, so the `?key=` form wrote the key verbatim into the application log on every catalog call — which is how it left the machine and ended up pasted into a chat window. Verified equivalent: same responses, same quota. An absent key sends no header at all rather than an empty one, since a malformed credential and an anonymous request fail differently.
      **Found and fixed while wiring this up: `LibraryBook.categories` rejected the `NULL` the column actually stores.** `Book.categories` is nullable and `BookDataFetcher` writes `None`, not `[]`, when no catalog supplied genres — the normal outcome for editions outside the English-language mainstream. Validating such a book raised, so *one* bare record answered the whole of `GET /users/me/library` with a 500. Shipped in 6a with the suite fully green, because every fixture happened to have categories. Now a `mode="before"` validator on the schema, where the nullable column meets the non-nullable contract.
- [x] **Module 7: Client** — **started early, intentionally**, and finished alongside 6b. The mobile client (Expo + React Native + TypeScript) lives in `frontend/`, with its own `frontend/CLAUDE.md`. Testing on a **physical phone via Expo Go** (the Android emulator was rejected: ~1.5 GB RAM on a laptop with 7.4 GB that's also running Ollama). The `/dev` test HTML page is no longer needed — the real app replaces it.
      **The frontend is at parity with every backend module, 0 through 6b** — it caught up in the session Module 4 landed and never fell behind again, so neither side is ahead. See `frontend/docs/module-0-2-paritate-backend.md` (local, gitignored).

**The module list ends here — deliberately, not for lack of ideas.** The features the app was specified to have all exist and work on the phone. What `frontend/CLAUDE.md` used to track as a "Module 7: Polish" backlog (animations, offline handling, dark theme) is closed unbuilt: see the note in that file for what was actually left and what was already done. Reopening any of it is a new decision, not the resumption of a plan.

### Work pace: backend and frontend in parallel

The two parts progress **module by module, in parallel** — not backend-complete-first. After each backend module comes the corresponding frontend part, so every capability can be seen and tested as soon as it exists. Keep the "Modules — status" section in sync across both `CLAUDE.md` files.

## Useful commands

```bash
docker compose up -d postgres      # (repo root) the database; REQUIRED before uvicorn, alembic and pytest
uvicorn app.main:app --reload      # start the dev server (after `alembic upgrade head`)
curl localhost:8000/ready          # readiness (200 only while Postgres answers); /health is liveness only
pytest                             # run the tests (Postgres must be up; fails fast otherwise)
pytest -m "not slow"               # skip tests that call Ollama
black . && ruff check .            # formatting + lint
mypy app/                          # type checking
alembic upgrade head               # apply migrations (native dev; compose does it via `migrate`)
alembic revision --autogenerate -m "..."   # new migration after a model change; review it
                                   # tests use the `glance_test` database (TEST_DATABASE_URL overrides);
                                   # it is created only on a fresh volume, else `createdb` it;
                                   # 127.0.0.1, not localhost: on Windows localhost tries ::1 first, +2 s per connection
docker compose up -d postgres azurite   # (repo root) required before `pytest`
docker compose up --build          # (repo root) run postgres, azurite, migrate, api, worker (queue worker), ollama; needs backend/.env
docker compose --profile test run --rm test
                                   # (repo root) the suite inside Linux, on the locked versions
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

The project is **hybrid**: storage and embeddings stay ours; vision and LLM generation default to Groq Cloud. This is a deliberate pivot from the original all-local design — see "Architecture pivot: local vision/LLM → Groq Cloud" above for the reasoning (local model accuracy on this hardware was not good enough, and CPU inference was too slow).

- **Storage, cache, vectors and embeddings stay under our control** — one Postgres we run (compose locally / Flexible Server in Azure) with the pgvector extension holding both the relational data and the vectors, scan photos and the job queue in Blob/Queue Storage (Azurite locally / an Azure Storage account in the cloud), and `nomic-embed-text` via our own Ollama container. No third-party data services, regardless of `ai_provider`.
- **Vision and LLM generation go through Groq Cloud by default** (`Settings.ai_provider = "groq"`), using `groq.AsyncGroq` with the key in `GROQ_API_KEY`. The local path (`ai_provider = "ollama"`, Moondream + Llama 3.2) must keep working as a fallback — don't let it bit-rot or get deleted.
- **Never hardcode the provider.** Both integrations go through `Settings.ai_provider` / `Settings.vision_model` / `Settings.llm_model` in `core/config.py`, never a direct `if` on a model name scattered through the code.
- No other cloud LLM/vision APIs (OpenAI, Anthropic API, etc.) and no Google Colab — Groq is the one deliberate exception, not a general opening.
- Groq calls must handle rate limits and API errors gracefully (retry with backoff, then raise `AIProviderUnavailable`) — never let a preview-model hiccup propagate as an unhandled exception. See `groq_client.py`.
- No storage key or database password in a deployed container: managed identity only (`AZURE_STORAGE_ACCOUNT_NAME`, `DATABASE_AUTH=entra`); with `APP_ENV=production` any development value makes startup fail (see "Security & identity (cloud step 5)").
- No scraping. Only the official sources listed in the table above.
- No PyTorch in dependencies.
- `.env` never in git — only `.env.example` with keys and no real values.
- One module per session, tested and confirmed, then the next.