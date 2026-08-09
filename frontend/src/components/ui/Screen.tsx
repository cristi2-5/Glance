/**
 * The base container for every screen.
 *
 * Handles safe areas (notch, gesture bar) and the warm background, so
 * screens don't repeat the same plumbing.
 */

import type { ReactNode } from 'react'
import { ScrollView, StyleSheet, View, type ViewStyle } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { colors, spacing } from '@/theme'

interface ScreenProps {
  children: ReactNode
  /** Makes the content scrollable. For screens with long text or forms. */
  scrollable?: boolean
  /** Removes horizontal padding — useful for lists that touch the edges. */
  edgeToEdge?: boolean
  contentStyle?: ViewStyle
}

export function Screen({
  children,
  scrollable = false,
  edgeToEdge = false,
  contentStyle,
}: ScreenProps) {
  const insets = useSafeAreaInsets()

  const padding: ViewStyle = {
    paddingTop: insets.top + spacing.lg,
    paddingBottom: insets.bottom + spacing.lg,
    paddingHorizontal: edgeToEdge ? 0 : spacing.xl,
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
    backgroundColor: colors.background,
  },
})
