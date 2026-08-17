/**
 * Hooks for the personal library, profile stats, preferences and recommendations.
 *
 * **Everything here is live** as of backend Module 6b. The last mock in
 * this feature — `DEMO_RECOMMENDATIONS` — is gone, and with it the
 * `DEMO_RECOMMENDATIONS_DATA` flag and the `src/mocks/biblioteca.ts` file
 * it read from.
 *
 * ## Why every mutation invalidates by prefix
 *
 * Rating a book changes the entry, the counters on the profile
 * (`ratings_given`, `average_rating`), the derived preferences, **and the
 * recommendations** — all four are computed backend-side from the same
 * rows, and a rating is the direct input to every one of them. A 5 stars
 * on a book whose genre the reader had never rated changes the entire
 * suggestion list on the next fetch.
 *
 * Invalidating only the list would leave the profile header contradicting
 * the history under it; invalidating everything but the recommendations
 * would leave a suggestion visibly explained by "because you liked X"
 * pointing at a book the reader has just re-rated 1.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/errors'
import {
  addJournalEntry,
  editJournalEntry,
  getLibraryEntry,
  getLibraryPreferences,
  getLibraryStats,
  getRecommendations,
  listJournal,
  listLibrary,
  removeJournalEntry,
  removeLibraryEntry,
  updateLibraryEntry,
} from '@/api/endpoints/library'
import type {
  JournalEntry,
  LibraryEntry,
  LibraryEntryUpdate,
  LibraryPreferences,
  LibraryStats,
  ReadingStatus,
  RecommendationList,
} from '@/types/biblioteca'

/**
 * Every query key the library touches, in one place.
 *
 * All of them start with `'library'`, including `recommendations` —
 * that shared prefix is what makes `invalidateLibrary` complete by
 * construction rather than by remembering to list each key.
 */
export const libraryKeys = {
  list: (status?: ReadingStatus) => ['library', status ?? 'all'] as const,
  entry: (bookId: number | null) => ['library', 'entry', bookId] as const,
  journal: (bookId: number | null) => ['library', 'journal', bookId] as const,
  stats: () => ['library', 'stats'] as const,
  preferences: () => ['library', 'preferences'] as const,
  recommendations: () => ['library', 'recommendations'] as const,
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
 * at once), the stats, the derived preferences and the recommendations in
 * one call. A future mutation therefore cannot forget one of them and
 * leave the profile header contradicting the history under it.
 */
async function invalidateLibrary(
  queryClient: ReturnType<typeof useQueryClient>,
): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ['library'] })
}

/**
 * Personalized recommendations, most similar first.
 *
 * **The query is not retried automatically.** The expensive failure is a
 * 503 from an unreachable local embedding model, which a retry a second
 * later hits again — while the first call after a taste change can hold
 * the request for seconds doing catalog lookups and embedding. An explicit
 * "Try again" is cheaper and more honest, exactly as on the RAG summary.
 *
 * An empty result is a success, not an error. Read `based_on` to tell the
 * two empty cases apart — see `RecommendationList`.
 */
export function useRecommendations() {
  return useQuery<RecommendationList, ApiError>({
    queryKey: libraryKeys.recommendations(),
    queryFn: getRecommendations,
    retry: false,
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
