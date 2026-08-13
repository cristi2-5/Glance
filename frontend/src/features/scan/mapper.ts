/**
 * Translating a job's `result` field into the display model.
 *
 * The backend fills this shape progressively as modules land: recognition
 * in Module 3, catalog metadata in Module 4. (The Module 5 summary is not
 * part of it — it comes from its own endpoint, keyed on `book_id`.) We
 * detect whether the shape received is real and tell the caller — the
 * screen shows an explicit indicator, so we never confuse a mock with a
 * real result.
 *
 * Every field is read defensively rather than trusted, because this data
 * crosses a version boundary: a phone running a build newer than the
 * backend it's pointed at would otherwise crash on a missing key.
 */

import { DEMO_ANALYSIS } from '@/mocks/analiza'
import type { AnalysisResult } from '@/types/api'

/** The interpreted result, with the data's origin marked explicitly. */
export interface DisplayableAnalysis {
  analysis: AnalysisResult
  /** `true` when the backend hasn't produced real data yet and we're showing the mock. */
  isDemo: boolean
}

/**
 * Checks whether `result` has the real shape, produced by Modules 3-5.
 *
 * The minimum criterion is the presence of a non-empty title — without it
 * there's nothing to show, regardless of what other fields exist.
 */
function hasRealShape(result: Record<string, unknown>): boolean {
  return typeof result['title'] === 'string' && result['title'].trim().length > 0
}

/**
 * Converts a finished job's `result` into displayable data.
 *
 * Args:
 *   result: The job's `result` field, or `null` if missing.
 *
 * Returns:
 *   The analysis to display, marked as real or demo.
 */
export function interpretResult(result: Record<string, unknown> | null): DisplayableAnalysis {
  if (!result || !hasRealShape(result)) {
    return { analysis: DEMO_ANALYSIS, isDemo: true }
  }

  const raw = result as Partial<AnalysisResult>

  return {
    isDemo: false,
    analysis: {
      title: raw.title ?? '',
      author: raw.author ?? null,
      confidence: typeof raw.confidence === 'number' ? raw.confidence : 0,
      method: raw.method ?? 'ocr',
      needs_review: raw.needs_review ?? false,
      corrected: raw.corrected ?? false,
      book_id: typeof raw.book_id === 'number' ? raw.book_id : null,
      // Absent rather than false is what a pre-Module-4 backend sends, and
      // both mean the same thing here: no catalog data to show.
      metadata_found: raw.metadata_found ?? false,
      description: raw.description ?? null,
      cover_url: raw.cover_url ?? null,
      categories: Array.isArray(raw.categories) ? raw.categories : [],
      average_rating: typeof raw.average_rating === 'number' ? raw.average_rating : null,
      ratings_count: typeof raw.ratings_count === 'number' ? raw.ratings_count : null,
      source_count: typeof raw.source_count === 'number' ? raw.source_count : 0,
    },
  }
}
