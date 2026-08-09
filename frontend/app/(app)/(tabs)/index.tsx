/** Ecranul principal: punctul de plecare al unei scanări. */

import { Feather } from '@expo/vector-icons'
import * as Haptics from 'expo-haptics'
import { useRouter } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { Screen } from '@/components/ui/Screen'
import { useAuthStore } from '@/store/authStore'
import { colors, radius, shadow, spacing, typography } from '@/theme'

export default function HomeScreen() {
  const router = useRouter()
  const utilizator = useAuthStore((s) => s.utilizator)

  const numeAfisat = utilizator?.email.split('@')[0] ?? 'cititorule'

  function deschideCamera() {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
    router.push('/scan/camera')
  }

  return (
    <Screen scrollable>
      <View style={styles.antet}>
        <Text style={styles.eyebrow}>Salut, {numeAfisat}</Text>
        <Text style={styles.titlu}>Ce carte ai în mână?</Text>
      </View>

      <Pressable
        accessibilityHint="Deschide camera pentru a fotografia coperta unei cărți"
        accessibilityLabel="Scanează o copertă"
        accessibilityRole="button"
        onPress={deschideCamera}
        style={({ pressed }) => [styles.cardCaptura, pressed ? styles.cardApasat : null]}
      >
        <View style={styles.cercIcon}>
          <Feather color={colors.inkInverse} name="camera" size={30} />
        </View>
        <Text style={styles.titluCaptura}>Scanează o copertă</Text>
        <Text style={styles.subtitluCaptura}>
          Ține cartea dreaptă, cu titlul vizibil. Restul îl fac eu.
        </Text>
      </Pressable>

      <View style={styles.sectiune}>
        <Text style={styles.titluSectiune}>Cum funcționează</Text>

        <View style={styles.pasi}>
          <Pas
            descriere="Fotografiezi coperta. Textul e citit local, pe laptopul tău."
            index={1}
            titlu="Recunoaștere"
          />
          <Pas
            descriere="Caut descrieri și opinii critice în surse deschise."
            index={2}
            titlu="Documentare"
          />
          <Pas
            descriere="Primești un rezumat cu trimitere la sursa fiecărei afirmații."
            index={3}
            titlu="Sinteză"
          />
        </View>
      </View>
    </Screen>
  )
}

interface PasProps {
  index: number
  titlu: string
  descriere: string
}

function Pas({ index, titlu, descriere }: PasProps) {
  return (
    <View style={styles.pas}>
      <View style={styles.numarPas}>
        <Text style={styles.textNumarPas}>{index}</Text>
      </View>
      <View style={styles.continutPas}>
        <Text style={styles.titluPas}>{titlu}</Text>
        <Text style={styles.descrierePas}>{descriere}</Text>
      </View>
    </View>
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
  cardCaptura: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.xxl,
    paddingHorizontal: spacing.xl,
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.border,
    ...shadow.lifted,
  },
  cardApasat: {
    backgroundColor: colors.surfaceMuted,
  },
  cercIcon: {
    width: 72,
    height: 72,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  titluCaptura: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  subtitluCaptura: {
    ...typography.caption,
    color: colors.inkMuted,
    textAlign: 'center',
    maxWidth: 260,
  },
  sectiune: {
    marginTop: spacing.xxl,
    gap: spacing.lg,
  },
  titluSectiune: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  pasi: {
    gap: spacing.lg,
  },
  pas: {
    flexDirection: 'row',
    gap: spacing.md,
    alignItems: 'flex-start',
  },
  numarPas: {
    width: 28,
    height: 28,
    borderRadius: radius.pill,
    backgroundColor: colors.accentSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  textNumarPas: {
    ...typography.label,
    color: colors.accent,
  },
  continutPas: {
    flex: 1,
    gap: 2,
  },
  titluPas: {
    ...typography.titleCard,
    color: colors.ink,
  },
  descrierePas: {
    ...typography.caption,
    color: colors.inkMuted,
  },
})
