import io
import json
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw, PngImagePlugin, features

from mechanical_librarian.adapters import cover_artifacts
from mechanical_librarian.adapters.cover_artifacts import (
    CoverDecodeRejected,
    decode_cover,
)
from mechanical_librarian.bootstrap import cover_child
from mechanical_librarian.domain.cover_evidence import CoverSourceState
from mechanical_librarian.domain.cover_rules import CoverMeasures, same_art
from tests.support.cover_images import synthetic_image as _image


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


class _AnsweringChild:
    """A decoder child that answers with a fixed protocol document."""

    returncode = 0
    pid = 0

    def __init__(self, document: dict[str, object]) -> None:
        self._stdout = json.dumps(document).encode()

    def communicate(self, *_: object, **__: object) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _design(size: tuple[int, int], palette: int = 0) -> Image.Image:
    """A cover-like design of flat shapes, drawn in proportion to its size."""
    width, height = size
    image = Image.new("RGB", size, ((40 + palette * 90) % 256, 60, 110))
    draw = ImageDraw.Draw(image)
    for index in range(6):
        left = (index * 37 + palette * 53) % 70 / 100 * width
        top = (index * 23 + palette * 29) % 80 / 100 * height
        colour = ((index * 70 + palette * 40) % 256, (index * 110) % 256, 200)
        box = (left, top, left + width * 0.3, top + height * 0.15)
        if (index + palette) % 2:
            draw.ellipse(box, fill=colour)
        else:
            draw.rectangle(box, fill=colour)
    return image


def _measured(payload: bytes) -> tuple[CoverMeasures, tuple[int, int]]:
    artifact = decode_cover(payload).artifact
    assert artifact.measures is not None
    return artifact.measures, (artifact.width, artifact.height)


def test_the_child_measures_its_own_preview_from_decoded_pixels() -> None:
    decoded = decode_cover(_gradient((480, 720)))
    measures = decoded.artifact.measures
    preview = Image.open(io.BytesIO(decoded.preview))

    assert measures is not None
    # The colour histogram counts every preview pixel, so the measures were
    # read from the 320x480 preview, not from the 480x720 original.
    assert (320, 480) == preview.size
    assert 320 * 480 == sum(measures.colour)
    assert 48 == len(measures.layout)
    assert 16 == len(measures.phash) == len(measures.dhash)


def test_one_design_measures_as_the_same_art_across_encodings_and_scales() -> None:
    image = _design((960, 1440))
    large = io.BytesIO()
    image.save(large, "PNG")
    small = io.BytesIO()
    image.resize((320, 480), Image.Resampling.LANCZOS).save(small, "JPEG", quality=80)

    assert same_art(*_measured(large.getvalue()), *_measured(small.getvalue()))


def test_two_different_designs_are_not_the_same_art() -> None:
    assert not same_art(*_measured(_gradient()), *_measured(_image("PNG", (240, 360))))


def test_a_rotated_original_measures_as_its_upright_twin() -> None:
    upright = Image.open(io.BytesIO(_gradient((240, 360))))
    straight = io.BytesIO()
    upright.save(straight, "PNG")
    turned = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # Rotate 90 CW to display, so the stored pixels lie on their side.
    upright.transpose(Image.Transpose.ROTATE_90).save(turned, "PNG", exif=exif)

    # The stated width and height stay the stored ones; only the pixels turn.
    assert _measured(straight.getvalue())[0] == _measured(turned.getvalue())[0]


def test_stated_metadata_never_feeds_the_measures() -> None:
    image = Image.open(io.BytesIO(_gradient((240, 360))))
    plain = io.BytesIO()
    image.save(plain, "PNG")
    stated = PngImagePlugin.PngInfo()
    stated.add_text("width", "9999")
    stated.add_text("height", "9999")
    claimed = io.BytesIO()
    image.save(claimed, "PNG", pnginfo=stated, dpi=(10, 10))

    assert plain.getvalue() != claimed.getvalue()
    assert _measured(plain.getvalue()) == _measured(claimed.getvalue())


def _bad_hash(measures: dict[str, Any]) -> None:
    measures["phash"] = "0" * 15


def _short_histogram(measures: dict[str, Any]) -> None:
    measures["colour"] = measures["colour"][:-1]


def _layout_overflow(measures: dict[str, Any]) -> None:
    measures["layout"] = [256, *measures["layout"][1:]]


