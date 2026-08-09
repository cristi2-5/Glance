/**
 * Butonul standard al aplicației.
 *
 * Trei variante: `primary` (teracotă plin), `secondary` (contur) și
 * `ghost` (doar text). Include feedback haptic la apăsare și o stare de
 * încărcare care blochează apăsările repetate.
 */

import * as Haptics from 'expo-haptics'
import { ActivityIndicator, Pressable, StyleSheet, Text, type ViewStyle } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost'

interface ButtonProps {
  label: string
  onPress: () => void
  variant?: ButtonVariant
  /** Afișează un indicator și blochează apăsarea. */
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
  const inactiv = disabled || loading

  function handlePress() {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
    onPress()
  }

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactiv, busy: loading }}
      disabled={inactiv}
      onPress={handlePress}
      style={({ pressed }) => [
        styles.baza,
        styles[variant],
        pressed && !inactiv ? stylesApasat[variant] : null,
        inactiv ? styles.inactiv : null,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator
          color={variant === 'primary' ? colors.inkInverse : colors.accent}
          size="small"
        />
      ) : (
        <Text style={[styles.eticheta, stylesEticheta[variant]]}>{label}</Text>
      )}
    </Pressable>
  )
}

const styles = StyleSheet.create({
  baza: {
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
  inactiv: {
    opacity: 0.5,
  },
  eticheta: {
    ...typography.button,
  },
})

const stylesApasat = StyleSheet.create({
  primary: { backgroundColor: colors.accentPressed, borderColor: colors.accentPressed },
  secondary: { backgroundColor: colors.surfaceMuted },
  ghost: { opacity: 0.6 },
})

const stylesEticheta = StyleSheet.create({
  primary: { color: colors.inkInverse },
  secondary: { color: colors.ink },
  ghost: { color: colors.accent },
})
