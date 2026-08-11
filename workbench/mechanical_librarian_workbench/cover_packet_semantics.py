"""One semantic validator shared by cover packet production and admission."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from mechanical_librarian.domain.cover_evidence import PREVIEW_VARIANT
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)

MAX_COVER_PREVIEWS = 12
MAX_COVER_PREVIEW_BYTES = 1_048_576
MAX_COVER_PACKET_BYTES = 8_388_608

_COVER_REFERENCE = re.compile(r"^cover_ref_[0-9a-f]{64}$")
_PACKET_REFERENCE = re.compile(r"^cover_packet_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SOURCE_KINDS = frozenset({"abs", "epub", "hardcover", "open_library"})
_DESIGNATION_RANK = {
    "exact_edition": 0,
    "related_edition": 1,
    "work": 2,
    "default": 3,
}
_CONTEXT_FIELDS = frozenset({
    "country", "format", "isbn", "language", "publication_date",
    "publication_place", "publisher",
})
_SOURCE_OMISSIONS = frozenset({
    "source_absent", "source_animated_or_multiframe", "source_decode_failed",
    "source_invalid_declaration", "source_oversized", "source_rejected",
    "source_source_changed", "source_timeout", "source_truncated",
    "source_unavailable", "source_unsafe_remote", "source_unsupported_format",
})


@dataclass(frozen=True, slots=True)
class AdmittedCoverPacket:
    document: str
    images: tuple[tuple[str, str, bytes], ...]


def canonical_packet_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def provenance_sort_key(
    value: dict[str, object],
) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    context = value["context"]
    if type(context) is not list:
        raise ValueError("cover provenance is invalid")
    return (
        value["source_kind"],
        value["provider"] or "",
        value["designation"] or "",
        tuple((item["field"], item["value"]) for item in context),
    )


def normalize_provenance(
    values: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Discard semantically empty multiplicity and return canonical order."""
    by_value = {canonical_packet_json(value): value for value in values}
    return sorted(by_value.values(), key=provenance_sort_key)


def candidate_priority(
    source_counts: dict[str, int],
    source_kind: str,
    identity_rank: int,
    width: int,
    height: int,
    reference: str,
) -> tuple[int, int, int, str, str]:
    return (
        source_counts[source_kind], identity_rank, -(width * height),
        source_kind, reference,
    )


def designation_rank(value: str | None) -> int:
    return _DESIGNATION_RANK.get(value, 4)


