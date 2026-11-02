/**
 * The items rotated on the scan loading screen (`DidYouKnow`).
 *
 * Two kinds: well-established facts about books and reading, and short tips
 * about using the app. **Facts must be uncontroversial.** No statistics that
 * somebody could dispute, no superlatives we cannot source; when a figure is
 * approximate the sentence says so ("about 49"). A wrong fact on a screen the
 * reader stares at for half a minute is worse than a spinner.
 */

export type FactKind = 'fact' | 'tip'

export interface Fact {
  kind: FactKind
  text: string
}

/** Label shown above each kind. */
export const FACT_LABELS: Record<FactKind, string> = {
  fact: 'DID YOU KNOW?',
  tip: 'TIP',
}

export const FACTS: readonly Fact[] = [
  {
    kind: 'fact',
    text: 'The Gutenberg Bible was printed in Mainz around 1455, one of the first major books made with movable type in Europe. About 49 copies survive today.',
  },
  {
    kind: 'fact',
    text: 'ISBNs have existed since 1970. They had 10 digits until 2007, when the standard moved to the 13 digits you see on books today.',
  },
  {
    kind: 'fact',
    text: 'The familiar smell of old books comes from the paper slowly breaking down: lignin in the pulp decays and releases compounds that smell a little like vanilla.',
  },
  {
    kind: 'fact',
    text: 'Frank Herbert’s Dune was turned down by more than twenty publishers before it was published in 1965.',
  },
  {
    kind: 'fact',
    text: 'Agatha Christie is widely regarded as the best-selling novelist of all time.',
  },
  {
    kind: 'fact',
    text: 'Some of the earliest books printed in Romanian were made in Brașov in the 1500s by Deacon Coresi.',
  },
  {
    kind: 'fact',
    text: 'Don Quixote, published in 1605, is often called the first modern novel.',
  },
  {
    kind: 'fact',
    text: 'Penguin Books launched in 1935 and helped make good paperbacks cheap enough to buy on a whim.',
  },
  {
    kind: 'fact',
    text: 'The Dewey Decimal system that organises many libraries was first published in 1876.',
  },
  {
    kind: 'fact',
    text: 'Japanese has a word, tsundoku, for buying books and letting them pile up unread.',
  },
  {
    kind: 'fact',
    text: 'UNESCO’s World Book Day is celebrated every year on 23 April.',
  },
  {
    kind: 'tip',
    text: 'For the best result, photograph the cover straight on, in good light and without glare.',
  },
  {
    kind: 'tip',
    text: 'If Glance picks the wrong book, tap “Wrong book? Edit title or author” on the result.',
  },
  {
    kind: 'tip',
    text: 'Rate the books you love 4 or 5 stars. Your recommendations are built from them.',
  },
  {
    kind: 'tip',
    text: 'Your journal notes are private. They are never used to write summaries.',
  },
  {
    kind: 'tip',
    text: 'Tap a sentence in a summary to see the source passage it came from.',
  },
]
