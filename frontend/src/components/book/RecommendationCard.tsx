/**
 * One suggested book: cover, title, why it is here, and a way to shelve it.
 *
 * Deliberately **not** `BookCard` with a `footer`. A recommendation card
 * carries an action the history card must never grow — the history is a
 * record of what happened, this is an offer — and the explanation is the
 * point of the card rather than a caption under it, so it gets its own
 * weight and its own rule above it.
 *
 * ## The match score is not rendered
 *
 * `Recommendation.score` orders the list and goes no further. "0.73" is
 * not something a reader can act on, and rendering it as "73% match" would
 * dress a cosine distance up as a measurement of taste. The explanation is
 * the honest version of the same information: it names a book they
 * actually rated.
 *
 * ## The card does not open the book screen
 *
 * A recommended book is by construction *not* in the reader's library —
 * that is the filter the backend applies before ranking. The book screen
 * renders from the library entry, so tapping through would land on its
 * "not in your library" state every single time. One offer, one action.
 *
 * ## "Want to read" is the ordinary library write
 *
 * A recommended book is a real, persisted book on the backend, so this is
 * the same `PUT /books/{id}/library` the scan result screen fires — no
 * second write path, and it turns up in the history and the profile
 * counters immediately.
 *
 * The consequence to expect: that mutation invalidates the `'library'`
 * prefix, the suggestion list refetches, and the book is now excluded from
 * it — so the card *leaves the list*. That is correct, and it is why the
 * shelved state is read from this card's own mutation rather than from a
 * `useLibraryEntry` query. Asking the server whether each of a dozen
 * recommendations is in the library would be a dozen requests whose answer
 * is known in advance to be "no", and the card would still blink out from
 * under the reader's finger with nothing to show for it.
 */

import { Feather } from '@expo/vector-icons'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { Card } from '@/components/ui/Card'
import { useUpdateLibraryEntry } from '@/features/library/hooks'
import { colors, radius, space, textFit, typography } from '@/theme'
import type { Recommendation } from '@/types/biblioteca'

import { BookCover } from './BookCover'
import { RatingStars } from './RatingStele'

interface RecommendationCardProps {
  recommendation: Recommendation
}

export function RecommendationCard({ recommendation }: RecommendationCardProps) {
  const { book, explanation } = recommendation
  const update = useUpdateLibraryEntry()

  return (
    <Card style={styles.card}>
      <View style={styles.row}>
        <BookCover title={book.title} url={book.cover_url} />

        <View style={styles.details}>
          <Text {...textFit.bookTitleCard} style={styles.title}>
            {book.title}
          </Text>
          {book.author !== null ? (
            <Text {...textFit.author} style={styles.author}>
              {book.author}
            </Text>
          ) : null}

          {book.average_rating !== null ? (
            <RatingStars size={12} value={book.average_rating} />
          ) : null}
        </View>
      </View>

      <View style={styles.reason}>
        <Feather color={colors.indigo} name="corner-down-right" size={14} />
        <View style={styles.reasonText}>
          <Text {...textFit.prose} style={styles.explanation}>
            {explanation}
          </Text>
        </View>
      </View>

      {update.isSuccess ? (
        <View style={styles.shelved}>
          <Feather color={colors.success} name="check" size={14} />
          <View style={styles.reasonText}>
            <Text {...textFit.chip} style={styles.shelvedLabel}>
              Added to your library
            </Text>
          </View>
        </View>
      ) : (
        <View style={styles.actions}>
          <Pressable
            accessibilityLabel={`Add ${book.title} to want to read`}
            accessibilityRole="button"
            accessibilityState={{ busy: update.isPending }}
            disabled={update.isPending}
            onPress={() => {
              update.mutate({ bookId: book.id, update: { status: 'want_to_read' } })
            }}
            style={({ pressed }) => [
              styles.action,
              pressed ? styles.actionPressed : null,
              update.isPending ? styles.actionInactive : null,
            ]}
          >
            <Text {...textFit.chip} style={styles.actionLabel}>
              Want to read
            </Text>
          </Pressable>

          {update.error ? (
            <View style={styles.reasonText}>
              <Text {...textFit.prose} style={styles.failure}>
                {update.error.message}
              </Text>
            </View>
          ) : null}
        </View>
      )}
    </Card>
  )
}

const styles = StyleSheet.create({
  card: {
    padding: space[4],
    gap: space[3],
  },
  row: {
    flexDirection: 'row',
    gap: space[4],
  },
  // `textColumn`: without `flex: 1, minWidth: 0` a long title pushes out
  // of the row and `numberOfLines` never gets a chance to apply.
  details: {
    flex: 1,
    minWidth: 0,
    gap: space[1],
    justifyContent: 'center',
  },
  title: {
    ...typography.titleCard,
    color: colors.ink,
  },
  author: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  reason: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: space[2],
    paddingTop: space[3],
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  reasonText: {
    flex: 1,
    minWidth: 0,
  },
  explanation: {
    ...typography.caption,
    color: colors.indigo,
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space[3],
  },
  action: {
    backgroundColor: colors.espresso,
    borderRadius: radius.pill,
    paddingHorizontal: space[4],
    paddingVertical: space[2],
  },
  actionPressed: {
    backgroundColor: colors.espressoPressed,
  },
  actionInactive: {
    opacity: 0.5,
  },
  actionLabel: {
    ...typography.label,
    color: colors.inkInverse,
  },
  failure: {
    ...typography.caption,
    color: colors.danger,
  },
  shelved: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space[2],
  },
  shelvedLabel: {
    ...typography.label,
    color: colors.success,
  },
})
