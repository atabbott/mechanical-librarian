"""S10's level derivation: the level set, its notes and a case's level offer.

At a wider Book matching level the live ProposalSet and decision set stay
exactly as they are at ``identifier`` and alone decide anything (#1355, S10).
This module derives the second, level set beside them: its lineage, the
would-admit notes ADR-0092 defines for live manual decisions the level would
decide, and the manual fields whose offer the level substitutes. Nothing here
grants authority, and no live set reads anything this module returns.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    canonical_json,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    LevelEvidence,
    ProposalSet,
    ProposalValueError,
    evaluate_report,
    level_evidence,
)
from mechanical_librarian.domain.review_case import (
    LEVEL_COMBINATION_DESTINATIONS,
    WIDER_WORK_IDENTITY_LEVELS,
    ReviewCase,
    level_offer_document,
)
from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
    decide_shadow_packet,
)
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

LEVEL_PROPOSAL_SET_SCHEMA: Final = "level-proposal-set-v1"
MAX_LEVEL_PROPOSAL_SET_BYTES: Final = 2_097_152
# The non-cover destinations a level substitutes (S7; covers are A's).
LEVEL_OFFER_DESTINATIONS: Final = (
    TargetDestination.TITLE.value,
    TargetDestination.SUBTITLE.value,
    TargetDestination.AUTHORS.value,
    TargetDestination.DESCRIPTION.value,
    TargetDestination.GENRES.value,
    TargetDestination.TAGS.value,
    TargetDestination.SERIES.value,
)
# A live outcome that decides on its own; every other disposition but
# ``ignore`` leaves the field to the owner (AC17).
_AUTOMATIC_DISPOSITIONS: Final = frozenset({"candidate", "keep_current"})
_LEVEL_PROPOSAL_SET_ID = re.compile(r"^level_proposal_set_[0-9a-f]{64}$")
_DECISION_SET_ID = re.compile(r"^shadow_decision_set_[0-9a-f]{64}$")
_ALTERNATIVE_ID = re.compile(r"^alternative_[0-9a-f]{64}$")


class LevelProposalSetError(ValueError):
    """A level set, note or offer that is not exactly what derivation gives."""


@dataclass(frozen=True, slots=True)
class LevelDecisionNote:
    """ADR-0092's would-admit note for one live manual decision (S10).

    It names the level's rule and the level alternative its decision set
    selects. The live decision keeps its manual action and abstention reason;
    the note grants nothing and no screen shows it. The three provenance
    members pin the engines that formed it, so a note from an older engine
    fails closed.
    """

    decision_set_id: str
    level_proposal_set_id: str
    field: str
    work_identity_level: str
    rule: str
    alternative_reference: str
    decision_policy_version: str = DECISION_POLICY_VERSION
    decision_policy_sha256: str = DECISION_POLICY_SHA256
    proposal_policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.decision_set_id) is not str
            or _DECISION_SET_ID.fullmatch(self.decision_set_id) is None
            or type(self.level_proposal_set_id) is not str
            or _LEVEL_PROPOSAL_SET_ID.fullmatch(self.level_proposal_set_id) is None
            or self.field not in LEVEL_OFFER_DESTINATIONS
            or self.work_identity_level not in WIDER_WORK_IDENTITY_LEVELS
            or self.rule != f"work_identity:{self.work_identity_level}"
            or type(self.alternative_reference) is not str
            or _ALTERNATIVE_ID.fullmatch(self.alternative_reference) is None
            or self.decision_policy_version != DECISION_POLICY_VERSION
            or self.decision_policy_sha256 != DECISION_POLICY_SHA256
            or self.proposal_policy_version != POLICY_VERSION
        ):
            raise LevelProposalSetError("level decision note is invalid")


@dataclass(frozen=True, slots=True)
class LevelDerivation:
    """One case's level derivation at one wider level, all in memory."""

    level: WorkIdentityLevel
    level_proposal_set_id: str
    document: str
    notes: tuple[LevelDecisionNote, ...]
    offer: str | None


def level_proposal_set_document(
    live: ProposalSet,
    level_set: ProposalSet,
    level: WorkIdentityLevel,
    lineage: list[dict[str, object]],
) -> tuple[str, str]:
    """The stored level set and its content-addressed id (S10, AC11)."""
    if level.value not in WIDER_WORK_IDENTITY_LEVELS:
        raise LevelProposalSetError("level proposal set level is invalid")
    data: dict[str, object] = {
        "level_set": level_set.to_data(),
        "lineage": lineage,
        "policy_version": POLICY_VERSION,
        "proposal_set_id": live.proposal_set_id,
        "report_id": live.report_id,
        "schema_version": LEVEL_PROPOSAL_SET_SCHEMA,
        "work_identity_level": level.value,
    }
    document = canonical_json(data)
    if len(document.encode("utf-8")) > MAX_LEVEL_PROPOSAL_SET_BYTES:
        raise LevelProposalSetError("level proposal set is too large")
    identity = (
        "level_proposal_set_" + hashlib.sha256(document.encode("utf-8")).hexdigest()
    )
    return identity, document


