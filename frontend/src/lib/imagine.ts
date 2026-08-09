/**
 * Pregătirea fotografiei de copertă înainte de upload.
 *
 * O poză făcută cu un telefon modern are 8-16 MP și depășește ușor limita de
 * 8 MB a backendului (`MAX_UPLOAD_SIZE_BYTES`). Redimensionăm și recomprimăm
 * *local*, ca să nu încasăm 413 după ce am irosit secunde bune de upload pe
 * Wi-Fi.
 *
 * API-ul folosit e cel contextual din SDK 57 (`ImageManipulator.manipulate`),
 * nu `manipulateAsync`, care e marcat deprecated.
 */

import { ImageManipulator, SaveFormat } from 'expo-image-manipulator'

/**
 * Lățimea maximă trimisă spre backend.
 *
 * Backendul redimensionează oricum la 768 px pentru OCR, dar îi lăsăm o marjă:
 * OCR-ul se descurcă mai bine cu detaliile fine dintr-un titlu mic dacă
 * primește o imagine mai bogată decât ținta lui finală.
 */
const LATIME_MAXIMA_PX = 1600

/** Calitatea JPEG a imaginii trimise. 0.85 e limita de la care artefactele încep să afecteze OCR-ul. */
const CALITATE_JPEG = 0.85

/** Rezultatul pregătirii unei imagini pentru upload. */
export interface ImaginePregatita {
  uri: string
  width: number
  height: number
}

/**
 * Redimensionează și recomprimă o fotografie de copertă.
 *
 * Args:
 *   uri: URI-ul local al fotografiei (din `takePictureAsync`).
 *   latimeOriginala: Lățimea imaginii sursă, dacă e cunoscută. Când e sub
 *     `LATIME_MAXIMA_PX`, se sare peste redimensionare ca să nu mărim artificial
 *     o imagine mică — upscaling-ul nu adaugă informație, doar dimensiune.
 *
 * Returns:
 *   Imaginea pregătită, ca fișier JPEG în directorul cache.
 */
export async function pregatesteCopertaPentruUpload(
  uri: string,
  latimeOriginala?: number
): Promise<ImaginePregatita> {
  const context = ImageManipulator.manipulate(uri)

  const trebuieRedimensionata = latimeOriginala === undefined || latimeOriginala > LATIME_MAXIMA_PX
  if (trebuieRedimensionata) {
    context.resize({ width: LATIME_MAXIMA_PX })
  }

  const imagineRandata = await context.renderAsync()
  const rezultat = await imagineRandata.saveAsync({
    format: SaveFormat.JPEG,
    compress: CALITATE_JPEG,
  })

  return {
    uri: rezultat.uri,
    width: rezultat.width,
    height: rezultat.height,
  }
}