def _missing_dhash(measures: dict[str, Any]) -> None:
    del measures["dhash"]


def _empty_histogram(measures: dict[str, Any]) -> None:
    measures["colour"] = [0] * 64


@pytest.mark.parametrize(
    "tamper",
    (
        pytest.param(_bad_hash, id="bad_hash"),
        pytest.param(_short_histogram, id="short_histogram"),
        pytest.param(_layout_overflow, id="layout_overflow"),
        pytest.param(_missing_dhash, id="missing_dhash"),
        pytest.param(_empty_histogram, id="empty_histogram"),
    ),
)
def test_malformed_child_measures_are_a_rejected_decode(
    tamper: Callable[[dict[str, Any]], None],
) -> None:
    payload = _gradient()
    document = cover_child._decode(payload)
    tamper(cast("dict[str, Any]", document["measures"]))

    with (
        patch.object(
            cover_artifacts.subprocess,
            "Popen",
            return_value=_AnsweringChild(document),
        ),
        pytest.raises(CoverDecodeRejected) as raised,
    ):
        decode_cover(payload)
    assert CoverSourceState.DECODE_FAILED == raised.value.state


def test_the_unaltered_child_document_is_accepted_through_the_same_seam() -> None:
    payload = _gradient()
    with patch.object(
        cover_artifacts.subprocess,
        "Popen",
        return_value=_AnsweringChild(cover_child._decode(payload)),
    ):
        assert decode_cover(payload).artifact == decode_cover(payload).artifact


def _reference_phash(image: Image.Image) -> str:
    """imagehash's pHash written out directly, with no rounding at all.

    A 32x32 LANCZOS grayscale sample, the unnormalised DCT-II along each axis
    (scipy's ``dct`` with no norm), the 8x8 low band in row-major order against
    its median, and the most significant bit first.
    """
    import math

    samples = image.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    pixels = samples.tobytes()
    band = [
        sum(
            4
            * math.cos(math.pi * u * (2 * y + 1) / 64)
            * math.cos(math.pi * v * (2 * x + 1) / 64)
            * pixels[y * 32 + x]
            for y in range(32)
            for x in range(32)
        )
        for u in range(8)
        for v in range(8)
    ]
    ordered = sorted(band)
    median = (ordered[31] + ordered[32]) / 2
    value = 0
    for coefficient in band:
        value = (value << 1) | int(coefficient > median)
    return f"{value:016x}"


def test_flat_previews_hash_alike_whatever_their_grey() -> None:
    """Residue in a flat preview's zero coefficients never becomes hash bits."""
    from mechanical_librarian.adapters.cover_measures import measure_preview

    hashes = {
        grey: measure_preview(Image.new("RGBA", (320, 480), (grey, grey, grey, 255)))[
            "phash"
        ]
        for grey in (1, 11, 12, 128, 254, 255)
    }
    assert set(hashes.values()) == {"8000000000000000"}
    black = measure_preview(Image.new("RGBA", (320, 480), (0, 0, 0, 255)))
    assert black["phash"] == "0000000000000000"


def test_the_flat_grey_regression_holds_through_the_decoder() -> None:
    def flat(grey: int) -> str:
        output = io.BytesIO()
        Image.new("RGB", (320, 480), (grey, grey, grey)).save(output, "PNG")
        measures = decode_cover(output.getvalue()).artifact.measures
        assert measures is not None
        return measures.phash

    assert flat(11) == flat(12)


@pytest.mark.parametrize("seed", range(6))
def test_the_perceptual_hash_matches_imagehash_on_ordinary_images(seed: int) -> None:
    """The stabilised transform keeps imagehash's bits, which the study used."""
    import random

    from mechanical_librarian.adapters.cover_measures import _phash

    generator = random.Random(seed)
    image = Image.new("RGB", (320, 480))
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        left, top = generator.randrange(320), generator.randrange(480)
        right = left + generator.randrange(8, 160)
        bottom = top + generator.randrange(8, 240)
        fill = (
            generator.randrange(256),
            generator.randrange(256),
            generator.randrange(256),
        )
        draw.rectangle((left, top, right, bottom), fill=fill)
    noisy = bytes(
        min(255, max(0, channel + generator.randrange(-6, 7)))
        for channel in image.tobytes()
    )
    image = Image.frombytes("RGB", image.size, noisy)

    assert _phash(image) == _reference_phash(image)
