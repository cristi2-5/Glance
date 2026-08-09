/** Sending the cover for analysis. */

import { apiClient } from '@/api/client'
import { UPLOAD_TIMEOUT_MS } from '@/config/env'
import type { JobCreated } from '@/types/api'

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
