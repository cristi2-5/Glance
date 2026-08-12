/**
 * Glance palette — paper ground, espresso ink, mustard highlight.
 *
 * Extracted from the editorial reference set (see
 * `.claude/skills/design-system/SKILL.md`). Two rules govern it:
 *
 * 1. **Chrome is colorless.** Saturation belongs to the content — book
 *    covers, artwork. The interface itself is paper and ink.
 * 2. **The primary action is dark, not colored.** Every call to action in
 *    the references is a neutral-dark pill; the one warm color is a
 *    *highlight*, never a button fill.
 *
 * Never use literal colors in components; import from here.
 */

export const colors = {
  // ---------------------------------------------------------------- ground

  /** Screen background — warm cream paper, not clinical white. */
  paper: '#F4F1E8',
  /** Raised surfaces: cards, sheets. */
  paperRaised: '#FBF9F3',
  /** Recessed surfaces: inputs, chips, grouped rows. */
  paperSunken: '#E9E3D4',

  // ------------------------------------------------------------------- ink

  /** Primary text — warm near-black. */
  ink: '#1E1B16',
  /** Secondary text: authors, metadata, supporting copy. */
  inkMuted: '#5C564C',
  /** Tertiary text: placeholders, counts, eyebrows. */
  inkFaint: '#918A7C',
  /** Text on a dark surface. */
  inkInverse: '#FBF9F3',

  // ---------------------------------------------------------------- action

  /** Primary action — espresso. Button fills, active tab labels. */
  espresso: '#3A342E',
  /** Pressed variant of the primary action. */
  espressoPressed: '#292420',

  // -------------------------------------------------------------- highlight

  /** Mustard. Highlights, active indicators, rating stars — never a button fill. */
  highlight: '#E8C24E',
  /** Tinted background derived from the highlight (badges, soft banners). */
  highlightSoft: '#F7EBC4',

  /** Ink blue — editorial secondary: links, source marks, illustrations. */
  indigo: '#2F4B99',
  /** Tinted background derived from the indigo. */
  indigoSoft: '#E2E7F3',

  // --------------------------------------------------------------- semantic

  success: '#4A7C59',
  danger: '#B3402F',
  dangerSoft: '#F7E4E0',

  // ------------------------------------------------------------- structure

  /** Hairline separators — the references lean on these instead of shadows. */
  border: '#DFD8C6',
  /** Stronger outline: input borders, empty-state frames. */
  borderStrong: '#C9BFA8',

  /** Overlay for modals and the camera. */
  overlay: 'rgba(30, 27, 22, 0.55)',
  /** Background of the camera screen. */
  cameraBackdrop: '#17140F',

  // ----------------------------------------------------- deprecated aliases
  // Kept so screens not yet migrated to the paper system keep compiling and
  // stay readable. Delete each one as its last consumer is migrated.

  /** @deprecated Use `paper`. */
  background: '#F4F1E8',
  /** @deprecated Use `paperRaised`. */
  surface: '#FBF9F3',
  /** @deprecated Use `paperSunken`. */
  surfaceMuted: '#E9E3D4',
  /** @deprecated Use `espresso` — the primary action is dark, not terracotta. */
  accent: '#3A342E',
  /** @deprecated Use `espressoPressed`. */
  accentPressed: '#292420',
  /** @deprecated Use `highlightSoft` or `paperSunken` depending on intent. */
  accentSoft: '#F7EBC4',
  /** @deprecated Use `highlight`. */
  amber: '#E8C24E',
} as const

export type ColorToken = keyof typeof colors
