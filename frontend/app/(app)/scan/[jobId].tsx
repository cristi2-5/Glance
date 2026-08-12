/**
 * The result of a scan.
 *
 * Polls `GET /jobs/{id}` until the job reaches `done` or `failed`. Shows
 * the recognized title/author (Module 3) and the catalog metadata gathered
 * for it (Module 4). If the backend returns a shape older than this build
 * understands, the screen falls back to demo data — explicitly marked,
 * never slipped in as real.
 */

import { Feather } from '@expo/vector-icons'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { useMemo } from 'react'
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
import { BookCover } from '@/components/book/BookCover'
import { RatingStars } from '@/components/book/RatingStele'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { Screen } from '@/components/ui/Screen'
import { useJob } from '@/features/scan/hooks'
import { interpretResult } from '@/features/scan/mapper'
import { colors, radius, spacing, typography } from '@/theme'
import type { AnalysisResult, SourceReview } from '@/types/api'

/** Readable labels for the content sources. */
const SOURCE_NAME: Record<SourceReview['source'], string> = {
  wikipedia: 'Wikipedia',
  open_library: 'Open Library',
  google_books: 'Google Books',
}

/** Readable captions for how the title/author were recognized. */
const METHOD_CAPTION: Record<AnalysisResult['method'], string> = {
  ocr: 'Read from the cover text',
  vision: 'Recognized visually',
  manual: 'Corrected by you',
}

