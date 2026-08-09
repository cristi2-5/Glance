/**
 * Paleta Glance — hârtie caldă, cerneală espresso, accent teracotă.
 *
 * Nu folosi niciodată culori literale în componente; importă de aici.
 * Structura e pregătită pentru o temă întunecată viitoare: componentele
 * consumă `colors`, nu constantele individuale.
 */

export const colors = {
  /** Fundalul ecranelor — crem de hârtie, nu alb clinic. */
  background: '#FBF7F0',
  /** Suprafețe ridicate: carduri, foi modale. */
  surface: '#FFFDF9',
  /** Suprafață secundară pentru zone grupate (input-uri, chips). */
  surfaceMuted: '#F3EBDE',

  /** Text principal — espresso, mai blând decât negrul pur. */
  ink: '#2B2118',
  /** Text secundar: subtitluri, metadate. */
  inkMuted: '#6B5D50',
  /** Text terțiar: placeholder, note de subsol. */
  inkFaint: '#9C8B7A',
  /** Text pe fundal de accent. */
  inkInverse: '#FFFDF9',

  /** Accent principal — teracotă. Butoane primare, elemente active. */
  accent: '#C4633F',
  /** Varianta apăsată a accentului. */
  accentPressed: '#A85234',
  /** Fundal discret derivat din accent (badge-uri, evidențieri). */
  accentSoft: '#F5E3DA',

  /** Chihlimbar — stele de rating, evidențieri secundare. */
  amber: '#D9922A',

  /** Stări semantice. */
  success: '#4A7C59',
  danger: '#B3402F',
  dangerSoft: '#F7E4E0',

  /** Contururi și separatoare. */
  border: '#E5DACA',
  borderStrong: '#D2C2AC',

  /** Overlay pentru modale și camera. */
  overlay: 'rgba(43, 33, 24, 0.55)',
  /** Fundalul ecranului de cameră. */
  cameraBackdrop: '#1A140F',
} as const

export type ColorToken = keyof typeof colors
