# CLAUDE.md — Glance (client mobil)

Acest fișier se încarcă automat la fiecare sesiune de lucru în `frontend/`. Nu-l repeta în prompturi — actualizează-l pe măsură ce proiectul avansează. Pentru backend, vezi `../CLAUDE.md`.

## Regulă de pornire: verifică documentația versionată

**Expo se schimbă rapid.** Înainte de a scrie cod care folosește un SDK Expo, citește documentația pentru versiunea exactă: <https://docs.expo.dev/versions/v57.0.0/>. API-uri schimbate recent și ușor de greșit din memorie:

- `expo-camera` → componenta e `CameraView` + hook-ul `useCameraPermissions` (nu vechiul `Camera`).
- `expo-image-manipulator` → API contextual `ImageManipulator.manipulate(uri).resize(...).renderAsync()`, apoi `.saveAsync(...)`. `manipulateAsync` există, dar e **deprecated**.

Când tipurile din `node_modules` contrazic documentația, tipurile câștigă — sunt versiunea chiar instalată.

## Prezentare generală

Clientul mobil pentru **Glance**: fotografiezi coperta unei cărți, aplicația recunoaște titlul și autorul, adună material din surse deschise, generează un rezumat prin RAG și oferă recomandări.

React Native + Expo SDK 57, TypeScript strict, expo-router. Backendul e local (FastAPI pe laptop) — clientul **nu** vorbește niciodată cu servicii cloud.

## Cum rulezi

Aplicația se testează pe **telefon fizic prin Expo Go** (decis explicit; emulatorul Android a fost respins — ~1.5 GB RAM pe un laptop cu 7.4 GB care rulează și Ollama).

```bash
# 1. Backendul, cu --host 0.0.0.0 ca telefonul să-l vadă în LAN.
#    Fără asta uvicorn ascultă doar pe 127.0.0.1 și aplicația dă „Nu mă pot conecta".
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 0.0.0.0

# 2. Metro, în alt terminal.
cd frontend && npx expo start
# → scanezi QR-ul cu Expo Go. Telefonul și laptopul pe aceeași rețea Wi-Fi.
```

Adresa backendului **nu trebuie configurată**: `src/config/env.ts` o deduce din gazda Metro, deci merge pe orice rețea fără editat fișiere. Suprascrie cu `EXPO_PUBLIC_API_URL` în `.env` doar dacă backendul rulează pe altă mașină decât Metro.

Referință: IP-ul Wi-Fi al laptopului de dezvoltare a fost `192.168.1.8`. Există și Tailscale (`100.76.52.49`) — instalat și pe telefon, permite folosirea aplicației în afara rețelei locale.

## Decizii de arhitectură (nu le rediscuta fără motiv nou)

### Refresh-ul sesiunii e serializat (single-flight)

**Cea mai importantă decizie din client.** Backendul rotește refresh tokenul: la fiecare `/auth/refresh` reușit, tokenul folosit devine `revoked` (vezi `backend/docs/module-1-auth.md`). Dacă două cereri primesc 401 simultan și fiecare pornește propriul refresh, a doua folosește un token deja consumat, ia 401 și deconectează utilizatorul fără motiv.

`src/api/client.ts` ține o singură `promisiuneRefresh`. Prima cerere care ia 401 o creează; restul se atașează la ea. Refresh-ul folosește `clientBrut`, un axios *fără* interceptors — altfel un 401 la refresh ar declanșa un nou refresh, la infinit.

Simptomul unei regresii aici: delogări aleatorii, greu de reprodus, apărute la ecrane care fac mai multe cereri deodată.

### Normalizarea erorilor într-un singur loc

Backendul răspunde cu două forme de `detail`: string pentru excepțiile de domeniu (`GlanceError`), array de obiecte pentru validarea Pydantic (422). `src/api/errors.ts` le aplatizează în `ApiError`, cu `message` gata de afișat și `eroriCampuri` pentru formulare. Nicio componentă nu citește `error.response.data` direct.

### Tokenurile în SecureStore, cu oglindă în memorie

`expo-secure-store` folosește Keychain/Keystore. Peste el, `src/api/tokenStore.ts` ține o copie în memorie, pentru că interceptorul citește tokenul la fiecare cerere, iar un acces la Keychain per request s-ar simți.

### Starea sesiunii are trei valori, nu două

`'necunoscuta' | 'autentificat' | 'neautentificat'`. Fără `'necunoscuta'`, un utilizator deja autentificat vede o clipă ecranul de login la pornire, până se verifică tokenul din Keychain. Splash screen-ul rămâne vizibil până când starea se rezolvă **și** fonturile s-au încărcat.

### Mock-urile sunt marcate vizibil

Ecranele pentru Modulele 3-6 (încă neimplementate pe backend) folosesc date din `src/mocks/`, dar afișează întotdeauna un `<NotaDemo>` sau un banner. Un mock nemarcat e o minciună pe ecran — nu se poate distinge de un rezultat real în timpul testării.

