# CLAUDE.md — Glance

Acest fișier se încarcă automat de Claude Code la fiecare sesiune. Nu-l repeta în prompturi — actualizează-l pe măsură ce proiectul avansează.

## Prezentare generală

**Glance** — aplicație mobilă + backend 100% local. Fotografiezi coperta unei cărți, iar aplicația recunoaște titlul și autorul, adună material despre carte din surse deschise, generează un rezumat prin RAG și oferă recomandări personalizate pe baza istoricului de lectură.

**Regulă strictă: niciun apel către API-uri cloud de tip LLM (OpenAI, Anthropic API etc.) și fără Google Colab. Toate modelele AI rulează local prin Ollama.**

## Constrângeri hardware (decisive pentru design)

Laptop de dezvoltare: **7.4 GB RAM total**, CPU-only, Windows 11, Python 3.11.9, Ollama 0.32.6.

Consecințe care nu sunt negociabile:

1. **Niciodată două modele Ollama încărcate simultan.** Se setează `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=5m`. Reîncărcarea costă 2-4 s, swap-ul ar costa minute.
2. **Fluxul complet durează 30-120 s.** Deci endpoint-urile grele sunt **asincrone**: returnează `202 Accepted` + `job_id`, clientul face poll pe `GET /jobs/{id}`. Fără Celery/Redis — doar `BackgroundTasks` + un tabel `jobs` în SQLite.
3. **Evităm PyTorch.** Instalare de ~2.5 GB pe disc și sute de MB în RAM pentru ceva ce Ollama face deja. Embeddings prin `nomic-embed-text`, OCR prin ONNX Runtime.

## Arhitectură

```
Client (mobile app)
      │  POST /books/analyze-cover (imagine + JWT)  →  202 { job_id }
      │  GET  /jobs/{job_id}                        →  poll până la status=done
      ▼
FastAPI backend (local, pe laptop)
      │
      ├── RapidOCR (ONNX)          → text brut de pe copertă        [rapid, <1 s]
      ├── Moondream (via Ollama)   → fallback când OCR e nesigur    [lent, 15-40 s]
      ├── Google Books / Open Library / Wikipedia → metadate + text despre carte
      ├── SQLite                   → users, jobs, books, sources, reading_history, preferences
      ├── ChromaDB                 → embeddings (nomic-embed-text), servesc RAG *și* recomandări
      └── Llama 3.2 (via Ollama)   → rezumat generat din contextul regăsit
```

## Decizii luate (nu le rediscuta fără motiv nou)

### Vision: OCR-first, Moondream ca fallback

`RapidOCR` (ONNX Runtime, ~10 MB, fără torch) citește textul de pe copertă în sub o secundă. Candidații de text se trimit la Google Books și se aleg prin fuzzy match (`rapidfuzz`). Moondream se invocă **doar** când OCR-ul întoarce prea puțin text sau matching-ul are scor slab — tipic la coperți ilustrate, fără text clar.

Motiv: pe text tipărit, OCR-ul e mai precis decât un VLM de 1.8B la nume proprii, și de ~30× mai rapid. Moondream rămâne în proiect pentru cazurile unde chiar e nevoie de înțelegere vizuală.

### Surse de conținut: doar oficiale, fără scraping

**Google Books API nu returnează text de recenzii** — doar `description`, `categories`, `averageRating`, `ratingsCount`. Fără corpus, RAG-ul n-are ce regăsi. Sursele reale:

| Sursă | Ce oferă | Licență / acces |
|---|---|---|
| Google Books API | descriere, categorii, ISBN, rating mediu | gratis, fără cheie pentru volume publice |
| Open Library | subjects, descrieri, ediții, ratings | CC0 |
| Wikipedia REST API | rezumat intrigă + secțiunea *Reception / Critical reception* — **principala sursă de opinie critică** | CC BY-SA |

Toate implementează un `Protocol` comun `SursaContinut`. Există și `ScraperSource` **definit dar neimplementat**, ca punct de extensie. Nu se scrie scraping fără cerere explicită.

### Embeddings cu rol dublu

Vectorii generați la ingestia unei cărți servesc și RAG-ul (Modulul 5) și recomandările (Modulul 6). Nu construim două pipeline-uri de embedding.

## Modele Ollama

| Rol | Model | RAM | Fallback dacă e prea lent |
|---|---|---|---|
| Vision (fallback) | `moondream` | ~1.7 GB | — |
| LLM rezumat | `llama3.2` (3B) | ~2.0 GB | `llama3.2:1b`, apoi `qwen3:4b` |
| Embeddings | `nomic-embed-text` | ~275 MB | rămâne încărcat permanent (keep-alive lung) |

