/** Hooks for the cover scanning flow. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { analyzeCover } from '@/api/endpoints/books'
import { correctJob, getJob } from '@/api/endpoints/jobs'
import { JOB_POLLING_INTERVAL_MS } from '@/config/env'
import { prepareCoverForUpload } from '@/lib/imagine'
import type { CorrectionRequest, JobCreated, JobPublic } from '@/types/api'

/** Arguments for starting an analysis. */
interface AnalysisArgs {
  /** The local URI of the photo, straight from the camera. */
  uri: string
  /** The original width, to avoid needlessly enlarging a small image. */
  width?: number
}

/**
 * Prepares the image and starts the analysis job.
 *
 * The resizing happens here, not on the screen: it's a transport concern
 * (the backend's 8 MB limit), not a UI one.
 */
export function useAnalyzeCover() {
  return useMutation<JobCreated, Error, AnalysisArgs>({
    async mutationFn({ uri, width }) {
      const prepared = await prepareCoverForUpload(uri, width)
      return analyzeCover(prepared.uri)
    },
  })
}

/**
 * Tracks a job until it finishes.
 *
 * Polling stops on its own once `status` becomes `done` or `failed` —
 * `refetchInterval` returns `false`, so we stop hitting the server for no
 * reason while the screen stays open.
 *
 * Args:
 *   jobId: The job's id, or `null` while one doesn't exist yet.
 */
export function useJob(jobId: number | null) {
  return useQuery<JobPublic>({
    queryKey: ['job', jobId],
    enabled: jobId !== null,
    async queryFn() {
      if (jobId === null) {
        throw new Error('useJob was called without a jobId.')
      }
      return getJob(jobId)
    },
    refetchInterval(query) {
      const status = query.state.data?.status
      const isFinished = status === 'done' || status === 'failed'
      return isFinished ? false : JOB_POLLING_INTERVAL_MS
    },
  })
}

/**
 * Applies a manual title/author correction to a job's result.
 *
 * On success, writes the updated job straight into the `['job', jobId]`
 * cache entry, so the result screen reflects the correction immediately
 * with no refetch.
 *
 * The backend returns that job in `running`, not `done`: a correction
 * usually means the wrong book was recognized, so it discards the metadata
 * fetched for the old title and re-fetches for the new one in the
 * background. Writing a `running` job into the cache is what makes
 * `useJob`'s `refetchInterval` resume polling — which is the intended
 * behaviour here, not an accident to guard against.
 */
export function useCorrectJob(jobId: number) {
  const queryClient = useQueryClient()

  return useMutation<JobPublic, Error, CorrectionRequest>({
    mutationFn(correction) {
      return correctJob(jobId, correction)
    },
    onSuccess(data) {
      queryClient.setQueryData(['job', jobId], data)
    },
  })
}
