/**
 * Apeluri pentru fluxul de autentificare.
 *
 * Atenție la două particularități ale backendului, verificate în schema
 * OpenAPI:
 *   - `/auth/login` primește **JSON** `{email, password}`, nu form-data
 *     OAuth2, în ciuda schemei de securitate `OAuth2PasswordBearer`;
 *   - `/auth/register` întoarce `UserPublic`, **nu** tokenuri — după
 *     înregistrare e nevoie de un login explicit.
 */

import { apiClient } from '@/api/client'
import type { CredentialeRequest, RaspunsTokenuri, UtilizatorPublic } from '@/types/api'

/**
 * Creează un cont nou.
 *
 * Args:
 *   credentiale: Email și parolă (minim 8 caractere).
 *
 * Returns:
 *   Utilizatorul creat. Nu conține tokenuri — vezi nota din antetul modulului.
 *
 * Raises:
 *   ApiError: 422 dacă emailul e deja folosit sau parola e prea scurtă.
 */
export async function inregistreaza(credentiale: CredentialeRequest): Promise<UtilizatorPublic> {
  const { data } = await apiClient.post<UtilizatorPublic>('/auth/register', credentiale)
  return data
}

/**
 * Autentifică un utilizator existent.
 *
 * Args:
 *   credentiale: Email și parolă.
 *
 * Returns:
 *   Perechea de tokenuri (acces + refresh).
 *
 * Raises:
 *   ApiError: 401 la credențiale greșite sau cont inactiv.
 */
export async function autentifica(credentiale: CredentialeRequest): Promise<RaspunsTokenuri> {
  const { data } = await apiClient.post<RaspunsTokenuri>('/auth/login', credentiale)
  return data
}

/**
 * Revocă refresh tokenul pe server.
 *
 * Backendul răspunde 204 și e idempotent: un token inexistent sau deja
 * revocat nu produce eroare, deci apelul e sigur de făcut „orbește".
 *
 * Args:
 *   refreshToken: Tokenul de revocat.
 */
export async function deconecteaza(refreshToken: string): Promise<void> {
  await apiClient.post('/auth/logout', { refresh_token: refreshToken })
}
