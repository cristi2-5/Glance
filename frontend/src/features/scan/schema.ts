/**
 * Validation for the manual correction form.
 *
 * Mirrors the backend's `CorrectionRequest` (`app/schemas/vision.py`):
 * title 1-300 characters, author optional and capped at 300. Local
 * validation gives instant feedback; the backend remains the authority.
 */

import { z } from 'zod'

const MAX_LENGTH = 300

export const correctionSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, 'Enter the title.')
    .max(MAX_LENGTH, `Title must be at most ${MAX_LENGTH} characters.`),
  author: z.string().trim().max(MAX_LENGTH, `Author must be at most ${MAX_LENGTH} characters.`),
})

export type CorrectionData = z.infer<typeof correctionSchema>
