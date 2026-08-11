"""Bound one report's exact cover previews for an isolated reviewer job."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.domain.cover_evidence import (
    PREVIEW_VARIANT,
    CoverDesignation,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import MetadataReport

MAX_COVER_PREVIEWS = 12
MAX_COVER_PREVIEW_BYTES = 1_048_576
MAX_COVER_PACKET_BYTES = 8_388_608

_DESIGNATION_RANK = {
    CoverDesignation.EXACT_EDITION: 0,
    CoverDesignation.RELATED_EDITION: 1,
    CoverDesignation.WORK: 2,
    CoverDesignation.DEFAULT: 3,
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _opaque_reference(report_id: str, source_id: str) -> str:
    digest = hashlib.sha256(
        _canonical({"report_id": report_id, "source_id": source_id}).encode("utf-8")
    ).hexdigest()
    return "cover_ref_" + digest


@dataclass(frozen=True, slots=True)
class CoverPacketImage:
    reference: str
    digest: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class PreparedCoverPacket:
    document: str
    images: tuple[CoverPacketImage, ...]
    packet_digest: str
    omitted_count: int


@dataclass(slots=True)
class _Candidate:
    reference: str
    digest: str
    payload: bytes
    width: int
    height: int
    incumbent: bool
    sources: list[CoverSource]

    @property
    def identity_rank(self) -> int:
        ranks = [
            _DESIGNATION_RANK.get(source.provenance.designation, 4)
            for source in self.sources
            if source.provenance is not None
        ]
        return min(ranks, default=4)

    @property
    def source_kind(self) -> str:
        return min(source.kind.value for source in self.sources)


def prepare_cover_packet(
    report_document: str,
    read_preview: Callable[[str, str], bytes],
) -> PreparedCoverPacket:
    """Project exact report-bound previews into one deterministic private packet."""
    report = MetadataReport.from_json(report_document)
    retrieved = tuple(
        source for source in report.cover_sources
        if source.state is CoverSourceState.RETRIEVED
    )
    if not retrieved:
        raise ValueError("cover packet has no retrieved previews")

    candidates_by_digest: dict[str, _Candidate] = {}
    omissions: list[dict[str, str]] = [
        {
            "reason": "source_" + source.state.value,
            "reference": _opaque_reference(report.report_id, source.source_id),
        }
        for source in report.cover_sources
        if source.state is not CoverSourceState.RETRIEVED
    ]
    artifact_payloads: dict[tuple[str, str], bytes] = {}
    for source in sorted(retrieved, key=lambda value: value.source_id):
        artifact = source.artifact
        if artifact is None or artifact.preview_variant != PREVIEW_VARIANT:
            raise ValueError("cover packet preview is unbound")
        key = (artifact.artifact_id, artifact.preview_variant)
        if key not in artifact_payloads:
            payload = read_preview(*key)
            if type(payload) is not bytes:
                raise ValueError("cover packet preview is invalid")
            artifact_payloads[key] = payload
        payload = artifact_payloads[key]
        if hashlib.sha256(payload).hexdigest() != artifact.preview_digest:
            raise ValueError("cover packet preview digest changed")
        reference = _opaque_reference(report.report_id, source.source_id)
        existing = candidates_by_digest.get(artifact.preview_digest)
        if existing is not None:
            existing.sources.append(source)
            if source.kind is CoverSourceKind.ABS:
                existing.incumbent = True
                existing.reference = reference
            omissions.append({
                "reason": "duplicate_preview",
                "reference": reference,
                "retained_reference": existing.reference,
            })
            continue
        candidates_by_digest[artifact.preview_digest] = _Candidate(
            reference=reference,
            digest=artifact.preview_digest,
            payload=payload,
            width=artifact.width,
            height=artifact.height,
            incumbent=source.kind is CoverSourceKind.ABS,
            sources=[source],
        )

    candidates = list(candidates_by_digest.values())
    oversize = [value for value in candidates if len(value.payload) > MAX_COVER_PREVIEW_BYTES]
    if any(value.incumbent for value in oversize):
        raise ValueError("cover packet incumbent exceeds the preview limit")
    for value in oversize:
        omissions.append({"reason": "preview_byte_limit", "reference": value.reference})
    candidates = [value for value in candidates if len(value.payload) <= MAX_COVER_PREVIEW_BYTES]

    incumbents = [value for value in candidates if value.incumbent]
    if len(incumbents) > 1:
        raise ValueError("cover packet has multiple distinct incumbents")
    selected = list(incumbents)
    selected_bytes = sum(len(value.payload) for value in selected)
    remaining = [value for value in candidates if not value.incumbent]
    source_counts = {kind.value: 0 for kind in CoverSourceKind}
    for value in selected:
        source_counts[value.source_kind] += 1
    while remaining and len(selected) < MAX_COVER_PREVIEWS:
        candidate = min(
            remaining,
            key=lambda value: (
                source_counts[value.source_kind],
                value.identity_rank,
                -(value.width * value.height),
                value.source_kind,
                value.reference,
            ),
        )
        remaining.remove(candidate)
        if selected_bytes + len(candidate.payload) > MAX_COVER_PACKET_BYTES:
            omissions.append({
                "reason": "aggregate_byte_limit", "reference": candidate.reference
            })
            continue
        selected.append(candidate)
        selected_bytes += len(candidate.payload)
        source_counts[candidate.source_kind] += 1
    for value in remaining:
        omissions.append({"reason": "shortlist_limit", "reference": value.reference})
    if not selected:
        raise ValueError("cover packet has no eligible previews")

    candidate_data: list[dict[str, object]] = []
    images: list[CoverPacketImage] = []
    for candidate in selected:
        provenance: list[dict[str, object]] = []
        for source in sorted(candidate.sources, key=lambda value: value.source_id):
            detail = source.provenance
            provenance.append({
                "context": [] if detail is None else [value.to_data() for value in detail.context],
                "designation": None if detail is None else detail.designation.value,
                "provider": None if detail is None else detail.provider,
                "source_kind": source.kind.value,
            })
        candidate_data.append({
            "height": candidate.height,
            "incumbent": candidate.incumbent,
            "media_type": "image/png",
            "preview_sha256": candidate.digest,
            "preview_variant": PREVIEW_VARIANT,
            "provenance": provenance,
            "reference": candidate.reference,
            "width": candidate.width,
        })
        images.append(CoverPacketImage(candidate.reference, candidate.digest, candidate.payload))

    omissions.sort(key=lambda value: (value["reference"], value["reason"]))
    packet = {
        "candidates": candidate_data,
        "omissions": omissions,
        "packet_reference": "cover_packet_" + hashlib.sha256(report.report_id.encode()).hexdigest(),
        "schema_version": "cover-review-packet-v1",
    }
    document = _canonical(packet)
    return PreparedCoverPacket(
        document=document,
        images=tuple(images),
        packet_digest=hashlib.sha256(document.encode("utf-8")).hexdigest(),
        omitted_count=len(omissions),
    )


__all__ = [
    "CoverPacketImage",
    "MAX_COVER_PACKET_BYTES",
    "MAX_COVER_PREVIEW_BYTES",
    "MAX_COVER_PREVIEWS",
    "PreparedCoverPacket",
    "prepare_cover_packet",
]
