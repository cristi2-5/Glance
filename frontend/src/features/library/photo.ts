/**
 * The reader's own photo of a book, served by the backend.
 *
 * The photo lives in a **private** Blob container and has no public URL:
 * `GET /books/{id}/library/photo` returns it only to the user whose library
 * entry holds it. So an image component cannot just be handed a URL — the
 * request needs the `Authorization` header, which `expo-image` accepts in
 * its `source`.
 *
 * Known limit: these requests bypass the axios interceptor that renews an
 * expired access token, so a photo requested with a token older than its
 * 30-minute life simply fails to load and the cover falls back to the
 * typographic initial, until the next screen reads a fresh token.
 */

import type { ImageSource } from 'expo-image'
import { useEffect, useMemo, useState } from 'react'

import { tokenStore } from '@/api/tokenStore'
import { API_URL } from '@/config/env'

/** Identifies a photo to show: which book, and a value that changes with the photo. */
export interface PhotoRef {
  bookId: number
  /**
   * Any string that changes when the photo may have changed (the entry's
   * `updated_at`). It goes in the query string so the image cache does not
   * keep serving the previous photo after a rescan; the backend ignores it.
   */
  version: string
}

/**
 * Builds the `expo-image` source for a photo reference.
 *
 * Args:
 *   photo: Which photo to show, or `null` for "this book has none".
 *
 * Returns:
 *   An authenticated image source, or `null` while there is nothing to
 *   show (no photo, or the token is not read yet).
 */
export function useLibraryPhoto(photo: PhotoRef | null): ImageSource | null {
  const [token, setToken] = useState<string | null>(null)
  const bookId = photo?.bookId ?? null
  const version = photo?.version ?? null

  useEffect(() => {
    if (bookId === null) {
      return
    }
    let cancelled = false
    void tokenStore.getAccessToken().then((value) => {
      if (!cancelled) {
        setToken(value)
      }
    })
    return () => {
      cancelled = true
    }
  }, [bookId])

  return useMemo(() => {
    if (bookId === null || version === null || token === null) {
      return null
    }
    return {
      uri: `${API_URL}/books/${bookId}/library/photo?v=${encodeURIComponent(version)}`,
      headers: { Authorization: `Bearer ${token}` },
    }
  }, [bookId, version, token])
}
