/**
 * The reader's own photo of a book, as a small labelled thumbnail.
 *
 * Shown on the book screen next to the catalog cover, because the photo is
 * the one picture of *their* copy. The "YOUR PHOTO" label lives here, not in
 * the parent: the whole block renders nothing while the authenticated source
 * is not ready, and also if the image fails to load (expired token, photo
 * gone), so the screen never shows a caption over a grey box that looks like
 * a broken image.
 */

import { Image } from 'expo-image'
import { useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'

import { type PhotoRef, useLibraryPhoto } from '@/features/library/photo'
import { colors, radius, space, textFit, typography } from '@/theme'

interface UserPhotoProps {
  photoOf: PhotoRef
  /** Used for the accessibility label. */
  title: string
}

export function UserPhoto({ photoOf, title }: UserPhotoProps) {
  const source = useLibraryPhoto(photoOf)
  const [failed, setFailed] = useState(false)

  if (!source || failed) {
    return null
  }

  return (
    <View style={styles.block}>
      <Text {...textFit.micro} style={styles.label}>
        YOUR PHOTO
      </Text>
      <Image
        accessibilityLabel={`Your photo of ${title}`}
        contentFit="cover"
        onError={() => {
          setFailed(true)
        }}
        source={source}
        style={styles.photo}
        transition={200}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  block: {
    gap: space[2],
    marginTop: space[6],
  },
  label: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  photo: {
    width: 96,
    height: 144,
    borderRadius: radius.sm,
    backgroundColor: colors.paperSunken,
  },
})
