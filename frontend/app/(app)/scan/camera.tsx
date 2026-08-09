/**
 * Cover capture.
 *
 * Uses the SDK 57 API: the `CameraView` component with the
 * `useCameraPermissions` hook. After capture, the image is resized locally
 * (see `useAnalyzeCover`) and sent to the backend, and the user is moved to
 * the result screen with the id of the received job.
 */

import { Feather } from '@expo/vector-icons'
import { CameraView, useCameraPermissions } from 'expo-camera'
import * as Haptics from 'expo-haptics'
import { useRouter } from 'expo-router'
import { useRef, useState } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { normalizeError } from '@/api/errors'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Screen } from '@/components/ui/Screen'
import { useAnalyzeCover } from '@/features/scan/hooks'
import { colors, radius, spacing, typography } from '@/theme'

export default function CameraScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const cameraRef = useRef<CameraView>(null)
  const [permission, requestPermission] = useCameraPermissions()
  const [error, setError] = useState<string | null>(null)
  const [capturing, setCapturing] = useState(false)

  const analysis = useAnalyzeCover()
  const busy = capturing || analysis.isPending

  async function takePhoto() {
    if (!cameraRef.current || busy) {
      return
    }

    setError(null)
    setCapturing(true)
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy)

    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 1, exif: false })

      if (!photo) {
        setError('Could not take the photo. Try again.')
        return
      }

      const job = await analysis.mutateAsync({ uri: photo.uri, width: photo.width })
      router.replace(`/scan/${job.job_id}`)
    } catch (problem) {
      setError(normalizeError(problem).message)
    } finally {
      setCapturing(false)
    }
  }

  if (!permission) {
    return (
      <Screen>
        <View style={styles.centered}>
          <ActivityIndicator color={colors.accent} />
        </View>
      </Screen>
    )
  }

  if (!permission.granted) {
    return (
      <Screen contentStyle={styles.centered}>
        <Feather color={colors.inkFaint} name="camera-off" size={40} />
        <Text style={styles.permissionTitle}>I need the camera</Text>
        <Text style={styles.permissionText}>
          Glance only uses the camera to photograph the cover. The image never leaves your local
          network.
        </Text>
        <Button
          label="Allow access"
          onPress={() => {
            void requestPermission()
          }}
          style={styles.permissionButton}
        />
        <Button label="Back" onPress={() => router.back()} variant="ghost" />
      </Screen>
    )
  }

  return (
    <View style={styles.root}>
      <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} />

      <View style={[styles.bar, { paddingTop: insets.top + spacing.md }]}>
        <Pressable
          accessibilityLabel="Close camera"
          accessibilityRole="button"
          hitSlop={12}
          onPress={() => router.back()}
          style={styles.closeButton}
        >
          <Feather color={colors.inkInverse} name="x" size={22} />
        </Pressable>
      </View>

      <View style={styles.guide} pointerEvents="none">
        <View style={styles.frame} />
        <Text style={styles.guideText}>Frame the cover inside the box</Text>
      </View>

      <View style={[styles.shutterZone, { paddingBottom: insets.bottom + spacing.xl }]}>
        {error ? <ErrorBanner message={error} /> : null}

        {analysis.isPending ? <Text style={styles.statusText}>Sending the image…</Text> : null}

        <Pressable
          accessibilityLabel="Photograph the cover"
          accessibilityRole="button"
          accessibilityState={{ disabled: busy, busy }}
          disabled={busy}
          onPress={() => {
            void takePhoto()
          }}
          style={({ pressed }) => [
            styles.shutter,
            pressed && !busy ? styles.shutterPressed : null,
            busy ? styles.shutterInactive : null,
          ]}
        >
          {busy ? (
            <ActivityIndicator color={colors.accent} />
          ) : (
            <View style={styles.shutterCore} />
          )}
        </Pressable>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.cameraBackdrop,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.md,
  },
  permissionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
    textAlign: 'center',
  },
  permissionText: {
    ...typography.body,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  permissionButton: {
    alignSelf: 'stretch',
    marginTop: spacing.md,
  },
  bar: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    paddingHorizontal: spacing.lg,
    zIndex: 2,
  },
  closeButton: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    backgroundColor: colors.overlay,
    alignItems: 'center',
    justifyContent: 'center',
  },
  guide: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.lg,
  },
  frame: {
    width: '72%',
    aspectRatio: 0.66,
    borderWidth: 2,
    borderColor: 'rgba(255, 253, 249, 0.85)',
    borderRadius: radius.md,
  },
  guideText: {
    ...typography.caption,
    color: colors.inkInverse,
    backgroundColor: colors.overlay,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  shutterZone: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    alignItems: 'center',
    gap: spacing.md,
    paddingHorizontal: spacing.xl,
  },
  statusText: {
    ...typography.caption,
    color: colors.inkInverse,
  },
  shutter: {
    width: 76,
    height: 76,
    borderRadius: radius.pill,
    borderWidth: 4,
    borderColor: colors.inkInverse,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shutterPressed: {
    transform: [{ scale: 0.94 }],
  },
  shutterInactive: {
    opacity: 0.6,
  },
  shutterCore: {
    width: 58,
    height: 58,
    borderRadius: radius.pill,
    backgroundColor: colors.inkInverse,
  },
})
