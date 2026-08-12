# CLAUDE.md — Glance (mobile client)

This file is automatically loaded at every work session in `frontend/`. Don't repeat it in prompts — update it as the project progresses. For the backend, see `../CLAUDE.md`.

## Starting rule: check the versioned docs

**Expo changes fast.** Before writing code that uses an Expo SDK API, read the docs for the exact version: <https://docs.expo.dev/versions/v54.0.0/>. APIs that changed recently and are easy to get wrong from memory:

- `expo-camera` → the component is `CameraView` + the `useCameraPermissions` hook (not the old `Camera`).
- `expo-image-manipulator` → contextual API `ImageManipulator.manipulate(uri).resize(...).renderAsync()`, then `.saveAsync(...)`. `manipulateAsync` exists but is **deprecated**.

When the types in `node_modules` contradict the docs, the types win — they're the version actually installed.

### Why SDK 54, not "latest" (57)

The project was originally scaffolded with `expo@latest`, which at the time meant SDK 57. **Expo Go on the phone doesn't keep pace with npm** — especially on iOS, where Apple review delays the compatible Expo Go release by days or weeks after a new SDK ships. Symptom: the app refuses to open the project, with an SDK-incompatibility message, even though Expo Go looks "up to date" in the App Store.

Always check which SDK the *actually installed* Expo Go build supports (not what the marketing page says) before picking the `expo` version in `package.json`. If an incompatibility shows up again, downgrade with:

```bash
npx expo install expo@^<supported-version>.0.0
rm -rf node_modules package-lock.json
npm install
npx expo install --fix
```

After any SDK change, re-check `app.json` → `plugins`: every package listed there must actually have an `app.plugin.js` (`ls node_modules/<package>/app.plugin.js`). A package without a real plugin (e.g. `expo-image`, `expo-status-bar` — neither needs one) makes `expo config`/`expo start` fail silently, with no message, on Node 22+ because of experimental "type stripping" hitting `.ts` files inside `node_modules`. The symptom is exit code 1 with no stdout/stderr — if that happens, run `node node_modules/expo/node_modules/@expo/cli config --json` directly to see the real error, which is hidden behind the `npx expo` CLI.

## Overview

The mobile client for **Glance**: you photograph a book cover, the app recognizes the title and author, gathers material from open sources, generates a summary via RAG, and offers recommendations.

React Native + Expo SDK 54, strict TypeScript, expo-router. The backend is local (FastAPI on the laptop) — the client **never** talks to cloud services.

## How to run it

