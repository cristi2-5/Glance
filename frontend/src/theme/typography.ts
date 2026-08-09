/**
 * Scara tipografică Glance.
 *
 * Două familii, cu roluri stricte:
 * - **Fraunces** (serif) — titluri, nume de cărți, citate. Dă tonul editorial.
 * - **Inter** (sans) — tot restul interfeței: butoane, etichete, corp de text.
 *
 * Numele fonturilor trebuie să coincidă exact cu cheile încărcate în
 * `useAppFonts` (`src/theme/fonts.ts`), altfel React Native cade tăcut
 * pe fontul de sistem, fără eroare.
 */

import type { TextStyle } from 'react-native'

export const fontFamily = {
  displayRegular: 'Fraunces_400Regular',
  displayMedium: 'Fraunces_500Medium',
  displaySemiBold: 'Fraunces_600SemiBold',
  displayBold: 'Fraunces_700Bold',
  displayItalic: 'Fraunces_400Regular_Italic',
  bodyRegular: 'Inter_400Regular',
  bodyMedium: 'Inter_500Medium',
  bodySemiBold: 'Inter_600SemiBold',
  bodyBold: 'Inter_700Bold',
} as const

/**
 * Stiluri gata de aplicat pe `<Text>`. Preferă-le în locul combinării
 * manuale de `fontSize` + `fontFamily`, ca scara să rămână consistentă.
 */
export const typography = {
  /** Titlul unui ecran mare (Home, ecranul de rezultat). */
  displayLarge: {
    fontFamily: fontFamily.displayBold,
    fontSize: 34,
    lineHeight: 41,
    letterSpacing: -0.5,
  },
  /** Titlul unei cărți în ecranul de rezultat. */
  displayMedium: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 27,
    lineHeight: 34,
    letterSpacing: -0.3,
  },
  /** Titluri de secțiune ("Recomandări pentru tine"). */
  displaySmall: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 21,
    lineHeight: 28,
    letterSpacing: -0.2,
  },
  /** Titlul unei cărți în listă/card. */
  titleCard: {
    fontFamily: fontFamily.displayMedium,
    fontSize: 17,
    lineHeight: 23,
  },
  /** Corp de text pentru rezumate — generos la lineHeight, se citește mult. */
  bodyReading: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 16,
    lineHeight: 26,
  },
  /** Corp de text standard în interfață. */
  body: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 15,
    lineHeight: 22,
  },
  /** Text secundar: autor, metadate, ajutor sub input-uri. */
  caption: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 13,
    lineHeight: 18,
  },
  /** Etichete de buton. */
  button: {
    fontFamily: fontFamily.bodySemiBold,
    fontSize: 16,
    lineHeight: 20,
  },
  /** Etichete de câmp și titluri de tab. */
  label: {
    fontFamily: fontFamily.bodyMedium,
    fontSize: 13,
    lineHeight: 17,
  },
  /** Text mic, majuscule rărite — pentru eyebrow-uri deasupra titlurilor. */
  overline: {
    fontFamily: fontFamily.bodySemiBold,
    fontSize: 11,
    lineHeight: 14,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
  },
} as const satisfies Record<string, TextStyle>

export type TypographyToken = keyof typeof typography