def validate_level_proposal_set(
    identity: str, proposal_set_id: str, report_id: str, level: str, document: str
) -> ProposalSet:
    """Read one stored level set back exactly, or refuse it."""
    try:
        raw = json.loads(document)
        level_set = ProposalSet.from_json(canonical_json(raw["level_set"]))
    except (json.JSONDecodeError, KeyError, TypeError, ProposalValueError) as error:
        raise LevelProposalSetError("level proposal set is invalid") from error
    if (
        type(raw) is not dict
        or set(raw)
        != {
            "level_set",
            "lineage",
            "policy_version",
            "proposal_set_id",
            "report_id",
            "schema_version",
            "work_identity_level",
        }
        or raw["schema_version"] != LEVEL_PROPOSAL_SET_SCHEMA
        or raw["policy_version"] != POLICY_VERSION
        or raw["proposal_set_id"] != proposal_set_id
        or raw["report_id"] != report_id
        or level_set.report_id != report_id
        or raw["work_identity_level"] != level
        or level not in WIDER_WORK_IDENTITY_LEVELS
        or type(raw["lineage"]) is not list
        or canonical_json(raw) != document
        or len(document.encode("utf-8")) > MAX_LEVEL_PROPOSAL_SET_BYTES
        or identity
        != "level_proposal_set_" + hashlib.sha256(document.encode("utf-8")).hexdigest()
    ):
        raise LevelProposalSetError("level proposal set is invalid")
    return level_set


def _lineage(
    level_set: ProposalSet, evidence: LevelEvidence
) -> list[dict[str, object]]:
    """Every level alternative's rule and creator comparison (S13, AC11).

    An alternative is a level alternative when any of its evidence references
    belongs to a candidate only the level admits.
    """
    references = evidence.references()
    lineage: list[dict[str, object]] = []
    for result in level_set.results:
        if result.destination.value not in LEVEL_OFFER_DESTINATIONS:
            continue
        for alternative in result.alternatives:
            cited = references.intersection(alternative.evidence_references)
            if not cited:
                continue
            lineage.append(
                {
                    "alternative_id": alternative.alternative_id,
                    "creator_comparison": (
                        "initials_fold"
                        if any(
                            evidence.comparisons[reference] == "initials_fold"
                            for reference in cited
                        )
                        else "closed"
                    ),
                    "destination": result.destination.value,
                    "rule": f"work_identity:{evidence.level.value}",
                }
            )
    return lineage


def _outcomes(
    case: ReviewCase,
    proposal: ProposalSet,
    report: MetadataReport,
    policy: ResolvedMetadataPolicy,
    policy_generation: int,
    level: WorkIdentityLevel,
) -> tuple[str, ShadowOutcomeSet | None]:
    """One set's decision set id and its outcome set, computed in memory.

    A recording projects no outcome while no audiobook tranche is qualified,
    so none of its fields is decided automatically then (#966).
    """
    packet = ShadowReviewPacket.create(
        case,
        proposal,
        report,
        include_policy_ignored=True,
        work_identity_level=level,
    )
    decisions = decide_shadow_packet(packet)
    if (
        case.media_kind == "audiobook"
        and not audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
    ):
        return decisions.decision_set_id, None
    return decisions.decision_set_id, ShadowOutcomeSet.create(
        case,
        decisions,
        policy,
        policy_generation,
        report,
        proposal,
        media_kind=case.media_kind,
        work_identity_level=level,
    )


def _dispositions(outcome: ShadowOutcomeSet | None) -> dict[str, str]:
    """Each destination's live disposition; an automatic one wins a tie."""
    dispositions: dict[str, str] = {}
    for value in () if outcome is None else outcome.outcomes:
        if (
            value.destination not in dispositions
            or value.disposition in _AUTOMATIC_DISPOSITIONS
        ):
            dispositions[value.destination] = value.disposition
    return dispositions


def _manual(dispositions: Mapping[str, str], destination: str) -> bool:
    """Whether the live decision leaves this field to the owner (AC17)."""
    disposition = dispositions.get(destination)
    return disposition is None or disposition not in (
        _AUTOMATIC_DISPOSITIONS | {"ignore"}
    )


