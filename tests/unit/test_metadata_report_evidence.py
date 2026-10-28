"""Metadata-report evidence: provider diagnostics and access times,
typed date events, the closed claim and field vocabularies, and the
canonical content-addressed report.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest

from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CurrentnessError,
    GenerateMetadataReport,
    InspectionResult,
    MetadataReportError,
    ProviderBinding,
    ReportRepository,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioObservation,
    AudioObservationKind,
    AudioTrackDigest,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_REPORT_BYTES,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    ComparisonMethod,
    DateLinkConfidence,
    FieldResult,
    FieldSource,
    FieldSourceResult,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    RearClaimProvenance,
    ReportValueError,
    TargetFacts,
    audio_set_content_digest,
    audio_set_identity,
    canonical_json,
    claim_scope,
    comparison_links,
    default_target_fields,
    derive_field_results,
    normalize_date_value,
    normalize_title,
    provider_access_time,
)
from mechanical_librarian.domain.proposal_set import (
    TargetDestination,
    evaluate_report,
)
from mechanical_librarian.domain.work_identity import work_title_has_variant_marker
from tests.support.audiobook_reports import recorded_dispatch, recording_lookup_report
from tests.support.metadata_report_doubles import (
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
)


def test_real_audiobookshelf_currentness_accepts_asymmetric_equivalent_locations() -> (
    None
):
    def payload(*, file_path: str | None, size: int = 1) -> dict[str, object]:
        file_metadata: dict[str, object] = {"relPath": "book.epub", "size": size}
        if file_path is not None:
            file_metadata["path"] = file_path
        return {
            "id": "item-1",
            "libraryId": "library-id",
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "relPath": "Author/Book",
            "path": "/media/Author/Book",
            "isFile": False,
            "media": {
                "metadata": {"title": "Current title"},
                "ebookFile": {
                    "ebookFormat": "epub",
                    "ino": "inode-1",
                    "metadata": file_metadata,
                },
            },
        }

    def service_for(responses: list[dict[str, object]]):
        position = 0

        def factory() -> httpx.Client:
            nonlocal position

            def handler(_request: httpx.Request) -> httpx.Response:
                nonlocal position
                response = responses[position]
                position += 1
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, json=response
                )

            return httpx.Client(transport=httpx.MockTransport(handler))

        target = AudiobookshelfItemReader(
            client_factory=factory,
            endpoint="https://example.invalid",
            library_id="library-id",
            service_path="/media",
            maximum_bytes=65_536,
        )
        repository = FakeReportRepository()
        return GenerateMetadataReport(
            target=target,
            inspector=FakeInspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: cast(ReportRepository, repository),
            providers=(),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks", "primary", "library-id", "ebook", "books", ()
            ),
            mount=MediaMountConfiguration("books", "/container", "/media", True),
            limits=ReportLimits(),
        ), repository

    initial = payload(file_path=None)
    service, repository = service_for(
        [initial, payload(file_path="/media/Author/Book/book.epub")]
    )
    assert service.run("item-1", 0)
    assert 1 == len(repository.values)

    changed, repository = service_for([initial, payload(file_path=None, size=2)])
    with pytest.raises(CurrentnessError):
        changed.run("item-1", 0)
    assert {} == repository.values

    contradictory, repository = service_for(
        [initial, payload(file_path="/media/Other/book.epub")]
    )
    with pytest.raises(MetadataReportError):
        contradictory.run("item-1", 0)
    assert {} == repository.values


_PROVIDER_DIAGNOSTIC = FailureDiagnostic(
    FailureCategory.UNKNOWN,
    FailureComponent.PROVIDER,
    FailureStage.RESPONSE,
)
_REFUSED_PROVIDER_DIAGNOSTICS = (
    (
        FailureDiagnostic(
            FailureCategory.UNKNOWN,
            FailureComponent.REVIEWER,
            FailureStage.RESPONSE,
        ),
    ),
    (
        FailureDiagnostic(
            FailureCategory.UNKNOWN,
            FailureComponent.PROVIDER,
            FailureStage.REQUEST,
        ),
    ),
    (
        FailureDiagnostic(
            FailureCategory.CITATION_MEMBERSHIP,
            FailureComponent.PROVIDER,
            FailureStage.RESPONSE,
        ),
    ),
    (_PROVIDER_DIAGNOSTIC, _PROVIDER_DIAGNOSTIC),
    (
        _PROVIDER_DIAGNOSTIC,
        FailureDiagnostic(
            FailureCategory.UNKNOWN,
            FailureComponent.PROVIDER,
            FailureStage.RESPONSE,
            count=2,
        ),
    ),
    (
        _PROVIDER_DIAGNOSTIC,
        FailureDiagnostic(
            FailureCategory.HTTP_NON_SUCCESS,
            FailureComponent.PROVIDER,
            FailureStage.RESPONSE,
        ),
    ),
)


@pytest.mark.parametrize(
    "diagnostics",
    _REFUSED_PROVIDER_DIAGNOSTICS,
    ids=(
        "foreign_component",
        "foreign_stage",
        "foreign_category",
        "repeated_diagnostic",
        "repeated_key",
        "unsorted_categories",
    ),
)
def test_provider_diagnostics_reject_foreign_and_noncanonical_aggregates(
    diagnostics,
) -> None:
    valid = _PROVIDER_DIAGNOSTIC
    with pytest.raises(ReportValueError):
        ProviderObservation(
            "catalogue",
            ProviderStatus.UNAVAILABLE,
            diagnostics=diagnostics,
        )

    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.UNAVAILABLE,
                diagnostics=(valid,),
            ),
        ),
    )
    raw = json.loads(report.to_json())
    raw["providers"][0]["diagnostics"][0]["component"] = "reviewer"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(raw))


@pytest.mark.parametrize(
    "clock",
    (True, float("nan"), float("inf"), 10**1000),
    ids=("bool", "nan", "infinity", "unbounded_integer"),
)
@pytest.mark.parametrize(
    "invalid",
    (
        "",
        "2023-11-14T22:13:20Z",
        "2023-11-14T22:13:20.125000+00:00",
        "2023-02-29T22:13:20.125000Z",
        "2023-11-14T22:13:60.125000Z",
    ),
)
def test_provider_access_time_is_canonical_bounded_and_replay_stable(
    invalid, clock
) -> None:
    accessed_at = provider_access_time(1_700_000_000.125)
    assert "2023-11-14T22:13:20.125000Z" == accessed_at
    observation = ProviderObservation(
        "catalogue",
        ProviderStatus.NO_MATCH,
        query_kind=ProviderQueryKind.WORK,
        authority="hardcover",
        accessed_at=accessed_at,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(observation,),
    )
    reopened = MetadataReport.from_json(report.to_json())
    assert accessed_at == reopened.providers[0].accessed_at
    assert report.to_json() == reopened.to_json()
    missing = report.to_data()
    missing["providers"][0].pop("accessed_at")
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(missing))
    assert (
        ProviderObservation("catalogue", ProviderStatus.INSUFFICIENT_INPUT).to_data()[
            "accessed_at"
        ]
        is None
    )

    with pytest.raises(ReportValueError):
        provider_access_time(clock)
    with pytest.raises(ReportValueError):
        ProviderObservation(
            "catalogue",
            ProviderStatus.NO_MATCH,
            accessed_at=invalid,
        )


def test_provider_dates_preserve_lexical_normalized_precision_and_linkage() -> None:
    lexical = "2024-03-05T10:30:00-04:00"
    claim = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.PUBLICATION_DATE,
        value=lexical,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    assert lexical == claim.date_lexical
    assert lexical == claim.value
    assert DatePrecision.TIMESTAMP == claim.date_precision
    assert DateEvent.MANIFESTATION_PUBLICATION == claim.date_event
    assert DateLinkConfidence.EXPLICIT == claim.date_confidence
    with pytest.raises(ReportValueError):
        replace(claim, date_precision=DatePrecision.DAY)

    unresolved = ProviderEvidence(
        ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
        "2024-03",
        ClaimScope.EDITION,
    )
    assert "2024-03" == unresolved.date_normalized
    assert DatePrecision.MONTH == unresolved.date_precision
    assert DateEvent.UNKNOWN == unresolved.date_event
    assert DateLinkConfidence.UNRESOLVED == unresolved.date_confidence
    assert 0 == unresolved.source_ordinal
    with pytest.raises(ReportValueError):
        replace(unresolved, source_ordinal=-1)
    with pytest.raises(ReportValueError):
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.PUBLICATION_DATE,
            value="2024-03-05T10:30:00+14:01",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780306406157",
                canonical="9780306406157",
                excerpt="9780306406157",
                applicability="unknown",
                checksum="valid_isbn_13",
                applicability_basis="unlabelled",
                scope=ClaimScope.EDITION,
            ),
        ),
        local_reasons=("opening_matter_isbn",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (
                    ProviderCandidate(
                        "edition-1",
                        "work-1",
                        (
                            ProviderClaim.create(
                                provider="catalogue",
                                candidate="edition-1",
                                kind=ClaimKind.ISBN,
                                value="9780306406157",
                                state=FieldState.VALID,
                                scope=ClaimScope.EDITION,
                                applicability="unknown",
                            ),
                            ProviderClaim.create(
                                provider="catalogue",
                                candidate="edition-1",
                                kind=ClaimKind.PUBLICATION_DATE,
                                value=lexical,
                                state=FieldState.VALID,
                                scope=ClaimScope.EDITION,
                                applicability="not_applicable",
                                raw_order=1,
                            ),
                        ),
                        (),
                    ),
                ),
            ),
        ),
    )
    replay_data = report.to_data()
    replay_data["providers"][0]["candidates"][0]["claims"][1]["date_precision"] = "day"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(replay_data))


def test_from_json_refuses_an_unpaired_surrogate_as_its_declared_type() -> None:
    """The length guard's codec failure is a refusal, not an escape.

    The guard encodes the document to measure it, so a string carrying an
    unpaired surrogate makes `encode` raise. Run outside the conversion
    block, that `UnicodeEncodeError` escaped `from_json` as itself and made
    the declared `ReportValueError` contract a half-truth no caller could
    narrow to. The guard now sits inside the block, so the codec failure is
    the one declared refusal and stays chained as the cause (#698).
    """
    with pytest.raises(ReportValueError) as refused:
        MetadataReport.from_json('{"a": "\ud800"}')
    assert isinstance(refused.value.__cause__, UnicodeEncodeError)

    # The guard's other two rejections keep the identity they always had,
    # and neither acquires a cause it did not have.
    for document in ("", "{}" + "　" * MAX_REPORT_BYTES):
        with pytest.raises(ReportValueError) as refused:
            MetadataReport.from_json(document)
        assert refused.value.__cause__ is None


def test_provider_asin_is_canonical_before_reference_and_report_replay() -> None:
    claim = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.ASIN,
        value="b012345678",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    canonical = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.ASIN,
        value="B012345678",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    assert "B012345678" == claim.value
    assert canonical.reference == claim.reference
    with pytest.raises(ReportValueError):
        replace(claim, value="b012345678")
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (
                    ProviderCandidate(
                        "edition-1",
                        "work-1",
                        (claim,),
                    ),
                ),
            ),
        ),
    )
    replayed = MetadataReport.from_json(report.to_json())
    assert "B012345678" == replayed.providers[0].candidates[0].claims[0].value


def test_local_date_event_scope_is_event_aware_and_replay_rejects_mismatch() -> None:
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.DATE_EVENT,
            observed="1998",
            canonical="1998",
            excerpt="First published 1998",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            date_event=event,
            date_precision=DatePrecision.YEAR,
        )
        for event in (
            DateEvent.WORK_FIRST_PUBLICATION,
            DateEvent.CREATION,
            DateEvent.COPYRIGHT,
        )
    )
    assert [claim.scope for claim in claims] == [
        ClaimScope.WORK,
        ClaimScope.WORK,
        ClaimScope.EDITION,
    ]
    with pytest.raises(ReportValueError):
        replace(claims[0], scope=ClaimScope.EDITION)

    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Example"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=claims,
        local_reasons=("opening_date_event",),
        providers=(),
    )
    replay_data = report.to_data()
    replay_data["local"]["claims"][0]["scope"] = "edition"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(replay_data))


@pytest.mark.parametrize(
    ("kind", "event", "observed"),
    (
        (
            ClaimKind.PUBLICATION_DATE,
            DateEvent.MANIFESTATION_PUBLICATION,
            "2001-02-03T04:05:06Z",
        ),
        (
            ClaimKind.DATE_EVENT,
            DateEvent.WORK_FIRST_PUBLICATION,
            "2001-02-03T04:05:06+01:00:00",
        ),
        (ClaimKind.DATE_EVENT, DateEvent.COPYRIGHT, "2001-02-03"),
    ),
)
@pytest.mark.parametrize("forged_observed", ("2002-04-05", "not-a-date"))
def test_valid_local_dates_bind_observed_canonical_and_precision(
    kind, event, observed, forged_observed
) -> None:
    canonical, precision = normalize_date_value(observed)
    claim = LocalClaim.create(
        kind=kind,
        observed=observed,
        canonical=canonical,
        excerpt=observed,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=claim_scope(kind, event),
        date_event=event,
        date_precision=precision,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("package_metadata_date",),
        providers=(),
    )
    assert report == MetadataReport.from_json(report.to_json())
    document = report.to_data()
    document["local"]["claims"][0]["observed"] = forged_observed
    document["local"]["claims"][0]["excerpt"] = forged_observed
    with pytest.raises(ReportValueError, match="local date precision is inconsistent"):
        MetadataReport.from_json(canonical_json(document))


@pytest.mark.parametrize(
    ("kind", "event", "observed", "canonical", "precision"),
    (
        (
            ClaimKind.PUBLICATION_DATE,
            DateEvent.MANIFESTATION_PUBLICATION,
            "2001",
            "2002",
            DatePrecision.YEAR,
        ),
        (
            ClaimKind.DATE_EVENT,
            DateEvent.CREATION,
            "not-a-date",
            "2001",
            DatePrecision.YEAR,
        ),
        (
            ClaimKind.DATE_EVENT,
            DateEvent.COPYRIGHT,
            "2001-02-03T04:05:06Z",
            "2001-02-03T04:05:06Z",
            DatePrecision.TIMESTAMP,
        ),
    ),
)
def test_inconsistent_local_date_precision_is_refused_at_construction(
    kind, event, observed, canonical, precision
) -> None:
    with pytest.raises(ReportValueError, match="local date precision is inconsistent"):
        LocalClaim.create(
            kind=kind,
            observed=observed,
            canonical=canonical,
            excerpt=observed,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=claim_scope(kind, event),
            date_event=event,
            date_precision=precision,
        )


@pytest.mark.parametrize(
    ("field_name", "forged", "document_forged"),
    (
        ("canonical", "2001", "2001"),
        ("date_precision", DatePrecision.YEAR, DatePrecision.YEAR.value),
    ),
)
@pytest.mark.parametrize(
    ("state", "observed"),
    (
        (FieldState.ABSENT, "absent-date"),
        (FieldState.MALFORMED, "not-a-date"),
        (FieldState.OVERSIZED, "oversized-source-text-sha256:" + "a" * 64),
        (FieldState.UNSUPPORTED, "unsafe-source-text-sha256:" + "b" * 64),
        (FieldState.UNTYPED, "untyped-date"),
        (FieldState.INAPPLICABLE, "2001"),
    ),
)
@pytest.mark.parametrize(
    ("kind", "event"),
    (
        (ClaimKind.PUBLICATION_DATE, DateEvent.MANIFESTATION_PUBLICATION),
        (ClaimKind.DATE_EVENT, DateEvent.COPYRIGHT),
    ),
)
def test_nonvalid_local_dates_cannot_retain_canonical_values_or_precision(
    kind, event, state, observed, field_name, forged, document_forged
) -> None:
    claim = LocalClaim.create(
        kind=kind,
        observed=observed,
        canonical=None,
        excerpt=observed,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=claim_scope(kind, event),
        state=state,
        date_event=event,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("package_metadata_date",),
        providers=(),
    )
    assert report == MetadataReport.from_json(report.to_json())
    with pytest.raises(ReportValueError, match="local date precision is inconsistent"):
        replace(claim, **{field_name: forged})
    document = report.to_data()
    document["local"]["claims"][0][field_name] = document_forged
    with pytest.raises(ReportValueError, match="local date precision is inconsistent"):
        MetadataReport.from_json(canonical_json(document))


def test_local_dates_and_contextual_credits_replay_without_candidate_conflation() -> (
    None
):
    publication = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2024-03",
        canonical="2024-03",
        excerpt="This ebook edition published 2024-03",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.MONTH,
        occurrence=1,
    )
    conversion = LocalClaim.create(
        kind=ClaimKind.CONTEXTUAL_CREDIT,
        observed="Tool House",
        canonical="Tool House",
        excerpt="EPUB conversion by Tool House",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="conversion",
        occurrence=2,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Example"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(publication, conversion),
        local_reasons=("not_applicable",),
        providers=(),
    )
    replayed = MetadataReport.from_json(report.to_json())
    assert replayed == report
    assert replayed.local_claims[0].date_event == DateEvent.MANIFESTATION_PUBLICATION
    assert replayed.local_claims[0].date_precision is DatePrecision.MONTH
    assert replayed.local_claims[1].kind != ClaimKind.CONTRIBUTOR
    assert replayed.local_claims[1].kind is ClaimKind.CONTEXTUAL_CREDIT


def test_exhausted_provider_reopens_as_typed_terminal_without_attempts() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Current title", (selected,))
    target = FakeTarget([snapshot, snapshot])
    claim = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    inspector = FakeInspector(InspectionResult("c" * 64, (claim,)))
    dispatches = 0

    def unavailable(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": {}},
        )

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: cast(
                ReportRepository, open_repository(database)
            ),
            providers=(
                ProviderBinding(
                    "google_books",
                    True,
                    lambda: GoogleBooksMetadataProvider(
                        "google_books",
                        httpx.Client(transport=httpx.MockTransport(unavailable)),
                        65_536,
                        sleep=lambda _: None,
                        jitter=lambda: 0.0,
                    ),
                    "google_books",
                ),
            ),
            connection=TargetConnectionConfiguration(
                "primary",
                "https://example.invalid",
                "/private/target",
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("google_books",),
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        )
        report = MetadataReport.from_json(service.run("item-1", 0))
        repository = open_repository(database, create=False)
        try:
            persisted = repository.get(report.report_id)
        finally:
            repository.close()
    reopened = MetadataReport.from_json(persisted)
    assert 6 == dispatches
    assert ProviderStatus.NO_MATCH != reopened.providers[0].status
    assert ProviderStatus.UNAVAILABLE == reopened.providers[0].status
    assert () == reopened.providers[0].candidates
    assert ("http_non_success",) == tuple(
        value.category.value for value in reopened.providers[0].diagnostics
    )
    assert (6,) == tuple(value.count for value in reopened.providers[0].diagnostics)
    assert '"attempt' not in persisted
    for forbidden in (
        "delay_seconds",
        "elapsed_seconds",
        "ordinal",
        "retry-after",
    ):
        assert forbidden not in persisted


def test_rejected_cover_state_round_trips_in_serialized_report() -> None:
    source = CoverSource(
        cover_source_id(CoverSourceKind.OPEN_LIBRARY, "open_library:edition:OL1M"),
        CoverSourceKind.OPEN_LIBRARY,
        CoverSourceState.REJECTED,
        provenance=CoverProvenance(
            "open_library",
            CoverDesignation.EXACT_EDITION,
            "open-library-work-OL5W",
            "open-library-edition-OL1M",
        ),
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
        cover_sources=(source,),
    )
    serialized = report.to_json()
    assert '"state":"rejected"' in serialized
    assert report == MetadataReport.from_json(serialized)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("title", "The Example: An Abridgement"),
        ("subtitle", "An Abridgment"),
        ("title", "The Abridged Example"),
        ("subtitle", "An Adapted Translation"),
    ),
)
@pytest.mark.parametrize(
    "unmarked",
    (
        "The Example",
        "Abridgementary Notes",
        "Abridgmental Notes",
        "Adaptational Notes",
        "Translationist Notes",
    ),
)
def test_variant_marker_fails_closed_for_abridgement_nouns_and_preserves_token_boundaries(
    field, value, unmarked
) -> None:
    assert work_title_has_variant_marker(value)
    assert not work_title_has_variant_marker(unmarked)


def test_closed_claim_vocabulary_is_exact() -> None:
    assert {kind.value for kind in ClaimKind} == {
        "title",
        "subtitle",
        "creator",
        "contributor",
        "series",
        "series_sequence",
        "isbn",
        "asin",
        "publication_date",
        "date_event",
        "contextual_credit",
        "publisher",
        "description",
        "language",
        "subject",
        "imprint",
        "genre",
        "tag",
        "duration",
        "performance_type",
        "adult_product",
    }


def test_field_results_are_complete_canonical_and_separate_from_claims() -> None:
    results = derive_field_results(())
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
        field_results=results,
    )
    assert report.field_results == results
    assert MetadataReport.from_json(report.to_json()) == report
    with pytest.raises(ReportValueError):
        MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
            field_results=results[:-1],
        )
    contradictory = tuple(
        FieldResult(value.kind, FieldState.VALID, value.sources)
        if value.kind is ClaimKind.TITLE
        else value
        for value in results
    )
    with pytest.raises(ReportValueError):
        MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
            field_results=contradictory,
        )
    source_contradiction = tuple(
        FieldResult(
            value.kind,
            value.state,
            (
                FieldSourceResult(FieldSource.EPUB, FieldState.ABSENT),
                FieldSourceResult(FieldSource.TARGET_METADATA, FieldState.VALID),
                FieldSourceResult(FieldSource.AUDIO_TAG, FieldState.ABSENT),
                FieldSourceResult(FieldSource.AUDIO_SIDECAR, FieldState.ABSENT),
                FieldSourceResult(FieldSource.AUDIO_PATH, FieldState.ABSENT),
                FieldSourceResult(FieldSource.PRIOR_ADMISSION, FieldState.UNSUPPORTED),
            ),
        )
        if value.kind is ClaimKind.TITLE
        else value
        for value in results
    )
    with pytest.raises(ReportValueError):
        MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
            field_results=source_contradiction,
        )


def test_non_valid_observation_state_is_projected_to_its_field_result() -> None:
    claim = LocalClaim.create(
        kind=ClaimKind.DESCRIPTION,
        observed="oversized-source-text-sha256:" + "a" * 64,
        canonical=None,
        excerpt="oversized-source-text-sha256:" + "a" * 64,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        basis="package_metadata",
        state=FieldState.OVERSIZED,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("package_metadata_description",),
        providers=(),
    )
    assert (
        dict((value.kind, value.state) for value in report.field_results)[
            ClaimKind.DESCRIPTION
        ]
        == FieldState.OVERSIZED
    )


def test_malformed_and_inapplicable_claims_derive_independent_field_results() -> None:
    claims = (
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="not-an-isbn",
            canonical=None,
            excerpt="not-an-isbn",
            applicability="unknown",
            checksum="invalid",
            applicability_basis="unlabelled",
            state=FieldState.MALFORMED,
        ),
        LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="not supplied",
            canonical="not supplied",
            excerpt="not supplied",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            state=FieldState.INAPPLICABLE,
        ),
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=claims,
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    states = {value.kind: value.state for value in report.field_results}
    assert states[ClaimKind.ISBN] == FieldState.MALFORMED
    assert states[ClaimKind.PUBLISHER] == FieldState.INAPPLICABLE


def test_source_capabilities_keep_target_title_only_and_epub_exact() -> None:
    title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Current title",
        canonical="Current title",
        excerpt="Current title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(title,),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    fields = {
        value.kind: {source.source: source.state for source in value.sources}
        for value in report.field_results
    }
    assert fields[ClaimKind.TITLE][FieldSource.TARGET_METADATA] == FieldState.VALID
    assert fields[ClaimKind.TITLE][FieldSource.EPUB] == FieldState.ABSENT
    assert all(
        fields[kind][FieldSource.TARGET_METADATA] is FieldState.UNSUPPORTED
        for kind in ClaimKind
        if kind is not ClaimKind.TITLE
    )


@pytest.mark.parametrize(
    ("prefix", "state", "forged_state", "canonical"),
    (
        ("unsafe-source-text-sha256:", FieldState.UNSUPPORTED, FieldState.VALID, None),
        (
            "unsafe-source-text-sha256:",
            FieldState.UNSUPPORTED,
            FieldState.OVERSIZED,
            None,
        ),
        (
            "unsafe-source-text-sha256:",
            FieldState.UNSUPPORTED,
            FieldState.UNSUPPORTED,
            "Forged",
        ),
        ("oversized-source-text-sha256:", FieldState.OVERSIZED, FieldState.VALID, None),
        (
            "oversized-source-text-sha256:",
            FieldState.OVERSIZED,
            FieldState.UNSUPPORTED,
            None,
        ),
        (
            "oversized-source-text-sha256:",
            FieldState.OVERSIZED,
            FieldState.OVERSIZED,
            "Forged",
        ),
    ),
)
def test_source_text_sentinels_refuse_a_forged_state_or_canonical(
    prefix, state, forged_state, canonical
) -> None:
    sentinel = prefix + "a" * 64
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=sentinel,
        canonical=None,
        excerpt=sentinel,
        applicability="not_applicable",
        basis="package_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        state=state,
    )
    assert claim.state == state
    with pytest.raises(ReportValueError):
        LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed=sentinel,
            canonical=canonical,
            excerpt=sentinel,
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            state=forged_state,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("observed", "/Users/private/title"),
        ("canonical", "Authorization: Bearer abcdefgh"),
        ("excerpt", "/Users/private/title"),
        ("role", "Authorization: Bearer abcdefgh"),
    ),
)
def test_unsafe_text_is_refused_in_every_public_local_claim_field(field, value) -> None:
    values = {
        "observed": "Ada Example",
        "canonical": "Ada Example",
        "excerpt": "Ada Example",
        "role": None,
    }
    values[field] = value
    with pytest.raises(ReportValueError):
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=values["observed"],
            canonical=values["canonical"],
            excerpt=values["excerpt"],
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            role=values["role"],
        )


@pytest.mark.parametrize("field", ("marker", "citation"))
def test_unsafe_text_is_refused_in_every_rear_provenance_field(field) -> None:
    values = {"marker": "copyright", "citation": "Copyright 2026"}
    values[field] = "/Users/private/title"
    with pytest.raises(ReportValueError):
        RearClaimProvenance("a" * 64, 1, 1, values["marker"], values["citation"])


@pytest.mark.parametrize(
    "role",
    (
        "unsafe-source-text-sha256:" + "a" * 64,
        "oversized-source-text-sha256:" + "a" * 64,
    ),
    ids=("unsafe", "oversized"),
)
def test_creator_roles_accept_a_canonical_source_text_sentinel(role) -> None:
    claim = LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed="Ada Example",
        canonical="Ada Example",
        excerpt="Ada Example",
        applicability="not_applicable",
        basis="package_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role=role,
    )
    assert claim.role == role


@pytest.mark.parametrize(
    "role",
    (
        "unsafe-source-text-sha256:" + "A" * 64,
        "oversized-source-text-sha256:short",
        "x" * 65,
    ),
    ids=("uppercase_digest", "short_digest", "oversized_role"),
)
def test_creator_roles_refuse_a_noncanonical_source_text_sentinel(role) -> None:
    with pytest.raises(ReportValueError):
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed="Ada Example",
            canonical="Ada Example",
            excerpt="Ada Example",
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            role=role,
        )


def test_local_claim_source_sentinels_and_public_evidence_are_closed() -> None:
    valid = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Current title",
        canonical="Current title",
        excerpt="Current title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )
    with pytest.raises(ReportValueError):
        LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Example Press",
            canonical="Example Press",
            excerpt="Example Press",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(valid,),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    data = report.to_data()
    data["local"]["claims"][0]["canonical"] = "/Users/private/title"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(data))

    replay = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed="Ada Example",
                canonical="Ada Example",
                excerpt="Ada Example",
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                role="author",
            ),
        ),
        local_reasons=("package_metadata_creator",),
        providers=(),
    )
    replay_data = replay.to_data()
    replay_data["local"]["claims"][0]["role"] = "unsafe-source-text-sha256:" + "A" * 64
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(replay_data))


_REAR_PROVENANCE = RearClaimProvenance("a" * 64, 1, 1, "copyright", "Copyright 2026")


def _provider_claim(kind: ClaimKind, value: str) -> ProviderClaim:
    return ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION if kind is ClaimKind.ISBN else ClaimScope.WORK,
        applicability="ebook" if kind is ClaimKind.ISBN else "not_applicable",
    )


@pytest.mark.parametrize(
    ("kind", "value"),
    (
        (ClaimKind.TITLE, "/Users/private/title"),
        (ClaimKind.TITLE, "Authorization: Bearer abcdefgh"),
    ),
    ids=("path", "credential"),
)
def test_provider_claim_values_refuse_unsafe_text(kind, value) -> None:
    with pytest.raises(ReportValueError):
        _provider_claim(kind, value)


@pytest.mark.parametrize(
    "kind",
    (
        ClaimKind.SUBTITLE,
        ClaimKind.SERIES,
        ClaimKind.SERIES_SEQUENCE,
        ClaimKind.PUBLISHER,
        ClaimKind.IMPRINT,
    ),
)
def test_rear_provenance_is_carried_by_every_bounded_opening_matter_kind(kind) -> None:
    claim = LocalClaim.create(
        kind=kind,
        observed="Bounded value",
        canonical="Bounded value",
        excerpt="Bounded value",
        applicability="not_applicable",
        basis="opening_matter",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        rear_provenance=_REAR_PROVENANCE,
    )
    assert claim.rear_provenance == _REAR_PROVENANCE
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("opening_matter",),
        providers=(),
    )
    assert MetadataReport.from_json(report.to_json()) == report


@pytest.mark.parametrize(
    ("kind", "basis", "role", "rear"),
    (
        (ClaimKind.TITLE, "opening_matter", "author", None),
        (ClaimKind.DESCRIPTION, "opening_matter", None, _REAR_PROVENANCE),
        (ClaimKind.TITLE, "package_metadata", None, _REAR_PROVENANCE),
        (ClaimKind.TITLE, "target_metadata", None, _REAR_PROVENANCE),
    ),
    ids=("role_on_title", "rear_on_description", "rear_on_package", "rear_on_target"),
)
def test_local_role_and_rear_provenance_refuse_foreign_kinds_and_bases(
    kind, basis, role, rear
) -> None:
    with pytest.raises(ReportValueError):
        LocalClaim.create(
            kind=kind,
            observed="A title",
            canonical="A title",
            excerpt="A title",
            applicability="not_applicable",
            basis=basis,
            checksum="not_applicable",
            applicability_basis="not_applicable",
            role=role,
            rear_provenance=rear,
        )


def test_provider_claim_and_local_role_rear_provenance_are_closed() -> None:
    assert _provider_claim(ClaimKind.TITLE, "A title").kind == ClaimKind.TITLE
    assert _provider_claim(ClaimKind.ISBN, "9780306406157").kind == ClaimKind.ISBN
    assert (
        _provider_claim(ClaimKind.PUBLISHER, "Example Press").kind
        == ClaimKind.PUBLISHER
    )
    valid_rear = LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed="Ada Example",
        canonical="Ada Example",
        excerpt="Ada Example",
        applicability="not_applicable",
        basis="opening_matter",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="author",
        rear_provenance=_REAR_PROVENANCE,
    )
    assert valid_rear.rear_provenance == _REAR_PROVENANCE


def test_edition_field_scope_agrees_with_same_scope_provider_evidence() -> None:
    for kind in (
        ClaimKind.PUBLICATION_DATE,
        ClaimKind.PUBLISHER,
        ClaimKind.IMPRINT,
        ClaimKind.LANGUAGE,
    ):
        assert claim_scope(kind) == ClaimScope.EDITION
    local = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Press",
        canonical="Press",
        excerpt="Press",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=claim_scope(ClaimKind.PUBLISHER),
    )
    imprint = LocalClaim.create(
        kind=ClaimKind.IMPRINT,
        observed="Press",
        canonical="Press",
        excerpt="Press",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=claim_scope(ClaimKind.IMPRINT),
    )
    provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.PUBLISHER,
        value="Press",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    provider_imprint = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.IMPRINT,
        value="Press",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    assert comparison_links(provider, (local, imprint)) == (
        ComparisonLink(local.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT),
    )
    assert comparison_links(provider_imprint, (local, imprint)) == (
        ComparisonLink(imprint.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT),
    )
    normalized_provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.PUBLISHER,
        value="PRESS",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    assert comparison_links(normalized_provider, (local,)) == (
        ComparisonLink(
            local.reference,
            Comparison.AGREEMENT,
            ComparisonMethod.CLOSED_FIELD_NORMALIZATION,
        ),
    )
    normalized_local = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Press  House",
        canonical="Press House",
        excerpt="Press  House",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    canonical_provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.PUBLISHER,
        value="Press House",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )
    assert comparison_links(canonical_provider, (normalized_local,)) == (
        ComparisonLink(
            normalized_local.reference,
            Comparison.AGREEMENT,
            ComparisonMethod.CLOSED_FIELD_NORMALIZATION,
        ),
    )


def test_non_valid_title_sentinel_cannot_become_canonical() -> None:
    sentinel = "unsafe-source-text-sha256:" + "a" * 64
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=sentinel,
        canonical=None,
        excerpt=sentinel,
        applicability="not_applicable",
        basis="package_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        state=FieldState.UNSUPPORTED,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("package_metadata_title",),
        providers=(),
    )
    assert report.local_claims[0].canonical is None
    assert report.field_results[0].state == FieldState.UNSUPPORTED


def test_closed_local_field_vocabulary_accepts_package_and_contextual_evidence() -> (
    None
):
    publisher = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Example Press",
        canonical="Example Press",
        excerpt="Publisher: Example Press",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        basis="package_metadata",
    )
    subject = LocalClaim.create(
        kind=ClaimKind.SUBJECT,
        observed="Speculative fiction",
        canonical=None,
        excerpt="Subject: Speculative fiction",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        basis="package_metadata",
    )
    assert publisher.kind == ClaimKind.PUBLISHER
    assert subject.canonical is None


def test_report_is_closed_canonical_and_content_addressed() -> None:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="A title",
        canonical="A title",
        excerpt="A title",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("opening_title",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )
    assert MetadataReport.from_json(report.to_json()) == report
    assert report.report_id.startswith("report_")
    assert report.local_status == "candidate"


def test_title_claims_retain_exact_512_characters_but_isbn_evidence_stays_bounded() -> (
    None
):
    title = "T" * 512
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )
    assert claim.observed == title
    assert (
        TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields(title)
        ).current_title
        == title
    )
    with pytest.raises(ReportValueError):
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9" * 281,
            canonical=None,
            excerpt="9" * 281,
            applicability="unknown",
            checksum="invalid",
            applicability_basis="unlabelled",
        )


@pytest.mark.parametrize(
    ("title", "canonical"),
    (
        ("Cafe\u0301", "Café"),
        ("An  intentional  title", "An intentional title"),
    ),
)
def test_safe_observed_titles_preserve_exact_text_and_canonicalize_claims(
    title, canonical
) -> None:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=normalize_title(title),
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    replay = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    assert report.target.current_title == title
    assert report.local_claims[0].observed == title
    assert report.local_claims[0].canonical == canonical
    assert report.report_id == replay.report_id
    assert MetadataReport.from_json(report.to_json()) == report


def _audio_claim(
    kind: ClaimKind,
    observed: str,
    basis: str = "embedded_tag",
    role: str | None = None,
) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=observed,
        canonical=observed,
        excerpt=observed,
        applicability="not_applicable",
        basis=basis,
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role=role,
        occurrence=1,
    )


def _audio_track(
    ordinal: int, digest: str, *, claims: tuple[str, ...] = ()
) -> AudioTrackDigest:
    return AudioTrackDigest(
        ordinal,
        f"{ordinal:064x}",
        digest,
        "m4b",
        f"part-{ordinal}.m4b",
        ordinal,
        60_000,
        claims,
    )


# Every audio section names the files it was composed from, so the default is
# the one-track manifest a single-file report publishes.
_ONE_TRACK_AUDIO_FACTS = AudioFacts(tracks=(_audio_track(1, "b" * 64),))


def _audio_report(
    claims: tuple[LocalClaim, ...] = (),
    *,
    providers: tuple[ProviderObservation, ...] = (),
    audio: AudioFacts | None = _ONE_TRACK_AUDIO_FACTS,
    file_format: str = "m4b",
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", default_target_fields("Recorded title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, file_format),
        local_claims=claims,
        local_reasons=("no_local_audio_claims",) if not claims else ("embedded_tag",),
        providers=providers,
        audio=audio,
    )


def test_an_audio_report_refuses_any_provider_observation() -> None:
    with pytest.raises(ReportValueError):
        _audio_report(
            providers=(
                ProviderObservation(
                    "catalogue", ProviderStatus.NO_MATCH, "adapter", "generation"
                ),
            )
        )


@pytest.mark.parametrize(
    ("file_format", "audio"),
    (
        ("m4b", None),
        ("epub", _ONE_TRACK_AUDIO_FACTS),
    ),
    ids=("audio-file-without-section", "ebook-with-audio-section"),
)
def test_the_audio_section_must_match_the_selected_file(
    file_format: str, audio: AudioFacts | None
) -> None:
    with pytest.raises(ReportValueError):
        _audio_report(audio=audio, file_format=file_format)


def test_an_audio_report_round_trips_its_structured_section() -> None:
    report = _audio_report(
        (_audio_claim(ClaimKind.DURATION, "43200.000"),),
        audio=AudioFacts(
            (AudioChapter(1, 0, 10_000, "One"), AudioChapter(2, 10_000, 20_000)),
            True,
            AudioEmbeddedCover(True, AudioEvidenceSurface.CONTAINER_STREAM),
            tracks=(_audio_track(1, "b" * 64),),
        ),
    )

    reopened = MetadataReport.from_json(report.to_json())

    assert reopened.to_json() == report.to_json()
    assert reopened.audio is not None
    assert reopened.audio.chapters_truncated is True
    assert reopened.audio.embedded_cover.present is True
    assert [value.title for value in reopened.audio.chapters] == ["One", None]


@pytest.mark.parametrize(
    "chapters",
    (
        (AudioChapter(2, 0, 1_000),),
        (AudioChapter(1, 0, 1_000), AudioChapter(3, 1_000, 2_000)),
    ),
    ids=("first-ordinal", "gap"),
)
def test_audio_chapter_ordinals_must_be_contiguous(
    chapters: tuple[AudioChapter, ...],
) -> None:
    with pytest.raises(ReportValueError):
        AudioFacts(chapters, tracks=(_audio_track(1, "b" * 64),))


def test_an_embedded_cover_is_never_a_cover_source() -> None:
    report = _audio_report(
        audio=AudioFacts(
            embedded_cover=AudioEmbeddedCover(
                True, AudioEvidenceSurface.CONTAINER_STREAM
            ),
            tracks=(_audio_track(1, "b" * 64),),
        )
    )

    assert report.cover_sources == ()
    assert report.audio is not None
    assert report.audio.embedded_cover.present is True


def test_one_track_keeps_its_own_identity_and_a_set_composes_a_new_one() -> None:
    """The identity is what the pipeline binds a report's work to.

    A one-track set has to stay its own track's identity or every existing
    single-file report becomes a report of a different item. Beyond one track
    the identity is the ordered list, so adding, removing, reordering, or
    replacing any track moves it exactly once.
    """
    first, second = f"{1:064x}", f"{2:064x}"

    assert audio_set_identity((first,)) == first
    composed = audio_set_identity((first, second))
    assert composed not in {first, second}
    assert composed != audio_set_identity((second, first))
    for invalid in ((), "not-a-tuple", (first, "short")):
        with pytest.raises(ReportValueError):
            audio_set_identity(cast(tuple[str, ...], invalid))


def test_a_single_file_audio_section_still_names_the_one_file_it_composed() -> None:
    """A v35 single-file report is the one-track case of the composed shape.

    It keeps every v34 key and adds a composition kind and a one-entry
    manifest, so a replay of the same mount reproduces the same document.
    """
    audio = AudioFacts(tracks=(_audio_track(1, "b" * 64),))
    report = _audio_report(audio=audio)

    assert report.audio is not None
    assert report.audio.composition is AudioChapterComposition.SINGLE_FILE
    assert len(report.audio.tracks) == 1
    assert report.audio.observations == ()
    assert MetadataReport.from_json(report.to_json()) == report
    assert _audio_report(audio=audio).to_json() == report.to_json()


def test_a_composed_audio_section_round_trips_its_manifest_and_observations() -> None:
    claim = _audio_claim(ClaimKind.SERIES, "Discworld")
    digests = (f"{1:064x}", f"{2:064x}")
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", default_target_fields("Recorded title")
        ),
        file=FileFacts(0, "a" * 64, audio_set_content_digest(digests), "m4b"),
        local_claims=(claim,),
        local_reasons=("embedded_tag",),
        providers=(),
        audio=AudioFacts(
            composition=AudioChapterComposition.PER_TRACK,
            tracks=(
                _audio_track(1, digests[0], claims=(claim.reference,)),
                _audio_track(2, digests[1], claims=(claim.reference,)),
            ),
            observations=(
                AudioObservation(AudioObservationKind.TRACK_SEQUENCE_GAP, (2,), None),
            ),
        ),
    )

    reopened = MetadataReport.from_json(report.to_json())

    assert reopened == report
    assert reopened.audio is not None
    assert [value.claims for value in reopened.audio.tracks] == [
        (claim.reference,),
        (claim.reference,),
    ]
    assert reopened.audio.observations[0].kind is (
        AudioObservationKind.TRACK_SEQUENCE_GAP
    )


def _work_scope_candidate_lane(identity: str) -> ProviderObservation:
    """One retained candidate, work scope only, so a citation can resolve to it.

    A candidate the runtime band did not corroborate keeps its identity and
    loses its edition-scope claims, which is the exact shape preparation's own
    truncation record is read against. This is a fixture correction and proves
    nothing on its own: the citation rule itself is proved in
    `tests/unit/test_metadata_report_persistence.py`. It exists here so the
    vocabulary test below cites an edition the report actually retains, as any
    real report does.
    """
    return ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        (),
        (
            ProviderCandidate(
                identity,
                "unresolved",
                (
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate=identity,
                        kind=ClaimKind.TITLE,
                        value="Recorded title",
                        state=FieldState.VALID,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                    ),
                ),
                (),
            ),
        ),
        ProviderQueryKind.ASIN,
        "audible_catalog",
    )


def test_every_observation_the_vocabulary_names_survives_the_document() -> None:
    """The vocabulary is closed, so the document has to admit all of it.

    An observation a composition can state but a report cannot carry would be
    evidence that exists only until it is written down.
    """
    kinds = tuple(AudioObservationKind)
    report = _audio_report(
        providers=(_work_scope_candidate_lane("candidate-1"),),
        audio=AudioFacts(
            tracks=(_audio_track(1, "b" * 64),),
            observations=tuple(
                AudioObservation(kind, (1,), None, "candidate-1") for kind in kinds
            ),
        ),
    )

    reopened = MetadataReport.from_json(report.to_json())

    assert reopened.audio is not None
    assert tuple(value.kind for value in reopened.audio.observations) == kinds
    # The candidate citation is part of the closed shape, so every kind has to
    # survive carrying one even though only the truncation kind ever sets it.
    assert {value.candidate for value in reopened.audio.observations} == {"candidate-1"}
    assert len(kinds) == 11


def test_the_item_content_digest_must_compose_the_manifest_it_names() -> None:
    """A digest that is not the composition of the tracks describes no item."""
    with pytest.raises(ReportValueError):
        _audio_report(
            audio=AudioFacts(tracks=(_audio_track(1, "c" * 64),)),
        )


def test_a_track_may_not_name_a_claim_the_report_does_not_carry() -> None:
    with pytest.raises(ReportValueError):
        _audio_report(
            audio=AudioFacts(
                tracks=(_audio_track(1, "b" * 64, claims=(f"local_{0:064x}",)),)
            ),
        )


@pytest.mark.parametrize(
    "build",
    (
        lambda: AudioFacts(
            composition=AudioChapterComposition.PER_TRACK,
            tracks=(_audio_track(1, "b" * 64),),
        ),
        lambda: AudioFacts(tracks=(_audio_track(2, "b" * 64),)),
        lambda: AudioFacts(
            composition=AudioChapterComposition.SINGLE_FILE,
            tracks=(_audio_track(1, "b" * 64), _audio_track(2, "c" * 64)),
        ),
        lambda: AudioFacts(
            tracks=(_audio_track(1, "b" * 64),),
            observations=(
                AudioObservation(AudioObservationKind.TRACK_SEQUENCE_GAP, (2,)),
            ),
        ),
        AudioFacts,
    ),
    ids=(
        "kind-disagrees-with-one-track",
        "ordinals-not-contiguous",
        "single-file-kind-with-two-tracks",
        "observation-names-an-absent-track",
        "no-track-at-all",
    ),
)
def test_the_audio_manifest_refuses_a_set_it_cannot_describe(
    build: Callable[[], AudioFacts],
) -> None:
    with pytest.raises(ReportValueError):
        build()


def test_an_isbn_shaped_asin_is_retained_as_local_evidence() -> None:
    report = _audio_report((_audio_claim(ClaimKind.ASIN, "0641977697", "path"),))

    assert [value.observed for value in report.local_claims] == ["0641977697"]
    assert MetadataReport.from_json(report.to_json()).local_claims == (
        report.local_claims
    )


@pytest.mark.parametrize(
    "kind",
    (ClaimKind.DURATION, ClaimKind.SERIES, ClaimKind.CONTRIBUTOR),
    ids=("duration", "series", "contributor"),
)
def test_an_abs_asserted_field_outside_title_and_asin_is_still_refused(
    kind: ClaimKind,
) -> None:
    with pytest.raises(ReportValueError):
        _audio_claim(kind, "asserted", "target_metadata")


def test_an_asserted_asin_is_a_target_metadata_claim_reported_valid() -> None:
    """The assertion is citable evidence, and nothing more than that.

    It is stated on its own surface with its own basis, so the report can name
    where a fed ASIN lane came from. `TARGET_METADATA` reports `valid` for the
    ASIN because a claim of that kind is present, and stays `unsupported` for
    every kind that surface still cannot carry.
    """
    asserted = _audio_claim(ClaimKind.ASIN, "B0CW1KL2QD", "target_metadata")
    report = _audio_report((asserted,))

    assert ClaimScope.EDITION is asserted.scope
    assert "target_metadata" == asserted.basis
    asin = next(value for value in report.field_results if value.kind is ClaimKind.ASIN)
    assert (FieldSource.TARGET_METADATA, FieldState.VALID) in {
        (value.source, value.state) for value in asin.sources
    }
    duration = next(
        value for value in report.field_results if value.kind is ClaimKind.DURATION
    )
    assert (FieldSource.TARGET_METADATA, FieldState.UNSUPPORTED) in {
        (value.source, value.state) for value in duration.sources
    }
    assert MetadataReport.from_json(report.to_json()).local_claims == (asserted,)


@pytest.mark.parametrize(
    "kind",
    (ClaimKind.DURATION, ClaimKind.PERFORMANCE_TYPE),
    ids=("duration", "performance"),
)
def test_recorded_manifestation_fields_are_edition_scoped(kind: ClaimKind) -> None:
    assert claim_scope(kind) is ClaimScope.EDITION


def _audio_asin(observed: str, canonical: str | None, state: FieldState) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=observed,
        canonical=canonical,
        excerpt=f"asin: {observed}",
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        state=state,
    )


def test_one_malformed_asin_surface_reports_malformed_beside_a_valid_one() -> None:
    """Deliberate: the owner is told a surface carried junk, not only that
    another surface carried something usable."""
    results = derive_field_results(
        (
            _audio_asin("B0CW1KL2QD", "B0CW1KL2QD", FieldState.VALID),
            _audio_asin("B0CW1KL-QD", None, FieldState.MALFORMED),
        )
    )

    asin = next(value for value in results if value.kind is ClaimKind.ASIN)
    assert asin.state is FieldState.MALFORMED
    assert (FieldSource.AUDIO_TAG, FieldState.MALFORMED) in {
        (value.source, value.state) for value in asin.sources
    }


def test_a_malformed_asin_is_never_an_alternative_a_proposal_can_offer() -> None:
    """The gate is upstream of every decision surface, so nothing has to
    re-derive it: proposals already admit only valid, canonical claims."""
    from tests.support.audiobook_reports import (
        admitted_audiobook_report,
        audio_local_claims,
    )

    report = admitted_audiobook_report(
        audio_local_claims()
        + (
            _audio_asin("B08G9PRS1K", "B08G9PRS1K", FieldState.VALID),
            _audio_asin("B0CW1KL-QD", None, FieldState.MALFORMED),
        )
    )

    asin = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.ASIN
    )

    assert [value.value.value for value in asin.alternatives] == ["B08G9PRS1K"]


def test_recording_lookup_roundtrip_retains_only_complete_narrator_agreement() -> None:
    from mechanical_librarian.application.metadata_report import (
        admitted_audiobook_identity,
    )
    from mechanical_librarian.domain.review_case import ReviewCase
    from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
    from mechanical_librarian.domain.shadow_review import ShadowReviewPacket

    report = recording_lookup_report()
    reopened = MetadataReport.from_json(report.to_json())
    assert reopened == report
    observation = reopened.providers[0]
    assert len(observation.recording_lookup_candidates) == 2
    assert observation.status is ProviderStatus.NO_MATCH
    assert observation.candidates == ()
    assert observation.dispatch_lineage is None
    assert observation.transformation_lineage is None
    assert not admitted_audiobook_identity(reopened)
    empty = MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=(),
        audio=report.audio,
    )
    assert report.identifier_evidence == empty.identifier_evidence
    # Complete plausible casts may agree without electing either sale product.
    results = evaluate_report(report).results
    assert tuple(
        value
        for value in results
        if value.destination is not TargetDestination.NARRATORS
    ) == tuple(
        value
        for value in evaluate_report(empty).results
        if value.destination is not TargetDestination.NARRATORS
    )
    narration = next(
        value for value in results if value.destination is TargetDestination.NARRATORS
    )
    from mechanical_librarian.domain.proposal_set import (
        _plausible_narrator_candidates,
        narrator_candidate_set_agrees,
    )

    assert not narration.alternatives
    plausible = _plausible_narrator_candidates(reopened, observation)
    assert tuple(plausible) == observation.recording_lookup_candidates
    assert all(
        tuple(
            claim.value
            for claim in candidate.claims
            if claim.kind is ClaimKind.CONTRIBUTOR
        )
        == ("Ray Porter",)
        for candidate in plausible
    )
    member_references = [
        {
            claim.reference
            for claim in candidate.claims
            if claim.kind is ClaimKind.CONTRIBUTOR
        }
        for candidate in observation.recording_lookup_candidates
    ]
    assert len(member_references) == 2 and all(member_references)
    references = tuple(sorted(set().union(*member_references)))
    assert narrator_candidate_set_agrees(reopened, references)
    assert not narrator_candidate_set_agrees(reopened, references[:-1])
    assert (
        evaluate_report(report).bibliographic_combinations
        == evaluate_report(empty).bibliographic_combinations
    )

    proposal = evaluate_report(reopened)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, reopened), proposal, reopened
    )
    assert not any(
        value.destination == "narrators"
        and value.recommendation == "recommend_candidate"
        for value in decide_shadow_packet(
            ShadowReviewPacket.from_json(packet.to_json(), reopened, proposal)
        ).decisions
    )


@pytest.mark.parametrize(
    "variant",
    (
        "missing",
        "null",
        "reordered",
        "duplicate",
        "foreign",
        "nonwork",
        "ordinary",
        "reference",
        "wrong_work",
    ),
)
def test_recording_lookup_strict_replay_refuses_invalid_evidence(variant: str) -> None:
    report = recording_lookup_report()
    data = report.to_data()
    providers = cast(list[dict[str, object]], data["providers"])
    observation = providers[0]
    candidates = cast(
        list[dict[str, object]], observation["recording_lookup_candidates"]
    )
    if variant == "missing":
        del observation["recording_lookup_candidates"]
    elif variant == "null":
        observation["recording_lookup_candidates"] = None
    elif variant == "reordered":
        candidates.reverse()
    elif variant == "duplicate":
        candidates.append(candidates[0])
    elif variant == "foreign":
        observation["authority"] = "google_books"
    elif variant == "nonwork":
        observation["query_kind"] = "asin"
    elif variant == "ordinary":
        observation["candidates"] = candidates
    elif variant == "reference":
        cast(list[dict[str, object]], candidates[0]["claims"])[0]["reference"] = (
            "provider_" + "0" * 24
        )
    else:
        target = cast(dict[str, object], data["target"])
        fields = cast(list[dict[str, object]], target["fields"])
        next(field for field in fields if field["destination"] == "authors")[
            "value"
        ] = ["Different Author"]
    # Recompute identity so refusal proves the nested consumer, not a stale digest.
    import hashlib

    identity = dict(data)
    identity.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(data))


def test_lookup_work_lane_and_sqlite_proposal_roundtrip(tmp_path: Path) -> None:
    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        provider_observation,
    )
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from tests.support.audiobook_providers import work_plan

    raw = recording_lookup_report()
    plan = work_plan(
        *(claim for claim in raw.local_claims if claim.basis != "target_metadata")
    )
    lane = plan.lanes[0]
    observation = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                lane,
                ProviderStatus.NO_MATCH,
                recording_lookup_candidates=raw.providers[
                    0
                ].recording_lookup_candidates,
            ),
        ),
        authority="audnexus",
    )
    assert observation.lanes[0].status is ProviderStatus.NO_MATCH
    assert observation.lanes[0].candidates == ()
    assert observation.lanes[0].dispatch_lineage is None
    assert observation.input_references == lane.input_references
    report = MetadataReport.create(
        target=raw.target,
        file=raw.file,
        local_claims=plan.local_claims,
        local_reasons=raw.local_reasons,
        providers=(recorded_dispatch(observation),),
        audio=raw.audio,
    )
    database = tmp_path / "reports.sqlite"
    repository = open_repository(database, create=True)
    try:
        assert repository.publish(report, lambda: True) == report.to_json()
        proposal = evaluate_report(
            MetadataReport.from_json(repository.get_proposal_input(report.report_id))
        )
        assert ProposalSet.from_json(repository.publish_proposal(proposal)) == proposal
    finally:
        repository.close()
    repository = open_repository(database, create=False)
    try:
        assert MetadataReport.from_json(repository.get(report.report_id)) == report
        assert (
            ProposalSet.from_json(repository.get_proposal(proposal.proposal_set_id))
            == proposal
        )
    finally:
        repository.close()
    with pytest.raises(ReportValueError, match="lookup provenance"):
        replace(
            observation,
            lanes=(replace(observation.lanes[0], status=ProviderStatus.UNAVAILABLE),),
        )


@pytest.mark.parametrize(
    "variant",
    (
        "wrong_author",
        "variant_title",
        "variant_subtitle",
        "abridger",
        "adapter",
        "translator",
        "overflow",
    ),
)
def test_lookup_candidates_replay_individual_identity_and_bound_guards(
    variant: str,
) -> None:
    report = recording_lookup_report()
    observation = report.providers[0]
    candidate = observation.recording_lookup_candidates[0]
    if variant == "overflow":
        with pytest.raises(ReportValueError):
            replace(
                observation,
                recording_lookup_candidates=observation.recording_lookup_candidates * 3,
            )
        return
    if variant in {"wrong_author", "variant_title"}:
        kind = ClaimKind.CREATOR if variant == "wrong_author" else ClaimKind.TITLE
        source = next(claim for claim in candidate.claims if claim.kind is kind)
        changed = ProviderClaim.create(
            provider=observation.provider,
            candidate=candidate.identity,
            kind=kind,
            value="Different Author"
            if variant == "wrong_author"
            else "Project Hail Mary (Abridged)",
            state=source.state,
            scope=source.scope,
            applicability=source.applicability,
            role=source.role,
            raw_order=source.raw_order,
        )
        claims = tuple(
            changed if claim is source else claim for claim in candidate.claims
        )
    else:
        claims = (
            *candidate.claims,
            ProviderClaim.create(
                provider=observation.provider,
                candidate=candidate.identity,
                kind=ClaimKind.SUBTITLE
                if variant == "variant_subtitle"
                else ClaimKind.CONTRIBUTOR,
                value="Abridged edition"
                if variant == "variant_subtitle"
                else "Other Person",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=None if variant == "variant_subtitle" else variant,
                raw_order=len(candidate.claims),
            ),
        )
    candidate = replace(candidate, claims=claims)
    observation = replace(
        observation,
        recording_lookup_candidates=(
            candidate,
            *observation.recording_lookup_candidates[1:],
        ),
    )
    with pytest.raises(ReportValueError, match="lookup work identity"):
        MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=(observation,),
            audio=report.audio,
        )


def test_actual_audiobook_predecessors_refuse_current_document_consumers() -> None:
    from pathlib import Path

    from mechanical_librarian.domain.proposal_set import (
        ProposalSet,
        ProposalValueError,
        evaluate_report,
    )
    from mechanical_librarian.domain.review_case import ReviewCase
    from mechanical_librarian.domain.shadow_review import (
        ShadowReviewPacket,
        ShadowReviewValueError,
    )
    from tests.support.audiobook_reports import admitted_audiobook_report

    root = Path(__file__).parents[1] / "support" / "audiobook_predecessors"
    report = admitted_audiobook_report()
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    assert MetadataReport.from_json(report.to_json()) == report
    assert ProposalSet.from_json(proposal.to_json()) == proposal
    assert ShadowReviewPacket.from_json(packet.to_json(), report, proposal) == packet
    with pytest.raises(ReportValueError):
        MetadataReport.from_json((root / "report.json").read_text())
    with pytest.raises(ProposalValueError):
        ProposalSet.from_json((root / "proposal.json").read_text())
    with pytest.raises(ShadowReviewValueError):
        ShadowReviewPacket.from_json(
            (root / "packet.json").read_text(), report, proposal
        )


def test_incumbent_paragraph_roundtrip_keeps_source_fingerprint_exact() -> None:
    from mechanical_librarian.domain.field_effect import field_value_version
    from mechanical_librarian.domain.field_value_comparison import values_match
    from mechanical_librarian.domain.metadata_report import TargetDestination
    from tests.support.audiobookshelf_targets import target_snapshot

    current = " \tCafé first paragraph.\r\n\r\nSecond paragraph.\t "
    changed = current.replace("\r\n", "\n")
    first = TargetFacts(
        "primary",
        "ebooks",
        "item-1",
        target_snapshot({"title": "Title", "description": current}).fields,
    )
    second = TargetFacts(
        "primary",
        "ebooks",
        "item-1",
        target_snapshot({"title": "Title", "description": changed}).fields,
    )
    assert TargetFacts.from_data(first.to_data()) == first
    assert first.fingerprint != second.fingerprint
    assert field_value_version("description", current) != field_value_version(
        "description", changed
    )
    assert values_match(TargetDestination.DESCRIPTION, current, changed)


@pytest.mark.parametrize("kind", [ClaimKind.TITLE, ClaimKind.SUBTITLE])
@pytest.mark.parametrize("value", ["Example\u200b Book", "\u200b", "Example\u2060Book"])
def test_valid_provider_title_requires_comparison_safe_text(
    kind: ClaimKind, value: str
) -> None:
    with pytest.raises(ReportValueError, match="title is invalid"):
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        )


@pytest.mark.parametrize("kind", [ClaimKind.TITLE, ClaimKind.SUBTITLE])
@pytest.mark.parametrize(
    "value", ["Café — 日本語!", "Cafe\u0301", "A: Different Title"]
)
def test_valid_provider_title_retains_exact_unicode(
    kind: ClaimKind, value: str
) -> None:
    claim = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
    )
    assert claim.value == value


def _hinted_admission(
    hints: tuple[str, ...] = ("B000000011",),
) -> MetadataReport:
    """An asserted lane admitted on its one rung beside band-first hints (#1257)."""
    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        provider_observation,
    )
    from mechanical_librarian.domain.metadata_report import (
        FileFacts,
        RecordingLookupHint,
        TargetFacts,
        WorkDispatchLineage,
    )
    from mechanical_librarian.domain.work_identity import work_query_ladder
    from tests.support.audiobook_providers import work_plan
    from tests.support.audiobook_reports import (
        audio_local_claims,
        audio_target_fields,
        audiobook_candidate,
        recorded_dispatch,
    )
    from tests.support.metadata_report_doubles import single_track_audio_facts

    plan = work_plan(*audio_local_claims())
    rung = WorkDispatchLineage.from_query(
        work_query_ladder("Project Hail Mary", ("Andy Weir",), audiobook=True)[0]
    )
    candidate = audiobook_candidate(
        plan.local_claims, provider="audible", identity="audible-product-B08G9PRS1K"
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("embedded_tag_duration",),
        providers=(
            recorded_dispatch(
                provider_observation(
                    "audible",
                    (
                        ProviderLaneResult(
                            plan.lanes[0],
                            ProviderStatus.CANDIDATES,
                            (candidate,),
                            dispatch_lineage=rung,
                            recording_lookup_hints=tuple(
                                RecordingLookupHint(asin, 58_200, rung)
                                for asin in hints
                            ),
                        ),
                    ),
                    authority="audible_catalog",
                )
            ),
        ),
        audio=single_track_audio_facts(),
    )


def _readdressed(data: dict[str, object]) -> str:
    import hashlib

    identity = dict(data)
    identity.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    )
    return canonical_json(data)


