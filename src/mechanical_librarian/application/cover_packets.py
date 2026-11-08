"""Bound one report's exact cover previews for an isolated reviewer job."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.application.cover_packet_semantics import (
    MAX_COVER_PACKET_BYTES,
    MAX_COVER_PREVIEW_BYTES,
    MAX_COVER_PREVIEWS,
    bind_cover_packet,
    candidate_priority,
    canonical_packet_json,
    cover_transport_reference,
    designation_rank,
    normalize_cover_preview,
    normalize_provenance,
    packet_designation,
    validate_cover_preview,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.cover_evidence import (
    PREVIEW_VARIANT,
    CoverDesignation,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import work_route_cover_sources

SCHEMA_VERSION = "cover-review-packet-v2"
PRODUCER_REVISION = "cover-packet-producer-v1"


def cover_source_reference(report_id: str, source_id: str) -> str:
    digest = hashlib.sha256(
        canonical_packet_json({"report_id": report_id, "source_id": source_id}).encode(
            "utf-8"
        )
    ).hexdigest()
    return "cover_ref_" + digest


def _technical_quality(width: int, height: int) -> tuple[int, int, int]:
    return width * height, width, height


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
    source_digest: str
    payload: bytes
    width: int
    height: int
    preview_width: int
    preview_height: int
    incumbent: bool
    sources: list[CoverSource]
    references: list[str]

    @property
    def identity_rank(self) -> int:
        ranks = [
            designation_rank(packet_designation(source.provenance.designation))
            for source in self.sources
            if source.provenance is not None
        ]
        return min(ranks, default=4)

    @property
    def source_kinds(self) -> tuple[str, ...]:
        return tuple(sorted({source.kind.value for source in self.sources}))

    @property
    def listed_only(self) -> bool:
        """Whether every provider source of this preview is a listed edition."""
        return {
            source.provenance.designation
            for source in self.sources
            if source.provenance is not None
        } == {CoverDesignation.LISTED_EDITION}

    def priority(self, source_counts: dict[str, int]) -> tuple[int, int, int, str, str]:
        return candidate_priority(
            source_counts,
            self.source_kinds,
            self.identity_rank,
            self.width,
            self.height,
            self.reference,
        )


def prepare_cover_packet(
    report_document: str,
    read_preview: Callable[[str, str], bytes],
) -> PreparedCoverPacket:
    """Project exact report-bound previews into one deterministic private packet."""
    report = MetadataReport.from_json(report_document)
    sources = report.cover_sources
    if report.audio is not None and not admitted_audiobook_identity(report):
        # With no admitted recording, the gallery holds only the library's
        # own cover and the covers the work route offers (#1642).
        offered = work_route_cover_sources(report)
        if not offered:
            raise ValueError("cover packet requires recording admission")
        sources = tuple(
            source
            for source in sources
            if source.kind is CoverSourceKind.ABS or source in offered
        )
    retrieved = tuple(
        source for source in sources if source.state is CoverSourceState.RETRIEVED
    )
    if not retrieved:
        raise ValueError("cover packet has no retrieved previews")

    candidates_by_digest: dict[str, _Candidate] = {}
    # Every source not retrieved is named, as before #1642, so a pending
    # work cover says why the gallery is incomplete.
    omissions: list[dict[str, object]] = [
        {
            "reason": "source_" + source.state.value,
            "reference": cover_source_reference(report.report_id, source.source_id),
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
        if len(payload) <= MAX_COVER_PREVIEW_BYTES:
            normalized = normalize_cover_preview(
                payload, source_sha256=artifact.preview_digest
            )
            transport_payload = normalized.payload
            transport_digest = normalized.digest
            preview_width, preview_height = normalized.width, normalized.height
        else:
            # Oversized groups are omitted before candidate emission; decoding
            # them would widen work beyond the existing byte gate.
            preview_width = preview_height = 1
            transport_payload = payload
            transport_digest = artifact.preview_digest
        reference = cover_source_reference(report.report_id, source.source_id)
        existing = candidates_by_digest.get(transport_digest)
        if existing is not None:
            existing.sources.append(source)
            existing.references.append(reference)
            if source.kind is CoverSourceKind.ABS:
                existing.incumbent = True
                existing.reference = reference
                existing.source_digest = artifact.preview_digest
                existing.width = artifact.width
                existing.height = artifact.height
            elif not existing.incumbent and _technical_quality(
                artifact.width, artifact.height
            ) > _technical_quality(existing.width, existing.height):
                existing.width = artifact.width
                existing.height = artifact.height
            continue
        candidates_by_digest[transport_digest] = _Candidate(
            reference=reference,
            digest=transport_digest,
            source_digest=artifact.preview_digest,
            payload=transport_payload,
            width=artifact.width,
            height=artifact.height,
            preview_width=preview_width,
            preview_height=preview_height,
            incumbent=source.kind is CoverSourceKind.ABS,
            sources=[source],
            references=[reference],
        )

    return _finalize_cover_packet(list(candidates_by_digest.values()), omissions)


def _omit_group(
    omissions: list[dict[str, object]],
    candidate: _Candidate,
    reason: str,
) -> None:
    for reference in sorted(candidate.references):
        omissions.append(
            {
                "preview_bytes": len(candidate.payload),
                "reason": reason,
                "reference": reference,
            }
        )


def _packet_order(selected: list[_Candidate]) -> list[_Candidate]:
    """The kept candidates in the order the packet validator recomputes."""
    ordered = [value for value in selected if value.incumbent]
    remaining = [value for value in selected if not value.incumbent]
    source_counts = {kind.value: 0 for kind in CoverSourceKind}
    for value in ordered:
        for source_kind in value.source_kinds:
            source_counts[source_kind] += 1
    while remaining:
        candidate = min(remaining, key=lambda value: value.priority(source_counts))
        remaining.remove(candidate)
        ordered.append(candidate)
        for source_kind in candidate.source_kinds:
            source_counts[source_kind] += 1
    return ordered


def _finalize_cover_packet(
    candidates: list[_Candidate],
    omissions: list[dict[str, object]],
) -> PreparedCoverPacket:
    """Select groups, then bind each duplicate only to final retained state."""

    for candidate in candidates:
        if len(candidate.payload) <= MAX_COVER_PREVIEW_BYTES:
            if hashlib.sha256(candidate.payload).hexdigest() != candidate.digest:
                raise ValueError("cover packet transport digest changed")
            validate_cover_preview(
                candidate.payload,
                expected_width=candidate.preview_width,
                expected_height=candidate.preview_height,
            )

    oversize = [
        value for value in candidates if len(value.payload) > MAX_COVER_PREVIEW_BYTES
    ]
    if any(value.incumbent for value in oversize):
        raise ValueError("cover packet incumbent exceeds the preview limit")
    for value in oversize:
        _omit_group(omissions, value, "preview_byte_limit")
    candidates = [
        value for value in candidates if len(value.payload) <= MAX_COVER_PREVIEW_BYTES
    ]

    incumbents = [value for value in candidates if value.incumbent]
    if len(incumbents) > 1:
        raise ValueError("cover packet has multiple distinct incumbents")
    selected = list(incumbents)
    selected_bytes = sum(len(value.payload) for value in selected)
    remaining = [value for value in candidates if not value.incumbent]
    source_counts = {kind.value: 0 for kind in CoverSourceKind}
    for value in selected:
        for source_kind in value.source_kinds:
            source_counts[source_kind] += 1
    # A listed edition can never be confirmed, so every other cover is kept
    # before one (#1778), and the kept ones are then put in packet order.
    while remaining and len(selected) < MAX_COVER_PREVIEWS:
        candidate = min(
            remaining,
            key=lambda value: (value.listed_only, value.priority(source_counts)),
        )
        remaining.remove(candidate)
        if selected_bytes + len(candidate.payload) > MAX_COVER_PACKET_BYTES:
            _omit_group(omissions, candidate, "aggregate_byte_limit")
            continue
        selected.append(candidate)
        selected_bytes += len(candidate.payload)
        for source_kind in candidate.source_kinds:
            source_counts[source_kind] += 1
    for value in remaining:
        _omit_group(omissions, value, "shortlist_limit")
    if not selected:
        raise ValueError("cover packet has no eligible previews")
    selected = _packet_order(selected)

    candidate_data: list[dict[str, object]] = []
    images: list[CoverPacketImage] = []
    for candidate in selected:
        source_digests = {
            reference: source.artifact.preview_digest
            for source, reference in zip(
                candidate.sources, candidate.references, strict=True
            )
            if source.artifact is not None
        }
        for reference in sorted(set(candidate.references) - {candidate.reference}):
            omissions.append(
                {
                    "preview_sha256": source_digests[reference],
                    "reason": "duplicate_preview",
                    "reference": reference,
                    "retained_reference": candidate.reference,
                }
            )
        provenance: list[dict[str, object]] = []
        for source in candidate.sources:
            detail = source.provenance
            provenance.append(
                {
                    "context": []
                    if detail is None
                    else [value.to_data() for value in detail.context],
                    "designation": (
                        None
                        if detail is None
                        else packet_designation(detail.designation)
                    ),
                    "provider": None if detail is None else detail.provider,
                    "source_kind": source.kind.value,
                }
            )
        provenance = normalize_provenance(provenance)
        source_bindings = sorted(
            {
                (
                    reference,
                    source_digests[reference],
                )
                for source, reference in zip(
                    candidate.sources, candidate.references, strict=True
                )
                if source.artifact is not None
            }
        )
        source_binding_data = [
            {"preview_sha256": digest, "reference": reference}
            for reference, digest in source_bindings
        ]
        transport_reference = cover_transport_reference(
            candidate.reference, source_binding_data, candidate.digest
        )
        candidate_data.append(
            {
                "height": candidate.height,
                "incumbent": candidate.incumbent,
                "media_type": "image/png",
                "preview_bytes": len(candidate.payload),
                "preview_height": candidate.preview_height,
                "preview_sha256": candidate.source_digest,
                "preview_variant": PREVIEW_VARIANT,
                "preview_width": candidate.preview_width,
                "provenance": provenance,
                "reference": candidate.reference,
                "source_bindings": source_binding_data,
                "transport_reference": transport_reference,
                "transport_sha256": candidate.digest,
                "width": candidate.width,
            }
        )
        images.append(
            CoverPacketImage(transport_reference, candidate.digest, candidate.payload)
        )

    omissions.sort(key=lambda value: (value["reference"], value["reason"]))
    packet_body = {
        "candidates": candidate_data,
        "omissions": omissions,
        "schema_version": SCHEMA_VERSION,
    }
    packet = {
        **packet_body,
        "packet_reference": "cover_packet_"
        + hashlib.sha256(canonical_packet_json(packet_body).encode()).hexdigest(),
    }
    document = canonical_packet_json(packet)
    bound_images = tuple(
        (value.reference, value.digest, value.payload) for value in images
    )
    bind_cover_packet(document, bound_images)
    return PreparedCoverPacket(
        document=document,
        images=tuple(images),
        packet_digest=hashlib.sha256(document.encode("utf-8")).hexdigest(),
        omitted_count=len(omissions),
    )


__all__ = [
    "SCHEMA_VERSION",
    "CoverPacketImage",
    "MAX_COVER_PACKET_BYTES",
    "MAX_COVER_PREVIEW_BYTES",
    "MAX_COVER_PREVIEWS",
    "PreparedCoverPacket",
    "prepare_cover_packet",
]
