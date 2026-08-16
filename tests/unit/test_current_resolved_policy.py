"""Direct unit cover for the relocated current-policy resolution.

`tests/unit/test_metadata_policy_activation.py` continues to reach this through
the composition root's re-export; these cases address the application function
itself and pin the three configuration failures it distinguishes.
"""

from __future__ import annotations

import json

import pytest

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_policy import current_resolved_policy
from mechanical_librarian.application.review_cases import ReviewCaseError
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)


def _configuration(
    override: dict[str, dict[str, str]] | None = None,
) -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "https://example.invalid", "/run/token"),),
        (
            LibraryConfiguration(
                "books", "primary", "library-1", "ebook", None, ("catalogue",),
                policy_override=override,
            ),
        ),
        (),
        (
            ProviderConnectionConfiguration(
                "catalogue", True, False, None, "open_library",
                "owner@example.invalid",
            ),
        ),
    )


class _Repository:
    def __init__(self, persisted: ResolvedMetadataPolicy | None, bound: str | None = None) -> None:
        self._persisted = persisted
        self._bound = bound
        self.bindings: list[str] = []

    def current_resolved_metadata_policy(self, library: str) -> ResolvedMetadataPolicy | None:
        del library
        return self._persisted

    def active_metadata_policy(self) -> str:
        return json.dumps(MetadataPolicyProfile.builtin().to_data())

    def bind_initial_metadata_policy_resolution(self, document: str) -> str:
        self.bindings.append(document)
        if self._bound is not None:
            return self._bound
        return ResolvedMetadataPolicy.from_data(json.loads(document)).resolved_id


def _configured(override: dict[str, dict[str, str]] | None = None) -> ResolvedMetadataPolicy:
    return resolve_policy(MetadataPolicyProfile.builtin(), "books", override)


def test_absent_resolution_is_bound_from_configuration_once() -> None:
    repository = _Repository(None)
    resolved = current_resolved_policy(_configuration(), repository, "books")
    assert _configured() == resolved
    assert [_configured().canonical_json] == repository.bindings


def test_matching_persisted_resolution_is_returned_without_binding() -> None:
    repository = _Repository(_configured())
    resolved = current_resolved_policy(_configuration(), repository, "books")
    assert _configured() == resolved
    assert [] == repository.bindings


def test_drifted_persisted_resolution_is_a_configuration_failure() -> None:
    override = {"options": {}, "destinations": {"series": "ignore"}}
    repository = _Repository(_configured(override))
    with pytest.raises(ReviewCaseError) as raised:
        current_resolved_policy(_configuration(), repository, "books")
    assert "configuration" == str(raised.value)
    assert [] == repository.bindings


def test_unconfigured_library_is_a_configuration_failure() -> None:
    repository = _Repository(None)
    with pytest.raises(ReviewCaseError):
        current_resolved_policy(_configuration(), repository, "absent")
    assert [] == repository.bindings


def test_bind_returning_another_identity_is_a_configuration_failure() -> None:
    repository = _Repository(None, bound="resolved_policy_" + "0" * 64)
    with pytest.raises(ReviewCaseError):
        current_resolved_policy(_configuration(), repository, "books")
    assert 1 == len(repository.bindings)
