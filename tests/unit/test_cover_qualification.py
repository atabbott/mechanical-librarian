import copy
import hashlib
import json
from pathlib import Path

import pytest

from mechanical_librarian.domain.audiobook_cover_replacement import (
    material_audiobook_cover_reference,
)
from mechanical_librarian.domain.cover_qualification import (
    QUALIFICATION_CANDIDATE_SHA256,
    CoverQualificationCase,
    qualify_cover_election,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    CoverActualTargetState,
    CoverElectionCandidate,
)
from tests.support.audiobook_covers import audiobook_cover_environment


def _candidate(
    reference: str, identity: str, ordinal: int, *, incumbent: bool = False
) -> CoverElectionCandidate:
    return CoverElectionCandidate(
        reference=reference,
        identity=identity,
        identity_citations=("identity_" + reference,),
        source_citations=("source_" + reference,),
        incumbent=incumbent,
        width=100 + ordinal,
        height=150 + ordinal,
        packet_ordinal=ordinal,
    )


def test_qualification_returns_only_aggregate_safe_counts() -> None:
    first = _candidate("candidate_a", "verified_work_match", 0)
    second = _candidate("candidate_b", "verified_manifestation_match", 1)
    case = CoverQualificationCase(
        candidates=(first, second),
        expected_identity=(
            (first.reference, "verified_work_match"),
            (second.reference, "verified_manifestation_match"),
        ),
        expected_order=(second.reference, first.reference),
        expected_recommendation=second.reference,
        provider_fetching=False,
        expected_ready=True,
        duplicates_collapsed=True,
    )

    summary = qualify_cover_election("development", (case,) * 30)

    assert summary == {
        "candidate_sha256": QUALIFICATION_CANDIDATE_SHA256,
        "cohort": "development",
        "packet_count": 30,
        "passed": True,
        "duplicate_collapse_failures": 0,
        "false_admissions": 0,
        "readiness_disagreements": 0,
        "recommendation_disagreements": 0,
        "rank_order_disagreements": 0,
    }
    assert "candidate_a" not in str(summary)


def test_candidate_digest_is_the_public_manifest_file_bytes() -> None:
    manifest = (
        Path(__file__).parents[2]
        / "docs"
        / "development"
        / "cover-election-qualification-v2.json"
    ).read_bytes()

    assert hashlib.sha256(manifest).hexdigest() == QUALIFICATION_CANDIDATE_SHA256


def test_qualification_fails_closed_on_false_admission_and_active_fetching() -> None:
    candidate = _candidate("candidate_a", "verified_work_match", 0)
    case = CoverQualificationCase(
        candidates=(candidate,),
        expected_identity=((candidate.reference, None),),
        expected_order=(),
        expected_recommendation=None,
        provider_fetching=True,
        expected_ready=False,
        duplicates_collapsed=True,
    )

    summary = qualify_cover_election("holdout_a", (case,) * 10)

    assert summary["false_admissions"] == 10
    assert summary["recommendation_disagreements"] == 0
    assert summary["passed"] is False


def test_qualification_compares_rank_to_an_independent_expected_order() -> None:
    first = _candidate("candidate_a", "verified_work_match", 0)
    second = _candidate("candidate_b", "verified_manifestation_match", 1)
    case = CoverQualificationCase(
        candidates=(first, second),
        expected_identity=(
            (first.reference, "verified_work_match"),
            (second.reference, "verified_manifestation_match"),
        ),
        expected_order=(first.reference, second.reference),
        expected_recommendation=first.reference,
        provider_fetching=False,
        expected_ready=True,
        duplicates_collapsed=True,
    )

    summary = qualify_cover_election("holdout_b", (case,) * 10)

    assert summary["rank_order_disagreements"] == 10
    assert summary["recommendation_disagreements"] == 10
    assert summary["passed"] is False