export default function RezultatScanareScreen() {
  const router = useRouter()
  const { jobId } = useLocalSearchParams<{ jobId: string }>()

  const numericId = Number.parseInt(jobId ?? '', 10)
  const isValidId = Number.isFinite(numericId)

  const { data: job, error } = useJob(isValidId ? numericId : null)

  const interpretation = useMemo(
    () => (job?.status === 'done' ? interpretResult(job.result) : null),
    [job?.status, job?.result]
  )

  if (!isValidId) {
    return (
      <Screen contentStyle={styles.centered}>
        <ErrorBanner message="Invalid job identifier." />
        <Button label="Back" onPress={() => router.replace('/')} variant="secondary" />
      </Screen>
    )
  }

  if (error) {
    const message = error instanceof ApiError ? error.message : 'Could not read the analysis status.'
    return (
      <Screen contentStyle={styles.centered}>
        <ErrorBanner message={message} />
        <Button label="Back to Home" onPress={() => router.replace('/')} variant="secondary" />
      </Screen>
    )
  }

  // The first read, before polling has returned anything.
  if (!job) {
    return <InProgressState status="pending" />
  }

  if (job.status === 'pending' || job.status === 'running') {
    // A job that is running *and* already carries a correction is
    // re-fetching metadata for the title the user just typed — a different
    // wait, worth naming, since the cover has already been read.
    const isRefetchingCorrection = job.status === 'running' && job.result?.['corrected'] === true
    return <InProgressState refetchingCorrection={isRefetchingCorrection} status={job.status} />
  }

  if (job.status === 'failed') {
    return (
      <Screen contentStyle={styles.centered}>
        <Feather color={colors.danger} name="alert-triangle" size={40} />
        <Text style={styles.stateTitle}>Analysis failed</Text>
        <Text style={styles.stateText}>{job.error ?? 'The backend did not give a reason.'}</Text>
        <Button label="Try again" onPress={() => router.replace('/scan/camera')} />
        <Button label="Back to Home" onPress={() => router.replace('/')} variant="ghost" />
      </Screen>
    )
  }

  if (!interpretation) {
    return <InProgressState status="running" />
  }

  const { analysis, isDemo } = interpretation

  return (
    <Screen scrollable>
      <View style={styles.bar}>
        <Pressable
          accessibilityLabel="Back"
          accessibilityRole="button"
          hitSlop={12}
          onPress={() => router.replace('/')}
        >
          <Feather color={colors.ink} name="arrow-left" size={24} />
        </Pressable>
      </View>

      {isDemo ? (
        <View style={styles.demoWarning}>
          <Feather color={colors.accent} name="info" size={16} />
          <Text style={styles.demoText}>
            Demo data. The backend received the image, but returned a result this build doesn&apos;t
            recognize.
          </Text>
        </View>
      ) : null}

      <View style={styles.header}>
        <View style={styles.identity}>
          <BookCover size="hero" title={analysis.title} url={analysis.cover_url} />

          <View style={styles.identityText}>
            <Text style={styles.bookTitle}>{analysis.title}</Text>
            {analysis.author ? <Text style={styles.author}>by {analysis.author}</Text> : null}

            {analysis.average_rating !== null ? (
              <View style={styles.ratingRow}>
                <RatingStars value={analysis.average_rating} />
                {analysis.ratings_count !== null ? (
                  <Text style={styles.ratingCount}>
                    {formatRatingsCount(analysis.ratings_count)}
                  </Text>
                ) : null}
              </View>
            ) : null}
          </View>
        </View>

        <View style={styles.metaRow}>
          {analysis.corrected ? (
            <Text style={styles.correctedCaption}>Corrected by you</Text>
          ) : (
            <Chip
              label={`Confidence ${Math.round(analysis.confidence * 100)}%`}
              tone={analysis.needs_review ? 'warning' : 'accent'}
            />
          )}
        </View>

        {!analysis.corrected ? (
          <Text style={styles.methodCaption}>{METHOD_CAPTION[analysis.method]}</Text>
        ) : null}

        {analysis.categories.length > 0 ? (
          <View style={styles.categories}>
            {analysis.categories.map((category) => (
              <Chip label={category} key={category} />
            ))}
          </View>
        ) : null}

        {!analysis.metadata_found && !isDemo ? <NoMetadataNotice /> : null}

        {analysis.needs_review ? (
          <View style={styles.needsReviewCard}>
            <Feather color={colors.accent} name="help-circle" size={18} />
            <View style={styles.needsReviewText}>
              <Text style={styles.needsReviewTitle}>Not fully sure about this one</Text>
              <Text style={styles.needsReviewBody}>
                The title or author might not be quite right. You can fix it by hand.
              </Text>
            </View>
            <Button
              label="Fix the title"
              onPress={() => router.push(`/scan/correct/${jobId}`)}
              style={styles.needsReviewButton}
              variant="secondary"
            />
          </View>
        ) : null}
      </View>

      {analysis.summary ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>About the book</Text>
          <Text style={styles.summary}>{analysis.summary}</Text>
        </View>
      ) : analysis.description ? (
        // Until Module 5 there is no generated summary, so we show the
        // publisher's blurb — labelled as such, so it isn't mistaken for
        // one.
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>From the publisher</Text>
          <Text style={styles.summary}>{analysis.description}</Text>
          {analysis.source_count > 0 ? (
            <Text style={styles.sourceCaption}>
              {analysis.source_count === 1
                ? '1 source passage gathered. A written summary arrives with the next step.'
                : `${analysis.source_count} source passages gathered. A written summary arrives with the next step.`}
            </Text>
          ) : null}
        </View>
      ) : null}

      {analysis.reviews.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>What the sources say</Text>

          <View style={styles.reviews}>
            {analysis.reviews.map((review) => (
              <ReviewCard key={review.id} review={review} />
            ))}
          </View>
        </View>
      ) : null}

      <Button
        label="Scan another book"
        onPress={() => router.replace('/scan/camera')}
        style={styles.finalButton}
        variant="secondary"
      />
    </Screen>
  )
}

/**
 * Formats a ratings count for display next to the stars.
 *
 * Thousands are abbreviated because the exact figure carries no meaning to
 * a reader — only the order of magnitude does.
 */
