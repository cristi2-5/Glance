/**
 * Validation for the authentication forms.
 *
 * The rules mirror the backend's constraints (`app/schemas/auth.py`): a
 * valid email and a password of at least 8 characters at registration.
 * Local validation exists to give instant feedback, not to replace server
 * validation — that remains the authority, and its errors are displayed the
 * same way, through `ApiError.fieldErrors`.
 */

import { z } from 'zod'

/** Minimum password length, identical to `Field(min_length=8)` in the backend. */
const MIN_PASSWORD_LENGTH = 8

export const loginSchema = z.object({
  email: z.email('This email address doesn\'t look valid.'),
  password: z.string().min(1, 'Enter your password.'),
})

export const registerSchema = z
  .object({
    email: z.email('This email address doesn\'t look valid.'),
    password: z
      .string()
      .min(MIN_PASSWORD_LENGTH, `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`),
    confirmPassword: z.string().min(1, 'Confirm your password.'),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: 'Passwords do not match.',
    path: ['confirmPassword'],
  })

export type LoginData = z.infer<typeof loginSchema>
export type RegisterData = z.infer<typeof registerSchema>
