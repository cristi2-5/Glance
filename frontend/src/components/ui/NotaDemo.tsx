/**
 * Marcaj pentru ecranele alimentate din mock-uri.
 *
 * Există ca să nu confundăm niciodată date demonstrative cu date reale în
 * timpul dezvoltării. Dispare singur când hook-urile trec pe API real.
 */

import { Feather } from '@expo/vector-icons'
import { StyleSheet, Text, View } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

interface NotaDemoProps {
  /** Ce anume lipsește din backend, pe scurt. */
  mesaj: string
}

export function NotaDemo({ mesaj }: NotaDemoProps) {
  return (
    <View style={styles.container}>
      <Feather color={colors.accent} name="info" size={16} />
      <Text style={styles.text}>{mesaj}</Text>
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
