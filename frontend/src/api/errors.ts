/**
 * Normalizarea erorilor venite de la backend.
 *
 * Backendul răspunde cu **două forme diferite** de `detail`:
 *   - excepțiile de domeniu (`GlanceError`) → `{"detail": "mesaj"}` — string;
 *   - erorile de validare Pydantic (422)    → `{"detail": [{loc, msg, type}]}` — array.
 *
 * Fără stratul ăsta, afișarea directă a lui `detail` produce „[object Object]"
 * pe ecran. Tot aici traducem erorile de rețea în mesaje utile.
 */

import axios from 'axios'

/** Un element din răspunsul de validare FastAPI. */
interface DetaliuValidare {
  loc: (string | number)[]
  msg: string
  type: string
}

/**
 * Eroare uniformă de API, cu mesaj gata de afișat și erori pe câmpuri.
 */
export class ApiError extends Error {
  /** Codul HTTP; `0` pentru erori de rețea sau timeout. */
  readonly status: number
  /** Mesaj pe câmp de formular, cheia fiind numele câmpului (`email`, `password`). */
  readonly eroriCampuri: Record<string, string>

  constructor(message: string, status: number, eroriCampuri: Record<string, string> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.eroriCampuri = eroriCampuri
  }

  /** `true` dacă cererea nu a ajuns la server (offline, IP greșit, backend oprit). */
  get esteEroareDeRetea(): boolean {
    return this.status === 0
  }

  /** `true` dacă sesiunea nu mai e validă și utilizatorul trebuie să se autentifice din nou. */
  get esteNeautentificat(): boolean {
    return this.status === 401
  }
}

/**
 * Transformă lista de erori de validare FastAPI într-un dicționar pe câmpuri.
 *
 * `loc` arată de obicei ca `["body", "email"]`, deci luăm ultimul element
 * ca nume de câmp.
 */
function extrageEroriCampuri(detalii: DetaliuValidare[]): Record<string, string> {
  const rezultat: Record<string, string> = {}

  for (const detaliu of detalii) {
    const camp = detaliu.loc[detaliu.loc.length - 1]
    if (typeof camp === 'string' && camp !== 'body') {
      rezultat[camp] = detaliu.msg
    }
  }

  return rezultat
}

/**
 * Construiește un mesaj scurt, lizibil, dintr-o listă de erori de validare.
 */
function rezumaEroriValidare(detalii: DetaliuValidare[]): string {
  const primul = detalii[0]
  if (!primul) {
    return 'Datele trimise nu sunt valide.'
  }

  const camp = primul.loc[primul.loc.length - 1]
  return typeof camp === 'string' && camp !== 'body' ? `${camp}: ${primul.msg}` : primul.msg
}

/**
 * Convertește orice eroare aruncată de axios într-un `ApiError`.
 *
 * Args:
 *   eroare: Valoarea prinsă în `catch` — poate fi orice.
 *
 * Returns:
 *   Un `ApiError` cu mesaj în română, potrivit pentru afișare directă.
 */
export function normalizeazaEroare(eroare: unknown): ApiError {
  if (eroare instanceof ApiError) {
    return eroare
  }

  if (!axios.isAxiosError(eroare)) {
    const mesaj = eroare instanceof Error ? eroare.message : 'A apărut o eroare neașteptată.'
    return new ApiError(mesaj, 0)
  }

  if (eroare.code === 'ECONNABORTED') {
    return new ApiError('Serverul nu a răspuns la timp. Încearcă din nou.', 0)
  }

  if (!eroare.response) {
    return new ApiError(
      'Nu mă pot conecta la server. Verifică dacă backendul rulează și dacă telefonul e pe aceeași rețea Wi-Fi.',
      0
    )
  }

  const { status, data } = eroare.response
  const detail: unknown = (data as { detail?: unknown } | undefined)?.detail

  if (typeof detail === 'string') {
    return new ApiError(detail, status)
  }

  if (Array.isArray(detail)) {
    const detalii = detail as DetaliuValidare[]
    return new ApiError(rezumaEroriValidare(detalii), status, extrageEroriCampuri(detalii))
  }

  return new ApiError(mesajImplicitPentruStatus(status), status)
}

/** Mesaj de rezervă când backendul nu trimite un `detail` interpretabil. */
function mesajImplicitPentruStatus(status: number): string {
  switch (status) {
    case 401:
      return 'Sesiunea a expirat. Autentifică-te din nou.'
    case 403:
      return 'Nu ai acces la această resursă.'
    case 404:
      return 'Resursa cerută nu există.'
    case 413:
      return 'Imaginea este prea mare.'
    case 415:
      return 'Formatul imaginii nu este acceptat.'
    case 503:
      return 'Un serviciu necesar este momentan indisponibil.'
    default:
      return `Eroare de server (${status}).`
  }
}