def _hint_lane(data: dict[str, object], provider: int = 0) -> dict[str, object]:
    providers = cast(list[dict[str, object]], data["providers"])
    lanes = cast(list[dict[str, object]], providers[provider]["lanes"])
    return lanes[0]


def _hint_at(data: dict[str, object], provider: int = 0) -> dict[str, object]:
    hints = cast(
        list[dict[str, object]], _hint_lane(data, provider)["recording_lookup_hints"]
    )
    return hints[0]


def _other_rung() -> dict[str, object]:
    from mechanical_librarian.domain.metadata_report import WorkDispatchLineage
    from mechanical_librarian.domain.work_identity import work_query_ladder

    return WorkDispatchLineage.from_query(
        work_query_ladder("Project Hail Mary: A Novel", ("Andy Weir",), audiobook=True)[
            1
        ]
    ).to_data()


def _foreign(data: dict[str, object]) -> None:
    cast(list[dict[str, object]], data["providers"])[0]["authority"] = "google_books"


def _audnexus(data: dict[str, object]) -> None:
    cast(list[dict[str, object]], data["providers"])[0]["authority"] = "audnexus"


def _itunes(data: dict[str, object]) -> None:
    cast(list[dict[str, object]], data["providers"])[0]["authority"] = "itunes_search"


