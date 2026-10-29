/**
 * A book's card in lists: cover (or a typographic substitute),
 * title, author and metadata.
 */

import { StyleSheet, Text, View } from 'react-native'

import { Card } from '@/components/ui/Card'
import type { PhotoRef } from '@/features/library/photo'
import { colors, spacing, typography } from '@/theme'
import type { LibraryBook } from '@/types/biblioteca'

import { BookCover } from './BookCover'
import { RatingStars } from './RatingStele'

interface BookCardProps {
  book: LibraryBook
  /** Extra row under the metadata — a recommendation's explanation, for example. */
  footer?: string
  onPress?: () => void
  /** The reader's own photo, used as the cover when the catalog has none. */
  photoOf?: PhotoRef | null
}

export function BookCard({ book, footer, onPress, photoOf = null }: BookCardProps) {
  return (
    <Card {...(onPress ? { onPress } : {})} style={styles.card}>
      <View style={styles.row}>
        <BookCover photoOf={photoOf} title={book.title} url={book.cover_url} />

        <View style={styles.details}>
          <Text numberOfLines={2} style={styles.title}>
            {book.title}
          </Text>
          <Text numberOfLines={1} style={styles.author}>
            {book.author}
          </Text>

          {book.average_rating !== null ? (
            <RatingStars size={12} value={book.average_rating} />
          ) : null}

          {footer ? (
            <Text numberOfLines={2} style={styles.footer}>
              {footer}
            </Text>
          ) : null}
        </View>
      </View>
    </Card>
  )
}

const styles = StyleSheet.create({
  card: {
    padding: spacing.md,
  },
  row: {
    flexDirection: 'row',
    gap: spacing.md,
  },
  details: {
    flex: 1,
    gap: spacing.xs,
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
  footer: {
    ...typography.caption,
    color: colors.accent,
    marginTop: 2,
  },
})
