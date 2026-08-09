/**
 * Glance palette — warm paper, espresso ink, terracotta accent.
 *
 * Never use literal colors in components; import from here. The structure
 * is prepared for a future dark theme: components consume `colors`, not the
 * individual constants.
 */

export const colors = {
  /** Screen background — creamy paper, not clinical white. */
  background: '#FBF7F0',
  /** Raised surfaces: cards, modal sheets. */
  surface: '#FFFDF9',
  /** Secondary surface for grouped areas (inputs, chips). */
  surfaceMuted: '#F3EBDE',

  /** Primary text — espresso, softer than pure black. */
  ink: '#2B2118',
  /** Secondary text: subtitles, metadata. */
  inkMuted: '#6B5D50',
  /** Tertiary text: placeholder, footnotes. */
  inkFaint: '#9C8B7A',
  /** Text on an accent background. */
  inkInverse: '#FFFDF9',

  /** Primary accent — terracotta. Primary buttons, active elements. */
  accent: '#C4633F',
  /** Pressed variant of the accent. */
  accentPressed: '#A85234',
  /** Subtle background derived from the accent (badges, highlights). */
  accentSoft: '#F5E3DA',

  /** Amber — rating stars, secondary highlights. */
  amber: '#D9922A',

  /** Semantic states. */
  success: '#4A7C59',
  danger: '#B3402F',
  dangerSoft: '#F7E4E0',

  /** Outlines and separators. */
  border: '#E5DACA',
  borderStrong: '#D2C2AC',

  /** Overlay for modals and the camera. */
  overlay: 'rgba(43, 33, 24, 0.55)',
  /** Background of the camera screen. */
  cameraBackdrop: '#1A140F',
} as const

export type ColorToken = keyof typeof colors
