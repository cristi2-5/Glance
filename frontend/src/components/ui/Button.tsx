/**
 * The app's standard button.
 *
 * Three variants: `primary` (solid terracotta), `secondary` (outline), and
 * `ghost` (text only). Includes haptic feedback on press and a loading
 * state that blocks repeated presses.
 */

import * as Haptics from 'expo-haptics'
import { ActivityIndicator, Pressable, StyleSheet, Text, type ViewStyle } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost'

interface ButtonProps {
  label: string
  onPress: () => void
  variant?: ButtonVariant
  /** Shows a spinner and blocks the press. */
  loading?: boolean
  disabled?: boolean
  style?: ViewStyle
}

export function Button({
  label,
  onPress,
  variant = 'primary',
  loading = false,
  disabled = false,
  style,
}: ButtonProps) {
  const inactive = disabled || loading

  function handlePress() {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
    onPress()
  }

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy: loading }}
      disabled={inactive}
      onPress={handlePress}
      style={({ pressed }) => [
        styles.base,
        styles[variant],
        pressed && !inactive ? pressedStyles[variant] : null,
        inactive ? styles.inactive : null,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator
          color={variant === 'primary' ? colors.inkInverse : colors.accent}
          size="small"
        />
      ) : (
        <Text style={[styles.label, labelStyles[variant]]}>{label}</Text>
      )}
    </Pressable>
  )
}

const styles = StyleSheet.create({
  base: {
    minHeight: 52,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.xl,
    borderRadius: radius.md,
    borderWidth: 1,
  },
  primary: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  secondary: {
    backgroundColor: 'transparent',
    borderColor: colors.borderStrong,
  },
  ghost: {
    backgroundColor: 'transparent',
    borderColor: 'transparent',
    minHeight: 44,
  },
  inactive: {
    opacity: 0.5,
  },
  label: {
    ...typography.button,
  },
})

const pressedStyles = StyleSheet.create({
  primary: { backgroundColor: colors.accentPressed, borderColor: colors.accentPressed },
  secondary: { backgroundColor: colors.surfaceMuted },
  ghost: { opacity: 0.6 },
})

const labelStyles = StyleSheet.create({
  primary: { color: colors.inkInverse },
  secondary: { color: colors.ink },
  ghost: { color: colors.accent },
})
