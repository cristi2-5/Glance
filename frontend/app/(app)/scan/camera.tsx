/**
 * Captura coperții.
 *
 * API-ul folosit e cel din SDK 57: componenta `CameraView` cu hook-ul
 * `useCameraPermissions`. După captură, imaginea e redimensionată local
 * (vezi `useAnalizaCoperta`) și trimisă spre backend, iar utilizatorul e
 * mutat pe ecranul de rezultat cu id-ul job-ului primit.
 */

import { Feather } from '@expo/vector-icons'
import { CameraView, useCameraPermissions } from 'expo-camera'
import * as Haptics from 'expo-haptics'
import { useRouter } from 'expo-router'
import { useRef, useState } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { normalizeazaEroare } from '@/api/errors'
import { BannerEroare } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Screen } from '@/components/ui/Screen'
import { useAnalizaCoperta } from '@/features/scan/hooks'
import { colors, radius, spacing, typography } from '@/theme'

export default function CameraScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const cameraRef = useRef<CameraView>(null)
  const [permisiune, cerePermisiune] = useCameraPermissions()
  const [eroare, setEroare] = useState<string | null>(null)
  const [captureaza, setCaptureaza] = useState(false)

  const analiza = useAnalizaCoperta()
  const ocupat = captureaza || analiza.isPending

  async function fotografiaza() {
    if (!cameraRef.current || ocupat) {
      return
    }

    setEroare(null)
    setCaptureaza(true)
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy)

    try {
      const foto = await cameraRef.current.takePictureAsync({ quality: 1, exif: false })

      if (!foto) {
        setEroare('Nu am putut face fotografia. Încearcă din nou.')
        return
      }

      const job = await analiza.mutateAsync({ uri: foto.uri, latime: foto.width })
      router.replace(`/scan/${job.job_id}`)
    } catch (problema) {
      setEroare(normalizeazaEroare(problema).message)
    } finally {
      setCaptureaza(false)
    }
  }

  if (!permisiune) {
    return (
      <Screen>
        <View style={styles.centrat}>
          <ActivityIndicator color={colors.accent} />
        </View>
      </Screen>
    )
  }

  if (!permisiune.granted) {
    return (
      <Screen contentStyle={styles.centrat}>
        <Feather color={colors.inkFaint} name="camera-off" size={40} />
        <Text style={styles.titluPermisiune}>Am nevoie de cameră</Text>
        <Text style={styles.textPermisiune}>
          Glance folosește camera doar ca să fotografieze coperta. Imaginea nu părăsește rețeaua ta
          locală.
        </Text>
        <Button
          label="Permite accesul"
          onPress={() => {
            void cerePermisiune()
          }}
          style={styles.butonPermisiune}
        />
        <Button label="Înapoi" onPress={() => router.back()} variant="ghost" />
      </Screen>
    )
  }

  return (
    <View style={styles.radacina}>
      <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} />

      <View style={[styles.bara, { paddingTop: insets.top + spacing.md }]}>
        <Pressable
          accessibilityLabel="Închide camera"
          accessibilityRole="button"
          hitSlop={12}
          onPress={() => router.back()}
          style={styles.butonInchide}
        >
          <Feather color={colors.inkInverse} name="x" size={22} />
        </Pressable>
      </View>

      <View style={styles.ghidaj} pointerEvents="none">
        <View style={styles.chenar} />
        <Text style={styles.textGhidaj}>Încadrează coperta în chenar</Text>
      </View>

      <View style={[styles.zonaDeclansator, { paddingBottom: insets.bottom + spacing.xl }]}>
        {eroare ? <BannerEroare mesaj={eroare} /> : null}

        {analiza.isPending ? <Text style={styles.textStare}>Trimit imaginea…</Text> : null}

        <Pressable
          accessibilityLabel="Fotografiază coperta"
          accessibilityRole="button"
          accessibilityState={{ disabled: ocupat, busy: ocupat }}
          disabled={ocupat}
          onPress={() => {
            void fotografiaza()
          }}
          style={({ pressed }) => [
            styles.declansator,
            pressed && !ocupat ? styles.declansatorApasat : null,
            ocupat ? styles.declansatorInactiv : null,
          ]}
        >
          {ocupat ? (
            <ActivityIndicator color={colors.accent} />
          ) : (
            <View style={styles.miezDeclansator} />
          )}
        </Pressable>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  radacina: {
    flex: 1,
    backgroundColor: colors.cameraBackdrop,
  },
  centrat: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.md,
  },
  titluPermisiune: {
    ...typography.displaySmall,
    color: colors.ink,
    textAlign: 'center',
  },
  textPermisiune: {
    ...typography.body,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  butonPermisiune: {
    alignSelf: 'stretch',
    marginTop: spacing.md,
  },
  bara: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    paddingHorizontal: spacing.lg,
    zIndex: 2,
  },
  butonInchide: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    backgroundColor: colors.overlay,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ghidaj: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.lg,
  },
  chenar: {
    width: '72%',
    aspectRatio: 0.66,
    borderWidth: 2,
    borderColor: 'rgba(255, 253, 249, 0.85)',
    borderRadius: radius.md,
  },
  textGhidaj: {
    ...typography.caption,
    color: colors.inkInverse,
    backgroundColor: colors.overlay,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  zonaDeclansator: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    alignItems: 'center',
    gap: spacing.md,
    paddingHorizontal: spacing.xl,
  },
  textStare: {
    ...typography.caption,
    color: colors.inkInverse,
  },
  declansator: {
    width: 76,
    height: 76,
    borderRadius: radius.pill,
    borderWidth: 4,
    borderColor: colors.inkInverse,
    alignItems: 'center',
    justifyContent: 'center',
  },
  declansatorApasat: {
    transform: [{ scale: 0.94 }],
  },
  declansatorInactiv: {
    opacity: 0.6,
  },
  miezDeclansator: {
    width: 58,
    height: 58,
    borderRadius: radius.pill,
    backgroundColor: colors.inkInverse,
  },
})
