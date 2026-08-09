/** Star rating, on a 5-point scale. Also shows the numeric value. */

import { Feather } from '@expo/vector-icons'
import { StyleSheet, Text, View } from 'react-native'

import { colors, spacing, typography } from '@/theme'

interface RatingStarsProps {
  /** The average value, 0-5. */
  value: number
  /** The size of a star in px. */
  size?: number
  showValue?: boolean
}

const STAR_COUNT = 5

export function RatingStars({ value, size = 14, showValue = true }: RatingStarsProps) {
  const roundedValue = Math.round(value)

  return (
    <View accessibilityLabel={`Rating ${value.toFixed(1)} out of 5`} style={styles.container}>
      <View style={styles.stars}>
        {Array.from({ length: STAR_COUNT }, (_, index) => (
          <Feather
            color={index < roundedValue ? colors.amber : colors.borderStrong}
            key={index}
            name="star"
            size={size}
          />
        ))}
      </View>

      {showValue ? <Text style={styles.value}>{value.toFixed(1)}</Text> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  stars: {
    flexDirection: 'row',
    gap: 2,
  },
  value: {
    ...typography.caption,
    color: colors.inkMuted,
  },
})
