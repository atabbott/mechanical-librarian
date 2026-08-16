from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError


def test_expected_database_deleted_never_recreates():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        identity = repository.identity
        repository.close()
        path.unlink()
        with pytest.raises(MetadataReportError):
            open_repository(path, expected_identity=identity)
        assert not path.exists()
