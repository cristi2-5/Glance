/**
 * The reader's own rating, as tappable stars.
 *
 * Distinct from `RatingStars`, which renders the **catalog's** average
 * and is read-only. Both appear in this app, sometimes on the same
 * screen, and they mean different things: one is thousands of strangers,
 * the other is you. They are never drawn as the same control — this one
 * is larger, tappable, and always labelled.
 *
 * Tapping the current rating clears it, which is the undo affordance and
 * the reason the backend distinguishes an explicit `null` from an absent
 * field in a partial update.
 */

import { Feather } from '@expo/vector-icons'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { useLibraryEntry, useUpdateLibraryEntry } from '@/features/library/hooks'
import { colors, space, textFit, typography } from '@/theme'

interface RatingInputProps {
  bookId: number
}

const STAR_COUNT = 5

export function RatingInput({ bookId }: RatingInputProps) {
  const { data: entry } = useLibraryEntry(bookId)
  const update = useUpdateLibraryEntry()

  const rating = entry?.rating ?? null

  return (
    <View style={styles.block}>
      <Text {...textFit.micro} style={styles.label}>
        YOUR RATING
      </Text>

      <View style={styles.row}>
        {Array.from({ length: STAR_COUNT }, (_, index) => {
          const value = index + 1
          const filled = rating !== null && value <= rating
          return (
            <Pressable
              accessibilityLabel={`Rate ${value} out of 5`}
              accessibilityRole="button"
              disabled={update.isPending}
              hitSlop={space[2]}
              key={value}
              onPress={() => {
                update.mutate({ bookId, update: { rating: rating === value ? null : value } })
              }}
            >
              <Feather
                color={filled ? colors.highlight : colors.borderStrong}
                name="star"
                size={30}
              />
            </Pressable>
          )
        })}

        {rating === null ? (
          <Text {...textFit.micro} style={styles.hint}>
            NOT RATED
          </Text>
        ) : null}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  block: {
    gap: space[2],
  },
  label: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space[2],
  },
  hint: {
    ...typography.micro,
    color: colors.inkFaint,
    marginLeft: space[2],
  },
})
