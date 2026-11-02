/**
 * A rotating card of book facts and app tips, for the scan loading screen.
 *
 * A scan takes 10-60 s and the screen used to be a spinner and a sentence.
 * This makes the wait feel less empty without pretending to be progress.
 *
 * Details that matter:
 * - It starts at a random item, so two scans in a row don't look identical.
 * - The fade uses RN's built-in `Animated` with the native driver (no
 *   extra dependency, no JS-thread work). With Reduce Motion on, the text
 *   simply swaps.
 * - Both timers are cleared on unmount: the loading screen is replaced by
 *   the result as soon as polling sees `done`.
 */

import { useEffect, useRef, useState } from 'react'
import { AccessibilityInfo, Animated, StyleSheet, Text } from 'react-native'

import { FACT_LABELS, FACTS } from '@/features/scan/facts'
import { colors, radius, shadow, space, textFit, typography } from '@/theme'

/** How long each item stays on screen. */
const ROTATE_MS = 7000
const FADE_MS = 300

export function DidYouKnow() {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * FACTS.length))
  const opacity = useRef(new Animated.Value(1)).current
  const reduceMotion = useRef(false)

  useEffect(() => {
    let cancelled = false
    void AccessibilityInfo.isReduceMotionEnabled().then((enabled) => {
      if (!cancelled) {
        reduceMotion.current = enabled
      }
    })

    const timer = setInterval(() => {
      if (reduceMotion.current) {
        setIndex((current) => (current + 1) % FACTS.length)
        return
      }
      // Fade out, swap the text while invisible, fade back in.
      Animated.timing(opacity, { toValue: 0, duration: FADE_MS, useNativeDriver: true }).start(
        ({ finished }) => {
          if (!finished || cancelled) {
            return
          }
          setIndex((current) => (current + 1) % FACTS.length)
          Animated.timing(opacity, { toValue: 1, duration: FADE_MS, useNativeDriver: true }).start()
        },
      )
    }, ROTATE_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
      opacity.stopAnimation()
    }
  }, [opacity])

  const item = FACTS[index]
  if (!item) {
    return null
  }

  return (
    <Animated.View style={[styles.card, { opacity }]}>
      <Text {...textFit.micro} style={styles.label}>
        {FACT_LABELS[item.kind]}
      </Text>
      <Text {...textFit.prose} style={styles.text}>
        {item.text}
      </Text>
    </Animated.View>
  )
}

const styles = StyleSheet.create({
  card: {
    alignSelf: 'stretch',
    // Items differ in length; a floor keeps the centred screen from jumping.
    minHeight: space[10] * 4,
    gap: space[2],
    padding: space[4],
    backgroundColor: colors.paperRaised,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    ...shadow.soft,
  },
  label: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  text: {
    ...typography.body,
    color: colors.ink,
  },
})
