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
from typing import cast

from mechanical_librarian.application.cover_packet_semantics import (
    bind_cover_packet,
    canonical_packet_json,
    normalize_cover_preview,
    normalize_provenance,
    packet_designation,
)
from mechanical_librarian.application.cover_packets import cover_source_reference
from mechanical_librarian.domain.audiobook_corroboration import (
    CorroborationOutcome,
    cover_hash_agreement,
)
from mechanical_librarian.domain.cover_evidence import (
    AUDIOBOOK_COVER_SOURCE_KINDS,
    CoverArtifact,
    CoverDesignation,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    IdentifierEvidence,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderEvidenceKind,
    ProviderQueryKind,
    TargetDestination,
    TargetFacts,
    TargetFieldState,
    creator_comparison_key,
    manifestation_publication_compatible,
)
from mechanical_librarian.domain.proposal_set import (
    provider_candidate_represents_target_recording,
)
from mechanical_librarian.domain.work_identity_levels import (
    WorkIdentityLevel,
    level_admission,
    omnibus_against_volume,
)

SCHEMA_VERSION = "cover-identity-report-v1"
_OUTCOMES = frozenset(
    {
        "verified_manifestation_match",
        "verified_work_match",
        "plausible_match",
        "unknown",
        "conflict",
    }
)
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
    "audiobook-cross-authority-cover-perceptual-hash:work-scope-corroboration",
    "source-bound-retained-recording-admission:verified-work",
    "listed-edition-work-identity-binding:unknown-with-level-shadow",
)
CLASSIFIER_REFERENCE = (
    "cover_identity_classifier_"
    + hashlib.sha256(
        canonical_packet_json(
            {"schema_version": SCHEMA_VERSION, "rules": _CLASSIFIER_RULES}
        ).encode("utf-8")
    ).hexdigest()
)
# Retained for the frozen 0.15.0 contract tests and persisted policy reader.
_CLASSIFIER_REFERENCE = CLASSIFIER_REFERENCE

_CREATOR_OR_CONTRIBUTOR_CLAIMS = frozenset(
    {
        ClaimKind.CREATOR,
        ClaimKind.CONTRIBUTOR,
    }
)
_CREATOR_OR_CONTRIBUTOR_EVIDENCE = frozenset(
    {
        ProviderEvidenceKind.AUTHOR_IDENTITY,
        ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
    }
)


def _opaque_evidence_reference(report_id: str, reference: str) -> str:
    return (
        "cover_evidence_ref_"
        + hashlib.sha256(
            canonical_packet_json(
                {"report_id": report_id, "reference": reference}
            ).encode("utf-8")
        ).hexdigest()
    )


def _work_target_reference(report: MetadataReport) -> str:
    return (
        "work_target_ref_"
        + hashlib.sha256(
            canonical_packet_json(
                {"report_id": report.report_id, "target": report.target.fingerprint}
            ).encode("utf-8")
        ).hexdigest()
    )


def _preview_reference(packet_reference: str, candidate: dict[str, object]) -> str:
    return (
        "cover_preview_ref_"
        + hashlib.sha256(
            canonical_packet_json(
                {
                    "candidate_reference": candidate["reference"],
                    "packet_reference": packet_reference,
                    "preview_sha256": candidate["preview_sha256"],
                }
            ).encode("utf-8")
        ).hexdigest()
    )


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
    corroboration_hashes: dict[str, tuple[tuple[str, str], ...]] = {}
    for candidate in packet["candidates"]:
        if type(candidate) is not dict:
            raise ValueError("identity packet candidate is invalid")
        sources = _report_bound_candidate_sources(report, candidate, read_preview)
        outcome, reasons, evidence = _classify_sources(report, sources)
        if outcome not in _OUTCOMES:
            raise ValueError("identity outcome is invalid")
        corroboration_hashes[cast("str", candidate["reference"])] = (
            _audiobook_cover_hashes(sources)
        )
        rows.append(
            {
                "candidate_reference": candidate["reference"],
                "evidence_references": sorted(
                    {
                        _opaque_evidence_reference(report.report_id, item)
                        for item in evidence
                    }
                ),
                "outcome": outcome,
                "preview_reference": _preview_reference(
                    packet["packet_reference"], candidate
                ),
                "reasons": sorted(set(reasons)),
            }
        )
    corroboration = _cover_corroboration_reasons(report, corroboration_hashes)
    for row in rows:
        additional = corroboration.get(cast("str", row["candidate_reference"]))
        if additional:
            row["reasons"] = sorted(set(cast("list[str]", row["reasons"])) | additional)
    body = {
        "candidates": rows,
        "classifier_reference": CLASSIFIER_REFERENCE,
        "cover_packet_reference": packet["packet_reference"],
        "report_reference": report.report_id,
        "schema_version": SCHEMA_VERSION,
        "work_target_reference": _work_target_reference(report),
    }
    return CoverIdentityReport(canonical_packet_json(body))


