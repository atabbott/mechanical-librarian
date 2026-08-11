"""Closed, report-local identity classification for retained cover previews.

This projection is intentionally separate from ``cover-review-packet-v1``.  It
uses no provider client, image interpretation, persistence, or policy input;
it merely binds the already-retained report evidence to the exact packet
candidate references.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    Comparison,
    IdentifierRelation,
    MetadataReport,
    ProviderCandidate,
    ProviderEvidenceKind,
    manifestation_publication_compatible,
)
from mechanical_librarian_workbench.cover_packet_semantics import (
    bind_cover_packet,
    canonical_packet_json,
    normalize_provenance,
)
from mechanical_librarian_workbench.cover_packets import cover_source_reference

SCHEMA_VERSION = "cover-identity-report-v1"
_OUTCOMES = frozenset({
    "verified_manifestation_match", "verified_work_match", "plausible_match",
    "unknown", "conflict",
})
_CLASSIFIER_RULES = (
    "packet-candidate-reference-and-provenance:exact-report-bound",
    "unbound-provider-identity:unknown",
    "edition-work-contradiction:conflict",
    "unrelated-identifier:conflict",
    "accepted-manifestation-with-compatible-publication:verified-manifestation",
    "accepted-manifestation-with-incompatible-publication:conflict",
    "accepted-or-associated-work-including-default-cover:verified-work",
    "retained-claim-and-date-evidence-comparisons:conservative-compatibility",
    "provider-identity-with-bibliographic-agreement:plausible",
    "provider-identity-with-ambiguous-or-insufficient-bibliography:unknown",
)
_CLASSIFIER_REFERENCE = "cover_identity_classifier_" + hashlib.sha256(
    canonical_packet_json(
        {"schema_version": SCHEMA_VERSION, "rules": _CLASSIFIER_RULES}
    ).encode("utf-8")
).hexdigest()

_IDENTITY_CLAIMS = frozenset({
    ClaimKind.TITLE,
    ClaimKind.CREATOR,
    ClaimKind.LANGUAGE,
    ClaimKind.PUBLISHER,
    ClaimKind.PUBLICATION_DATE,
})

def _opaque_evidence_reference(report_id: str, reference: str) -> str:
    return "cover_evidence_ref_" + hashlib.sha256(
        canonical_packet_json({"report_id": report_id, "reference": reference}).encode("utf-8")
    ).hexdigest()


def _work_target_reference(report: MetadataReport) -> str:
    return "work_target_ref_" + hashlib.sha256(
        canonical_packet_json(
            {"report_id": report.report_id, "target": report.target.fingerprint}
        ).encode("utf-8")
    ).hexdigest()


def _preview_reference(packet_reference: str, candidate: dict[str, object]) -> str:
    return "cover_preview_ref_" + hashlib.sha256(
        canonical_packet_json(
            {
                "candidate_reference": candidate["reference"],
                "packet_reference": packet_reference,
                "preview_sha256": candidate["preview_sha256"],
            }
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CoverIdentityReport:
    """Canonical public-safe classification for every packet candidate."""

    document: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.document.encode("utf-8")).hexdigest()


def classify_cover_identity(
    report_document: str,
    packet_document: str,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> CoverIdentityReport:
    """Classify every exact packet candidate from already retained evidence.

    A verified result requires an exact provider identity binding plus accepted
    (manifestation) or accepted/associated (work) identifier evidence.  Any
    retained edition/work contradiction wins deterministically.  The document
    contains opaque references and fixed reason codes only.
    """
    report = MetadataReport.from_json(report_document)
    bind_cover_packet(packet_document, image_payloads)
    packet = json.loads(packet_document)
    if canonical_packet_json(packet) != packet_document:
        raise ValueError("identity packet is not canonical")

    rows: list[dict[str, object]] = []
    for candidate in packet["candidates"]:
        if type(candidate) is not dict:
            raise ValueError("identity packet candidate is invalid")
        sources = _report_bound_candidate_sources(report, candidate)
        outcome, reasons, evidence = _classify_sources(report, sources)
        if outcome not in _OUTCOMES:
            raise ValueError("identity outcome is invalid")
        rows.append({
            "candidate_reference": candidate["reference"],
            "evidence_references": sorted({
                _opaque_evidence_reference(report.report_id, item)
                for item in evidence
            }),
            "outcome": outcome,
            "preview_reference": _preview_reference(
                packet["packet_reference"], candidate
            ),
            "reasons": sorted(set(reasons)),
        })
    body = {
        "candidates": rows,
        "classifier_reference": _CLASSIFIER_REFERENCE,
        "cover_packet_reference": packet["packet_reference"],
        "report_reference": report.report_id,
        "schema_version": SCHEMA_VERSION,
        "work_target_reference": _work_target_reference(report),
    }
    return CoverIdentityReport(canonical_packet_json(body))


def _report_bound_candidate_sources(
    report: MetadataReport, candidate: dict[str, object]
) -> tuple[CoverSource, ...]:
    sources = tuple(sorted(
        (
            source
            for source in report.cover_sources
            if source.state is CoverSourceState.RETRIEVED
            and source.artifact is not None
            and source.artifact.preview_digest == candidate["preview_sha256"]
        ),
        key=lambda source: source.source_id,
    ))
    if not sources:
        raise ValueError("identity candidate is not report-bound")
    references = tuple(
        cover_source_reference(report.report_id, source.source_id)
        for source in sources
    )
    incumbent_references = tuple(
        reference
        for source, reference in zip(sources, references, strict=True)
        if source.kind is CoverSourceKind.ABS
    )
    expected_reference = (
        incumbent_references[-1] if incumbent_references else references[0]
    )
    best = max(
        sources,
        key=lambda source: (
            source.artifact.width * source.artifact.height,
            source.artifact.width,
            source.artifact.height,
        ),
    )
    expected_provenance = normalize_provenance([
        {
            "context": (
                []
                if source.provenance is None
                else [value.to_data() for value in source.provenance.context]
            ),
            "designation": (
                None
                if source.provenance is None
                else source.provenance.designation.value
            ),
            "provider": (
                None if source.provenance is None else source.provenance.provider
            ),
            "source_kind": source.kind.value,
        }
        for source in sources
    ])
    if (
        candidate["reference"] != expected_reference
        or candidate["incumbent"] != bool(incumbent_references)
        or candidate["width"] != best.artifact.width
        or candidate["height"] != best.artifact.height
        or candidate["provenance"] != expected_provenance
        or any(
            source.artifact.preview_variant != candidate["preview_variant"]
            for source in sources
        )
    ):
        raise ValueError("identity candidate report binding is invalid")
    return sources


def _classify_sources(
    report: MetadataReport, sources: tuple[CoverSource, ...]
) -> tuple[str, list[str], list[str]]:
    provider_sources = tuple(source for source in sources if source.provenance is not None)
    if not provider_sources:
        return "unknown", ["no_provider_identity"], []
    matches = []
    for source in provider_sources:
        provenance = source.provenance
        assert provenance is not None
        for observation in report.providers:
            if observation.provider != provenance.provider:
                continue
            for candidate in observation.candidates:
                if candidate.work_identity != provenance.work_identity:
                    continue
                if (
                    provenance.edition_identity is not None
                    and candidate.identity != provenance.edition_identity
                ):
                    continue
                matches.append((source, observation.provider, candidate))
    if not matches:
        return "unknown", ["unbound_provider_identity"], []
    evidence: list[str] = []
    compatibility_reasons: set[str] = set()
    manifestation = False
    work = False
    for source, provider, candidate in matches:
        if any(
            item.kind
            is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
            for item in candidate.evidence
        ):
            return "conflict", ["edition_work_contradiction"], []

        bound = [
            item for item in report.identifier_evidence
            if item.provider == provider and item.candidate_identity == candidate.identity
            and item.work_identity == candidate.work_identity
        ]
        unrelated = [
            item.reference
            for item in bound
            if item.relation is IdentifierRelation.UNRELATED
        ]
        if unrelated:
            return "conflict", ["unrelated_identifier"], unrelated

        compatibility_reasons.add(_bibliographic_compatibility(candidate))

        accepted = [item.reference for item in bound if item.relation is IdentifierRelation.ACCEPTED]
        associated = [item.reference for item in bound if item.relation is IdentifierRelation.ASSOCIATED]
        evidence.extend(accepted)
        evidence.extend(associated)
        if (
            source.provenance is not None
            and source.provenance.designation is CoverDesignation.EXACT_EDITION
            and accepted
        ):
            if not manifestation_publication_compatible(
                candidate, report.local_claims
            ):
                return (
                    "conflict",
                    ["manifestation_publication_incompatible"],
                    accepted,
                )
            manifestation = True
        if (
            source.provenance is not None
            and source.provenance.designation in {
                CoverDesignation.EXACT_EDITION,
                CoverDesignation.DEFAULT,
                CoverDesignation.RELATED_EDITION,
                CoverDesignation.WORK,
            }
            and (accepted or associated)
        ):
            work = True
    if manifestation:
        return "verified_manifestation_match", ["accepted_manifestation_identifier"], evidence
    if work:
        return "verified_work_match", ["accepted_or_associated_work_identifier"], evidence
    if "retained_bibliographic_ambiguity" in compatibility_reasons:
        return "unknown", sorted(compatibility_reasons), []
    if "retained_bibliographic_agreement" not in compatibility_reasons:
        return "unknown", sorted(compatibility_reasons), []
    return (
        "plausible_match",
        [
            "provider_identity_without_explicit_identifier",
            *sorted(compatibility_reasons),
        ],
        [],
    )


def _bibliographic_compatibility(candidate: ProviderCandidate) -> str:
    outcomes: set[Comparison] = set()
    for claim in candidate.claims:
        if claim.kind in _IDENTITY_CLAIMS:
            outcomes.update(link.outcome for link in claim.links)
    for item in candidate.evidence:
        outcomes.update(link.outcome for link in item.links)
    if Comparison.DISAGREEMENT in outcomes:
        return "retained_bibliographic_ambiguity"
    if Comparison.AGREEMENT in outcomes:
        return "retained_bibliographic_agreement"
    return "retained_bibliographic_evidence_insufficient"


__all__ = ["CoverIdentityReport", "SCHEMA_VERSION", "classify_cover_identity"]
