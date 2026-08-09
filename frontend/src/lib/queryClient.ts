/**
 * Configurarea cache-ului TanStack Query.
 */

import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/errors'

/** Numărul de reîncercări pentru cererile care pot eșua tranzitoriu. */
const REINCERCARI_MAXIME = 2

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      /**
       * Nu reîncercăm erorile de client (4xx): un 401, 403 sau 404 nu se
       * rezolvă repetând cererea. Reîncercăm doar 5xx și erorile de rețea.
       */
      retry(numarIncercari, eroare) {
        if (eroare instanceof ApiError && eroare.status >= 400 && eroare.status < 500) {
          return false
        }
        return numarIncercari < REINCERCARI_MAXIME
      },
    },
    mutations: {
      retry: false,
    },
  },
})
