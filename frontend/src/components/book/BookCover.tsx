/**
 * A book's cover image, with a typographic fallback.
 *
 * Shared between the list card and the scan result screen so both degrade
 * the same way. When there's no `cover_url` — the catalog had no match, or
 * matched an edition with no thumbnail — we show the title's initial on a
 * warm background rather than an empty gray box, which reads as a broken
 * image rather than a deliberate absence.
 *
 * The fallback chain is catalog cover → **the reader's own photo** (when the
 * library entry has one) → the initial. A book the catalogs never heard of
 * still shows the thing the reader actually photographed.
 *
 * A source that fails to load (an expired token on the photo request, a 404
 * for a cover URL the catalog has since dropped) drops to the next link of
 * the chain instead of leaving an empty box.
 */

import { Image } from 'expo-image'
import { useEffect, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'

import { type PhotoRef, useLibraryPhoto } from '@/features/library/photo'
import { colors, radius, typography } from '@/theme'

/** `list` sits inside a row of cards; `hero` heads the scan result screen. */
export type CoverSize = 'list' | 'hero'

interface BookCoverProps {
  /** The catalog thumbnail URL, or `null` when there isn't one. */
  url: string | null
  /** Used for the fallback initial and the accessibility label. */
  title: string
  size?: CoverSize
  /** The reader's own photo, shown only when there is no catalog cover. */
  photoOf?: PhotoRef | null
}

export function BookCover({ url, title, size = 'list', photoOf = null }: BookCoverProps) {
  const sizeStyle = size === 'hero' ? styles.hero : styles.list
  const photo = useLibraryPhoto(url ? null : photoOf)
  const [coverFailed, setCoverFailed] = useState(false)
  const [photoFailed, setPhotoFailed] = useState(false)

  // A new URL or photo gets its own chance to load.
  useEffect(() => setCoverFailed(false), [url])
  useEffect(() => setPhotoFailed(false), [photo?.uri])

  if (url && !coverFailed) {
    return (
      <Image
        accessibilityLabel={`Cover of ${title}`}
        contentFit="cover"
        onError={() => setCoverFailed(true)}
        source={{ uri: url }}
        style={[styles.base, sizeStyle]}
        transition={200}
      />
    )
  }

  if (photo && !photoFailed) {
    return (
      <Image
        accessibilityLabel={`Your photo of ${title}`}
        contentFit="cover"
        onError={() => setPhotoFailed(true)}
        source={photo}
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
