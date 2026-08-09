/**
 * Containerul de bază al fiecărui ecran.
 *
 * Se ocupă de zonele sigure (notch, bara de gesturi) și de fundalul cald,
 * ca ecranele să nu repete aceeași plumbărie.
 */

import type { ReactNode } from 'react'
import { ScrollView, StyleSheet, View, type ViewStyle } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { colors, spacing } from '@/theme'

interface ScreenProps {
  children: ReactNode
  /** Face conținutul derulabil. Pentru ecrane cu text lung sau formulare. */
  scrollable?: boolean
  /** Elimină padding-ul orizontal — util pentru liste care ating marginile. */
  edgeToEdge?: boolean
  contentStyle?: ViewStyle
}

export function Screen({
  children,
  scrollable = false,
  edgeToEdge = false,
  contentStyle,
}: ScreenProps) {
  const insets = useSafeAreaInsets()

  const padding: ViewStyle = {
    paddingTop: insets.top + spacing.lg,
    paddingBottom: insets.bottom + spacing.lg,
    paddingHorizontal: edgeToEdge ? 0 : spacing.xl,
  }

  if (scrollable) {
    return (
      <ScrollView
        style={styles.radacina}
        contentContainerStyle={[padding, contentStyle]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {children}
      </ScrollView>
    )
  }

  return <View style={[styles.radacina, padding, contentStyle]}>{children}</View>
}

const styles = StyleSheet.create({
  radacina: {
    flex: 1,
    backgroundColor: colors.background,
  },
})
