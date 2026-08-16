import io
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable

import pytest
from PIL import Image, features

from mechanical_librarian.adapters.cover_artifacts import (
    CoverDecodeRejected,
    decode_cover,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceState


def _image(kind: str, size: tuple[int, int] = (640, 960)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, kind)
    return output.getvalue()


def _huge_png_header(width: int, height: int) -> bytes:
    payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(payload))
        + b"IHDR"
        + payload
        + struct.pack(">I", zlib.crc32(b"IHDR" + payload) & 0xFFFFFFFF)
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_production_decoder_features_are_available() -> None:
    assert features.check("jpg")
    assert features.check("zlib")
    assert features.check("webp")


def test_deterministic_png_preview_strips_metadata_and_does_not_upscale() -> None:
    decoded = decode_cover(_image("JPEG", (100, 200)))
    preview = Image.open(io.BytesIO(decoded.preview))
    assert (100, 200) == preview.size
    assert "PNG" == preview.format
    assert "RGBA" == preview.mode
    assert "image/jpeg" == decoded.artifact.media_type


def test_rejects_unsupported_bytes() -> None:
    with pytest.raises(CoverDecodeRejected) as raised:
        decode_cover(b"not an image")
    assert CoverSourceState.UNSUPPORTED_FORMAT == raised.value.state


def test_scales_large_image_to_fixed_bounds() -> None:
    decoded = decode_cover(_image("PNG", (640, 960)))
    assert Image.open(io.BytesIO(decoded.preview)).width <= 320
    assert Image.open(io.BytesIO(decoded.preview)).height <= 480


def test_rejects_oversized_input_with_typed_state() -> None:
    with pytest.raises(CoverDecodeRejected) as raised:
        decode_cover(b"x" * (8 * 1024 * 1024 + 1))
    assert CoverSourceState.OVERSIZED == raised.value.state


@pytest.mark.parametrize(
    "build",
    (
        pytest.param(lambda: _image("PNG", (4097, 4096)), id="pixel_bomb"),
        pytest.param(lambda: _huge_png_header(100_000, 100_000), id="decompression_bomb"),
    ),
)
def test_real_decoder_child_rejects_pixel_and_decompression_bombs(
    build: Callable[[], bytes],
) -> None:
    with pytest.raises(CoverDecodeRejected) as raised:
        decode_cover(build())
    assert CoverSourceState.OVERSIZED == raised.value.state


def test_accepts_static_webp_and_rejects_multiframe_input() -> None:
    assert "image/webp" == decode_cover(_image("WEBP")).artifact.media_type
    animation = io.BytesIO()
    first = Image.new("RGB", (10, 10), "red")
    second = Image.new("RGB", (10, 10), "blue")
    first.save(animation, "GIF", save_all=True, append_images=[second])
    with pytest.raises(CoverDecodeRejected) as raised:
        decode_cover(animation.getvalue())
    assert CoverSourceState.ANIMATED_OR_MULTIFRAME == raised.value.state


def test_decoder_child_main_starts_with_network_isolation() -> None:
    probe = (
        "import ctypes, os, platform, socket, sys\n"
        "from mechanical_librarian.bootstrap import cover_child\n"
        "def probe_decode(data):\n"
        "  try: socket.create_connection(('8.8.8.8', 53))\n"
        "  except OSError: pass\n"
        "  else: raise SystemExit(7)\n"
        "  if sys.platform.startswith('linux'):\n"
        "    numbers={'x86_64':41,'aarch64':198}\n"
        "    number=numbers.get(platform.machine())\n"
        "    if number is not None:\n"
        "      ctypes.set_errno(0); result=ctypes.CDLL(None, use_errno=True).syscall(number, 2, 1, 0)\n"
        "      if result != -1 or ctypes.get_errno() != 1: raise SystemExit(8)\n"
        "  return {'state':'oversized'}\n"
        "cover_child._decode=probe_decode\n"
        "raise SystemExit(cover_child.main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe],
        input=b"x",
        capture_output=True,
        cwd="/",
    )
    assert 0 == result.returncode
    assert b'{"state":"oversized"}' == result.stdout
