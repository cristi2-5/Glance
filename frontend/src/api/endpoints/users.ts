/** Calls related to the current user. */

import { apiClient } from '@/api/client'
import type { UserPublic } from '@/types/api'

/**
 * Reads the authenticated user's profile.
 *
 * Also used as a session check at app startup: if the token from the
 * Keychain has expired, the interceptor renews it transparently; if the
 * refresh token is no longer valid either, the call fails with 401 and the
 * session is cleared.
 *
 * Returns:
 *   The current user.
 *
 * Raises:
 *   ApiError: 401 if the session is no longer valid.
 */
export async function getCurrentUser(): Promise<UserPublic> {
  const { data } = await apiClient.get<UserPublic>('/users/me')
  return data
}
