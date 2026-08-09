/**
 * Spacing, corner radii, and shadows.
 *
 * The spacing scale is based on 4 px. Use the tokens, not literal numbers —
 * otherwise the screens' vertical rhythm quietly falls apart.
 */

import type { ViewStyle } from 'react-native'

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  pill: 999,
} as const

/**
 * Subtle, warm shadows (not blue-gray), suited to a cream background.
 * `elevation` covers Android, the rest covers iOS.
 */
export const shadow = {
  /** Cards at rest. */
  soft: {
    shadowColor: '#2B2118',
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  /** Raised elements: covers, modal sheets. */
  lifted: {
    shadowColor: '#2B2118',
    shadowOpacity: 0.12,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
} as const satisfies Record<string, ViewStyle>
