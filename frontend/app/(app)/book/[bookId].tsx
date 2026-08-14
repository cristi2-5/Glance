/**
 * One book, as it stands with this reader: status, rating, and the
 * reading journal.
 *
 * Reached by tapping a book in the profile. This is the reflective half
 * of the app — the scan result is capture (a cover, a summary, an
 * intention), and this is where an opinion accumulates over the days it
 * takes to actually read something.
 *
 * **The book data comes from the library entry**, not a separate book
 * endpoint. `LibraryEntry.book` already carries the cover, title, author
 * and categories, so the screen renders from one request. That also
 * makes the "not in your library" case structural rather than an extra
 * check: no entry means nothing to show, and the screen says so instead
 * of rendering a shell.
 */

import { Feather } from '@expo/vector-icons'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from 'react-native'

import { BookCover } from '@/components/book/BookCover'
import { JournalTimeline } from '@/components/book/JournalTimeline'
import { RatingInput } from '@/components/book/RatingInput'
import { RatingStars } from '@/components/book/RatingStele'
import { ReadingStatusPicker } from '@/components/book/ReadingStatusPicker'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { Screen } from '@/components/ui/Screen'
import { useLibraryEntry, useRemoveLibraryEntry } from '@/features/library/hooks'
import { colors, space, textFit, typography } from '@/theme'

export default function BookScreen() {
  const router = useRouter()
  const { bookId } = useLocalSearchParams<{ bookId: string }>()

  const numericId = Number.parseInt(bookId ?? '', 10)
  const validId = Number.isFinite(numericId) ? numericId : null

  const { data: entry, isPending, error } = useLibraryEntry(validId)
  const remove = useRemoveLibraryEntry()

  function confirmRemove() {
    if (validId === null) {
      return
    }
    Alert.alert(
      'Remove from your library?',
      'Your rating and every journal note for this book go with it.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: () => {
            remove.mutate(validId, {
              onSuccess: () => {
                router.back()
              },
            })
          },
        },
      ],
    )
  }

  if (validId === null) {
    return (
      <Screen>
        <ErrorBanner message="That book link is not valid." />
        <BackButton onPress={() => router.back()} />
      </Screen>
    )
  }

  if (isPending) {
    return (
      <Screen>
        <ActivityIndicator color={colors.espresso} style={styles.loading} />
      </Screen>
    )
  }

  if (error) {
    return (
      <Screen>
        <ErrorBanner message={error.message} />
        <BackButton onPress={() => router.back()} />
      </Screen>
    )
  }

  if (!entry) {
    return (
      <Screen>
        <Card>
          <View style={styles.empty}>
            <Feather color={colors.inkFaint} name="book" size={28} />
            <Text {...textFit.prose} style={styles.emptyText}>
              This book is not in your library. Scan its cover and it will land here.
            </Text>
          </View>
        </Card>
        <BackButton onPress={() => router.back()} />
      </Screen>
    )
  }

  const { book } = entry

  return (
    <Screen scrollable>
      <Pressable
        accessibilityLabel="Back"
        accessibilityRole="button"
        hitSlop={space[2]}
        onPress={() => router.back()}
        style={styles.backRow}
      >
        <Feather color={colors.inkMuted} name="chevron-left" size={20} />
        <Text {...textFit.micro} style={styles.backLabel}>
          YOUR BOOKS
        </Text>
      </Pressable>

      <View style={styles.header}>
        <BookCover size="hero" title={book.title} url={book.cover_url} />

        <View style={styles.identity}>
          <Text {...textFit.bookTitleHero} style={styles.title}>
            {book.title}
          </Text>
          {book.author ? (
            <Text {...textFit.author} style={styles.author}>
              {book.author}
            </Text>
          ) : null}

          {book.average_rating !== null ? (
            <View style={styles.catalogRating}>
              <RatingStars size={12} value={book.average_rating} />
              <Text {...textFit.micro} style={styles.catalogLabel}>
                CATALOG AVERAGE
              </Text>
            </View>
          ) : null}
        </View>
      </View>

      {book.categories.length > 0 ? (
        <View style={styles.categories}>
          {book.categories.map((category) => (
            <Chip key={category} label={category} />
          ))}
        </View>
      ) : null}

      <View style={styles.controls}>
        <ReadingStatusPicker bookId={validId} />
        <RatingInput bookId={validId} />
      </View>

      <Text {...textFit.micro} style={styles.provenance}>
        {describeProvenance(entry.first_scanned_at, entry.scan_count)}
      </Text>

      <View style={styles.journal}>
        <JournalTimeline bookId={validId} />
      </View>

      <Button
        label="Remove from library"
        onPress={confirmRemove}
        style={styles.removeButton}
        variant="secondary"
      />
    </Screen>
  )
}

/** The back affordance used by the screen's error and empty states. */
function BackButton({ onPress }: { onPress: () => void }) {
  return <Button label="Back" onPress={onPress} style={styles.removeButton} variant="secondary" />
}

/**
 * A one-line account of how this book got here.
 *
 * Args:
 *   firstScannedAt: ISO 8601, or `null` for a book never scanned — one
 *     added by writing about it rather than photographing it.
 *   scanCount: How many times the cover was scanned.
 */
function describeProvenance(firstScannedAt: string | null, scanCount: number): string {
  if (firstScannedAt === null) {
    return 'ADDED WITHOUT A SCAN'
  }

  const date = new Date(firstScannedAt)
  const when = Number.isNaN(date.getTime())
    ? ''
    : ` ON ${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }).toUpperCase()}`

  return scanCount > 1 ? `SCANNED ${scanCount} TIMES, FIRST${when}` : `SCANNED${when}`
}

const styles = StyleSheet.create({
  loading: {
    marginTop: space[8],
  },
  backRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space[1],
    marginBottom: space[4],
  },
  backLabel: {
    ...typography.micro,
    color: colors.inkMuted,
  },
  header: {
    flexDirection: 'row',
    gap: space[4],
  },
  identity: {
    // `textColumn`: without `flex: 1, minWidth: 0` a long title pushes out
    // of the row and never truncates.
    flex: 1,
    minWidth: 0,
    gap: space[1],
    justifyContent: 'center',
  },
  title: {
    ...typography.displayMedium,
    color: colors.ink,
  },
  author: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  catalogRating: {
    gap: space[1],
    marginTop: space[1],
  },
  catalogLabel: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  categories: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: space[2],
    marginTop: space[5],
  },
  controls: {
    gap: space[5],
    marginTop: space[6],
  },
  provenance: {
    ...typography.micro,
    color: colors.inkFaint,
    marginTop: space[4],
  },
  journal: {
    marginTop: space[8],
  },
  empty: {
    alignItems: 'flex-start',
    gap: space[3],
    paddingVertical: space[4],
  },
  emptyText: {
    ...typography.body,
    color: colors.inkMuted,
  },
  removeButton: {
    marginTop: space[8],
  },
})