def _audiobook_cover_hashes(
    sources: tuple[CoverSource, ...],
) -> tuple[tuple[str, str], ...]:
    """Read one candidate's audiobook provider covers as (provider, hash)."""
    return tuple(
        sorted(
            {
                (source.provenance.provider, source.artifact.perceptual_hash)
                for source in sources
                if source.kind in AUDIOBOOK_COVER_SOURCE_KINDS
                and source.provenance is not None
                and source.artifact is not None
                and source.artifact.perceptual_hash is not None
            }
        )
    )


def _cover_corroboration_reasons(
    report: MetadataReport,
    hashes: dict[str, tuple[tuple[str, str], ...]],
) -> dict[str, set[str]]:
    """Apply ADR-0064's cover rule across two independent audiobook authorities.

    An audiobook's identity is rarely settled by an identifier, so what two
    authorities picture for one recording is evidence about it. The rule is
    valid at work scope only, and only across *different* providers: two rows
    from one authority are one statement, not two, and the rule is asked
    nothing it could answer with itself.

    Only audiobook cover kinds reach here, so an ebook report's identity
    document is unchanged: it never forms a pair.
    """
    if report.file.format not in AUDIO_FILE_FORMATS:
        return {}
    reasons: dict[str, set[str]] = {}
    references = sorted(hashes)
    for index, left in enumerate(references):
        for right in references[index + 1 :]:
            for left_provider, left_hash in hashes[left]:
                for right_provider, right_hash in hashes[right]:
                    if left_provider == right_provider:
                        continue
                    signal = cover_hash_agreement(
                        left_hash, right_hash, ClaimScope.WORK
                    )
                    if signal.outcome is CorroborationOutcome.NOT_COMPARABLE:
                        continue
                    reasons.setdefault(left, set()).update(signal.reasons)
                    reasons.setdefault(right, set()).update(signal.reasons)
    return reasons


