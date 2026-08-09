/** Etichetă compactă: genuri, categorii, marcaje de stare. */

import { StyleSheet, Text, View } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

export type ChipTon = 'neutru' | 'accent' | 'avertisment'

interface ChipProps {
  eticheta: string
  ton?: ChipTon
}

export function Chip({ eticheta, ton = 'neutru' }: ChipProps) {
  return (
    <View style={[styles.baza, stylesFundal[ton]]}>
      <Text style={[styles.text, stylesText[ton]]}>{eticheta}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  baza: {
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

const stylesFundal = StyleSheet.create({
  neutru: { backgroundColor: colors.surfaceMuted, borderColor: colors.border },
  accent: { backgroundColor: colors.accentSoft, borderColor: colors.accentSoft },
  avertisment: { backgroundColor: colors.dangerSoft, borderColor: colors.danger },
})

const stylesText = StyleSheet.create({
  neutru: { color: colors.inkMuted },
  accent: { color: colors.accent },
  avertisment: { color: colors.danger },
})
