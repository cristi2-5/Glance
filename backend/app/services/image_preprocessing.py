"""Cover image preprocessing shared by OCR and the vision fallback.

Both `OcrService` and `VisionService`'s Moondream fallback consume the same
prepared JPEG bytes, so neither owns this step: EXIF rotation, downscaling
to a bounded long edge, and JPEG recompression happen once, here.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.exceptions import ImageProcessingFailed


@dataclass(frozen=True)
class PreparedImage:
    """A cover image ready for OCR / vision consumption.

    Attributes:
        jpeg_bytes: The recompressed JPEG content.
        width: The final width, in pixels.
        height: The final height, in pixels.
    """

    jpeg_bytes: bytes
    width: int
    height: int


def preprocess_cover(content: bytes) -> PreparedImage:
    """Normalizes a raw uploaded image for OCR and vision processing.

    Applies EXIF-aware rotation, converts to RGB, downscales the long edge
    to `Settings.image_max_edge_px` (never upscales), and recompresses as
    JPEG at `Settings.image_jpeg_quality`.

    Args:
        content: The raw bytes of the uploaded file.

    Returns:
        The prepared image, as JPEG bytes plus final dimensions.

    Raises:
        ImageProcessingFailed: If `content` is not a decodable image.
    """
    settings = get_settings()

    try:
        opened = Image.open(BytesIO(content))
        opened.load()  # type: ignore[no-untyped-call]
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageProcessingFailed("The uploaded file is not a valid image.") from exc

    image: Image.Image | None = ImageOps.exif_transpose(opened)
    if image is None:
        raise ImageProcessingFailed("The uploaded file is not a valid image.")

    if image.mode != "RGB":
        image = image.convert("RGB")

    long_edge = max(image.width, image.height)
    if long_edge > settings.image_max_edge_px:
        scale = settings.image_max_edge_px / long_edge
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=settings.image_jpeg_quality, optimize=True)

    return PreparedImage(jpeg_bytes=buffer.getvalue(), width=image.width, height=image.height)
