/**
 * Layout-ul rădăcină: providers, fonturi și restaurarea sesiunii.
 *
 * Splash screen-ul rămâne vizibil până când *ambele* condiții sunt
 * îndeplinite: fonturile s-au încărcat și știm dacă utilizatorul are sesiune.
 * Altfel apar două defecte vizibile — un flash cu fontul de sistem și o
 * clipă de ecran de login pentru un utilizator deja autentificat.
 */

import { QueryClientProvider } from '@tanstack/react-query'
import { Stack } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { StatusBar } from 'expo-status-bar'
import { useEffect } from 'react'
import { StyleSheet } from 'react-native'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { SafeAreaProvider } from 'react-native-safe-area-context'

import { seteazaHandlerSesiuneExpirata } from '@/api/client'
import { queryClient } from '@/lib/queryClient'
import { useAuthStore } from '@/store/authStore'
import { colors, useAppFonts } from '@/theme'

void SplashScreen.preventAutoHideAsync()

export default function RootLayout() {
  const [fonturiIncarcate] = useAppFonts()
  const stare = useAuthStore((s) => s.stare)
  const restaureazaSesiunea = useAuthStore((s) => s.restaureazaSesiunea)
  const invalideazaLocal = useAuthStore((s) => s.invalideazaLocal)

  // Interceptorul HTTP nu poate importa store-ul direct (ar crea o
  // dependență circulară), deci îi predăm handlerul o singură dată.
  useEffect(() => {
    seteazaHandlerSesiuneExpirata(invalideazaLocal)
  }, [invalideazaLocal])

  useEffect(() => {
    void restaureazaSesiunea()
  }, [restaureazaSesiunea])

  const gataDeAfisare = fonturiIncarcate && stare !== 'necunoscuta'

  useEffect(() => {
    if (gataDeAfisare) {
      void SplashScreen.hideAsync()
    }
  }, [gataDeAfisare])

  if (!gataDeAfisare) {
    return null
  }

  return (
    <GestureHandlerRootView style={styles.radacina}>
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
  radacina: { flex: 1 },
})