def _duplicated(data: dict[str, object]) -> None:
    _hint_at(data)["asin"] = "B000000001"


def _out_of_band(data: dict[str, object]) -> None:
    _hint_at(data)["runtime_seconds"] = 58_244 + 91


def _admitted_duplicate(data: dict[str, object]) -> None:
    _hint_at(data)["asin"] = "B08G9PRS1K"


def _unladdered_rung(data: dict[str, object]) -> None:
    _hint_at(data)["rung"] = _other_rung()


def _unladdered_dispatch(data: dict[str, object]) -> None:
    lineage = cast(dict[str, object], _hint_lane(data)["dispatch_lineage"])
    lineage["candidate_limit_narrowing_retry"] = True


def _second_audible(data: dict[str, object]) -> None:
    from tests.support.audiobook_reports import recording_lookup_hint_report

    second = recording_lookup_hint_report(
        hint_asins=("B000000021", "B000000022"), provider="audible-second"
    )
    cast(list[object], data["providers"]).extend(
        json.loads(second.to_json())["providers"]
    )


def _three_hints(data: dict[str, object]) -> None:
    hints = cast(list[object], _hint_lane(data)["recording_lookup_hints"])
    rung = cast(dict[str, object], hints[0])["rung"]
    hints.extend(
        {
            "asin": asin,
            "basis": "runtime_within_tight_band",
            "rung": rung,
            "runtime_seconds": 58_200,
        }
        for asin in ("B000000021", "B000000022")
    )


