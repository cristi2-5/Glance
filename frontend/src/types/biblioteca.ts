/**
 * Tipuri pentru istoricul de lectură, preferințe și recomandări.
 *
 * Backendul nu expune încă aceste resurse — apar la **Modulul 6**
 * (`ReadingHistory`, `Preference`, recomandări content-based). Le definim
 * de pe acum ca ecranele să fie scrise contra unui contract stabil; mock-urile
 * din `src/mocks/` respectă exact aceste forme.
 *
 * Când Modulul 6 e gata, se compară cu schema reală și se ajustează *aici*,
 * o singură dată — ecranele urmează prin typecheck.
 */

/** O carte, așa cum apare în liste și carduri. */
export interface CarteSumar {
  id: string
  titlu: string
  autor: string
  coperta_url: string | null
  categorii: string[]
  rating_mediu: number | null
}

/** O intrare din istoricul de lectură al utilizatorului. */
export interface IntrareIstoric {
  id: string
  carte: CarteSumar
  /** Nota dată de utilizator, 1-5. `null` dacă n-a evaluat încă. */
  nota_utilizator: number | null
  /** ISO 8601 — când a fost scanată sau marcată ca citită. */
  citita_la: string
}

/**
 * O recomandare, cu explicația care o justifică.
 *
 * Explicația nu e cosmetică: Modulul 6 e content-based, deci fiecare
 * recomandare *poate* fi motivată prin cărțile care au generat-o.
 */
export interface Recomandare {
  id: string
  carte: CarteSumar
  /** Scorul de potrivire, 0-1. */
  scor: number
  /** Text de forma „pentru că ți-a plăcut X". */
  explicatie: string
}

/** Preferințele declarate ale utilizatorului. */
export interface PreferinteUtilizator {
  genuri_favorite: string[]
  autori_favoriti: string[]
}
