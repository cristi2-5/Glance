/**
 * The root layout: providers, fonts, and session restoration.
 *
 * The splash screen stays visible until *both* conditions are met: the
 * fonts have loaded and we know whether the user has a session. Otherwise
 * two visible glitches appear — a flash of the system font and a brief
 * login screen for an already-authenticated user.
 */

import { QueryClientProvider } from '@tanstack/react-query'
import { Stack } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { StatusBar } from 'expo-status-bar'
import { useEffect } from 'react'
import { StyleSheet } from 'react-native'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { SafeAreaProvider } from 'react-native-safe-area-context'

import { setSessionExpiredHandler } from '@/api/client'
import { queryClient } from '@/lib/queryClient'
import { useAuthStore } from '@/store/authStore'
import { colors, useAppFonts } from '@/theme'

void SplashScreen.preventAutoHideAsync()

export default function RootLayout() {
  const [fontsLoaded] = useAppFonts()
  const status = useAuthStore((s) => s.status)
  const restoreSession = useAuthStore((s) => s.restoreSession)
  const invalidateLocal = useAuthStore((s) => s.invalidateLocal)

  // The HTTP interceptor can't import the store directly (it would create
  // a circular dependency), so we hand it the handler once.
  useEffect(() => {
    setSessionExpiredHandler(invalidateLocal)
  }, [invalidateLocal])

  useEffect(() => {
    void restoreSession()
  }, [restoreSession])

  const readyToDisplay = fontsLoaded && status !== 'unknown'

  useEffect(() => {
    if (readyToDisplay) {
      void SplashScreen.hideAsync()
    }
  }, [readyToDisplay])

  if (!readyToDisplay) {
    return null
  }

  return (
    <GestureHandlerRootView style={styles.root}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <StatusBar style="dark" />
          <Stack
            screenOptions={{
              headerShown: false,
              contentStyle: { backgroundColor: colors.background },
            }}
          />
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1 },
})