def _hint_base(base: str) -> MetadataReport:
    from tests.support.audiobook_reports import recording_lookup_hint_report

    if base == "admitted":
        return _hinted_admission()
    return recording_lookup_hint_report(lookup_asins=("B000000001",))


@pytest.mark.parametrize(
    ("base", "mutations", "message"),
    (
        ("admitted", (_foreign,), "recording lookup hint provenance is invalid"),
        ("admitted", (_audnexus,), "recording lookup hint provenance is invalid"),
        ("admitted", (_itunes,), "recording lookup hint provenance is invalid"),
        ("lookup", (_audnexus,), "recording lookup hint provenance is invalid"),
        ("lookup", (_itunes,), "recording lookup hint provenance is invalid"),
        (
            "admitted",
            (_foreign, _admitted_duplicate, _out_of_band),
            "recording lookup hint provenance is invalid",
        ),
        ("admitted", (_unladdered_rung,), "recording lookup hint rung is inconsistent"),
        (
            "admitted",
            (_unladdered_dispatch,),
            "recording lookup hint rung is inconsistent",
        ),
        (
            "admitted",
            (_unladdered_rung, _admitted_duplicate),
            "recording lookup hint rung is inconsistent",
        ),
        ("admitted", (_admitted_duplicate,), "duplicates a retained product"),
        ("lookup", (_duplicated,), "duplicates a retained product"),
        ("lookup", (_duplicated, _out_of_band), "duplicates a retained product"),
        ("lookup", (_out_of_band,), "recording lookup hint is outside the tight band"),
        (
            "lookup",
            (_out_of_band, _second_audible),
            "recording lookup hint is outside the tight band",
        ),
        ("lookup", (_three_hints,), "provider lane recording lookup hints are invalid"),
    ),
    ids=(
        "foreign_authority",
        "audnexus_authority",
        "itunes_authority",
        "audnexus_lookup_authority",
        "itunes_lookup_authority",
        "provenance_first",
        "rung_outside_ladder",
        "admission_outside_ladder",
        "rung_before_duplicate",
        "admitted_asin",
        "exact_lookup_asin",
        "duplicate_before_band",
        "outside_band",
        "band_with_a_second_provider",
        "over_provider_bound",
    ),
)
def test_a_report_replays_its_recording_lookup_hints_in_precedence_order(
    base: str,
    mutations: tuple[Callable[[dict[str, object]], None], ...],
    message: str,
) -> None:
    report = _hint_base(base)
    assert report == MetadataReport.from_json(report.to_json())
    data = cast(dict[str, object], json.loads(report.to_json()))
    for mutate in mutations:
        mutate(data)
    with pytest.raises(ReportValueError, match=message):
        MetadataReport.from_json(_readdressed(data))


