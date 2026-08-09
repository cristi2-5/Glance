# Frontend, Modulele 0-2: aducerea clientului la paritate cu backendul

> **De ce un singur fișier pentru trei module.** Backendul avea deja Modulele 0-2 terminate când a început lucrul la client. Sesiunea asta a fost o recuperare de decalaj, nu trei module separate. Începând cu Modulul 3, frontendul revine la convenția „un document per modul", în pas cu backendul.

## Ce trebuia făcut

Un client mobil care să acopere exact capabilitățile existente pe backend: autentificare completă cu sesiune persistentă, și fluxul de scanare până la afișarea rezultatului unui job. Ecranele care depind de Modulele 3-6 (vision, surse, RAG, recomandări) se construiesc acum pe date demonstrative, marcate vizibil.

## Recunoașterea contractelor înainte de cod

Contractele au fost extrase din `app.openapi()` — aceeași sursă pe care o servește Swagger UI la `/docs`, dar fără server pornit. Cinci constatări au schimbat designul clientului:

1. **Refresh tokenul e single-use, cu rotation.** A dictat arhitectura stratului HTTP (vezi mai jos).
2. **`/auth/login` primește JSON `{email, password}`**, nu form-data OAuth2 — în ciuda schemei de securitate `OAuth2PasswordBearer` din OpenAPI.
3. **`/auth/register` întoarce `UserPublic`, nu tokenuri.** Înregistrarea e urmată de un login explicit.
4. **Două forme de `detail` în erori** — string pentru `GlanceError`, array pentru validarea Pydantic.
5. **Lipsea `CORSMiddleware`**, iar `uvicorn --reload` ascultă doar pe `127.0.0.1`.

De asemenea: câmpul multipart se numește exact `file`; `POST /analyze-cover` întoarce `{job_id}`, dar `GET /jobs/{id}` întoarce `{id, ...}`.

## Decizii tehnice

### Refresh serializat (single-flight) — cea mai importantă

Pentru că backendul revocă refresh tokenul la fiecare utilizare, două cereri care iau 401 simultan ar produce două refresh-uri, iar al doilea ar eșua pe un token deja consumat → delogare fără motiv.

`src/api/client.ts` menține o singură `promisiuneRefresh`; toți apelanții concurenți se atașează la ea. Refresh-ul însuși trece printr-un axios fără interceptors (`clientBrut`), altfel un 401 la refresh ar declanșa recursiv alt refresh.

### Normalizare a erorilor într-un singur strat

`src/api/errors.ts` transformă ambele forme de `detail` într-un `ApiError` cu `message` afișabil și `eroriCampuri` pentru formulare, plus mesaje în română pentru erorile de rețea și timeout. Nicio componentă nu atinge `error.response.data`.

### Adresa backendului dedusă din gazda Metro

`src/config/env.ts` derivă `API_URL` din `Constants.expoConfig.hostUri` — telefonul știe deja IP-ul laptopului de la care a încărcat bundle-ul. Efect practic: schimbarea rețelei Wi-Fi nu cere editarea niciunui fișier. `EXPO_PUBLIC_API_URL` rămâne ca override.

### Trei stări de sesiune, nu două

`'necunoscuta'` acoperă intervalul dintre pornire și verificarea tokenului din Keychain. Splash screen-ul rămâne vizibil până când sesiunea se rezolvă *și* fonturile se încarcă — altfel apar un flash cu fontul de sistem și o clipă de ecran de login pentru un utilizator deja autentificat.

### Redimensionare pe client, înainte de upload

`src/lib/imagine.ts` reduce la 1600 px și recomprimă JPEG la 0.85. Backendul respinge peste 8 MB cu 413, iar o poză de telefon depășește ușor pragul. 1600 px, nu 768 ca ținta finală a backendului, ca să nu-i luăm detaliile fine din titlurile mici.

API-ul folosit e cel contextual din SDK 57 (`ImageManipulator.manipulate(...).resize(...).renderAsync()`), verificat în tipurile din `node_modules` — `manipulateAsync` e deprecated.

