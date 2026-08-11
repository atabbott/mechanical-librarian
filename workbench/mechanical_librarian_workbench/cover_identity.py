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
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    Comparison,
    ComparisonLink,
    IdentifierRelation,
    MetadataReport,
    PublicationDateRelation,
    ProviderCandidate,
    ProviderEvidenceKind,
    manifestation_publication_compatible,
)
from mechanical_librarian_workbench.cover_packet_semantics import (
    bind_cover_packet,
    canonical_packet_json,
)

SCHEMA_VERSION = "cover-identity-report-v1"
_OUTCOMES = frozenset({
    "verified_manifestation_match", "verified_work_match", "plausible_match",
    "unknown", "conflict",
})
_CLASSIFIER_REFERENCE = "cover_identity_classifier_" + hashlib.sha256(
    canonical_packet_json(
        {"schema_version": SCHEMA_VERSION, "rules": 2}
    ).encode("utf-8")
).hexdigest()

_IDENTITY_CLAIMS = frozenset({
    ClaimKind.TITLE,
    ClaimKind.CREATOR,
    ClaimKind.LANGUAGE,
    ClaimKind.PUBLISHER,
    ClaimKind.PUBLICATION_DATE,
})

_INCOMPATIBLE_DATES = frozenset({
    PublicationDateRelation.EVENT_INCOMPATIBLE,
    PublicationDateRelation.FINER_CONFLICT,
    PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
})


def _opaque_evidence_reference(report_id: str, reference: str) -> str:
    return "cover_evidence_ref_" + hashlib.sha256(
        canonical_packet_json({"report_id": report_id, "reference": reference}).encode("utf-8")
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

    sources_by_digest: dict[str, tuple[CoverSource, ...]] = {}
    for source in report.cover_sources:
        if source.state is CoverSourceState.RETRIEVED and source.artifact is not None:
            sources_by_digest.setdefault(source.artifact.preview_digest, ())
            sources_by_digest[source.artifact.preview_digest] += (source,)

    rows: list[dict[str, object]] = []
    for candidate in packet["candidates"]:
        if type(candidate) is not dict:
            raise ValueError("identity packet candidate is invalid")
        sources = sources_by_digest.get(candidate["preview_sha256"], ())
        if not sources:
            raise ValueError("identity candidate is not report-bound")
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
            "reasons": sorted(set(reasons)),
        })
    body = {
        "candidates": rows,
        "classifier_reference": _CLASSIFIER_REFERENCE,
        "cover_packet_reference": packet["packet_reference"],
        "report_reference": report.report_id,
        "schema_version": SCHEMA_VERSION,
    }
    return CoverIdentityReport(canonical_packet_json(body))


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

        comparison_reasons, comparison_evidence = _comparison_contradictions(
            candidate
        )
        if comparison_reasons:
            return "conflict", comparison_reasons, comparison_evidence

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
    return "plausible_match", ["provider_identity_without_explicit_identifier"], []


def _comparison_contradictions(
    candidate: ProviderCandidate,
) -> tuple[list[str], list[str]]:
    reasons: set[str] = set()
    evidence: set[str] = set()
    for claim in candidate.claims:
        if claim.kind not in _IDENTITY_CLAIMS:
            continue
        disagreements = tuple(
            link for link in claim.links if _is_contradictory_link(link)
        )
        if not disagreements:
            continue
        reasons.add(f"{claim.kind.value}_contradiction")
        evidence.add(claim.reference)
        evidence.update(
            link.local_reference
            for link in disagreements
            if link.local_reference is not None
        )
    for item in candidate.evidence:
        disagreements = tuple(
            link for link in item.links if _is_contradictory_link(link)
        )
        if not disagreements:
            continue
        reasons.add("publication_date_contradiction")
        evidence.update(
            link.local_reference
            for link in disagreements
            if link.local_reference is not None
        )
    return sorted(reasons), sorted(evidence)


def _is_contradictory_link(link: ComparisonLink) -> bool:
    return (
        link.outcome is Comparison.DISAGREEMENT
        or link.publication_date_relation in _INCOMPATIBLE_DATES
    )


__all__ = ["CoverIdentityReport", "SCHEMA_VERSION", "classify_cover_identity"]