def test_two_audible_providers_each_keep_their_own_hint_bound() -> None:
    """The bound is per Audible observation: each has one asserted lane."""
    from tests.support.audiobook_reports import recording_lookup_hint_report

    data = cast(
        dict[str, object],
        json.loads(
            recording_lookup_hint_report(
                hint_asins=("B000000011", "B000000012")
            ).to_json()
        ),
    )
    _second_audible(data)
    report = MetadataReport.from_json(_readdressed(data))

    assert 4 == sum(
        len(lane.recording_lookup_hints)
        for provider in report.providers
        for lane in provider.lanes
    )
    assert report == MetadataReport.from_json(report.to_json())


def test_an_ebook_report_carries_no_recording_lookup_hint() -> None:
    data = cast(dict[str, object], json.loads(_hinted_admission().to_json()))
    data["audio"] = None
    cast(dict[str, object], data["file"])["format"] = "epub"
    with pytest.raises(
        ReportValueError, match="recording lookup hint provenance is invalid"
    ):
        MetadataReport.from_json(_readdressed(data))


def test_the_alternative_lane_cannot_carry_a_forged_hint() -> None:
    """The #1255 rule holds for hints: only the asserted lane states them."""
    from mechanical_librarian.domain.metadata_report import (
        FileFacts,
        ProviderLaneObservation,
        ProviderObservation,
        ProviderQueryKind,
        RecordingLookupHint,
        TargetFacts,
        WorkDispatchLineage,
        work_ladder_diagnostics,
    )
    from mechanical_librarian.domain.work_identity import work_query_ladder
    from tests.support.audiobook_providers import (
        alternative_work_plan,
        audio_target,
        local_runtime_claim,
    )

    plan = alternative_work_plan()
    local = (*plan.local_claims, local_runtime_claim())
    lanes = []
    for lane in plan.lanes:
        ladder = tuple(
            WorkDispatchLineage.from_query(query)
            for query in work_query_ladder(
                lane.title or "", lane.authors, audiobook=True
            )
        )
        hints = (
            (RecordingLookupHint("B000000011", 58_200, ladder[0]),)
            if lane.title_basis is not None
            else ()
        )
        lanes.append(
            ProviderLaneObservation(
                lane.identity,
                ProviderQueryKind.WORK,
                ProviderStatus.NO_MATCH,
                lane.input_references,
                diagnostics=work_ladder_diagnostics(0, len(ladder)),
                exhausted_dispatch=ladder,
                recording_lookup_hints=hints,
            )
        )
    lanes.sort(key=lambda value: value.identity)
    diagnostics = work_ladder_diagnostics(
        0, sum(len(v.exhausted_dispatch) for v in lanes)
    )
    with pytest.raises(
        ReportValueError, match="recording lookup hint provenance is invalid"
    ):
        MetadataReport.create(
            target=TargetFacts(
                "primary",
                "audio",
                "item-1",
                audio_target("Asserted Title", ("Ada Author",)).fields,
            ),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=local,
            local_reasons=("target_metadata_title",),
            providers=(
                ProviderObservation(
                    "audible",
                    ProviderStatus.NO_MATCH,
                    tuple(sorted({r for v in lanes for r in v.input_references})),
                    query_kind=ProviderQueryKind.DISCOVERY,
                    authority="audible_catalog",
                    lanes=tuple(lanes),
                    diagnostics=diagnostics,
                ),
            ),
            audio=plan.audio,
        )


