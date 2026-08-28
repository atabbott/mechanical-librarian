import hashlib
from pathlib import Path

from mechanical_librarian.domain.cover_qualification import (
    QUALIFICATION_CANDIDATE_SHA256,
    CoverQualificationCase,
    qualify_cover_election,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    CoverElectionCandidate,
)


def _candidate(reference: str, identity: str, ordinal: int) -> CoverElectionCandidate:
    return CoverElectionCandidate(
        reference=reference,
        identity=identity,
        identity_citations=("identity_" + reference,),
        source_citations=("source_" + reference,),
        incumbent=False,
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
        / "cover-election-qualification-v1.json"
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
