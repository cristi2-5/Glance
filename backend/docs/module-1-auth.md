# Modulul 1: Auth

## Ce trebuia făcut

Un sistem de conturi minimal, dar corect: înregistrare, autentificare, reînnoire de sesiune și deconectare, plus un endpoint care confirmă cine e utilizatorul curent. Fără el, niciun endpoint viitor (`/books/analyze-cover`, `/jobs/{id}`, istoricul de lectură) nu are cum să știe cui aparține o resursă.

## Ce s-a implementat

### Model de date

- **`app/models/user.py` — `User`**: `id`, `email` (unic, indexat), `hashed_password`, `is_active`, `created_at`.
- **`app/models/refresh_token.py` — `RefreshToken`**: `id`, `token_hash`, `user_id` (FK), `expires_at`, `revoked`, `created_at`.
- **`app/db/session.py`**: `Base` declarativă, `engine` async (din `settings.database_url`), `AsyncSessionLocal`, dependency `get_db()`.
- **`app/db/init_db.py`**: `init_db()` — `Base.metadata.create_all`, apelat din `lifespan` la pornirea aplicației (`app/main.py`). Suficient cât schema e simplă; se trece la Alembic când apar migrații reale (vezi CLAUDE.md).

### Refresh token: opac, nu JWT

Decizie luată explicit cu userul: refresh tokenul e un string aleator (`secrets.token_urlsafe(32)`), **nu** un JWT. Doar hash-ul SHA-256 al lui se stochează în `refresh_tokens.token_hash` — o citire a bazei de date nu expune token-uri valide. Motivul: revocarea și rotation-ul sunt un simplu `UPDATE ... SET revoked = true`, fără nevoie de a urmări `jti`-uri sau de a parsa/valida JWT-uri la fiecare refresh. La scara asta (un singur user, local), simplitatea a bătut orice beneficiu teoretic al unui JWT refresh token.

**Rotation**: la fiecare `/auth/refresh` reușit, tokenul folosit e marcat `revoked = True` și se emite unul nou. Reîncercarea cu tokenul vechi eșuează cu 401 — testat explicit (`test_refresh_rotation_invalideaza_tokenul_vechi`).

### `app/core/security.py`

- `hash_password` / `verify_password` — `bcrypt` direct (nu `passlib`, vezi regula din CLAUDE.md).
- `create_access_token` / `decode_access_token` — JWT semnat (`python-jose`), payload `{sub, type="access", iat, exp}`, expiră după `access_token_expire_minutes`.
- `generate_refresh_token` / `hash_refresh_token` / `refresh_token_expiry` — pentru fluxul de refresh token opac descris mai sus.

### Rute

| Rută | Ce face |
|---|---|
| `POST /auth/register` | Creează un `User`, 201 cu `UserPublic` (fără parolă). 422 dacă emailul e deja folosit. |
| `POST /auth/login` | Verifică email+parolă, emite pereche `access_token` + `refresh_token`. 401 la credențiale greșite sau cont inactiv. |
| `POST /auth/refresh` | Validează refresh tokenul (există, nu e revocat, nu a expirat), îl revocă, emite o pereche nouă (rotation). 401 dacă tokenul e invalid. |
| `POST /auth/logout` | Revocă refresh tokenul dat. 204, idempotent — nu scurge informație dacă tokenul exista sau nu. |
| `GET /users/me` | Returnează `UserPublic` pentru utilizatorul din tokenul de acces curent (`Authorization: Bearer ...`). 401 fără token sau cu token expirat/invalid. |

`app/api/deps.py` expune `DbSession` (dependency de sesiune DB) și `UtilizatorCurent` (rezolvă `User` din JWT-ul de acces prin `OAuth2PasswordBearer`).

### Teste (`tests/test_auth.py`)

