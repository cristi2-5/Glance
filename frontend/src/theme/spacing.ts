/**
 * Spacing, corner radii, and shadows.
 *
 * The spacing scale is a **4 px baseline grid**, addressed by step number
 * rather than t-shirt size. Numbered steps beat `xl`/`xxl`/`xxxl` for one
 * practical reason: t-shirt names run out exactly where the layout is most
 * generous, and "I need something between `lg` and `xl`" is how magic
 * numbers get into a codebase.
 *
 * Every margin, padding and gap in the app comes from `space`. No literals.
 */

import type { ViewStyle } from 'react-native'

/**
 * The 4 px grid. Index = number of base units.
 *
 * Conventional usage:
 * - `space[1]`–`space[2]` — inside a component (icon↔label, title↔subtitle)
 * - `space[3]`–`space[4]` — between related elements, card padding
 * - `space[6]` — screen gutter
 * - `space[8]` — between sections
 * - `space[10]` — above a hero
 */
export const space = {
  0: 0,
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  7: 32,
  8: 40,
  9: 48,
  10: 64,
} as const

export type SpaceStep = keyof typeof space

/**
 * @deprecated Use `space` with numbered steps.
 *
 * Kept so screens not yet migrated keep compiling — the values are identical,
 * so mixing the two during the migration cannot break the grid. Delete once
 * the last screen is migrated.
 */
export const spacing = {
  xs: space[1],
  sm: space[2],
  md: space[3],
  lg: space[4],
  xl: space[6],
  xxl: space[7],
  xxxl: space[9],
} as const

export const radius = {
  /** Squared off — full-bleed bands, highlight marks. */
  none: 0,
  /** Cover thumbnails. Book covers are near-square in the references. */
  xs: 4,
  sm: 8,
  md: 12,
  /** Cards. */
  lg: 16,
  xl: 24,
  /** Hero sheets and large media blocks. */
  xxl: 28,
  /** Buttons and chips — every call to action in the references is a full pill. */
  pill: 999,
} as const

/**
 * Shadows are deliberately faint: the reference set separates surfaces with
 * hairline borders (`colors.border`) and lets shadow do almost nothing. If a
 * card looks flat, add a border — don't reach for a heavier shadow.
 *
 * `elevation` covers Android, the rest covers iOS.
 */
export const shadow = {
  /** Cards at rest. Pair with a hairline border. */
  soft: {
    shadowColor: '#1E1B16',
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 3 },
    elevation: 1,
  },
  /** Raised elements: covers, sheets. Still subtle by design. */
  lifted: {
    shadowColor: '#1E1B16',
    shadowOpacity: 0.09,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 6 },
    elevation: 4,
  },
} as const satisfies Record<string, ViewStyle>
