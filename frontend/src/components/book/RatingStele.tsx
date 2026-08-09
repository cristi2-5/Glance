/** Rating cu stele, pe o scară de 5. Afișează și valoarea numerică. */

import { Feather } from '@expo/vector-icons'
import { StyleSheet, Text, View } from 'react-native'

import { colors, spacing, typography } from '@/theme'

interface RatingSteleProps {
  /** Valoarea medie, 0-5. */
  valoare: number
  /** Dimensiunea unei stele în px. */
  marime?: number
  afiseazaValoarea?: boolean
}

const NUMAR_STELE = 5

export function RatingStele({ valoare, marime = 14, afiseazaValoarea = true }: RatingSteleProps) {
  const valoareRotunjita = Math.round(valoare)

  return (
    <View accessibilityLabel={`Rating ${valoare.toFixed(1)} din 5`} style={styles.container}>
      <View style={styles.stele}>
        {Array.from({ length: NUMAR_STELE }, (_, index) => (
          <Feather
            color={index < valoareRotunjita ? colors.amber : colors.borderStrong}
            key={index}
            name="star"
            size={marime}
          />
        ))}
      </View>

      {afiseazaValoarea ? <Text style={styles.valoare}>{valoare.toFixed(1)}</Text> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  stele: {
    flexDirection: 'row',
    gap: 2,
  },
  valoare: {
    ...typography.caption,
    color: colors.inkMuted,
  },
})
