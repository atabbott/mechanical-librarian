"""Private one-shot decoder process for untrusted cover image bytes."""

from __future__ import annotations

import base64
import ctypes
import errno
import io
import json
import os
import platform
import resource
import socket
import sys
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError, features

_MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
_MAX_PREVIEW_BYTES = 1024 * 1024
_MAX_PIXELS = 16_777_216
# A 64-bit difference hash: eight rows of nine grayscale samples, compared left
# to right, gives eight bits a row.  The hash is computed here because this is
# the one place the untrusted bytes are ever decoded, and nowhere else may
# decode them again to get it.
_HASH_WIDTH = 9
_HASH_HEIGHT = 8


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
            preview.thumbnail((320, 480), Image.Resampling.LANCZOS)
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
