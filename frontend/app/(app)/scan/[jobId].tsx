/**
 * Rezultatul unei scanări.
 *
 * Face polling pe `GET /jobs/{id}` până când job-ul ajunge `done` sau
 * `failed`. Cât timp Modulele 3-5 nu sunt gata, backendul întoarce un
 * placeholder, iar ecranul afișează date demonstrative — marcate explicit,
 * niciodată strecurate ca reale.
 */

import { Feather } from '@expo/vector-icons'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { useMemo } from 'react'
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View } from 'react-native'

import { ApiError } from '@/api/errors'
import { RatingStele } from '@/components/book/RatingStele'
import { BannerEroare } from '@/components/ui/BannerEroare'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { Screen } from '@/components/ui/Screen'
import { useJob } from '@/features/scan/hooks'
import { interpreteazaRezultat } from '@/features/scan/mapper'
import { colors, radius, spacing, typography } from '@/theme'
import type { RecenzieSursa } from '@/types/api'

/** Etichete lizibile pentru sursele de conținut. */
const NUME_SURSA: Record<RecenzieSursa['sursa'], string> = {
  wikipedia: 'Wikipedia',
  open_library: 'Open Library',
  google_books: 'Google Books',
}

export default function RezultatScanareScreen() {
  const router = useRouter()
  const { jobId } = useLocalSearchParams<{ jobId: string }>()

  const idNumeric = Number.parseInt(jobId ?? '', 10)
  const idValid = Number.isFinite(idNumeric)

  const { data: job, error } = useJob(idValid ? idNumeric : null)

  const interpretare = useMemo(
    () => (job?.status === 'done' ? interpreteazaRezultat(job.result) : null),
    [job?.status, job?.result]
  )

  if (!idValid) {
    return (
      <Screen contentStyle={styles.centrat}>
        <BannerEroare mesaj="Identificator de job invalid." />
        <Button label="Înapoi" onPress={() => router.replace('/')} variant="secondary" />
      </Screen>
    )
  }

  if (error) {
    const mesaj = error instanceof ApiError ? error.message : 'Nu am putut citi starea analizei.'
    return (
      <Screen contentStyle={styles.centrat}>
        <BannerEroare mesaj={mesaj} />
        <Button label="Înapoi la Acasă" onPress={() => router.replace('/')} variant="secondary" />
      </Screen>
    )
  }

  // Prima citire, înainte ca polling-ul să întoarcă ceva.
  if (!job) {
    return <StareInProgres status="pending" />
  }

  if (job.status === 'pending' || job.status === 'running') {
    return <StareInProgres status={job.status} />
  }

  if (job.status === 'failed') {
    return (
      <Screen contentStyle={styles.centrat}>
        <Feather color={colors.danger} name="alert-triangle" size={40} />
        <Text style={styles.titluStare}>Analiza a eșuat</Text>
        <Text style={styles.textStare}>{job.error ?? 'Backendul nu a oferit un motiv.'}</Text>
        <Button label="Încearcă din nou" onPress={() => router.replace('/scan/camera')} />
        <Button label="Înapoi la Acasă" onPress={() => router.replace('/')} variant="ghost" />
      </Screen>
    )
  }

  if (!interpretare) {
    return <StareInProgres status="running" />
  }

  const { analiza, esteDemonstrativ } = interpretare

  return (
    <Screen scrollable>
      <View style={styles.bara}>
        <Pressable
          accessibilityLabel="Înapoi"
          accessibilityRole="button"
          hitSlop={12}
          onPress={() => router.replace('/')}
        >
          <Feather color={colors.ink} name="arrow-left" size={24} />
        </Pressable>
      </View>

      {esteDemonstrativ ? (
        <View style={styles.avertismentDemo}>
          <Feather color={colors.accent} name="info" size={16} />
          <Text style={styles.textDemo}>
            Date demonstrative. Backendul a preluat imaginea, dar recunoașterea și sinteza sosesc cu
            Modulele 3-5.
          </Text>
        </View>
      ) : null}

      <View style={styles.antet}>
        <Text style={styles.titluCarte}>{analiza.titlu}</Text>
        {analiza.autor ? <Text style={styles.autor}>de {analiza.autor}</Text> : null}

        <View style={styles.metaRand}>
          {analiza.rating_mediu !== null ? <RatingStele valoare={analiza.rating_mediu} /> : null}
          <Chip
            eticheta={`Încredere ${Math.round(analiza.incredere * 100)}%`}
            ton={analiza.incredere >= 0.7 ? 'accent' : 'avertisment'}
          />
        </View>

        {analiza.categorii.length > 0 ? (
          <View style={styles.categorii}>
            {analiza.categorii.map((categorie) => (
              <Chip eticheta={categorie} key={categorie} />
            ))}
          </View>
        ) : null}
      </View>

      {analiza.rezumat ? (
        <View style={styles.sectiune}>
          <Text style={styles.titluSectiune}>Despre carte</Text>
          <Text style={styles.rezumat}>{analiza.rezumat}</Text>
        </View>
      ) : null}

      {analiza.recenzii.length > 0 ? (
        <View style={styles.sectiune}>
          <Text style={styles.titluSectiune}>Ce spun sursele</Text>

          <View style={styles.recenzii}>
            {analiza.recenzii.map((recenzie) => (
              <CardRecenzie key={recenzie.id} recenzie={recenzie} />
            ))}
          </View>
        </View>
      ) : null}

      <Button
        label="Scanează altă carte"
        onPress={() => router.replace('/scan/camera')}
        style={styles.butonFinal}
        variant="secondary"
      />
    </Screen>
  )
}

