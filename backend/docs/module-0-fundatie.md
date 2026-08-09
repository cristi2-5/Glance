# Modulul 0: Fundație

## Ce trebuia făcut

Scheletul de bază al backend-ului: proiect Python instalabil, configurare centralizată, gestiune de erori, logging structurat și un endpoint minimal (`/health`) care dovedește că serverul pornește și răspunde. Fără el, niciun modul următor (Auth, Vision, RAG etc.) n-are unde să se agațe.

## Ce s-a implementat

### `pyproject.toml`
Proiectul e definit ca pachet instalabil (`pip install -e ".[dev]"`), cu toate dependențele din tabelul de stack tehnologic din CLAUDE.md — **fără PyTorch**. Configurările pentru `pytest`, `black`, `ruff` și `mypy` (strict) stau tot aici, ca sursă unică de adevăr.

### `app/core/config.py`
Clasă `Settings` (`pydantic-settings`) care citește variabile din `backend/.env` (fallback pe valori implicite rezonabile pentru dev). Acoperă: cale bază de date SQLite, folder Chroma, secretul JWT, parametrii Ollama (host, model vision/LLM/embeddings, timeout), limita de upload. Expusă printr-un `get_settings()` cache-uit cu `lru_cache`, ca să nu recitim `.env` la fiecare request.

### `app/core/exceptions.py`
O ierarhie de excepții de domeniu, toate derivate din `GlanceError`, fiecare cu `status_code`-ul HTTP potrivit:
- `ResursaNegasita` → 404
- `AcccesInterzis` → 403
- `DateInvalide` → 422
- `CredentialeInvalide` → 401
- `ServiciuExternIndisponibil` → 503

Un `register_exception_handlers(app)` prinde orice `GlanceError` la nivel global și-l traduce în JSON, logând avertismentul prin `structlog`. Modulele viitoare (Auth, Books, Jobs) ridică direct aceste excepții în loc să manipuleze `HTTPException` peste tot.

### `app/core/logging.py`
`configure_logging(debug: bool)` configurează `structlog`: JSON în producție, output colorat lizibil în consolă când `debug=True`. Se apelează o singură dată, la pornirea aplicației.

### `app/main.py`
Instanțiază `FastAPI`, aplică logging-ul și handler-ele de excepții, expune `GET /health` → `{"status": "ok", "app": "Glance"}`.

### Teste (`tests/conftest.py`, `tests/test_health.py`)
Fixture `client` — un `httpx.AsyncClient` legat direct de aplicație prin `ASGITransport`, fără server real pornit pe un port. Testul verifică status 200 și body-ul de la `/health`.

### `.env.example` / `.gitignore` / `git init`
`.env.example` documentează toate cheile din `Settings`, fără valori reale. `.gitignore` exclude `.venv/`, `.env`, `backend/data/`, `.claude/` și artefacte uzuale (`__pycache__`, cache-urile de `mypy`/`ruff`/`pytest`). Repo-ul git a fost inițializat la rădăcina proiectului (`Smart_Book/`), nu în `backend/`.

## Decizii tehnice

- **`HTTP_422_UNPROCESSABLE_CONTENT`** în loc de `HTTP_422_UNPROCESSABLE_ENTITY` — Starlette a deprecat a doua variantă; am comutat direct ca să nu rămână un warning în output-ul testelor.
- **`.claude/` ignorat de git** — conține fișiere interne de sesiune ale Claude Code (ex. `scheduled_tasks.lock`), nu cod de proiect.
- **Repo git la rădăcina `Smart_Book/`**, nu în `backend/`, ca să poată include ulterior și clientul mobil (Modulul 7) în același istoric.

## Fișiere noi

```
.gitignore
backend/
├── pyproject.toml
├── .env.example
├── app/
│   ├── main.py
│   ├── core/{config,exceptions,logging}.py
│   └── {api,api/routes,models,schemas,services,services/sources,workers,db}/__init__.py
└── tests/
    ├── conftest.py
    └── test_health.py
```

## Cum se verifică

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -v      # 1 passed
./.venv/Scripts/python.exe -m ruff check .   # All checks passed
./.venv/Scripts/python.exe -m black --check . # curat
./.venv/Scripts/python.exe -m mypy app/      # Success: no issues found
```

## Status

Gata. Commit inițial: `33d3f5e`.
