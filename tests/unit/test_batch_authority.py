from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError


class BatchAuthorityTests(unittest.TestCase):
 def test_expected_database_deleted_never_recreates(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/"reports.sqlite"; repository=open_repository(path); identity=repository.identity; repository.close(); path.unlink()
   with self.assertRaises(MetadataReportError):open_repository(path,expected_identity=identity)
   self.assertFalse(path.exists())
