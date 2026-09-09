"""Aggregate-safe qualification harness for deterministic cover authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from mechanical_librarian.domain.deterministic_cover_election import (
    CoverActualTargetState,
    CoverElectionCandidate,
    elect_confirmed_covers,
)

COHORTS = ("development", "holdout_a", "holdout_b")
MINIMUM_PACKETS = {"development": 30, "holdout_a": 10, "holdout_b": 10}
REQUIREMENTS = {
    "duplicate_collapse_failures": 0,
    "false_admissions": 0,
    "readiness_disagreements": 0,
    "recommendation_disagreements": 0,
    "rank_order_disagreements": 0,
}
MANIFEST = {
    "cohorts": list(COHORTS),
    "expectations": [
        "explicit_actual_target_state",
        "incumbent_excluded_from_replacement_order",
        "independent_identity",
        "independent_order",
        "independent_owner_readiness",
        "independent_recommendation",
    ],
    "minimum_packets": MINIMUM_PACKETS,
    "requirements": REQUIREMENTS,
    "schema_version": "cover-election-qualification-v2",
}
MANIFEST_DOCUMENT = json.dumps(
    MANIFEST, ensure_ascii=False, separators=(",", ":"), sort_keys=True
) + "\n"
QUALIFICATION_CANDIDATE_SHA256 = hashlib.sha256(
    MANIFEST_DOCUMENT.encode("utf-8")
).hexdigest()


@dataclass(frozen=True, slots=True)
class CoverQualificationCase:
    """One private case reduced to the facts the public harness needs."""

    candidates: tuple[CoverElectionCandidate, ...]
    expected_identity: tuple[tuple[str, str | None], ...]
    expected_order: tuple[str, ...]
    expected_recommendation: str | None
    provider_fetching: bool
    expected_ready: bool
    duplicates_collapsed: bool
    actual_target_state: CoverActualTargetState = CoverActualTargetState.KNOWN_EMPTY


def qualify_cover_election(
    cohort: str, cases: tuple[CoverQualificationCase, ...]
) -> dict[str, object]:
    """Replay a frozen cohort and return aggregate counts without identities."""
    if cohort not in COHORTS or type(cases) is not tuple:
        raise ValueError("cover qualification input is invalid")
    totals = {
        "duplicate_collapse_failures": 0,
        "false_admissions": 0,
        "readiness_disagreements": 0,
        "recommendation_disagreements": 0,
        "rank_order_disagreements": 0,
    }
    for case in cases:
        if type(case) is not CoverQualificationCase:
            raise ValueError("cover qualification input is invalid")
        expected_identity = dict(case.expected_identity)
        if set(expected_identity) != {candidate.reference for candidate in case.candidates}:
            raise ValueError("cover qualification bindings are invalid")
        actual = elect_confirmed_covers(
            case.candidates,
            provider_fetching=case.provider_fetching,
            actual_target_state=case.actual_target_state,
        )
        incumbent_references = {
            candidate.reference for candidate in case.candidates if candidate.incumbent
        }
        expected_confirmed = {
            reference
            for reference, identity in case.expected_identity
            if identity is not None and reference not in incumbent_references
        }
        if (
            len(set(case.expected_order)) != len(case.expected_order)
            or set(case.expected_order) != expected_confirmed
            or (
                case.expected_recommendation is not None
                and case.expected_recommendation not in case.expected_order
            )
            or (not case.expected_ready and case.expected_recommendation is not None)
        ):
            raise ValueError("cover qualification expectation is invalid")
        actual_references = tuple(
            item.candidate.reference for item in actual.ranked
        )
        totals["duplicate_collapse_failures"] += not case.duplicates_collapsed
        totals["false_admissions"] += len(set(actual_references) - expected_confirmed)
        totals["readiness_disagreements"] += actual.ready != case.expected_ready
        totals["rank_order_disagreements"] += actual_references != case.expected_order
        totals["recommendation_disagreements"] += (
            None if actual.recommendation is None else actual.recommendation.candidate.reference
        ) != case.expected_recommendation
    passed = len(cases) >= MINIMUM_PACKETS[cohort] and all(
        totals[name] <= limit for name, limit in REQUIREMENTS.items()
    )
    return {
        "candidate_sha256": QUALIFICATION_CANDIDATE_SHA256,
        "cohort": cohort,
        "packet_count": len(cases),
        "passed": passed,
        **totals,
    }
