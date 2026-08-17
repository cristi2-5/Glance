/** Calls for the personal library: history, status, ratings, stats, and the journal. */

import { apiClient } from '@/api/client'
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
 * Lists the user's library, most recently touched first.
 *
 * Args:
 *   status: Restrict to one reading status, or omit for everything.
 *   limit: Page size.
 *   offset: How many entries to skip.
 *
 * Returns:
 *   The matching entries. An empty array is the normal state of a new
 *   account — the caller renders an empty state, not an error.
 */
export async function listLibrary(options?: {
  status?: ReadingStatus
  limit?: number
  offset?: number
}): Promise<LibraryEntry[]> {
  const { data } = await apiClient.get<LibraryEntry[]>('/users/me/library', {
    params: {
      ...(options?.status ? { status: options.status } : {}),
      ...(options?.limit !== undefined ? { limit: options.limit } : {}),
      ...(options?.offset !== undefined ? { offset: options.offset } : {}),
    },
  })
  return data
}

/**
 * Reads the profile counters, computed over the whole library.
 *
 * Deliberately not derived from `listLibrary().length`: that endpoint is
 * paginated, so a page length is not the number of books.
 *
 * Returns:
 *   The counters for the current user.
 */
export async function getLibraryStats(): Promise<LibraryStats> {
  const { data } = await apiClient.get<LibraryStats>('/users/me/stats')
  return data
}

/**
 * Reads favorite genres and authors, derived from books rated 4+.
 *
 * Returns:
 *   The derived preferences. `based_on === 0` means nothing qualifies yet.
 */
export async function getLibraryPreferences(): Promise<LibraryPreferences> {
  const { data } = await apiClient.get<LibraryPreferences>('/users/me/preferences')
  return data
}

/**
 * Reads the books this reader might like next, most similar first.
 *
 * The first call after a rating changes can take a few seconds: the
 * backend asks the catalogs for new candidates and embeds them locally.
 * Subsequent calls rank against an index that is already built.
 *
 * Returns:
 *   The recommendations plus `based_on`. **An empty list is a normal
 *   response, never an error** — read `based_on` to tell "nothing to build
 *   a profile from yet" from "the catalogs had nothing new".
 */
export async function getRecommendations(): Promise<RecommendationList> {
  const { data } = await apiClient.get<RecommendationList>('/users/me/recommendations')
  return data
}

/**
 * Reads what the user has recorded about one book.
 *
 * Args:
 *   bookId: The book to look up.
 *
 * Returns:
 *   The user's entry.
 *
 * Raises:
 *   ApiError: 404 if this book isn't in the user's library.
 */
export async function getLibraryEntry(bookId: number): Promise<LibraryEntry> {
  const { data } = await apiClient.get<LibraryEntry>(`/books/${bookId}/library`)
  return data
}

/**
 * Sets the reading status, rating or note for a book.
 *
 * **Send only the keys being changed.** The backend treats an absent
 * field as "leave alone" and an explicit `null` as "clear", so passing a
 * whole entry object would rewrite fields the user didn't touch.
 *
 * Args:
 *   bookId: The book to record against.
 *   update: The fields to change.
 *
 * Returns:
 *   The updated entry, created first if the user had none.
 *
 * Raises:
 *   ApiError: 404 if no cached book has this id.
 */
export async function updateLibraryEntry(
  bookId: number,
  update: LibraryEntryUpdate,
): Promise<LibraryEntry> {
  const { data } = await apiClient.put<LibraryEntry>(`/books/${bookId}/library`, update)
  return data
}

/**
 * Removes a book from the user's library.
 *
 * The cached book itself survives — it is shared with everyone who scans
 * the same cover, and carries the fetched sources and generated summary.
 *
 * Args:
 *   bookId: The book to remove from this library.
 *
 * Raises:
 *   ApiError: 404 if this book isn't in the user's library.
 */
export async function removeLibraryEntry(bookId: number): Promise<void> {
  await apiClient.delete(`/books/${bookId}/library`)
}

/**
 * Reads a book's journal for the current reader, oldest note first.
 *
 * Chronological because this is a journal: its value is watching an
 * opinion move between chapter three and the last page.
 *
 * Args:
 *   bookId: The book whose journal to read.
 *
 * Returns:
 *   The notes. An empty array is the normal state of a new book.
 *
 * Raises:
 *   ApiError: 404 if this book isn't in the reader's library.
 */
export async function listJournal(bookId: number): Promise<JournalEntry[]> {
  const { data } = await apiClient.get<JournalEntry[]>(`/books/${bookId}/journal`)
  return data
}

/**
 * Writes a new note into a book's journal.
 *
 * Args:
 *   bookId: The book being written about.
 *   content: The note. Blank text is rejected by the backend.
 *
 * Returns:
 *   The created note.
 *
 * Raises:
 *   ApiError: 404 if no cached book has this id, 422 if the note is blank.
 */
export async function addJournalEntry(bookId: number, content: string): Promise<JournalEntry> {
  const { data } = await apiClient.post<JournalEntry>(`/books/${bookId}/journal`, { content })
  return data
}

/**
 * Rewrites one note. Only its `updated_at` moves, never `created_at`.
 *
 * Args:
 *   bookId: The book the note belongs to.
 *   entryId: The note to rewrite.
 *   content: The replacement text.
 *
 * Returns:
 *   The updated note.
 *
 * Raises:
 *   ApiError: 404 if the note isn't this reader's, 422 if blank.
 */
export async function editJournalEntry(
  bookId: number,
  entryId: number,
  content: string,
): Promise<JournalEntry> {
  const { data } = await apiClient.patch<JournalEntry>(
    `/books/${bookId}/journal/${entryId}`,
    { content },
  )
  return data
}

/**
 * Removes one note from a book's journal.
 *
 * Args:
 *   bookId: The book the note belongs to.
 *   entryId: The note to remove.
 *
 * Raises:
 *   ApiError: 404 if the note isn't this reader's.
 */
export async function removeJournalEntry(bookId: number, entryId: number): Promise<void> {
  await apiClient.delete(`/books/${bookId}/journal/${entryId}`)
}
