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

## The design system is a skill — load it before touching any screen

The visual language lives in **`.claude/skills/design-system/SKILL.md`**, implemented by the tokens in `src/theme/`. Load that skill before writing or editing any screen or UI component, before picking a color/size/padding/radius, and before adding a `<Text>` that renders user data (book titles, author names, emails).

It is the authority on three things this file deliberately doesn't repeat:

- **Tokens** — the paper/ink palette, the type scale (every `lineHeight` a multiple of 4), the 4 px spacing grid (`space[1]`…`space[10]`), radii, shadows.
- **Text fitting** — the per-element truncate-vs-shrink contract (`textFit` presets in `src/theme/text.ts`), plus the structural rules that actually prevent overflow in React Native: `textColumn` on any text inside a row, never a fixed `height` on a text container, `maxFontSizeMultiplier` everywhere.
- **Safe areas** — which `edges` a screen passes to `Screen`, and why tab screens must not absorb the bottom inset.

The palette changed with this system: the primary action is now **espresso `#3A342E`**, not terracotta, and mustard `#E8C24E` is a *highlight* that must never be a button fill or a text color on paper. The old token names (`background`, `surface`, `accent`, `amber`, `spacing.xs…xxxl`) remain exported as deprecated aliases so un-migrated screens keep compiling — don't use them in new code, and delete each as its last consumer is migrated.

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

### The summary is a section that loads itself, not part of the job result

The scan job returns the book — cover, title, rating, blurb — in one poll.
The generated summary is a **separate request** (`GET /books/{book_id}/summary`,
`useBookSummary`), and `SummarySection` owns its own loading, error,
unavailable and success states.

The reason is timing: the first summary for a book runs the whole RAG
pipeline on the laptop, embedding every passage locally before generating.
Blocking the result screen on that would hide a book we already have
everything else for, behind a spinner, for up to a minute and a half. So
the screen renders immediately and the summary section fills in.

Three consequences worth keeping:

- **`available: false` is a success, not an error.** It means the book had
  nothing worth summarizing — routine for Romanian editions and small print
  runs. A thrown `ApiError` means the request actually failed. The section
  renders those differently: the first falls back to the publisher's blurb
  with an explanation, the second says so and offers "Try again".
- **The query is not retried automatically** (`retry: false`). The
  expensive failure is a 503 from an unreachable Ollama or Groq, which a
  retry a second later hits again — while each attempt can hold a 90 s
  timeout. An explicit button is cheaper and more honest.
- **The demo result never gets a summary.** `DEMO_ANALYSIS.book_id` is
  `null` and the section is skipped entirely. A fabricated summary with
  fabricated citations would be indistinguishable from a real one during
  testing — the exact thing the "mocks are visibly marked" rule exists to
  prevent, and worse here because citations *look* like verification.

"From the publisher" stays as a heading in every fallback path. A
publisher's blurb is marketing copy; a RAG summary is sourced description.
Letting a reader mistake one for the other is what the label prevents.

### Mocks are visibly marked

A screen rendering data the backend cannot produce yet always shows a `<DemoNote>` or a banner. An unmarked mock is a lie on the screen — indistinguishable from a real result during testing.

**As of Module 6b there are no feature mocks left.** `DEMO_RECOMMENDATIONS_DATA` and `src/mocks/biblioteca.ts` are deleted; what remains under `src/mocks/` is `DEMO_ANALYSIS`, the scan screen's offline fallback, switched in `src/features/scan/mapper.ts`.

