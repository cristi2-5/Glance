/**
 * Hooks for the personal library, profile stats, preferences and recommendations.
 *
 * The library half is **live** as of backend Module 6a. Recommendations
 * are still demo data until Module 6b, which is why `DEMO_DATA` no longer
 * covers the whole file — a single flag over a screen that is now half
 * real and half fake would mislabel both halves.
 *
 * ## Why every mutation invalidates three keys
 *
 * Rating a book changes the entry, the counters on the profile
 * (`ratings_given`, `average_rating`), *and* the derived preferences —
 * those are computed backend-side from books rated 4+, so a rating is
 * exactly the input they read. Invalidating only the list would leave the
 * profile header contradicting the history rendered under it, which is
 * the specific inconsistency this module exists to remove.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/errors'
import {
  addJournalEntry,
  editJournalEntry,
  getLibraryEntry,
  getLibraryPreferences,
  getLibraryStats,
  listJournal,
  listLibrary,
  removeJournalEntry,
  removeLibraryEntry,
  updateLibraryEntry,
} from '@/api/endpoints/library'
import { DEMO_RECOMMENDATIONS } from '@/mocks/biblioteca'
import type {
  JournalEntry,
  LibraryEntry,
  LibraryEntryUpdate,
  LibraryPreferences,
  LibraryStats,
  Recommendation,
  ReadingStatus,
} from '@/types/biblioteca'

/**
 * `true` while **recommendations** still come from `src/mocks/`.
 *
 * Scoped to that one feature now — history, stats and preferences are
 * real. Screens use it to show a visible marker, because an unmarked mock
 * is a lie on screen.
 */
export const DEMO_RECOMMENDATIONS_DATA = true

/** Simulates network latency, so loading states stay visible in development. */
const MOCK_DELAY_MS = 400

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(value)
    }, MOCK_DELAY_MS)
  })
}

/** Every query key the library touches, in one place. */
export const libraryKeys = {
  list: (status?: ReadingStatus) => ['library', status ?? 'all'] as const,
  entry: (bookId: number | null) => ['library', 'entry', bookId] as const,
  journal: (bookId: number | null) => ['library', 'journal', bookId] as const,
  stats: () => ['library', 'stats'] as const,
  preferences: () => ['library', 'preferences'] as const,
} as const

/**
 * What the user has recorded about one book, or `null` if nothing yet.
 *
 * **A 404 is mapped to `null`, not surfaced as an error.** "This book
 * isn't in your library" is the ordinary starting state of every book,
 * and letting it reach the UI as an `ApiError` would put an error banner
 * over a perfectly healthy screen. A real failure (offline, 500) still
 * throws.
 *
 * Args:
 *   bookId: The book to look up, or `null` when the scan matched no
 *     catalog entry — there is no book to record against.
 */
export function useLibraryEntry(bookId: number | null) {
  return useQuery<LibraryEntry | null, ApiError>({
    queryKey: libraryKeys.entry(bookId),
    enabled: bookId !== null,
    async queryFn() {
      if (bookId === null) {
        return null
      }
      try {
        return await getLibraryEntry(bookId)
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          return null
        }
        throw error
      }
    },
  })
}

/**
 * The current user's library, most recently touched first.
 *
 * Args:
 *   status: Restrict to one reading status, or omit for everything.
 */
export function useLibrary(status?: ReadingStatus) {
  return useQuery<LibraryEntry[], ApiError>({
    queryKey: libraryKeys.list(status),
    queryFn: () => listLibrary(status ? { status } : undefined),
  })
}

/** Profile counters, computed backend-side over the whole library. */
export function useLibraryStats() {
  return useQuery<LibraryStats, ApiError>({
    queryKey: libraryKeys.stats(),
    queryFn: getLibraryStats,
  })
}

/** Favorite genres and authors, derived from books rated 4+. */
export function usePreferences() {
  return useQuery<LibraryPreferences, ApiError>({
    queryKey: libraryKeys.preferences(),
    queryFn: getLibraryPreferences,
  })
}

