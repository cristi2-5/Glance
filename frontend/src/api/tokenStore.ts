/**
 * Token storage in the operating system's secure area.
 *
 * `expo-secure-store` uses the Keychain on iOS and Keystore on Android —
 * unlike `AsyncStorage`, the content isn't readable from an app backup or
 * from a rooted phone.
 *
 * On top of the native storage we keep an in-memory mirror: the request
 * interceptor reads the token on *every* request, and a Keychain access per
 * request would be visible as latency.
 */

import * as SecureStore from 'expo-secure-store'

import type { TokenResponse } from '@/types/api'

const ACCESS_KEY = 'glance.access_token'
const REFRESH_KEY = 'glance.refresh_token'

let accessInMemory: string | null = null
let refreshInMemory: string | null = null
let hydrated = false

/**
 * Reads the tokens from secure storage into the in-memory mirror.
 *
 * Idempotent: subsequent calls no longer touch the Keychain.
 */
async function hydrate(): Promise<void> {
  if (hydrated) {
    return
  }

  const [access, refresh] = await Promise.all([
    SecureStore.getItemAsync(ACCESS_KEY),
    SecureStore.getItemAsync(REFRESH_KEY),
  ])

  accessInMemory = access
  refreshInMemory = refresh
  hydrated = true
}

export const tokenStore = {
  /** Forces a read from storage — called once, at app startup. */
  hydrate,

  /**
   * Returns:
   *   The current access token, or `null` if there is no session.
   */
  async getAccessToken(): Promise<string | null> {
    await hydrate()
    return accessInMemory
  },

  /**
   * Returns:
   *   The current refresh token, or `null` if there is no session.
   */
  async getRefreshToken(): Promise<string | null> {
    await hydrate()
    return refreshInMemory
  },

  /**
   * Persists a new pair of tokens.
   *
   * Args:
   *   tokens: The response from `/auth/login`, `/auth/register`, or `/auth/refresh`.
   */
  async save(tokens: TokenResponse): Promise<void> {
    accessInMemory = tokens.access_token
    refreshInMemory = tokens.refresh_token
    hydrated = true

    await Promise.all([
      SecureStore.setItemAsync(ACCESS_KEY, tokens.access_token),
      SecureStore.setItemAsync(REFRESH_KEY, tokens.refresh_token),
    ])
  },

  /** Clears the session from memory and from secure storage. */
  async clear(): Promise<void> {
    accessInMemory = null
    refreshInMemory = null
    hydrated = true

    await Promise.all([
      SecureStore.deleteItemAsync(ACCESS_KEY),
      SecureStore.deleteItemAsync(REFRESH_KEY),
    ])
  },
}