12 teste, toate pe o bază de date SQLite **în memorie**, izolată per test (fixture `db_session_factory` în `conftest.py`, cu `StaticPool` ca să persiste conexiunea în cadrul unui singur test). `app.dependency_overrides[get_db]` înlocuiește sesiunea reală cu cea de test — zero atingere a `backend/data/glance.db` din suită.

Acoperă exact criteriile din CLAUDE.md:
- **register duplicat** → 422
- **login greșit** (parolă greșită / email inexistent) → 401
- **token expirat** (JWT construit manual cu `exp` în trecut) → 401
- **refresh rotation** (tokenul vechi devine invalid după un refresh reușit) → 401 la reîncercare, 200 cu tokenul nou

Plus: register reușit nu expune parola, lipsă token → 401, refresh cu token necunoscut → 401, logout idempotent.

## Decizii tehnice

- **Refresh token opac în DB**, nu JWT — vezi secțiunea de mai sus. Ales explicit cu userul dintre cele două variante.
- **`register` nu loghează automat** — întoarce doar `UserPublic` (201). Login e un pas separat, explicit. Ține fluxurile simple și separate: creare cont ≠ sesiune.
- **`logout` e idempotent** — un refresh token inexistent sau deja revocat tot întoarce 204, ca să nu ofere un oracol ("acest token a existat vreodată?") unui atacator.
- **Duplicat de email → `DateInvalide` (422)**, nu un nou tip de excepție `409 Conflict`. Ierarhia de excepții din CLAUDE.md nu are un echivalent 409; adăugarea unuia nou pentru un singur caz de uz nu se justifica.
- **Datetime-uri naive UTC peste tot** (`datetime.utcnow()`), nu aware — coloanele SQLAlchemy `DateTime` sunt naive; amestecul aware/naive ar fi produs erori de comparație greu de depistat între `expires_at` (din DB) și „acum".
- **`email-validator` adăugat ca dependență nouă** — necesar pentru `pydantic.EmailStr`. Nu face cereri de rețea (fără `check_deliverability`), deci nu încalcă regula „zero apeluri de rețea în suită".
- **`types-python-jose` adăugat ca dependență de dev** — elimină eroarea `mypy` de stub-uri lipsă pentru `jose`.

## Fișiere noi / modificate

```
backend/
├── pyproject.toml                          # +email-validator, +types-python-jose
├── app/
│   ├── main.py                             # lifespan → init_db(), routere auth+users
│   ├── db/
│   │   ├── session.py                      # Base, engine, AsyncSessionLocal, get_db
│   │   └── init_db.py                      # create_all
│   ├── models/
│   │   ├── user.py                         # User
│   │   └── refresh_token.py                # RefreshToken
│   ├── schemas/
│   │   ├── auth.py                         # RegisterRequest, LoginRequest, RefreshRequest, TokenResponse
│   │   └── user.py                         # UserPublic
│   ├── core/
│   │   └── security.py                     # hashing, JWT, refresh token opac
│   └── api/
│       ├── deps.py                         # DbSession, UtilizatorCurent
│       └── routes/
│           ├── auth.py                     # register, login, refresh, logout
│           └── users.py                    # GET /users/me
└── tests/
    ├── conftest.py                         # fixture DB de test izolată (SQLite în memorie)
    └── test_auth.py                        # 12 teste
```

## Cum se verifică

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -v            # 12 passed (+ 1 din module-0)
./.venv/Scripts/python.exe -m ruff check .          # All checks passed
./.venv/Scripts/python.exe -m black --check .       # curat
./.venv/Scripts/python.exe -m mypy app/             # Success: no issues found
```

## Ce rămâne pentru sesiuni viitoare

- Înainte de orice deploy (chiar și local, dincolo de dev): setează `JWT_SECRET_KEY` real în `.env` — implicit e placeholder-ul `schimba-aceasta-cheie-in-.env`.
- Modulul 2 (schelet API + job-uri) va folosi `UtilizatorCurent` din `api/deps.py` ca să lege job-urile de proprietarul lor.

## Status

Gata.
