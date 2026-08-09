/** The login screen. */

import { zodResolver } from '@hookform/resolvers/zod'
import { Link } from 'expo-router'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Screen } from '@/components/ui/Screen'
import { loginSchema, type LoginData } from '@/features/auth/schema'
import { useAuthStore } from '@/store/authStore'
import { colors, spacing, typography } from '@/theme'

export default function LoginScreen() {
  const login = useAuthStore((s) => s.login)
  const [generalError, setGeneralError] = useState<string | null>(null)

  const {
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginData>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  })

  async function submit(data: LoginData) {
    setGeneralError(null)

    try {
      await login(data)
      // The redirect is handled by `(auth)/_layout.tsx` once the status
      // becomes "authenticated" — we don't navigate manually, so there's a
      // single source of truth for where the user ends up.
    } catch (error) {
      if (error instanceof ApiError) {
        const fields = Object.entries(error.fieldErrors)

        for (const [field, message] of fields) {
          if (field === 'email' || field === 'password') {
            setError(field, { message })
          }
        }

        if (fields.length === 0) {
          setGeneralError(error.message)
        }
        return
      }

      setGeneralError('An unexpected error occurred.')
    }
  }

  return (
    <Screen scrollable contentStyle={styles.content}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.header}>
          <Text style={styles.eyebrow}>Glance</Text>
          <Text style={styles.title}>Welcome back</Text>
          <Text style={styles.subtitle}>
            Photograph a cover and find out what the book is about, in a few seconds.
          </Text>
        </View>

        <View style={styles.form}>
          {generalError ? <ErrorBanner message={generalError} /> : null}

          <Controller
            control={control}
            name="email"
            render={({ field: { onChange, onBlur, value } }) => (
              <Input
                autoCapitalize="none"
                autoComplete="email"
                error={errors.email?.message}
                keyboardType="email-address"
                label="Email"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="name@example.com"
                value={value}
              />
            )}
          />

          <Controller
            control={control}
            name="password"
            render={({ field: { onChange, onBlur, value } }) => (
              <Input
                autoCapitalize="none"
                autoComplete="current-password"
                error={errors.password?.message}
                label="Password"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="••••••••"
                secureTextEntry
                value={value}
              />
            )}
          />

          <Button
            label="Log in"
            loading={isSubmitting}
            onPress={() => {
              void handleSubmit(submit)()
            }}
            style={styles.button}
          />
        </View>

        <View style={styles.footer}>
          <Text style={styles.footerText}>Don't have an account yet?</Text>
          <Link href="/register" style={styles.link}>
            Create one
          </Link>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  )
}

const styles = StyleSheet.create({
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
  footer: {
    marginTop: spacing.xxl,
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    gap: spacing.xs,
  },
  footerText: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  link: {
    ...typography.caption,
    fontFamily: typography.label.fontFamily,
    color: colors.accent,
  },
})