The app is tested on a **physical phone via Expo Go** (explicit decision; the Android emulator was rejected — ~1.5 GB RAM on a laptop with 7.4 GB that's also running Ollama).

```bash
# 1. Backend, with --host 0.0.0.0 so the phone can see it on the LAN.
#    Without this uvicorn only listens on 127.0.0.1 and the app shows "Can't connect".
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 0.0.0.0

# 2. Metro, in another terminal.
cd frontend && npx expo start
# → scan the QR code with Expo Go. Phone and laptop on the same Wi-Fi network.
```

The backend address **doesn't need to be configured**: `src/config/env.ts` infers it from the Metro host, so it works on any network without editing files. Override with `EXPO_PUBLIC_API_URL` in `.env` only if the backend runs on a different machine than Metro.

Reference: the dev laptop's Wi-Fi IP was `192.168.1.8`. Tailscale is also set up (`100.76.52.49`) — installed on the phone too, allows using the app outside the local network.

## Architecture decisions (don't revisit without a new reason)

### Session refresh is serialized (single-flight)

**The most important decision in the client.** The backend rotates the refresh token: on every successful `/auth/refresh`, the token used becomes `revoked` (see `backend/docs/module-1-auth.md`, local/gitignored). If two requests get a 401 at the same time and each starts its own refresh, the second one uses an already-consumed token, gets a 401, and logs the user out for no reason.

`src/api/client.ts` keeps a single `refreshPromise`. The first request that gets a 401 creates it; the rest attach to it. The refresh uses `rawClient`, an axios instance *without* interceptors — otherwise a 401 on the refresh call itself would trigger another refresh, infinitely.

The symptom of a regression here: random, hard-to-reproduce logouts, showing up on screens that fire several requests at once.

### Error normalization in a single place

The backend responds with two shapes of `detail`: a string for domain exceptions (`GlanceError`), an array of objects for Pydantic validation (422). `src/api/errors.ts` flattens both into `ApiError`, with a display-ready `message` and `fieldErrors` for forms. No component reads `error.response.data` directly.

### Tokens in SecureStore, mirrored in memory

`expo-secure-store` uses Keychain/Keystore. On top of it, `src/api/tokenStore.ts` keeps an in-memory copy, because the interceptor reads the token on every request, and a Keychain access per request would be noticeable.

### Session state has three values, not two

`'unknown' | 'authenticated' | 'unauthenticated'`. Without `'unknown'`, an already-authenticated user briefly sees the login screen on startup, until the token is verified against the Keychain. The splash screen stays visible until the state resolves **and** the fonts have loaded.

### A correction discards the metadata fetched for the old title

Once Module 4 populates covers, blurbs and ratings, the manual-correction
flow has a trap: if the user corrects "Dune" to something else, the
metadata already fetched describes the *wrong book*. Leaving it on screen
under the corrected title is worse than showing nothing — it looks
authoritative and is simply false.

So `PATCH /jobs/{id}/correction` clears the catalog half of the result
immediately, puts the job back into `running`, and re-fetches in the
background. **The client needed no special-casing for this**: `useJob`'s
`refetchInterval` resumes polling the moment a `running` job lands in the
cache, and the result screen already handles `running`. The only addition
is a distinct wait message when `result.corrected` is true — the cover has
already been read, so "Analyzing" would be a lie.

The corrected title always wins over the catalog's spelling on the way
back: the user typed it deliberately.

### Mocks are visibly marked

Screens for Modules 3-6 (not yet implemented on the backend) use data from `src/mocks/`, but always show a `<DemoNote>` or a banner. An unmarked mock is a lie on the screen — indistinguishable from a real result during testing.

Switching to the real API happens **in a single place per domain**: `src/features/library/hooks.ts` (the `DEMO_DATA` flag) and `src/features/scan/mapper.ts`. Screens don't change.

### Image resizing happens on the client

The backend rejects uploads over 8 MB with 413. A phone photo easily exceeds that. `src/lib/imagine.ts` resizes to 1600 px and recompresses JPEG at 0.85 *before* upload — more than the 768 px the backend downsizes to anyway for OCR, so we don't lose fine detail from titles.

### Route protection via layouts, not per-screen redirects

`app/(app)/_layout.tsx` redirects to `/login` without a session; `app/(auth)/_layout.tsx` redirects to `/` with a session. Screens don't navigate manually after login — the state changes, the layout reacts. A single source of truth for "where the user ends up".

### `(app)` is a Stack that contains Tabs

Scan screens need to cover the whole screen, without the tab bar, but still under session protection. That's why `(app)/_layout.tsx` is a `Stack` with `(tabs)` as its first screen and `scan/*` alongside it, not directly a `Tabs`.

## Tech stack

| Need | Library | Notes |
|---|---|---|
| Framework | `expo` SDK 54 + `react-native` 0.81 | React 19.1 |
| Navigation | `expo-router` | file-based; routes are the files in `app/` |
| Server state | `@tanstack/react-query` | `refetchInterval` does the job polling |
| Auth state | `zustand` | the only global state |
| HTTP | `axios` | interceptors for refresh |
| Token storage | `expo-secure-store` | Keychain / Keystore |
| Camera | `expo-camera` | `CameraView` + `useCameraPermissions` |
| Images | `expo-image-manipulator`, `expo-image` | resize before upload; cover cache |
| Forms | `react-hook-form` + `zod` v4 | `z.email()`, not `z.string().email()` |
| Fonts | `@expo-google-fonts/fraunces`, `.../inter` | Fraunces = headings, Inter = UI |
| Icons | `@expo/vector-icons` (Feather) | |
| Styling | `StyleSheet` + tokens from `src/theme` | no NativeWind — zero Babel/Metro config to maintain |

`react-dom` must match the `react` version from the current SDK exactly (currently `19.1.0`). A mismatch here causes ERESOLVE on any later `npm install`, pulled in by a transitive package (`@expo/dom-webview` → `react-server-dom-webpack`). Don't fix this with `--legacy-peer-deps` — repin `react-dom` to the correct version and reinstall clean.

## Directory structure

```
frontend/
├── app/                            # expo-router — routing and composition only
│   ├── _layout.tsx                 # providers, fonts, session restore, splash
│   ├── (auth)/
│   │   ├── _layout.tsx             # redirect → / if a session exists
│   │   ├── login.tsx
│   │   └── register.tsx
│   └── (app)/
│       ├── _layout.tsx             # access gate: redirect → /login without a session
│       ├── (tabs)/
│       │   ├── _layout.tsx         # tab bar
│       │   ├── index.tsx           # Home — cover capture
│       │   ├── recomandari.tsx     # mock (Module 6)
│       │   └── profil.tsx          # real identity + mock history/preferences
│       └── scan/
│           ├── camera.tsx          # capture, full-screen
│           └── [jobId].tsx         # polling + result
├── src/
│   ├── api/
│   │   ├── client.ts               # axios + single-flight refresh
│   │   ├── tokenStore.ts           # SecureStore + in-memory mirror
│   │   ├── errors.ts               # ApiError, normalizes the two shapes of `detail`
│   │   └── endpoints/              # auth, users, books, jobs
│   ├── components/
│   │   ├── ui/                     # Button, Input, Screen, Card, Chip, BannerEroare, NotaDemo
│   │   └── book/                   # CardCarte, RatingStele, BookCover
│   ├── config/env.ts               # API_URL inferred from the Metro host
│   ├── features/
│   │   ├── auth/schema.ts          # zod validation
│   │   ├── scan/                   # hooks (upload + polling), result mapper
│   │   └── library/hooks.ts        # history/preferences/recommendations — mock switch
│   ├── lib/                        # imagine.ts, queryClient.ts
│   ├── mocks/                      # demo data, typed like the real API
│   ├── store/authStore.ts
│   ├── theme/                      # colors, typography, spacing, fonts
│   └── types/                      # api.ts (mirrors Pydantic), biblioteca.ts (Module 6)
└── .env.example
```

Filenames under `app/` and `src/` stayed as originally chosen (e.g. `recomandari.tsx`, `profil.tsx`, `CardCarte.tsx`, `imagine.ts`, `biblioteca.ts`) even after the Romanian→English identifier refactor — `app/` filenames are expo-router route segments, so renaming them would change live URLs; the `src/` ones were left unchanged for consistency. The *exported* symbols inside these files are English (e.g. `CardCarte.tsx` exports `BookCard`, `NotaDemo.tsx` exports `DemoNote`).

**New files get English names** (e.g. `BookCover.tsx`, added in Module 4). The legacy names are grandfathered, not a convention to follow — so the two spellings will coexist for a while, and that's expected.

`app/` contains only routing; logic lives in `src/features/`. Rule of thumb: if a file under `app/` exceeds ~200 lines or contains network logic, move it into a feature hook.

## Modules — status

The frontend progresses **in parallel with the backend**, module by module. Don't build screens for backend modules that don't exist yet except on visibly-marked mocks.

- [x] **Module 0: Foundation** — Expo scaffold, strict TypeScript with `noUncheckedIndexedAccess`, `@/*` alias, theme tokens, fonts, `env.ts` with host inference.
      *Done when:* `npx tsc --noEmit` clean and `npx expo export` produces a bundle.
- [x] **Module 1: Auth** — `tokenStore`, `client.ts` with single-flight refresh, `ApiError`, `authStore` with three states, Login/Register screens, route protection via layouts.
      *Done when:* register → login → session persists across restart → logout, all verified on the phone.
- [x] **Module 2: Scan skeleton** — `expo-camera`, resize before upload, `POST /books/analyze-cover`, polling on `GET /jobs/{id}` with automatic stop, result screen with `pending`/`running`/`done`/`failed` states.
      *Done when:* a real photo goes through the flow up to displaying the placeholder result.
- [x] **Module 3: Vision** — real title/author with confidence displayed on the result screen (`app/(app)/scan/[jobId].tsx`); confidence-chip tone and the "Fix the title" prompt both key off the backend's `needs_review` flag, never a local threshold. Manual-correction screen at `app/(app)/scan/correct/[jobId].tsx`, wired to `PATCH /jobs/{id}/correction` via `useCorrectJob` (`src/features/scan/hooks.ts`), which writes the result straight into the `['job', jobId]` query cache on success.
- [x] **Module 4: Data fetcher** — real covers (`cover_url` → `expo-image` via the shared `BookCover`), publisher description, categories, average rating with its ratings count. `AnalysisResult` in `src/types/api.ts` gained `book_id`, `metadata_found`, `description`, `ratings_count`, `source_count`. Two states the screen now handles explicitly: **no catalog match** (`metadata_found === false` → a plain notice, not a blank page — routine for Romanian editions) and **a corrected title** (see below).
      *Done when:* `npx tsc --noEmit` clean and `npx expo export` bundles. **Done.**
- [ ] **Module 5: RAG** — real summary with citations; every claim must link to a tappable source. The `SourceReview` structure is already in the result screen. Until then, the screen shows the publisher's `description` under a "From the publisher" heading, explicitly labelled so it isn't mistaken for a generated summary.
- [ ] **Module 6: Recommendations** — replace the mocks in `src/features/library/hooks.ts`, set `DEMO_DATA` to `false`, check the types in `src/types/biblioteca.ts` against the real schema.
- [ ] **Module 7: Polish** — animations (`react-native-reanimated` already installed), empty states, offline handling, dark theme (tokens are structured for it).

## Useful commands

```bash
npx expo start                     # start Metro (QR for Expo Go)
npx expo start --clear             # when Metro serves a stale bundle
npx tsc --noEmit                   # typecheck — run before every commit
npx expo export --platform android # verify without a phone that everything bundles
npx expo install <package>         # NOT `npm install` for Expo packages — picks the SDK-compatible version
```

## Code conventions

- **Strict type hints.** `strict` + `noUncheckedIndexedAccess`. No unjustified `any`, no `as` that hides a real mismatch (documented exception: `FormData` with local files in React Native).
- **Docstrings** on every module, exported component, and public function — what it does, parameters, return value. Explain *why*, not *what*, when the decision isn't obvious.
- **Naming**: English throughout, including domain terms — the codebase was translated from an earlier Romanian-first convention; don't reintroduce Romanian identifiers. Filenames were deliberately left unchanged (see the directory structure note above) even though the exported symbols inside them are English.
- **No literal colors or spacing** in components. Everything from `src/theme`.
- **No `console.log`** in code that stays. For on-phone diagnostics, use the screen — the user doesn't see the Metro terminal.
- **Every screen explicitly handles four states**: loading, empty, error, success. A screen that assumes data exists is a screen that will crash.
- **The types in `src/types/api.ts` are the contract.** When the backend changes, update them *first*; the typecheck then shows which screens need adjusting.

## Strict rules

- No calls to cloud services. The local backend is the only network destination.
- `.env` never in git — only `.env.example`.
- Tokens only in `expo-secure-store`, never in `AsyncStorage` or an in-memory store persisted to disk.
- Don't add Expo packages with `npm install` — use `npx expo install`.
- One module per session, verified on the phone, then the next.
- Mocks are always visibly marked.