def _report_bound_candidate_sources(
    report: MetadataReport,
    candidate: dict[str, object],
    read_preview: Callable[[str, str], bytes],
) -> tuple[CoverSource, ...]:
    # ``bind_cover_packet`` admitted the candidate, so its source bindings are
    # a list of validated ``{"reference", "preview_sha256"}`` mappings, and
    # every source retained below has a retrieved artifact.
    bindings = {
        (value["reference"], value["preview_sha256"])
        for value in cast("list[dict[str, object]]", candidate["source_bindings"])
    }
    sources = tuple(
        sorted(
            (
                source
                for source in report.cover_sources
                if source.state is CoverSourceState.RETRIEVED
                and source.artifact is not None
                and (
                    cover_source_reference(report.report_id, source.source_id),
                    source.artifact.preview_digest,
                )
                in bindings
            ),
            key=lambda source: source.source_id,
        )
    )
    if not sources:
        raise ValueError("identity candidate report binding is invalid")
    references = tuple(
        cover_source_reference(report.report_id, source.source_id) for source in sources
    )
    if bindings != {
        (reference, cast("CoverArtifact", source.artifact).preview_digest)
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
    best = next(
        (
            source
            for source, reference in zip(sources, references, strict=True)
            if reference == expected_reference
        ),
        None,
    )
    if best is None:
        raise ValueError("identity candidate report binding is invalid")
    if not incumbent_references:
        best = max(
            sources,
            key=lambda source: (
                cast("CoverArtifact", source.artifact).width
                * cast("CoverArtifact", source.artifact).height,
                cast("CoverArtifact", source.artifact).width,
                cast("CoverArtifact", source.artifact).height,
            ),
        )
    expected_provenance = normalize_provenance(
        [
            {
                "context": (
                    []
                    if source.provenance is None
                    else [value.to_data() for value in source.provenance.context]
                ),
                "designation": (
                    None
                    if source.provenance is None
                    else packet_designation(source.provenance.designation)
                ),
                "provider": (
                    None if source.provenance is None else source.provenance.provider
                ),
                "source_kind": source.kind.value,
            }
            for source in sources
        ]
    )
    if (
        candidate["reference"] != expected_reference
        or candidate["preview_sha256"] != dict(bindings)[expected_reference]
        or candidate["incumbent"] != bool(incumbent_references)
        or candidate["width"] != cast("CoverArtifact", best.artifact).width
        or candidate["height"] != cast("CoverArtifact", best.artifact).height
        or candidate["provenance"] != expected_provenance
        or any(
            cast("CoverArtifact", source.artifact).preview_variant
            != candidate["preview_variant"]
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
    provider_sources = tuple(
        source for source in sources if source.provenance is not None
    )
    if not provider_sources:
        return "unknown", ["no_provider_identity"], []
    matches = []
    for source in provider_sources:
        provenance = source.provenance
        assert provenance is not None
        # A listed edition names an edition that is never a candidate, so it
        # binds by its book's work identity alone (#1380).
        listed = provenance.designation is CoverDesignation.LISTED_EDITION
        for observation in report.providers:
            if observation.provider != provenance.provider:
                continue
            for candidate in observation.candidates:
                if candidate.work_identity != provenance.work_identity:
                    continue
                if (
                    not listed
                    and provenance.edition_identity is not None
                    and candidate.identity != provenance.edition_identity
                ):
                    continue
                matches.append(
                    (
                        source,
                        observation.provider,
                        candidate,
                        _is_isbn_discovery_candidate(observation, candidate),
                        provider_candidate_represents_target_recording(
                            report, observation, candidate
                        ),
                    )
                )
    if not matches:
        return "unknown", ["unbound_provider_identity"], []
    if all(_listed(match[0]) for match in matches):
        return _classify_listed_edition(report, matches)
    evidence: list[str] = []
    title_agreement = False
    creator_or_contributor_agreement = False
    creator_or_contributor_disagreement = False
    manifestation = False
    work = False
    recording = False
    for (
        source,
        provider,
        candidate,
        isbn_discovery_supported,
        recording_supported,
    ) in matches:
        bound = _bound_identifiers(report, provider, candidate)
        conflict = _retained_conflict(candidate, bound)
        if conflict is not None:
            return conflict
        if _listed(source):
            # Beside another bound source, a listed edition adds only its
            # conflicts: it never raises the candidate's outcome.
            continue

        if recording_supported and source.kind in AUDIOBOOK_COVER_SOURCE_KINDS:
            recording = True
            evidence.extend(
                claim.reference
                for claim in candidate.claims
                if claim.kind is ClaimKind.ASIN
            )
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

        accepted = [
            item.reference
            for item in bound
            if item.relation is IdentifierRelation.ACCEPTED
        ]
        associated = [
            item.reference
            for item in bound
            if item.relation is IdentifierRelation.ASSOCIATED
        ]
        evidence.extend(accepted)
        evidence.extend(associated)
        if (
            source.provenance is not None
            and source.provenance.designation is CoverDesignation.EXACT_EDITION
            and accepted
        ):
            if not manifestation_publication_compatible(candidate, report.local_claims):
                return (
                    "conflict",
                    ["manifestation_publication_incompatible"],
                    accepted,
                )
            manifestation = True
        if (
            source.provenance is not None
            and source.provenance.designation
            in {
                CoverDesignation.EXACT_EDITION,
                CoverDesignation.DEFAULT,
                CoverDesignation.RELATED_EDITION,
                CoverDesignation.WORK,
            }
            and (accepted or associated)
        ):
            work = True
    if manifestation:
        return (
            "verified_manifestation_match",
            ["accepted_manifestation_identifier"],
            evidence,
        )
    if recording:
        return "verified_work_match", ["retained_recording_admission"], evidence
    if work:
        return (
            "verified_work_match",
            ["accepted_or_associated_work_identifier"],
            evidence,
        )
    if creator_or_contributor_disagreement and not creator_or_contributor_agreement:
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


def _listed(source: CoverSource) -> bool:
    return (
        source.provenance is not None
        and source.provenance.designation is CoverDesignation.LISTED_EDITION
    )


def _bound_identifiers(
    report: MetadataReport, provider: str, candidate: ProviderCandidate
) -> list[IdentifierEvidence]:
    return [
        item
        for item in report.identifier_evidence
        if item.provider == provider
        and item.candidate_identity == candidate.identity
        and item.work_identity == candidate.work_identity
    ]


def _retained_conflict(
    candidate: ProviderCandidate, bound: list[IdentifierEvidence]
) -> tuple[str, list[str], list[str]] | None:
    """The retained contradiction that makes a bound candidate a conflict."""
    if any(
        item.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
        for item in candidate.evidence
    ):
        return "conflict", ["edition_work_contradiction"], []
    unrelated = [
        item.reference
        for item in bound
        if item.relation is IdentifierRelation.UNRELATED
    ]
    if unrelated:
        return "conflict", ["unrelated_identifier"], unrelated
    return None


def _classify_listed_edition(
    report: MetadataReport,
    matches: list[tuple[CoverSource, str, ProviderCandidate, bool, bool]],
) -> tuple[str, list[str], list[str]]:
    """Classify a candidate whose only bound sources are listed editions.

    The outcome is always `unknown` until #1355's activation reads it: the
    reasons record the strictest ADR-0096 level that `title_and_creator`, the
    widest, admits, so any configured level can be compared with it later. The
    target's stored authors are compared, never a query spelling, so a creator
    form only the query used never reaches the predicate. Any conflict, then
    any creator contradiction, wins over a level.
    """
    target_title = report.target.current_title
    target_creators = _target_authors(report.target)
    local_claims = getattr(report, "local_claims", ())
    contradicted = False
    levels: set[WorkIdentityLevel] = set()
    for _source, provider, candidate, _isbn, _recording in matches:
        conflict = _retained_conflict(
            candidate, _bound_identifiers(report, provider, candidate)
        )
        if conflict is not None:
            return conflict
        _title, agreement, disagreement = _plausible_match_evidence(
            candidate, local_claims
        )
        candidate_contradicted = disagreement and not agreement
        contradicted = contradicted or candidate_contradicted
        record_title = _record_title(candidate)
        admission = level_admission(
            WorkIdentityLevel.TITLE_AND_CREATOR,
            target_title,
            target_creators,
            record_title,
            _record_creators(candidate),
            omnibus_against_volume=(
                _omnibus(record_title, target_title)
                or _omnibus(target_title, record_title)
            ),
            contradicted=candidate_contradicted,
        )
        if admission is not None:
            levels.add(admission.level)
    if contradicted:
        return "unknown", ["retained_creator_or_contributor_disagreement"], []
    if not levels:
        return "unknown", ["listed_edition_level_refused"], []
    level = (
        WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
        if WorkIdentityLevel.TITLE_AND_CREATOR_EXACT in levels
        else WorkIdentityLevel.TITLE_AND_CREATOR
    )
    return (
        "unknown",
        ["listed_edition_level_shadow", f"work_identity:{level.value}"],
        [],
    )


def _target_authors(target: TargetFacts) -> tuple[str, ...]:
    """The target's AUTHORS value as stored, in order; empty when not a value."""
    for field in target.fields:
        if field.destination is TargetDestination.AUTHORS:
            if field.state is TargetFieldState.VALUE and type(field.value) is tuple:
                return tuple(value for value in field.value if type(value) is str)
            return ()
    return ()


def _work_claim_values(candidate: ProviderCandidate, kind: ClaimKind) -> list[str]:
    return [
        claim.value
        for claim in sorted(candidate.claims, key=lambda claim: claim.raw_order)
        if claim.kind is kind
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and type(claim.value) is str
    ]


def _record_title(candidate: ProviderCandidate) -> str | None:
    titles = _work_claim_values(candidate, ClaimKind.TITLE)
    return titles[0] if len(titles) == 1 else None


def _record_creators(candidate: ProviderCandidate) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_work_claim_values(candidate, ClaimKind.CREATOR)))


def _omnibus(collection_title: str | None, volume_title: str | None) -> bool:
    if collection_title is None or volume_title is None:
        return False
    return omnibus_against_volume(collection_title, volume_title)


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
        return (
            getattr(
                observation,
                "query_kind",
                ProviderQueryKind.DISCOVERY,
            )
            is ProviderQueryKind.DISCOVERY
        )
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
                creator_or_contributor_agreement or Comparison.AGREEMENT in outcomes
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
            creator_or_contributor_agreement or Comparison.AGREEMENT in outcomes
        )
        creator_or_contributor_disagreement = (
            creator_or_contributor_disagreement or Comparison.DISAGREEMENT in outcomes
        )
    # Both comprehensions filter on the attribute they then read, so the
    # retained claims carry a value and the local claims are ``LocalClaim``.
    retained_creator_or_contributor_values = {
        creator_comparison_key(cast("str", claim.value))
        for claim in candidate.claims
        if (
            getattr(claim, "kind", None) in _CREATOR_OR_CONTRIBUTOR_CLAIMS
            and getattr(claim, "value", None) is not None
        )
    }
    local_creator_or_contributor_values = {
        creator_comparison_key(cast("LocalClaim", claim).observed)
        for claim in local_claims
        if (
            getattr(claim, "kind", None) in _CREATOR_OR_CONTRIBUTOR_CLAIMS
            and getattr(claim, "canonical", None) is not None
        )
    }
    creator_or_contributor_agreement = creator_or_contributor_agreement or bool(
        retained_creator_or_contributor_values & local_creator_or_contributor_values
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
