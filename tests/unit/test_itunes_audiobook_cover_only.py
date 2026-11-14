"""iTunes Search is an audiobook's cover source and nothing else (#1779).

Its observation stays in the audio report so its cover binds, and every reader
that would turn it into a field value, backing, field result or recording fact
skips it through `provider_supplies_field_evidence`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from mechanical_librarian.application.cover_identity import _classify_sources
from mechanical_librarian.application.review_web import _report_detail
from mechanical_librarian.domain.audio_health import derive_audio_health
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
    recording_identity_basis,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    TargetDestination,
    claim_scope,
    field_evidence_providers,
    finalize_candidate_comparisons,
    provider_supplies_field_evidence,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    evaluate_report,
    provider_candidate_represents_target_recording,
    provider_candidate_represents_target_work,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    audio_local_claims,
    audiobook_candidate,
    manual_audiobook_observation,
    recorded_dispatch,
)

_IDENTITY = "itunes-collection-1001"
# Every value differs from what the item and the admitted recording state, so
# any reader that took one would show it.
_STATED = (
    (ClaimKind.TITLE, "EMBER TIDE PROTOCOL", None),
    (ClaimKind.CREATOR, "Corin Halloway", "author"),
    (ClaimKind.DESCRIPTION, "A synthetic store synopsis no other source states.", None),
    (ClaimKind.GENRE, "Synthetic Store Genre", None),
    (ClaimKind.SERIES, "Synthetic Store Saga", None),
    (ClaimKind.SERIES_SEQUENCE, "4", None),
)


def _candidate(local_claims: tuple[LocalClaim, ...]) -> ProviderCandidate:
    claims = tuple(
        ProviderClaim.create(
            provider="store",
            candidate=_IDENTITY,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=claim_scope(kind),
            applicability="not_applicable",
            role=role,
            raw_order=order,
        )
        for order, (kind, value, role) in enumerate(_STATED)
    )
    return finalize_candidate_comparisons(
        ProviderCandidate(
            _IDENTITY,
            "unresolved",
            claims,
            (
                ProviderEvidence(
                    ProviderEvidenceKind.EDITION_WORK_RELATION,
                    "unresolved",
                    ClaimScope.EDITION,
                ),
            ),
        ),
        local_claims,
        "store",
    )


def _source(provider: str, kind: CoverSourceKind, candidate: str) -> CoverSource:
    artifact = CoverArtifact.create(
        f"{provider}-original".encode(),
        f"{provider}-preview".encode(),
        "image/jpeg",
        600,
        600,
    )
    return CoverSource(
        cover_source_id(kind, f"{provider}:{candidate}"),
        kind,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=CoverProvenance(
            provider, CoverDesignation.RELATED_EDITION, "unresolved", candidate
        ),
    )


def _report(authority: str | None = "itunes_search") -> MetadataReport:
    """The admitted recording, with the store's observation when one is named."""
    local = audio_local_claims()
    report = admitted_audiobook_report(
        local,
        additional_providers=(
            ()
            if authority is None
            else (manual_audiobook_observation("store", authority, _candidate(local)),)
        ),
    )
    sources = [
        _source(
            "catalogue", CoverSourceKind.AUDIBLE_CATALOG, "audible-product-B0SYNE7P1K"
        )
    ]
    if authority is not None:
        sources.append(_source("store", CoverSourceKind.ITUNES_SEARCH, _IDENTITY))
    return MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
        cover_sources=tuple(sorted(sources, key=lambda value: value.source_id)),
    )


def _store(report: MetadataReport) -> ProviderObservation:
    return next(value for value in report.providers if value.provider == "store")


def _store_references(report: MetadataReport) -> frozenset[str]:
    return frozenset(
        claim.reference
        for candidate in _store(report).candidates
        for claim in candidate.claims
    )


def _cited(proposal: ProposalSet) -> frozenset[str]:
    return frozenset(
        reference
        for result in proposal.results
        for alternative in result.alternatives
        for reference in alternative.evidence_references
    )


