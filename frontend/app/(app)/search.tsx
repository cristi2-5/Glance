/**
 * Find a book by typing its title (and optionally author) instead of
 * photographing it.
 *
 * Posts to `POST /books/lookup`, which answers `202 {job_id}` like a scan, and
 * then `replace`s into the scan result route so the existing polling, loading
 * facts and result screen are reused as they are. `replace` rather than `push`
 * so Back from the result returns to Home, not to a filled-in form.
 */

import { useRouter } from 'expo-router'
import { useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'

import { normalizeError } from '@/api/errors'
import { TitleAuthorForm } from '@/components/book/TitleAuthorForm'
import { BackRow } from '@/components/ui/BackRow'
import { Screen } from '@/components/ui/Screen'
import { useLookupBook } from '@/features/scan/hooks'
import type { LookupData } from '@/features/scan/schema'
import { colors, space, textFit, typography } from '@/theme'

export default function SearchScreen() {
  const router = useRouter()
  const lookup = useLookupBook()
  const [error, setError] = useState<string | null>(null)

  async function submit(data: LookupData) {
    setError(null)
    try {
      const job = await lookup.mutateAsync({
        title: data.title,
        author: data.author.length > 0 ? data.author : null,
      })
      router.replace(`/scan/${job.job_id}`)
    } catch (problem) {
      setError(normalizeError(problem).message)
    }
  }

  return (
    <Screen scrollable>
      <BackRow label="Home" onPress={() => router.back()} />

      <View style={styles.header}>
        <Text {...textFit.micro} style={styles.eyebrow}>
          FIND A BOOK
        </Text>
        <Text {...textFit.screenTitle} style={styles.title}>
          Type the title
        </Text>
        <Text {...textFit.prose} style={styles.subtitle}>
          No cover at hand? Enter the title, and the author if you know it. We&apos;ll look the
          book up for you.
        </Text>
      </View>

      <TitleAuthorForm
        authorHint="Optional, but it helps tell books with the same title apart."
        error={error}
        onSubmit={submit}
        submitLabel="Find book"
      />
    </Screen>
  )
}

const styles = StyleSheet.create({
  header: {
    gap: space[2],
    marginBottom: space[7],
  },
  eyebrow: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  title: {
    ...typography.displayLarge,
    color: colors.ink,
  },
  subtitle: {
    ...typography.body,
    color: colors.inkMuted,
  },
})