@pytest.mark.parametrize("admitted", (False, True))
def test_hints_leave_every_decision_reader_unchanged(admitted: bool) -> None:
    """A hint asserts nothing: proposals, basis and scope ignore it (#1257)."""
    from mechanical_librarian.domain.audiobook_corroboration import (
        recording_identity_basis,
    )
    from mechanical_librarian.domain.proposal_set import audiobook_review_scope
    from tests.support.audiobook_reports import recording_lookup_hint_report

    if admitted:
        hinted, bare = _hinted_admission(), _hinted_admission(())
    else:
        hinted = recording_lookup_hint_report(lookup_asins=("B000000001",))
        bare = recording_lookup_hint_report(lookup_asins=("B000000001",), hint_asins=())
    assert hinted.report_id != bare.report_id
    assert hinted.providers[0].lanes[0].recording_lookup_hints
    assert recording_identity_basis(hinted) == recording_identity_basis(bare)
    assert audiobook_review_scope(hinted) == audiobook_review_scope(bare)
    assert hinted.identifier_evidence == bare.identifier_evidence
    assert [value.to_data() for value in evaluate_report(hinted).results] == [
        value.to_data() for value in evaluate_report(bare).results
    ]


def _starved_audible(duration: str | None = "58244.201") -> MetadataReport:
    """An asserted Audible lane the deadline stopped before its first rung."""
    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        ProviderResult,
        provider_attempt_diagnostics,
        provider_observation,
        with_lane_dispatch,
    )
    from mechanical_librarian.application.provider_evidence import (
        ProviderIncompleteCause,
        ProviderIncompleteCauseRecord,
        ProviderOperationalEvidence,
    )
    from mechanical_librarian.domain.metadata_report import TargetFacts
    from tests.support.audiobook_providers import work_plan
    from tests.support.audiobook_reports import (
        audio_local_claims,
        audio_target_fields,
    )
    from tests.support.metadata_report_doubles import single_track_audio_facts

    plan = work_plan(*audio_local_claims(duration))
    causes = (ProviderIncompleteCauseRecord(ProviderIncompleteCause.DEADLINE),)
    observation = provider_observation(
        "audible",
        (ProviderLaneResult(plan.lanes[0], ProviderStatus.INCOMPLETE),),
        authority="audible_catalog",
        diagnostics=provider_attempt_diagnostics(
            (), status="incomplete", incomplete_causes=causes
        ),
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("embedded_tag_duration",),
        providers=(
            with_lane_dispatch(
                observation,
                ProviderResult(
                    observation,
                    evidence=ProviderOperationalEvidence(incomplete_causes=causes),
                ),
                audio=True,
            ),
        ),
        audio=single_track_audio_facts(),
    )


