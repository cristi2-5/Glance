/**
 * Date demonstrative pentru Profil și Recomandări (Modulul 6 de backend).
 *
 * Vezi nota din `src/types/biblioteca.ts`: formele de aici sunt contractul
 * pe care îl vor respecta datele reale.
 */

import type {
  IntrareIstoric,
  PreferinteUtilizator,
  Recomandare,
} from '@/types/biblioteca'

export const ISTORIC_DEMO: IntrareIstoric[] = [
  {
    id: 'h1',
    nota_utilizator: 5,
    citita_la: '2026-07-28T18:40:00Z',
    carte: {
      id: 'b1',
      titlu: 'Numele trandafirului',
      autor: 'Umberto Eco',
      coperta_url: null,
      categorii: ['Roman istoric', 'Mister'],
      rating_mediu: 4.2,
    },
  },
  {
    id: 'h2',
    nota_utilizator: 4,
    citita_la: '2026-07-11T09:15:00Z',
    carte: {
      id: 'b2',
      titlu: 'Maestrul și Margareta',
      autor: 'Mihail Bulgakov',
      coperta_url: null,
      categorii: ['Realism magic', 'Satiră'],
      rating_mediu: 4.4,
    },
  },
  {
    id: 'h3',
    nota_utilizator: null,
    citita_la: '2026-06-30T20:05:00Z',
    carte: {
      id: 'b3',
      titlu: 'Solaris',
      autor: 'Stanisław Lem',
      coperta_url: null,
      categorii: ['Science fiction', 'Filosofic'],
      rating_mediu: 4.0,
    },
  },
]

export const PREFERINTE_DEMO: PreferinteUtilizator = {
  genuri_favorite: ['Roman istoric', 'Science fiction', 'Realism magic', 'Mister'],
  autori_favoriti: ['Umberto Eco', 'Stanisław Lem', 'Mihail Bulgakov'],
}

export const RECOMANDARI_DEMO: Recomandare[] = [
  {
    id: 'r1',
    scor: 0.91,
    explicatie: 'Pentru că ți-a plăcut Numele trandafirului',
    carte: {
      id: 'b4',
      titlu: 'Pendulul lui Foucault',
      autor: 'Umberto Eco',
      coperta_url: null,
      categorii: ['Roman', 'Conspirație', 'Istorie'],
      rating_mediu: 3.9,
    },
  },
  {
    id: 'r2',
    scor: 0.84,
    explicatie: 'Aproape de Solaris prin temă și ton',
    carte: {
      id: 'b5',
      titlu: 'Cyberiada',
      autor: 'Stanisław Lem',
      coperta_url: null,
      categorii: ['Science fiction', 'Satiră'],
      rating_mediu: 4.3,
    },
  },
  {
    id: 'r3',
    scor: 0.78,
    explicatie: 'Realism magic, ca Maestrul și Margareta',
    carte: {
      id: 'b6',
      titlu: 'Un veac de singurătate',
      autor: 'Gabriel García Márquez',
      coperta_url: null,
      categorii: ['Realism magic', 'Saga de familie'],
      rating_mediu: 4.1,
    },
  },
  {
    id: 'r4',
    scor: 0.72,
    explicatie: 'Mister istoric, în linia lecturilor tale',
    carte: {
      id: 'b7',
      titlu: 'Umbra vântului',
      autor: 'Carlos Ruiz Zafón',
      coperta_url: null,
      categorii: ['Mister', 'Roman istoric'],
      rating_mediu: 4.3,
    },
  },
]