function formatRatingsCount(count: number): string {
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1).replace(/\.0$/, '')}k ratings`
  }
  return count === 1 ? '1 rating' : `${count} ratings`
}

/**
 * Shown when no catalog matched the scanned cover.
 *
 * Not an error state: the recognition worked, the book simply isn't in
 * Google Books or Open Library — routine for Romanian editions. Saying so
 * is better than a page that silently lacks a cover, a blurb and a rating,
 * which reads as the app being broken.
 */
function NoMetadataNotice() {
  return (
    <View style={styles.noticeCard}>
      <Feather color={colors.inkMuted} name="book" size={18} />
      <View style={styles.noticeText}>
        <Text style={styles.noticeTitle}>No catalog entry for this one</Text>
        <Text style={styles.noticeBody}>
          We read the cover, but neither Google Books nor Open Library has this edition. That&apos;s
          common for translations and small print runs.
        </Text>
      </View>
    </View>
  )
}

/** The screen shown while the job is in progress. */
function InProgressState({
  status,
  refetchingCorrection = false,
}: {
  status: 'pending' | 'running'
  /** `true` while metadata is being re-gathered for a corrected title. */
  refetchingCorrection?: boolean
}) {
  if (refetchingCorrection) {
    return (
      <Screen contentStyle={styles.centered}>
        <ActivityIndicator color={colors.accent} size="large" />
        <Text style={styles.stateTitle}>Looking that up</Text>
        <Text style={styles.stateText}>
          Finding the cover and details for the title you entered…
        </Text>
      </Screen>
    )
  }

  const message =
    status === 'pending'
      ? 'Image received. Waiting for the analysis to start…'
      : 'Reading the cover and gathering information about the book…'

  return (
    <Screen contentStyle={styles.centered}>
      <ActivityIndicator color={colors.accent} size="large" />
      <Text style={styles.stateTitle}>Analyzing</Text>
      <Text style={styles.stateText}>{message}</Text>
      <Text style={styles.stateNote}>
        On a laptop without a dedicated graphics card, the full step can take up to two minutes.
      </Text>
    </Screen>
  )
}

/** An excerpt from a source, with a link to the original. */
function ReviewCard({ review }: { review: SourceReview }) {
  const hasLink = review.url !== null

  return (
    <Card
      {...(hasLink
        ? {
            onPress: () => {
              void Linking.openURL(review.url as string)
            },
          }
        : {})}
    >
      <View style={styles.reviewHeader}>
        <Chip label={SOURCE_NAME[review.source]} tone="accent" />
        {hasLink ? <Feather color={colors.inkFaint} name="external-link" size={14} /> : null}
      </View>

      <Text style={styles.reviewTitle}>{review.source_title}</Text>
      <Text style={styles.reviewExcerpt}>{review.excerpt}</Text>
    </Card>
  )
}

const styles = StyleSheet.create({
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.md,
  },
  bar: {
    marginBottom: spacing.lg,
  },
  demoWarning: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    padding: spacing.md,
    marginBottom: spacing.xl,
    backgroundColor: colors.accentSoft,
    borderRadius: radius.md,
  },
  demoText: {
    ...typography.caption,
    flex: 1,
    color: colors.accent,
  },
  header: {
    gap: spacing.md,
  },
  identity: {
    flexDirection: 'row',
    gap: spacing.lg,
  },
  identityText: {
    flex: 1,
    gap: spacing.sm,
    justifyContent: 'center',
  },
  bookTitle: {
    ...typography.displayMedium,
    color: colors.ink,
  },
  author: {
    ...typography.body,
    color: colors.inkMuted,
  },
  ratingRow: {
    gap: spacing.xs,
  },
  ratingCount: {
    ...typography.caption,
    color: colors.inkFaint,
  },
  noticeCard: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    padding: spacing.md,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.md,
  },
  noticeText: {
    flex: 1,
    gap: 2,
  },
  noticeTitle: {
    ...typography.label,
    color: colors.ink,
  },
  noticeBody: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  sourceCaption: {
    ...typography.caption,
    color: colors.inkFaint,
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    flexWrap: 'wrap',
  },
  correctedCaption: {
    ...typography.label,
    color: colors.accent,
  },
  methodCaption: {
    ...typography.caption,
    color: colors.inkFaint,
  },
  categories: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  needsReviewCard: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    padding: spacing.md,
    backgroundColor: colors.accentSoft,
    borderRadius: radius.md,
  },
  needsReviewText: {
    flex: 1,
    gap: 2,
  },
  needsReviewTitle: {
    ...typography.label,
    color: colors.ink,
  },
  needsReviewBody: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  needsReviewButton: {
    alignSelf: 'center',
  },
  section: {
    marginTop: spacing.xxl,
    gap: spacing.md,
  },
  sectionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  summary: {
    ...typography.bodyReading,
    color: colors.ink,
  },
  reviews: {
    gap: spacing.md,
  },
  reviewHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  reviewTitle: {
    ...typography.titleCard,
    color: colors.ink,
    marginBottom: spacing.xs,
  },
  reviewExcerpt: {
    ...typography.body,
    color: colors.inkMuted,
  },
  stateTitle: {
    ...typography.displaySmall,
    color: colors.ink,
    textAlign: 'center',
  },
  stateText: {
    ...typography.body,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  stateNote: {
    ...typography.caption,
    color: colors.inkFaint,
    textAlign: 'center',
    maxWidth: 280,
  },
  finalButton: {
    marginTop: spacing.xxl,
  },
})
