/**
 * Hook-uri pentru istoric, preferințe și recomandări.
 *
 * **Punctul unic de comutare mock → API real.** Ecranele consumă doar
 * hook-urile de aici; când Modulul 6 al backendului e gata, se înlocuiesc
 * corpurile funcțiilor `queryFn` cu apeluri HTTP și se pune
 * `DATE_DEMONSTRATIVE` pe `false`. Niciun ecran nu se modifică.
 */

import { useQuery } from '@tanstack/react-query'

import { ISTORIC_DEMO, PREFERINTE_DEMO, RECOMANDARI_DEMO } from '@/mocks/biblioteca'
import type { IntrareIstoric, PreferinteUtilizator, Recomandare } from '@/types/biblioteca'

/**
 * `true` cât timp datele vin din `src/mocks/`. Ecranele îl folosesc ca să
 * afișeze un marcaj vizibil — un mock nemarcat e o minciună pe ecran.
 */
export const DATE_DEMONSTRATIVE = true

/** Simulează latența rețelei, ca stările de încărcare să fie vizibile în dezvoltare. */
const INTARZIERE_MOCK_MS = 400

function intarzie<T>(valoare: T): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(valoare)
    }, INTARZIERE_MOCK_MS)
  })
}

/** Istoricul de lectură al utilizatorului curent. */
export function useIstoric() {
  return useQuery<IntrareIstoric[]>({
    queryKey: ['istoric'],
    queryFn: () => intarzie(ISTORIC_DEMO),
  })
}

/** Genurile și autorii preferați. */
export function usePreferinte() {
  return useQuery<PreferinteUtilizator>({
    queryKey: ['preferinte'],
    queryFn: () => intarzie(PREFERINTE_DEMO),
  })
}

/** Recomandările personalizate, ordonate descrescător după scor. */
export function useRecomandari() {
  return useQuery<Recomandare[]>({
    queryKey: ['recomandari'],
    queryFn: () => intarzie(RECOMANDARI_DEMO),
  })
}
