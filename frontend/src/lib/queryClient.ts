/**
 * TanStack Query cache configuration.
 */

import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/errors'

/** Number of retries for requests that can fail transiently. */
const MAX_RETRIES = 2

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      /**
       * We don't retry client errors (4xx): a 401, 403, or 404 isn't
       * resolved by repeating the request. We only retry 5xx and network errors.
       */
      retry(failureCount, error) {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false
        }
        return failureCount < MAX_RETRIES
      },
    },
    mutations: {
      retry: false,
    },
  },
})
