/**
 * Manual correction screen.
 *
 * Reached from the result screen's "Fix the title" button, shown when
 * `AnalysisResult.needs_review` is true. Prefilled with the recognized
 * title/author; saving calls `PATCH /jobs/{id}/correction` and returns to
 * the result screen, which picks up the correction from the shared
 * `['job', jobId]` query cache (see `useCorrectJob`).
 */

import { zodResolver } from '@hookform/resolvers/zod'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { useEffect, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { ActivityIndicator, KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Screen } from '@/components/ui/Screen'
import { useCorrectJob, useJob } from '@/features/scan/hooks'
import { interpretResult } from '@/features/scan/mapper'
import { correctionSchema, type CorrectionData } from '@/features/scan/schema'
import { colors, spacing, typography } from '@/theme'

export default function CorrectResultScreen() {
  const router = useRouter()
  const { jobId } = useLocalSearchParams<{ jobId: string }>()

  const numericId = Number.parseInt(jobId ?? '', 10)
  const isValidId = Number.isFinite(numericId)

  const { data: job, error, isLoading } = useJob(isValidId ? numericId : null)
  const correction = useCorrectJob(isValidId ? numericId : -1)
  const [generalError, setGeneralError] = useState<string | null>(null)

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CorrectionData>({
    resolver: zodResolver(correctionSchema),
    defaultValues: { title: '', author: '' },
  })

  useEffect(() => {
    if (job?.status === 'done') {
      const { analysis } = interpretResult(job.result)
      reset({ title: analysis.title, author: analysis.author ?? '' })
    }
  }, [job, reset])

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

  return (
    <Screen scrollable contentStyle={styles.content}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.header}>
          <Text style={styles.eyebrow}>Manual correction</Text>
          <Text style={styles.title}>Fix the title</Text>
          <Text style={styles.subtitle}>
            The cover recognition wasn't fully confident. Correct the title and author below.
          </Text>
        </View>

        <View style={styles.form}>
          {generalError ? <ErrorBanner message={generalError} /> : null}

          <Controller
            control={control}
            name="title"
            render={({ field: { onChange, onBlur, value } }) => (
              <Input
                autoCapitalize="words"
                error={errors.title?.message}
                label="Title"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="Book title"
                value={value}
              />
            )}
          />

          <Controller
            control={control}
            name="author"
            render={({ field: { onChange, onBlur, value } }) => (
              <Input
                autoCapitalize="words"
                error={errors.author?.message}
                hint="Leave empty if the author isn't visible on the cover."
                label="Author"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="Author name"
                value={value}
              />
            )}
          />

          <Button
            label="Save correction"
            loading={isSubmitting}
            onPress={() => {
              void handleSubmit(submit)()
            }}
            style={styles.button}
          />
          <Button label="Cancel" onPress={() => router.back()} variant="ghost" />
        </View>
      </KeyboardAvoidingView>
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
  content: {
    flexGrow: 1,
    justifyContent: 'center',
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
  form: {
    gap: spacing.lg,
  },
  button: {
    marginTop: spacing.sm,
  },
})
