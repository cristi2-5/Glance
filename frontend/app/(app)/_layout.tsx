/**
 * Grupul de ecrane protejate.
 *
 * Poarta de acces a aplicației: fără sesiune validă, orice rută de aici
 * redirecționează spre login. Conține un `Stack`, nu direct `Tabs`, pentru că
 * ecranele de scanare (camera, rezultatul) trebuie să acopere tot ecranul,
 * fără bara de tab-uri.
 */

import { Redirect, Stack } from 'expo-router'

import { useAuthStore } from '@/store/authStore'
import { colors } from '@/theme'

export default function AppLayout() {
  const stare = useAuthStore((s) => s.stare)

  if (stare !== 'autentificat') {
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
      <Stack.Screen name="scan/camera" options={{ animation: 'slide_from_bottom' }} />
      <Stack.Screen name="scan/[jobId]" />
    </Stack>
  )
}
