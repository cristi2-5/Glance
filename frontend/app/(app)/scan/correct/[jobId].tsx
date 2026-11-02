/**
 * Manual correction screen.
 *
 * Reached from the result screen's "Wrong book?" link (always available on a
 * finished scan) and from the "Fix the title" button on low-confidence ones.
 * Prefilled with the current title/author; saving calls `PATCH /jobs/{id}/correction` and returns to
 * the result screen, which picks up the correction from the shared
 * `['job', jobId]` query cache (see `useCorrectJob`).
 */

import { useLocalSearchParams, useRouter } from 'expo-router'
import { useState } from 'react'
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
import { TitleAuthorForm } from '@/components/book/TitleAuthorForm'
import { BackRow } from '@/components/ui/BackRow'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Screen } from '@/components/ui/Screen'
import { useCorrectJob, useJob } from '@/features/scan/hooks'
import { interpretResult } from '@/features/scan/mapper'
import type { CorrectionData } from '@/features/scan/schema'
import { colors, spacing, typography } from '@/theme'

export default function CorrectResultScreen() {
  const router = useRouter()
  const { jobId } = useLocalSearchParams<{ jobId: string }>()

  const numericId = Number.parseInt(jobId ?? '', 10)
  const isValidId = Number.isFinite(numericId)

  const { data: job, error, isLoading } = useJob(isValidId ? numericId : null)
  const correction = useCorrectJob(isValidId ? numericId : -1)
  const [generalError, setGeneralError] = useState<string | null>(null)

  async function submit(data: CorrectionData) {
    setGeneralError(null)
    try {
      await correction.mutateAsync({
        title: data.title,
        author: data.author.length > 0 ? data.author : null,
      })
      router.back()
    } catch (problem) {
      setGeneralError(problem instanceof ApiError ? problem.message : 'An unexpected error occurred.')
    }
  }

  if (!isValidId) {
    return (
      <Screen contentStyle={styles.centered}>
        <ErrorBanner message="Invalid job identifier." />
        <Button label="Back" onPress={() => router.back()} variant="secondary" />
      </Screen>
    )
  }

  if (isLoading || !job) {
    return (
      <Screen contentStyle={styles.centered}>
        <ActivityIndicator color={colors.accent} size="large" />
      </Screen>
    )
  }

  if (error || job.status !== 'done') {
    const message =
      error instanceof ApiError ? error.message : "This analysis isn't finished yet, so it can't be corrected."
    return (
      <Screen contentStyle={styles.centered}>
        <ErrorBanner message={message} />
        <Button label="Back" onPress={() => router.back()} variant="secondary" />
      </Screen>
    )
  }

  // Prefilled with what is currently shown, so a small typo fix is one edit.
  const { analysis } = interpretResult(job.result)

  return (
    <Screen scrollable>
      <BackRow label="Result" onPress={() => router.back()} />

      <View style={styles.header}>
        <Text style={styles.eyebrow}>Manual correction</Text>
        <Text style={styles.title}>Fix the title</Text>
        <Text style={styles.subtitle}>
          If the book isn&apos;t the right one, correct the title and author below.
        </Text>
      </View>

      <TitleAuthorForm
        authorHint="Leave empty if you don't know the author."
        error={generalError}
        initialAuthor={analysis.author ?? ''}
        initialTitle={analysis.title}
        onSubmit={submit}
        submitLabel="Save correction"
      />
      <Button label="Cancel" onPress={() => router.back()} style={styles.cancel} variant="ghost" />
    </Screen>
  )
}

const styles = StyleSheet.create({
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.md,
  },
  header: {
    marginBottom: spacing.xxl,
    gap: spacing.sm,
  },
  eyebrow: {
    ...typography.overline,
    color: colors.accent,
  },
  title: {
    ...typography.displayLarge,
    color: colors.ink,
  },
  subtitle: {
    ...typography.body,
    color: colors.inkMuted,
  },
  cancel: {
    marginTop: spacing.sm,
  },
})
