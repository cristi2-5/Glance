/**
 * Stocarea tokenurilor în zona securizată a sistemului de operare.
 *
 * `expo-secure-store` folosește Keychain pe iOS și Keystore pe Android —
 * spre deosebire de `AsyncStorage`, conținutul nu e lizibil dintr-un backup
 * al aplicației sau de pe un telefon rootat.
 *
 * Peste stocarea nativă ținem o oglindă în memorie: interceptorul de cereri
 * citește tokenul la *fiecare* request, iar un acces la Keychain per cerere
 * ar fi vizibil ca latență.
 */

import * as SecureStore from 'expo-secure-store'

import type { RaspunsTokenuri } from '@/types/api'

const CHEIE_ACCESS = 'glance.access_token'
const CHEIE_REFRESH = 'glance.refresh_token'

let accessInMemorie: string | null = null
let refreshInMemorie: string | null = null
let hidratat = false

/**
 * Citește tokenurile din stocarea securizată în oglinda din memorie.
 *
 * Idempotentă: apelurile ulterioare nu mai ating Keychain-ul.
 */
async function hidrateaza(): Promise<void> {
  if (hidratat) {
    return
  }

  const [access, refresh] = await Promise.all([
    SecureStore.getItemAsync(CHEIE_ACCESS),
    SecureStore.getItemAsync(CHEIE_REFRESH),
  ])

  accessInMemorie = access
  refreshInMemorie = refresh
  hidratat = true
}

export const tokenStore = {
  /** Forțează citirea din stocare — apelată o dată, la pornirea aplicației. */
  hidrateaza,

  /**
   * Returns:
   *   Tokenul de acces curent, sau `null` dacă nu există sesiune.
   */
  async getAccessToken(): Promise<string | null> {
    await hidrateaza()
    return accessInMemorie
  },

  /**
   * Returns:
   *   Refresh tokenul curent, sau `null` dacă nu există sesiune.
   */
  async getRefreshToken(): Promise<string | null> {
    await hidrateaza()
    return refreshInMemorie
  },

  /**
   * Persistă o pereche nouă de tokenuri.
   *
   * Args:
   *   tokenuri: Răspunsul de la `/auth/login`, `/auth/register` sau `/auth/refresh`.
   */
  async salveaza(tokenuri: RaspunsTokenuri): Promise<void> {
    accessInMemorie = tokenuri.access_token
    refreshInMemorie = tokenuri.refresh_token
    hidratat = true

    await Promise.all([
      SecureStore.setItemAsync(CHEIE_ACCESS, tokenuri.access_token),
      SecureStore.setItemAsync(CHEIE_REFRESH, tokenuri.refresh_token),
    ])
  },

  /** Șterge sesiunea din memorie și din stocarea securizată. */
  async sterge(): Promise<void> {
    accessInMemorie = null
    refreshInMemorie = null
    hidratat = true

    await Promise.all([
      SecureStore.deleteItemAsync(CHEIE_ACCESS),
      SecureStore.deleteItemAsync(CHEIE_REFRESH),
    ])
  },
}
