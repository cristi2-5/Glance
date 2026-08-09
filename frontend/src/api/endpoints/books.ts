/** Trimiterea coperții spre analiză. */

import { apiClient } from '@/api/client'
import { TIMEOUT_UPLOAD_MS } from '@/config/env'
import type { JobCreat } from '@/types/api'

/**
 * Descrierea unui fișier local, în forma pe care o acceptă `FormData` în
 * React Native. Nu e un `Blob` real, dar `fetch`/`XMLHttpRequest` din RN
 * știu să-l trateze; de aici casturile de mai jos.
 */
interface FisierLocalRN {
  uri: string
  name: string
  type: string
}

/**
 * Trimite fotografia coperții și pornește analiza asincronă.
 *
 * Imaginea trebuie să fie deja redimensionată — vezi
 * `pregatesteCopertaPentruUpload` din `src/lib/imagine.ts`.
 *
 * Args:
 *   uri: URI-ul local al imaginii JPEG pregătite.
 *
 * Returns:
 *   Id-ul job-ului creat, de urmărit prin `GET /jobs/{id}`.
 *
 * Raises:
 *   ApiError: 413 dacă imaginea depășește 8 MB, 415 dacă tipul nu e acceptat,
 *     401 dacă sesiunea a expirat.
 */
export async function analizeazaCoperta(uri: string): Promise<JobCreat> {
  const fisier: FisierLocalRN = {
    uri,
    name: 'coperta.jpg',
    type: 'image/jpeg',
  }

  const formular = new FormData()
  // Numele câmpului trebuie să fie exact `file` — așa e declarat parametrul
  // `UploadFile` în `backend/app/api/routes/books.py`.
  formular.append('file', fisier as unknown as Blob)

  const { data } = await apiClient.post<JobCreat>('/books/analyze-cover', formular, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: TIMEOUT_UPLOAD_MS,
  })

  return data
}
