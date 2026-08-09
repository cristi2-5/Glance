/**
 * Normalization of errors coming from the backend.
 *
 * The backend responds with **two different shapes** for `detail`:
 *   - domain exceptions (`GlanceError`) → `{"detail": "message"}` — string;
 *   - Pydantic validation errors (422)  → `{"detail": [{loc, msg, type}]}` — array.
 *
 * Without this layer, displaying `detail` directly produces "[object Object]"
 * on screen. This is also where we translate network errors into useful
 * messages.
 */

import axios from 'axios'

/** An item from FastAPI's validation response. */
interface ValidationDetail {
  loc: (string | number)[]
  msg: string
  type: string
}

/**
 * A uniform API error, with a display-ready message and per-field errors.
 */
export class ApiError extends Error {
  /** The HTTP status code; `0` for network errors or timeouts. */
  readonly status: number
  /** Message per form field, keyed by field name (`email`, `password`). */
  readonly fieldErrors: Record<string, string>

  constructor(message: string, status: number, fieldErrors: Record<string, string> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
  }

  /** `true` if the request never reached the server (offline, wrong IP, backend down). */
  get isNetworkError(): boolean {
    return this.status === 0
  }

  /** `true` if the session is no longer valid and the user needs to log in again. */
  get isUnauthenticated(): boolean {
    return this.status === 401
  }
}

/**
 * Turns FastAPI's list of validation errors into a per-field dictionary.
 *
 * `loc` usually looks like `["body", "email"]`, so we take the last element
 * as the field name.
 */
function extractFieldErrors(details: ValidationDetail[]): Record<string, string> {
  const result: Record<string, string> = {}

  for (const detail of details) {
    const field = detail.loc[detail.loc.length - 1]
    if (typeof field === 'string' && field !== 'body') {
      result[field] = detail.msg
    }
  }

  return result
}

/**
 * Builds a short, readable message from a list of validation errors.
 */
function summarizeValidationErrors(details: ValidationDetail[]): string {
  const first = details[0]
  if (!first) {
    return 'The submitted data is not valid.'
  }

  const field = first.loc[first.loc.length - 1]
  return typeof field === 'string' && field !== 'body' ? `${field}: ${first.msg}` : first.msg
}

/**
 * Converts any error thrown by axios into an `ApiError`.
 *
 * Args:
 *   error: The value caught in `catch` — can be anything.
 *
 * Returns:
 *   An `ApiError` with a message ready to display directly.
 */
export function normalizeError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }

  if (!axios.isAxiosError(error)) {
    const message = error instanceof Error ? error.message : 'An unexpected error occurred.'
    return new ApiError(message, 0)
  }

  if (error.code === 'ECONNABORTED') {
    return new ApiError('The server did not respond in time. Try again.', 0)
  }

  if (!error.response) {
    return new ApiError(
      "Can't connect to the server. Check that the backend is running and that the phone is on the same Wi-Fi network.",
      0
    )
  }

  const { status, data } = error.response
  const detail: unknown = (data as { detail?: unknown } | undefined)?.detail

  if (typeof detail === 'string') {
    return new ApiError(detail, status)
  }

  if (Array.isArray(detail)) {
    const details = detail as ValidationDetail[]
    return new ApiError(summarizeValidationErrors(details), status, extractFieldErrors(details))
  }

  return new ApiError(defaultMessageForStatus(status), status)
}

/** Fallback message when the backend doesn't send an interpretable `detail`. */
function defaultMessageForStatus(status: number): string {
  switch (status) {
    case 401:
      return 'Your session has expired. Please log in again.'
    case 403:
      return "You don't have access to this resource."
    case 404:
      return 'The requested resource does not exist.'
    case 413:
      return 'The image is too large.'
    case 415:
      return 'The image format is not supported.'
    case 503:
      return 'A required service is currently unavailable.'
    default:
      return `Server error (${status}).`
  }
}
