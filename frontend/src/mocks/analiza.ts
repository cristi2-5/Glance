/**
 * Date demonstrative pentru ecranele care depind de module de backend
 * încă neimplementate (Modulele 3-6).
 *
 * Respectă exact tipurile din `src/types/api.ts`. Când backendul începe să
 * întoarcă date reale, ecranele nu se schimbă — doar mapper-ul încetează să
 * mai cadă pe mock.
 */

import type { RezultatAnaliza } from '@/types/api'

export const ANALIZA_DEMO: RezultatAnaliza = {
  titlu: 'Numele trandafirului',
  autor: 'Umberto Eco',
  incredere: 0.94,
  rezumat:
    'Într-o abație benedictină din nordul Italiei anului 1327, călugărul franciscan William de Baskerville ' +
    'este chemat să medieze o dispută teologică, dar ajunge să investigheze o serie de morți suspecte. ' +
    'Împreună cu novicele Adso, descoperă că firul crimelor duce spre biblioteca abației — un labirint ' +
    'în care e ascunsă o carte pe care cineva ucide ca să o țină nedeschisă. Romanul îmbină ancheta ' +
    'polițistă cu eseul despre semiotică, erezie și puterea cărților.',
  coperta_url: null,
  categorii: ['Roman istoric', 'Mister', 'Literatură italiană'],
  rating_mediu: 4.2,
  recenzii: [
    {
      id: 'wiki-reception-1',
      sursa: 'wikipedia',
      titlu_sursa: 'Reception',
      extras:
        'Romanul a fost primit ca o reușită rară: un policier medieval care funcționează deopotrivă ca ' +
        'lectură populară și ca tratat despre interpretare. Criticii au remarcat densitatea referințelor, ' +
        'considerată de unii o barieră, de alții tocmai sursa plăcerii.',
      url: 'https://en.wikipedia.org/wiki/The_Name_of_the_Rose',
    },
    {
      id: 'ol-subjects-1',
      sursa: 'open_library',
      titlu_sursa: 'Descriere Open Library',
      extras:
        'Primul roman al semioticianului Umberto Eco, publicat în 1980, tradus în peste 40 de limbi. ' +
        'Este citat frecvent ca exemplu de roman postmodern accesibil publicului larg.',
      url: 'https://openlibrary.org/works/OL27479W',
    },
    {
      id: 'gb-description-1',
      sursa: 'google_books',
      titlu_sursa: 'Descriere editor',
      extras:
        'O anchetă tulburătoare într-o abație în care moartea pare să urmeze tiparul Apocalipsei, ' +
        'spusă de un narator ajuns la capătul vieții.',
      url: null,
    },
  ],
}
