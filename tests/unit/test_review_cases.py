import json
import unittest

from mechanical_librarian.application.metadata_report import CurrentnessError
from mechanical_librarian.application.review_cases import (
    AdmitReviewCase,
    ReviewCaseError,
)
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

    def test_incompatible_evaluator_output_retains_typed_failure(self) -> None:
        repository = _Repository(ReviewAdmissionIncompatibleError())
        with self.assertRaises(ReviewCaseError) as raised:
            AdmitReviewCase(repository, lambda _: True).run(
                repository.proposal.proposal_set_id
            )
        self.assertEqual("incompatible", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
