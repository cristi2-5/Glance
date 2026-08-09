# Glance

A mobile app + 100% local backend. Photograph a book cover, and the app recognizes the title and author, gathers material about the book from open sources, generates a summary via RAG, and offers personalized recommendations based on reading history.

**No cloud LLM/vision APIs.** All AI (OCR fallback, embeddings, summarization) runs locally through [Ollama](https://ollama.com).

## Architecture

```
Client (mobile app)
      │  POST /books/analyze-cover (image + JWT)  →  202 { job_id }
      │  GET  /jobs/{job_id}                        →  poll until status=done
      ▼
FastAPI backend (local)
      │
      ├── RapidOCR (ONNX)          → raw text from the cover               [fast, <1 s]
      ├── Moondream (via Ollama)   → fallback when OCR is unreliable       [slow, 15-40 s]
      ├── Google Books / Open Library / Wikipedia → metadata + text about the book
      ├── SQLite                   → users, jobs, books, sources, reading_history, preferences
      ├── ChromaDB                 → embeddings, power both RAG and recommendations
      └── Llama 3.2 (via Ollama)   → summary generated from retrieved context
```

## Stack

- **Backend**: FastAPI, SQLAlchemy 2.0 (async) + SQLite, ChromaDB, RapidOCR, Ollama (Llama 3.2, Moondream, nomic-embed-text)
- **Client**: React Native + Expo (SDK 54), TypeScript, expo-router

## Getting started

### Backend

```bash
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 0.0.0.0
```

### Mobile client

Tested on a physical phone via Expo Go (the Android emulator isn't used — too RAM-heavy alongside Ollama).

```bash
cd frontend
npm install
npx expo start
# scan the QR code with Expo Go — phone and laptop must be on the same Wi-Fi network
```

The client infers the backend address from the Metro host automatically; override with `EXPO_PUBLIC_API_URL` in `frontend/.env` only if the backend runs on a different machine.

## Project status

Development proceeds one module at a time, backend and frontend in lockstep. See `CLAUDE.md` and `frontend/CLAUDE.md` for the detailed module checklist and architectural decisions.

- [x] Module 0 — Foundation (backend + client)
- [x] Module 1 — Auth (backend + client)
- [x] Module 2 — API skeleton & jobs / scan skeleton (backend + client)
- [ ] Module 3 — Vision (OCR + Moondream fallback)
- [ ] Module 4 — Data fetcher & cache
- [ ] Module 5 — RAG summary
- [ ] Module 6 — Recommendations

## License

Personal project, no license specified yet.