def bind_cover_packet(
    packet_document: str,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> AdmittedCoverPacket:
    """Validate every v1 packet field, invariant, and exact ordered image."""
    try:
        packet = json.loads(
            packet_document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if (
            type(packet) is not dict
            or canonical_packet_json(packet) != packet_document
            or set(packet) != {"candidates", "omissions", "packet_reference", "schema_version"}
            or packet["schema_version"] != "cover-review-packet-v1"
            or type(packet["candidates"]) is not list
            or not 1 <= len(packet["candidates"]) <= MAX_COVER_PREVIEWS
            or type(packet["omissions"]) is not list
            or _PACKET_REFERENCE.fullmatch(packet["packet_reference"]) is None
            or type(image_payloads) is not tuple
            or any(type(value) is not tuple or len(value) != 3 for value in image_payloads)
        ):
            raise ValueError
        candidates = packet["candidates"]
        candidate_facts = tuple(_validate_candidate(value) for value in candidates)
        supplied = tuple((value[0], value[1]) for value in image_payloads)
        expected = tuple((value["reference"], value["preview_sha256"]) for value in candidates)
        if (
            supplied != expected
            or len({value[0] for value in supplied}) != len(supplied)
            or len({value[1] for value in supplied}) != len(supplied)
        ):
            raise ValueError
        total = 0
        for image, candidate in zip(image_payloads, candidates, strict=True):
            reference, digest, payload = image
            if (
                type(reference) is not str
                or type(digest) is not str
                or type(payload) is not bytes
                or not 1 <= len(payload) <= MAX_COVER_PREVIEW_BYTES
                or not payload.startswith(b"\x89PNG\r\n\x1a\n")
                or hashlib.sha256(payload).hexdigest() != digest
                or len(payload) != candidate["preview_bytes"]
            ):
                raise ValueError
            total += len(payload)
        if total > MAX_COVER_PACKET_BYTES:
            raise ValueError
        _validate_candidate_order(candidates, candidate_facts)
        retained = {
            value["reference"]: value["preview_sha256"] for value in candidates
        }
        _validate_omissions(packet["omissions"], retained, total)
        packet_body = canonical_packet_json({
            "candidates": packet["candidates"],
            "omissions": packet["omissions"],
            "schema_version": packet["schema_version"],
        })
        if packet["packet_reference"] != "cover_packet_" + hashlib.sha256(packet_body.encode()).hexdigest():
            raise ValueError
        return AdmittedCoverPacket(packet_document, image_payloads)
    except (
        EvidenceValueError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise ValueError("cover packet is invalid") from error


def _validate_candidate(value: object) -> tuple[str, int]:
    if (
        type(value) is not dict
        or set(value) != {
            "height", "incumbent", "media_type", "preview_bytes",
            "preview_sha256", "preview_variant", "provenance", "reference", "width",
        }
        or _COVER_REFERENCE.fullmatch(value["reference"]) is None
        or _DIGEST.fullmatch(value["preview_sha256"]) is None
        or value["media_type"] != "image/png"
        or value["preview_variant"] != PREVIEW_VARIANT
        or type(value["preview_bytes"]) is not int
        or not 1 <= value["preview_bytes"] <= MAX_COVER_PREVIEW_BYTES
        or type(value["width"]) is not int
        or type(value["height"]) is not int
        or not 1 <= value["width"] <= 8192
        or not 1 <= value["height"] <= 8192
        or value["width"] * value["height"] > 16_777_216
        or type(value["incumbent"]) is not bool
        or type(value["provenance"]) is not list
        or not value["provenance"]
    ):
        raise ValueError
    provenance = tuple(_validate_provenance(item) for item in value["provenance"])
    if provenance != tuple(sorted(set(provenance))):
        raise ValueError
    incumbent = any(item[0] == "abs" for item in provenance)
    if value["incumbent"] is not incumbent:
        raise ValueError
    source_kind = min(item[0] for item in provenance)
    rank = min(
        (designation_rank(item[2]) for item in provenance if item[2]),
        default=4,
    )
    return source_kind, rank


def _validate_provenance(
    value: object,
) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    if (
        type(value) is not dict
        or set(value) != {"context", "designation", "provider", "source_kind"}
        or value["source_kind"] not in _SOURCE_KINDS
        or type(value["context"]) is not list
        or len(value["context"]) > 16
    ):
        raise ValueError
    source_kind = value["source_kind"]
    if source_kind in {"abs", "epub"}:
        if value["provider"] is not None or value["designation"] is not None or value["context"]:
            raise ValueError
        return source_kind, "", "", ()
    if (
        type(value["provider"]) is not str
        or _PROVIDER.fullmatch(value["provider"]) is None
        or value["designation"] not in _DESIGNATION_RANK
    ):
        raise ValueError
    context = tuple(_validate_context(item) for item in value["context"])
    if context != tuple(sorted(set(context))):
        raise ValueError
    return source_kind, value["provider"], value["designation"], context


def _validate_context(value: object) -> tuple[str, str]:
    if type(value) is not dict or set(value) != {"field", "value"} or value["field"] not in _CONTEXT_FIELDS:
        raise ValueError
    validated = validate_projection_text(value["value"], "cover context", 280)
    return value["field"], validated


def _validate_candidate_order(
    candidates: list[dict[str, object]],
    facts: tuple[tuple[str, int], ...],
) -> None:
    incumbent_positions = [
        index for index, candidate in enumerate(candidates)
        if candidate["incumbent"] is True
    ]
    if len(incumbent_positions) > 1 or incumbent_positions not in ([], [0]):
        raise ValueError
    remaining = list(range(len(candidates)))
    expected: list[int] = []
    source_counts = {kind: 0 for kind in _SOURCE_KINDS}
    if incumbent_positions:
        index = incumbent_positions[0]
        remaining.remove(index)
        expected.append(index)
        source_counts[facts[index][0]] += 1
    while remaining:
        index = min(
            remaining,
            key=lambda position: candidate_priority(
                source_counts,
                facts[position][0],
                facts[position][1],
                candidates[position]["width"],
                candidates[position]["height"],
                candidates[position]["reference"],
            ),
        )
        remaining.remove(index)
        expected.append(index)
        source_counts[facts[index][0]] += 1
    if expected != list(range(len(candidates))):
        raise ValueError


def _validate_omissions(
    omissions: list[object],
    retained: dict[str, str],
    retained_bytes: int,
) -> None:
    normalized: list[tuple[str, str]] = []
    omitted_references: set[str] = set()
    for omission in omissions:
        if type(omission) is not dict or type(omission.get("reason")) is not str:
            raise ValueError
        reason = omission["reason"]
        if reason in _SOURCE_OMISSIONS:
            expected = {"reason", "reference"}
        elif reason == "duplicate_preview":
            expected = {"preview_sha256", "reason", "reference", "retained_reference"}
        elif reason in {"aggregate_byte_limit", "preview_byte_limit", "shortlist_limit"}:
            expected = {"preview_bytes", "reason", "reference"}
        else:
            raise ValueError
        if set(omission) != expected or _COVER_REFERENCE.fullmatch(omission["reference"]) is None:
            raise ValueError
        reference = omission["reference"]
        if reference in retained or reference in omitted_references:
            raise ValueError
        omitted_references.add(reference)
        if reason == "duplicate_preview":
            retained_reference = omission["retained_reference"]
            digest = omission["preview_sha256"]
            if retained.get(retained_reference) != digest or _DIGEST.fullmatch(digest) is None:
                raise ValueError
        elif reason in {"aggregate_byte_limit", "preview_byte_limit", "shortlist_limit"}:
            preview_bytes = omission["preview_bytes"]
            if (
                type(preview_bytes) is not int
                or not 1 <= preview_bytes <= 2_147_483_647
            ):
                raise ValueError
            if reason == "preview_byte_limit" and preview_bytes <= MAX_COVER_PREVIEW_BYTES:
                raise ValueError
            if reason == "aggregate_byte_limit" and (
                preview_bytes > MAX_COVER_PREVIEW_BYTES
                or retained_bytes + preview_bytes <= MAX_COVER_PACKET_BYTES
            ):
                raise ValueError
            if reason == "shortlist_limit" and (
                preview_bytes > MAX_COVER_PREVIEW_BYTES
                or len(retained) != MAX_COVER_PREVIEWS
            ):
                raise ValueError
        normalized.append((reference, reason))
    if normalized != sorted(normalized):
        raise ValueError


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(value)


__all__ = [
    "MAX_COVER_PACKET_BYTES", "MAX_COVER_PREVIEW_BYTES", "MAX_COVER_PREVIEWS",
    "bind_cover_packet", "candidate_priority", "canonical_packet_json",
    "designation_rank", "normalize_provenance", "provenance_sort_key",
]
