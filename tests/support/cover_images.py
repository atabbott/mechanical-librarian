"""Synthetic pixel payloads for decoder and guarded-cover proof."""

import io

from PIL import Image


def synthetic_image(kind: str, size: tuple[int, int] = (640, 960)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, kind)
    return output.getvalue()
