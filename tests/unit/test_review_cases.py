import json
import unittest
from pathlib import Path
from unittest.mock import patch

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


class ReviewCaseApplicationTests(unittest.TestCase):
    def test_external_currentness_drift_is_typed_stale_without_admission(self) -> None:
        repository = _Repository()

        def drift(_):
            raise CurrentnessError("changed")

        result = json.loads(
            AdmitReviewCase(repository, drift).run(
                repository.proposal.proposal_set_id
            )
        )
        self.assertEqual(
            {
                "review_case_id": None,
                "schema_version": "review-admission-v1",
                "status": "stale",
            },
            result,
        )
        self.assertEqual(0, repository.admissions)

    def test_transaction_currentness_race_is_typed_stale(self) -> None:
        repository = _Repository(ReviewAdmissionStaleError())
        result = json.loads(
            AdmitReviewCase(repository, lambda _: True).run(
                repository.proposal.proposal_set_id
            )
        )
        self.assertEqual("stale", result["status"])

    def test_indeterminate_preflight_failure_is_typed_currentness(self) -> None:
        repository = _Repository()

        def unavailable(_):
            raise RuntimeError("synthetic service unavailable")

        with self.assertRaises(ReviewCaseError) as raised:
            AdmitReviewCase(repository, unavailable).run(
                repository.proposal.proposal_set_id
            )
        self.assertEqual("currentness", raised.exception.code)
        self.assertEqual(0, repository.admissions)

    def test_preflight_persistence_failure_retains_its_typed_boundary(self) -> None:
        repository = _Repository()

        def unavailable(_):
            raise ReviewCaseError("persistence")

        with self.assertRaises(ReviewCaseError) as raised:
            AdmitReviewCase(repository, unavailable).run(
                repository.proposal.proposal_set_id
            )
        self.assertEqual("persistence", raised.exception.code)
        self.assertEqual(0, repository.admissions)

    def test_composed_preflight_repository_read_is_typed_persistence(self) -> None:
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
            self.assertRaises(ReviewCaseError) as raised,
        ):
            admit_review_case(
                Path("/synthetic/config"),
                Path("/synthetic/database"),
                Path("/synthetic/artifacts"),
                repository.proposal.proposal_set_id,
            )
        self.assertEqual("persistence", raised.exception.code)
        self.assertEqual(0, repository.admissions)

    def test_repository_failure_remains_typed_persistence(self) -> None:
        repository = _Repository(RuntimeError("synthetic persistence failure"))
        with self.assertRaises(ReviewCaseError) as raised:
            AdmitReviewCase(repository, lambda _: True).run(
                repository.proposal.proposal_set_id
            )
        self.assertEqual("persistence", raised.exception.code)
        self.assertEqual(1, repository.admissions)

    def test_incompatible_evaluator_output_retains_typed_failure(self) -> None:
        repository = _Repository(ReviewAdmissionIncompatibleError())
        with self.assertRaises(ReviewCaseError) as raised:
            AdmitReviewCase(repository, lambda _: True).run(
                repository.proposal.proposal_set_id
            )
        self.assertEqual("incompatible", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
