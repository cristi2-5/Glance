/**
 * Demo data for screens that depend on backend modules not yet
 * implemented (Modules 3-6).
 *
 * Respects exactly the types in `src/types/api.ts`. Once the backend
 * starts returning real data, the screens don't change — only the mapper
 * stops falling back to the mock.
 */

import type { AnalysisResult } from '@/types/api'

export const DEMO_ANALYSIS: AnalysisResult = {
  title: 'The Name of the Rose',
  author: 'Umberto Eco',
  confidence: 0.94,
  summary:
    'In a Benedictine abbey in northern Italy in the year 1327, the Franciscan friar William of ' +
    'Baskerville is summoned to mediate a theological dispute, but ends up investigating a series of ' +
    'suspicious deaths. Together with the novice Adso, he discovers that the thread of the murders ' +
    "leads to the abbey's library — a labyrinth where a book is hidden that someone is willing to " +
    'kill to keep closed. The novel blends a detective investigation with an essay on semiotics, ' +
    'heresy, and the power of books.',
  cover_url: null,
  categories: ['Historical fiction', 'Mystery', 'Italian literature'],
  average_rating: 4.2,
  reviews: [
    {
      id: 'wiki-reception-1',
      source: 'wikipedia',
      source_title: 'Reception',
      excerpt:
        'The novel was received as a rare achievement: a medieval murder mystery that works equally ' +
        'well as popular reading and as a treatise on interpretation. Critics noted the density of its ' +
        'references, seen by some as a barrier and by others as precisely the source of its pleasure.',
      url: 'https://en.wikipedia.org/wiki/The_Name_of_the_Rose',
    },
    {
      id: 'ol-subjects-1',
      source: 'open_library',
      source_title: 'Open Library description',
      excerpt:
        "The first novel by semiotician Umberto Eco, published in 1980, translated into more than 40 " +
        'languages. It is frequently cited as an example of a postmodern novel accessible to a wide ' +
        'audience.',
      url: 'https://openlibrary.org/works/OL27479W',
    },
    {
      id: 'gb-description-1',
      source: 'google_books',
      source_title: "Publisher's description",
      excerpt:
        'A troubling investigation in an abbey where death seems to follow the pattern of the ' +
        'Apocalypse, told by a narrator who has reached the end of his life.',
      url: null,
    },
  ],
}
