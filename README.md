# Glance

You're standing in a bookshop holding a book you've never heard of. The blurb on the back is marketing copy, the cover tells you nothing, and your phone can give you a hundred star ratings but no actual answer to the only question you have: *is this worth my time?*

Glance is the app for that moment. Photograph the cover. It works out which book you're holding, goes and reads what's actually been written about it, and gives you a short summary where **every single sentence tells you where it came from** — tap one and you see the exact passage it was drawn from. Nothing is invented, and you never have to take its word for anything.

It also remembers what you've read. Over time, that turns into recommendations that can explain themselves: not "87% match", but *"because you liked Dune"*.

## What you can do

**Scan a cover.** Point the camera at a book. A few seconds later you have the title, the author, the cover art, the genres and the average rating — pulled from public catalogs, not typed in by you.

**Fix it when it's wrong.** Odd typography, a worn spine, a translated edition nobody has catalogued — sometimes the read is wrong. Correct the title and the app throws away everything it fetched for the wrong book and starts again, rather than leaving the previous book's cover and blurb sitting under your corrected title.

**Read a summary you can check.** Not the publisher's blurb — a summary built from Wikipedia's plot and critical-reception sections, catalog descriptions and subject listings. Every sentence is tappable, and tapping highlights the passage it was built from. The publisher's blurb is still there, but clearly labelled as such, because marketing copy and sourced description are different things.

**Keep a library.** Every scan lands on your shelf, initially marked only as *scanned* — the app doesn't presume you meant anything by pointing a camera at a book. From there you say what it actually is: want to read, reading, or read. Add a rating out of five whenever you have one. Scanning the same book twice doesn't create a second copy of it.

**Keep a reading journal.** Dated notes on any book, shown oldest first, so the doubt you had in chapter three stays above the conclusion you reached at the end. That's the whole point of a journal, and reversing the order would destroy it.

**See your taste described back to you.** Your favourite genres and authors are worked out from the books you actually rated highly — never a list you had to declare at signup and then watch go stale.

**Get recommendations with a reason.** Ranked suggestions, each one naming the book of yours it came from, with "Want to read" writing straight into your library.

## How it works

### Reading the cover

A vision model looks at every cover first and is asked for the title and author as structured data, rather than a sentence someone has to parse afterwards. Its guess is then checked against the book catalogs. If they confirm it, that's the answer.

If they don't — or the model fails — an on-device OCR pass reads the printed text off the cover instead and that text is fuzzy-matched against the catalogs. Whichever of the two readings the catalogs are more confident about wins. OCR is genuinely better than a vision model at proper nouns on clean printed type; the vision model is better at everything else, which is why both exist and neither is trusted blindly.

One detail worth knowing, because it produces visibly wrong results otherwise: **the author is the name on the cover, not the catalog's contributor list.** Google Books returns translators, illustrators and editors mixed in unlabelled, which for one Romanian edition of Jules Verne meant four names where the book credits one. When the scanned name appears in that list, it wins — the cover is the better authority, and the camera already read it.

Because the whole pipeline takes half a minute or more, a scan doesn't block. It returns a job id immediately and the app polls until it's done.

### The summary, and why every sentence has a source

Three official sources are used, and nothing is scraped:

| Source | What it contributes |
|---|---|
| **Google Books** | description, genres, ISBN, average rating |
| **Open Library** | subjects, descriptions, editions — CC0 |
| **Wikipedia** | plot summary and the *Reception* section — the actual critical opinion |

Wikipedia is the important one. Review sites were investigated and every single candidate disallows its review pages in `robots.txt`, so scraping them was never an option. That turned out to be a better outcome anyway: professional criticism with citations is far stronger input for a summary than a wall of user star-ratings and one-liners. Wikipedia is searched in both English and Romanian, because a Romanian edition scanned under its Romanian title simply doesn't exist on the English site.

Everything gathered is split into passages of a few hundred words each, cut on sentence boundaries, and turned into vectors **on your own machine**. When you ask for a summary, the passages closest to what's being asked about are retrieved and handed to a language model — along with a hard instruction to say nothing the passages don't support.

