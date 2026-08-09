/** The home screen: the starting point of a scan. */

import { Feather } from '@expo/vector-icons'
import * as Haptics from 'expo-haptics'
import { useRouter } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { Screen } from '@/components/ui/Screen'
import { useAuthStore } from '@/store/authStore'
import { colors, radius, shadow, spacing, typography } from '@/theme'

export default function HomeScreen() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)

  const displayName = user?.email.split('@')[0] ?? 'reader'

  function openCamera() {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
    router.push('/scan/camera')
  }

  return (
    <Screen scrollable>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>Hi, {displayName}</Text>
        <Text style={styles.title}>What book do you have in hand?</Text>
      </View>

      <Pressable
        accessibilityHint="Opens the camera to photograph a book cover"
        accessibilityLabel="Scan a cover"
        accessibilityRole="button"
        onPress={openCamera}
        style={({ pressed }) => [styles.captureCard, pressed ? styles.cardPressed : null]}
      >
        <View style={styles.iconCircle}>
          <Feather color={colors.inkInverse} name="camera" size={30} />
        </View>
        <Text style={styles.captureTitle}>Scan a cover</Text>
        <Text style={styles.captureSubtitle}>
          Hold the book straight, with the title visible. I'll take care of the rest.
        </Text>
      </Pressable>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>How it works</Text>

        <View style={styles.steps}>
          <Step
            description="You photograph the cover. The text is read locally, on your laptop."
            index={1}
            title="Recognition"
          />
          <Step
            description="I look up descriptions and critical opinions from open sources."
            index={2}
            title="Research"
          />
          <Step
            description="You get a summary with a reference to the source of each claim."
            index={3}
            title="Synthesis"
          />
        </View>
      </View>
    </Screen>
  )
}

interface StepProps {
  index: number
  title: string
  description: string
}

function Step({ index, title, description }: StepProps) {
  return (
    <View style={styles.step}>
      <View style={styles.stepNumber}>
        <Text style={styles.stepNumberText}>{index}</Text>
      </View>
      <View style={styles.stepContent}>
        <Text style={styles.stepTitle}>{title}</Text>
        <Text style={styles.stepDescription}>{description}</Text>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  header: {
    gap: spacing.xs,
    marginBottom: spacing.xl,
  },
  eyebrow: {
    ...typography.overline,
    color: colors.accent,
  },
  title: {
    ...typography.displayLarge,
    color: colors.ink,
  },
  captureCard: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.xxl,
    paddingHorizontal: spacing.xl,
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.border,
    ...shadow.lifted,
  },
  cardPressed: {
    backgroundColor: colors.surfaceMuted,
  },
  iconCircle: {
    width: 72,
    height: 72,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  captureTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  captureSubtitle: {
    ...typography.caption,
    color: colors.inkMuted,
    textAlign: 'center',
    maxWidth: 260,
  },
  section: {
    marginTop: spacing.xxl,
    gap: spacing.lg,
  },
  sectionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  steps: {
    gap: spacing.lg,
  },
  step: {
    flexDirection: 'row',
    gap: spacing.md,
    alignItems: 'flex-start',
  },
  stepNumber: {
    width: 28,
    height: 28,
    borderRadius: radius.pill,
    backgroundColor: colors.accentSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepNumberText: {
    ...typography.label,
    color: colors.accent,
  },
  stepContent: {
    flex: 1,
    gap: 2,
  },
  stepTitle: {
    ...typography.titleCard,
    color: colors.ink,
  },
  stepDescription: {
    ...typography.caption,
    color: colors.inkMuted,
  },
})
