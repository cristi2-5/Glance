/**
 * The result of a scan.
 *
 * Polls `GET /jobs/{id}` until the job reaches `done` or `failed`. As long
 * as Modules 3-5 aren't ready, the backend returns a placeholder and the
 * screen shows demo data — explicitly marked, never slipped in as real.
 */

import { Feather } from '@expo/vector-icons'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { useMemo } from 'react'
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
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
    return <InProgressState status={job.status} />
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
            Demo data. The backend received the image, but recognition and synthesis arrive with
            Modules 3-5.
          </Text>
        </View>
      ) : null}

      <View style={styles.header}>
        <Text style={styles.bookTitle}>{analysis.title}</Text>
        {analysis.author ? <Text style={styles.author}>by {analysis.author}</Text> : null}

        <View style={styles.metaRow}>
          {analysis.average_rating !== null ? <RatingStars value={analysis.average_rating} /> : null}
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

/** The screen shown while the job is in progress. */
function InProgressState({ status }: { status: 'pending' | 'running' }) {
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
  bookTitle: {
    ...typography.displayMedium,
    color: colors.ink,
  },
  author: {
    ...typography.body,
    color: colors.inkMuted,
    marginTop: -spacing.sm,
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
