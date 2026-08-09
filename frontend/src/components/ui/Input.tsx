/**
 * Câmp de text cu etichetă și mesaj de eroare.
 *
 * Eroarea vine de obicei din `ApiError.eroriCampuri`, adică direct din
 * răspunsul de validare al backendului, sau din validarea locală `zod`.
 */

import { forwardRef, useState } from 'react'
import { StyleSheet, Text, TextInput, View, type TextInputProps } from 'react-native'

import { colors, radius, spacing, typography } from '@/theme'

interface InputProps extends Omit<TextInputProps, 'style'> {
  label: string
  /** Mesaj de eroare afișat sub câmp; conturul devine roșu când e prezent. */
  error?: string | undefined
  /** Text de ajutor, afișat doar când nu există eroare. */
  hint?: string
}

export const Input = forwardRef<TextInput, InputProps>(function Input(
  { label, error, hint, ...props },
  ref
) {
  const [focalizat, setFocalizat] = useState(false)

  return (
    <View style={styles.container}>
      <Text style={styles.eticheta}>{label}</Text>

      <TextInput
        ref={ref}
        placeholderTextColor={colors.inkFaint}
        {...props}
        onBlur={(e) => {
          setFocalizat(false)
          props.onBlur?.(e)
        }}
        onFocus={(e) => {
          setFocalizat(true)
          props.onFocus?.(e)
        }}
        style={[
          styles.camp,
          focalizat ? styles.campFocalizat : null,
          error ? styles.campEroare : null,
        ]}
      />

      {error ? (
        <Text style={styles.textEroare}>{error}</Text>
      ) : hint ? (
        <Text style={styles.textAjutor}>{hint}</Text>
      ) : null}
    </View>
  )
})

const styles = StyleSheet.create({
  container: {
    gap: spacing.xs,
  },
  eticheta: {
    ...typography.label,
    color: colors.inkMuted,
  },
  camp: {
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
  campFocalizat: {
    borderColor: colors.accent,
  },
  campEroare: {
    borderColor: colors.danger,
    backgroundColor: colors.dangerSoft,
  },
  textEroare: {
    ...typography.caption,
    color: colors.danger,
  },
  textAjutor: {
    ...typography.caption,
    color: colors.inkFaint,
  },
})
