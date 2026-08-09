# Modulul 2: Schelet API + job-uri

## Ce trebuia făcut

Scheletul asincron pe care se vor sprijini toate modulele grele (vision, data fetcher, RAG): un tabel `jobs`, `POST /books/analyze-cover` care validează upload-ul și pornește un job în fundal, `GET /jobs/{id}` pentru polling, izolat pe proprietar. Fără el, Modulul 3 (vision) n-ar avea unde să scrie rezultatul unei analize care durează 30-120 s.

## Ce s-a implementat

### Model de date

- **`app/models/job.py` — `Job`**: `id`, `user_id` (FK, indexat), `status` (`pending`/`running`/`done`/`failed`, din enum-ul `JobStatus`), `result` (JSON, nullable), `error` (text, nullable), `created_at`, `updated_at` (auto pe `onupdate`).

### Rute

| Rută | Ce face |
|---|---|
| `POST /books/analyze-cover` | Primește imaginea (multipart), validează tip MIME + dimensiune, creează un `Job` (`pending`), pornește task-ul de fundal. Răspunde 202 cu `{job_id}`. |
| `GET /jobs/{id}` | Returnează starea + rezultatul/eroarea job-ului. 404 dacă nu există, 403 dacă nu-i aparține utilizatorului curent. |

Upload-ul e validat pe două criterii, fiecare cu excepția lui:
- **tip MIME** — doar `image/jpeg`, `image/png`, `image/heic`, `image/heif` → altfel `TipFisierNesuportat` (415)
- **dimensiune** — peste `settings.max_upload_size_bytes` (8 MB) → `FisierPreaMare` (413)

Ambele excepții noi în `core/exceptions.py`, adăugate (spre deosebire de decizia din Modulul 1 de a nu crea excepții noi) pentru că 413 și 415 sunt coduri HTTP distincte, corecte semantic, pe care `DateInvalide` (422) nu le-ar fi putut reprezenta corect.

### Worker de fundal: `app/workers/cover_pipeline.py`

`proceseaza_coperta(job_id, continut_imagine, session_factory)` — parcurge `pending → running → done`, cu un rezultat placeholder (`{"mesaj": ..., "dimensiune_imagine_bytes": ...}`). Nu conține încă try/except pentru eșec, pentru că nu există încă niciun mod real în care poate eșua — Modulele 3-5 vor înlocui corpul cu OCR, fetch de date și sinteză reale, moment în care apare și tranziția spre `failed`.

**Semnătura ia `session_factory` ca parametru**, nu importă direct `AsyncSessionLocal`. Motiv: `BackgroundTasks` rulează după ce sesiunea request-ului s-a închis, deci workerul trebuie să-și deschidă propria sesiune — dar dacă ar importa direct fabrica de producție, ar scrie mereu în `data/glance.db` chiar și în teste, ratând complet baza de date SQLite în memorie folosită de fixture-uri. Soluția: `app/db/session.py` expune `get_session_factory()` ca dependency FastAPI (analog cu `get_db()`), suprascriabilă în `conftest.py` la fel ca `get_db`. Ruta `analyze-cover` primește fabrica prin `SessionFactory` (dependency) și o pasează explicit workerului.

### Teste (`tests/test_jobs.py`)

7 teste noi, toate pe baza de date de test în memorie (fixture existentă, extinsă cu override-ul de `get_session_factory`):
- upload valid → job ajunge `done`, cu rezultatul placeholder vizibil
- tip de fișier nesuportat → 415
- fișier peste 8 MB → 413
- `analyze-cover` fără autentificare → 401
- `GET /jobs/{id}` inexistent → 404
- `GET /jobs/{id}` fără autentificare → 401
- `GET /jobs/{id}` al altui utilizator → 403

Pentru că `BackgroundTasks` din Starlette rulează *în interiorul* apelului ASGI (înainte ca `ASGITransport` să returneze răspunsul către `httpx.AsyncClient`), până se termină `await client.post(...)` job-ul e deja `done` — nu a fost nevoie de sleep/retry în teste.

## Decizii tehnice

- **`session_factory` injectat, nu importat direct** — vezi secțiunea worker de mai sus. Cea mai importantă decizie a modulului; fără ea, testele ar fi trecut fals-pozitiv local dar ar fi corupt `data/glance.db`.
- **`FisierPreaMare` (413) și `TipFisierNesuportat` (415) ca excepții noi** — justificat de coduri HTTP pe care ierarhia existentă nu le acoperea, spre deosebire de cazul duplicat-de-email din Modulul 1.
- **Fără try/except în workerul placeholder** — ar fi error handling pentru un scenariu care nu poate apărea încă (nu se face nicio operație care poate eșua). Revine când Modulul 3 adaugă OCR real.
- **`POST /analyze-cover` întoarce doar `{job_id}`**, nu tot obiectul `Job` — se potrivește exact cu contractul din arhitectura din CLAUDE.md (`202 { job_id }`). Detaliile complete (status, result, error) sunt disponibile pe `GET /jobs/{id}`.
- **`JobStatus(enum.StrEnum)`**, nu `class JobStatus(str, enum.Enum)` — echivalent funcțional, dar e forma recomandată de `ruff` (UP042) pe Python 3.11+.

## Fișiere noi / modificate

```
backend/
├── pyproject.toml                          # +python-multipart (necesar pentru UploadFile)
├── app/
│   ├── main.py                             # +routere books, jobs
│   ├── db/
│   │   └── session.py                      # +get_session_factory()
│   ├── api/
│   │   ├── deps.py                         # +SessionFactory
│   │   └── routes/
│   │       ├── books.py                    # POST /analyze-cover
│   │       └── jobs.py                     # GET /jobs/{id}
│   ├── core/
│   │   └── exceptions.py                   # +FisierPreaMare, +TipFisierNesuportat
│   ├── models/
│   │   ├── job.py                          # Job, JobStatus
│   │   └── __init__.py                     # +Job, +JobStatus
│   ├── schemas/
│   │   └── job.py                          # JobCreated, JobPublic
│   └── workers/
│       └── cover_pipeline.py               # proceseaza_coperta (placeholder)
└── tests/
    ├── conftest.py                         # +override get_session_factory
    └── test_jobs.py                        # 7 teste
```

## Cum se verifică

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -v            # 19 passed
./.venv/Scripts/python.exe -m ruff check .          # All checks passed
./.venv/Scripts/python.exe -m black --check .       # curat
./.venv/Scripts/python.exe -m mypy app/             # Success: no issues found
```

## Ce rămâne pentru sesiuni viitoare

- Modulul 3 (vision) înlocuiește corpul lui `proceseaza_coperta` cu preprocesare Pillow + RapidOCR + fallback Moondream, și adaugă tranziția reală spre `failed` (cu try/except justificat de data asta).
- Imaginea încărcată nu e persistată încă pe disk — `analyze-cover` doar o validează și o pasează în memorie workerului. De reevaluat la Modulul 3 dacă trebuie salvată (ex. pentru corecție manuală a titlului/autorului).

## Status

Gata.
