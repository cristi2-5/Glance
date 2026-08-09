/**
 * The user's profile: real identity (from `/users/me`), plus history and
 * preferences on demo data until Module 6.
 */

import { Feather } from '@expo/vector-icons'
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native'

import { BookCard } from '@/components/book/CardCarte'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { DemoNote } from '@/components/ui/NotaDemo'
import { Screen } from '@/components/ui/Screen'
import { DEMO_DATA, useHistory, usePreferences } from '@/features/library/hooks'
import { useAuthStore } from '@/store/authStore'
import { colors, radius, spacing, typography } from '@/theme'

export default function ProfileScreen() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  const { data: history, isPending: loadingHistory } = useHistory()
  const { data: preferences } = usePreferences()

  const booksRead = history?.length ?? 0
  const ratingsGiven = history?.filter((entry) => entry.user_rating !== null).length ?? 0

  function confirmLogout() {
    Alert.alert('Log out?', "You'll need to log in again next time.", [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Log out',
        style: 'destructive',
        onPress: () => {
          void logout()
        },
      },
    ])
  }

  return (
    <Screen scrollable>
      <View style={styles.header}>
        <View style={styles.avatar}>
          <Text style={styles.avatarInitial}>
            {(user?.email.charAt(0) ?? '?').toUpperCase()}
          </Text>
        </View>

        <View style={styles.identity}>
          <Text style={styles.email}>{user?.email ?? 'Unknown'}</Text>
          <Text style={styles.memberSince}>
            Member since {formatDate(user?.created_at)}
          </Text>
        </View>
      </View>

      <View style={styles.stats}>
        <Stat label="Books scanned" value={booksRead} />
        <Stat label="Ratings given" value={ratingsGiven} />
        <Stat label="Genres followed" value={preferences?.favorite_genres.length ?? 0} />
      </View>

      {DEMO_DATA ? (
        <DemoNote message="History and preferences are demo data — they arrive with backend Module 6. The email above is real." />
      ) : null}

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Favorite authors</Text>
        <View style={styles.chips}>
          {preferences?.favorite_authors.map((author) => (
            <Chip label={author} key={author} tone="accent" />
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Favorite genres</Text>
        <View style={styles.chips}>
          {preferences?.favorite_genres.map((genre) => (
            <Chip label={genre} key={genre} />
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Reading history</Text>

        {loadingHistory ? (
          <ActivityIndicator color={colors.accent} />
        ) : history && history.length > 0 ? (
          <View style={styles.list}>
            {history.map((entry) => (
              <BookCard
                book={entry.book}
                key={entry.id}
                footer={
                  entry.user_rating !== null
                    ? `Your rating: ${entry.user_rating}/5`
                    : 'No rating yet'
                }
              />
            ))}
          </View>
        ) : (
          <Card>
            <View style={styles.empty}>
              <Feather color={colors.inkFaint} name="book-open" size={28} />
              <Text style={styles.emptyText}>
                No books scanned yet. Start from the Home tab.
              </Text>
            </View>
          </Card>
        )}
      </View>

      <Button
        label="Log out"
        onPress={confirmLogout}
        style={styles.logoutButton}
        variant="secondary"
      />
    </Screen>
  )
}

/** A numeric statistic in the profile header. */
function Stat({ label, value }: { label: string; value: number }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  )
}

/**
 * Formats the account creation date as "August 2026".
 *
 * Args:
 *   iso: The ISO 8601 date from `UserPublic.created_at`, or `undefined`.
 */
function formatDate(iso: string | undefined): string {
  if (!iso) {
    return '—'
  }

  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }

  return date.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.lg,
    marginBottom: spacing.xl,
  },
  avatar: {
    width: 64,
    height: 64,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInitial: {
    ...typography.displayMedium,
    color: colors.inkInverse,
  },
  identity: {
    flex: 1,
    gap: 2,
  },
  email: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  memberSince: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  stats: {
    flexDirection: 'row',
    gap: spacing.md,
    marginBottom: spacing.xl,
  },
  stat: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
    paddingVertical: spacing.md,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.md,
  },
  statValue: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  statLabel: {
    ...typography.caption,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  section: {
    marginTop: spacing.xl,
    gap: spacing.md,
  },
  sectionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  list: {
    gap: spacing.md,
  },
  empty: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.lg,
  },
  emptyText: {
    ...typography.caption,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  logoutButton: {
    marginTop: spacing.xxl,
  },
})
