/**
 * The base container for every screen.
 *
 * Handles safe areas (notch, gesture bar) and the paper background, so screens
 * don't repeat the same plumbing.
 *
 * The `edges` prop exists because tab screens must *not* pad for the bottom
 * inset: the tab bar already sits above the gesture bar, so adding
 * `insets.bottom` again leaves a dead band of paper above the tab bar. Screens
 * pushed as a full-screen stack route (camera, result) do need it.
 */

import type { ReactNode } from 'react'
import { ScrollView, StyleSheet, View, type ViewStyle } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { colors, space } from '@/theme'

type ScreenEdge = 'top' | 'bottom'

interface ScreenProps {
  children: ReactNode
  /** Makes the content scrollable. For screens with long text or forms. */
  scrollable?: boolean
  /** Removes horizontal padding — useful for lists that touch the edges. */
  edgeToEdge?: boolean
  /**
   * Which safe-area insets to absorb. Defaults to both.
   * Tab screens should pass `['top']` — the tab bar covers the bottom.
   */
  edges?: readonly ScreenEdge[]
  contentStyle?: ViewStyle
}

const DEFAULT_EDGES: readonly ScreenEdge[] = ['top', 'bottom']

export function Screen({
  children,
  scrollable = false,
  edgeToEdge = false,
  edges = DEFAULT_EDGES,
  contentStyle,
}: ScreenProps) {
  const insets = useSafeAreaInsets()

  const padding: ViewStyle = {
    paddingTop: (edges.includes('top') ? insets.top : 0) + space[4],
    // Without a bottom inset to absorb, the content still needs breathing room
    // so the last element doesn't butt up against the tab bar.
    paddingBottom: edges.includes('bottom') ? insets.bottom + space[4] : space[8],
    paddingHorizontal: edgeToEdge ? 0 : space[6],
  }

  if (scrollable) {
    return (
      <ScrollView
        style={styles.root}
        contentContainerStyle={[padding, contentStyle]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {children}
      </ScrollView>
    )
  }

  return <View style={[styles.root, padding, contentStyle]}>{children}</View>
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.paper,
  },
})
