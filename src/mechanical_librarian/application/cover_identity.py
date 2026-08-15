"""Closed, report-local identity classification for retained cover previews.

This projection is intentionally separate from ``cover-review-packet-v2``.  It
uses no provider client, semantic image interpretation, persistence, or policy
input; source pixels only authenticate the already-retained report evidence to
the exact packet candidate transport.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.application.cover_packet_semantics import (
    bind_cover_packet,
    canonical_packet_json,
    normalize_cover_preview,
    normalize_provenance,
)
from mechanical_librarian.application.cover_packets import cover_source_reference
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
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderEvidenceKind,
    ProviderQueryKind,
    creator_comparison_key,
    manifestation_publication_compatible,
)

SCHEMA_VERSION = "cover-identity-report-v1"
_OUTCOMES = frozenset({
    "verified_manifestation_match", "verified_work_match", "plausible_match",
    "unknown", "conflict",
})
_CLASSIFIER_RULES = (
    "packet-candidate-reference-and-provenance:exact-report-bound",
    "packet-source-normalization:exact-transport-bound",
    "report-bound-epub-cover:verified-manifestation",
    "unbound-provider-identity:unknown",
    "edition-work-contradiction:conflict",
    "unrelated-identifier:conflict",
    "accepted-manifestation-with-compatible-publication:verified-manifestation",
    "accepted-manifestation-with-incompatible-publication:conflict",
    "accepted-or-associated-work-including-default-cover:verified-work",
    "provider-isbn-lane-title-agreement-without-unopposed-creator-or-work-contradiction:plausible",
    "provider-isbn-lane-title-insufficient-or-unopposed-creator-disagreement:unknown",
    "legacy-discovery-without-lane-candidate-mappings:aggregate-fallback",
)
CLASSIFIER_REFERENCE = "cover_identity_classifier_" + hashlib.sha256(
    canonical_packet_json(
        {"schema_version": SCHEMA_VERSION, "rules": _CLASSIFIER_RULES}
    ).encode("utf-8")
).hexdigest()
# Retained for the frozen 0.15.0 contract tests and persisted policy reader.
_CLASSIFIER_REFERENCE = CLASSIFIER_REFERENCE

_CREATOR_OR_CONTRIBUTOR_CLAIMS = frozenset({
    ClaimKind.CREATOR,
    ClaimKind.CONTRIBUTOR,
})
_CREATOR_OR_CONTRIBUTOR_EVIDENCE = frozenset({
    ProviderEvidenceKind.AUTHOR_IDENTITY,
    ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
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
    *,
    read_preview: Callable[[str, str], bytes],
) -> CoverIdentityReport:
    """Classify every exact packet candidate from already retained evidence.

    A verified provider result requires an exact provider identity binding plus
    accepted (manifestation) or accepted/associated (work) identifier evidence.
    An exact report-bound EPUB cover verifies its source manifestation. Any
    retained edition/work contradiction wins deterministically for provider
    evidence. The document contains opaque references and fixed reason codes
    only.
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
        sources = _report_bound_candidate_sources(report, candidate, read_preview)
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
        "classifier_reference": CLASSIFIER_REFERENCE,
        "cover_packet_reference": packet["packet_reference"],
        "report_reference": report.report_id,
        "schema_version": SCHEMA_VERSION,
        "work_target_reference": _work_target_reference(report),
    }
    return CoverIdentityReport(canonical_packet_json(body))


