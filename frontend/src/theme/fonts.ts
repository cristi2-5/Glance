/**
 * Loading the app's fonts.
 *
 * The keys in the object passed to `useFonts` become the names usable in
 * `fontFamily`. They must match exactly the values in
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
 * Loads the Fraunces and Inter font families.
 *
 * Returns:
 *   `[loaded, error]` — `loaded` becomes `true` once all fonts are
 *   available. The splash screen must be kept visible until then, otherwise
 *   the UI flashes with the system font.
 */
export function useAppFonts(): [boolean, Error | null] {
  const [loaded, error] = useFonts({
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

  return [loaded, error]
}
