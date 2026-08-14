/**
 * Types for the personal library, preferences and recommendations.
 *
 * The library half now mirrors the **real** backend schemas
 * (`app/schemas/library.py`, Module 6a) rather than a placeholder
 * contract. Recommendations are still ahead of the backend — they arrive
 * in Module 6b — and are marked as such below.
 *
 * The card type is `LibraryBook`, not `BookSummary`: `BookSummary` in
 * `types/api.ts` is the *generated RAG summary*, a completely different
 * thing, and two types with one name across a codebase that renders both
 * on the same screen is a mistake waiting to be made.
 */

/** The book fields a list needs. `app/schemas/library.py` — `LibraryBook`. */
export interface LibraryBook {
  id: number
  title: string
  author: string | null
  cover_url: string | null
  categories: string[]
  /** The **catalog's** average, not the user's own rating. */
  average_rating: number | null
}

/** Where a book stands for this user. `app/models/library.py` — `ReadingStatus`. */
export type ReadingStatus = 'scanned' | 'want_to_read' | 'reading' | 'read'

/** One book in the user's library. `app/schemas/library.py` — `LibraryEntryRead`. */
export interface LibraryEntry {
  id: number
  book: LibraryBook
  status: ReadingStatus
  /** The **user's own** rating, 1-5, distinct from `book.average_rating`. */
  rating: number | null
  /**
   * How many journal notes exist for this book.
   *
   * The notes themselves are fetched separately — a list of fifty books
   * does not need every word anyone wrote about any of them.
   */
  journal_count: number
  scan_count: number
  /** ISO 8601, or `null` for an entry never created by a scan. */
  first_scanned_at: string | null
  read_at: string | null
  updated_at: string
}

/**
 * A partial change to one entry, for `PUT /books/{id}/library`.
 *
 * **Only send the keys you mean to change.** The backend distinguishes an
 * absent field from an explicit `null`, so setting a status leaves the
 * rating intact, while `{ rating: null }` clears it deliberately.
 * Spreading a whole entry into this would rewrite every field.
 *
 * The reader's own writing is not here — it lives in the journal, as
 * dated notes.
 */
export interface LibraryEntryUpdate {
  status?: ReadingStatus
  rating?: number | null
}

/**
 * One dated note from the reader's journal about a book.
 *
 * `app/schemas/library.py` — `JournalEntryRead`. Notes accumulate rather
 * than overwrite: a book read over two weeks produces several thoughts,
 * and the interesting ones are usually not the last.
 */
export interface JournalEntry {
  id: number
  content: string
  /** ISO 8601 — places the note in the timeline. Never moved by an edit. */
  created_at: string
  /** ISO 8601 — moves on edit, so "edited" is showable without reordering. */
  updated_at: string
}

/** Profile counters. `app/schemas/library.py` — `LibraryStats`. */
export interface LibraryStats {
  books_scanned: number
  books_read: number
  ratings_given: number
  /** How many journal notes the reader has written, across every book. */
  journal_notes: number
  /** The mean of the user's own ratings, `null` before the first one. */
  average_rating: number | null
}

/**
 * Favorite genres and authors, **derived** from the library.
 *
 * Not a form the user fills in. `based_on === 0` means nothing has been
 * rated 4+ yet and the lists are legitimately empty — the screen asks for
 * a rating rather than rendering invented tastes.
 */
export interface LibraryPreferences {
  favorite_genres: string[]
  favorite_authors: string[]
  based_on: number
}

/**
 * A recommendation, with the explanation that justifies it.
 *
 * **Still ahead of the backend** — Module 6b. The explanation isn't
 * cosmetic: recommendations are content-based, so each one is justified
 * through the book in the user's library that generated it.
 */
export interface Recommendation {
  id: string
  book: LibraryBook
  /** Match score, 0-1. */
  score: number
  /** Text of the form "because you liked X". */
  explanation: string
}
