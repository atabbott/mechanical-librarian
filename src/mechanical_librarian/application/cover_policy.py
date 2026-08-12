"""Deterministic policy adjudication over exact report-bound cover evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .cover_identity import (
    SCHEMA_VERSION as IDENTITY_SCHEMA_VERSION,
    CLASSIFIER_REFERENCE,
    _preview_reference,
    classify_cover_identity,
)
from .cover_observation import (
    INSTRUCTION,
    LEGACY_INSTRUCTION_SHA256,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
    TAXONOMY_REFERENCE,
    _observer_reference,
    _output_schema,
)
from .cover_packet_semantics import bind_cover_packet, canonical_packet_json

SCHEMA_VERSION = "cover-policy-report-v1"
POLICY_SCHEMA_VERSION = "cover-policy-v1"
_IDENTITY_OUTCOMES = frozenset({
    "conflict",
    "plausible_match",
    "unknown",
    "verified_manifestation_match",
    "verified_work_match",
})
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
        return "cover_policy_" + hashlib.sha256(self.document.encode("utf-8")).hexdigest()


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
) -> CoverPolicyReport:
    """Return one exact recommendation or a closed abstention."""
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
    if observation_document is None:
        return _report(
            packet["packet_reference"],
            classifier_reference,
            policy,
            identity_document,
            None,
            (),
            None,
            ("observations_unavailable",),
        )

    observations = _observation_rows(
        observation_document,
        packet["packet_reference"],
        candidates,
        references,
        profile_payload,
    )
    identity_by_reference = {
        value["candidate_reference"]: value["outcome"]
        for value in identity["candidates"]
    }
    reasons: set[str] = set()
    eligible = []
    for reference in references:
        identity_outcome = identity_by_reference[reference]
        if identity_outcome == "conflict":
            reasons.add("conflict_excluded")
        elif identity_outcome == "unknown":
            reasons.add("identity_unknown_excluded")
        else:
            eligible.append(reference)
    if not eligible:
        reasons.add("no_eligible_candidates")
        return _report(
            packet["packet_reference"],
            classifier_reference,
            policy,
            identity_document,
            observation_document,
            (),
            None,
            tuple(sorted(reasons)),
        )

    by_reference = {
        value["candidate_reference"]: value for value in observations["observations"]
    }
    ambiguous = False
    acceptable: list[str] = []
    for reference in eligible:
        value = by_reference[reference]
        excluded = False
        if (
            policy.avoid_promotional_text
            and value["promotional_text"] == "present"
        ):
            reasons.add("promotional_text_excluded")
            excluded = True
        if (
            policy.avoid_tie_in_branding
            and value["tie_in_branding"] == "present"
        ):
            reasons.add("tie_in_branding_excluded")
            excluded = True
        if excluded:
            continue
        if (
            policy.avoid_promotional_text
            and value["promotional_text"] == "unknown"
        ) or (
            policy.avoid_tie_in_branding
            and value["tie_in_branding"] == "unknown"
        ):
            ambiguous = True
        acceptable.append(reference)
    if policy.prefer_minimal_composition and any(
        by_reference[reference]["composition"] == "unknown"
        for reference in acceptable
    ):
        ambiguous = True
    if ambiguous:
        return _report(
            packet["packet_reference"],
            classifier_reference,
            policy,
            identity_document,
            observation_document,
            (),
            None,
            ("observations_ambiguous",),
        )
    if not acceptable:
        reasons.add("no_policy_compatible_candidate")
        return _report(
            packet["packet_reference"],
            classifier_reference,
            policy,
            identity_document,
            observation_document,
            (),
            None,
            tuple(sorted(reasons)),
        )

    preferred = acceptable
    if policy.prefer_minimal_composition:
        minimal = [
            reference
            for reference in acceptable
            if by_reference[reference]["composition"] == "minimal"
        ]
        if minimal:
            preferred = minimal
            reasons.add("preferred_minimal")
    reasons.add("policy_satisfied")
    return _report(
        packet["packet_reference"],
        classifier_reference,
        policy,
        identity_document,
        observation_document,
        tuple(acceptable),
        preferred[0],
        tuple(sorted(reasons)),
    )


def _identity_rows(
    document: str,
    packet_reference: object,
    candidates: list[object],
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
            != _preview_reference(packet_reference, expected[row["candidate_reference"]])
            or any(_REFERENCE.fullmatch(str(item)) is None for item in row["evidence_references"])
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
    candidates: list[object],
    references: tuple[str, ...],
    profile_payload: bytes | None,
) -> dict[str, object]:
    value = _load_object(document, "cover observation")
    schema_version = value.get("schema_version")
    expected_instruction = (
        LEGACY_INSTRUCTION_SHA256
        if schema_version == LEGACY_SCHEMA_VERSION
        else hashlib.sha256(INSTRUCTION.encode("utf-8")).hexdigest()
    )
    expected_schema = hashlib.sha256(_output_schema(references).encode("utf-8")).hexdigest()
    if type(profile_payload) is not bytes:
        raise ValueError("cover observation profile is invalid")
    expected_observer = _observer_reference(
        profile_payload, expected_instruction, expected_schema
    )
    valid_shape = (
        (
            schema_version == LEGACY_SCHEMA_VERSION
            and set(value) == {
                "cover_packet_reference", "instruction_sha256", "observations",
                "observer_reference", "output_schema_sha256", "schema_version",
                "taxonomy_reference",
            }
        )
        or (
            schema_version == OBSERVATION_SCHEMA_VERSION
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
    )
    if (
        not valid_shape
        or value["schema_version"] not in {LEGACY_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION}
        or value["cover_packet_reference"] != packet_reference
        or value["taxonomy_reference"] != TAXONOMY_REFERENCE
        or value["observer_reference"] != expected_observer
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
                "composition",
                "medium",
                "preview_sha256",
                "preview_variant",
                "promotional_text",
                "tie_in_branding",
            }
            or _required_reference(row["candidate_reference"]) not in references
        ):
            raise ValueError("cover observation report is invalid")
        candidate = expected[row["candidate_reference"]]
        if (
            row["preview_sha256"] != candidate["preview_sha256"]
            or row["preview_variant"] != candidate["preview_variant"]
            or row["composition"] not in {"dense", "minimal", "unknown"}
            or row["medium"]
            not in {"illustrated", "mixed", "photographic", "typographic", "unknown"}
            or row["promotional_text"] not in {"absent", "present", "unknown"}
            or row["tie_in_branding"] not in {"absent", "present", "unknown"}
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
                != {"diagnostic_bytes", "diagnostic_sha256", "dispatched", "failure_code"}
                or attempt["failure_code"] != "local_cli_abort"
                or type(attempt["diagnostic_bytes"]) is not int
                or not 1 <= attempt["diagnostic_bytes"] <= 2_147_483_647
                or type(attempt["diagnostic_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", attempt["diagnostic_sha256"])
                is None
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
    acceptable: tuple[str, ...],
    recommendation: str | None,
    reasons: tuple[str, ...],
) -> CoverPolicyReport:
    if recommendation is not None and recommendation not in acceptable:
        raise ValueError("cover recommendation is invalid")
    status = "recommended" if recommendation is not None else "abstained"
    body = {
        "acceptable_candidate_references": list(acceptable),
        "cover_packet_reference": packet_reference,
        "identity_classifier_reference": classifier_reference,
        "identity_report_sha256": hashlib.sha256(identity_document.encode("utf-8")).hexdigest(),
        "observation_report_sha256": (
            hashlib.sha256(observation_document.encode("utf-8")).hexdigest()
            if observation_document is not None
            else None
        ),
        "observation_taxonomy_reference": TAXONOMY_REFERENCE,
        "policy_reference": policy.reference,
        "reasons": list(reasons),
        "recommended_candidate_reference": recommendation,
        "schema_version": SCHEMA_VERSION,
        "status": status,
    }
    return CoverPolicyReport(canonical_packet_json(body))


def _required_reference(value: object) -> str:
    if type(value) is not str or _REFERENCE.fullmatch(value) is None:
        raise ValueError("cover reference is invalid")
    return value


def _load_object(document: str, label: str) -> dict[str, object]:
    try:
        if type(document) is not str or not 2 <= len(document.encode("utf-8")) <= 1_048_576:
            raise ValueError
        value = json.loads(
            document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if type(value) is not dict or canonical_packet_json(value) != document:
            raise ValueError
        return value
    except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError, ValueError) as error:
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