`llama3:latest` (4.7 GB) e prea mare pentru acest laptop — de șters, e dublat funcțional de `llama3.2`.
`qwen3:4b` e rezervă: output structurat mai bun, dar „thinking mode" costă timp pe CPU. De comparat la Modulul 5.

## Stack tehnologic

| Nevoie | Librărie | Observații |
|---|---|---|
| Framework API | `fastapi` + `uvicorn` | async by default |
| Validare / schemas | `pydantic` v2 | toate request/response bodies ca `BaseModel` |
| Config & secrete | `pydantic-settings` + `python-dotenv` | citește din `.env`, niciodată hardcodat |
| ORM | `sqlalchemy` 2.0 async + `aiosqlite` | `alembic` doar când schema începe să evolueze |
| Auth | `python-jose[cryptography]` (JWT) + `bcrypt` direct | **nu** `passlib` — 1.7.4 crapă cu `bcrypt>=4.1`. Alternativă curată: `pwdlib[argon2]` |
| OCR | `rapidocr-onnxruntime` | ONNX, fără torch |
| Procesare imagini | `pillow` | rotire EXIF, resize, recompresie |
| Vision / LLM | `ollama` (client Python) | local |
| Vector DB | `chromadb` (persistent client) | folder `./data/chroma` |
| Embeddings | `nomic-embed-text` via Ollama | 768-dim |
| Client HTTP | `httpx` (async) | retry + backoff |
| Fuzzy matching | `rapidfuzz` | normalizare titlu/autor |
| Testare | `pytest` + `pytest-asyncio` + `httpx.AsyncClient` + `respx` | zero apeluri de rețea reale în suită |
| Formatare / linting | `black` + `ruff` | înainte de fiecare commit |
| Type checking | `mypy` (strict) | fără `Any` nejustificat |
| Logging | `structlog` | niciodată `print()` în cod de producție |

## Structură de directoare (backend)

```
backend/
├── app/
│   ├── main.py                     # entrypoint FastAPI
│   ├── core/
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   ├── security.py             # JWT, hashing parole
│   │   ├── exceptions.py           # excepții custom + handlers
│   │   └── logging.py              # setup structlog
│   ├── api/
│   │   ├── deps.py                 # dependencies (utilizator_curent, db session)
│   │   └── routes/
│   │       ├── auth.py             # register, login, refresh, logout
│   │       ├── books.py            # /analyze-cover, detalii carte
│   │       ├── jobs.py             # GET /jobs/{id}
│   │       └── users.py            # profil, istoric, preferințe, recomandări
│   ├── models/                     # SQLAlchemy: User, RefreshToken, Job, Book,
│   │                               #   SursaText, ReadingHistory, Preference
│   ├── schemas/                    # Pydantic (request/response)
│   ├── services/
│   │   ├── ocr_service.py          # RapidOCR + preprocesare imagine
│   │   ├── vision_service.py       # Moondream fallback
│   │   ├── ollama_client.py        # wrapper cu timeout/retry, shared
│   │   ├── sources/
│   │   │   ├── base.py             # Protocol SursaContinut
│   │   │   ├── google_books.py
│   │   │   ├── open_library.py
│   │   │   └── wikipedia.py
│   │   ├── data_fetcher.py         # orchestrare surse + normalizare + cache
│   │   ├── rag_service.py          # chunking, embeddings, Chroma, sinteză
│   │   └── recommendation_service.py
│   ├── workers/
│   │   └── cover_pipeline.py       # job-ul complet: OCR → fetch → ingest → rezumat
│   └── db/
│       ├── session.py
│       └── init_db.py
├── tests/
│   ├── conftest.py
│   └── fixtures/                   # coperți de test, răspunsuri HTTP mock
├── docs/
│   └── module-N-nume.md            # rezumat scris la finalul fiecărui modul
├── data/                           # SQLite + Chroma (în .gitignore)
├── .env.example
├── pyproject.toml
└── CLAUDE.md
```

## Module — status

Un modul pe sesiune. Nu se trece mai departe până testele nu trec.

- [x] **Modulul 0: Fundație** — `pyproject.toml`, venv, `config.py`, `exceptions.py`, `logging.py`, `main.py` cu `/health`, `conftest.py`, `.env.example`, `.gitignore`, `git init`.
      *Gata când:* `pytest` verde pe testul de health, `mypy app/` curat, `ruff` curat.
