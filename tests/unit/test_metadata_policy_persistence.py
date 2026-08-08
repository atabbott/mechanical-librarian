from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import MetadataReportError, open_repository
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile


class MetadataPolicyPersistenceTests(unittest.TestCase):
    def test_immutable_canonical_draft_reopens_without_policy_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            repository = open_repository(path)
            try:
                profile = MetadataPolicyProfile.builtin()
                policy_id = repository.create_metadata_policy_draft(profile.canonical_json)
                self.assertEqual(profile.policy_id, policy_id)
                self.assertEqual(profile.canonical_json, repository.get_metadata_policy_draft(policy_id))
                with self.assertRaises(MetadataReportError):
                    repository.create_metadata_policy_draft(profile.canonical_json + " ")
            finally:
                repository.close()
            reopened = open_repository(path, create=False)
            try:
                self.assertEqual(profile.canonical_json, reopened.get_metadata_policy_draft(profile.policy_id))
            finally:
                reopened.close()
