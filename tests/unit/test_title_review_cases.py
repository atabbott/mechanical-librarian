from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError

from mechanical_librarian.application.title_review_cases import (
    ReviewAdmission,
    ReviewTransitionKind,
)
from mechanical_librarian.domain.title_proposals import (
    TitleProposalResult,
    proposal_set_from_manifest,
)
from mechanical_librarian.domain.title_review_cases import (
    SYNTHETIC_REVIEW_ADAPTER,
    SYNTHETIC_REVIEW_CONNECTION,
    SYNTHETIC_REVIEW_LIBRARY,
    SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
    SyntheticReviewTarget,
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


def qualifying_case_revision(
    revision: int,
    *,
    previous_set_id: str | None,
    lineage: str = "synthetic-title-preview-agree-empty",
    external_item: str = "synthetic-item",
) -> SyntheticTitleReviewCase:
    source_manifest = manifest()
    source_manifest["lineage"] = lineage
    source_manifest["lineage_revision"] = revision
    source_manifest["previous_set_id"] = previous_set_id
    source_manifest["snapshot"]["id"] = f"synthetic-snapshot-{revision}"  # type: ignore[index]
    proposal_set = proposal_set_from_manifest(
        manifest=source_manifest,
        result=TitleProposalResult.NEEDS_REVIEW,
        proposed_value="A Synthetic Book",
        previous_set_id=previous_set_id,
    )
    result = review_case_from_proposal_set(proposal_set)
    assert result is not None
    if external_item != result.target.external_item:
        # Model a corrupted typed readback; canonical sources close this value.
        object.__setattr__(
            result,
            "target",
            SyntheticReviewTarget(
                SYNTHETIC_REVIEW_ADAPTER,
                SYNTHETIC_REVIEW_CONNECTION,
                SYNTHETIC_REVIEW_LIBRARY,
                external_item,
            ),
        )
    return result


def admission_values(
    kind: ReviewTransitionKind,
    *,
    source_result: TitleProposalResult,
    previous_case: SyntheticTitleReviewCase | None,
    current_case: SyntheticTitleReviewCase | None,
    replayed: bool = False,
    case_history_count: int,
    transition_count: int,
) -> dict[str, object]:
    reasons = {
        ReviewTransitionKind.CREATED: "admit current synthetic review case",
        ReviewTransitionKind.SUPERSEDED: "supersede current synthetic review case",
        ReviewTransitionKind.RETIRED: "retire current synthetic review case",
        ReviewTransitionKind.NO_CASE: "record current synthetic no-case result",
    }
    return {
        "source_set_id": (
            current_case.source_set_id
            if current_case is not None
            else "ps-current-nonqualifying"
        ),
        "source_result": source_result,
        "current_case": current_case,
        "previous_case": previous_case,
        "current_case_id": None if current_case is None else current_case.case_id,
        "transition_id": f"transition-{kind.value}",
        "transition_kind": kind,
        "transition_reason": reasons[kind],
        "replayed": replayed,
        "case_history_count": case_history_count,
        "transition_count": transition_count,
    }


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


class ReviewAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = qualifying_case()
        self.current = qualifying_case_revision(
            2,
            previous_set_id=self.previous.source_set_id,
        )

    def test_all_valid_transition_kinds_accept_replay_variants(self) -> None:
        fresh_nonqualifying = set(TitleProposalResult) - {
            TitleProposalResult.NEEDS_REVIEW,
            TitleProposalResult.STALE_INPUT,
        }
        qualifying_cases = (
            (
                ReviewTransitionKind.CREATED,
                None,
                self.previous,
                1,
                1,
            ),
            (
                ReviewTransitionKind.SUPERSEDED,
                self.previous,
                self.current,
                2,
                2,
            ),
        )
        for kind, previous, current, case_count, transition_count in qualifying_cases:
            for replayed in (False, True):
                with self.subTest(kind=kind, replayed=replayed):
                    values = admission_values(
                        kind,
                        source_result=TitleProposalResult.NEEDS_REVIEW,
                        previous_case=previous,
                        current_case=current,
                        replayed=replayed,
                        case_history_count=case_count,
                        transition_count=transition_count,
                    )
                    self.assertEqual(ReviewAdmission(**values).replayed, replayed)  # type: ignore[arg-type]

        nonqualifying_cases = (
            (ReviewTransitionKind.RETIRED, self.previous, 1, 2),
            (ReviewTransitionKind.NO_CASE, None, 0, 1),
        )
        for kind, previous, case_count, transition_count in nonqualifying_cases:
            for source_result in fresh_nonqualifying:
                for replayed in (False, True):
                    with self.subTest(
                        kind=kind,
                        source_result=source_result,
                        replayed=replayed,
                    ):
                        values = admission_values(
                            kind,
                            source_result=source_result,
                            previous_case=previous,
                            current_case=None,
                            replayed=replayed,
                            case_history_count=case_count,
                            transition_count=transition_count,
                        )
                        self.assertEqual(ReviewAdmission(**values).replayed, replayed)  # type: ignore[arg-type]

    def test_invalid_lifecycle_relationships_fail_closed(self) -> None:
        created = admission_values(
            ReviewTransitionKind.CREATED,
            source_result=TitleProposalResult.NEEDS_REVIEW,
            previous_case=None,
            current_case=self.previous,
            case_history_count=1,
            transition_count=1,
        )
        superseded = admission_values(
            ReviewTransitionKind.SUPERSEDED,
            source_result=TitleProposalResult.NEEDS_REVIEW,
            previous_case=self.previous,
            current_case=self.current,
            case_history_count=2,
            transition_count=2,
        )
        retired = admission_values(
            ReviewTransitionKind.RETIRED,
            source_result=TitleProposalResult.NO_CHANGE,
            previous_case=self.previous,
            current_case=None,
            case_history_count=1,
            transition_count=2,
        )
        no_case = admission_values(
            ReviewTransitionKind.NO_CASE,
            source_result=TitleProposalResult.NO_CHANGE,
            previous_case=None,
            current_case=None,
            case_history_count=0,
            transition_count=1,
        )
        wrong_lineage = qualifying_case_revision(
            2,
            previous_set_id=self.previous.source_set_id,
            lineage="synthetic-title-preview-other",
        )
        wrong_target = qualifying_case_revision(
            2,
            previous_set_id=self.previous.source_set_id,
            external_item="synthetic-item-other",
        )
        wrong_predecessor = qualifying_case_revision(
            2,
            previous_set_id="ps-not-the-predecessor",
        )
        skipped_revision = qualifying_case_revision(
            3,
            previous_set_id=self.previous.source_set_id,
        )
        invalid = (
            ("missing replacement", {**superseded, "current_case": None, "current_case_id": None}),
            (
                "self supersession",
                {
                    **superseded,
                    "source_set_id": self.previous.source_set_id,
                    "current_case": self.previous,
                    "current_case_id": self.previous.case_id,
                },
            ),
            (
                "cross lineage",
                {
                    **superseded,
                    "source_set_id": wrong_lineage.source_set_id,
                    "current_case": wrong_lineage,
                    "current_case_id": wrong_lineage.case_id,
                },
            ),
            (
                "cross target",
                {
                    **superseded,
                    "source_set_id": wrong_target.source_set_id,
                    "current_case": wrong_target,
                    "current_case_id": wrong_target.case_id,
                },
            ),
            (
                "broken predecessor",
                {
                    **superseded,
                    "source_set_id": wrong_predecessor.source_set_id,
                    "current_case": wrong_predecessor,
                    "current_case_id": wrong_predecessor.case_id,
                },
            ),
            (
                "skipped revision",
                {
                    **superseded,
                    "source_set_id": skipped_revision.source_set_id,
                    "current_case": skipped_revision,
                    "current_case_id": skipped_revision.case_id,
                },
            ),
            ("mismatched current source", {**created, "source_set_id": "ps-other"}),
            (
                "mismatched superseded source",
                {**superseded, "source_set_id": "ps-other"},
            ),
            (
                "mismatched current pointer",
                {**created, "current_case_id": "case-other"},
            ),
            (
                "stale retirement",
                {**retired, "source_result": TitleProposalResult.STALE_INPUT},
            ),
            (
                "stale no case",
                {**no_case, "source_result": TitleProposalResult.STALE_INPUT},
            ),
            (
                "needs review retirement",
                {**retired, "source_result": TitleProposalResult.NEEDS_REVIEW},
            ),
            (
                "needs review no case",
                {**no_case, "source_result": TitleProposalResult.NEEDS_REVIEW},
            ),
        )
        for name, values in invalid:
            for replayed in (False, True):
                with self.subTest(name=name, replayed=replayed):
                    with self.assertRaises(ValueError):
                        ReviewAdmission(**{**values, "replayed": replayed})  # type: ignore[arg-type]

    def test_each_transition_rejects_invalid_count_siblings(self) -> None:
        valid = (
            admission_values(
                ReviewTransitionKind.CREATED,
                source_result=TitleProposalResult.NEEDS_REVIEW,
                previous_case=None,
                current_case=self.previous,
                case_history_count=1,
                transition_count=1,
            ),
            admission_values(
                ReviewTransitionKind.SUPERSEDED,
                source_result=TitleProposalResult.NEEDS_REVIEW,
                previous_case=self.previous,
                current_case=self.current,
                case_history_count=2,
                transition_count=2,
            ),
            admission_values(
                ReviewTransitionKind.RETIRED,
                source_result=TitleProposalResult.NO_CHANGE,
                previous_case=self.previous,
                current_case=None,
                case_history_count=1,
                transition_count=2,
            ),
            admission_values(
                ReviewTransitionKind.NO_CASE,
                source_result=TitleProposalResult.NO_CHANGE,
                previous_case=None,
                current_case=None,
                case_history_count=0,
                transition_count=1,
            ),
        )
        for values in valid:
            kind = values["transition_kind"]
            for field in ("case_history_count", "transition_count"):
                for offset in (-1, 1):
                    changed = int(values[field]) + offset
                    if field == "transition_count" and changed < 1:
                        continue
                    if field == "case_history_count" and changed < 0:
                        continue
                    with self.subTest(kind=kind, field=field, changed=changed):
                        with self.assertRaises(ValueError):
                            ReviewAdmission(**{**values, field: changed})  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
