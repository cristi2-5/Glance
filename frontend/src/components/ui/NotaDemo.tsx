/**
 * Marker for screens powered by mock data.
 *
 * Exists so we never confuse demo data with real data during development.
 * Disappears on its own once the hooks switch to the real API.
 */

import { Feather } from '@expo/vector-icons'
import { StyleSheet, Text, View } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

interface DemoNoteProps {
  /** What exactly is missing from the backend, briefly. */
  message: string
}

export function DemoNote({ message }: DemoNoteProps) {
  return (
    <View style={styles.container}>
      <Feather color={colors.accent} name="info" size={16} />
      <Text style={styles.text}>{message}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    padding: spacing.md,
    backgroundColor: colors.accentSoft,
    borderRadius: radius.md,
  },
  text: {
    ...typography.caption,
    flex: 1,
    color: colors.accent,
  },
})
