/** Recomandări personalizate (date demonstrative până la Modulul 6). */

import { ActivityIndicator, StyleSheet, Text, View } from 'react-native'

import { CardCarte } from '@/components/book/CardCarte'
import { NotaDemo } from '@/components/ui/NotaDemo'
import { Screen } from '@/components/ui/Screen'
import { DATE_DEMONSTRATIVE, useRecomandari } from '@/features/library/hooks'
import { colors, spacing, typography } from '@/theme'

export default function RecomandariScreen() {
  const { data: recomandari, isPending } = useRecomandari()

  return (
    <Screen scrollable>
      <View style={styles.antet}>
        <Text style={styles.eyebrow}>Pentru tine</Text>
        <Text style={styles.titlu}>Ce ai putea citi mai departe</Text>
        <Text style={styles.subtitlu}>
          Sugestiile pornesc de la cărțile pe care le-ai scanat și de la notele pe care le-ai dat.
        </Text>
      </View>

      {DATE_DEMONSTRATIVE ? (
        <NotaDemo mesaj="Date demonstrative — recomandările reale sosesc cu Modulul 6 al backendului." />
      ) : null}

      {isPending ? (
        <ActivityIndicator color={colors.accent} style={styles.incarcare} />
      ) : (
        <View style={styles.lista}>
          {recomandari?.map((recomandare) => (
            <CardCarte
              carte={recomandare.carte}
              key={recomandare.id}
              subsol={recomandare.explicatie}
            />
          ))}
        </View>
      )}
    </Screen>
  )
}

const styles = StyleSheet.create({
  antet: {
    gap: spacing.xs,
    marginBottom: spacing.xl,
  },
  eyebrow: {
    ...typography.overline,
    color: colors.accent,
  },
  titlu: {
    ...typography.displayLarge,
    color: colors.ink,
  },
  subtitlu: {
    ...typography.body,
    color: colors.inkMuted,
    marginTop: spacing.xs,
  },
  incarcare: {
    marginTop: spacing.xxl,
  },
  lista: {
    gap: spacing.md,
    marginTop: spacing.xl,
  },
})
