from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from scripts.qualify_audiobookshelf_field_effect import (
    QualificationError,
    _epub,
    _operator_epub,
    _require_installed_full_chain_coverage,
)


class AudiobookshelfFieldEffectQualificationTests(unittest.TestCase):
    def test_missing_installed_destination_fails_full_chain_qualification(self) -> None:
        expected = {"title": "automatic_policy", "cover": "human"}
        with self.assertRaisesRegex(
            QualificationError, "installed_scheduler_failed"
        ) as captured:
            _require_installed_full_chain_coverage(
                {"title": "automatic_policy"},
                expected,
                activity_present=True,
                diagnostics={"thread_alive": False},
            )
        self.assertIn('"missing": {"cover": "human"}', str(captured.exception))

        _require_installed_full_chain_coverage(
            expected,
            expected,
            activity_present=True,
            diagnostics={},
        )

    def test_operator_overlay_preserves_content_and_binds_valid_isbn_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.epub"
            destination = root / "operator.epub"
            _epub(source)
            isbn, title, author = _operator_epub(source, destination, 3)

            self.assertEqual(13, len(isbn))
            self.assertTrue(isbn.isdigit())
            self.assertEqual(
                0,
                sum(
                    int(value) * (1 if position % 2 == 0 else 3)
                    for position, value in enumerate(isbn)
                ) % 10,
            )
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination) as overlaid:
                self.assertEqual(
                    original.read("OEBPS/chapter.xhtml"),
                    overlaid.read("OEBPS/chapter.xhtml"),
                )
                package = ET.fromstring(overlaid.read("OEBPS/content.opf"))
                metadata = package.find("{http://www.idpf.org/2007/opf}metadata")
                assert metadata is not None
                dc = "{http://purl.org/dc/elements/1.1/}"
                self.assertEqual(title, metadata.findtext(dc + "title"))
                self.assertEqual(isbn, metadata.findtext(dc + "identifier"))
            self.assertEqual("Synthetic Author.", author)
            self.assertEqual(1, len(metadata.findall(dc + "creator")))


if __name__ == "__main__":
    unittest.main()
