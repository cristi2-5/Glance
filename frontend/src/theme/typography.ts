/**
 * The Glance type scale.
 *
 * Two families, with strict roles:
 * - **Fraunces** (serif) — headings, book titles, quotes. Sets the editorial tone.
 * - **Inter** (sans) — everything else in the UI: buttons, labels, body copy.
 *
 * The font names must match exactly the keys loaded in `useAppFonts`
 * (`src/theme/fonts.ts`), otherwise React Native silently falls back to the
 * system font, without an error.
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
 * Styles ready to apply to `<Text>`. Prefer these over manually combining
 * `fontSize` + `fontFamily`, so the scale stays consistent.
 */
export const typography = {
  /** Title of a large screen (Home, the result screen). */
  displayLarge: {
    fontFamily: fontFamily.displayBold,
    fontSize: 34,
    lineHeight: 41,
    letterSpacing: -0.5,
  },
  /** A book's title on the result screen. */
  displayMedium: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 27,
    lineHeight: 34,
    letterSpacing: -0.3,
  },
  /** Section titles ("Recommended for you"). */
  displaySmall: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 21,
    lineHeight: 28,
    letterSpacing: -0.2,
  },
  /** A book's title in a list/card. */
  titleCard: {
    fontFamily: fontFamily.displayMedium,
    fontSize: 17,
    lineHeight: 23,
  },
  /** Body copy for summaries — generous lineHeight, meant for extended reading. */
  bodyReading: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 16,
    lineHeight: 26,
  },
  /** Standard UI body copy. */
  body: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 15,
    lineHeight: 22,
  },
  /** Secondary text: author, metadata, help text under inputs. */
  caption: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 13,
    lineHeight: 18,
  },
  /** Button labels. */
  button: {
    fontFamily: fontFamily.bodySemiBold,
    fontSize: 16,
    lineHeight: 20,
  },
  /** Field labels and tab titles. */
  label: {
    fontFamily: fontFamily.bodyMedium,
    fontSize: 13,
    lineHeight: 17,
  },
  /** Small, letter-spaced uppercase text — for eyebrows above headings. */
  overline: {
    fontFamily: fontFamily.bodySemiBold,
    fontSize: 11,
    lineHeight: 14,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
  },
} as const satisfies Record<string, TextStyle>

export type TypographyToken = keyof typeof typography
