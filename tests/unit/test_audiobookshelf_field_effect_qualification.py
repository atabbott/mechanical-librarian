from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from scripts.qualify_audiobookshelf_field_effect import _epub, _operator_epub


class AudiobookshelfFieldEffectQualificationTests(unittest.TestCase):
    def test_operator_overlay_preserves_content_and_binds_valid_isbn_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.epub"
            destination = root / "operator.epub"
            _epub(source)
            isbn, title = _operator_epub(source, destination, 3)

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
            self.assertEqual(
                ["Local Qualification Author"],
                [value.text for value in metadata.findall(dc + "creator")],
            )


if __name__ == "__main__":
    unittest.main()
