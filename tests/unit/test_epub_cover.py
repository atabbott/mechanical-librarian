import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from mechanical_librarian.adapters.evidence_extractors.epub_cover import read_declared_cover
from mechanical_librarian.domain.cover_evidence import CoverSourceState


class DeclaredEpubCoverTests(unittest.TestCase):
    def test_reads_only_declared_cover_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
                archive.writestr("OPS/book.opf", '<package><manifest><item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/></manifest></package>')
                archive.writestr("OPS/cover.png", b"declared")
                archive.writestr("OPS/other.png", b"not selected")
            descriptor = os.open(path, os.O_RDONLY)
            try:
                result = read_declared_cover(descriptor)
            finally:
                os.close(descriptor)
            self.assertEqual(CoverSourceState.DECLARED, result.state)
            self.assertEqual(b"declared", result.data)

    def test_absent_cover_is_not_heuristically_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
                archive.writestr("book.opf", '<package><manifest><item id="image" href="cover.png" media-type="image/png"/></manifest></package>')
                archive.writestr("cover.png", b"not declared")
            descriptor = os.open(path, os.O_RDONLY)
            try:
                result = read_declared_cover(descriptor)
            finally:
                os.close(descriptor)
            self.assertEqual(CoverSourceState.ABSENT, result.state)
