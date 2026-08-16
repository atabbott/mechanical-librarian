import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.application.metadata_report import CurrentnessError
from mechanical_librarian.application.review_cases import (
    AdmitReviewCase,
    ReviewCaseError,
)
from mechanical_librarian.bootstrap.composition import admit_review_case
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewAdmissionStaleError,
)


def _proposal():
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    return evaluate_report(report)


class _Repository:
    def __init__(self, error: Exception | None = None) -> None:
        self.proposal = _proposal()
        self.error = error
        self.admissions = 0

    def get_proposal(self, proposal_set_id: str) -> str:
        return self.proposal.to_json()

    def admit_review_case(self, proposal_set) -> str:
        self.admissions += 1
        if self.error is not None:
            raise self.error
        return (
            '{"review_case_id":null,"schema_version":"review-admission-v1",'
            '"status":"no_case"}'
        )

    def get_review_case(self, review_case_id: str) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


def test_external_currentness_drift_is_typed_stale_without_admission() -> None:
    repository = _Repository()

    def drift(_):
        raise CurrentnessError("changed")

    result = json.loads(
        AdmitReviewCase(repository, drift).run(
            repository.proposal.proposal_set_id
        )
    )
    assert {
            "review_case_id": None,
            "schema_version": "review-admission-v1",
            "status": "stale",
        } == result
    assert 0 == repository.admissions


def test_transaction_currentness_race_is_typed_stale() -> None:
    repository = _Repository(ReviewAdmissionStaleError())
    result = json.loads(
        AdmitReviewCase(repository, lambda _: True).run(
            repository.proposal.proposal_set_id
        )
    )
    assert "stale" == result["status"]


def test_indeterminate_preflight_failure_is_typed_currentness() -> None:
    repository = _Repository()

    def unavailable(_):
        raise RuntimeError("synthetic service unavailable")

    with pytest.raises(ReviewCaseError) as raised:
        AdmitReviewCase(repository, unavailable).run(
            repository.proposal.proposal_set_id
        )
    assert "currentness" == raised.value.code
    assert 0 == repository.admissions


def test_preflight_persistence_failure_retains_its_typed_boundary() -> None:
    repository = _Repository()

    def unavailable(_):
        raise ReviewCaseError("persistence")

    with pytest.raises(ReviewCaseError) as raised:
        AdmitReviewCase(repository, unavailable).run(
            repository.proposal.proposal_set_id
        )
    assert "persistence" == raised.value.code
    assert 0 == repository.admissions


def test_composed_preflight_repository_read_is_typed_persistence() -> None:
    class UnavailableRepository(_Repository):
        def get(self, report_id: str) -> str:
            raise RuntimeError("synthetic persistence failure")

    repository = UnavailableRepository()
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=object(),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.CoverArtifactStore",
            return_value=object(),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ),
        pytest.raises(ReviewCaseError) as raised,
    ):
        admit_review_case(
            Path("/synthetic/config"),
            Path("/synthetic/database"),
            Path("/synthetic/artifacts"),
            repository.proposal.proposal_set_id,
        )
    assert "persistence" == raised.value.code
    assert 0 == repository.admissions


def test_repository_failure_remains_typed_persistence() -> None:
    repository = _Repository(RuntimeError("synthetic persistence failure"))
    with pytest.raises(ReviewCaseError) as raised:
        AdmitReviewCase(repository, lambda _: True).run(
            repository.proposal.proposal_set_id
        )
    assert "persistence" == raised.value.code
    assert 1 == repository.admissions


def test_incompatible_evaluator_output_retains_typed_failure() -> None:
    repository = _Repository(ReviewAdmissionIncompatibleError())
    with pytest.raises(ReviewCaseError) as raised:
        AdmitReviewCase(repository, lambda _: True).run(
            repository.proposal.proposal_set_id
        )
    assert "incompatible" == raised.value.code
