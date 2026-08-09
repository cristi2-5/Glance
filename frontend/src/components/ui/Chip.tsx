/** Compact label: genres, categories, status tags. */

import { StyleSheet, Text, View } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

export type ChipTone = 'neutral' | 'accent' | 'warning'

interface ChipProps {
  label: string
  tone?: ChipTone
}

export function Chip({ label, tone = 'neutral' }: ChipProps) {
  return (
    <View style={[styles.base, backgroundStyles[tone]]}>
      <Text style={[styles.text, textStyles[tone]]}>{label}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  base: {
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.pill,
    borderWidth: 1,
  },
  text: {
    ...typography.caption,
    fontFamily: typography.label.fontFamily,
  },
})

const backgroundStyles = StyleSheet.create({
  neutral: { backgroundColor: colors.surfaceMuted, borderColor: colors.border },
  accent: { backgroundColor: colors.accentSoft, borderColor: colors.accentSoft },
  warning: { backgroundColor: colors.dangerSoft, borderColor: colors.danger },
})

const textStyles = StyleSheet.create({
  neutral: { color: colors.inkMuted },
  accent: { color: colors.accent },
  warning: { color: colors.danger },
})
