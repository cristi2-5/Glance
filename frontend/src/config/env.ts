/**
 * Configurația de mediu a clientului.
 *
 * Adresa backendului se rezolvă în trei trepte, în ordinea asta:
 *   1. `EXPO_PUBLIC_API_URL` din `.env`, dacă e setată explicit.
 *   2. Derivată din gazda Metro (`hostUri`) — telefonul știe deja IP-ul
 *      laptopului de la care a încărcat bundle-ul, deci nu trebuie
 *      rescris nimic când te muți pe altă rețea Wi-Fi.
 *   3. `localhost`, ca ultimă soluție (util doar pe emulator/web).
 */

import Constants from 'expo-constants'

/** Portul pe care ascultă backendul FastAPI. */
const PORT_BACKEND = 8000

/**
 * Deduce adresa backendului din gazda serverului Metro.
 *
 * Returns:
 *   URL-ul backendului pe același host ca Metro, sau `null` dacă gazda
 *   nu poate fi determinată (build de producție, de exemplu).
 */
function deducereDinGazdaExpo(): string | null {
  const hostUri = Constants.expoConfig?.hostUri ?? Constants.expoGoConfig?.debuggerHost
  if (!hostUri) {
    return null
  }

  const gazda = hostUri.split(':')[0]
  if (!gazda) {
    return null
  }

  return `http://${gazda}:${PORT_BACKEND}`
}

/** Adresa de bază a backendului Glance. */
export const API_URL: string =
  process.env.EXPO_PUBLIC_API_URL ?? deducereDinGazdaExpo() ?? `http://localhost:${PORT_BACKEND}`

/** Timeout implicit pentru cererile obișnuite (ms). */
export const TIMEOUT_IMPLICIT_MS = 15_000

/**
 * Timeout pentru upload-ul coperții — mai generos, imaginea poate fi mare
 * pe o conexiune Wi-Fi slabă.
 */
export const TIMEOUT_UPLOAD_MS = 60_000

/** Intervalul de polling pe `GET /jobs/{id}` (ms). */
export const INTERVAL_POLLING_JOB_MS = 1_500

/**
 * Limita de dimensiune impusă de backend (8 MB, `MAX_UPLOAD_SIZE_BYTES`).
 * Ținută aici ca să putem redimensiona *înainte* de upload, nu să încasăm 413.
 */
export const DIMENSIUNE_MAXIMA_UPLOAD_BYTES = 8 * 1024 * 1024
