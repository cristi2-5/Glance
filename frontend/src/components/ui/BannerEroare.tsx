/**
 * Bandă de eroare pentru mesaje care nu aparțin unui câmp anume
 * (credențiale greșite, backend inaccesibil).
 */

import { Feather } from '@expo/vector-icons'
import { StyleSheet, Text, View } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

interface BannerEroareProps {
  mesaj: string
}

export function BannerEroare({ mesaj }: BannerEroareProps) {
  return (
    <View accessibilityRole="alert" style={styles.container}>
      <Feather color={colors.danger} name="alert-circle" size={18} />
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
    backgroundColor: colors.dangerSoft,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.danger,
  },
  text: {
    ...typography.caption,
    flex: 1,
    color: colors.danger,
  },
})
