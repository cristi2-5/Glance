"""Tests for `app.services.image_preprocessing.preprocess_cover`."""

from io import BytesIO

import pytest
from PIL import Image

from app.core.exceptions import ImageProcessingFailed
from app.services.image_preprocessing import preprocess_cover


def _make_jpeg(width: int, height: int, exif_orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), color=(200, 120, 80))
    buffer = BytesIO()
    if exif_orientation is not None:
        exif = image.getexif()
        exif[0x0112] = exif_orientation  # Orientation tag
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_downscales_long_edge_to_max() -> None:
    content = _make_jpeg(2400, 1600)

    prepared = preprocess_cover(content)

    assert max(prepared.width, prepared.height) == 768
    assert prepared.jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic bytes


def test_does_not_upscale_small_images() -> None:
    content = _make_jpeg(300, 200)

    prepared = preprocess_cover(content)

    assert prepared.width == 300
    assert prepared.height == 200


def test_exif_orientation_is_applied() -> None:
    # Orientation 6 = rotate 270 (portrait source photographed sideways), so
    # a 400x600 buffer with this tag should present rotated, as 600x400.
    # Kept under image_max_edge_px so the assertion isolates the rotation
    # from the (separately tested) downscaling behavior.
    content = _make_jpeg(400, 600, exif_orientation=6)

    prepared = preprocess_cover(content)

    assert prepared.width == 600
    assert prepared.height == 400


def test_rejects_undecodable_content() -> None:
    with pytest.raises(ImageProcessingFailed):
        preprocess_cover(b"not an image, just garbage bytes")
