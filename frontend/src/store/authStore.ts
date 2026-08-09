/**
 * Global authentication state.
 *
 * This is the only truly global state in the app — the rest of the server
 * data lives in the TanStack Query cache, which already has invalidation
 * and refetch.
 *
 * Session state has **three** values, not two. `'unknown'` covers the
 * interval between app startup and checking the token from the Keychain.
 * Without it, an already-authenticated user would briefly see the login
 * screen before being redirected.
 */

import { create } from 'zustand'

import { login as apiLogin, logout as apiLogout, register as apiRegister } from '@/api/endpoints/auth'
import { getCurrentUser } from '@/api/endpoints/users'
import { normalizeError } from '@/api/errors'
import { tokenStore } from '@/api/tokenStore'
import type { CredentialsRequest, UserPublic } from '@/types/api'

/** The possible stages of the session. */
export type SessionState = 'unknown' | 'authenticated' | 'unauthenticated'

interface AuthState {
  status: SessionState
  user: UserPublic | null

  /** Restores the session from the Keychain at app startup. */
  restoreSession: () => Promise<void>
  /** Logs in and populates the current user. */
  login: (credentials: CredentialsRequest) => Promise<void>
  /** Creates an account, then logs in immediately with the same credentials. */
  createAccount: (credentials: CredentialsRequest) => Promise<void>
  /** Revokes the session locally and on the server. */
  logout: () => Promise<void>
  /** Marks the session as lost, without a network call (used by the interceptor). */
  invalidateLocal: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  status: 'unknown',
  user: null,

  async restoreSession() {
    await tokenStore.hydrate()
    const token = await tokenStore.getAccessToken()

    if (!token) {
      set({ status: 'unauthenticated', user: null })
      return
    }

    try {
      // If the access token has expired, the interceptor renews it here,
      // transparently. If the refresh token is no longer valid either, we get a 401.
      const user = await getCurrentUser()
      set({ status: 'authenticated', user })
    } catch {
      await tokenStore.clear()
      set({ status: 'unauthenticated', user: null })
    }
  },

  async login(credentials) {
    try {
      const tokens = await apiLogin(credentials)
      await tokenStore.save(tokens)

      const user = await getCurrentUser()
      set({ status: 'authenticated', user })
    } catch (error) {
      throw normalizeError(error)
    }
  },

  async createAccount(credentials) {
    try {
      // `/auth/register` only returns the profile, not tokens — that's why
      // an explicit login with the same credentials follows.
      await apiRegister(credentials)

      const tokens = await apiLogin(credentials)
      await tokenStore.save(tokens)

      const user = await getCurrentUser()
      set({ status: 'authenticated', user })
    } catch (error) {
      throw normalizeError(error)
    }
  },

  async logout() {
    const refreshToken = await tokenStore.getRefreshToken()

    if (refreshToken) {
      try {
        await apiLogout(refreshToken)
      } catch {
        // Logout is best-effort: if the server doesn't respond, the local
        // session still has to be cleared. Any leftover token expires on its own anyway.
      }
    }

    await tokenStore.clear()
    set({ status: 'unauthenticated', user: null })
  },

  invalidateLocal() {
    set({ status: 'unauthenticated', user: null })
  },
}))