Comutarea la API real se face **într-un singur loc per domeniu**: `src/features/library/hooks.ts` (flag `DATE_DEMONSTRATIVE`) și `src/features/scan/mapper.ts`. Ecranele nu se modifică.

### Redimensionarea imaginii se face pe client

Backendul respinge upload-uri peste 8 MB cu 413. O poză de telefon depășește ușor pragul. `src/lib/imagine.ts` redimensionează la 1600 px și recomprimă JPEG la 0.85 *înainte* de upload — mai mult decât cei 768 px la care backendul reduce oricum pentru OCR, ca să nu-i luăm detaliile fine din titluri.

### Protecția rutelor prin layout-uri, nu prin redirect-uri în ecrane

`app/(app)/_layout.tsx` redirecționează spre `/login` fără sesiune; `app/(auth)/_layout.tsx` redirecționează spre `/` cu sesiune. Ecranele nu navighează manual după login — starea se schimbă, layout-ul reacționează. O singură sursă de adevăr pentru „unde ajunge utilizatorul".

### `(app)` e un Stack care conține Tabs

Ecranele de scanare trebuie să acopere tot ecranul, fără bara de tab-uri, dar tot sub protecția sesiunii. De aceea `(app)/_layout.tsx` e un `Stack` cu `(tabs)` ca prim ecran și `scan/*` alături, nu direct un `Tabs`.

## Stack tehnologic

| Nevoie | Librărie | Observații |
|---|---|---|
| Framework | `expo` SDK 57 + `react-native` 0.86 | React 19 |
| Navigare | `expo-router` | file-based; rutele sunt fișierele din `app/` |
| Server state | `@tanstack/react-query` | `refetchInterval` face polling-ul pe job-uri |
| Auth state | `zustand` | singurul state global |
| HTTP | `axios` | interceptors pentru refresh |
| Stocare tokenuri | `expo-secure-store` | Keychain / Keystore |
| Cameră | `expo-camera` | `CameraView` + `useCameraPermissions` |
| Imagini | `expo-image-manipulator`, `expo-image` | resize înainte de upload; cache de coperți |
| Formulare | `react-hook-form` + `zod` v4 | `z.email()`, nu `z.string().email()` |
| Fonturi | `@expo-google-fonts/fraunces`, `.../inter` | Fraunces = titluri, Inter = interfață |
| Iconițe | `@expo/vector-icons` (Feather) | |
| Stilizare | `StyleSheet` + tokens din `src/theme` | fără NativeWind — zero config Babel/Metro de întreținut |

`react-dom` e fixat la `19.2.3` ca să coincidă cu `react`. Fără pinning, npm trage `react-dom@19.2.8`, care cere `react@^19.2.8`, și instalarea eșuează cu ERESOLVE. Nu rezolva asta cu `--legacy-peer-deps`.

## Structură de directoare

```
frontend/
├── app/                            # expo-router — doar rutare și compoziție
│   ├── _layout.tsx                 # providers, fonturi, restaurare sesiune, splash
│   ├── (auth)/
│   │   ├── _layout.tsx             # redirect → / dacă există sesiune
│   │   ├── login.tsx
│   │   └── register.tsx
│   └── (app)/
│       ├── _layout.tsx             # poarta de acces: redirect → /login fără sesiune
│       ├── (tabs)/
│       │   ├── _layout.tsx         # bara de tab-uri
│       │   ├── index.tsx           # Acasă — captură copertă
│       │   ├── recomandari.tsx     # mock (Modulul 6)
│       │   └── profil.tsx          # identitate reală + istoric/preferințe mock
│       └── scan/
│           ├── camera.tsx          # captură, full-screen
│           └── [jobId].tsx         # polling + rezultat
├── src/
│   ├── api/
│   │   ├── client.ts               # axios + single-flight refresh
│   │   ├── tokenStore.ts           # SecureStore + oglindă în memorie
│   │   ├── errors.ts               # ApiError, normalizarea celor două forme de `detail`
│   │   └── endpoints/              # auth, users, books, jobs
│   ├── components/
│   │   ├── ui/                     # Button, Input, Screen, Card, Chip, BannerEroare, NotaDemo
│   │   └── book/                   # CardCarte, RatingStele
│   ├── config/env.ts               # API_URL dedus din gazda Metro
│   ├── features/
│   │   ├── auth/schema.ts          # validare zod
│   │   ├── scan/                   # hooks (upload + polling), mapper rezultat
│   │   └── library/hooks.ts        # istoric/preferințe/recomandări — comutator mock
│   ├── lib/                        # imagine.ts, queryClient.ts
│   ├── mocks/                      # date demonstrative, tipate ca API-ul real
│   ├── store/authStore.ts
│   ├── theme/                      # colors, typography, spacing, fonts
│   └── types/                      # api.ts (oglindește Pydantic), biblioteca.ts (Modulul 6)
└── .env.example
```

