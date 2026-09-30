"""Deterministic policy adjudication over exact report-bound cover evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from mechanical_librarian.domain.deterministic_cover_election import (
    CoverActualTargetState,
    CoverElectionCandidate,
    elect_confirmed_covers,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFieldState,
)

from .cover_identity import (
    CLASSIFIER_REFERENCE,
    _preview_reference,
    classify_cover_identity,
)
from .cover_identity import (
    SCHEMA_VERSION as IDENTITY_SCHEMA_VERSION,
)
from .cover_observation import (
    INSTRUCTION,
    LEGACY_INSTRUCTION_SHA256,
    LEGACY_OUTPUT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    LEGACY_TAXONOMY,
    LEGACY_TAXONOMY_REFERENCE,
    OUTPUT_SCHEMA_VERSION,
    PREVIOUS_INSTRUCTION_SHA256,
    PREVIOUS_SCHEMA_VERSION,
    RETAINED_V3_INSTRUCTION_SHA256,
    TAXONOMY,
    TAXONOMY_REFERENCE,
    _observer_reference,
    _output_schema,
)
from .cover_observation import (
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
)
from .cover_packet_semantics import bind_cover_packet, canonical_packet_json

SCHEMA_VERSION = "cover-policy-report-v2"
POLICY_SCHEMA_VERSION = "cover-policy-v1"
_IDENTITY_OUTCOMES = frozenset(
    {
        "conflict",
        "plausible_match",
        "unknown",
        "verified_manifestation_match",
        "verified_work_match",
    }
)
_REFERENCE = re.compile(r"[a-z][a-z0-9_]{0,63}_[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class CoverPolicy:
    """One closed owner policy; no prompt text or free-form preference."""

    avoid_promotional_text: bool = False
    avoid_tie_in_branding: bool = False
    prefer_minimal_composition: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (
                self.avoid_promotional_text,
                self.avoid_tie_in_branding,
                self.prefer_minimal_composition,
            )
        ):
            raise ValueError("cover policy is invalid")

    @property
    def document(self) -> str:
        return canonical_packet_json(
            {
                "avoid_promotional_text": self.avoid_promotional_text,
                "avoid_tie_in_branding": self.avoid_tie_in_branding,
                "prefer_minimal_composition": self.prefer_minimal_composition,
                "schema_version": POLICY_SCHEMA_VERSION,
            }
        )

    @property
    def reference(self) -> str:
        return (
            "cover_policy_" + hashlib.sha256(self.document.encode("utf-8")).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class CoverPolicyReport:
    document: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.document.encode("utf-8")).hexdigest()


def adjudicate_cover_policy(
    report_document: str,
    packet_document: str,
    image_payloads: tuple[tuple[str, str, bytes], ...],
    observation_document: str | None,
    policy: CoverPolicy,
    *,
    profile_payload: bytes | None = None,
    read_preview: Callable[[str, str], bytes],
    provider_fetching: bool = False,
) -> CoverPolicyReport:
    """Return an identity-confirmed technical recommendation.

    Observations remain bound diagnostic evidence, but cannot change the
    confirmation, readiness, or order that this policy reports.
    """
    if type(policy) is not CoverPolicy:
        raise ValueError("cover policy is invalid")
    identity_document = classify_cover_identity(
        report_document,
        packet_document,
        image_payloads,
        read_preview=read_preview,
    ).document
    bind_cover_packet(packet_document, image_payloads)
    packet = _load_object(packet_document, "cover packet")
    candidates = packet["candidates"]
    if type(candidates) is not list:
        raise ValueError("cover packet is invalid")
    references = tuple(_required_reference(value["reference"]) for value in candidates)

    identity = _identity_rows(
        identity_document,
        packet["packet_reference"],
        candidates,
        references,
    )
    classifier_reference = identity["classifier_reference"]
    identity_by_reference = {
        value["candidate_reference"]: value
        for value in cast("list[dict[str, object]]", identity["candidates"])
    }
    observation_taxonomy_reference = TAXONOMY_REFERENCE
    if observation_document is not None:
        # Reviewer observations remain advisory, but an admitted document must
        # still satisfy the exact packet/profile binding before it is retained.
        observation = _observation_rows(
            observation_document,
            packet["packet_reference"],
            candidates,
            references,
            profile_payload,
        )
        observation_taxonomy_reference = cast("str", observation["taxonomy_reference"])
    election_candidates = tuple(
        CoverElectionCandidate(
            reference=reference,
            identity=cast("str", identity_by_reference[reference]["outcome"]),
            identity_citations=tuple(
                cast(
                    "list[str]",
                    identity_by_reference[reference]["evidence_references"],
                )
            ),
            source_citations=tuple(
                cast("str", binding["reference"])
                for binding in candidates[index]["source_bindings"]
            ),
            incumbent=cast("bool", candidates[index]["incumbent"]),
            width=cast("int", candidates[index]["width"]),
            height=cast("int", candidates[index]["height"]),
            packet_ordinal=index,
        )
        for index, reference in enumerate(references)
        if identity_by_reference[reference]["outcome"]
        in {"verified_manifestation_match", "verified_work_match"}
    )
    report = MetadataReport.from_json(report_document)
    target_cover = next(
        value
        for value in report.target.fields
        if value.destination is TargetDestination.COVER
    )
    has_confirmed_incumbent = any(
        candidate.incumbent for candidate in election_candidates
    )
    has_retained_incumbent = any(
        cast("bool", candidate["incumbent"]) for candidate in candidates
    )
    actual_target_state = (
        CoverActualTargetState.CONFIRMED
        if target_cover.state is TargetFieldState.VALUE and has_confirmed_incumbent
        else CoverActualTargetState.KNOWN_EMPTY
        if target_cover.state
        in {
            TargetFieldState.MISSING,
            TargetFieldState.NULL,
            TargetFieldState.EMPTY,
        }
        and not has_retained_incumbent
        else CoverActualTargetState.UNKNOWN
    )
    election = elect_confirmed_covers(
        election_candidates,
        provider_fetching=provider_fetching,
        actual_target_state=actual_target_state,
    )
    acceptable = tuple(item.candidate.reference for item in election.ranked)
    return _report(
        packet["packet_reference"],
        classifier_reference,
        policy,
        identity_document,
        observation_document,
        observation_taxonomy_reference,
        acceptable,
        None
        if election.recommendation is None
        else election.recommendation.candidate.reference,
        election.ready,
        actual_target_state.value,
        (
            ("provider_fetching",)
            if acceptable and provider_fetching
            else ("actual_target_unverified",)
            if acceptable and actual_target_state is CoverActualTargetState.UNKNOWN
            else ("confirmed_incumbent_not_improved",)
            if acceptable
            and actual_target_state is CoverActualTargetState.CONFIRMED
            and election.recommendation is None
            else ("deterministic_confirmed_order",)
            if acceptable
            else ("no_confirmed_candidates",)
        ),
    )


def _identity_rows(
    document: str,
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
) -> dict[str, object]:
    value = _load_object(document, "cover identity")
    if (
        set(value)
        != {
            "candidates",
            "classifier_reference",
            "cover_packet_reference",
            "report_reference",
            "schema_version",
            "work_target_reference",
        }
        or value["schema_version"] != IDENTITY_SCHEMA_VERSION
        or value["cover_packet_reference"] != packet_reference
        or value["classifier_reference"] != CLASSIFIER_REFERENCE
        or _REFERENCE.fullmatch(str(value["report_reference"])) is None
        or _REFERENCE.fullmatch(str(value["work_target_reference"])) is None
        or type(value["candidates"]) is not list
    ):
        raise ValueError("cover identity report is invalid")
    rows = value["candidates"]
    if len(rows) != len(references):
        raise ValueError("cover identity report is invalid")
    admitted = []
    expected = {candidate["reference"]: candidate for candidate in candidates}
    for row in rows:
        if (
            type(row) is not dict
            or set(row)
            != {
                "candidate_reference",
                "evidence_references",
                "outcome",
                "preview_reference",
                "reasons",
            }
            or _required_reference(row["candidate_reference"]) not in references
            or row["outcome"] not in _IDENTITY_OUTCOMES
            or type(row["evidence_references"]) is not list
            or type(row["reasons"]) is not list
            or row["preview_reference"]
            != _preview_reference(
                # ``bind_cover_packet`` already admitted the packet, so its
                # ``packet_reference`` is the validated reference string.
                cast("str", packet_reference),
                expected[row["candidate_reference"]],
            )
            or any(
                _REFERENCE.fullmatch(str(item)) is None
                for item in row["evidence_references"]
            )
            or any(type(item) is not str for item in row["reasons"])
        ):
            raise ValueError("cover identity report is invalid")
        admitted.append(row)
    if tuple(sorted(row["candidate_reference"] for row in admitted)) != tuple(
        sorted(references)
    ):
        raise ValueError("cover identity report is invalid")
    return value


def _observation_rows(
    document: str,
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
    profile_payload: bytes | None,
) -> dict[str, object]:
    value, taxonomy, taxonomy_reference, expected_instruction, expected_schema = (
        _observation_header(document, references)
    )
    if expected_instruction == RETAINED_V3_INSTRUCTION_SHA256:
        raise ValueError("cover observation report is not current")
    if type(profile_payload) is not bytes:
        raise ValueError("cover observation profile is invalid")
    expected_observer = _observer_reference(
        profile_payload,
        expected_instruction,
        expected_schema,
        taxonomy_reference,
    )
    value = _validated_observation_rows(
        value,
        packet_reference,
        candidates,
        references,
        taxonomy,
        taxonomy_reference,
        expected_instruction,
        expected_schema,
    )
    if value["observer_reference"] != expected_observer:
        raise ValueError("cover observation report is invalid")
    return value


def _observation_document_rows(
    document: str,
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
) -> tuple[dict[str, object], str, str, str]:
    """Validate retained observation semantics without claiming profile identity."""
    value, taxonomy, taxonomy_reference, expected_instruction, expected_schema = (
        _observation_header(document, references)
    )
    return (
        _validated_observation_rows(
            value,
            packet_reference,
            candidates,
            references,
            taxonomy,
            taxonomy_reference,
            expected_instruction,
            expected_schema,
        ),
        expected_instruction,
        expected_schema,
        taxonomy_reference,
    )


def _observation_header(
    document: str,
    references: tuple[str, ...],
) -> tuple[
    dict[str, object],
    tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    str,
    str,
    str,
]:
    value = _load_object(document, "cover observation")
    schema_version = value.get("schema_version")
    if type(schema_version) is not str:
        raise ValueError("cover observation report is invalid")
    legacy = schema_version in {LEGACY_SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION}
    taxonomy = LEGACY_TAXONOMY if legacy else TAXONOMY
    taxonomy_reference = LEGACY_TAXONOMY_REFERENCE if legacy else TAXONOMY_REFERENCE
    expected_instruction = {
        LEGACY_SCHEMA_VERSION: LEGACY_INSTRUCTION_SHA256,
        PREVIOUS_SCHEMA_VERSION: PREVIOUS_INSTRUCTION_SHA256,
        OBSERVATION_SCHEMA_VERSION: hashlib.sha256(
            INSTRUCTION.encode("utf-8")
        ).hexdigest(),
    }.get(schema_version)
    if (
        schema_version == OBSERVATION_SCHEMA_VERSION
        and value.get("instruction_sha256") == RETAINED_V3_INSTRUCTION_SHA256
    ):
        expected_instruction = RETAINED_V3_INSTRUCTION_SHA256
    expected_schema = hashlib.sha256(
        _output_schema(
            references,
            taxonomy=taxonomy,
            schema_version=(
                LEGACY_OUTPUT_SCHEMA_VERSION if legacy else OUTPUT_SCHEMA_VERSION
            ),
        ).encode("utf-8")
    ).hexdigest()
    if expected_instruction is None:
        raise ValueError("cover observation report is invalid")
    return (
        value,
        taxonomy,
        taxonomy_reference,
        expected_instruction,
        expected_schema,
    )


def _validated_observation_rows(
    value: dict[str, object],
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
    taxonomy: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    taxonomy_reference: str,
    expected_instruction: str,
    expected_schema: str,
) -> dict[str, object]:
    taxonomy_names = tuple(name for name, _values in taxonomy)
    taxonomy_values = {name: frozenset(dict(values)) for name, values in taxonomy}
    schema_version = value["schema_version"]
    valid_shape = (
        schema_version == LEGACY_SCHEMA_VERSION
        and set(value)
        == {
            "cover_packet_reference",
            "instruction_sha256",
            "observations",
            "observer_reference",
            "output_schema_sha256",
            "schema_version",
            "taxonomy_reference",
        }
    ) or (
        schema_version in {PREVIOUS_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION}
        and set(value)
        == {
            "attempt_evidence",
            "cover_packet_reference",
            "instruction_sha256",
            "observations",
            "observer_reference",
            "output_schema_sha256",
            "schema_version",
            "taxonomy_reference",
        }
        and _attempt_evidence(value["attempt_evidence"])
    )
    if (
        not valid_shape
        or value["schema_version"]
        not in {
            LEGACY_SCHEMA_VERSION,
            PREVIOUS_SCHEMA_VERSION,
            RETAINED_V3_INSTRUCTION_SHA256,
            OBSERVATION_SCHEMA_VERSION,
        }
        or value["cover_packet_reference"] != packet_reference
        or value["taxonomy_reference"] != taxonomy_reference
        or type(value["observer_reference"]) is not str
        or _REFERENCE.fullmatch(value["observer_reference"]) is None
        or value["instruction_sha256"] != expected_instruction
        or value["output_schema_sha256"] != expected_schema
        or type(value["observations"]) is not list
    ):
        raise ValueError("cover observation report is invalid")
    expected = {candidate["reference"]: candidate for candidate in candidates}
    admitted = []
    for row in value["observations"]:
        if (
            type(row) is not dict
            or set(row)
            != {
                "candidate_reference",
                "preview_sha256",
                "preview_variant",
                *taxonomy_names,
            }
            or _required_reference(row["candidate_reference"]) not in references
        ):
            raise ValueError("cover observation report is invalid")
        candidate = expected[row["candidate_reference"]]
        if (
            row["preview_sha256"] != candidate["preview_sha256"]
            or row["preview_variant"] != candidate["preview_variant"]
            or any(row[name] not in taxonomy_values[name] for name in taxonomy_names)
        ):
            raise ValueError("cover observation report is invalid")
        admitted.append(row)
    if tuple(sorted(row["candidate_reference"] for row in admitted)) != tuple(
        sorted(references)
    ):
        raise ValueError("cover observation report is invalid")
    return value


def _attempt_evidence(value: object) -> bool:
    if type(value) is not list or not 1 <= len(value) <= 2:
        return False
    for index, attempt in enumerate(value):
        if type(attempt) is not dict or attempt.get("dispatched") is not True:
            return False
        if index == 0 and len(value) == 2:
            if (
                set(attempt)
                != {
                    "diagnostic_bytes",
                    "diagnostic_sha256",
                    "dispatched",
                    "failure_code",
                }
                or attempt["failure_code"] != "local_cli_abort"
                or type(attempt["diagnostic_bytes"]) is not int
                or not 1 <= attempt["diagnostic_bytes"] <= 2_147_483_647
                or type(attempt["diagnostic_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", attempt["diagnostic_sha256"]) is None
            ):
                return False
        elif set(attempt) != {"dispatched"}:
            return False
    return True


def _report(
    packet_reference: object,
    classifier_reference: object,
    policy: CoverPolicy,
    identity_document: str,
    observation_document: str | None,
    observation_taxonomy_reference: str,
    acceptable: tuple[str, ...],
    recommendation: str | None,
    owner_ready: bool,
    actual_target_state: str,
    reasons: tuple[str, ...],
) -> CoverPolicyReport:
    if recommendation is not None and recommendation not in acceptable:
        raise ValueError("cover recommendation is invalid")
    status = "recommended" if recommendation is not None else "abstained"
    body = {
        "acceptable_candidate_references": list(acceptable),
        "actual_target_state": actual_target_state,
        "cover_packet_reference": packet_reference,
        "identity_classifier_reference": classifier_reference,
        "identity_report_sha256": hashlib.sha256(
            identity_document.encode("utf-8")
        ).hexdigest(),
        "observation_report_sha256": (
            hashlib.sha256(observation_document.encode("utf-8")).hexdigest()
            if observation_document is not None
            else None
        ),
        "observation_taxonomy_reference": observation_taxonomy_reference,
        "owner_ready": owner_ready,
        "policy_reference": policy.reference,
        "reasons": list(reasons),
        "recommended_candidate_reference": recommendation,
        "schema_version": SCHEMA_VERSION,
        "status": status,
    }
    return CoverPolicyReport(canonical_packet_json(body))


def read_cover_policy_report(
    document: str,
    *,
    packet_reference: str,
    classifier_reference: str,
    policy: CoverPolicy,
    identity_document: str,
    observation_document: str | None,
    observation_taxonomy_reference: str,
    candidate_references: tuple[str, ...],
) -> dict[str, object]:
    """Validate one canonical policy report against its retained inputs."""
    value = _load_object(document, "cover policy report")
    fields = {
        "acceptable_candidate_references",
        "actual_target_state",
        "cover_packet_reference",
        "identity_classifier_reference",
        "identity_report_sha256",
        "observation_report_sha256",
        "observation_taxonomy_reference",
        "owner_ready",
        "policy_reference",
        "reasons",
        "recommended_candidate_reference",
        "schema_version",
        "status",
    }
    acceptable_value = value.get("acceptable_candidate_references")
    reasons_value = value.get("reasons")
    recommendation = value.get("recommended_candidate_reference")
    if (
        set(value) != fields
        or value.get("schema_version") != SCHEMA_VERSION
        or type(acceptable_value) is not list
        or any(type(item) is not str for item in acceptable_value)
        or len(set(acceptable_value)) != len(acceptable_value)
        or any(item not in candidate_references for item in acceptable_value)
        or type(value.get("actual_target_state")) is not str
        or value.get("actual_target_state")
        not in {item.value for item in CoverActualTargetState}
        or value.get("cover_packet_reference") != packet_reference
        or value.get("identity_classifier_reference") != classifier_reference
        or value.get("identity_report_sha256")
        != hashlib.sha256(identity_document.encode("utf-8")).hexdigest()
        or value.get("observation_report_sha256")
        != (
            None
            if observation_document is None
            else hashlib.sha256(observation_document.encode("utf-8")).hexdigest()
        )
        or value.get("observation_taxonomy_reference") != observation_taxonomy_reference
        or type(value.get("owner_ready")) is not bool
        or value.get("policy_reference") != policy.reference
        or type(reasons_value) is not list
        or len(reasons_value) != 1
        or any(
            type(item) is not str
            or item
            not in {
                "actual_target_unverified",
                "confirmed_incumbent_not_improved",
                "deterministic_confirmed_order",
                "no_confirmed_candidates",
                "provider_fetching",
            }
            for item in reasons_value
        )
        or (recommendation is not None and type(recommendation) is not str)
    ):
        raise ValueError("cover policy report is invalid")
    acceptable = tuple(cast("list[str]", acceptable_value))
    reasons = tuple(cast("list[str]", reasons_value))
    if (
        _report(
            packet_reference,
            classifier_reference,
            policy,
            identity_document,
            observation_document,
            observation_taxonomy_reference,
            acceptable,
            recommendation,
            cast("bool", value["owner_ready"]),
            cast("str", value["actual_target_state"]),
            reasons,
        ).document
        != document
    ):
        raise ValueError("cover policy report is invalid")
    return value


def _required_reference(value: object) -> str:
    if type(value) is not str or _REFERENCE.fullmatch(value) is None:
        raise ValueError("cover reference is invalid")
    return value


def _load_object(document: str, label: str) -> dict[str, object]:
    try:
        if (
            type(document) is not str
            or not 2 <= len(document.encode("utf-8")) <= 1_048_576
        ):
            raise ValueError
        value = json.loads(
            document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if type(value) is not dict or canonical_packet_json(value) != document:
            raise ValueError
        return value
    except (
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise ValueError(f"{label} document is invalid") from error


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid number: {value}")


__all__ = [
    "CoverPolicy",
    "CoverPolicyReport",
    "POLICY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "adjudicate_cover_policy",
]
