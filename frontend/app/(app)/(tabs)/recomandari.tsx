/**
 * Personalized recommendations — real as of backend Module 6b.
 *
 * The demo note is gone, and with it the last mock in this feature.
 *
 * ## Two empty states, not one
 *
 * The screen never renders a bare "no recommendations". `based_on` tells
 * the two apart and they call for opposite things from the reader:
 *
 * - **`based_on === 0`** — there is nothing to build a profile from yet.
 *   Either nothing has been rated 4+, or the liked books are catalogued so
 *   thinly that nothing could be derived from them. The screen asks for a
 *   rating and points at the profile, where rating happens.
 * - **`based_on > 0` with an empty list** — the profile exists, the
 *   catalogs simply had nothing past what the reader already owns. "Check
 *   back" is the honest line; asking them to rate more would be a lie
 *   about what went wrong.
 *
 * Collapsing them would mean asking a reader who has rated forty books to
 * please rate a book.
 *
 * ## A 503 is not an empty shelf
 *
 * The first fetch after a taste change reaches the catalogs and runs the
 * local embedding model, so it can genuinely fail. That renders as an
 * error with a retry, never as "we found nothing for you" — the same
 * distinction the summary section draws, and for the same reason: one of
 * those is a fact about the reader's library and the other is a fact about
 * the laptop.
 */

import { Feather } from '@expo/vector-icons'
import { useRouter } from 'expo-router'
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native'

import { RecommendationCard } from '@/components/book/RecommendationCard'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Screen } from '@/components/ui/Screen'
import { useRecommendations } from '@/features/library/hooks'
import { colors, space, textFit, typography } from '@/theme'

export default function RecommendationsScreen() {
  const router = useRouter()
  const { data, isPending, error, refetch, isFetching } = useRecommendations()

  const recommendations = data?.recommendations ?? []
  const basedOn = data?.based_on ?? 0

  return (
    // `edges={['top']}` — the tab bar already clears the gesture bar.
    <Screen scrollable edges={['top']}>
      <View style={styles.header}>
        <Text {...textFit.chip} style={styles.eyebrow}>
          FOR YOU
        </Text>
        <Text {...textFit.screenTitle} style={styles.title}>
          What you could read next
        </Text>
        <Text {...textFit.prose} style={styles.subtitle}>
          {basedOn > 0
            ? `Built from the ${basedOn} book${basedOn === 1 ? '' : 's'} you rated 4 or higher.`
            : 'Suggestions come from the books you rate highly.'}
        </Text>
      </View>

      {error ? (
        <View style={styles.state}>
          <ErrorBanner message={error.message} />
          <Button
            label="Try again"
            loading={isFetching}
            onPress={() => {
              void refetch()
            }}
            variant="secondary"
          />
        </View>
      ) : isPending ? (
        <View style={styles.state}>
          <ActivityIndicator color={colors.espresso} />
          <Text {...textFit.prose} style={styles.waiting}>
            Looking through the catalogs for books close to the ones you liked. The first run
            takes a few seconds.
          </Text>
        </View>
      ) : recommendations.length > 0 ? (
        <View style={styles.list}>
          {recommendations.map((recommendation) => (
            <RecommendationCard
              key={recommendation.book.id}
              recommendation={recommendation}
            />
          ))}
        </View>
      ) : basedOn === 0 ? (
        <Card>
          <View style={styles.empty}>
            <Feather color={colors.inkFaint} name="star" size={28} />
            <Text {...textFit.prose} style={styles.emptyText}>
              Nothing to go on yet. Rate a book 4 stars or higher and suggestions built from it
              will show up here.
            </Text>
            <Button
              label="Go to your books"
              onPress={() => {
                router.push('/profil')
              }}
              variant="secondary"
            />
          </View>
        </Card>
      ) : (
        <Card>
          <View style={styles.empty}>
            <Feather color={colors.inkFaint} name="compass" size={28} />
            <Text {...textFit.prose} style={styles.emptyText}>
              Nothing new to suggest right now — the catalogs turned up only books you already
              have. Rate a few more and check back.
            </Text>
            <Button
              label="Check again"
              loading={isFetching}
              onPress={() => {
                void refetch()
              }}
              variant="secondary"
            />
          </View>
        </Card>
      )}
    </Screen>
  )
}

const styles = StyleSheet.create({
  header: {
    gap: space[1],
    marginBottom: space[7],
  },
  eyebrow: {
    ...typography.overline,
    color: colors.inkFaint,
  },
  title: {
    ...typography.displayLarge,
    color: colors.ink,
  },
  // Left-aligned: this wraps to two lines, and a centred multi-line
  // paragraph is a bug in this design system.
  subtitle: {
    ...typography.body,
    color: colors.inkMuted,
    marginTop: space[2],
  },
  state: {
    gap: space[4],
    alignItems: 'flex-start',
  },
  waiting: {
    ...typography.body,
    color: colors.inkMuted,
  },
  list: {
    gap: space[3],
  },
  empty: {
    alignItems: 'flex-start',
    gap: space[3],
    paddingVertical: space[4],
  },
  emptyText: {
    ...typography.body,
    color: colors.inkMuted,
  },
})
