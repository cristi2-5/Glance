/**
 * Types for reading history, preferences and recommendations.
 *
 * The backend doesn't expose these resources yet — they arrive in
 * **Module 6** (`ReadingHistory`, `Preference`, content-based recommendations).
 * We define them now so the screens can be written against a stable
 * contract; the mocks in `src/mocks/` respect these shapes exactly.
 *
 * Once Module 6 is ready, compare against the real schema and adjust
 * *here*, once — the screens follow through typecheck.
 */

/** A book, as it appears in lists and cards. */
export interface BookSummary {
  id: string
  title: string
  author: string
  cover_url: string | null
  categories: string[]
  average_rating: number | null
}

/** An entry in the user's reading history. */
export interface HistoryEntry {
  id: string
  book: BookSummary
  /** The user's rating, 1-5. `null` if not rated yet. */
  user_rating: number | null
  /** ISO 8601 — when it was scanned or marked as read. */
  read_at: string
}

/**
 * A recommendation, with the explanation that justifies it.
 *
 * The explanation isn't cosmetic: Module 6 is content-based, so each
 * recommendation *can* be justified through the books that generated it.
 */
export interface Recommendation {
  id: string
  book: BookSummary
  /** Match score, 0-1. */
  score: number
  /** Text of the form "because you liked X". */
  explanation: string
}

/** The user's declared preferences. */
export interface UserPreferences {
  favorite_genres: string[]
  favorite_authors: string[]
}
