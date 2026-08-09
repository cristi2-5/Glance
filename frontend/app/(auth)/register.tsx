/**
 * Ecranul de înregistrare.
 *
 * Backendul nu întoarce tokenuri la `/auth/register`, deci `creeazaCont`
 * înlănțuiește register + login. Pentru utilizator rămâne un singur pas.
 */

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
import { schemaInregistrare, type DateInregistrare } from '@/features/auth/schema'
import { useAuthStore } from '@/store/authStore'
import { colors, spacing, typography } from '@/theme'

export default function RegisterScreen() {
  const creeazaCont = useAuthStore((s) => s.creeazaCont)
  const [eroareGenerala, setEroareGenerala] = useState<string | null>(null)

  const {
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<DateInregistrare>({
    resolver: zodResolver(schemaInregistrare),
    defaultValues: { email: '', password: '', confirmare: '' },
  })

  async function trimite(date: DateInregistrare) {
    setEroareGenerala(null)

    try {
      await creeazaCont({ email: date.email, password: date.password })
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
          <Text style={styles.titlu}>Creează-ți biblioteca</Text>
          <Text style={styles.subtitlu}>
            Contul păstrează cărțile scanate și îmbunătățește recomandările în timp.
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
                autoComplete="new-password"
                error={errors.password?.message}
                hint="Minim 8 caractere."
                label="Parolă"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="••••••••"
                secureTextEntry
                value={value}
              />
            )}
          />

          <Controller
            control={control}
            name="confirmare"
            render={({ field: { onChange, onBlur, value } }) => (
              <Input
                autoCapitalize="none"
                autoComplete="new-password"
                error={errors.confirmare?.message}
                label="Confirmă parola"
                onBlur={onBlur}
                onChangeText={onChange}
                placeholder="••••••••"
                secureTextEntry
                value={value}
              />
            )}
          />

          <Button
            label="Creează cont"
            loading={isSubmitting}
            onPress={() => {
              void handleSubmit(trimite)()
            }}
            style={styles.buton}
          />
        </View>

        <View style={styles.subsol}>
          <Text style={styles.textSubsol}>Ai deja cont?</Text>
          <Link href="/login" style={styles.link}>
            Autentifică-te
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
