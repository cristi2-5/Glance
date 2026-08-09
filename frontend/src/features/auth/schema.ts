/**
 * Validarea formularelor de autentificare.
 *
 * Regulile oglindesc constrângerile backendului (`app/schemas/auth.py`):
 * email valid și parolă de minim 8 caractere la înregistrare. Validarea
 * locală există ca să dea feedback instant, nu ca să înlocuiască validarea
 * de server — aceasta rămâne autoritatea, iar erorile ei sunt afișate la
 * fel, prin `ApiError.eroriCampuri`.
 */

import { z } from 'zod'

/** Lungimea minimă a parolei, identică cu `Field(min_length=8)` din backend. */
const LUNGIME_MINIMA_PAROLA = 8

export const schemaLogin = z.object({
  email: z.email('Adresa de email nu pare validă.'),
  password: z.string().min(1, 'Introdu parola.'),
})

export const schemaInregistrare = z
  .object({
    email: z.email('Adresa de email nu pare validă.'),
    password: z
      .string()
      .min(LUNGIME_MINIMA_PAROLA, `Parola trebuie să aibă cel puțin ${LUNGIME_MINIMA_PAROLA} caractere.`),
    confirmare: z.string().min(1, 'Confirmă parola.'),
  })
  .refine((date) => date.password === date.confirmare, {
    message: 'Parolele nu coincid.',
    path: ['confirmare'],
  })

export type DateLogin = z.infer<typeof schemaLogin>
export type DateInregistrare = z.infer<typeof schemaInregistrare>
