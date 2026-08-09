/**
 * The application's HTTP client, with automatic session renewal.
 *
 * ## Why the refresh is serialized
 *
 * The backend rotates the refresh token: on every successful
 * `/auth/refresh`, the token used is marked `revoked` and a new one is
 * issued (`backend/docs/module-1-auth.md`). If two requests get a 401 at
 * the same time and each starts its own refresh, the second one uses the
 * already-consumed token, gets a 401, and logs the user out for no reason.
 *
 * That's why every request that needs a new token awaits the **same**
 * promise (`refreshPromise`). The first request to get a 401 starts the
 * refresh; the rest attach to it and retry their call with the resulting
 * token.
 */

import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

import { API_URL, DEFAULT_TIMEOUT_MS } from '@/config/env'
import type { TokenResponse } from '@/types/api'

import { ApiError, normalizeError } from './errors'
import { tokenStore } from './tokenStore'

/** Flagged request config, so we don't retry the same request forever. */
interface RetryableConfig extends InternalAxiosRequestConfig {
  _retriedAfterRefresh?: boolean
}

/** The client used by the rest of the app. */
export const apiClient = axios.create({
  baseURL: API_URL,
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * Client without interceptors, used **exclusively** for `/auth/refresh`.
 * If the refresh went through `apiClient`, a 401 on refresh would trigger
 * a new refresh, forever.
 */
const rawClient = axios.create({
  baseURL: API_URL,
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
})

/** Routes that must never trigger a refresh on 401. */
const ROUTES_WITHOUT_REFRESH = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']

/** The refresh promise in flight; `null` when nothing is being renewed. */
let refreshPromise: Promise<string> | null = null

/** Callback used by the state layer to learn that the session has died for good. */
let onSessionExpired: (() => void) | null = null

/**
 * Registers the handler called when the session can no longer be renewed.
 *
 * Args:
 *   handler: Argument-less function — usually clearing `authStore`.
 */
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler
}

/**
 * Actually performs the session renewal.
 *
 * Returns:
 *   The new access token.
 *
 * Raises:
 *   ApiError: If there's no refresh token or the backend rejects it. In
 *     both cases the local session is cleared and the expiry handler is called.
 */
async function performRefresh(): Promise<string> {
  const refreshToken = await tokenStore.getRefreshToken()

  if (!refreshToken) {
    await tokenStore.clear()
    onSessionExpired?.()
    throw new ApiError('Your session has expired. Please log in again.', 401)
  }

  try {
    const { data } = await rawClient.post<TokenResponse>('/auth/refresh', {
      refresh_token: refreshToken,
    })
    await tokenStore.save(data)
    return data.access_token
  } catch (error) {
    await tokenStore.clear()
    onSessionExpired?.()
    throw normalizeError(error)
  }
}

/**
 * Renews the session, guaranteeing a single refresh request in flight.
 *
 * Returns:
 *   The fresh access token, shared by all concurrent callers.
 */
function renewSession(): Promise<string> {
  refreshPromise ??= performRefresh().finally(() => {
    refreshPromise = null
  })

  return refreshPromise
}

apiClient.interceptors.request.use(async (config) => {
  const token = await tokenStore.getAccessToken()

  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }

  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetryableConfig | undefined
    const isAuthRoute = ROUTES_WITHOUT_REFRESH.some((route) => config?.url?.startsWith(route) ?? false)

    const cannotRetry =
      error.response?.status !== 401 ||
      !config ||
      config._retriedAfterRefresh === true ||
      isAuthRoute

    if (cannotRetry) {
      throw normalizeError(error)
    }

    config._retriedAfterRefresh = true

    const newToken = await renewSession()
    config.headers.Authorization = `Bearer ${newToken}`

    return apiClient.request(config)
  }
)
