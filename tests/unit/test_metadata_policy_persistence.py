from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
)


def test_qualified_automatic_canonical_draft_reopens_and_unqualified_intent_is_rejected() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.sqlite"
        repository = open_repository(path)
        try:
            destinations = {
                category: "unsupported" if category == "explicit" else "ask"
                for category in OPTION_CATALOGUE
            }
            destinations["title_layout"] = "automatic"
            profile = MetadataPolicyProfile.create(
                "Qualified automatic draft", dict(DEFAULT_OPTIONS), destinations
            )
            assert "automatic" == repository.shadow_posture()
            policy_id = repository.create_metadata_policy_draft(profile.canonical_json)
            assert profile.policy_id == policy_id
            assert profile.canonical_json == repository.get_metadata_policy_draft(
                policy_id
            )
            with pytest.raises(MetadataReportError):
                repository.create_metadata_policy_draft(profile.canonical_json + " ")
            unqualified_options = dict(DEFAULT_OPTIONS)
            unqualified_options["publisher_preference"] = "imprint"
            unqualified = MetadataPolicyProfile.create(
                "Unqualified automatic draft",
                unqualified_options,
                {**destinations, "publisher_preference": "automatic"},
            )
            with pytest.raises(
                MetadataReportError, match="automatic intent is unqualified"
            ):
                repository.create_metadata_policy_draft(unqualified.canonical_json)
        finally:
            repository.close()
        reopened = open_repository(path, create=False)
        try:
            assert profile.canonical_json == reopened.get_metadata_policy_draft(
                profile.policy_id
            )
        finally:
            reopened.close()
