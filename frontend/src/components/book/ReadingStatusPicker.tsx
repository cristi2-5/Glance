/**
 * The three reading statuses, as pills.
 *
 * This is the **only** library control on the scan result screen, and
 * deliberately so. Right after photographing a cover the reader has an
 * intention — "I want to read this", "I'm halfway through", "I've read
 * this one" — and nothing else. Asking what they thought of the book at
 * that moment asks for an opinion they have not had time to form; the
 * rating and the journal live on the book screen, where there is
 * something to say.
 *
 * Tapping the active pill clears it back to `scanned`, so a mis-tap is
 * undoable without a separate control.
 */

import { Pressable, StyleSheet, Text, View } from 'react-native'

import { useLibraryEntry, useUpdateLibraryEntry } from '@/features/library/hooks'
import { colors, radius, space, textFit, typography } from '@/theme'
import type { ReadingStatus } from '@/types/biblioteca'

interface ReadingStatusPickerProps {
  /** The cached book. `null` when the scan matched no catalog entry. */
  bookId: number | null
}

/** The statuses offered, in the order a book moves through them. */
const OPTIONS: { value: ReadingStatus; label: string }[] = [
  { value: 'want_to_read', label: 'Want to read' },
  { value: 'reading', label: 'Reading' },
  { value: 'read', label: 'Read' },
]

export function ReadingStatusPicker({ bookId }: ReadingStatusPickerProps) {
  const { data: entry } = useLibraryEntry(bookId)
  const update = useUpdateLibraryEntry()

  if (bookId === null) {
    return null
  }

  const current = entry?.status ?? 'scanned'

  return (
    <View style={styles.row}>
      {OPTIONS.map((option) => {
        const active = current === option.value
        return (
          <Pressable
            accessibilityLabel={option.label}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
            disabled={update.isPending}
            key={option.value}
            onPress={() => {
              update.mutate({
                bookId,
                update: { status: active ? 'scanned' : option.value },
              })
            }}
            style={({ pressed }) => [
              styles.pill,
              active ? styles.pillActive : null,
              pressed ? styles.pressed : null,
            ]}
          >
            <Text {...textFit.chip} style={[styles.label, active ? styles.labelActive : null]}>
              {option.label}
            </Text>
          </Pressable>
        )
      })}
    </View>
  )
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: space[2],
  },
  pill: {
    paddingHorizontal: space[4],
    paddingVertical: space[2],
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.paperSunken,
  },
  pillActive: {
    backgroundColor: colors.espresso,
    borderColor: colors.espresso,
  },
  pressed: {
    opacity: 0.7,
  },
  label: {
    ...typography.label,
    color: colors.inkMuted,
  },
  labelActive: {
    color: colors.inkInverse,
  },
})
