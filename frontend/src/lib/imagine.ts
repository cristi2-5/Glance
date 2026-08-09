/**
 * Preparing the cover photo before upload.
 *
 * A photo taken with a modern phone is 8-16 MP and easily exceeds the
 * backend's 8 MB limit (`MAX_UPLOAD_SIZE_BYTES`). We resize and recompress
 * *locally*, so we don't get a 413 after wasting good seconds uploading over
 * Wi-Fi.
 *
 * The API used is the contextual one from SDK 57 (`ImageManipulator.manipulate`),
 * not `manipulateAsync`, which is marked deprecated.
 */

import { ImageManipulator, SaveFormat } from 'expo-image-manipulator'

/**
 * Maximum width sent to the backend.
 *
 * The backend resizes to 768 px for OCR anyway, but we leave it some margin:
 * OCR handles fine details in a small title better if it receives a richer
 * image than its final target.
 */
const MAX_WIDTH_PX = 1600

/** JPEG quality of the uploaded image. 0.85 is the point past which artifacts start affecting OCR. */
const JPEG_QUALITY = 0.85

/** The result of preparing an image for upload. */
export interface PreparedImage {
  uri: string
  width: number
  height: number
}

/**
 * Resizes and recompresses a cover photo.
 *
 * Args:
 *   uri: The local URI of the photo (from `takePictureAsync`).
 *   originalWidth: The width of the source image, if known. When it's below
 *     `MAX_WIDTH_PX`, resizing is skipped so we don't artificially enlarge a
 *     small image — upscaling adds no information, only size.
 *
 * Returns:
 *   The prepared image, as a JPEG file in the cache directory.
 */
export async function prepareCoverForUpload(
  uri: string,
  originalWidth?: number
): Promise<PreparedImage> {
  const context = ImageManipulator.manipulate(uri)

  const needsResize = originalWidth === undefined || originalWidth > MAX_WIDTH_PX
  if (needsResize) {
    context.resize({ width: MAX_WIDTH_PX })
  }

  const renderedImage = await context.renderAsync()
  const result = await renderedImage.saveAsync({
    format: SaveFormat.JPEG,
    compress: JPEG_QUALITY,
  })

  return {
    uri: result.uri,
    width: result.width,
    height: result.height,
  }
}
