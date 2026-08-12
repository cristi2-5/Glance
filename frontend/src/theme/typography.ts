/**
 * The Glance type scale.
 *
 * Two families, with strict roles:
 * - **Fraunces** (serif) — headings, book titles, quotes. Sets the editorial tone.
 * - **Inter** (sans) — everything else: buttons, labels, body copy, micro text.
 *
 * Two properties of the scale are deliberate and load-bearing:
 *
 * 1. **Every `lineHeight` is a multiple of 4**, so text sits on the same
 *    baseline grid as the spacing scale (`space` in `./spacing.ts`).
 * 2. **The scale has gaps** — nothing between 17 and 21, nothing between 32
 *    and 56. The reference set gets its character from refusing intermediate
 *    sizes; filling the gaps in is what makes an interface look generic.
 *
 * The font names must match exactly the keys loaded in `useAppFonts`
 * (`./fonts.ts`), otherwise React Native silently falls back to the system
 * font, with no error.
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
 *
 * Pair each one with the matching preset from `./text.ts` — the type style
 * sets how text *looks*, the text preset sets how it *fits*.
 */
export const typography = {
  /** Hero title. At most once per screen, and never over user-supplied text. */
  displayHero: {
    fontFamily: fontFamily.displayBold,
    fontSize: 56,
    lineHeight: 60,
    letterSpacing: -1.5,
  },
  /** Screen title. */
  displayLarge: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 32,
    lineHeight: 40,
    letterSpacing: -0.6,
  },
  /** A book's title on the result screen — the subject of the page. */
  displayMedium: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 26,
    lineHeight: 32,
    letterSpacing: -0.4,
  },
  /** Section heading ("How it works", "Recommended for you"). */
  displaySmall: {
    fontFamily: fontFamily.displaySemiBold,
    fontSize: 21,
    lineHeight: 28,
    letterSpacing: -0.2,
  },
  /** A book's title in a list or card. */
  titleCard: {
    fontFamily: fontFamily.displayMedium,
    fontSize: 17,
    lineHeight: 24,
  },
  /** Long-form prose: summaries, descriptions. Generous leading. */
  bodyReading: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 16,
    lineHeight: 28,
  },
  /** Standard UI copy. */
  body: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 15,
    lineHeight: 24,
  },
  /** Emphasis inside UI copy. */
  bodyStrong: {
    fontFamily: fontFamily.bodyMedium,
    fontSize: 15,
    lineHeight: 24,
  },
  /** Secondary text: author, metadata, help text under inputs. */
  caption: {
    fontFamily: fontFamily.bodyRegular,
    fontSize: 13,
    lineHeight: 20,
  },
  /** Button labels. */
  button: {
    fontFamily: fontFamily.bodySemiBold,
    fontSize: 15,
    lineHeight: 20,
    letterSpacing: 0.3,
  },
  /** Field labels. */
  label: {
    fontFamily: fontFamily.bodyMedium,
    fontSize: 13,
    lineHeight: 16,
  },
  /** Uppercase eyebrow above a heading; chip and pill labels. */
  overline: {
    fontFamily: fontFamily.bodySemiBold,
    fontSize: 11,
    lineHeight: 16,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
  },
  /** The smallest voice: source attributions, tab labels, step indices. */
  micro: {
    fontFamily: fontFamily.bodyMedium,
    fontSize: 10,
    lineHeight: 12,
    letterSpacing: 1.4,
    textTransform: 'uppercase',
  },
} as const satisfies Record<string, TextStyle>

export type TypographyToken = keyof typeof typography
