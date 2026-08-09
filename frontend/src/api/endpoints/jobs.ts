/** Reading the state of asynchronous jobs. */

import { apiClient } from '@/api/client'
import type { JobPublic } from '@/types/api'

/**
 * Reads the current state of a job.
 *
 * Args:
 *   jobId: The id returned by `POST /books/analyze-cover`.
 *
 * Returns:
 *   The job, with `status` and — once done — `result` or `error`.
 *
 * Raises:
 *   ApiError: 404 if the job doesn't exist, 403 if it belongs to another user.
 */
export async function getJob(jobId: number): Promise<JobPublic> {
  const { data } = await apiClient.get<JobPublic>(`/jobs/${jobId}`)
  return data
}
