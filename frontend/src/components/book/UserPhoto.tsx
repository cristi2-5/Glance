/**
 * The reader's own photo of a book, as a small thumbnail.
 *
 * Shown on the book screen next to the catalog cover, because the photo is
 * the one picture of *their* copy. Renders nothing while the authenticated
 * source is not ready, rather than a grey box that looks like a broken image.
 */

import { Image } from 'expo-image'
import { StyleSheet } from 'react-native'

import { type PhotoRef, useLibraryPhoto } from '@/features/library/photo'
import { colors, radius } from '@/theme'

interface UserPhotoProps {
  photoOf: PhotoRef
  /** Used for the accessibility label. */
  title: string
}

export function UserPhoto({ photoOf, title }: UserPhotoProps) {
  const source = useLibraryPhoto(photoOf)

  if (!source) {
    return null
  }

  return (
    <Image
      accessibilityLabel={`Your photo of ${title}`}
      contentFit="cover"
      source={source}
      style={styles.photo}
      transition={200}
    />
  )
}

const styles = StyleSheet.create({
  photo: {
    width: 96,
    height: 144,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceMuted,
  },
})
