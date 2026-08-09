/** Reading and correcting the state of asynchronous jobs. */

import { apiClient } from '@/api/client'
import type { CorrectionRequest, JobPublic } from '@/types/api'

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

/**
 * Applies a manual title/author correction to a finished job's result.
 *
 * Used when the recognized title/author had low confidence
 * (`AnalysisResult.needs_review`) and the user overrides it by hand.
 *
 * Args:
 *   jobId: The id of the job to correct.
 *   correction: The corrected title and, optionally, author.
 *
 * Returns:
 *   The updated job, with `result.method === 'manual'` and `result.corrected === true`.
 *
 * Raises:
 *   ApiError: 404 if the job doesn't exist, 403 if it belongs to another
 *     user, 422 if the job isn't finished yet.
 */
export async function correctJob(jobId: number, correction: CorrectionRequest): Promise<JobPublic> {
  const { data } = await apiClient.patch<JobPublic>(`/jobs/${jobId}/correction`, correction)
  return data
}