def derive_level(
    report: MetadataReport,
    live: ProposalSet,
    case: ReviewCase,
    policy: ResolvedMetadataPolicy,
    policy_generation: int,
    level: WorkIdentityLevel,
    policy_destinations: Mapping[str, str] | None = None,
) -> LevelDerivation:
    """Derive one case's level set, notes and offer at one wider level (S10).

    ``live`` and ``case`` are the live set and its case, unchanged. The level
    set is evaluated with the same resolved policy; its decision set is
    computed and never stored. A live manual field the level offers other
    alternatives for is substituted; the title, subtitle and series group is
    substituted whole or not at all, and not at all when any member is
    decided automatically. A field decided automatically keeps its live
    proposal.
    """
    if (
        type(report) is not MetadataReport
        or type(live) is not ProposalSet
        or type(case) is not ReviewCase
        or type(level) is not WorkIdentityLevel
        or level is WorkIdentityLevel.IDENTIFIER
        or case.proposal_set_id != live.proposal_set_id
        or case.report_id != report.report_id
    ):
        raise LevelProposalSetError("level derivation input is invalid")
    evidence = cast("LevelEvidence", level_evidence(report, level))
    level_set = evaluate_report(
        report,
        live.resolved_policy_id,
        policy_destinations,
        work_identity_level=level,
    )
    lineage = _lineage(level_set, evidence)
    identity, document = level_proposal_set_document(live, level_set, level, lineage)
    decision_set_id, live_outcome = _outcomes(
        case, live, report, policy, policy_generation, WorkIdentityLevel.IDENTIFIER
    )
    live_dispositions = _dispositions(live_outcome)
    _, level_outcome = _outcomes(
        ReviewCase.create(level_set, report),
        level_set,
        report,
        policy,
        policy_generation,
        level,
    )
    level_alternatives = {
        cast("str", entry["alternative_id"]): entry for entry in lineage
    }
    notes = tuple(
        LevelDecisionNote(
            decision_set_id,
            identity,
            destination,
            level.value,
            f"work_identity:{level.value}",
            candidate,
        )
        for destination, candidate in (
            (value.destination, value.candidate_id)
            for value in (() if level_outcome is None else level_outcome.outcomes)
            if value.disposition == "candidate" and value.candidate_id is not None
        )
        if destination in LEVEL_OFFER_DESTINATIONS
        and _manual(live_dispositions, destination)
        and candidate in level_alternatives
    )
    return LevelDerivation(
        level,
        identity,
        document,
        notes,
        _offer(case, live, level_set, level, identity, lineage, live_dispositions),
    )


def _offer(
    case: ReviewCase,
    live: ProposalSet,
    level_set: ProposalSet,
    level: WorkIdentityLevel,
    identity: str,
    lineage: list[dict[str, object]],
    dispositions: Mapping[str, str],
) -> str | None:
    """The case's level offer document, or ``None`` when nothing differs."""
    live_results = {value.destination.value: value for value in live.results}
    level_results = {value.destination.value: value for value in level_set.results}
    changed = {
        destination
        for destination in LEVEL_OFFER_DESTINATIONS
        if tuple(value.to_data() for value in live_results[destination].alternatives)
        != tuple(value.to_data() for value in level_results[destination].alternatives)
    }
    substituted = {
        destination
        for destination in changed
        if destination not in LEVEL_COMBINATION_DESTINATIONS
        and _manual(dispositions, destination)
    }
    if changed.intersection(LEVEL_COMBINATION_DESTINATIONS) and all(
        _manual(dispositions, destination)
        for destination in LEVEL_COMBINATION_DESTINATIONS
    ):
        substituted.update(LEVEL_COMBINATION_DESTINATIONS)
    if not substituted:
        return None
    return level_offer_document(
        case,
        level.value,
        identity,
        tuple(
            (
                destination.value,
                [
                    value.to_data()
                    for value in level_results[destination.value].alternatives
                ],
                sorted(
                    (
                        {
                            "alternative_id": entry["alternative_id"],
                            "creator_comparison": entry["creator_comparison"],
                            "rule": entry["rule"],
                        }
                        for entry in lineage
                        if entry["destination"] == destination.value
                    ),
                    key=lambda entry: cast("str", entry["alternative_id"]),
                ),
            )
            for destination in TargetDestination
            if destination.value in substituted
        ),
    )


__all__ = [
    "LEVEL_OFFER_DESTINATIONS",
    "LEVEL_PROPOSAL_SET_SCHEMA",
    "MAX_LEVEL_PROPOSAL_SET_BYTES",
    "LevelDecisionNote",
    "LevelDerivation",
    "LevelProposalSetError",
    "derive_level",
    "level_proposal_set_document",
    "validate_level_proposal_set",
]