/**
 * Sets a book's reading status, rating or note.
 *
 * Pass only the fields being changed: the backend leaves absent fields
 * alone and treats an explicit `null` as a clear, so spreading a whole
 * entry in would rewrite what the user didn't touch.
 */
export function useUpdateLibraryEntry() {
  const queryClient = useQueryClient()

  return useMutation<LibraryEntry, ApiError, { bookId: number; update: LibraryEntryUpdate }>({
    mutationFn: ({ bookId, update }) => updateLibraryEntry(bookId, update),
    onSuccess: () => {
      void invalidateLibrary(queryClient)
    },
  })
}

/** Removes a book from the library. The cached book itself is untouched. */
export function useRemoveLibraryEntry() {
  const queryClient = useQueryClient()

  return useMutation<void, ApiError, number>({
    mutationFn: (bookId) => removeLibraryEntry(bookId),
    onSuccess: () => {
      void invalidateLibrary(queryClient)
    },
  })
}

/**
 * Marks every library-derived query stale after a write.
 *
 * Invalidated by **prefix**, on purpose. Every key in `libraryKeys`
 * starts with `'library'`, so this catches the lists (including each
 * status filter — marking a book read moves it between two cached lists
 * at once), the stats and the derived preferences in one call. A future
 * mutation therefore cannot forget one of them and leave the profile
 * header contradicting the history under it.
 */
async function invalidateLibrary(
  queryClient: ReturnType<typeof useQueryClient>,
): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ['library'] })
}

/**
 * Personalized recommendations, sorted by score descending.
 *
 * **Still demo data** — backend Module 6b. Screens rendering this must
 * show the demo marker; see `DEMO_RECOMMENDATIONS_DATA`.
 */
export function useRecommendations() {
  return useQuery<Recommendation[]>({
    queryKey: ['recommendations'],
    queryFn: () => delay(DEMO_RECOMMENDATIONS),
  })
}

/**
 * A book's journal for the current reader, oldest note first.
 *
 * A 404 becomes an empty list for the same reason `useLibraryEntry` maps
 * one to `null`: "no journal yet" is the starting state of every book,
 * not a failure worth an error banner.
 *
 * Args:
 *   bookId: The book whose journal to read.
 */
export function useJournal(bookId: number | null) {
  return useQuery<JournalEntry[], ApiError>({
    queryKey: libraryKeys.journal(bookId),
    enabled: bookId !== null,
    async queryFn() {
      if (bookId === null) {
        return []
      }
      try {
        return await listJournal(bookId)
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          return []
        }
        throw error
      }
    },
  })
}

/** Writes a new note into a book's journal. */
export function useAddJournalEntry() {
  const queryClient = useQueryClient()

  return useMutation<JournalEntry, ApiError, { bookId: number; content: string }>({
    mutationFn: ({ bookId, content }) => addJournalEntry(bookId, content),
    onSuccess: () => {
      void invalidateLibrary(queryClient)
    },
  })
}

/** Rewrites one note. `created_at` never moves, so the timeline holds. */
export function useEditJournalEntry() {
  const queryClient = useQueryClient()

  return useMutation<
    JournalEntry,
    ApiError,
    { bookId: number; entryId: number; content: string }
  >({
    mutationFn: ({ bookId, entryId, content }) => editJournalEntry(bookId, entryId, content),
    onSuccess: () => {
      void invalidateLibrary(queryClient)
    },
  })
}

/** Removes one note from a book's journal. */
export function useRemoveJournalEntry() {
  const queryClient = useQueryClient()

  return useMutation<void, ApiError, { bookId: number; entryId: number }>({
    mutationFn: ({ bookId, entryId }) => removeJournalEntry(bookId, entryId),
    onSuccess: () => {
      void invalidateLibrary(queryClient)
    },
  })
}
