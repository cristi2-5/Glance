/**
 * Text field with a label and an error message.
 *
 * The error usually comes from `ApiError.fieldErrors`, i.e. directly from
 * the backend's validation response, or from local `zod` validation.
 */

import { forwardRef, useState } from 'react'
import { StyleSheet, Text, TextInput, View, type TextInputProps } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

interface InputProps extends Omit<TextInputProps, 'style'> {
  label: string
  /** Error message shown below the field; the outline turns red when present. */
  error?: string | undefined
  /** Help text, shown only when there's no error. */
  hint?: string
}

export const Input = forwardRef<TextInput, InputProps>(function Input(
  { label, error, hint, ...props },
  ref
) {
  const [focused, setFocused] = useState(false)

  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>

      <TextInput
        ref={ref}
        placeholderTextColor={colors.inkFaint}
        {...props}
        onBlur={(e) => {
          setFocused(false)
          props.onBlur?.(e)
        }}
        onFocus={(e) => {
          setFocused(true)
          props.onFocus?.(e)
        }}
        style={[
          styles.field,
          focused ? styles.fieldFocused : null,
          error ? styles.fieldError : null,
        ]}
      />

      {error ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : hint ? (
        <Text style={styles.hintText}>{hint}</Text>
      ) : null}
    </View>
  )
})

const styles = StyleSheet.create({
  container: {
    gap: spacing.xs,
  },
  label: {
    ...typography.label,
    color: colors.inkMuted,
  },
  field: {
    ...typography.body,
    minHeight: 52,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    color: colors.ink,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
  },
  fieldFocused: {
    borderColor: colors.accent,
  },
  fieldError: {
    borderColor: colors.danger,
    backgroundColor: colors.dangerSoft,
  },
  errorText: {
    ...typography.caption,
    color: colors.danger,
  },
  hintText: {
    ...typography.caption,
    color: colors.inkFaint,
  },
})
