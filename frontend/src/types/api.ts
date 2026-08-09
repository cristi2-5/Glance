/**
 * Tipuri care oglindesc exact schemele Pydantic ale backendului.
 *
 * Sursa de adevăr e schema OpenAPI expusă de FastAPI la `/openapi.json`.
 * Când backendul se schimbă, se actualizează întâi acest fișier — restul
 * clientului compilează pe baza lui, deci nepotrivirile ies la typecheck,
 * nu la runtime pe telefon.
 */

/** `app/schemas/user.py` — `UserPublic`. */
export interface UtilizatorPublic {
  id: number
  email: string
  is_active: boolean
  /** ISO 8601. */
  created_at: string
}

/** `app/schemas/auth.py` — `TokenResponse`. */
export interface RaspunsTokenuri {
  access_token: string
  refresh_token: string
  token_type: string
}

/** Corpul pentru `POST /auth/register` și `POST /auth/login`. */
export interface CredentialeRequest {
  email: string
  /** Minim 8 caractere — constrângere impusă și de backend. */
  password: string
}

/** Corpul pentru `POST /auth/refresh` și `POST /auth/logout`. */
export interface RefreshRequest {
  refresh_token: string
}

/** `app/schemas/job.py` — `JobCreated`, răspunsul la `POST /books/analyze-cover`. */
export interface JobCreat {
  job_id: number
}

/**
 * Stările unui job, din `JobStatus` (`app/models/job.py`).
 */
export type StatusJob = 'pending' | 'running' | 'done' | 'failed'

/**
 * `app/schemas/job.py` — `JobPublic`, răspunsul la `GET /jobs/{id}`.
 *
 * Atenție la asimetria de denumire față de `JobCreat`: la creare câmpul e
 * `job_id`, la citire e `id`. E o particularitate a backendului, aplatizată
 * în hook-urile din `src/features/scan/`.
 */
export interface JobPublic {
  id: number
  status: StatusJob
  /** Populat doar când `status === 'done'`. Forma depinde de modulul de backend. */
  result: Record<string, unknown> | null
  /** Populat doar când `status === 'failed'`. */
  error: string | null
  created_at: string
  updated_at: string
}

/**
 * Rezultatul analizei unei coperți.
 *
 * Deocamdată backendul (Modulul 2) întoarce un placeholder; Modulele 3-5 vor
 * popula câmpurile reale. Tipul e definit acum, iar mock-urile îl respectă,
 * ca ecranele să nu se schimbe când sosesc datele adevărate.
 */
export interface RezultatAnaliza {
  titlu: string
  autor: string | null
  /** Încrederea recunoașterii, 0-1. Sub un prag, oferim corecție manuală. */
  incredere: number
  rezumat: string | null
  coperta_url: string | null
  categorii: string[]
  rating_mediu: number | null
  recenzii: RecenzieSursa[]
}

/** O opinie critică atribuită unei surse, pentru trasabilitate. */
export interface RecenzieSursa {
  id: string
  sursa: 'wikipedia' | 'open_library' | 'google_books'
  titlu_sursa: string
  extras: string
  url: string | null
}
