"""Metadata-report evidence: provider diagnostics and access times,
typed date events, the closed claim and field vocabularies, and the
canonical content-addressed report.
"""

from __future__ import annotations

import json
import tempfile
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
    canonical_json,
    claim_scope,
    comparison_links,
    default_target_fields,
    derive_field_results,
    normalize_date_value,
    normalize_title,
    provider_access_time,
)
from mechanical_librarian.domain.work_identity import work_title_has_variant_marker
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
