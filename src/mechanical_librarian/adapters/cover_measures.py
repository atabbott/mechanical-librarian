"""Pixel measures the isolated cover decoder computes (#1363, ADR-0064, ADR-0093).

Only ``bootstrap.cover_child`` calls these, inside the constrained decoder
process, so untrusted bytes are still decoded in exactly one place. The
same-art measures follow the 2026-10-24 near-duplicate study's definitions and
input, the 320x480 preview composited on white, so the cover rules' thresholds
keep their measured meaning.
"""

from __future__ import annotations

import math
import struct

from PIL import Image, ImageChops

# A 64-bit difference hash: eight rows of nine grayscale samples, compared left
# to right, gives eight bits a row.
_HASH_WIDTH = 9
_HASH_HEIGHT = 8
# The same-art measures (#1363, ADR-0093) are read from the 320x480 preview,
# composited on white, because that is the image the near-duplicate study
# measured: its thresholds keep their meaning only over the same input.
PREVIEW_BOX = (320, 480)
_DCT_SIZE = 32
_DCT_BAND = 8
_DCT_ROWS = tuple(
    tuple(
        2 * math.cos(math.pi * k * (2 * n + 1) / (2 * _DCT_SIZE))
        for n in range(_DCT_SIZE)
    )
    for k in range(_DCT_BAND)
)
# The decimal places a DCT coefficient keeps: residue sits near 1e-9 here.
_DCT_DIGITS = 6
_LAYOUT_CELLS = 4


def difference_hash(image: Image.Image) -> str:
    """Return the 64-bit difference hash of one decoded image as 16 hex digits.

    Rows run top to bottom and bits run most significant first, so the same
    image always produces the same string and two images may be compared by
    Hamming distance without either side knowing how the bits were laid out.
    """
    samples = image.convert("L").resize(
        (_HASH_WIDTH, _HASH_HEIGHT), Image.Resampling.LANCZOS
    )
    # One grayscale byte per sample in row-major order, so the raster is read
    # without a pixel-access object.
    raster = samples.tobytes()
    if len(raster) != _HASH_WIDTH * _HASH_HEIGHT:
        raise ValueError("cover hash sampling failed")
    value = 0
    for row in range(_HASH_HEIGHT):
        offset = row * _HASH_WIDTH
        for column in range(_HASH_WIDTH - 1):
            value = (value << 1) | int(
                raster[offset + column] > raster[offset + column + 1]
            )
    return f"{value:016x}"


def _phash(image: Image.Image) -> str:
    """Return the 64-bit DCT perceptual hash of one image as 16 hex digits.

    The imagehash definition: a 32x32 grayscale sample, the unnormalised 2-D
    DCT-II, and its 8x8 low band compared with that band's median. Only the
    eight low rows of the transform are ever needed, so only they are built.
    """
    samples = image.convert("L").resize(
        (_DCT_SIZE, _DCT_SIZE), Image.Resampling.LANCZOS
    )
    raster = samples.tobytes()
    if len(raster) != _DCT_SIZE * _DCT_SIZE:
        raise ValueError("cover hash sampling failed")
    rows = [raster[row * _DCT_SIZE : (row + 1) * _DCT_SIZE] for row in range(_DCT_SIZE)]
    # The column transform of every row, then the row transform of the band.
    columns = [
        [
            sum(basis[n] * rows[n][column] for n in range(_DCT_SIZE))
            for column in range(_DCT_SIZE)
        ]
        for basis in _DCT_ROWS
    ]
    # A coefficient that is exactly zero, such as every non-DC coefficient of a
    # flat grey, comes out as floating-point residue far below 1e-6, and that
    # residue would decide hash bits. Rounding to a millionth makes it zero; a
    # real coefficient moves by under half a millionth, so its bit changes only
    # if it sits that close to the median, and imagehash's bits otherwise stand.
    band = [
        round(sum(basis[n] * line[n] for n in range(_DCT_SIZE)), _DCT_DIGITS)
        for line in columns
        for basis in _DCT_ROWS
    ]
    ordered = sorted(band)
    median = (ordered[31] + ordered[32]) / 2
    value = 0
    for coefficient in band:
        value = (value << 1) | int(coefficient > median)
    return f"{value:016x}"


def _single(value: float) -> float:
    """Round one value to IEEE single precision, as the study's arrays were."""
    return float(struct.unpack("f", struct.pack("f", value))[0])


def _composite_quarter(channel: int, alpha: int) -> int:
    """The 0 to 3 quarter of one channel composited on white in single precision."""
    weight = _single(alpha / 255.0)
    value = _single(_single(channel * weight) + _single(255.0 * _single(1.0 - weight)))
    return min(3, int(value // 64))


def measure_preview(preview: Image.Image) -> dict[str, object]:
    """Return the same-art measures of one RGBA preview, composited on white."""
    white = Image.new("RGBA", preview.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(white, preview).convert("RGB")
    # A fully opaque preview's alpha band has the extrema (255, 255).
    if preview.getchannel("A").getextrema() == (255, 255):
        red, green, blue = flat.split()
        # A 4x4x4 joint histogram: each channel's quarter, combined as one byte.
        joint = ImageChops.add(
            ImageChops.add(
                red.point(lambda value: (value // 64) * 16),
                green.point(lambda value: (value // 64) * 4),
            ),
            blue.point(lambda value: value // 64),
        )
        colour = joint.histogram()[:64]
    else:
        # The study binned the unrounded single-precision composite, so a
        # translucent pixel is binned from that value, not the rounded image.
        colour = [0] * 64
        raster = preview.tobytes()
        quarters: dict[tuple[int, int], int] = {}
        for offset in range(0, len(raster), 4):
            alpha = raster[offset + 3]
            index = 0
            for channel in raster[offset : offset + 3]:
                quarter = quarters.get((channel, alpha))
                if quarter is None:
                    quarter = quarters[(channel, alpha)] = _composite_quarter(
                        channel, alpha
                    )
                index = index * 4 + quarter
            colour[index] += 1
    layout = list(
        flat.resize((_LAYOUT_CELLS, _LAYOUT_CELLS), Image.Resampling.BOX).tobytes()
    )
    if sum(colour) != flat.width * flat.height or len(layout) != 48:
        raise ValueError("cover measure sampling failed")
    return {
        "colour": colour,
        "dhash": difference_hash(flat),
        "layout": layout,
        "phash": _phash(flat),
    }


__all__ = ["PREVIEW_BOX", "difference_hash", "measure_preview"]
