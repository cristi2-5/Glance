/**
 * The generated summary on the scan result screen, with tappable citations.
 *
 * The section owns its own request and its own four states, rather than
 * being handed data by the screen. That is deliberate: a summary is the
 * slowest thing on this page by a wide margin — the first request for a
 * book embeds its entire corpus locally before generating — and blocking
 * the whole screen on it would hide a book we already have the cover,
 * title and rating for.
 *
 * The citation contract, which is the point of the whole module: every
 * sentence shown here is a `SummaryClaim` the backend verified against a
 * retrieved passage, and every `chunk_id` on it resolves to an entry in
 * `reviews`. So each sentence can be tapped to reveal exactly the
 * passage(s) it came from. Nothing is rendered as unattributed prose —
 * if the backend could not ground a statement, it dropped it before
 * sending, and if it could ground nothing at all, `available` is false and
 * this falls back to the publisher's blurb.
 *
 * The fallback stays clearly labelled "From the publisher" in every case
 * where it appears. A publisher's blurb is marketing copy; a RAG summary
 * is sourced description. Letting a reader mistake one for the other is
 * the failure this heading exists to prevent.
 */

import { Feather } from '@expo/vector-icons'
import { useMemo, useState } from 'react'
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View } from 'react-native'

import { Button } from '@/components/ui/Button'
import { useBookSummary } from '@/features/scan/hooks'
import { colors, radius, space, textColumn, textFit, typography } from '@/theme'
import type { BookSummary, SourceReview } from '@/types/api'

/** Readable labels for the content sources. */
const SOURCE_NAME: Record<SourceReview['source'], string> = {
  wikipedia: 'Wikipedia',
  open_library: 'Open Library',
  google_books: 'Google Books',
}

/** Readable labels for the aspects the backend reports as uncovered. */
const ASPECT_NAME: Record<string, string> = {
  premise: 'what happens in it',
  themes: 'its themes and style',
  reception: 'how critics received it',
}

interface SummarySectionProps {
  /** The cached book to summarize; `null` when no catalog matched. */
  bookId: number | null
  /** The publisher's blurb, shown as the labelled fallback. */
  description: string | null
  /**
   * How many passages the backend cached for this book. Zero means the
   * summary would certainly come back unavailable, so we don't spend a
   * request (and up to 90 s of waiting) finding that out.
   */
  sourceCount: number
}

export function SummarySection({ bookId, description, sourceCount }: SummarySectionProps) {
  const canGenerate = bookId !== null && sourceCount > 0
  const { data, error, isPending, refetch, isFetching } = useBookSummary(
    canGenerate ? bookId : null
  )

  if (!canGenerate) {
    return <PublisherFallback description={description} />
  }

  if (isPending) {
    return <SummaryLoading />
  }

  if (error) {
    return (
      <SummaryError
        description={description}
        isRetrying={isFetching}
        onRetry={() => {
          void refetch()
        }}
      />
    )
  }

  if (!data.available) {
    return <PublisherFallback description={description} reason="unavailable" />
  }

  return <CitedSummary summary={data} />
}

/**
 * The real thing: claims as flowing prose, each tappable to its sources.
 *
 * The claims are rendered as nested `<Text>` inside one parent so the
 * summary reads as a paragraph rather than a stack of separate sentences —
 * a list of bullet-like rows would make it look like extracted data
 * instead of something written.
 */
