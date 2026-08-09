/**
 * Încărcarea fonturilor aplicației.
 *
 * Cheile din obiectul pasat lui `useFonts` devin numele folosibile în
 * `fontFamily`. Trebuie să coincidă exact cu valorile din
 * `src/theme/typography.ts`.
 */

import {
  Fraunces_400Regular,
  Fraunces_400Regular_Italic,
  Fraunces_500Medium,
  Fraunces_600SemiBold,
  Fraunces_700Bold,
} from '@expo-google-fonts/fraunces'
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
} from '@expo-google-fonts/inter'
import { useFonts } from 'expo-font'

/**
 * Încarcă familiile Fraunces și Inter.
 *
 * Returns:
 *   `[incarcate, eroare]` — `incarcate` devine `true` când toate fonturile
 *   sunt disponibile. Ecranul de splash trebuie ținut vizibil până atunci,
 *   altfel interfața pâlpâie cu fontul de sistem.
 */
export function useAppFonts(): [boolean, Error | null] {
  const [incarcate, eroare] = useFonts({
    Fraunces_400Regular,
    Fraunces_400Regular_Italic,
    Fraunces_500Medium,
    Fraunces_600SemiBold,
    Fraunces_700Bold,
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  })

  return [incarcate, eroare]
}
