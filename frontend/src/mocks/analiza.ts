/**
 * Demo data for screens that depend on backend modules not yet
 * implemented (Module 6).
 *
 * Respects exactly the types in `src/types/api.ts`. Once the backend
 * starts returning real data, the screens don't change — only the mapper
 * stops falling back to the mock.
 *
 * `DEMO_ANALYSIS` is the fallback the result screen shows when the backend
 * returns a shape this build doesn't recognize, and it is always rendered
 * behind a visible demo banner. The summary is deliberately *not* mocked:
 * it has its own endpoint and its own loading and unavailable states, and
 * a fake cited summary is exactly the kind of thing that would be
 * indistinguishable from a real one during testing.
 */

import type { AnalysisResult } from '@/types/api'

export const DEMO_ANALYSIS: AnalysisResult = {
  title: 'The Name of the Rose',
  author: 'Umberto Eco',
  confidence: 0.94,
  method: 'ocr',
  needs_review: false,
  corrected: false,
  book_id: null,
  metadata_found: true,
  description:
    'The first novel by semiotician Umberto Eco: a murder investigation in a fourteenth-century ' +
    'abbey, which is also a treatise on signs, heresy, and the danger of a book.',
  cover_url: null,
  categories: ['Historical fiction', 'Mystery', 'Italian literature'],
  average_rating: 4.2,
  ratings_count: 3140,
  source_count: 3,
}
