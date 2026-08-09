/**
 * Starea globală de autentificare.
 *
 * E singurul state cu adevărat global din aplicație — restul datelor de server
 * stau în cache-ul TanStack Query, care are deja invalidare și refetch.
 *
 * Starea sesiunii are **trei** valori, nu două. `'necunoscuta'` acoperă
 * intervalul dintre pornirea aplicației și verificarea tokenului din Keychain.
 * Fără ea, un utilizator deja autentificat ar vedea o clipă ecranul de login
 * înainte să fie redirecționat.
 */

import { create } from 'zustand'

import { deconecteaza as apiDeconecteaza, autentifica, inregistreaza } from '@/api/endpoints/auth'
import { getUtilizatorCurent } from '@/api/endpoints/users'
import { normalizeazaEroare } from '@/api/errors'
import { tokenStore } from '@/api/tokenStore'
import type { CredentialeRequest, UtilizatorPublic } from '@/types/api'

/** Etapele posibile ale sesiunii. */
export type StareSesiune = 'necunoscuta' | 'autentificat' | 'neautentificat'

interface AuthState {
  stare: StareSesiune
  utilizator: UtilizatorPublic | null

  /** Restaurează sesiunea din Keychain la pornirea aplicației. */
  restaureazaSesiunea: () => Promise<void>
  /** Autentifică și populează utilizatorul curent. */
  intra: (credentiale: CredentialeRequest) => Promise<void>
  /** Creează un cont, apoi autentifică imediat cu aceleași credențiale. */
  creeazaCont: (credentiale: CredentialeRequest) => Promise<void>
  /** Revocă sesiunea local și pe server. */
  iesi: () => Promise<void>
  /** Marchează sesiunea ca pierdută, fără apel de rețea (folosit de interceptor). */
  invalideazaLocal: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  stare: 'necunoscuta',
  utilizator: null,

  async restaureazaSesiunea() {
    await tokenStore.hidrateaza()
    const token = await tokenStore.getAccessToken()

    if (!token) {
      set({ stare: 'neautentificat', utilizator: null })
      return
    }

    try {
      // Dacă tokenul de acces a expirat, interceptorul îl reînnoiește aici,
      // transparent. Dacă nici refresh tokenul nu mai e valid, primim 401.
      const utilizator = await getUtilizatorCurent()
      set({ stare: 'autentificat', utilizator })
    } catch {
      await tokenStore.sterge()
      set({ stare: 'neautentificat', utilizator: null })
    }
  },

  async intra(credentiale) {
    try {
      const tokenuri = await autentifica(credentiale)
      await tokenStore.salveaza(tokenuri)

      const utilizator = await getUtilizatorCurent()
      set({ stare: 'autentificat', utilizator })
    } catch (eroare) {
      throw normalizeazaEroare(eroare)
    }
  },

  async creeazaCont(credentiale) {
    try {
      // `/auth/register` întoarce doar profilul, nu tokenuri — de aceea
      // urmează un login explicit cu aceleași credențiale.
      await inregistreaza(credentiale)

      const tokenuri = await autentifica(credentiale)
      await tokenStore.salveaza(tokenuri)

      const utilizator = await getUtilizatorCurent()
      set({ stare: 'autentificat', utilizator })
    } catch (eroare) {
      throw normalizeazaEroare(eroare)
    }
  },

  async iesi() {
    const refreshToken = await tokenStore.getRefreshToken()

    if (refreshToken) {
      try {
        await apiDeconecteaza(refreshToken)
      } catch {
        // Logout-ul e best-effort: dacă serverul nu răspunde, sesiunea
        // locală tot trebuie ștearsă. Tokenul rămas expiră oricum singur.
      }
    }

    await tokenStore.sterge()
    set({ stare: 'neautentificat', utilizator: null })
  },

  invalideazaLocal() {
    set({ stare: 'neautentificat', utilizator: null })
  },
}))
