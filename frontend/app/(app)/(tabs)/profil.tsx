/**
 * The user's profile: identity, real counters, derived preferences, and
 * the reading history.
 *
 * Everything on this screen is now real (backend Module 6a) — the demo
 * note is gone, and with it the reason it existed.
 *
 * Two things it deliberately does *not* do:
 *
 * - **It does not count the history array.** `books_scanned` comes from
 *   `/users/me/stats`, computed over the whole library, because the list
 *   below is paginated and a page length is not a number of books. A
 *   header that says "12" over a list of 50 is worse than no header.
 * - **It does not invent preferences.** `based_on === 0` means nothing has
 *   been rated 4+ yet, and the screen says so instead of filling the row
 *   with whatever happened to pass the camera.
 */

import { Feather } from '@expo/vector-icons'
import { useRouter } from 'expo-router'
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native'

import { BookCard } from '@/components/book/CardCarte'
import { ErrorBanner } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { Screen } from '@/components/ui/Screen'
import { useLibrary, useLibraryStats, usePreferences } from '@/features/library/hooks'
import { useAuthStore } from '@/store/authStore'
import { colors, radius, space, textFit, typography } from '@/theme'
import type { LibraryEntry } from '@/types/biblioteca'

export default function ProfileScreen() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  const { data: history, isPending: loadingHistory, error: historyError } = useLibrary()
  const { data: stats } = useLibraryStats()
  const { data: preferences } = usePreferences()

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

  const hasPreferences =
    preferences !== undefined &&
    (preferences.favorite_genres.length > 0 || preferences.favorite_authors.length > 0)

  return (
    // `edges={['top']}` — the tab bar already clears the gesture bar.
    <Screen scrollable edges={['top']}>
      <View style={styles.header}>
        <View style={styles.avatar}>
          <Text style={styles.avatarInitial}>{(user?.email.charAt(0) ?? '?').toUpperCase()}</Text>
        </View>

        <View style={styles.identity}>
          <Text {...textFit.sectionTitle} style={styles.email}>
            {user?.email ?? 'Unknown'}
          </Text>
          <Text {...textFit.micro} style={styles.memberSince}>
            Member since {formatMonth(user?.created_at)}
          </Text>
        </View>
      </View>

      <View style={styles.stats}>
        <Stat label="Scanned" value={stats?.books_scanned ?? 0} />
        <Stat label="Read" value={stats?.books_read ?? 0} />
        <Stat label="Rated" value={stats?.ratings_given ?? 0} />
      </View>

      {stats?.average_rating !== null && stats?.average_rating !== undefined ? (
        <Text {...textFit.micro} style={styles.averageNote}>
          YOUR AVERAGE RATING: {stats.average_rating.toFixed(1)} / 5
        </Text>
      ) : null}

      <View style={styles.section}>
        <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
          What you like
        </Text>

        {hasPreferences ? (
          <>
            {preferences.favorite_authors.length > 0 ? (
              <View style={styles.chips}>
                {preferences.favorite_authors.map((author) => (
                  <Chip key={author} label={author} tone="accent" />
                ))}
              </View>
            ) : null}

            {preferences.favorite_genres.length > 0 ? (
              <View style={styles.chips}>
                {preferences.favorite_genres.map((genre) => (
                  <Chip key={genre} label={genre} />
                ))}
              </View>
            ) : null}

            <Text {...textFit.micro} style={styles.derivedNote}>
              DERIVED FROM {preferences.based_on} BOOK
              {preferences.based_on === 1 ? '' : 'S'} YOU RATED 4 OR HIGHER
            </Text>
          </>
        ) : (
          <Card>
            <View style={styles.empty}>
              <Feather color={colors.inkFaint} name="star" size={24} />
              <Text {...textFit.prose} style={styles.emptyText}>
                Rate a book 4 stars or higher and your favourite authors and genres will show up
                here.
              </Text>
            </View>
          </Card>
        )}
      </View>

      <View style={styles.section}>
        <Text {...textFit.sectionTitle} style={styles.sectionTitle}>
          Your books
        </Text>
        <Text {...textFit.micro} style={styles.sectionHint}>
          TAP A BOOK TO RATE IT AND KEEP A JOURNAL
        </Text>

        {historyError ? (
          <ErrorBanner message={historyError.message} />
        ) : loadingHistory ? (
          <ActivityIndicator color={colors.espresso} />
        ) : history && history.length > 0 ? (
          <View style={styles.list}>
            {history.map((entry) => (
              <BookCard
                book={entry.book}
                footer={entryFooter(entry)}
                key={entry.id}
                onPress={() => {
                  router.push(`/book/${entry.book.id}`)
                }}
              />
            ))}
          </View>
        ) : (
          <Card>
            <View style={styles.empty}>
              <Feather color={colors.inkFaint} name="book-open" size={28} />
              <Text {...textFit.prose} style={styles.emptyText}>
                No books yet. Scan a cover from the Home tab and it lands here automatically.
              </Text>
              <Button
                label="Scan a cover"
                onPress={() => {
                  router.push('/scan/camera')
                }}
                variant="secondary"
              />
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
      <Text {...textFit.statValue} style={styles.statValue}>
        {value}
      </Text>
      <Text {...textFit.micro} style={styles.statLabel}>
        {label.toUpperCase()}
      </Text>
    </View>
  )
}

/**
 * The one line under a book in the history.
 *
 * Prefers the user's own rating over the reading status, because it is
 * the thing they said about the book rather than the thing the app
 * recorded about the scan.
 *
 * Args:
 *   entry: The library entry to describe.
 */
function entryFooter(entry: LibraryEntry): string {
  const notes =
    entry.journal_count > 0
      ? ` · ${entry.journal_count} note${entry.journal_count === 1 ? '' : 's'}`
      : ''

  if (entry.rating !== null) {
    return `Your rating: ${entry.rating}/5${notes}`
  }

  switch (entry.status) {
    case 'read':
      return `Read${notes}`
    case 'reading':
      return `Currently reading${notes}`
    case 'want_to_read':
      return `Want to read${notes}`
    case 'scanned':
      return (entry.scan_count > 1 ? `Scanned ${entry.scan_count} times` : 'Scanned') + notes
  }
}

/**
 * Formats an account creation date as "August 2026".
 *
 * Args:
 *   iso: The ISO 8601 date from `UserPublic.created_at`, or `undefined`.
 */
function formatMonth(iso: string | undefined): string {
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
    gap: space[4],
    marginBottom: space[7],
  },
  avatar: {
    width: 64,
    height: 64,
    borderRadius: radius.pill,
    backgroundColor: colors.espresso,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInitial: {
    ...typography.displayMedium,
    color: colors.inkInverse,
  },
  identity: {
    // `textColumn`: without `flex: 1, minWidth: 0` a long email pushes
    // out of the row and never truncates.
    flex: 1,
    minWidth: 0,
    gap: space[1],
  },
  email: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  memberSince: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  stats: {
    flexDirection: 'row',
    gap: space[3],
  },
  stat: {
    flex: 1,
    minWidth: 0,
    alignItems: 'center',
    gap: space[1],
    paddingVertical: space[3],
    backgroundColor: colors.paperSunken,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  statValue: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  statLabel: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  averageNote: {
    ...typography.micro,
    color: colors.inkFaint,
    marginTop: space[2],
  },
  section: {
    marginTop: space[7],
    gap: space[3],
  },
  sectionTitle: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: space[2],
  },
  derivedNote: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  sectionHint: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  list: {
    gap: space[3],
  },
  empty: {
    alignItems: 'flex-start',
    gap: space[3],
    paddingVertical: space[4],
  },
  // Left-aligned: these wrap to two or three lines, and a centred
  // multi-line paragraph is a bug in this design system.
  emptyText: {
    ...typography.body,
    color: colors.inkMuted,
  },
  logoutButton: {
    marginTop: space[9],
  },
})
