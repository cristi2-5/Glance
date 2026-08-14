/** Personalized recommendations (demo data until backend Module 6b). */

import { ActivityIndicator, StyleSheet, Text, View } from 'react-native'

import { BookCard } from '@/components/book/CardCarte'
import { DemoNote } from '@/components/ui/NotaDemo'
import { Screen } from '@/components/ui/Screen'
import { DEMO_RECOMMENDATIONS_DATA, useRecommendations } from '@/features/library/hooks'
import { colors, spacing, typography } from '@/theme'

export default function RecommendationsScreen() {
  const { data: recommendations, isPending } = useRecommendations()

  return (
    <Screen scrollable>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>For you</Text>
        <Text style={styles.title}>What you could read next</Text>
        <Text style={styles.subtitle}>
          Suggestions are based on the books you've scanned and the ratings you've given.
        </Text>
      </View>

      {DEMO_RECOMMENDATIONS_DATA ? (
        <DemoNote message="Demo data — real recommendations arrive with backend Module 6b. Your library, ratings and profile are already real." />
      ) : null}

      {isPending ? (
        <ActivityIndicator color={colors.accent} style={styles.loading} />
      ) : (
        <View style={styles.list}>
          {recommendations?.map((recommendation) => (
            <BookCard
              book={recommendation.book}
              key={recommendation.id}
              footer={recommendation.explanation}
            />
          ))}
        </View>
      )}
    </Screen>
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
  subtitle: {
    ...typography.body,
    color: colors.inkMuted,
    marginTop: spacing.xs,
  },
  loading: {
    marginTop: spacing.xxl,
  },
  list: {
    gap: spacing.md,
    marginTop: spacing.xl,
  },
})