`app/` conține doar rutare; logica stă în `src/features/`. Regula practică: dacă un fișier din `app/` depășește ~200 de linii sau conține logică de rețea, mută-o într-un hook de feature.

## Module — status

Frontendul avansează **în paralel cu backendul**, modul cu modul. Nu construi ecrane pentru module de backend care nu există decât pe mock-uri marcate vizibil.

- [x] **Modulul 0: Fundație** — schelet Expo, TypeScript strict cu `noUncheckedIndexedAccess`, alias `@/*`, tokens de temă, fonturi, `env.ts` cu deducerea gazdei.
      *Gata când:* `npx tsc --noEmit` curat și `npx expo export` produce un bundle.
- [x] **Modulul 1: Auth** — `tokenStore`, `client.ts` cu single-flight refresh, `ApiError`, `authStore` cu trei stări, ecrane Login/Register, protecția rutelor prin layout-uri.
      *Gata când:* register → login → sesiune persistentă după restart → logout, toate verificate pe telefon.
- [x] **Modulul 2: Schelet scanare** — `expo-camera`, redimensionare înainte de upload, `POST /books/analyze-cover`, polling pe `GET /jobs/{id}` cu oprire automată, ecran de rezultat cu stările `pending`/`running`/`done`/`failed`.
      *Gata când:* o fotografie reală parcurge fluxul până la afișarea rezultatului placeholder.
- [ ] **Modulul 3: Vision** — afișarea titlului/autorului reali cu scorul de încredere; **ecran de corecție manuală** când încrederea e sub prag. Tipul `RezultatAnaliza` există deja în `src/types/api.ts`.
- [ ] **Modulul 4: Data fetcher** — coperți reale (`coperta_url` → `expo-image`), categorii, rating mediu.
- [ ] **Modulul 5: RAG** — rezumat real cu citări; fiecare afirmație trebuie să ducă la o sursă apăsabilă. Structura `RecenzieSursa` e deja în ecranul de rezultat.
- [ ] **Modulul 6: Recomandări** — înlocuiește mock-urile din `src/features/library/hooks.ts`, pune `DATE_DEMONSTRATIVE` pe `false`, verifică tipurile din `src/types/biblioteca.ts` contra schemei reale.
- [ ] **Modulul 7: Rafinare** — animații (`react-native-reanimated` e deja instalat), stări goale, tratarea offline, temă întunecată (tokenii sunt structurați pentru asta).

## Comenzi utile

```bash
npx expo start                     # pornește Metro (QR pentru Expo Go)
npx expo start --clear             # când Metro servește un bundle vechi
npx tsc --noEmit                   # typecheck — rulează-l înainte de fiecare commit
npx expo export --platform android # verifică fără telefon că totul se împachetează
npx expo install <pachet>          # NU `npm install` pentru pachete Expo — alege versiunea compatibilă cu SDK-ul
```

## Convenții de cod

- **Type hints stricte.** `strict` + `noUncheckedIndexedAccess`. Fără `any` nejustificat, fără `as` care ascunde o nepotrivire reală (excepția documentată: `FormData` cu fișiere locale în React Native).
- **Docstrings** pe fiecare modul, componentă exportată și funcție publică — ce face, parametri, ce returnează. Explică *de ce*, nu *ce*, când decizia nu e evidentă.
- **Denumiri**: română pentru domeniu (`carte`, `recenzii`, `utilizator`, `analiza`), engleză pentru primitive tehnice standard. Concret: props-urile componentelor UI generice sunt în engleză (`label`, `loading`, `disabled`), iar componentele de domeniu și logica de business sunt în română (`CardCarte`, `pregatesteCopertaPentruUpload`).
- **Fără culori sau spații literale** în componente. Totul din `src/theme`.
- **Fără `console.log`** în cod care rămâne. Pentru diagnostic pe telefon, folosește ecranul — utilizatorul nu vede terminalul Metro.
- **Fiecare ecran tratează explicit patru stări**: încărcare, gol, eroare, succes. Un ecran care presupune că datele există e un ecran care va crăpa.
- **Tipurile din `src/types/api.ts` sunt contractul.** Când backendul se schimbă, actualizează-le *întâi*; typecheck-ul arată apoi ce ecrane trebuie ajustate.

## Reguli stricte

- Niciun apel către servicii cloud. Backendul local e singura destinație de rețea.
- `.env` niciodată în git — doar `.env.example`.
- Tokenurile doar în `expo-secure-store`, niciodată în `AsyncStorage` sau într-un store în memorie persistat pe disc.
- Nu adăuga pachete Expo cu `npm install` — folosește `npx expo install`.
- Un modul per sesiune, verificat pe telefon, apoi următorul.
- Mock-urile se marchează vizibil, întotdeauna.