### Mock-uri marcate, comutabile dintr-un singur loc

Ecranele pentru Modulele 3-6 afișează un `<NotaDemo>` explicit. Comutarea la API real se face în `src/features/library/hooks.ts` (flag `DATE_DEMONSTRATIVE`) și în `src/features/scan/mapper.ts` — ecranele rămân neatinse. Mock-urile respectă exact tipurile din `src/types/`.

### Protecția rutelor prin layout-uri

`(app)/_layout.tsx` redirecționează spre `/login` fără sesiune; `(auth)/_layout.tsx` spre `/` cu sesiune. Ecranele nu navighează manual după login. `(app)` e un `Stack` care conține `(tabs)`, ca ecranele de scanare să fie full-screen dar tot protejate.

### `react-dom` fixat la `19.2.3`

`@expo/dom-webview` trage `react-dom@19.2.8`, care cere `react@^19.2.8`, în timp ce SDK 57 fixează `react@19.2.3` → ERESOLVE la orice instalare ulterioară. Rezolvat prin pinning explicit, nu prin `--legacy-peer-deps`, care ar fi lăsat un arbore incoerent.

## Modificări în backend

Într-un commit separat, minimal:

- `app/main.py` — `CORSMiddleware`, necesar doar pentru clienți din browser (Expo Go nativ nu trece prin CORS).
- `app/core/config.py` — setarea `cors_origins`, implicit `["*"]` pentru dezvoltare în LAN.
- `.env.example` — cheia `CORS_ORIGINS`.

Cele 19 teste existente, `mypy`, `ruff` și `black` rămân curate.

## Fișiere noi

```
frontend/
├── app/
│   ├── _layout.tsx                      # providers, fonturi, restaurare sesiune
│   ├── (auth)/{_layout,login,register}.tsx
│   └── (app)/
│       ├── _layout.tsx                  # poarta de acces
│       ├── (tabs)/{_layout,index,recomandari,profil}.tsx
│       └── scan/{camera,[jobId]}.tsx
├── src/
│   ├── api/{client,tokenStore,errors}.ts
│   ├── api/endpoints/{auth,users,books,jobs}.ts
│   ├── components/ui/{Button,Input,Screen,Card,Chip,BannerEroare,NotaDemo}.tsx
│   ├── components/book/{CardCarte,RatingStele}.tsx
│   ├── config/env.ts
│   ├── features/auth/schema.ts
│   ├── features/scan/{hooks,mapper}.ts
│   ├── features/library/hooks.ts
│   ├── lib/{imagine,queryClient}.ts
│   ├── mocks/{analiza,biblioteca}.ts
│   ├── store/authStore.ts
│   ├── theme/{colors,typography,spacing,fonts,index}.ts
│   └── types/{api,biblioteca}.ts
├── CLAUDE.md
└── .env.example
```

## Cum se verifică

```bash
cd frontend
npx tsc --noEmit                     # typecheck strict — curat
npx expo export --platform android   # bundle complet — verifică importurile la runtime
```

Ambele au trecut. Bundle-ul produs: 5.2 MB bytecode Hermes.

**Verificarea pe telefon nu a fost încă făcută** — necesită Expo Go pe un telefon fizic, în aceeași rețea cu laptopul, și backendul pornit cu `--host 0.0.0.0`. Vezi secțiunea „Cum rulezi" din `frontend/CLAUDE.md`.

## Ce rămâne pentru sesiuni viitoare

- **Verificarea pe telefon a fluxului complet**: register → login → repornire aplicație (sesiunea trebuie să persiste) → scanare → polling → rezultat → logout.
- Ecranul de corecție manuală a titlului/autorului — apare odată cu Modulul 3, când există un scor de încredere real.
- Nicio suită de teste automate pe frontend încă. De adăugat `jest-expo` + `@testing-library/react-native`, cu prioritate pe `client.ts` (refresh-ul concurent merită un test dedicat) și pe `errors.ts`.

## Status

Gata, în limita verificării automate. Confirmarea pe telefon rămâne de făcut cu userul.