def test_prospective_target_states_separate_owner_readiness_from_automatic_choice() -> (
    None
):
    incumbent = _candidate(
        "incumbent", "verified_manifestation_match", 0, incumbent=True
    )
    downgrade = _candidate("downgrade", "verified_work_match", 1)
    owner_only = CoverQualificationCase(
        candidates=(incumbent, downgrade),
        expected_identity=(
            (incumbent.reference, incumbent.identity),
            (downgrade.reference, downgrade.identity),
        ),
        expected_order=(downgrade.reference,),
        expected_recommendation=None,
        provider_fetching=False,
        expected_ready=True,
        duplicates_collapsed=True,
        actual_target_state=CoverActualTargetState.CONFIRMED,
    )
    unknown = CoverQualificationCase(
        candidates=(downgrade,),
        expected_identity=((downgrade.reference, downgrade.identity),),
        expected_order=(downgrade.reference,),
        expected_recommendation=None,
        provider_fetching=False,
        expected_ready=True,
        duplicates_collapsed=True,
        actual_target_state=CoverActualTargetState.UNKNOWN,
    )
    fetching = CoverQualificationCase(
        candidates=(downgrade,),
        expected_identity=((downgrade.reference, downgrade.identity),),
        expected_order=(downgrade.reference,),
        expected_recommendation=None,
        provider_fetching=True,
        expected_ready=False,
        duplicates_collapsed=True,
        actual_target_state=CoverActualTargetState.KNOWN_EMPTY,
    )

    for case in (owner_only, unknown, fetching):
        summary = qualify_cover_election("development", (case,))
        assert summary["packet_count"] == 1
        assert summary["passed"] is False
        assert summary["false_admissions"] == 0
        assert summary["readiness_disagreements"] == 0
        assert summary["recommendation_disagreements"] == 0
        assert summary["rank_order_disagreements"] == 0


@pytest.mark.parametrize(
    "sizes,expected_rank",
    [
        ([(80, 120), (640, 960)], 1),
        ([(299, 448), (598, 897)], 1),
        ([(300, 450), (1000, 1500)], None),
        ([(250, 375), (499, 748)], None),
        ([(250, 375), (500, 750)], 1),
        ([(299, 448), (597, 895)], None),
        ([(500, 500), (501, 501)], None),
        ([(500, 500), (1000, 1500)], None),
        ([(250, 375), (400, 800), (500, 500)], 2),
    ],
)
def test_actual_consideration_boundaries(tmp_path, sizes, expected_rank):
    with audiobook_cover_environment(tmp_path / "case", sizes) as state:
        assert len(state.decisions) == int(expected_rank is not None)
        if expected_rank is not None:
            decision = state.decisions[0]
            row = next(
                x
                for x in state.consideration["candidates"]
                if x["reference"] == decision.candidate_reference
            )
            assert row["deterministic_rank"] == expected_rank
            assert state.snapshots[0].decision_value == {
                "kind": "cover",
                "value": row["artifact_id"],
            }
            if expected_rank == 2:
                policy = json.loads(state.consideration["policy_report_document"])
                assert (
                    policy["recommended_candidate_reference"]
                    != decision.candidate_reference
                )


@pytest.mark.parametrize(
    "unknown,option",
    [(True, "replace_if_strictly_better"), (False, "fixed_source_order")],
)
def test_unknown_incumbent_and_existing_fill_preserve(tmp_path, unknown, option):
    with audiobook_cover_environment(
        tmp_path / "case",
        [(80, 120), (640, 960)],
        unknown_incumbent=unknown,
        option=option,
    ) as state:
        assert not state.decisions


@pytest.mark.parametrize(
    "change",
    [
        "missing_width",
        "invalid_width",
        "bool_width",
        "unknown_identity",
        "wrong_artifact",
        "missing_incumbent",
        "landscape",
        "edition_only",
        "ordinal_only",
        "reference_only",
    ],
)
def test_exact_predicate_negatives(tmp_path, change):
    with audiobook_cover_environment(
        tmp_path / "predicate", [(80, 120), (640, 960)]
    ) as state:
        rows = copy.deepcopy(state.consideration["candidates"])
    incumbent = next(x for x in rows if x["incumbent"])
    candidate = next(x for x in rows if not x["incumbent"])
    artifact = incumbent["artifact_id"]
    assert material_audiobook_cover_reference(rows, artifact) == candidate["reference"]
    if change == "missing_width":
        incumbent.pop("width")
    elif change == "invalid_width":
        incumbent["width"] = "80"
    elif change == "bool_width":
        incumbent["width"] = True
    elif change == "unknown_identity":
        incumbent["identity"]["outcome"] = "unknown"
    elif change == "wrong_artifact":
        artifact = "cover_wrong"
    elif change == "missing_incumbent":
        rows.remove(incumbent)
    elif change == "landscape":
        candidate.update(width=960, height=640)
    else:
        incumbent.update(width=500, height=500)
        candidate.update(width=500, height=500)
        if change == "edition_only":
            candidate["identity"]["outcome"] = "verified_manifestation_match"
        elif change == "ordinal_only":
            candidate["ordinal"] = -1
        else:
            candidate["reference"] = "cover_ref_000"
    assert material_audiobook_cover_reference(rows, artifact) is None
