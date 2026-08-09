/** Citirea stării job-urilor asincrone. */

import { apiClient } from '@/api/client'
import type { JobPublic } from '@/types/api'

/**
 * Citește starea curentă a unui job.
 *
 * Args:
 *   jobId: Id-ul întors de `POST /books/analyze-cover`.
 *
 * Returns:
 *   Job-ul, cu `status` și — când e gata — `result` sau `error`.
 *
 * Raises:
 *   ApiError: 404 dacă job-ul nu există, 403 dacă aparține altui utilizator.
 */
export async function getJob(jobId: number): Promise<JobPublic> {
  const { data } = await apiClient.get<JobPublic>(`/jobs/${jobId}`)
  return data
}