- [x] **Modulul 1: Auth** — modele `User` + `RefreshToken`, `POST /auth/register|login|refresh|logout`, `GET /users/me`. Refresh token opac (SHA-256 în DB, nu JWT), rotation la fiecare refresh.
      *Gata când:* teste pentru register duplicat, login greșit, token expirat, refresh rotation. Vezi `backend/docs/module-1-auth.md`.
- [x] **Modulul 2: Schelet API + job-uri** — routere, `deps.py`, exception handlers globale, tabel `jobs`, `GET /jobs/{id}`, upload validat (max 8 MB, JPEG/PNG/HEIC).
      *Gata când:* un job fake parcurge `pending → running → done` și e vizibil doar proprietarului. Vezi `backend/docs/module-2-schelet-api.md`.
- [ ] **Modulul 3: Vision** — preprocesare Pillow (rotire EXIF, resize 768 px, JPEG q85), RapidOCR, `OllamaClient`, fallback Moondream cu output `{titlu, autor, incredere}`, endpoint de corecție manuală.
      *Gata când:* teste cu client Ollama fake + un test `@pytest.mark.slow` pe 3 coperți reale din `tests/fixtures/`.
- [ ] **Modulul 4: Data fetcher & cache** — cele trei surse oficiale, normalizare titlu+autor cu `rapidfuzz`, modele `Book` + `SursaText`, cache cu TTL.
      *Gata când:* tot HTTP-ul mock-uit cu `respx`, zero apeluri de rețea în suită.
- [ ] **Modulul 5: RAG** — chunking (~500 tokens, overlap 50), embeddings, Chroma persistent, **retrieval filtrat obligatoriu pe `book_id`**, sinteză cu Llama 3.2 + citări la sursă, prompt anti-halucinație.
      *Gata când:* pe un corpus fixture, fiecare afirmație din rezumat e trasabilă la un chunk.
- [ ] **Modulul 6: Recomandări** — `ReadingHistory`, `Preference`, vector de profil (medie ponderată cu ratingul userului), candidate generation din Chroma, filtrare pe genuri și cărți deja citite, scor + explicație („pentru că ți-a plăcut X"). Content-based pur — un singur user, cold start garantat, fără collaborative filtering.
- [ ] **Modulul 7: Client** — întâi o pagină HTML de test în `/dev` pentru validarea fluxului, apoi mobil real (Expo/React Native). Userul vrea să poată testa interfața grafică vizual imediat ce există (idealul: emulator/simulator de telefon) — de ales concret unealta (Expo Go pe telefon fizic, Android Studio emulator, sau altceva) când se ajunge la acest modul, și de actualizat această secțiune atunci.

## Comenzi utile

```bash
uvicorn app.main:app --reload      # pornește serverul dev
pytest                             # rulează testele
pytest -m "not slow"               # sare peste testele care cheamă Ollama
black . && ruff check .            # formatare + lint
mypy app/                          # type checking
```

## Convenții de cod

- Type hints obligatorii pe toate funcțiile și metodele publice.
- Docstrings (stil Google) pe fiecare funcție, clasă și modul public — ce face, parametri, ce returnează, ce excepții poate arunca.
- Error handling explicit: excepții custom în `core/exceptions.py`, niciodată `except Exception: pass`.
- Fiecare serviciu extern (Ollama, HTTP, OCR) stă în spatele unui `Protocol`, ca să poată fi înlocuit cu un fake în teste.
- Denumire variabile: românește acolo unde e mai clar din domeniu (`carte`, `recenzii`, `utilizator_curent`), engleză pentru termeni tehnici standard (`request`, `response`, `session`).
- Fiecare modul nou vine cu teste `pytest` înainte de a fi considerat „gata".
- La finalul fiecărui modul se scrie un fișier de rezumat în `backend/docs/module-N-nume.md`: ce s-a implementat, deciziile tehnice luate, ce fișiere au apărut, cum se verifică (comenzi de test). Vezi `backend/docs/module-0-fundatie.md` ca exemplu.

## Reguli stricte

- Fără cloud LLM / cloud vision APIs. Tot ce ține de AI rulează local prin Ollama.
- Fără scraping. Doar sursele oficiale din tabelul de mai sus.
- Fără PyTorch în dependențe.
- `.env` niciodată în git — doar `.env.example` cu chei fără valori reale.
- Un singur modul per sesiune, testat și confirmat, apoi următorul.


