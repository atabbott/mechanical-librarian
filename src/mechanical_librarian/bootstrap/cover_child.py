"""Private one-shot decoder process for untrusted cover image bytes."""

from __future__ import annotations

import base64
import ctypes
import errno
import io
import json
import math
import os
import platform
import resource
import socket
import struct
import sys
import warnings

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError, features

_MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
_MAX_PREVIEW_BYTES = 1024 * 1024
_MAX_PIXELS = 16_777_216
# A 64-bit difference hash: eight rows of nine grayscale samples, compared left
# to right, gives eight bits a row.  The hash is computed here because this is
# the one place the untrusted bytes are ever decoded, and nowhere else may
# decode them again to get it.
_HASH_WIDTH = 9
_HASH_HEIGHT = 8
# The same-art measures (#1363, ADR-0093) are read from the 320x480 preview,
# composited on white, because that is the image the near-duplicate study
# measured: its thresholds keep their meaning only over the same input.
_PREVIEW_BOX = (320, 480)
_DCT_SIZE = 32
_DCT_BAND = 8
_DCT_ROWS = tuple(
    tuple(
        2 * math.cos(math.pi * k * (2 * n + 1) / (2 * _DCT_SIZE))
        for n in range(_DCT_SIZE)
    )
    for k in range(_DCT_BAND)
)
_LAYOUT_CELLS = 4


def _limits() -> None:
    for limit, maximum in (
        (resource.RLIMIT_AS, 256 * 1024 * 1024),
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_NPROC, 1),
        (resource.RLIMIT_CORE, 0),
    ):
        try:
            soft, hard = resource.getrlimit(limit)
            accepted = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
            resource.setrlimit(limit, (min(accepted, maximum), accepted))
        except (ValueError, OSError):
            if sys.platform.startswith("linux"):
                raise


def _deny_network(*_: object, **__: object) -> object:
    raise OSError("network is unavailable in cover decoder")


def _no_network() -> None:
    socket.socket = _deny_network  # type: ignore[assignment,misc]  # reason: deliberate monkeypatch of the socket type to deny network use in this one-shot child
    socket.create_connection = _deny_network  # type: ignore[assignment]
    socket.getaddrinfo = _deny_network  # type: ignore[assignment]


def _isolate_network() -> None:
    if not sys.platform.startswith("linux"):
        return
    numbers = {
        "x86_64": (41, 53),
        "aarch64": (198, 199),
    }.get(platform.machine())
    if numbers is None:
        raise OSError("network syscall filter is unavailable")

    class Filter(ctypes.Structure):
        _fields_ = (
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        )

    class Program(ctypes.Structure):
        _fields_ = (("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter)))

    instructions = [Filter(0x20, 0, 0, 0)]
    for number in numbers:
        instructions.extend(
            (Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, 0x00050000 | errno.EPERM))
        )
    instructions.append(Filter(0x06, 0, 0, 0x7FFF0000))
    array = (Filter * len(instructions))(*instructions)
    program = Program(len(instructions), array)
    library = ctypes.CDLL(None, use_errno=True)
    if (
        library.prctl(38, 1, 0, 0, 0) != 0
        or library.prctl(22, 2, ctypes.byref(program)) != 0
    ):
        raise OSError(ctypes.get_errno(), "network syscall filter is unavailable")


def _difference_hash(image: Image.Image) -> str:
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
    band = [
        sum(basis[n] * line[n] for n in range(_DCT_SIZE))
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


def _measures(preview: Image.Image) -> dict[str, object]:
    """Return the same-art measures of one RGBA preview, composited on white."""
    white = Image.new("RGBA", preview.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(white, preview).convert("RGB")
    if preview.getextrema()[3][0] == 255:
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
        "dhash": _difference_hash(flat),
        "layout": layout,
        "phash": _phash(flat),
    }


def _decode(data: bytes) -> dict[str, object]:
    if not (
        features.check("jpg") and features.check("zlib") and features.check("webp")
    ):
        return {"state": "decode_failed"}
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data))
        except UnidentifiedImageError:
            return {"state": "unsupported_format"}
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            return {"state": "oversized"}
        with image:
            if getattr(image, "n_frames", 1) != 1:
                return {"state": "animated_or_multiframe"}
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                return {"state": "unsupported_format"}
            if (
                image.width > 8192
                or image.height > 8192
                or image.width * image.height > _MAX_PIXELS
            ):
                return {"state": "oversized"}
            try:
                image.load()
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                return {"state": "oversized"}
            oriented = ImageOps.exif_transpose(image)
            # The hash reads the same orientation the preview does, so a
            # rotated original and its upright twin hash alike.
            perceptual_hash = _difference_hash(oriented)
            preview = oriented.convert("RGBA")
            preview.thumbnail(_PREVIEW_BOX, Image.Resampling.LANCZOS)
            measures = _measures(preview)
            output = io.BytesIO()
            preview.save(output, "PNG", optimize=False, compress_level=9)
    encoded = output.getvalue()
    if len(encoded) > _MAX_PREVIEW_BYTES:
        return {"state": "oversized"}
    return {
        "state": "retrieved",
        "format": {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[
            image.format
        ],
        "width": image.width,
        "height": image.height,
        "measures": measures,
        "perceptual_hash": perceptual_hash,
        "preview": base64.b64encode(encoded).decode("ascii"),
    }


def main() -> int:
    try:
        _limits()
        _isolate_network()
        _no_network()
        data = sys.stdin.buffer.read(_MAX_ORIGINAL_BYTES + 1)
        if not data or len(data) > _MAX_ORIGINAL_BYTES:
            os.write(sys.stdout.fileno(), b'{"state":"oversized"}')
            return 0
        result = _decode(data)
        os.write(
            sys.stdout.fileno(),
            json.dumps(result, separators=(",", ":")).encode("ascii"),
        )
        return 0
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
