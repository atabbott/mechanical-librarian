import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.domain.audiobook_decision_qualification import (
    F46_IDENTITIES_BY_DESTINATION,
    F46_PASSING_AUTOMATIC_OPTIONS,
)
from mechanical_librarian.domain.cover_qualification import (
    QUALIFICATION_CANDIDATE_SHA256,
    CoverQualificationCase,
    qualify_cover_election,
)
from mechanical_librarian.domain.cover_qualification_policy import (
    MEASURED_REPLACEMENT_IDENTITY,
    measured_replacement_record,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    CoverActualTargetState,
    CoverElectionCandidate,
)
from tests.support.audiobook_covers import (
    SYNTHETIC_MEASURED_RECORD,
    audiobook_cover_environment,
)


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


def _stage(state: Any) -> dict[str, Any]:
    policy = json.loads(state.consideration["policy_report_document"])
    return cast(dict[str, Any], policy["stage"])


def test_measured_replacement_without_a_record_is_a_suggestion(tmp_path):
    # The incumbent is under the floor and a square copy clears it: the stage
    # replaces, but no shipped record qualifies the switch, so it stays a
    # suggestion and nothing is decided (#1363 D9).
    assert measured_replacement_record() is None
    with audiobook_cover_environment(
        tmp_path / "case", [(200, 200), (600, 600)]
    ) as state:
        assert state.decisions == []
        stage = _stage(state)
        assert (stage["outcome"], stage["reason"]) == ("replace", "below_floor")
        assert state.consideration["candidates"]


def test_a_synthetic_measured_record_admits_the_switch(tmp_path):
    with audiobook_cover_environment(
        tmp_path / "case",
        [(200, 200), (600, 600)],
        measured_record=SYNTHETIC_MEASURED_RECORD,
    ) as state:
        assert len(state.decisions) == 1
        decision = state.decisions[0]
        assert decision.qualification_identity == MEASURED_REPLACEMENT_IDENTITY
        assert decision.policy_option == "fixed_source_order:measured_replacement"
        assert decision.qualification_manifest_sha256 == SYNTHETIC_MEASURED_RECORD
        assert decision.reason == "below_floor"
        stage = _stage(state)
        assert decision.candidate_reference == stage["choice"]
        row = next(
            x
            for x in state.consideration["candidates"]
            if x["reference"] == decision.candidate_reference
        )
        assert state.snapshots[0].decision_value == {
            "kind": "cover",
            "value": row["artifact_id"],
        }


def test_measured_record_never_admits_a_kept_cover(tmp_path):
    # The 700x700 copy of the same picture is under 1.5 times the current
    # cover's area, so it is no sharper copy and the cover is kept.
    with audiobook_cover_environment(
        tmp_path / "case",
        [(600, 600), (700, 700)],
        measured_record=SYNTHETIC_MEASURED_RECORD,
    ) as state:
        assert state.decisions == []
        assert _stage(state)["outcome"] == "keep"
        assert all(
            row["candidate_kind"] != "larger_copy"
            for row in state.consideration["candidates"]
        )


def test_a_larger_copy_of_the_same_picture_is_a_sharper_copy(tmp_path):
    # The packet binds the decoded 1200x1200 copy to the 600x600 current
    # cover, since their previews match; the stage still sees it (#1363).
    with audiobook_cover_environment(
        tmp_path / "case",
        [(600, 600), (1200, 1200)],
        measured_record=SYNTHETIC_MEASURED_RECORD,
    ) as state:
        stage = _stage(state)
        assert (stage["outcome"], stage["reason"]) == ("replace", "sharper_copy")
        row = next(
            x
            for x in state.consideration["candidates"]
            if x["reference"] == stage["choice"]
        )
        assert (row["candidate_kind"], row["width"], row["height"]) == (
            "larger_copy",
            1200,
            1200,
        )
        assert [d.reason for d in state.decisions] == ["sharper_copy"]
        assert state.decisions[0].candidate_reference == stage["choice"]


def test_measured_identity_is_absent_from_every_shipped_manifest():
    root = Path(__file__).parents[2]
    shipped = [
        path
        for folder in ("docs/reference", "src")
        for path in (root / folder).rglob("*.json")
    ]
    assert shipped
    for path in shipped:
        assert MEASURED_REPLACEMENT_IDENTITY not in path.read_text(encoding="utf-8")
    assert MEASURED_REPLACEMENT_IDENTITY not in F46_PASSING_AUTOMATIC_OPTIONS
    assert all(
        MEASURED_REPLACEMENT_IDENTITY not in identities
        for identities in F46_IDENTITIES_BY_DESTINATION.values()
    )


def test_retained_qualification_predecessor_bytes_are_frozen():
    root = Path(__file__).parents[1] / "support/retained_qualification_predecessors"
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["source_sha"] == "48781428ac0358ad573f2400821758596585418a"
    for name, digest in provenance["documents"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def test_retained_audiobook_cover_replacement_stays_refused():
    import inspect

    from mechanical_librarian.domain.automatic_decision import AutomaticDecisionError
    from mechanical_librarian.domain.cover_automatic_decision import (
        CoverAutomaticDecision,
    )

    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "support/retained_qualification_predecessors/cover.json"
        ).read_text()
    )
    document = fixture["decision"]
    retained = json.loads(document)
    assert retained["qualification_identity"] == (
        "cover_order:replace_if_strictly_better:audiobook_cover_replace"
    )
    # The retained decision's earlier F46 digest no longer decodes (ADR-0087).
    with pytest.raises(AutomaticDecisionError):
        CoverAutomaticDecision.from_json(document)
    parameters = inspect.signature(CoverAutomaticDecision.create).parameters
    assert "qualification_manifest_sha256" not in parameters
    values: dict[str, Any] = {
        key: tuple(retained[key])
        if key in {"rank_key", "citation_ids"}
        else retained.get(key)
        for key in parameters
    }
    # Its replacement option was removed with the material rule (#1363 Q11),
    # so even a current digest cannot rebind it: the sentinel stays refused.
    with pytest.raises(AutomaticDecisionError):
        CoverAutomaticDecision.create(**values)
    # Rebinding it under the measured identity needs a stage reason and a
    # qualification record, which no shipped build carries.
    with pytest.raises(AutomaticDecisionError):
        CoverAutomaticDecision.create(
            **{
                **values,
                "qualification_identity": MEASURED_REPLACEMENT_IDENTITY,
                "policy_option": "fixed_source_order:measured_replacement",
                "reason": "below_floor",
            }
        )
