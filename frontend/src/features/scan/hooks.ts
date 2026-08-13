/** Hooks for the cover scanning flow. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { analyzeCover, getBookSummary } from '@/api/endpoints/books'
import { correctJob, getJob } from '@/api/endpoints/jobs'
import { ApiError } from '@/api/errors'
import { JOB_POLLING_INTERVAL_MS } from '@/config/env'
import { prepareCoverForUpload } from '@/lib/imagine'
import type { BookSummary, CorrectionRequest, JobCreated, JobPublic } from '@/types/api'

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

/**
 * Fetches a book's generated summary with its citations.
 *
 * Runs as its own query rather than as part of the job result, because it
 * is slower than everything else on the result screen: the first request
 * for a book embeds its whole corpus locally before generating. Keeping it
 * separate is what lets the screen render the book immediately and show a
 * loading state for the summary section alone.
 *
 * Deliberately **not** retried. The expensive failure — the backend can't
 * reach Ollama or Groq — returns a 503 that a retry a second later will
 * hit again, and each attempt can occupy the request for a minute and a
 * half. The screen offers an explicit "Try again" instead, which is
 * cheaper and honest about what's happening.
 *
 * Args:
 *   bookId: The cached book id, or `null` when the scan produced none —
 *     no catalog entry means no passages, so there is nothing to ask for.
 */
export function useBookSummary(bookId: number | null) {
  return useQuery<BookSummary, ApiError>({
    queryKey: ['book-summary', bookId],
    enabled: bookId !== null,
    async queryFn() {
      if (bookId === null) {
        throw new Error('useBookSummary was called without a bookId.')
      }
      return getBookSummary(bookId)
    },
    retry: false,
    // The backend caches a generated summary on the book row, so re-asking
    // for one within a session only costs a round trip — but it also can't
    // change in that time, so there is no reason to spend it.
    staleTime: Infinity,
  })
}
