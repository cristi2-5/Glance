/** The home screen: the starting point of a scan. */

import { Feather } from '@expo/vector-icons'
import * as Haptics from 'expo-haptics'
import { useRouter } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { Screen } from '@/components/ui/Screen'
import { useAuthStore } from '@/store/authStore'
import { colors, radius, shadow, space, textColumn, textFit, typography } from '@/theme'

/** The three steps of the pipeline, as shown under the capture card. */
const STEPS = [
  {
    title: 'Recognition',
    description: "You photograph the cover. The title and author are read from it.",
  },
  {
    title: 'Research',
    description: 'I look up descriptions and critical opinions from open sources.',
  },
  {
    title: 'Synthesis',
    description: 'You get a summary with a reference to the source of each claim.',
  },
] as const

export default function HomeScreen() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)

  const displayName = user?.email.split('@')[0] ?? 'reader'

  function openCamera() {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
    router.push('/scan/camera')
  }

  return (
    // `edges={['top']}` — the tab bar already clears the gesture bar.
    <Screen scrollable edges={['top']}>
      <View style={styles.header}>
        {/* The email prefix is user data of unbounded length: one line, truncated. */}
        <Text {...textFit.micro} style={styles.eyebrow}>
          Hi, {displayName}
        </Text>
        <Text {...textFit.screenTitle} style={styles.title}>
          What book do you have in hand?
        </Text>
      </View>

      <Pressable
        accessibilityHint="Opens the camera to photograph a book cover"
        accessibilityLabel="Scan a cover"
        accessibilityRole="button"
        onPress={openCamera}
        style={({ pressed }) => [styles.captureCard, pressed ? styles.capturePressed : null]}
      >
        <View style={styles.captureBanner}>
          <View style={styles.iconCircle}>
            <Feather color={colors.inkInverse} name="camera" size={26} />
          </View>
        </View>

        <View style={styles.captureBody}>
          <Text {...textFit.sectionTitle} style={styles.captureTitle}>
            Scan a cover
          </Text>
          {/* Left-aligned: this is a sentence, not a label. */}
          <Text {...textFit.prose} style={styles.captureSubtitle}>
            Hold the book straight, with the title visible. I&apos;ll take care of the rest.
          </Text>
        </View>

        <View style={styles.captureFooter}>
          <Text {...textFit.chip} style={styles.captureAction}>
            Open the camera
          </Text>
          <Feather color={colors.ink} name="arrow-right" size={18} />
        </View>
      </Pressable>

      <View style={styles.section}>
        <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
          How it works
        </Text>

        <View style={styles.steps}>
          {STEPS.map((step, index) => (
            <Step
              description={step.description}
              first={index === 0}
              index={index + 1}
              key={step.title}
              title={step.title}
            />
          ))}
        </View>
      </View>
    </Screen>
  )
}

interface StepProps {
  index: number
  title: string
  description: string
  /** The first step skips the separator rule above it. */
  first: boolean
}

function Step({ index, title, description, first }: StepProps) {
  return (
    <View style={[styles.step, first ? null : styles.stepDivided]}>
      <Text {...textFit.micro} style={styles.stepIndex}>
        {String(index).padStart(2, '0')}
      </Text>

      {/* `textColumn` (flex: 1 + minWidth: 0) is what lets the text below
          actually wrap and truncate instead of overflowing the row. */}
      <View style={[textColumn, styles.stepContent]}>
        <Text {...textFit.sectionTitle} style={styles.stepTitle}>
          {title}
        </Text>
        <Text {...textFit.prose} style={styles.stepDescription}>
          {description}
        </Text>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  header: {
    gap: space[2],
    marginBottom: space[7],
  },
  eyebrow: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  title: {
    ...typography.displayLarge,
    color: colors.ink,
  },

  captureCard: {
    backgroundColor: colors.paperRaised,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    overflow: 'hidden',
    ...shadow.soft,
  },
  capturePressed: {
    backgroundColor: colors.paperSunken,
  },
  captureBanner: {
    height: space[10] + space[7],
    backgroundColor: colors.highlight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconCircle: {
    width: space[10],
    height: space[10],
    borderRadius: radius.pill,
    backgroundColor: colors.espresso,
    alignItems: 'center',
    justifyContent: 'center',
  },
  captureBody: {
    padding: space[5],
    gap: space[2],
  },
  captureTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  captureSubtitle: {
    ...typography.body,
    color: colors.inkMuted,
  },
  captureFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: space[3],
    paddingHorizontal: space[5],
    paddingVertical: space[4],
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  captureAction: {
    ...typography.overline,
    color: colors.ink,
    flexShrink: 1,
  },

  section: {
    marginTop: space[8],
    gap: space[4],
  },
  sectionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  steps: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  step: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: space[4],
    paddingVertical: space[4],
  },
  stepDivided: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  stepIndex: {
    ...typography.micro,
    color: colors.inkFaint,
    width: space[6],
    // Optically aligns the index with the first line of the step title.
    paddingTop: space[2],
  },
  stepContent: {
    gap: space[1],
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