def _dispatch_record(data: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], _hint_lane(data)["dispatch"])


def _absent_record(data: dict[str, object]) -> None:
    _hint_lane(data)["dispatch"] = None


def _off_table(data: dict[str, object]) -> None:
    _dispatch_record(data)["outcome"] = "overflow"


def _unbacked(data: dict[str, object]) -> None:
    cast(list[dict[str, object]], data["providers"])[0]["diagnostics"] = []


def _budget_spent(data: dict[str, object]) -> None:
    provider = cast(list[dict[str, object]], data["providers"])[0]
    cast(list[object], provider["diagnostics"]).insert(
        0,
        FailureDiagnostic(
            FailureCategory.LOOKUP_CONSTRUCTION,
            FailureComponent.PROVIDER,
            FailureStage.LOOKUP,
            retryable=False,
        ).to_data(),
    )


def _rungs(value: int) -> Callable[[dict[str, object]], None]:
    def mutate(data: dict[str, object]) -> None:
        _dispatch_record(data)["rungs"] = value

    return mutate


def _near_miss(value: int) -> Callable[[dict[str, object]], None]:
    def mutate(data: dict[str, object]) -> None:
        _dispatch_record(data)["near_miss_seconds"] = value

    return mutate


def _dispatch_base(base: str) -> MetadataReport:
    if base == "admitted":
        return _hinted_admission(hints=())
    if base == "unstated_runtime":
        return _starved_audible(duration=None)
    return _starved_audible()


