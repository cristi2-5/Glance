/**
 * Types that mirror the backend's Pydantic schemas exactly.
 *
 * The source of truth is the OpenAPI schema exposed by FastAPI at
 * `/openapi.json`. When the backend changes, this file is updated first —
 * the rest of the client compiles against it, so mismatches surface at
 * typecheck time, not at runtime on the phone.
 */

/** `app/schemas/user.py` — `UserPublic`. */
export interface UserPublic {
  id: number
  email: string
  is_active: boolean
  /** ISO 8601. */
  created_at: string
}

/** `app/schemas/auth.py` — `TokenResponse`. */
export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

/** Body for `POST /auth/register` and `POST /auth/login`. */
export interface CredentialsRequest {
  email: string
  /** Minimum 8 characters — also enforced by the backend. */
  password: string
}

/** Body for `POST /auth/refresh` and `POST /auth/logout`. */
export interface RefreshRequest {
  refresh_token: string
}

/** `app/schemas/job.py` — `JobCreated`, the response for `POST /books/analyze-cover`. */
export interface JobCreated {
  job_id: number
}

/**
 * The possible states of a job, from `JobStatus` (`app/models/job.py`).
 */
export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

/**
 * `app/schemas/job.py` — `JobPublic`, the response for `GET /jobs/{id}`.
 *
 * Note the naming asymmetry with `JobCreated`: at creation the field is
 * `job_id`, on read it's `id`. This is a backend quirk, flattened away in
 * the hooks under `src/features/scan/`.
 */
export interface JobPublic {
  id: number
  status: JobStatus
  /** Populated only when `status === 'done'`. Shape depends on the backend module. */
  result: Record<string, unknown> | null
  /** Populated only when `status === 'failed'`. */
  error: string | null
  created_at: string
  updated_at: string
}

/**
 * The result of analyzing a cover.
 *
 * Modules 4-5 still populate `summary`, `cover_url`, `categories`,
 * `average_rating`, and `reviews` as `null`/empty. The type is defined now,
 * and the mocks respect it, so the screens don't change when that data
 * arrives.
 */
export interface AnalysisResult {
  title: string
  author: string | null
  /** Recognition confidence, 0-1. Meaningful only for display; the app
   * never compares it to a threshold itself — see `needs_review`. */
  confidence: number
  /** Which path produced this result. `app/schemas/vision.py` — `CoverIdentification`. */
  method: 'ocr' | 'vision' | 'manual'
  /** `true` when the backend's confidence threshold wasn't met — offer manual correction. */
  needs_review: boolean
  /** `true` once the user has overridden the recognized title/author by hand. */
  corrected: boolean
  summary: string | null
  cover_url: string | null
  categories: string[]
  average_rating: number | null
  reviews: SourceReview[]
}

/** `app/schemas/vision.py` — `CorrectionRequest`, the body for `PATCH /jobs/{id}/correction`. */
export interface CorrectionRequest {
  title: string
  author?: string | null
}

/** A critical opinion attributed to a source, for traceability. */
export interface SourceReview {
  id: string
  source: 'wikipedia' | 'open_library' | 'google_books'
  source_title: string
  excerpt: string
  url: string | null
}