That instruction is not what makes it trustworthy, though. Asking a model not to make things up is a request, and the failures it doesn't catch are exactly the fluent, plausible ones. So the model doesn't return prose. It returns a **list of claims, each tagged with the passages it used**, and the summary you read is those claims joined together. Every claim is then checked against the passages that were actually retrieved: any claim citing nothing, or citing something that wasn't in front of the model, is **deleted before it reaches your screen**. If nothing survives that check, the app says so plainly and shows you the publisher's blurb instead.

There's an invariant underneath all of this that matters more than it sounds: **retrieval can only ever see one book's passages.** A summary of *Dune* built partly from *Foundation*'s reception would read beautifully, carry real citations, and be completely wrong — and nothing downstream could possibly detect it, least of all the model, which has no way to know a passage it was handed belongs to a different book. So the book filter isn't a convention anyone could forget: it's a required argument, the filter is built inside the storage layer where callers can't reach it, and every retrieved passage is re-checked before use.

### How recommendations are built

Books you rated 4 or 5 are averaged into a single vector — a rough numerical description of what you like — weighted so a 5 pulls harder than a 4.

Candidates can't come from books you've already scanned; that's precisely the set that has to be excluded. So the app goes back out to the catalogs and asks them open questions, seeded by your own derived genres and authors, then embeds what comes back and ranks it against your profile.

Books you rated 1 or 2 don't get subtracted from that vector. Subtracting produces a direction that corresponds to nothing you ever said, and therefore a suggestion nobody can explain. Instead they build a list of authors and genres to avoid — and **a like always beats a dislike**, so an author on both lists survives. Loving one of their books and disliking another is a fact about those two books, not a verdict on the author.

A 3 counts as neither. If it did, one book you merely finished would wipe out an entire genre.

Anything already in your library is filtered out before ranking, so a suggestion is always something new.

Finally, the reason. **The explanation is computed, not written by a model** — it names the book of yours that the suggestion is closest to. A model asked to justify a recommendation will happily produce a fluent paragraph about a book it was never shown, and a wrong reason next to a right recommendation is worse than no reason at all, because the reason is the part you'd actually trust.

The match score exists, and it orders the list, but it's never shown to you. "73% match" dresses a cosine distance up as a measurement of taste. Naming a book you really rated is the honest version of the same information.

## Where your data lives

Everything about *you* stays on the machine running the backend:

- your account, library, ratings and journal notes — a local SQLite file
- the vector store — local
- the embedding model that turns text into vectors — local, via Ollama

Two steps run in the cloud, on Groq: reading the cover, and writing the summary. This started out fully local and was changed deliberately — locally-sized models misread titles too often to be trusted and were slow enough to make the app unpleasant to use.

That path is still there and still maintained. Setting `AI_PROVIDER=ollama` moves both steps back onto your own machine, using Moondream and Llama 3.2, with no code change. Nothing else is sent anywhere: no other AI services, no analytics, no scraping.

## Built with

**Backend** — [FastAPI](https://fastapi.tiangolo.com) with async SQLAlchemy over SQLite, [ChromaDB](https://www.trychroma.com) for vectors, [RapidOCR](https://github.com/RapidAI/RapidOCR) on ONNX Runtime for on-device text reading (no PyTorch anywhere), [Ollama](https://ollama.com) for local embeddings, and [Groq](https://groq.com) for hosted inference. Fully type-checked with `mypy --strict`, structured logging via structlog.

**Client** — [React Native](https://reactnative.dev) and [Expo](https://expo.dev) with strict TypeScript, expo-router for navigation, TanStack Query for server state, Zustand for session state, and tokens stored in the device Keychain/Keystore.

**Models** — `qwen/qwen3.6-27b` reads covers, `openai/gpt-oss-120b` writes summaries, `nomic-embed-text` produces every embedding locally.

## Running it

You'll need Python 3.11, [Ollama](https://ollama.com), and a [Groq](https://groq.com) API key.

```bash
ollama pull nomic-embed-text
```

**Backend:**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # fill in JWT_SECRET_KEY and GROQ_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0
```

`--host 0.0.0.0` matters — without it the server only listens on localhost and your phone can't reach it.

**App:**

```bash
cd frontend
npm install
npx expo start
```

Scan the QR code with Expo Go, with the phone on the same Wi-Fi network. The app works out the backend's address from the development server on its own, so there's nothing to configure when you move between networks.

Want it running entirely on your own hardware? Set `AI_PROVIDER=ollama` in `backend/.env`, then `ollama pull moondream && ollama pull llama3.2`.
