/**
 * The group of protected screens.
 *
 * The app's access gate: without a valid session, any route here redirects
 * to login. Contains a `Stack`, not `Tabs` directly, because the scanning
 * screens (camera, result) need to cover the whole screen, without the tab bar.
 */

import { Redirect, Stack } from 'expo-router'

import { useAuthStore } from '@/store/authStore'
import { colors } from '@/theme'

export default function AppLayout() {
  const status = useAuthStore((s) => s.status)

  if (status !== 'authenticated') {
    return <Redirect href="/login" />
  }

  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <Stack.Screen name="(tabs)" />
      <Stack.Screen name="book/[bookId]" />
      <Stack.Screen name="scan/camera" options={{ animation: 'slide_from_bottom' }} />
      <Stack.Screen name="scan/[jobId]" />
      <Stack.Screen name="scan/correct/[jobId]" options={{ animation: 'slide_from_bottom' }} />
    </Stack>
  )
}
