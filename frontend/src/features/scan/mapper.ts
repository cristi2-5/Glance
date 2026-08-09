/**
 * Translating a job's `result` field into the display model.
 *
 * Backend Module 2 still returns a placeholder
 * (`{message, image_size_bytes}`), because OCR and synthesis only arrive in
 * Modules 3-5. We detect the shape received and tell the caller whether the
 * data is real or demo — the screen shows an explicit indicator, so we
 * never confuse a mock with a real result.
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
      summary: raw.summary ?? null,
      cover_url: raw.cover_url ?? null,
      categories: Array.isArray(raw.categories) ? raw.categories : [],
      average_rating: typeof raw.average_rating === 'number' ? raw.average_rating : null,
      reviews: Array.isArray(raw.reviews) ? raw.reviews : [],
    },
  }
}