The rule that got us here, kept for the next time it applies: switching to the real API happens **in a single place per domain**, and the flag is **per feature, not per file**. When Module 6a made history, stats and preferences real while recommendations stayed fake, a single `DEMO_DATA` covering the whole module would have marked real data as demo *and* fake data as real, depending on which screen read it. Narrow the flag as each feature lands, then delete it.

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
│       ├── book/[bookId].tsx       # one book: status, rating, reading journal
│       ├── (tabs)/
│       │   ├── _layout.tsx         # tab bar
│       │   ├── index.tsx           # Home — cover capture
│       │   ├── recomandari.tsx     # real suggestions + explanations (Module 6b)
│       │   └── profil.tsx          # real identity, counters, preferences, history
│       └── scan/
│           ├── camera.tsx          # capture, full-screen
│           └── [jobId].tsx         # polling + result
├── src/
│   ├── api/
│   │   ├── client.ts               # axios + single-flight refresh
│   │   ├── tokenStore.ts           # SecureStore + in-memory mirror
│   │   ├── errors.ts               # ApiError, normalizes the two shapes of `detail`
│   │   └── endpoints/              # auth, users, books, jobs, library
│   ├── components/
│   │   ├── ui/                     # Button, Input, Screen, Card, Chip, BannerEroare, NotaDemo
│   │   └── book/                   # CardCarte, RatingStele, BookCover, SummarySection,
│   │                               #   ReadingStatusPicker, RatingInput, JournalTimeline,
│   │                               #   RecommendationCard
│   ├── config/env.ts               # API_URL inferred from the Metro host
│   ├── features/
│   │   ├── auth/schema.ts          # zod validation
│   │   ├── scan/                   # hooks (upload + polling), result mapper
│   │   └── library/hooks.ts        # library, journal, stats, preferences, recommendations — all live
│   ├── lib/                        # imagine.ts, queryClient.ts
│   ├── mocks/                      # analiza.ts only — the scan screen's offline fallback
│   ├── store/authStore.ts
│   ├── theme/                      # colors, typography, spacing, fonts
│   └── types/                      # api.ts (mirrors Pydantic), biblioteca.ts (library + Module 6b)
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
- [x] **Module 5: RAG** — real summary with citations. `src/components/book/SummarySection.tsx` owns the whole section: it fetches itself via `useBookSummary` (`GET /books/{book_id}/summary`), renders the claims as flowing prose where **each sentence is tappable** and highlights the numbered source card(s) it came from, and keeps the publisher's `description` under its "From the publisher" heading as the fallback. `AnalysisResult` lost `summary` and `reviews`; `BookSummary`, `SummaryClaim` and the extended `SourceReview` replace them in `src/types/api.ts`.
      *Done when:* `npx tsc --noEmit` clean and `npx expo export` bundles. **Done.**
- [x] **Module 6a: Personal library + reading journal** — real history, counters, derived preferences, and the screens that produce them. `src/types/biblioteca.ts` mirrors the real backend schemas; `src/api/endpoints/library.ts` and the rewritten `src/features/library/hooks.ts` replace the mocks. `ReadingStatusPicker` sits on the scan result; the new `app/(app)/book/[bookId].tsx` carries `RatingInput` and `JournalTimeline`; `app/(app)/(tabs)/profil.tsx` runs entirely on real data with every row tappable.
      *Done when:* `npx tsc --noEmit` clean and `npx expo export` bundles. **Done.**
      **The card type was renamed `BookSummary` → `LibraryBook`.** `BookSummary` in `types/api.ts` is the *generated RAG summary* — a completely different thing that renders on the same screen. Two types sharing one name there is a mistake waiting to be made.
      **`DEMO_DATA` is gone, replaced by `DEMO_RECOMMENDATIONS_DATA`.** Recommendations are the only thing still on mocks (backend Module 6b), and one flag spanning a half-real screen would mislabel both halves.
      **Capture and reflection are separate screens, and that was a correction made on use.** The scan result used to ask "What did you think?" seconds after the shutter — a question with no honest answer yet. It now offers only the reading status. Rating and journal moved to the book screen, where there is something to say.
      **There is no Save button on the status or rating controls.** Every tap fires a `PUT` carrying *only* the field it changed, which is the shape the backend's partial update expects. A Save button would mean holding several fields in local state and shipping them together, the one request shape that *can* clobber. Tapping the active status or the current rating clears it — both the undo affordance and the reason the backend distinguishes an explicit `null` from an absent field.
      **`RatingInput` is deliberately not `RatingStele`.** One is the catalog's average from thousands of strangers, the other is the reader's own score, and both render on the book screen. Drawing them as the same control would make the two indistinguishable.
      **The journal composer clears its draft only *after* the note is stored.** Clearing on submit loses what the reader wrote if the request fails — and a journal note is the one thing in this app the user cannot re-derive.
      **The book screen renders from the library entry, not a book endpoint.** `LibraryEntry.book` already carries cover, title, author and categories, so it is one request — and "not in your library" becomes structural rather than an extra check.
      **`useLibraryEntry` maps a 404 to `null`, not to an error.** "This book isn't in your library yet" is the ordinary starting state of every book; surfacing it as an `ApiError` would put an error banner over a perfectly healthy screen.
      **The profile never counts the history array.** `books_scanned` comes from `/users/me/stats`, computed over the whole library, because the list is paginated and a page length is not a number of books.
      **Every mutation invalidates by the `'library'` prefix**, which catches the lists (each status filter separately), the stats and the derived preferences in one call — otherwise the header drifts out of sync with the history under it, which is the exact inconsistency this module removed.
