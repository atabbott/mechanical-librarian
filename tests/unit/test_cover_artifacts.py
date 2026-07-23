import io
import subprocess
import sys
import unittest

from PIL import Image
from PIL import features

from mechanical_librarian.adapters.cover_artifacts import CoverDecodeRejected, decode_cover
from mechanical_librarian.domain.cover_evidence import CoverSourceState


def _image(kind: str, size: tuple[int, int] = (640, 960)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, kind)
    return output.getvalue()


class CoverArtifactTests(unittest.TestCase):
    def test_production_decoder_features_are_available(self) -> None:
        self.assertTrue(features.check("jpg"))
        self.assertTrue(features.check("zlib"))
        self.assertTrue(features.check("webp"))

    def test_deterministic_png_preview_strips_metadata_and_does_not_upscale(self) -> None:
        decoded = decode_cover(_image("JPEG", (100, 200)))
        preview = Image.open(io.BytesIO(decoded.preview))
        self.assertEqual((100, 200), preview.size)
        self.assertEqual("PNG", preview.format)
        self.assertEqual("RGBA", preview.mode)
        self.assertEqual("image/jpeg", decoded.artifact.media_type)

    def test_rejects_unsupported_bytes(self) -> None:
        with self.assertRaises(CoverDecodeRejected) as raised:
            decode_cover(b"not an image")
        self.assertEqual(CoverSourceState.UNSUPPORTED_FORMAT, raised.exception.state)

    def test_scales_large_image_to_fixed_bounds(self) -> None:
        decoded = decode_cover(_image("PNG", (640, 960)))
        self.assertLessEqual(Image.open(io.BytesIO(decoded.preview)).width, 320)
        self.assertLessEqual(Image.open(io.BytesIO(decoded.preview)).height, 480)

    def test_rejects_oversized_input_with_typed_state(self) -> None:
        with self.assertRaises(CoverDecodeRejected) as raised:
            decode_cover(b"x" * (8 * 1024 * 1024 + 1))
        self.assertEqual(CoverSourceState.OVERSIZED, raised.exception.state)

    def test_accepts_static_webp_and_rejects_multiframe_input(self) -> None:
        self.assertEqual("image/webp", decode_cover(_image("WEBP")).artifact.media_type)
        animation = io.BytesIO()
        first = Image.new("RGB", (10, 10), "red")
        second = Image.new("RGB", (10, 10), "blue")
        first.save(animation, "GIF", save_all=True, append_images=[second])
        with self.assertRaises(CoverDecodeRejected) as raised:
            decode_cover(animation.getvalue())
        self.assertEqual(CoverSourceState.ANIMATED_OR_MULTIFRAME, raised.exception.state)

    def test_decoder_child_denies_network_authority(self) -> None:
        probe = (
            "from mechanical_librarian.bootstrap.cover_child import _no_network\n"
            "import socket\n"
            "_no_network()\n"
            "try: socket.create_connection(('8.8.8.8', 53))\n"
            "except OSError: raise SystemExit(0)\n"
            "raise SystemExit(1)\n"
        )
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True)
        self.assertEqual(0, result.returncode)
