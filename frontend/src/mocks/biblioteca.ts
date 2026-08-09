/**
 * Demo data for Profile and Recommendations (backend Module 6).
 *
 * See the note in `src/types/library.ts`: the shapes here are the
 * contract the real data will follow.
 */

import type {
  HistoryEntry,
  Recommendation,
  UserPreferences,
} from '@/types/biblioteca'

export const DEMO_HISTORY: HistoryEntry[] = [
  {
    id: 'h1',
    user_rating: 5,
    read_at: '2026-07-28T18:40:00Z',
    book: {
      id: 'b1',
      title: 'The Name of the Rose',
      author: 'Umberto Eco',
      cover_url: null,
      categories: ['Historical fiction', 'Mystery'],
      average_rating: 4.2,
    },
  },
  {
    id: 'h2',
    user_rating: 4,
    read_at: '2026-07-11T09:15:00Z',
    book: {
      id: 'b2',
      title: 'The Master and Margarita',
      author: 'Mikhail Bulgakov',
      cover_url: null,
      categories: ['Magical realism', 'Satire'],
      average_rating: 4.4,
    },
  },
  {
    id: 'h3',
    user_rating: null,
    read_at: '2026-06-30T20:05:00Z',
    book: {
      id: 'b3',
      title: 'Solaris',
      author: 'Stanisław Lem',
      cover_url: null,
      categories: ['Science fiction', 'Philosophical'],
      average_rating: 4.0,
    },
  },
]

export const DEMO_PREFERENCES: UserPreferences = {
  favorite_genres: ['Historical fiction', 'Science fiction', 'Magical realism', 'Mystery'],
  favorite_authors: ['Umberto Eco', 'Stanisław Lem', 'Mikhail Bulgakov'],
}

export const DEMO_RECOMMENDATIONS: Recommendation[] = [
  {
    id: 'r1',
    score: 0.91,
    explanation: 'Because you liked The Name of the Rose',
    book: {
      id: 'b4',
      title: "Foucault's Pendulum",
      author: 'Umberto Eco',
      cover_url: null,
      categories: ['Novel', 'Conspiracy', 'History'],
      average_rating: 3.9,
    },
  },
  {
    id: 'r2',
    score: 0.84,
    explanation: 'Close to Solaris in theme and tone',
    book: {
      id: 'b5',
      title: 'The Cyberiad',
      author: 'Stanisław Lem',
      cover_url: null,
      categories: ['Science fiction', 'Satire'],
      average_rating: 4.3,
    },
  },
  {
    id: 'r3',
    score: 0.78,
    explanation: 'Magical realism, like The Master and Margarita',
    book: {
      id: 'b6',
      title: 'One Hundred Years of Solitude',
      author: 'Gabriel García Márquez',
      cover_url: null,
      categories: ['Magical realism', 'Family saga'],
      average_rating: 4.1,
    },
  },
  {
    id: 'r4',
    score: 0.72,
    explanation: 'Historical mystery, in line with your reading',
    book: {
      id: 'b7',
      title: "The Shadow of the Wind",
      author: 'Carlos Ruiz Zafón',
      cover_url: null,
      categories: ['Mystery', 'Historical fiction'],
      average_rating: 4.3,
    },
  },
]
