import os
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from mechanical_librarian.adapters.evidence_extractors.epub_cover import read_declared_cover
from mechanical_librarian.domain.cover_evidence import CoverSourceState
from tests.support.epub_packages import EPUB3_PACKAGE, epub_bytes


class DeclaredEpubCoverTests(unittest.TestCase):
    def test_reads_only_declared_cover_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            path.write_bytes(epub_bytes())
            descriptor = os.open(path, os.O_RDONLY)
            try:
                result = read_declared_cover(descriptor, hashlib.sha256(path.read_bytes()).hexdigest())
            finally:
                os.close(descriptor)
            self.assertEqual(CoverSourceState.DECLARED, result.state)
            self.assertEqual(b"TRAP: cover bytes must remain opaque", result.data)

    def test_absent_cover_is_not_heuristically_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            package = EPUB3_PACKAGE.replace(b' properties="cover-image"', b"")
            path.write_bytes(epub_bytes(package=package))
            descriptor = os.open(path, os.O_RDONLY)
            try:
                result = read_declared_cover(descriptor, hashlib.sha256(path.read_bytes()).hexdigest())
            finally:
                os.close(descriptor)
            self.assertEqual(CoverSourceState.ABSENT, result.state)

    def test_exact_eight_mebibyte_cover_uses_bounded_binary_pipe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            cover = b"x" * (8 * 1024 * 1024)
            path.write_bytes(epub_bytes(include_default_content=False, extra_entries=(
                ("EPUB/nav.xhtml", b"nav", zipfile.ZIP_DEFLATED),
                ("EPUB/chapter.xhtml", b"chapter", zipfile.ZIP_DEFLATED),
                ("EPUB/cover.jpg", cover, zipfile.ZIP_STORED),
            )))
            descriptor = os.open(path, os.O_RDONLY)
            try:
                result = read_declared_cover(descriptor, hashlib.sha256(path.read_bytes()).hexdigest())
            finally:
                os.close(descriptor)
            self.assertEqual(CoverSourceState.DECLARED, result.state)
            self.assertEqual(cover, result.data)
