/**
 * Clientul HTTP al aplicației, cu reînnoire automată a sesiunii.
 *
 * ## De ce e refresh-ul serializat
 *
 * Backendul rotește refresh tokenul: la fiecare `/auth/refresh` reușit,
 * tokenul folosit e marcat `revoked` și se emite altul
 * (`backend/docs/module-1-auth.md`). Dacă două cereri primesc 401 în același
 * timp și fiecare pornește propriul refresh, a doua îl folosește pe cel deja
 * consumat, primește 401 și deconectează utilizatorul fără motiv.
 *
 * De aceea toate cererile care au nevoie de un token nou așteaptă **aceeași**
 * promisiune (`promisiuneRefresh`). Prima cerere care ia 401 pornește refresh-ul;
 * restul se atașează la el și își reiau apelul cu tokenul rezultat.
 */

import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

import { API_URL, TIMEOUT_IMPLICIT_MS } from '@/config/env'
import type { RaspunsTokenuri } from '@/types/api'

import { ApiError, normalizeazaEroare } from './errors'
import { tokenStore } from './tokenStore'

/** Config de cerere marcat, ca să nu reîncercăm la nesfârșit aceeași cerere. */
interface ConfigCuReincercare extends InternalAxiosRequestConfig {
  _reincercatDupaRefresh?: boolean
}

/** Clientul folosit de tot restul aplicației. */
export const apiClient = axios.create({
  baseURL: API_URL,
  timeout: TIMEOUT_IMPLICIT_MS,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * Client fără interceptors, folosit **exclusiv** pentru `/auth/refresh`.
 * Dacă refresh-ul ar trece prin `apiClient`, un 401 la refresh ar declanșa
 * un nou refresh, la infinit.
 */
const clientBrut = axios.create({
  baseURL: API_URL,
  timeout: TIMEOUT_IMPLICIT_MS,
  headers: { 'Content-Type': 'application/json' },
})

/** Rute care nu trebuie să declanșeze niciodată un refresh la 401. */
const RUTE_FARA_REFRESH = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']

/** Promisiunea de refresh în curs; `null` când nu se reînnoiește nimic. */
let promisiuneRefresh: Promise<string> | null = null

/** Callback prin care stratul de stare află că sesiunea a murit definitiv. */
let laSesiuneExpirata: (() => void) | null = null

/**
 * Înregistrează handlerul apelat când sesiunea nu mai poate fi reînnoită.
 *
 * Args:
 *   handler: Funcție fără argumente — de obicei golirea `authStore`.
 */
export function seteazaHandlerSesiuneExpirata(handler: () => void): void {
  laSesiuneExpirata = handler
}

/**
 * Execută efectiv reînnoirea sesiunii.
 *
 * Returns:
 *   Noul token de acces.
 *
 * Raises:
 *   ApiError: Dacă nu există refresh token sau backendul îl respinge. În
 *     ambele cazuri sesiunea locală e ștearsă și handlerul de expirare apelat.
 */
async function executaRefresh(): Promise<string> {
  const refreshToken = await tokenStore.getRefreshToken()

  if (!refreshToken) {
    await tokenStore.sterge()
    laSesiuneExpirata?.()
    throw new ApiError('Sesiunea a expirat. Autentifică-te din nou.', 401)
  }

  try {
    const { data } = await clientBrut.post<RaspunsTokenuri>('/auth/refresh', {
      refresh_token: refreshToken,
    })
    await tokenStore.salveaza(data)
    return data.access_token
  } catch (eroare) {
    await tokenStore.sterge()
    laSesiuneExpirata?.()
    throw normalizeazaEroare(eroare)
  }
}

/**
 * Reînnoiește sesiunea, garantând o singură cerere de refresh în zbor.
 *
 * Returns:
 *   Tokenul de acces proaspăt, partajat de toți apelanții concurenți.
 */
function reinnoiesteSesiunea(): Promise<string> {
  promisiuneRefresh ??= executaRefresh().finally(() => {
    promisiuneRefresh = null
  })

  return promisiuneRefresh
}

apiClient.interceptors.request.use(async (config) => {
  const token = await tokenStore.getAccessToken()

  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }

  return config
})

apiClient.interceptors.response.use(
  (raspuns) => raspuns,
  async (eroare: AxiosError) => {
    const config = eroare.config as ConfigCuReincercare | undefined
    const esteRutaDeAuth = RUTE_FARA_REFRESH.some((ruta) => config?.url?.startsWith(ruta) ?? false)

    const nuPutemReincerca =
      eroare.response?.status !== 401 ||
      !config ||
      config._reincercatDupaRefresh === true ||
      esteRutaDeAuth

    if (nuPutemReincerca) {
      throw normalizeazaEroare(eroare)
    }

    config._reincercatDupaRefresh = true

    const tokenNou = await reinnoiesteSesiunea()
    config.headers.Authorization = `Bearer ${tokenNou}`

    return apiClient.request(config)
  }
)
