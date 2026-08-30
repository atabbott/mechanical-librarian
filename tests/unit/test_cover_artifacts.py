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


def _gradient(size: tuple[int, int] = (240, 360)) -> bytes:
    """One image whose brightness varies, so its hash is not degenerate."""
    image = Image.new("RGB", size)
    image.putdata(
        [
            (index % 251, (index * 7) % 241, (index * 13) % 239)
            for index in range(size[0] * size[1])
        ]
    )
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_the_isolated_child_states_a_64_bit_perceptual_hash() -> None:
    decoded = decode_cover(_gradient())
    value = decoded.artifact.perceptual_hash

    assert value is not None
    assert 16 == len(value)
    assert value == value.lower()
    int(value, 16)


def test_the_perceptual_hash_is_deterministic_for_one_image() -> None:
    payload = _gradient()

    assert (
        decode_cover(payload).artifact.perceptual_hash
        == decode_cover(payload).artifact.perceptual_hash
    )


def test_one_design_hashes_alike_across_encodings_and_scales() -> None:
    """The rule compares two authorities' art, which is never byte-identical."""
    image = Image.open(io.BytesIO(_gradient((480, 720))))
    large = io.BytesIO()
    image.save(large, "PNG")
    small = io.BytesIO()
    image.resize((240, 360), Image.Resampling.LANCZOS).save(small, "JPEG", quality=80)

    left = decode_cover(large.getvalue()).artifact.perceptual_hash
    right = decode_cover(small.getvalue()).artifact.perceptual_hash

    assert left is not None
    assert right is not None
    assert bin(int(left, 16) ^ int(right, 16)).count("1") <= 10


def test_two_different_designs_do_not_hash_alike() -> None:
    left = decode_cover(_gradient()).artifact.perceptual_hash
    right = decode_cover(_image("PNG", (240, 360))).artifact.perceptual_hash

    assert left is not None
    assert right is not None
    assert left != right


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
        pytest.param(
            lambda: _huge_png_header(100_000, 100_000), id="decompression_bomb"
        ),
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
