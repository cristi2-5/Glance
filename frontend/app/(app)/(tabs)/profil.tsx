/**
 * Profilul utilizatorului: identitate reală (din `/users/me`), plus istoric
 * și preferințe pe date demonstrative până la Modulul 6.
 */

import { Feather } from '@expo/vector-icons'
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native'

import { CardCarte } from '@/components/book/CardCarte'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Chip } from '@/components/ui/Chip'
import { NotaDemo } from '@/components/ui/NotaDemo'
import { Screen } from '@/components/ui/Screen'
import { DATE_DEMONSTRATIVE, useIstoric, usePreferinte } from '@/features/library/hooks'
import { useAuthStore } from '@/store/authStore'
import { colors, radius, spacing, typography } from '@/theme'

export default function ProfilScreen() {
  const utilizator = useAuthStore((s) => s.utilizator)
  const iesi = useAuthStore((s) => s.iesi)

  const { data: istoric, isPending: incarcaIstoric } = useIstoric()
  const { data: preferinte } = usePreferinte()

  const carticitite = istoric?.length ?? 0
  const noteDate = istoric?.filter((intrare) => intrare.nota_utilizator !== null).length ?? 0

  function confirmaIesirea() {
    Alert.alert('Ieși din cont?', 'Va trebui să te autentifici din nou data viitoare.', [
      { text: 'Anulează', style: 'cancel' },
      {
        text: 'Ieși',
        style: 'destructive',
        onPress: () => {
          void iesi()
        },
      },
    ])
  }

  return (
    <Screen scrollable>
      <View style={styles.antet}>
        <View style={styles.avatar}>
          <Text style={styles.initialaAvatar}>
            {(utilizator?.email.charAt(0) ?? '?').toUpperCase()}
          </Text>
        </View>

        <View style={styles.identitate}>
          <Text style={styles.email}>{utilizator?.email ?? 'Necunoscut'}</Text>
          <Text style={styles.membruDin}>
            Membru din {formateazaData(utilizator?.created_at)}
          </Text>
        </View>
      </View>

      <View style={styles.statistici}>
        <Statistica eticheta="Cărți scanate" valoare={carticitite} />
        <Statistica eticheta="Note date" valoare={noteDate} />
        <Statistica eticheta="Genuri urmărite" valoare={preferinte?.genuri_favorite.length ?? 0} />
      </View>

      {DATE_DEMONSTRATIVE ? (
        <NotaDemo mesaj="Istoricul și preferințele sunt demonstrative — sosesc cu Modulul 6 al backendului. Emailul de mai sus e real." />
      ) : null}

      <View style={styles.sectiune}>
        <Text style={styles.titluSectiune}>Autori favoriți</Text>
        <View style={styles.chipuri}>
          {preferinte?.autori_favoriti.map((autor) => (
            <Chip eticheta={autor} key={autor} ton="accent" />
          ))}
        </View>
      </View>

      <View style={styles.sectiune}>
        <Text style={styles.titluSectiune}>Genuri preferate</Text>
        <View style={styles.chipuri}>
          {preferinte?.genuri_favorite.map((gen) => (
            <Chip eticheta={gen} key={gen} />
          ))}
        </View>
      </View>

      <View style={styles.sectiune}>
        <Text style={styles.titluSectiune}>Istoric de lectură</Text>

        {incarcaIstoric ? (
          <ActivityIndicator color={colors.accent} />
        ) : istoric && istoric.length > 0 ? (
          <View style={styles.lista}>
            {istoric.map((intrare) => (
              <CardCarte
                carte={intrare.carte}
                key={intrare.id}
                subsol={
                  intrare.nota_utilizator !== null
                    ? `Nota ta: ${intrare.nota_utilizator}/5`
                    : 'Fără notă'
                }
              />
            ))}
          </View>
        ) : (
          <Card>
            <View style={styles.gol}>
              <Feather color={colors.inkFaint} name="book-open" size={28} />
              <Text style={styles.textGol}>
                Încă nicio carte scanată. Începe din fila Acasă.
              </Text>
            </View>
          </Card>
        )}
      </View>

      <Button
        label="Ieși din cont"
        onPress={confirmaIesirea}
        style={styles.butonIesire}
        variant="secondary"
      />
    </Screen>
  )
}

/** O statistică numerică din antetul profilului. */
function Statistica({ eticheta, valoare }: { eticheta: string; valoare: number }) {
  return (
    <View style={styles.statistica}>
      <Text style={styles.valoareStatistica}>{valoare}</Text>
      <Text style={styles.etichetaStatistica}>{eticheta}</Text>
    </View>
  )
}

/**
 * Formatează data de creare a contului în forma „august 2026".
 *
 * Args:
 *   iso: Data ISO 8601 din `UserPublic.created_at`, sau `undefined`.
 */
function formateazaData(iso: string | undefined): string {
  if (!iso) {
    return '—'
  }

  const data = new Date(iso)
  if (Number.isNaN(data.getTime())) {
    return '—'
  }

  return data.toLocaleDateString('ro-RO', { month: 'long', year: 'numeric' })
}

const styles = StyleSheet.create({
  antet: {
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
  initialaAvatar: {
    ...typography.displayMedium,
    color: colors.inkInverse,
  },
  identitate: {
    flex: 1,
    gap: 2,
  },
  email: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  membruDin: {
    ...typography.caption,
    color: colors.inkMuted,
  },
  statistici: {
    flexDirection: 'row',
    gap: spacing.md,
    marginBottom: spacing.xl,
  },
  statistica: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
    paddingVertical: spacing.md,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.md,
  },
  valoareStatistica: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  etichetaStatistica: {
    ...typography.caption,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  sectiune: {
    marginTop: spacing.xl,
    gap: spacing.md,
  },
  titluSectiune: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  chipuri: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  lista: {
    gap: spacing.md,
  },
  gol: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.lg,
  },
  textGol: {
    ...typography.caption,
    color: colors.inkMuted,
    textAlign: 'center',
  },
  butonIesire: {
    marginTop: spacing.xxl,
  },
})
