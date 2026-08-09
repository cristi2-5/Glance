/** Ecranul de autentificare. */

import { zodResolver } from '@hookform/resolvers/zod'
import { Link } from 'expo-router'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
import { BannerEroare } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Screen } from '@/components/ui/Screen'
import { schemaLogin, type DateLogin } from '@/features/auth/schema'
import { useAuthStore } from '@/store/authStore'
import { colors, spacing, typography } from '@/theme'

export default function LoginScreen() {
  const intra = useAuthStore((s) => s.intra)
  const [eroareGenerala, setEroareGenerala] = useState<string | null>(null)

  const {
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<DateLogin>({
    resolver: zodResolver(schemaLogin),
    defaultValues: { email: '', password: '' },
  })

  async function trimite(date: DateLogin) {
    setEroareGenerala(null)

    try {
      await intra(date)
      // Redirecționarea o face `(auth)/_layout.tsx` când starea devine
      // „autentificat" — nu navigăm manual, ca să existe o singură sursă
      // de adevăr pentru unde ajunge utilizatorul.
    } catch (eroare) {
      if (eroare instanceof ApiError) {
        const campuri = Object.entries(eroare.eroriCampuri)

        for (const [camp, mesaj] of campuri) {
          if (camp === 'email' || camp === 'password') {
            setError(camp, { message: mesaj })
          }
        }

        if (campuri.length === 0) {
          setEroareGenerala(eroare.message)
        }
        return
      }

      setEroareGenerala('A apărut o eroare neașteptată.')
    }
  }

  return (
    <Screen scrollable contentStyle={styles.continut}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.antet}>
          <Text style={styles.eyebrow}>Glance</Text>
          <Text style={styles.titlu}>Bine ai revenit</Text>
          <Text style={styles.subtitlu}>
            Fotografiază o copertă și află despre ce e cartea, în câteva secunde.
          </Text>
        </View>

        <View style={styles.formular}>
          {eroareGenerala ? <BannerEroare mesaj={eroareGenerala} /> : null}

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
                placeholder="nume@exemplu.ro"
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
                label="Parolă"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="••••••••"
                secureTextEntry
                value={value}
              />
            )}
          />

          <Button
            label="Intră în cont"
            loading={isSubmitting}
            onPress={() => {
              void handleSubmit(trimite)()
            }}
            style={styles.buton}
          />
        </View>

        <View style={styles.subsol}>
          <Text style={styles.textSubsol}>Nu ai încă un cont?</Text>
          <Link href="/register" style={styles.link}>
            Creează unul
          </Link>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  continut: {
    flexGrow: 1,
    justifyContent: 'center',
  },
  antet: {
    marginBottom: spacing.xxl,
    gap: spacing.sm,
  },
  eyebrow: {
    ...typography.overline,
    color: colors.accent,
  },
  titlu: {
    ...typography.displayLarge,
    color: colors.ink,
  },
  subtitlu: {
    ...typography.body,
    color: colors.inkMuted,
  },
  formular: {
    gap: spacing.lg,
  },
  buton: {
    marginTop: spacing.sm,
  },
  subsol: {
    marginTop: spacing.xxl,
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    gap: spacing.xs,
  },
  textSubsol: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  link: {
    ...typography.caption,
    fontFamily: typography.label.fontFamily,
    color: colors.accent,
  },
})
