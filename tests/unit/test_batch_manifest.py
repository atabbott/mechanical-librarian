from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.batch_manifest import create, read
from mechanical_librarian.application.batch import (
    BatchError,
    CatalogueSnapshot,
    manifest,
)


def test_create_read_and_refuse_overwrite():
    raw = manifest(CatalogueSnapshot(1, "a" * 64, ()), "b" * 64, (), (), ())
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "manifest.json"
        create(p, raw)
        assert read(p) == raw
        with pytest.raises(BatchError):
            create(p, raw)


def test_rejects_tamper():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "manifest.json"
        p.write_text("{}")
        with pytest.raises(BatchError):
            read(p)


def test_refuses_symlink_and_non_private_parent():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        target = root / "target.json"
        target.write_text("{}")
        link = root / "manifest.json"
        link.symlink_to(target)
        with pytest.raises(BatchError):
            read(link)