function CitedSummary({ summary }: { summary: BookSummary }) {
  const [activeClaim, setActiveClaim] = useState<number | null>(null)

  // Citation numbers come from the order sources are first cited, which is
  // the order the backend already returns them in.
  const sourceNumbers = useMemo(() => {
    const numbers = new Map<string, number>()
    summary.reviews.forEach((review, index) => numbers.set(review.id, index + 1))
    return numbers
  }, [summary.reviews])

  const activeChunkIds = activeClaim === null ? null : summary.claims[activeClaim]?.chunk_ids

  return (
    <View style={styles.section}>
      <View style={styles.headingRow}>
        <View style={textColumn}>
          <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
            About the book
          </Text>
        </View>
      </View>

      <Text {...textFit.micro} style={styles.eyebrow}>
        WRITTEN FROM THE SOURCES BELOW
      </Text>

      <Text {...textFit.prose} style={styles.prose}>
        {summary.claims.map((claim, index) => {
          const isActive = index === activeClaim
          const markers = claim.chunk_ids
            .map((chunkId) => sourceNumbers.get(chunkId))
            .filter((value): value is number => value !== undefined)

          return (
            <Text
              accessibilityHint="Shows which source this sentence came from"
              accessibilityRole="button"
              key={`${index}-${claim.text.slice(0, 24)}`}
              onPress={() => setActiveClaim(isActive ? null : index)}
              style={isActive ? styles.claimActive : styles.claim}
            >
              {claim.text}
              <Text style={styles.marker}>
                {' '}
                {markers.join(',')}
              </Text>
              {index < summary.claims.length - 1 ? ' ' : ''}
            </Text>
          )
        })}
      </Text>

      <Text {...textFit.micro} style={styles.hint}>
        {activeClaim === null
          ? 'Tap a sentence to see its source'
          : 'Tap again to clear the highlight'}
      </Text>

      {summary.uncovered.length > 0 ? <UncoveredNote aspects={summary.uncovered} /> : null}

      <View style={styles.sources}>
        <Text {...textFit.sectionTitle} style={styles.sourcesTitle}>
          Sources
        </Text>

        {summary.reviews.map((review, index) => (
          <SourceCard
            index={index + 1}
            isHighlighted={activeChunkIds?.includes(review.id) ?? false}
            key={review.id}
            review={review}
          />
        ))}
      </View>
    </View>
  )
}

/**
 * One cited passage.
 *
 * Highlighted when the claim the reader tapped cites it — that link is the
 * whole reason the citation is worth showing rather than just counting.
 */
function SourceCard({
  index,
  isHighlighted,
  review,
}: {
  index: number
  isHighlighted: boolean
  review: SourceReview
}) {
  const hasLink = review.url !== null

  return (
    <View style={[styles.sourceCard, isHighlighted ? styles.sourceCardActive : null]}>
      <View style={styles.sourceHeader}>
        <View style={styles.sourceNumber}>
          <Text style={styles.sourceNumberText}>{index}</Text>
        </View>

        <View style={textColumn}>
          <Text {...textFit.micro} style={styles.sourceName}>
            {SOURCE_NAME[review.source].toUpperCase()}
          </Text>
          <Text {...textFit.author} style={styles.sourceTitle}>
            {review.source_title}
          </Text>
        </View>

        {hasLink ? (
          <Pressable
            accessibilityLabel={`Open the original on ${SOURCE_NAME[review.source]}`}
            accessibilityRole="link"
            hitSlop={12}
            onPress={() => {
              void Linking.openURL(review.url as string)
            }}
          >
            <Feather color={colors.indigo} name="external-link" size={16} />
          </Pressable>
        ) : null}
      </View>

      <Text {...textFit.prose} style={styles.sourceExcerpt}>
        {review.excerpt}
      </Text>

      {review.license ? (
        <Text {...textFit.micro} style={styles.license}>
          {review.license}
        </Text>
      ) : null}
    </View>
  )
}

/**
 * What the sources didn't cover.
 *
 * Shown rather than hidden: a summary that says nothing about a book's
 * reception because no source discussed it is different from one where
 * the reception was dull, and only the first is honest to state. This is
 * the visible half of the anti-hallucination contract.
 */
function UncoveredNote({ aspects }: { aspects: string[] }) {
  const readable = aspects.map((aspect) => ASPECT_NAME[aspect] ?? aspect)
  const list =
    readable.length === 1
      ? readable[0]
      : `${readable.slice(0, -1).join(', ')} or ${readable[readable.length - 1]}`

  return (
    <View style={styles.uncovered}>
      <Feather color={colors.inkFaint} name="info" size={14} />
      <View style={textColumn}>
        <Text {...textFit.prose} style={styles.uncoveredText}>
          The sources didn&apos;t cover {list}, so nothing here says anything about that.
        </Text>
      </View>
    </View>
  )
}

/**
 * The publisher's blurb, always labelled as such.
 *
 * `reason` distinguishes "we asked and there was nothing to summarize"
 * from "there was never anything to ask about", because only the first is
 * worth explaining to the reader.
 */
