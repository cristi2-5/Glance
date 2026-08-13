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
 * Vision (Module 3) fills the recognition fields; the data fetcher
 * (Module 4) fills the catalog ones.
 *
 * The generated summary is deliberately **not** here. It arrives from
 * `GET /books/{book_id}/summary` (Module 5) instead, because it takes
 * seconds longer than the catalog metadata and the result screen should
 * render the book immediately rather than hold everything back for it.
 * Use `book_id` to fetch it.
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

  /** The cached `books.id` row, or `null` if the fetch stage was skipped. */
  book_id: number | null
  /**
   * `false` when no catalog matched the scanned cover.
   *
   * This is a normal outcome, not an error: `title` and `author` are then
   * the vision reading and every catalog field below is empty. Common for
   * Romanian editions, which Google Books and Open Library cover poorly.
   * The screen says so plainly rather than showing a blank page.
   */
  metadata_found: boolean
  /** The publisher's blurb from the catalog. Not the RAG summary. */
  description: string | null
  cover_url: string | null
  categories: string[]
  average_rating: number | null
  /** How many ratings `average_rating` averages — a 4.5 from 3 people is
   * not a 4.5 from 3,000, and the screen shows the count for that reason. */
  ratings_count: number | null
  /**
   * How many source passages were cached for this book (descriptions,
   * subjects, plot, reception). Module 5 retrieves over exactly these, so
   * a `0` here means the summary request will come back unavailable —
   * useful for skipping the request entirely.
   */
  source_count: number
}

/** `app/schemas/vision.py` — `CorrectionRequest`, the body for `PATCH /jobs/{id}/correction`. */
export interface CorrectionRequest {
  title: string
  author?: string | null
}

/**
 * A cited passage backing the generated summary.
 *
 * `app/schemas/summary.py` — `SourceReview`. The `id` is the chunk id the
 * backend's retrieval produced; `SummaryClaim.chunk_ids` refers to it, and
 * that link is what makes each claim tappable.
 */
export interface SourceReview {
  id: string
  source: 'wikipedia' | 'open_library' | 'google_books'
  source_title: string
  excerpt: string
  url: string | null
  /** The licence the passage is published under, for attribution. */
  license?: string | null
}

/**
 * One statement of the summary, with the passages that support it.
 *
 * `app/schemas/summary.py` — `SummaryClaim`. `chunk_ids` is never empty:
 * the backend drops any claim whose citations don't resolve, so every id
 * here has a matching entry in `BookSummary.reviews`.
 */
export interface SummaryClaim {
  text: string
  chunk_ids: string[]
}

/**
 * `app/schemas/summary.py` — `BookSummary`, the response for
 * `GET /books/{book_id}/summary`.
 *
 * `available: false` is a normal, successful response — it means the book
 * has no passages worth summarizing (routine for Romanian editions and
 * small print runs), and the screen falls back to the publisher's blurb.
 * A *failed* request is a thrown `ApiError` instead. Keeping the two apart
 * is what lets the screen tell an honest gap from a backend problem.
 */
export interface BookSummary {
  book_id: number
  available: boolean
  /** The summary as prose — exactly the claims, joined. Empty when unavailable. */
  text: string
  claims: SummaryClaim[]
  /** The cited passages, in first-citation order. */
  reviews: SourceReview[]
  /** Aspects the sources didn't cover, reported rather than invented. */
  uncovered: string[]
  model: string | null
  /** ISO 8601. */
  generated_at: string | null
}