def _fields(proposal: ProposalSet) -> tuple[object, ...]:
    return tuple(
        result
        for result in proposal.results
        if result.destination is not TargetDestination.COVER
    )


def test_a_cover_only_authority_supplies_no_evidence_on_an_audio_report() -> None:
    report = _report()

    assert not provider_supplies_field_evidence(report, _store(report))
    assert _store(report) not in field_evidence_providers(report)
    assert all(
        provider_supplies_field_evidence(report, value)
        for value in report.providers
        if value.provider != "store"
    )
    # An ebook report reads iTunes as the corroborating source it is there.
    ebook = cast(MetadataReport, SimpleNamespace(audio=None))
    assert provider_supplies_field_evidence(ebook, _store(report))


def test_the_store_offers_no_value_backing_or_field_result() -> None:
    report = _report()
    proposal = evaluate_report(report)

    assert not _cited(proposal) & _store_references(report)
    # Every field decides exactly as it does without the store's observation,
    # so it backs no alternative either.
    assert _fields(evaluate_report(_report(None))) == _fields(proposal)


def test_the_same_statements_from_an_evidence_authority_are_offered() -> None:
    """The control: only the cover-only rule keeps the store's values out."""
    report = _report("hardcover")

    assert _cited(evaluate_report(report)) & _store_references(report)


def test_the_store_states_no_recording_fact() -> None:
    report = _report()
    store = _store(report)
    (candidate,) = store.candidates

    assert not provider_candidate_represents_target_recording(report, store, candidate)
    assert not provider_candidate_represents_target_work(report, store, candidate)
    without = _report(None)
    assert admitted_audiobook_identity(report) is admitted_audiobook_identity(without)
    assert recording_identity_basis(report) == recording_identity_basis(without)
    assert derive_audio_health(report) == derive_audio_health(without)


def test_a_recording_shaped_store_answer_admits_no_recording() -> None:
    """Were it read, this answer alone would admit the recording by runtime."""
    local = audio_local_claims()
    sole = admitted_audiobook_report(local)
    store = recorded_dispatch(
        manual_audiobook_observation(
            "store",
            "itunes_search",
            audiobook_candidate(
                local, identity="itunes-collection-1002", provider="store"
            ),
        )
    )

    def with_providers(*providers: ProviderObservation) -> MetadataReport:
        return MetadataReport.create(
            target=sole.target,
            file=sole.file,
            local_claims=sole.local_claims,
            local_reasons=sole.local_reasons,
            providers=providers,
            audio=sole.audio,
        )

    report = with_providers(store)
    (candidate,) = store.candidates

    assert not provider_candidate_represents_target_recording(report, store, candidate)
    assert not admitted_audiobook_identity(report)
    assert recording_identity_basis(report) is None
    assert derive_audio_health(with_providers()) == derive_audio_health(report)
    assert not _cited(evaluate_report(report)) & frozenset(
        claim.reference for claim in candidate.claims
    )


def test_the_store_cover_binds_and_is_offered_beside_the_recording() -> None:
    report = _report()
    source = next(
        value
        for value in report.cover_sources
        if value.provenance is not None and value.provenance.provider == "store"
    )

    # It binds to the store's candidate, which states no identifier, so its
    # identity stays unknown until ADR-0096's title and creator match.
    outcome, reasons, _evidence = _classify_sources(
        report, (source,), WorkIdentityLevel.IDENTIFIER
    )
    assert outcome == "unknown"
    assert reasons[0] == "retained_title_evidence_insufficient"
    cover = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.COVER
    )
    assert source.source_id in {
        reference
        for alternative in cover.alternatives
        for reference in alternative.evidence_references
    }


def test_the_report_detail_projects_no_store_claim() -> None:
    """The audiobook's report detail lists no iTunes statement as evidence."""

    def stated(authority: str) -> set[object]:
        report = _report(authority)
        return {
            value["candidate"]
            for value in _report_detail(report.to_data(), report)["provider_evidence"]
        }

    assert _IDENTITY not in stated("itunes_search")
    # The control: the same statements from an evidence authority are listed.
    assert _IDENTITY in stated("hardcover")