def _report_bound_candidate_sources(
    report: MetadataReport,
    candidate: dict[str, object],
    read_preview: Callable[[str, str], bytes],
) -> tuple[CoverSource, ...]:
    bindings = {
        (value["reference"], value["preview_sha256"])
        for value in candidate["source_bindings"]
    }
    sources = tuple(sorted(
        (
            source
            for source in report.cover_sources
            if source.state is CoverSourceState.RETRIEVED
            and source.artifact is not None
            and (
                cover_source_reference(report.report_id, source.source_id),
                source.artifact.preview_digest,
            ) in bindings
        ),
        key=lambda source: source.source_id,
    ))
    if not sources:
        raise ValueError("identity candidate report binding is invalid")
    references = tuple(
        cover_source_reference(report.report_id, source.source_id)
        for source in sources
    )
    if bindings != {
        (reference, source.artifact.preview_digest)
        for source, reference in zip(sources, references, strict=True)
    }:
        raise ValueError("identity candidate source binding is invalid")
    for source in sources:
        artifact = source.artifact
        assert artifact is not None
        normalized = normalize_cover_preview(
            read_preview(artifact.artifact_id, artifact.preview_variant),
            source_sha256=artifact.preview_digest,
        )
        if normalized.digest != candidate["transport_sha256"]:
            raise ValueError("identity candidate transport binding is invalid")
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
        or candidate["preview_sha256"] != dict(bindings)[expected_reference]
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
    if any(source.kind is CoverSourceKind.EPUB for source in sources):
        return "verified_manifestation_match", ["source_bound_epub_cover"], []
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
                matches.append(
                    (
                        source,
                        observation.provider,
                        candidate,
                        _is_isbn_discovery_candidate(observation, candidate),
                    )
                )
    if not matches:
        return "unknown", ["unbound_provider_identity"], []
    evidence: list[str] = []
    title_agreement = False
    creator_or_contributor_agreement = False
    creator_or_contributor_disagreement = False
    manifestation = False
    work = False
    for source, provider, candidate, isbn_discovery_supported in matches:
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

        if isbn_discovery_supported:
            (
                candidate_title_agreement,
                candidate_creator_or_contributor_agreement,
                candidate_creator_or_contributor_disagreement,
            ) = _plausible_match_evidence(
                candidate,
                getattr(report, "local_claims", ()),
            )
            title_agreement = title_agreement or candidate_title_agreement
            creator_or_contributor_agreement = (
                creator_or_contributor_agreement
                or candidate_creator_or_contributor_agreement
            )
            creator_or_contributor_disagreement = (
                creator_or_contributor_disagreement
                or candidate_creator_or_contributor_disagreement
            )

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
    if (
        creator_or_contributor_disagreement
        and not creator_or_contributor_agreement
    ):
        return "unknown", ["retained_creator_or_contributor_disagreement"], []
    if not title_agreement:
        return "unknown", ["retained_title_evidence_insufficient"], []
    return (
        "plausible_match",
        [
            "provider_identity_without_explicit_identifier",
            "retained_title_agreement",
        ],
        [],
    )


def _is_isbn_discovery_candidate(
    observation: object,
    candidate: ProviderCandidate,
) -> bool:
    lanes = tuple(getattr(observation, "lanes", ()))
    lane_candidates = tuple(
        lane_candidate
        for lane in lanes
        for lane_candidate in getattr(lane, "candidates", ())
    )
    if not lane_candidates:
        return getattr(
            observation,
            "query_kind",
            ProviderQueryKind.DISCOVERY,
        ) is ProviderQueryKind.DISCOVERY
    return any(
        lane.kind is ProviderQueryKind.ISBN
        and lane_candidate.edition_identity == candidate.identity
        and lane_candidate.work_identity == candidate.work_identity
        for lane in lanes
        for lane_candidate in lane.candidates
    )


def _plausible_match_evidence(
    candidate: ProviderCandidate,
    local_claims: tuple[LocalClaim, ...] | tuple[object, ...],
) -> tuple[bool, bool, bool]:
    title_agreement = False
    creator_or_contributor_agreement = False
    creator_or_contributor_disagreement = False
    for claim in candidate.claims:
        outcomes = {link.outcome for link in claim.links}
        if claim.kind is ClaimKind.TITLE:
            title_agreement = title_agreement or Comparison.AGREEMENT in outcomes
        if claim.kind in _CREATOR_OR_CONTRIBUTOR_CLAIMS:
            creator_or_contributor_agreement = (
                creator_or_contributor_agreement
                or Comparison.AGREEMENT in outcomes
            )
            creator_or_contributor_disagreement = (
                creator_or_contributor_disagreement
                or Comparison.DISAGREEMENT in outcomes
            )
    for item in candidate.evidence:
        if item.kind not in _CREATOR_OR_CONTRIBUTOR_EVIDENCE:
            continue
        outcomes = {link.outcome for link in item.links}
        creator_or_contributor_agreement = (
            creator_or_contributor_agreement
            or Comparison.AGREEMENT in outcomes
        )
        creator_or_contributor_disagreement = (
            creator_or_contributor_disagreement
            or Comparison.DISAGREEMENT in outcomes
        )
    retained_creator_or_contributor_values = {
        creator_comparison_key(claim.value)
        for claim in candidate.claims
        if (
            getattr(claim, "kind", None) in _CREATOR_OR_CONTRIBUTOR_CLAIMS
            and getattr(claim, "value", None) is not None
        )
    }
    local_creator_or_contributor_values = {
        creator_comparison_key(claim.observed)
        for claim in local_claims
        if (
            getattr(claim, "kind", None) in _CREATOR_OR_CONTRIBUTOR_CLAIMS
            and getattr(claim, "canonical", None) is not None
        )
    }
    creator_or_contributor_agreement = (
        creator_or_contributor_agreement
        or bool(
            retained_creator_or_contributor_values
            & local_creator_or_contributor_values
        )
    )
    return (
        title_agreement,
        creator_or_contributor_agreement,
        creator_or_contributor_disagreement,
    )


__all__ = [
    "CLASSIFIER_REFERENCE",
    "CoverIdentityReport",
    "SCHEMA_VERSION",
    "classify_cover_identity",
]
