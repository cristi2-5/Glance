/** Apeluri legate de utilizatorul curent. */

import { apiClient } from '@/api/client'
import type { UtilizatorPublic } from '@/types/api'

/**
 * Citește profilul utilizatorului autentificat.
 *
 * Folosit și ca verificare de sesiune la pornirea aplicației: dacă tokenul
 * din Keychain e expirat, interceptorul îl reînnoiește transparent; dacă nici
 * refresh tokenul nu mai e valid, apelul eșuează cu 401 și sesiunea se curăță.
 *
 * Returns:
 *   Utilizatorul curent.
 *
 * Raises:
 *   ApiError: 401 dacă sesiunea nu mai e validă.
 */
export async function getUtilizatorCurent(): Promise<UtilizatorPublic> {
  const { data } = await apiClient.get<UtilizatorPublic>('/users/me')
  return data
}