- [x] **Module 6b: Recommendations** — real suggestions with computed explanations. `src/mocks/biblioteca.ts` is **deleted**, `DEMO_RECOMMENDATIONS_DATA` is gone, and `useRecommendations` now calls `GET /users/me/recommendations`. `Recommendation` in `src/types/biblioteca.ts` matches the real schema; the new `RecommendationList` envelope carries `based_on`. The tab screen is rewritten around the new `src/components/book/RecommendationCard.tsx`.
      *Done when:* `npx tsc --noEmit` clean and `npx expo export` bundles. **Done.**
      **There are no mocks left in this app.** `src/mocks/analiza.ts` still holds `DEMO_ANALYSIS`, but that is the scan screen's offline fallback, not a stand-in for a missing backend.
      **`based_on` is why there are two empty states, not one.** `based_on === 0` means there is nothing to build a profile from — the screen asks for a rating and points at the profile. `based_on > 0` with an empty list means the profile exists and the catalogs had nothing past the reader's own library — "check back", with a retry. A single "no recommendations" would ask a reader who has rated forty books to please rate a book.
      **A 503 renders as an error with a retry, never as an empty shelf.** The first fetch after a taste change reaches the catalogs and runs the local embedding model, so it can genuinely fail — and one of those outcomes is a fact about the reader's library while the other is a fact about the laptop. Same distinction, and same `retry: false` reasoning, as `SummarySection`.
      **The match score is never rendered.** `score` orders the list and stops there. "73% match" would dress a cosine distance up as a measurement of taste; the explanation is the honest form of the same information, because it names a book the reader actually rated.
      **The recommendation card does not open the book screen.** A recommended book is by construction *not* in the library — that is the filter the backend applies before ranking — and the book screen renders from the library entry, so tapping through would land on "not in your library" every time. One offer, one action.
      **The card reads its shelved state from its own mutation, not from `useLibraryEntry`.** Querying per card would be a dozen requests whose answer is known in advance to be "no". And after "Want to read" the card *leaves the list*, because the mutation invalidates the `'library'` prefix and the book is now excluded from the refetched suggestions — so the feedback has to come from the mutation that is still in hand.
      **`libraryKeys.recommendations()` lives under the `'library'` prefix deliberately.** A rating is a direct input to the suggestion list, exactly as it is to the stats and the derived preferences. Leaving recommendations outside the prefix would let a suggestion sit there explained by "because you liked X" while X has just been re-rated 1.
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
- **No literal colors or spacing** in components. Everything from `src/theme` — see the `design-system` skill.
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
