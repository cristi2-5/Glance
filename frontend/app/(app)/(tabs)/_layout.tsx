/**
 * The main navigation bar: Home, For you, Profile.
 *
 * The bar height is derived from the bottom inset rather than hardcoded: a
 * fixed `height: 88` is too tall on devices with a physical home button and
 * too short once a large gesture bar is in play.
 *
 * The active tab is marked by a mustard rule above the icon, not by tinting
 * the label — mustard on paper fails contrast at label sizes, so the label
 * darkens to espresso instead and the highlight does the signalling.
 */

import { Feather } from '@expo/vector-icons'
import { Tabs } from 'expo-router'
import { StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { colors, radius, space, textFit, typography } from '@/theme'

type FeatherName = keyof typeof Feather.glyphMap

/** Icon plus the active-state rule above it. */
function TabIcon({ name, focused }: { name: FeatherName; focused: boolean }) {
  return (
    <View style={styles.iconWrap}>
      <View style={[styles.indicator, focused ? styles.indicatorActive : null]} />
      <Feather color={focused ? colors.ink : colors.inkFaint} name={name} size={22} />
    </View>
  )
}

/**
 * Rendered rather than styled via `tabBarLabelStyle`, because that option
 * cannot carry `numberOfLines`/`adjustsFontSizeToFit` — and the tab bar is the
 * tightest fixed box in the app, so it is exactly where a long label under a
 * large OS font scale would get clipped.
 */
function TabLabel({ label, focused }: { label: string; focused: boolean }) {
  return (
    <Text {...textFit.tabLabel} style={[styles.label, focused ? styles.labelActive : null]}>
      {label}
    </Text>
  )
}

export default function TabsLayout() {
  const insets = useSafeAreaInsets()

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: colors.paperRaised,
          borderTopColor: colors.border,
          borderTopWidth: StyleSheet.hairlineWidth,
          height: space[9] + space[2] + insets.bottom,
          paddingTop: space[2],
          paddingBottom: insets.bottom,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Home',
          tabBarIcon: ({ focused }) => <TabIcon focused={focused} name="home" />,
          tabBarLabel: ({ focused }) => <TabLabel focused={focused} label="Home" />,
        }}
      />
      <Tabs.Screen
        name="recomandari"
        options={{
          title: 'For you',
          tabBarIcon: ({ focused }) => <TabIcon focused={focused} name="compass" />,
          tabBarLabel: ({ focused }) => <TabLabel focused={focused} label="For you" />,
        }}
      />
      <Tabs.Screen
        name="profil"
        options={{
          title: 'Profile',
          tabBarIcon: ({ focused }) => <TabIcon focused={focused} name="user" />,
          tabBarLabel: ({ focused }) => <TabLabel focused={focused} label="Profile" />,
        }}
      />
    </Tabs>
  )
}

const styles = StyleSheet.create({
  iconWrap: {
    alignItems: 'center',
    gap: space[2],
  },
  indicator: {
    width: space[6],
    height: 3,
    borderRadius: radius.pill,
    backgroundColor: 'transparent',
  },
  indicatorActive: {
    backgroundColor: colors.highlight,
  },
  label: {
    ...typography.micro,
    color: colors.inkFaint,
    textAlign: 'center',
  },
  labelActive: {
    color: colors.ink,
  },
})