function PublisherFallback({
  description,
  reason,
}: {
  description: string | null
  reason?: 'unavailable'
}) {
  if (!description) {
    return null
  }

  return (
    <View style={styles.section}>
      <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
        From the publisher
      </Text>
      <Text {...textFit.prose} style={styles.prose}>
        {description}
      </Text>
      <Text {...textFit.prose} style={styles.fallbackNote}>
        {reason === 'unavailable'
          ? "This is the publisher's own description. There wasn't enough written about this " +
            'edition to put a sourced summary together.'
          : "This is the publisher's own description, not a written summary."}
      </Text>
    </View>
  )
}

/** The summary's own loading state — the rest of the screen is already up. */
function SummaryLoading() {
  return (
    <View style={styles.section}>
      <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
        About the book
      </Text>

      <View style={styles.loadingRow}>
        <ActivityIndicator color={colors.espresso} size="small" />
        <View style={textColumn}>
          <Text {...textFit.prose} style={styles.loadingText}>
            Reading the sources and writing a summary…
          </Text>
        </View>
      </View>

      <Text {...textFit.prose} style={styles.loadingNote}>
        The first time a book is summarized, everything written about it is indexed locally. That
        part is slow on a laptop without a graphics card.
      </Text>
    </View>
  )
}

/**
 * A failed summary request.
 *
 * The publisher's blurb still shows underneath, so the reader is not left
 * with an empty section — but the failure is stated rather than disguised
 * as an absence, because a retry may well work.
 */
function SummaryError({
  description,
  isRetrying,
  onRetry,
}: {
  description: string | null
  isRetrying: boolean
  onRetry: () => void
}) {
  return (
    <>
      <View style={styles.section}>
        <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
          About the book
        </Text>

        <View style={styles.errorCard}>
          <Feather color={colors.danger} name="alert-circle" size={18} />
          <View style={textColumn}>
            <Text {...textFit.prose} style={styles.errorText}>
              The summary couldn&apos;t be generated just now.
            </Text>
          </View>
        </View>

        <Button label="Try again" loading={isRetrying} onPress={onRetry} variant="secondary" />
      </View>

      <PublisherFallback description={description} />
    </>
  )
}

const styles = StyleSheet.create({
  section: {
    marginTop: space[7],
    gap: space[3],
  },
  headingRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  sectionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  eyebrow: {
    ...typography.micro,
    color: colors.inkFaint,
    marginTop: -space[2],
  },
  prose: {
    ...typography.bodyReading,
    color: colors.ink,
  },
  claim: {
    color: colors.ink,
  },
  claimActive: {
    color: colors.ink,
    backgroundColor: colors.highlightSoft,
  },
  marker: {
    ...typography.micro,
    color: colors.indigo,
  },
  hint: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  uncovered: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: space[2],
    padding: space[3],
    backgroundColor: colors.paperSunken,
    borderRadius: radius.md,
  },
  uncoveredText: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  sources: {
    marginTop: space[4],
    gap: space[3],
  },
  sourcesTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  sourceCard: {
    padding: space[4],
    gap: space[2],
    backgroundColor: colors.paperRaised,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  sourceCardActive: {
    borderColor: colors.indigo,
    backgroundColor: colors.indigoSoft,
  },
  sourceHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space[3],
  },
  sourceNumber: {
    minWidth: space[6],
    minHeight: space[6],
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radius.pill,
    backgroundColor: colors.indigoSoft,
  },
  sourceNumberText: {
    ...typography.label,
    color: colors.indigo,
  },
  sourceName: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  sourceTitle: {
    ...typography.bodyStrong,
    color: colors.ink,
  },
  sourceExcerpt: {
    ...typography.body,
    color: colors.inkMuted,
  },
  license: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  fallbackNote: {
    ...typography.caption,
    color: colors.inkFaint,
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space[3],
  },
  loadingText: {
    ...typography.body,
    color: colors.inkMuted,
  },
  loadingNote: {
    ...typography.caption,
    color: colors.inkFaint,
  },
  errorCard: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: space[2],
    padding: space[3],
    backgroundColor: colors.dangerSoft,
    borderRadius: radius.md,
  },
  errorText: {
    ...typography.body,
    color: colors.ink,
  },
})
