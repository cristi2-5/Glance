/**
 * Hooks for history, preferences and recommendations.
 *
 * **The single mock → real API switch point.** Screens only consume the
 * hooks here; once backend Module 6 is ready, the bodies of the `queryFn`
 * functions are replaced with HTTP calls and `DEMO_DATA` is set to `false`.
 * No screen needs to change.
 */

import { useQuery } from '@tanstack/react-query'

import { DEMO_HISTORY, DEMO_PREFERENCES, DEMO_RECOMMENDATIONS } from '@/mocks/biblioteca'
import type { HistoryEntry, Recommendation, UserPreferences } from '@/types/biblioteca'

/**
 * `true` as long as the data comes from `src/mocks/`. Screens use it to
 * show a visible marker — an unmarked mock is a lie on screen.
 */
export const DEMO_DATA = true

/** Simulates network latency, so loading states are visible during development. */
const MOCK_DELAY_MS = 400

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(value)
    }, MOCK_DELAY_MS)
  })
}

/** The current user's reading history. */
export function useHistory() {
  return useQuery<HistoryEntry[]>({
    queryKey: ['history'],
    queryFn: () => delay(DEMO_HISTORY),
  })
}

/** Favorite genres and authors. */
export function usePreferences() {
  return useQuery<UserPreferences>({
    queryKey: ['preferences'],
    queryFn: () => delay(DEMO_PREFERENCES),
  })
}

/** Personalized recommendations, sorted by score descending. */
export function useRecommendations() {
  return useQuery<Recommendation[]>({
    queryKey: ['recommendations'],
    queryFn: () => delay(DEMO_RECOMMENDATIONS),
  })
}
