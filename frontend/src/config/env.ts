/**
 * The client's environment configuration.
 *
 * The backend address is resolved in three steps, in this order:
 *   1. `EXPO_PUBLIC_API_URL` from `.env`, if set explicitly.
 *   2. Derived from the Metro host (`hostUri`) — the phone already knows
 *      the IP of the laptop it loaded the bundle from, so nothing needs to
 *      be rewritten when you move to a different Wi-Fi network.
 *   3. `localhost`, as a last resort (only useful on emulator/web).
 */

import Constants from 'expo-constants'

/** The port the FastAPI backend listens on. */
const BACKEND_PORT = 8000

/**
 * Derives the backend address from the Metro server's host.
 *
 * Returns:
 *   The backend URL on the same host as Metro, or `null` if the host
 *   can't be determined (a production build, for example).
 */
function deriveFromExpoHost(): string | null {
  const hostUri = Constants.expoConfig?.hostUri ?? Constants.expoGoConfig?.debuggerHost
  if (!hostUri) {
    return null
  }

  const host = hostUri.split(':')[0]
  if (!host) {
    return null
  }

  return `http://${host}:${BACKEND_PORT}`
}

/** The Glance backend's base address. */
export const API_URL: string =
  process.env.EXPO_PUBLIC_API_URL ?? deriveFromExpoHost() ?? `http://localhost:${BACKEND_PORT}`

/** Default timeout for ordinary requests (ms). */
export const DEFAULT_TIMEOUT_MS = 15_000

/**
 * Timeout for the cover upload — more generous, the image can be large on a
 * weak Wi-Fi connection.
 */
export const UPLOAD_TIMEOUT_MS = 60_000

/** Polling interval for `GET /jobs/{id}` (ms). */
export const JOB_POLLING_INTERVAL_MS = 1_500

/**
 * Size limit imposed by the backend (8 MB, `MAX_UPLOAD_SIZE_BYTES`).
 * Kept here so we can resize *before* upload, instead of getting a 413.
 */
export const MAX_UPLOAD_SIZE_BYTES = 8 * 1024 * 1024
