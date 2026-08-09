/**
 * Grupul de ecrane publice (login, înregistrare).
 *
 * Un utilizator cu sesiune validă nu are ce căuta aici — e trimis în aplicație.
 */

import { Redirect, Stack } from 'expo-router'

import { useAuthStore } from '@/store/authStore'
import { colors } from '@/theme'

export default function AuthLayout() {
  const stare = useAuthStore((s) => s.stare)

  if (stare === 'autentificat') {
    return <Redirect href="/" />
  }

  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    />
  )
}
