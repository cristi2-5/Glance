/**
 * Calls for the authentication flow.
 *
 * Watch out for two backend quirks, verified against the OpenAPI schema:
 *   - `/auth/login` accepts **JSON** `{email, password}`, not OAuth2
 *     form-data, despite the `OAuth2PasswordBearer` security scheme;
 *   - `/auth/register` returns `UserPublic`, **not** tokens — after
 *     registering, an explicit login is required.
 */

import { apiClient } from '@/api/client'
import type { CredentialsRequest, TokenResponse, UserPublic } from '@/types/api'

/**
 * Creates a new account.
 *
 * Args:
 *   credentials: Email and password (minimum 8 characters).
 *
 * Returns:
 *   The created user. Contains no tokens — see the note in the module header.
 *
 * Raises:
 *   ApiError: 422 if the email is already in use or the password is too short.
 */
export async function register(credentials: CredentialsRequest): Promise<UserPublic> {
  const { data } = await apiClient.post<UserPublic>('/auth/register', credentials)
  return data
}

/**
 * Logs in an existing user.
 *
 * Args:
 *   credentials: Email and password.
 *
 * Returns:
 *   The token pair (access + refresh).
 *
 * Raises:
 *   ApiError: 401 on wrong credentials or an inactive account.
 */
export async function login(credentials: CredentialsRequest): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/auth/login', credentials)
  return data
}

/**
 * Revokes the refresh token on the server.
 *
 * The backend responds 204 and is idempotent: a nonexistent or
 * already-revoked token doesn't produce an error, so the call is safe to
 * make "blindly".
 *
 * Args:
 *   refreshToken: The token to revoke.
 */
export async function logout(refreshToken: string): Promise<void> {
  await apiClient.post('/auth/logout', { refresh_token: refreshToken })
}
