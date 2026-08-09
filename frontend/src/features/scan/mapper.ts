/**
 * Traducerea câmpului `result` al unui job în modelul de afișare.
 *
 * Modulul 2 al backendului întoarce încă un placeholder
 * (`{mesaj, dimensiune_imagine_bytes}`), pentru că OCR-ul și sinteza apar
 * abia la Modulele 3-5. Detectăm forma primită și spunem apelantului dacă
 * datele sunt reale sau demonstrative — ecranul afișează un indicator
 * explicit, ca să nu confundăm niciodată un mock cu un rezultat adevărat.
 */

import { ANALIZA_DEMO } from '@/mocks/analiza'
import type { RezultatAnaliza } from '@/types/api'

/** Rezultatul interpretării, cu proveniența datelor marcată explicit. */
export interface AnalizaAfisabila {
  analiza: RezultatAnaliza
  /** `true` când backendul n-a produs încă date reale și afișăm mock-ul. */
  esteDemonstrativ: boolean
}

/**
 * Verifică dacă `result` are forma reală, produsă de Modulele 3-5.
 *
 * Criteriul minim e prezența unui titlu nevid — fără el nu avem ce afișa,
 * indiferent ce alte câmpuri există.
 */
function areFormaReala(result: Record<string, unknown>): boolean {
  return typeof result['titlu'] === 'string' && result['titlu'].trim().length > 0
}

/**
 * Convertește `result`-ul unui job terminat în date de afișat.
 *
 * Args:
 *   result: Câmpul `result` al job-ului, sau `null` dacă lipsește.
 *
 * Returns:
 *   Analiza de afișat, marcată ca reală sau demonstrativă.
 */
export function interpreteazaRezultat(result: Record<string, unknown> | null): AnalizaAfisabila {
  if (!result || !areFormaReala(result)) {
    return { analiza: ANALIZA_DEMO, esteDemonstrativ: true }
  }

  const brut = result as Partial<RezultatAnaliza>

  return {
    esteDemonstrativ: false,
    analiza: {
      titlu: brut.titlu ?? '',
      autor: brut.autor ?? null,
      incredere: typeof brut.incredere === 'number' ? brut.incredere : 0,
      rezumat: brut.rezumat ?? null,
      coperta_url: brut.coperta_url ?? null,
      categorii: Array.isArray(brut.categorii) ? brut.categorii : [],
      rating_mediu: typeof brut.rating_mediu === 'number' ? brut.rating_mediu : null,
      recenzii: Array.isArray(brut.recenzii) ? brut.recenzii : [],
    },
  }
}
