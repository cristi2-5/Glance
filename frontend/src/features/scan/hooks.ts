/** Hook-uri pentru fluxul de scanare a coperții. */

import { useMutation, useQuery } from '@tanstack/react-query'

import { analizeazaCoperta } from '@/api/endpoints/books'
import { getJob } from '@/api/endpoints/jobs'
import { INTERVAL_POLLING_JOB_MS } from '@/config/env'
import { pregatesteCopertaPentruUpload } from '@/lib/imagine'
import type { JobCreat, JobPublic } from '@/types/api'

/** Argumentele pentru pornirea unei analize. */
interface ArgumenteAnaliza {
  /** URI-ul local al fotografiei, direct din cameră. */
  uri: string
  /** Lățimea originală, ca să evităm mărirea inutilă a unei imagini mici. */
  latime?: number
}

/**
 * Pregătește imaginea și pornește job-ul de analiză.
 *
 * Redimensionarea se face aici, nu în ecran: e o preocupare de transport
 * (limita de 8 MB a backendului), nu de interfață.
 */
export function useAnalizaCoperta() {
  return useMutation<JobCreat, Error, ArgumenteAnaliza>({
    async mutationFn({ uri, latime }) {
      const pregatita = await pregatesteCopertaPentruUpload(uri, latime)
      return analizeazaCoperta(pregatita.uri)
    },
  })
}

/**
 * Urmărește un job până se termină.
 *
 * Polling-ul se oprește singur când `status` devine `done` sau `failed` —
 * `refetchInterval` întoarce `false`, deci nu mai batem la ușa serverului
 * degeaba cât timp ecranul rămâne deschis.
 *
 * Args:
 *   jobId: Id-ul job-ului, sau `null` cât timp nu există încă unul.
 */
export function useJob(jobId: number | null) {
  return useQuery<JobPublic>({
    queryKey: ['job', jobId],
    enabled: jobId !== null,
    async queryFn() {
      if (jobId === null) {
        throw new Error('useJob a fost apelat fără jobId.')
      }
      return getJob(jobId)
    },
    refetchInterval(query) {
      const status = query.state.data?.status
      const sAterminat = status === 'done' || status === 'failed'
      return sAterminat ? false : INTERVAL_POLLING_JOB_MS
    },
  })
}
