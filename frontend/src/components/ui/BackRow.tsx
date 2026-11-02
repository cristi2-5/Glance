/**
 * The back affordance shared by the result, book and correction screens:
 * a chevron plus a small uppercase label saying where it goes.
 *
 * One component because each screen used to draw its own (an arrow alone, a
 * chevron with "YOUR BOOKS", a ghost button), so the same gesture looked
 * different on every page. The row is at least 44 pt tall to be a fair tap
 * target.
 */

import { Feather } from '@expo/vector-icons'
import { Pressable, StyleSheet, Text } from 'react-native'

import { colors, space, textFit, typography } from '@/theme'

interface BackRowProps {
  /** Short destination name, shown uppercase. */
  label: string
  onPress: () => void
}

export function BackRow({ label, onPress }: BackRowProps) {
  return (
    <Pressable
      accessibilityLabel={`Back to ${label.toLowerCase()}`}
      accessibilityRole="button"
      hitSlop={space[2]}
      onPress={onPress}
      style={styles.row}
    >
      <Feather color={colors.inkMuted} name="chevron-left" size={20} />
      <Text {...textFit.micro} style={styles.label}>
        {label.toUpperCase()}
      </Text>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: space[1],
    minHeight: space[9] + space[2],
    marginBottom: space[2],
  },
  label: {
    ...typography.micro,
    color: colors.inkMuted,
  },
})
