from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.batch_manifest import create, read
from mechanical_librarian.application.batch import (
 BatchError,
 CatalogueSnapshot,
 manifest,
)


class Tests(unittest.TestCase):
    def test_create_read_and_refuse_overwrite(self):
        raw = manifest(CatalogueSnapshot(1, "a" * 64, ()), "b" * 64, (), (), ())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            create(p, raw)
            self.assertEqual(read(p), raw)
            with self.assertRaises(BatchError):
                create(p, raw)

    def test_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            p.write_text("{}")
            with self.assertRaises(BatchError):
                read(p)

    def test_refuses_symlink_and_non_private_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "target.json"
            target.write_text("{}")
            link = root / "manifest.json"
            link.symlink_to(target)
            with self.assertRaises(BatchError):
                read(link)
