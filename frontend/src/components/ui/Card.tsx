/** Suprafață ridicată, folosită pentru grupuri de conținut. */

import type { ReactNode } from 'react'
import { Pressable, StyleSheet, View, type ViewStyle } from 'react-native'

import { colors, radius, shadow, spacing } from '@/theme'

interface CardProps {
  children: ReactNode
  /** Când e dat, cardul devine apăsabil. */
  onPress?: () => void
  style?: ViewStyle
}

export function Card({ children, onPress, style }: CardProps) {
  if (onPress) {
    return (
      <Pressable
        accessibilityRole="button"
        onPress={onPress}
        style={({ pressed }) => [styles.card, pressed ? styles.apasat : null, style]}
      >
        {children}
      </Pressable>
    )
  }

  return <View style={[styles.card, style]}>{children}</View>
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    ...shadow.soft,
  },
  apasat: {
    backgroundColor: colors.surfaceMuted,
  },
})
