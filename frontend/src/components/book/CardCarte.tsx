/**
 * Cardul unei cărți în liste: copertă (sau un substitut tipografic),
 * titlu, autor și metadate.
 */

import { Image } from 'expo-image'
import { StyleSheet, Text, View } from 'react-native'

import { Card } from '@/components/ui/Card'
import { colors, radius, spacing, typography } from '@/theme'
import type { CarteSumar } from '@/types/biblioteca'

import { RatingStele } from './RatingStele'

interface CardCarteProps {
  carte: CarteSumar
  /** Rând suplimentar sub metadate — explicația unei recomandări, de exemplu. */
  subsol?: string
  onPress?: () => void
}

export function CardCarte({ carte, subsol, onPress }: CardCarteProps) {
  return (
    <Card {...(onPress ? { onPress } : {})} style={styles.card}>
      <View style={styles.rand}>
        <Coperta titlu={carte.titlu} url={carte.coperta_url} />

        <View style={styles.detalii}>
          <Text numberOfLines={2} style={styles.titlu}>
            {carte.titlu}
          </Text>
          <Text numberOfLines={1} style={styles.autor}>
            {carte.autor}
          </Text>

          {carte.rating_mediu !== null ? (
            <RatingStele marime={12} valoare={carte.rating_mediu} />
          ) : null}

          {subsol ? (
            <Text numberOfLines={2} style={styles.subsol}>
              {subsol}
            </Text>
          ) : null}
        </View>
      </View>
    </Card>
  )
}

/**
 * Coperta cărții. Când lipsește URL-ul — cazul obișnuit până la Modulul 4 —
 * afișăm inițiala titlului pe fundal cald, în locul unui pătrat gri gol.
 */
function Coperta({ url, titlu }: { url: string | null; titlu: string }) {
  if (url) {
    return <Image contentFit="cover" source={{ uri: url }} style={styles.coperta} transition={200} />
  }

  return (
    <View style={[styles.coperta, styles.copertaGoala]}>
      <Text style={styles.initiala}>{titlu.charAt(0).toUpperCase()}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    padding: spacing.md,
  },
  rand: {
    flexDirection: 'row',
    gap: spacing.md,
  },
  coperta: {
    width: 56,
    height: 84,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceMuted,
  },
  copertaGoala: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.border,
  },
  initiala: {
    ...typography.displayMedium,
    color: colors.borderStrong,
  },
  detalii: {
    flex: 1,
    gap: spacing.xs,
    justifyContent: 'center',
  },
  titlu: {
    ...typography.titleCard,
    color: colors.ink,
  },
  autor: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  subsol: {
    ...typography.caption,
    color: colors.accent,
    marginTop: 2,
  },
})