/** Ecranul afișat cât timp job-ul e în lucru. */
function StareInProgres({ status }: { status: 'pending' | 'running' }) {
  const mesaj =
    status === 'pending'
      ? 'Am primit imaginea. Aștept să înceapă analiza…'
      : 'Citesc coperta și adun informații despre carte…'

  return (
    <Screen contentStyle={styles.centrat}>
      <ActivityIndicator color={colors.accent} size="large" />
      <Text style={styles.titluStare}>Se analizează</Text>
      <Text style={styles.textStare}>{mesaj}</Text>
      <Text style={styles.notaStare}>
        Pe un laptop fără placă video dedicată, pasul complet poate dura până la două minute.
      </Text>
    </Screen>
  )
}

/** Un extras dintr-o sursă, cu link către original. */
function CardRecenzie({ recenzie }: { recenzie: RecenzieSursa }) {
  const areLink = recenzie.url !== null

  return (
    <Card
      {...(areLink
        ? {
            onPress: () => {
              void Linking.openURL(recenzie.url as string)
            },
          }
        : {})}
    >
      <View style={styles.antetRecenzie}>
        <Chip eticheta={NUME_SURSA[recenzie.sursa]} ton="accent" />
        {areLink ? <Feather color={colors.inkFaint} name="external-link" size={14} /> : null}
      </View>

      <Text style={styles.titluRecenzie}>{recenzie.titlu_sursa}</Text>
      <Text style={styles.extrasRecenzie}>{recenzie.extras}</Text>
    </Card>
  )
}

const styles = StyleSheet.create({
  centrat: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.md,
  },
  bara: {
    marginBottom: spacing.lg,
  },
  avertismentDemo: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    padding: spacing.md,
    marginBottom: spacing.xl,
    backgroundColor: colors.accentSoft,
    borderRadius: radius.md,
  },
  textDemo: {
    ...typography.caption,
    flex: 1,
    color: colors.accent,
  },
  antet: {
    gap: spacing.md,
  },
  titluCarte: {
    ...typography.displayMedium,
    color: colors.ink,
  },
  autor: {
    ...typography.body,
    color: colors.inkMuted,
    marginTop: -spacing.sm,
  },
  metaRand: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    flexWrap: 'wrap',
  },
  categorii: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  sectiune: {
    marginTop: spacing.xxl,
    gap: spacing.md,
  },
  titluSectiune: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  rezumat: {
    ...typography.bodyReading,
    color: colors.ink,
  },
  recenzii: {
    gap: spacing.md,
  },
  antetRecenzie: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  titluRecenzie: {
    ...typography.titleCard,
    color: colors.ink,
    marginBottom: spacing.xs,
  },
  extrasRecenzie: {
    ...typography.body,
    color: colors.inkMuted,
  },
  titluStare: {
    ...typography.displaySmall,
    color: colors.ink,
    textAlign: 'center',
  },
  textStare: {
    ...typography.body,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  notaStare: {
    ...typography.caption,
    color: colors.inkFaint,
    textAlign: 'center',
    maxWidth: 280,
  },
  butonFinal: {
    marginTop: spacing.xxl,
  },
})
