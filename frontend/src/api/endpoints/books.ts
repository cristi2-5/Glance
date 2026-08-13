/** Sending the cover for analysis, and reading a book's generated summary. */

import { apiClient } from '@/api/client'
import { SUMMARY_TIMEOUT_MS, UPLOAD_TIMEOUT_MS } from '@/config/env'
import type { BookSummary, JobCreated } from '@/types/api'

/**
 * Description of a local file, in the shape `FormData` accepts in
 * React Native. It's not a real `Blob`, but `fetch`/`XMLHttpRequest` in RN
 * know how to handle it; hence the casts below.
 */
interface LocalRNFile {
  uri: string
  name: string
  type: string
}

/**
 * Sends the cover photo and starts the asynchronous analysis.
 *
 * The image must already be resized — see `prepareCoverForUpload` in
 * `src/lib/image.ts`.
 *
 * Args:
 *   uri: The local URI of the prepared JPEG image.
 *
 * Returns:
 *   The id of the created job, to track via `GET /jobs/{id}`.
 *
 * Raises:
 *   ApiError: 413 if the image exceeds 8 MB, 415 if the type isn't accepted,
 *     401 if the session has expired.
 */
export async function analyzeCover(uri: string): Promise<JobCreated> {
  const file: LocalRNFile = {
    uri,
    name: 'cover.jpg',
    type: 'image/jpeg',
  }

  const form = new FormData()
  // The field name must be exactly `file` — that's how the `UploadFile`
  // parameter is declared in `backend/app/api/routes/books.py`.
  form.append('file', file as unknown as Blob)

  const { data } = await apiClient.post<JobCreated>('/books/analyze-cover', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: UPLOAD_TIMEOUT_MS,
  })

  return data
}

/**
 * Fetches the generated summary for a cached book.
 *
 * Gets its own timeout, longer than the client default: the first request
 * for a book runs the whole RAG pipeline (chunk, embed locally, retrieve,
 * generate), and only later ones are served from the backend's cache.
 *
 * Args:
 *   bookId: The cached book id, from `AnalysisResult.book_id`.
 *
 * Returns:
 *   The summary. Check `available` — `false` means the book had no
 *   passages to summarize, which is a successful response, not an error.
 *
 * Raises:
 *   ApiError: 404 if the book isn't cached, 503 if the local embedding
 *     model or the summary provider is unreachable, 401 if the session
 *     has expired.
 */
export async function getBookSummary(bookId: number): Promise<BookSummary> {
  const { data } = await apiClient.get<BookSummary>(`/books/${bookId}/summary`, {
    timeout: SUMMARY_TIMEOUT_MS,
  })

  return data
}
