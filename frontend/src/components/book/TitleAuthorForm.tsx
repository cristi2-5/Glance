/**
 * The title + author form shared by the correction screen and typed search.
 *
 * Both screens ask for exactly the same two fields with the same limits, so
 * the fields, validation and keyboard behaviour live here once: Return on the
 * title moves to the author, Return on the author submits.
 *
 * The parent owns the request (`onSubmit` may be async and throw); this
 * component shows the general error it is given and a loading button while
 * the promise is pending.
 */

import { zodResolver } from '@hookform/resolvers/zod'
import { useEffect, useRef } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { StyleSheet, View, type TextInputInstance } from 'react-native'

import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { correctionSchema, type CorrectionData } from '@/features/scan/schema'
import { space } from '@/theme'

interface TitleAuthorFormProps {
  /** Starting values; changing them resets the form (e.g. once a job loads). */
  initialTitle?: string
  initialAuthor?: string
  submitLabel: string
  /** Called with trimmed values. Rejecting is the parent's to report via `error`. */
  onSubmit: (data: CorrectionData) => Promise<void>
  /** A general (non-field) error to show above the fields. */
  error?: string | null
  authorHint?: string
}

export function TitleAuthorForm({
  initialTitle = '',
  initialAuthor = '',
  submitLabel,
  onSubmit,
  error = null,
  authorHint,
}: TitleAuthorFormProps) {
  const authorRef = useRef<TextInputInstance>(null)

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CorrectionData>({
    resolver: zodResolver(correctionSchema),
    defaultValues: { title: initialTitle, author: initialAuthor },
  })

  useEffect(() => {
    reset({ title: initialTitle, author: initialAuthor })
  }, [initialTitle, initialAuthor, reset])

  function submit() {
    // The button disables itself while loading, the keyboard's Return key
    // does not: without this, Return then the button sends the request twice.
    if (isSubmitting) {
      return
    }
    void handleSubmit(onSubmit)()
  }

  return (
    <View style={styles.form}>
      {error ? <ErrorBanner message={error} /> : null}

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
            onSubmitEditing={() => authorRef.current?.focus()}
            placeholder="Book title"
            returnKeyType="next"
            submitBehavior="submit"
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
            {...(authorHint ? { hint: authorHint } : {})}
            label="Author"
            onBlur={onBlur}
            onChangeText={onChange}
            onSubmitEditing={submit}
            placeholder="Author name"
            ref={authorRef}
            returnKeyType="done"
            value={value}
          />
        )}
      />

      <Button label={submitLabel} loading={isSubmitting} onPress={submit} style={styles.button} />
    </View>
  )
}

const styles = StyleSheet.create({
  form: {
    gap: space[4],
  },
  button: {
    marginTop: space[2],
  },
})
