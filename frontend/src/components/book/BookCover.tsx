/**
 * A book's cover image, with a typographic fallback.
 *
 * Shared between the list card and the scan result screen so both degrade
 * the same way. When there's no `cover_url` — the catalog had no match, or
 * matched an edition with no thumbnail — we show the title's initial on a
 * warm background rather than an empty gray box, which reads as a broken
 * image rather than a deliberate absence.
 */

import { Image } from 'expo-image'
import { StyleSheet, Text, View } from 'react-native'

import { colors, radius, typography } from '@/theme'

/** `list` sits inside a row of cards; `hero` heads the scan result screen. */
export type CoverSize = 'list' | 'hero'

interface BookCoverProps {
  /** The catalog thumbnail URL, or `null` when there isn't one. */
  url: string | null
  /** Used for the fallback initial and the accessibility label. */
  title: string
  size?: CoverSize
}

export function BookCover({ url, title, size = 'list' }: BookCoverProps) {
  const sizeStyle = size === 'hero' ? styles.hero : styles.list

  if (url) {
    return (
      <Image
        accessibilityLabel={`Cover of ${title}`}
        contentFit="cover"
        source={{ uri: url }}
        style={[styles.base, sizeStyle]}
        transition={200}
      />
    )
  }

  return (
    <View style={[styles.base, sizeStyle, styles.empty]}>
      <Text style={size === 'hero' ? styles.initialHero : styles.initial}>
        {title.charAt(0).toUpperCase()}
      </Text>
    </View>
  )
}

const styles = StyleSheet.create({
  base: {
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceMuted,
  },
  list: {
    width: 56,
    height: 84,
  },
  hero: {
    width: 120,
    height: 180,
  },
  empty: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.border,
  },
  initial: {
    ...typography.displayMedium,
    color: colors.borderStrong,
  },
  initialHero: {
    ...typography.displayLarge,
    color: colors.borderStrong,
  },
})