@pytest.mark.parametrize(
    ("base", "mutations", "message"),
    (
        ("admitted", (_absent_record,), "provider lane dispatch presence is invalid"),
        ("starved", (_absent_record,), "provider lane dispatch presence is invalid"),
        ("admitted", (_off_table,), "provider lane dispatch is invalid"),
        ("starved", (_unbacked,), "provider lane starvation is unbacked"),
        ("starved", (_budget_spent,), "provider lane starvation is unbacked"),
        (
            "starved",
            (_unbacked, _rungs(99)),
            "provider lane starvation is unbacked",
        ),
        (
            "starved",
            (_rungs(1), _audnexus),
            "provider lane rungs provenance is invalid",
        ),
        ("starved", (_rungs(0),), "provider lane rungs do not replay"),
        ("starved", (_rungs(99),), "provider lane rungs do not replay"),
        ("admitted", (_rungs(2),), "provider lane rungs do not replay"),
        (
            "starved",
            (_rungs(99), _near_miss(600)),
            "provider lane rungs do not replay",
        ),
        (
            "starved",
            (_near_miss(1200), _audnexus),
            "provider lane near-miss provenance is invalid",
        ),
        (
            "unstated_runtime",
            (_near_miss(1200),),
            "provider lane near-miss provenance is invalid",
        ),
        ("starved", (_near_miss(600),), "provider lane near-miss is inside the loose"),
        ("starved", (_near_miss(-600),), "provider lane near-miss is inside the loose"),
        (
            "starved",
            (_near_miss(10**9),),
            "provider lane near-miss exceeds its bound",
        ),
    ),
    ids=(
        "absent_admitted",
        "absent_starved",
        "outcome_off_table",
        "starved_without_timeout",
        "starved_beside_a_spent_budget",
        "backing_before_rungs",
        "rungs_from_another_provider",
        "starved_without_a_rung",
        "starved_past_the_ladder",
        "admitted_past_its_rung",
        "rungs_before_near_miss",
        "near_miss_from_another_provider",
        "near_miss_without_a_runtime",
        "near_miss_inside_the_band_above",
        "near_miss_inside_the_band_below",
        "near_miss_past_its_bound",
    ),
)
def test_a_report_replays_its_lane_dispatch_records_in_precedence_order(
    base: str,
    mutations: tuple[Callable[[dict[str, object]], None], ...],
    message: str,
) -> None:
    report = _dispatch_base(base)
    assert report == MetadataReport.from_json(report.to_json())
    data = cast(dict[str, object], json.loads(report.to_json()))
    for mutate in mutations:
        mutate(data)
    with pytest.raises(ReportValueError, match=message):
        MetadataReport.from_json(_readdressed(data))


@pytest.mark.parametrize(
    "changes",
    (
        {"rungs": 1},
        {"near_miss_seconds": -1200},
        {"rungs": 1, "near_miss_seconds": 900},
    ),
    ids=("rungs", "near_miss", "both"),
)
def test_a_starved_lane_states_the_rungs_and_near_miss_it_reached(
    changes: dict[str, int],
) -> None:
    data = cast(dict[str, object], json.loads(_starved_audible().to_json()))
    _dispatch_record(data).update(changes)

    report = MetadataReport.from_json(_readdressed(data))

    dispatch = report.providers[0].lanes[0].dispatch
    assert dispatch is not None and dispatch.outcome.value == "starved"
    assert changes == {key: getattr(dispatch, key) for key in changes}


def test_report_replay_reasserts_a_tampered_lane_outcome() -> None:
    """A lane changed after construction is held to the table again."""
    from mechanical_librarian.domain.metadata_report import (
        DispatchOutcome,
        ProviderLaneDispatch,
    )

    report = _starved_audible()
    object.__setattr__(
        report.providers[0].lanes[0],
        "dispatch",
        ProviderLaneDispatch(DispatchOutcome.OVERFLOW),
    )
    with pytest.raises(ReportValueError, match="provider lane dispatch is invalid"):
        replace(report)
