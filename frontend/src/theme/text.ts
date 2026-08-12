/**
 * Text-fitting presets — how each kind of text behaves when it doesn't fit.
 *
 * The governing rule: **truncate by default; shrink to fit only where the
 * container height is fixed and the string is short.** Shrinking a paragraph
 * breaks the baseline grid and produces a different font size on every screen;
 * truncating a button label produces a broken control. Hence the split.
 *
 * These are spreadable `<Text>` props, meant to sit next to a style from
 * `./typography.ts` — the type style says how text *looks*, the preset says
 * how it *fits*:
 *
 * ```tsx
 * <Text {...textFit.bookTitleCard} style={styles.title}>{book.title}</Text>
 * ```
 *
 * `maxFontSizeMultiplier` is set on every preset on purpose. Without it, an OS
 * font-scale setting of 200 % turns a two-line title into a clipped one; with
 * it, accessibility scaling still works, just bounded.
 */

import type { TextProps, ViewStyle } from 'react-native'

type TextFitPreset = Pick<
  TextProps,
  'numberOfLines' | 'ellipsizeMode' | 'adjustsFontSizeToFit' | 'minimumFontScale' | 'maxFontSizeMultiplier'
>

export const textFit = {
  // ------------------------------------------------------------- truncating

  /** Screen title. Two lines is the budget the layouts are built against. */
  screenTitle: {
    numberOfLines: 2,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.3,
  },
  /** A book's title where it is the subject of the screen. */
  bookTitleHero: {
    numberOfLines: 3,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.3,
  },
  /** A book's title in a list or card. */
  bookTitleCard: {
    numberOfLines: 2,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.3,
  },
  /** Author line. Always one line — it sits on a fixed baseline under the title. */
  author: {
    numberOfLines: 1,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.3,
  },
  /** Section heading. */
  sectionTitle: {
    numberOfLines: 1,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.3,
  },
  /** Chip, pill and eyebrow labels. The chip itself needs `flexShrink: 1`. */
  chip: {
    numberOfLines: 1,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.4,
  },
  /** Micro attributions: source names, step indices. */
  micro: {
    numberOfLines: 1,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.4,
  },
  /** A clamped preview of prose, paired with a "Read more" affordance. */
  proseClamped: {
    numberOfLines: 4,
    ellipsizeMode: 'tail',
    maxFontSizeMultiplier: 1.6,
  },

  // ---------------------------------------------------------------- flowing

  /** Full prose: summaries, descriptions. Never truncated — it grows the page. */
  prose: {
    maxFontSizeMultiplier: 1.6,
  },

  // ------------------------------------------------------------- shrinking
  // Only for fixed-height containers holding short, unbounded strings.

  /** Button label. A clipped label is a broken control, so this one shrinks. */
  buttonLabel: {
    numberOfLines: 1,
    adjustsFontSizeToFit: true,
    minimumFontScale: 0.85,
    maxFontSizeMultiplier: 1.2,
  },
  /** Numeric stat value ("8h50m", "128"). Width is unpredictable, height isn't. */
  statValue: {
    numberOfLines: 1,
    adjustsFontSizeToFit: true,
    minimumFontScale: 0.7,
    maxFontSizeMultiplier: 1.2,
  },
  /** Tab bar label — the tightest fixed box in the app. */
  tabLabel: {
    numberOfLines: 1,
    adjustsFontSizeToFit: true,
    minimumFontScale: 0.8,
    maxFontSizeMultiplier: 1.2,
  },
} as const satisfies Record<string, TextFitPreset>

export type TextFitToken = keyof typeof textFit

/**
 * The fix for the single most common overflow bug in React Native.
 *
 * A `<Text>` inside a `flexDirection: 'row'` will *not* shrink on its own — it
 * measures at its natural width and pushes its siblings out of the container,
 * so `numberOfLines` never gets a chance to apply. Wrapping the text column in
 * this style is what makes truncation actually happen.
 *
 * `minWidth: 0` matters as much as `flex: 1`: without it a long unbroken string
 * (a URL, a German compound title) still refuses to shrink below its intrinsic
 * width.
 */
export const textColumn: ViewStyle = {
  flex: 1,
  minWidth: 0,
}
