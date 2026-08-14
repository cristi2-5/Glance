/**
 * Demo data for the Recommendations screen (backend Module 6b).
 *
 * History, stats and preferences are **real** as of Module 6a and no
 * longer live here. What remains is only what the backend cannot answer
 * yet, so the demo marker on screen stays truthful about which half is
 * fabricated.
 */

import type { Recommendation } from '@/types/biblioteca'

export const DEMO_RECOMMENDATIONS: Recommendation[] = [
  {
    id: 'r1',
    score: 0.91,
    explanation: 'Because you liked The Name of the Rose',
    book: {
      id: 904,
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
      id: 905,
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
      id: 906,
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
      id: 907,
      title: "The Shadow of the Wind",
      author: 'Carlos Ruiz Zafón',
      cover_url: null,
      categories: ['Mystery', 'Historical fiction'],
      average_rating: 4.3,
    },
  },
]
