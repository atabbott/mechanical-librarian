from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError

from mechanical_librarian.domain.title_proposals import (
    TitleProposalResult,
    proposal_set_from_manifest,
)
from mechanical_librarian.domain.title_review_cases import (
    SYNTHETIC_REVIEW_ADAPTER,
    SYNTHETIC_REVIEW_CONNECTION,
    SYNTHETIC_REVIEW_LIBRARY,
    SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
    SyntheticTitleReviewCase,
    TitleReviewCaseValueError,
    review_case_from_proposal_set,
)
from tests.unit.test_title_proposals import manifest


def qualifying_case() -> SyntheticTitleReviewCase:
    proposal_set = proposal_set_from_manifest(
        manifest=manifest(),
        result=TitleProposalResult.NEEDS_REVIEW,
        proposed_value="A Synthetic Book",
    )
    result = review_case_from_proposal_set(proposal_set)
    assert result is not None
    return result


class SyntheticTitleReviewCaseTests(unittest.TestCase):
    def test_complete_binding_is_canonical_immutable_and_report_only(self) -> None:
        value = qualifying_case()
        self.assertEqual(SyntheticTitleReviewCase.from_json(value.to_json()), value)
        self.assertEqual(value.binding_version, SYNTHETIC_TITLE_REVIEW_BINDING_VERSION)
        self.assertEqual(value.target.adapter_id, SYNTHETIC_REVIEW_ADAPTER)
        self.assertEqual(value.target.connection_id, SYNTHETIC_REVIEW_CONNECTION)
        self.assertEqual(value.target.library_id, SYNTHETIC_REVIEW_LIBRARY)
        self.assertFalse(value.production_applicable)
        self.assertEqual(value.state, "undecided")
        self.assertNotIn("production_applicable", value.__dict__)
        self.assertFalse(hasattr(value, "decision"))
        self.assertFalse(hasattr(value, "evidence_reference"))
        with self.assertRaises(FrozenInstanceError):
            value.case_id = "case-changed"  # type: ignore[misc]

    def test_only_needs_review_with_one_current_title_proposal_creates_a_case(self) -> None:
        for result in TitleProposalResult:
            if result is TitleProposalResult.NEEDS_REVIEW:
                continue
            with self.subTest(result=result):
                proposal_set = proposal_set_from_manifest(
                    manifest=manifest(), result=result
                )
                self.assertIsNone(review_case_from_proposal_set(proposal_set))

    def test_stale_snapshot_is_rejected_before_any_result_projection(self) -> None:
        for result in (TitleProposalResult.NEEDS_REVIEW, TitleProposalResult.STALE_INPUT):
            with self.subTest(result=result):
                source = manifest()
                source["snapshot"]["current_fingerprint"] = "0" * 64  # type: ignore[index]
                proposal_set = proposal_set_from_manifest(
                    manifest=source,
                    result=result,
                    proposed_value=(
                        "A Synthetic Book"
                        if result is TitleProposalResult.NEEDS_REVIEW
                        else None
                    ),
                )
                with self.assertRaisesRegex(TitleReviewCaseValueError, "stale"):
                    review_case_from_proposal_set(proposal_set)

    def test_case_id_binds_every_complete_projection_family(self) -> None:
        baseline = qualifying_case()
        mutations = (
            ("snapshot", lambda value: value["snapshot"].__setitem__("id", "synthetic-snapshot-other")),
            ("fingerprint", lambda value: (value["snapshot"].__setitem__("fingerprint", "e" * 64), value["snapshot"].__setitem__("current_fingerprint", "e" * 64))),
            ("provenance", lambda value: value.__setitem__("opaque_file_identity", "f" * 64)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                changed = manifest()
                mutate(changed)
                source = proposal_set_from_manifest(
                    manifest=changed,
                    result=TitleProposalResult.NEEDS_REVIEW,
                    proposed_value="A Synthetic Book",
                )
                candidate = review_case_from_proposal_set(source)
                self.assertIsNotNone(candidate)
                self.assertNotEqual(candidate.case_id, baseline.case_id)  # type: ignore[union-attr]

    def test_hydration_rejects_authority_unknown_fields_and_tampering(self) -> None:
        value = qualifying_case()
        mutations = (
            lambda data: data.__setitem__("production_applicable", True),
            lambda data: data.__setitem__("mode", "production"),
            lambda data: data["proposal"].__setitem__("field", "cover"),
            lambda data: data["proposal"].__setitem__("disposition", "auto_apply"),
            lambda data: data["target"].__setitem__("adapter_id", "audiobookshelf"),
            lambda data: data.__setitem__("reviewer_output", {}),
            lambda data: data.__setitem__("case_id", "case-arbitrary"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = copy.deepcopy(json.loads(value.to_json()))
                mutate(data)
                with self.assertRaises(TitleReviewCaseValueError):
                    SyntheticTitleReviewCase.from_json(
                        json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                    )

    def test_duplicate_json_fields_fail_closed(self) -> None:
        value = qualifying_case().to_json()
        duplicate = value.replace(
            '"production_applicable":false,',
            '"production_applicable":false,"production_applicable":false,',
            1,
        )
        with self.assertRaisesRegex(TitleReviewCaseValueError, "duplicate"):
            SyntheticTitleReviewCase.from_json(duplicate)


if __name__ == "__main__":
    unittest.main()
