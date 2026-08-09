/**
 * The group of public screens (login, register).
 *
 * A user with a valid session has no business here — they're sent into the app.
 */

import { Redirect, Stack } from 'expo-router'

import { useAuthStore } from '@/store/authStore'
import { colors } from '@/theme'

export default function AuthLayout() {
  const status = useAuthStore((s) => s.status)

  if (status === 'authenticated') {
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
