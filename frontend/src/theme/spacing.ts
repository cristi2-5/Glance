/**
 * Spațiere, raze de colț și umbre.
 *
 * Scara de spațiere e bazată pe 4 px. Folosește tokenii, nu numere literale —
 * altfel ritmul vertical al ecranelor se destramă pe nesimțite.
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
 * Umbre discrete, calde (nu gri-albastru), potrivite pe fundal crem.
 * `elevation` acoperă Android, restul acoperă iOS.
 */
export const shadow = {
  /** Carduri în repaus. */
  soft: {
    shadowColor: '#2B2118',
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  /** Elemente ridicate: coperți, foi modale. */
  lifted: {
    shadowColor: '#2B2118',
    shadowOpacity: 0.12,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
} as const satisfies Record<string, ViewStyle>
