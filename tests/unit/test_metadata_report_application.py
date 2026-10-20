"""Metadata-report application boundaries: currentness across every
destination, separate extraction and provider timings, diagnostic
merge on reopen, and publication preconditions.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    _entry as catalogue_entry,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_ebook_location import (
    normalize_ebook_location,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    AUDIOBOOK_DISPATCH_ADAPTERS,
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    AudioItemInspectionError,
    CredentialError,
    CurrentnessError,
    GenerateMetadataReport,
    InspectionResult,
    MetadataDiscoveryPlan,
    MetadataLookup,
    MetadataReportError,
    ProviderBinding,
    ProviderCoverDescriptor,
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderObservationFailure,
    ProviderResult,
    ReportRepository,
    TargetCover,
    TargetFile,
    TargetSnapshot,
    UnsupportedMediaError,
    provider_observation,
    qualify_provider_observation,
    require_report_current,
)
from mechanical_librarian.application.operations import StageTimer, StageTiming
from mechanical_librarian.application.provider_evidence import (
    ProviderIncompleteCause,
    ProviderIncompleteCauseRecord,
    ProviderOperationalEvidence,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    provider_recovery_jobs,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspectionOutcome,
    AudioSidecarDigest,
    AudioTrackDigest,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
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
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    DispatchOutcome,
    FieldSource,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderLaneDispatch,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    audio_set_content_digest,
    audio_set_identity,
    default_target_fields,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.rich_metadata_preview import (
    compile_rich_metadata_preview,
)
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    work_query_ladder,
)
from tests.support.hardcover_evidence import hardcover_envelope, isbn_claim
from tests.support.metadata_report_doubles import (
    FakeAudioInspector,
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
    audio_result,
    single_track_audio_facts,
)


def test_wrong_work_adapter_evidence_stays_inspectable_but_cannot_reopen_authority() -> (
    None
):
    requests: list[tuple[str, str]] = []

    payload = hardcover_envelope(
        [
            {
                "id": 1,
                "book_id": 10,
                "isbn_13": "9780306406157",
                "release_date": "2031-01-02",
            }
        ],
        [
            {
                "id": 10,
                "title": "Different Book",
                "release_date": "2031-01-02",
                "contributions": [
                    {
                        "id": 1,
                        "contribution": "Author",
                        "author": {"id": 7, "name": "Other Author"},
                    }
                ],
                "taggings": [
                    {
                        "id": 1,
                        "tag": {
                            "id": 11,
                            "tag": "Wrong Work Genre",
                            "tag_category": {"id": 2, "category": "Genre"},
                        },
                    }
                ],
            }
        ],
    )

    def provider(
        name: str,
    ) -> HardcoverMetadataProvider | GoogleBooksMetadataProvider:
        if name == "secondary_catalogue":

            def google_response(request: httpx.Request) -> httpx.Response:
                requests.append((name, request.url.params.get("q", "")))
                return httpx.Response(
                    200,
                    json={
                        "totalItems": 1,
                        "items": [
                            {
                                "id": "wrong_volume",
                                "volumeInfo": {
                                    "title": "Different Book",
                                    "authors": ["Other Author"],
                                    "publishedDate": "2031-01-02",
                                    "industryIdentifiers": [
                                        {
                                            "type": "ISBN_13",
                                            "identifier": "9780306406157",
                                        }
                                    ],
                                    "categories": ["Wrong Work Genre"],
                                    "printType": "BOOK",
                                    "language": "en",
                                },
                            }
                        ],
                    },
                )

            return GoogleBooksMetadataProvider(
                name,
                httpx.Client(transport=httpx.MockTransport(google_response)),
                65_536,
            )

        def hardcover_response(request: httpx.Request) -> httpx.Response:
            operation = json.loads(request.content)["operationName"]
            requests.append((name, operation))
            return httpx.Response(200, json=payload)

        return HardcoverMetadataProvider(
            name,
            httpx.Client(transport=httpx.MockTransport(hardcover_response)),
            65_536,
        )

    isbn = LocalClaim.create(
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
    publication_date = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2031-01-02",
        canonical="2031-01-02",
        excerpt="2031-01-02",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.DAY,
    )
    fields = tuple(
        replace(
            field,
            state=TargetFieldState.VALUE,
            value=("Ada Example",),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Example Book")
    )
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Example Book", (selected,), fields=fields)

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite3"
        report_document = GenerateMetadataReport(
            target=FakeTarget([snapshot, snapshot]),
            inspector=FakeInspector(
                InspectionResult("c" * 64, (isbn, publication_date))
            ),
            repository_factory=lambda: open_repository(database),
            providers=(
                ProviderBinding(
                    "catalogue",
                    True,
                    lambda: provider("catalogue"),
                    authority="hardcover",
                ),
                ProviderBinding(
                    "secondary_catalogue",
                    True,
                    lambda: provider("secondary_catalogue"),
                    authority="google_books",
                ),
            ),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("catalogue", "secondary_catalogue"),
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        ).run("item-1", 0)

        report = MetadataReport.from_json(report_document)
        report_id = report.report_id
        options = dict(DEFAULT_OPTIONS)
        options["genres"] = "fill_empty"
        destinations = {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        }
        destinations["genres"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "Wrong-work refusal", options, destinations
        )
        resolved = resolve_policy(profile, report.target.library)
        proposal = evaluate_report(
            report,
            resolved.resolved_id,
            human_review_destinations(resolved),
        )
        preview = compile_rich_metadata_preview(resolved, report, proposal)

        repository = open_repository(database)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (
                (
                    report.target.library,
                    resolved.resolved_id,
                    resolved.canonical_json,
                ),
            ),
            2,
            expected_active_generation=1,
        )
        repository.activate_qualified_automatic(3)
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        document = repository.persist_shadow_outcome(case_id, 4)
        outcome = ShadowOutcomeSet.from_json(document)
        genre_outcome = next(
            value for value in outcome.outcomes if value.destination == "genres"
        )
        decisions = repository.get_review_case_decisions(case_id)
        repository.close()

        repository = open_repository(database)
        assert repository.get(report_id) == report.to_json()
        assert repository.get_proposal(proposal.proposal_set_id) == proposal.to_json()
        assert repository.get_review_case_decisions(case_id) == decisions
        assert repository.persist_shadow_outcome(case_id, 5) == document
        assert repository.list_shadow_outcomes(1)[0][0] == document
        assert repository.pending_field_effect_decision_documents(10) == ()
        repository.close()

    genre = next(field for field in preview.fields if field.field == "genres")
    publication = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.PUBLISHED_DATE
    )
    assert requests == [
        ("catalogue", "MetadataEvidenceByIsbn"),
        ("catalogue", "MetadataEvidenceByWork"),
        ("secondary_catalogue", "isbn:9780306406157"),
        (
            "secondary_catalogue",
            'intitle:"Example Book" inauthor:"Ada Example"',
        ),
    ]
    assert [provider.authority for provider in report.providers] == [
        "hardcover",
        "google_books",
    ]
    assert all(
        provider.request_accounting.logical_requests == 2
        for provider in report.providers
    )
    assert (
        sum(
            claim.kind is ClaimKind.GENRE
            for provider in report.providers
            for candidate in provider.candidates
            for claim in candidate.claims
        )
        == 1
    )
    assert (
        sum(
            claim.kind is ClaimKind.PUBLICATION_DATE
            for provider in report.providers
            for candidate in provider.candidates
            for claim in candidate.claims
        )
        == 2
    )
    assert publication.alternatives == ()
    assert genre.outcome == "abstain"
    assert genre.reason == "target_work_contradicted"
    assert genre.option == "fill_empty"
    assert genre.source_candidate_id is None
    assert genre.source_evidence_references == ()
    assert genre_outcome.disposition == "abstention"
    assert genre_outcome.candidate_id is None
    assert decisions == ()


def test_final_currentness_rejects_absent_abs_source_that_becomes_bytes() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    cover = TargetCover("descriptor", "/cover", "item-1")

    class Target(FakeTarget):
        def read_cover(self, _value: TargetCover, *, deadline=None) -> bytes:
            return b"appeared-after-observation"

    target = Target(
        [
            TargetSnapshot("ebook", "Title", (selected,), cover),
            TargetSnapshot("ebook", "Title", (selected,), cover),
        ]
    )

    class Observer:
        def observe(self, _target, _providers, current, *_args):
            return current

        def observe_current(self, *_args, **_kwargs):
            return (
                CoverSource(
                    cover_source_id(CoverSourceKind.ABS, "descriptor"),
                    CoverSourceKind.ABS,
                    CoverSourceState.ABSENT,
                ),
            )

    repository = FakeReportRepository()
    service = GenerateMetadataReport(
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
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=Observer(),
    )
    with pytest.raises(CurrentnessError):
        service.run("item-1", 0)
    assert {} == repository.values


def test_final_currentness_rejects_same_path_abs_byte_drift() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    cover = TargetCover("descriptor", "/cover", "item-1")
    snapshots = [
        TargetSnapshot("ebook", "Title", (selected,), cover),
        TargetSnapshot("ebook", "Title", (selected,), cover),
    ]

    class Target(FakeTarget):
        def read_cover(self, value: TargetCover, *, deadline=None) -> bytes:
            if value != cover:
                raise AssertionError("unexpected cover")
            return b"changed-after-staging"

    target = Target(snapshots)
    artifact = CoverArtifact.create(b"staged-original", b"preview", "image/png", 1, 1)

    class Observer:
        def observe(self, _target, _providers, current, *_args):
            return current

        def observe_current(self, *_args, **_kwargs):
            return (
                CoverSource(
                    cover_source_id(CoverSourceKind.ABS, "descriptor"),
                    CoverSourceKind.ABS,
                    CoverSourceState.RETRIEVED,
                    artifact,
                ),
            )

    repository = FakeReportRepository()
    service = GenerateMetadataReport(
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
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=Observer(),
    )
    with pytest.raises(CurrentnessError):
        service.run("item-1", 0)
    assert {} == repository.values


def test_repository_closes_when_inspection_or_provider_validation_fails() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    repository = FakeReportRepository()

    class BrokenInspector:
        def inspect(self, *args):
            raise RuntimeError("broken")

        def unchanged(self, *args):
            return True

    service = GenerateMetadataReport(
        target=FakeTarget([TargetSnapshot("ebook", "Title", (selected,))]),
        inspector=BrokenInspector(),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ()
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(MetadataReportError):
        service.run("item-1", 0)
    assert repository.closed
    repository = FakeReportRepository()
    inspector = FakeInspector(InspectionResult("c" * 64, ()))

    class MismatchedProvider:
        def observe(self, claims, *, deadline=None):
            return ProviderResult(ProviderObservation("other", ProviderStatus.NO_MATCH))

    service = GenerateMetadataReport(
        target=FakeTarget([TargetSnapshot("ebook", "Title", (selected,))]),
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(ProviderBinding("catalogue", True, MismatchedProvider),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(MetadataReportError):
        service.run("item-1", 0)
    assert repository.closed


def test_actual_extraction_and_provider_boundaries_have_separate_timings() -> None:
    class Timings:
        def __init__(self) -> None:
            self.values: list[StageTiming] = []

        def append(self, timing) -> None:
            self.values.append(timing)

    class Provider:
        def observe(self, plan, *, deadline=None):
            return ProviderResult(
                ProviderObservation("catalogue", ProviderStatus.NO_MATCH)
            )

    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Title", (selected,))
    timings = Timings()
    ticks = iter(
        (0, 2_000_000, 3_000_000, 4_000_000, 5_000_000, 10_000_000)
        + (11_000_000, 14_000_000, 15_000_000, 19_000_000)
    )
    service = GenerateMetadataReport(
        target=FakeTarget([snapshot, snapshot]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=FakeReportRepository,
        providers=(ProviderBinding("catalogue", True, Provider),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        timing=StageTimer(timings, monotonic_ns=lambda: next(ticks), clock=lambda: 9),
    )
    service.run("item-1", 0)
    assert [
        (value.stage, value.wait_class, value.duration_ms) for value in timings.values
    ] == [
        ("extraction", "none", 2),
        ("cover", "target", 1),
        ("provider_search", "provider", 5),
        ("cover", "provider", 3),
        ("persistence", "none", 4),
    ]


def test_provider_factory_credential_failure_binds_public_provider_identity() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")

    def unavailable():
        raise CredentialError("private credential detail")

    service = GenerateMetadataReport(
        target=FakeTarget([TargetSnapshot("ebook", "Title", (selected,))]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=FakeReportRepository,
        providers=(ProviderBinding("catalogue", True, unavailable),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(CredentialError) as raised:
        service.run("item-1", 0)
    assert raised.value.provider == "catalogue"


def test_provider_factory_fault_is_not_reported_as_a_credential_failure() -> None:
    """A broken factory is a construction fault, not a missing secret.

    The credential branch above already carries every failure the factory
    declares. The broad handler beside it reported everything else as
    `CredentialError` too, which named the provider's stored secret as the
    cause, marked the preparation non-retryable on `authentication`, and
    recorded a durable authentication fault against that provider's
    recovery health -- for a `TypeError` no credential can fix (#698).
    """
    selected = TargetFile("a" * 64, "book.epub", "epub")

    def broken():
        raise TypeError("factory() takes no arguments")

    service = GenerateMetadataReport(
        target=FakeTarget([TargetSnapshot("ebook", "Title", (selected,))]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=FakeReportRepository,
        providers=(ProviderBinding("catalogue", True, broken),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(MetadataReportError) as raised:
        service.run("item-1", 0)
    assert not isinstance(raised.value, CredentialError)
    assert str(raised.value) == "provider construction failed"
    assert isinstance(raised.value.__cause__, TypeError)


def test_repository_release_failure_cannot_displace_the_failure_it_reports() -> None:
    """A `close` that fails is noted on the failure, never raised over it.

    Every handler that releases the report handle while a failure is in
    flight called `close` unguarded, so a repository whose release itself
    failed replaced the failure the caller asked about with a raw adapter
    fault -- losing the identity, the message and the chained cause. The
    release now follows `catalogue._release_refresh`: the cleanup failure is
    noted on the original, which keeps its identity and its cause (#698).
    """
    selected = TargetFile("a" * 64, "book.epub", "epub")

    class UnreleasableRepository(FakeReportRepository):
        def close(self) -> None:
            raise OSError("database handle is wedged")

    def broken():
        raise TypeError("factory() takes no arguments")

    service = GenerateMetadataReport(
        target=FakeTarget([TargetSnapshot("ebook", "Title", (selected,))]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=UnreleasableRepository,
        providers=(ProviderBinding("catalogue", True, broken),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(MetadataReportError) as raised:
        service.run("item-1", 0)

    # The regression: this was an `OSError`, not the construction failure.
    assert str(raised.value) == "provider construction failed"
    assert isinstance(raised.value.__cause__, TypeError)
    assert [note for note in raised.value.__notes__ if "release failed" in note] == [
        "report repository release failed: OSError('database handle is wedged')"
    ]


@pytest.mark.parametrize(
    "reading",
    (
        RuntimeError("access clock is unavailable"),
        "not-an-access-time",
    ),
    ids=("raising_access_time", "non_timestamp_access_time"),
)
def test_failing_access_time_still_yields_the_promised_unavailable_lane(
    reading,
) -> None:
    """Unavailable or malformed snapshot access time cannot suppress the lane."""
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Current title", (selected,))
    claim = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="eBook ISBN: 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )

    def run_with(reading: object) -> tuple[MetadataReport, FakeReportRepository]:
        class Provider:
            def observe(self, _plan, *, deadline):
                evidence = ProviderOperationalEvidence(
                    accessed_at=None if isinstance(reading, BaseException) else reading
                )
                raise ProviderObservationFailure(
                    ProviderFailureSnapshot(evidence=evidence)
                ) from RuntimeError("adapter transport failed")

        repository = FakeReportRepository()
        service = GenerateMetadataReport(
            target=FakeTarget([snapshot, snapshot]),
            inspector=FakeInspector(InspectionResult("c" * 64, (claim,))),
            repository_factory=lambda: cast(ReportRepository, repository),
            providers=(ProviderBinding("catalogue", True, Provider),),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("catalogue",),
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        )
        return MetadataReport.from_json(service.run("item-1", 0)), repository

    report, repository = run_with(reading)

    observation = report.providers[0]
    assert observation.lanes
    for lane in observation.lanes:
        assert lane.status is ProviderStatus.UNAVAILABLE
    # `None` is the recorded "not obtainable" access time, and the
    # lane the handler promised is present either way.
    assert observation.accessed_at is None
    assert repository.closed


def test_application_replays_after_file_reorder_and_constructs_provider_once() -> None:
    first_file = TargetFile("a" * 64, "one.epub", "epub")
    second_file = TargetFile("b" * 64, "two.epub", "epub")
    target = FakeTarget(
        [
            TargetSnapshot("ebook", "Current title", (first_file, second_file)),
            TargetSnapshot("ebook", "Current title", (second_file, first_file)),
        ]
    )
    claim = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="eBook ISBN: 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    inspector = FakeInspector(InspectionResult("c" * 64, (claim,)))
    repository = FakeReportRepository()
    constructions: list[str] = []

    class Provider:
        def observe(self, claims, *, deadline=None):
            return ProviderResult(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.NO_MATCH,
                    tuple(
                        sorted(
                            value.reference
                            for value in claims
                            if value.kind is ClaimKind.ISBN
                        )
                    ),
                )
            )

    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(
            ProviderBinding(
                "catalogue",
                True,
                lambda: constructions.append("one") or Provider(),  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the construction and still yields the provider.
            ),
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    serialized = service.run("item-1", 0)
    report = MetadataReport.from_json(serialized)
    assert report.file.identity == "a" * 64
    assert constructions == ["one"]
    assert (inspector.inspections, inspector.verifications) == (1, 1)
    assert repository.closed


def test_application_shares_aggregate_with_covers_and_caps_the_metadata_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: started)
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Current title", (selected,))
    target = FakeTarget([snapshot, snapshot])
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    repository = FakeReportRepository()
    observed: list[tuple[str, float]] = []

    class Provider:
        def observe(self, _plan, *, deadline):
            observed.append(("metadata", deadline))
            return ProviderResult(
                ProviderObservation("catalogue", ProviderStatus.NO_MATCH)
            )

    class CoverObserver:
        def observe_current(self, _target, local, aggregate_deadline):
            observed.append(("current", aggregate_deadline))
            return local

        def observe(
            self,
            _target,
            _providers,
            _local_sources,
            _descriptors,
            aggregate_deadline,
        ):
            observed.append(("cover", aggregate_deadline))
            return ()

    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(ProviderBinding("catalogue", True, Provider),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=CoverObserver(),
    )

    report = MetadataReport.from_json(service.run("item-1", 0))

    assert ProviderStatus.NO_MATCH == report.providers[0].status
    # The incumbent read and the cover fetches share the 60-second aggregate;
    # the provider observation gets only its 20-second share of it (#1248).
    assert [
        ("current", started + 60),
        ("metadata", started + 20),
        ("cover", started + 60),
    ] == observed


def test_incomplete_cause_property_is_mapped_without_private_material() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Current title", (selected,))
    target = FakeTarget([snapshot, snapshot])
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    repository = FakeReportRepository()

    class Provider:
        @property
        def attempt_records(self):
            return ()

        @property
        def incomplete_causes(self):
            return (type("Cause", (), {"cause": "aggregate_bytes"})(),)

        def observe(self, _plan, *, deadline):
            return ProviderResult(
                ProviderObservation("catalogue", ProviderStatus.INCOMPLETE),
                evidence=ProviderOperationalEvidence(
                    attempt_records=self.attempt_records,
                    incomplete_causes=self.incomplete_causes,
                ),
            )

    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(ProviderBinding("catalogue", True, Provider),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )

    report = MetadataReport.from_json(service.run("item-1", 0))

    assert [
        FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
    ] == [diagnostic.category for diagnostic in report.providers[0].diagnostics]
    assert FailureCategory.TIMEOUT not in [
        diagnostic.category for diagnostic in report.providers[0].diagnostics
    ]
    assert "credential" not in report.to_json()
    assert "/private/library" not in report.to_json()


def test_ambiguous_provider_identity_reserves_the_malformed_response_category() -> None:
    """One candidate identity restated with claims that disagree is an identity
    failure, and the report never calls it a malformed or over-bound response."""
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
    repository = FakeReportRepository()

    def stated(title: str) -> ProviderCandidate:
        return ProviderCandidate(
            "edition-shared",
            "work-shared",
            (
                ProviderClaim.create(
                    provider="catalogue",
                    candidate="edition-shared",
                    kind=ClaimKind.TITLE,
                    value=title,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                ),
            ),
        )

    class Provider:
        def observe(self, plan, *, deadline):
            return ProviderResult(
                provider_observation(
                    "catalogue",
                    (
                        ProviderLaneResult(
                            plan.lanes[0],
                            ProviderStatus.CANDIDATES,
                            (stated("The Example"), stated("A Different Example")),
                        ),
                    ),
                )
            )

    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(ProviderBinding("catalogue", True, Provider),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks",
            "primary",
            "library-id",
            "ebook",
            "books",
            ("catalogue",),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )

    report = MetadataReport.from_json(service.run("item-1", 0))

    observed = report.providers[0]
    assert ProviderStatus.MALFORMED == observed.status
    assert () == observed.candidates
    assert [(FailureCategory.IDENTITY, FailureStage.IDENTITY)] == [
        (value.category, value.stage) for value in observed.diagnostics
    ]
    assert [(FailureCategory.IDENTITY, FailureStage.IDENTITY)] == [
        (value.category, value.stage)
        for lane in observed.lanes
        for value in lane.diagnostics
    ]
    assert FailureCategory.MALFORMED_OVER_BOUND_RESPONSE not in [
        value.category for value in observed.diagnostics
    ]


def test_provider_parser_attempt_and_incomplete_diagnostics_merge_once_on_reopen() -> (
    None
):
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

    class Provider:
        cover_descriptors = ()
        attempt_records = (
            type(
                "Attempt",
                (),
                {"category": "http_503", "outcome": "retry"},
            )(),
        )
        incomplete_causes = (type("Cause", (), {"cause": "aggregate_bytes"})(),)

        def observe(self, _plan, *, deadline):
            return ProviderResult(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.INCOMPLETE,
                    diagnostics=(
                        FailureDiagnostic(
                            FailureCategory.SCHEMA_CONSTRUCTION,
                            FailureComponent.PROVIDER,
                            FailureStage.SCHEMA,
                            count=1,
                        ),
                    ),
                ),
                cover_descriptors=self.cover_descriptors,
                evidence=ProviderOperationalEvidence(
                    attempt_records=self.attempt_records,
                    incomplete_causes=self.incomplete_causes,
                ),
            )

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: cast(
                ReportRepository, open_repository(database)
            ),
            providers=(ProviderBinding("catalogue", True, Provider),),
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
                ("catalogue",),
            ),
            mount=MediaMountConfiguration(
                "books",
                "/media",
                "/library",
                True,
            ),
            limits=ReportLimits(),
        )
        generated = MetadataReport.from_json(service.run("item-1", 0))
        repository = open_repository(database, create=False)
        try:
            persisted = repository.get(generated.report_id)
        finally:
            repository.close()
        reopened = MetadataReport.from_json(persisted)

    expected = (
        (
            FailureCategory.HTTP_NON_SUCCESS,
            FailureStage.RESPONSE,
            True,
            1,
        ),
        (
            FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
            FailureStage.RESPONSE,
            False,
            1,
        ),
        (
            FailureCategory.SCHEMA_CONSTRUCTION,
            FailureStage.SCHEMA,
            None,
            1,
        ),
    )
    assert expected == tuple(
        (
            value.category,
            value.stage,
            value.retryable,
            value.count,
        )
        for value in reopened.providers[0].diagnostics
    )
    assert generated.to_json() == reopened.to_json()


def test_application_preserves_inspector_occurrence_order_after_target_title() -> None:
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
        for value in ("Second in source", "First by hash")
    )
    target = FakeTarget(
        [
            TargetSnapshot(
                "ebook", "Current title", (TargetFile("a" * 64, "one.epub", "epub"),)
            )
        ]
        * 2
    )
    inspector = FakeInspector(InspectionResult("c" * 64, claims))
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ()
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    report = MetadataReport.from_json(service.run("item-1", 0))
    assert [claim.observed for claim in report.local_claims] == [
        "Current title",
        "Second in source",
        "First by hash",
    ]


def test_duplicate_occurrences_are_serialized_in_source_order_and_replay_stably() -> (
    None
):
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed="Repeated person",
            canonical="Repeated person",
            excerpt="Repeated person",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            occurrence=ordinal,
        )
        for ordinal in (2, 1)
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=claims,
        local_reasons=("package_metadata_creator",),
        providers=(),
    )
    assert [claim.occurrence for claim in report.local_claims] == [2, 1]
    assert MetadataReport.from_json(report.to_json()).to_json() == report.to_json()


@pytest.mark.parametrize(
    ("target", "mount", "file_index"),
    (
        (FakeTarget([TargetSnapshot("audiobook", "Audio", ())]), None, 0),
        (
            FakeTarget(
                [
                    TargetSnapshot(
                        "ebook",
                        "Ebook",
                        (TargetFile("a" * 64, "book.epub", "epub"),),
                    )
                ]
            ),
            MediaMountConfiguration("books", "/media", "/library", True),
            1,
        ),
        (
            FakeTarget(
                [
                    TargetSnapshot(
                        "ebook",
                        "Ebook",
                        (TargetFile("a" * 64, "book.pdf", "pdf"),),
                    )
                ]
            ),
            MediaMountConfiguration("books", "/media", "/library", True),
            0,
        ),
    ),
    ids=("absent-mount", "invalid-selection", "non-epub"),
)
def test_unsupported_media_precedes_inspector_provider_and_repository(
    target: FakeTarget,
    mount: MediaMountConfiguration | None,
    file_index: int,
) -> None:
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    activity: list[str] = []
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: (
            activity.append("repository") or cast(ReportRepository, repository)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the call and still yields the repository.
        ),
        providers=(
            ProviderBinding("catalogue", True, lambda: activity.append("provider")),  # type: ignore[return-value]  # reason: a sentinel factory the test proves is never invoked, so it never has to yield a provider.
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "audio", "primary", "library-id", "audiobook", None, ("catalogue",)
        ),
        mount=mount,
        limits=ReportLimits(),
    )
    with pytest.raises(UnsupportedMediaError):
        service.run("item-1", file_index)
    assert activity == []
    assert inspector.inspections == 0


def test_currentness_drift_inside_publication_precondition_publishes_nothing() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    target = FakeTarget(
        [
            TargetSnapshot("ebook", "Initial title", (selected,)),
            TargetSnapshot("ebook", "Changed title", (selected,)),
        ]
    )
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    activity: list[str] = []
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: (
            activity.append("repository") or cast(ReportRepository, repository)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the call and still yields the repository.
        ),
        providers=(
            ProviderBinding("catalogue", False, lambda: activity.append("provider")),  # type: ignore[return-value]  # reason: a sentinel factory the test proves is never invoked, so it never has to yield a provider.
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(CurrentnessError):
        service.run("item-1", 0)
    assert activity == ["repository"]
    assert repository.values == {}
    assert repository.closed
    assert (inspector.inspections, inspector.verifications) == (1, 0)


def test_progressive_publication_rejects_a_second_final_file() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    second = TargetFile("d" * 64, "other.epub", "epub")
    target = FakeTarget(
        [
            TargetSnapshot("ebook", "Current title", (selected,)),
            TargetSnapshot("ebook", "Current title", (selected, second)),
        ]
    )
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ()
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        require_singular_target=True,
    )

    with pytest.raises(CurrentnessError):
        service.run("item-1", 0)
    assert repository.values == {}
    assert repository.closed


def test_currentness_covers_every_target_destination() -> None:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    initial = TargetSnapshot("ebook", "Current title", (selected,))
    changed_fields = tuple(
        TargetField(
            field.destination,
            TargetFieldState.VALUE,
            ("Changed genre",),
        )
        if field.destination is TargetDestination.GENRES
        else field
        for field in initial.fields
    )
    target = FakeTarget(
        [
            initial,
            TargetSnapshot(
                "ebook",
                "Current title",
                (selected,),
                fields=changed_fields,
            ),
        ]
    )
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ()
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )

    with pytest.raises(CurrentnessError):
        service.run("item-1", 0)
    assert repository.values == {}
    assert repository.closed


def _audiobook_service(
    target: FakeTarget,
    repository: FakeReportRepository,
    audio_inspector: FakeAudioInspector | None,
    *,
    activity: list[str] | None = None,
    limits: ReportLimits | None = None,
    inspector: FakeInspector | None = None,
    plans: list[MetadataDiscoveryPlan] | None = None,
) -> GenerateMetadataReport:
    recorded_plans: list[MetadataDiscoveryPlan] = [] if plans is None else plans
    recorded = [] if activity is None else activity

    class _RecordingProvider:
        """Record the plan the audiobook path builds, and match nothing."""

        def observe(self, plan, *, deadline=None):
            recorded.append("provider")
            recorded_plans.append(plan)
            return ProviderResult(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.NO_MATCH,
                    query_kind=(
                        plan.lanes[0].kind if plan.lanes else ProviderQueryKind.NONE
                    ),
                    authority="audible_catalog",
                )
            )

    return GenerateMetadataReport(
        target=target,
        inspector=inspector or FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(
            ProviderBinding(
                "catalogue", True, _RecordingProvider, authority="audible_catalog"
            ),
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "audio", "primary", "library-id", "audiobook", "audiobooks", ("catalogue",)
        ),
        mount=MediaMountConfiguration("audiobooks", "/media", "/library", True),
        limits=limits or ReportLimits(),
        audio_inspector=audio_inspector,
    )


def test_audiobook_publishes_local_evidence_and_dispatches_its_asin_plan() -> None:
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    target = FakeTarget(
        [
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
        ]
    )
    audio_inspector = FakeAudioInspector(
        audio_result(
            (
                (ClaimKind.ASIN, "B0CW1KL2QD", "embedded_tag", None),
                (
                    ClaimKind.CONTRIBUTOR,
                    "Recorded Narrator",
                    "embedded_tag",
                    "narrator",
                ),
                (ClaimKind.DURATION, "43200.000", "embedded_tag", None),
                (ClaimKind.PERFORMANCE_TYPE, "unabridged", "sidecar", None),
                (ClaimKind.SERIES, "Discworld", "path", None),
            ),
            chapters=(AudioChapter(1, 0, 10_000, "One"),),
            embedded_cover=AudioEmbeddedCover(
                True, AudioEvidenceSurface.CONTAINER_STREAM
            ),
        )
    )
    repository = FakeReportRepository()
    activity: list[str] = []
    plans: list[MetadataDiscoveryPlan] = []

    published = MetadataReport.from_json(
        _audiobook_service(
            target, repository, audio_inspector, activity=activity, plans=plans
        ).run("item-1", 0)
    )

    # Activation: the audiobook path builds its own ASIN plan from the local
    # claims and runs it through the same provider loop an ebook does.
    assert activity == ["provider"]
    assert [lane.kind for lane in plans[0].lanes] == [ProviderQueryKind.ASIN]
    assert [lane.canonical_asin for lane in plans[0].lanes] == ["B0CW1KL2QD"]
    assert audio_inspector.inspections == 1
    assert [value.status for value in published.providers] == [ProviderStatus.NO_MATCH]
    assert published.cover_sources == ()
    assert published.file.format == "m4b"
    assert published.audio is not None
    assert published.audio.embedded_cover.present is True
    assert [value.ordinal for value in published.audio.chapters] == [1]
    assert published.local_reasons == (
        "embedded_tag_asin",
        "embedded_tag_contributor",
        "embedded_tag_duration",
        "path_series",
        "sidecar_performance_type",
    )
    duration = next(
        value for value in published.field_results if value.kind is ClaimKind.DURATION
    )
    assert {(value.source, value.state) for value in duration.sources} == {
        (FieldSource.EPUB, FieldState.ABSENT),
        (FieldSource.TARGET_METADATA, FieldState.UNSUPPORTED),
        (FieldSource.AUDIO_TAG, FieldState.VALID),
        (FieldSource.AUDIO_SIDECAR, FieldState.ABSENT),
        (FieldSource.AUDIO_PATH, FieldState.ABSENT),
    }
    series = next(
        value for value in published.field_results if value.kind is ClaimKind.SERIES
    )
    assert (FieldSource.AUDIO_PATH, FieldState.VALID) in {
        (value.source, value.state) for value in series.sources
    }


_FED_TITLE = "Recorded title"
_FED_AUTHOR = "Ada Author"
_FED_RUNTIME = "43200.000"


def _fed_snapshot(selected: TargetFile, *, asin: str | None = None) -> TargetSnapshot:
    """One audiobook target with an author, and optionally an asserted ASIN."""
    overrides = {
        TargetDestination.AUTHORS: TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, (_FED_AUTHOR,)
        )
    }
    if asin is not None:
        overrides[TargetDestination.ASIN] = TargetField(
            TargetDestination.ASIN, TargetFieldState.VALUE, asin
        )
    return TargetSnapshot(
        "audiobook",
        _FED_TITLE,
        (selected,),
        fields=tuple(
            overrides.get(field.destination, field)
            for field in default_target_fields(_FED_TITLE)
        ),
    )


def _fed_candidate(
    local_claims: tuple[LocalClaim, ...], provider: str, asin: str
) -> ProviderCandidate:
    """One candidate shaped as an audiobook adapter builds it, runtime included."""
    identity = f"{provider}-{asin}"
    specifications: tuple[tuple[ClaimKind, str, ClaimScope, str | None], ...] = (
        (ClaimKind.TITLE, _FED_TITLE, ClaimScope.WORK, None),
        (ClaimKind.CREATOR, _FED_AUTHOR, ClaimScope.WORK, "author"),
        (ClaimKind.ASIN, asin, ClaimScope.EDITION, None),
        (ClaimKind.DURATION, _FED_RUNTIME, ClaimScope.EDITION, None),
    )
    return finalize_candidate_comparisons(
        ProviderCandidate(
            identity,
            "unresolved",
            tuple(
                ProviderClaim.create(
                    provider=provider,
                    candidate=identity,
                    kind=kind,
                    value=value,
                    state=FieldState.VALID,
                    scope=scope,
                    applicability="not_applicable",
                    role=role,
                    raw_order=order,
                )
                for order, (kind, value, scope, role) in enumerate(specifications)
            ),
        ),
        local_claims,
        provider,
    )


class _WorkAnsweringProvider:
    """A catalogue that answers the search lane with one corroborated candidate.

    It observes every lane kind, so it always records lanes and is structurally
    never re-dispatched by the feed -- which is exactly how the real Audible
    catalogue behaves.
    """

    def __init__(
        self, name: str, asin: str, plans: list[MetadataDiscoveryPlan]
    ) -> None:
        self._name = name
        self._asin = asin
        self._plans = plans

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        self._plans.append(plan)
        lanes = tuple(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK
        )
        if not lanes:
            return ProviderResult(
                ProviderObservation(
                    self._name,
                    ProviderStatus.INSUFFICIENT_INPUT,
                    authority="audible_catalog",
                )
            )
        lane = lanes[0]
        title = cast("str", lane.title)
        qualified = qualify_provider_observation(
            ProviderObservation(
                self._name,
                ProviderStatus.CANDIDATES,
                (),
                (_fed_candidate(plan.local_claims, self._name, self._asin),),
                ProviderQueryKind.WORK,
                "audible_catalog",
                dispatch_lineage=WorkDispatchLineage.from_query(
                    work_query_ladder(title, lane.authors)[0]
                ),
            ),
            MetadataLookup(
                ProviderQueryKind.WORK, plan.local_claims, title, lane.authors
            ),
        )
        return ProviderResult(
            provider_observation(
                self._name,
                (
                    ProviderLaneResult(
                        lane,
                        qualified.status,
                        qualified.candidates,
                        dispatch_lineage=qualified.dispatch_lineage,
                        transformation_lineage=qualified.transformation_lineage,
                    ),
                ),
                authority="audible_catalog",
            )
        )


class _AsinAnsweringProvider:
    """A per-ASIN provider: a plan naming no product names nothing it can answer."""

    def __init__(self, name: str, plans: list[MetadataDiscoveryPlan]) -> None:
        self._name = name
        self._plans = plans

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        self._plans.append(plan)
        lanes = tuple(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ASIN
        )
        if not lanes:
            return ProviderResult(
                ProviderObservation(
                    self._name, ProviderStatus.INSUFFICIENT_INPUT, authority="audnexus"
                )
            )
        return ProviderResult(
            provider_observation(
                self._name,
                tuple(
                    ProviderLaneResult(lane, ProviderStatus.NO_MATCH) for lane in lanes
                ),
                authority="audnexus",
            )
        )


def _fed_audiobook_service(
    target: FakeTarget,
    repository: FakeReportRepository,
    audio_inspector: FakeAudioInspector,
    bindings: tuple[ProviderBinding, ...],
) -> GenerateMetadataReport:
    return GenerateMetadataReport(
        target=target,
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=bindings,
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "audio",
            "primary",
            "library-id",
            "audiobook",
            "audiobooks",
            tuple(sorted(binding.name for binding in bindings)),
        ),
        mount=MediaMountConfiguration("audiobooks", "/media", "/library", True),
        limits=ReportLimits(),
        audio_inspector=audio_inspector,
    )


def _fed_inspector(
    *extra: tuple[ClaimKind, str, str, str | None],
) -> FakeAudioInspector:
    return FakeAudioInspector(
        audio_result(((ClaimKind.DURATION, _FED_RUNTIME, "embedded_tag", None), *extra))
    )


def test_a_promoted_asin_is_fed_to_the_per_asin_providers_in_pass() -> None:
    """The gate promotes an ASIN and the same preparation spends it.

    Before this, an item without its own ASIN could have an ASIN corroborated
    and still reach no per-ASIN provider: the plan was fixed before any
    provider answered. Phase two closes that within the one pass.
    """
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    target = FakeTarget([_fed_snapshot(selected), _fed_snapshot(selected)])
    repository = FakeReportRepository()
    search_plans: list[MetadataDiscoveryPlan] = []
    asin_plans: list[MetadataDiscoveryPlan] = []

    published = MetadataReport.from_json(
        _fed_audiobook_service(
            target,
            repository,
            _fed_inspector(),
            (
                ProviderBinding(
                    "catalogue",
                    True,
                    lambda: _WorkAnsweringProvider(
                        "catalogue", "B0CW1KL2QD", search_plans
                    ),
                    authority="audible_catalog",
                ),
                ProviderBinding(
                    "audnexus",
                    True,
                    lambda: _AsinAnsweringProvider("audnexus", asin_plans),
                    authority="audnexus",
                ),
            ),
        ).run("item-1", 0)
    )

    # The catalogue answered its lane, so it is never re-dispatched; the
    # per-ASIN provider saw the search plan first and the promoted ASIN second.
    assert 1 == len(search_plans)
    assert [ProviderQueryKind.WORK] == [lane.kind for lane in search_plans[0].lanes]
    assert 2 == len(asin_plans)
    assert ["B0CW1KL2QD"] == [lane.canonical_asin for lane in asin_plans[1].lanes]
    assert ["audnexus", "catalogue"] == [
        value.provider for value in published.providers
    ]
    audnexus = next(
        value for value in published.providers if value.provider == "audnexus"
    )
    assert ProviderStatus.NO_MATCH is audnexus.status
    assert [ProviderQueryKind.ASIN] == [lane.kind for lane in audnexus.lanes]
    assert search_plans[0].lanes[0].input_references == (
        audnexus.lanes[0].input_references
    )


def test_an_asserted_asin_feeds_the_per_asin_providers_and_keeps_the_work_lane() -> (
    None
):
    """An assertion buys enrichment, not identity, and never costs the search."""
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    snapshot = _fed_snapshot(selected, asin="B0ASSERTED")
    target = FakeTarget([snapshot, snapshot])
    repository = FakeReportRepository()
    asin_plans: list[MetadataDiscoveryPlan] = []

    published = MetadataReport.from_json(
        _fed_audiobook_service(
            target,
            repository,
            _fed_inspector(),
            (
                ProviderBinding(
                    "audnexus",
                    True,
                    lambda: _AsinAnsweringProvider("audnexus", asin_plans),
                    authority="audnexus",
                ),
            ),
        ).run("item-1", 0)
    )

    assert [ProviderQueryKind.WORK] == [lane.kind for lane in asin_plans[0].lanes]
    assert ["B0ASSERTED"] == [lane.canonical_asin for lane in asin_plans[1].lanes]
    assert 1 == len(published.providers)
    assert [ProviderQueryKind.ASIN] == [
        lane.kind for lane in published.providers[0].lanes
    ]
    asserted = next(
        value
        for value in published.local_claims
        if value.kind is ClaimKind.ASIN and value.basis == "target_metadata"
    )
    assert (asserted.reference,) == published.providers[0].lanes[0].input_references
    asin = next(
        value for value in published.field_results if value.kind is ClaimKind.ASIN
    )
    assert (FieldSource.TARGET_METADATA, FieldState.VALID) in {
        (value.source, value.state) for value in asin.sources
    }


def test_a_local_asin_item_feeds_no_asserted_asin() -> None:
    """An item that names its own product already has what a feed would buy."""
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    snapshot = _fed_snapshot(selected, asin="B0ASSERTED")
    target = FakeTarget([snapshot, snapshot])
    repository = FakeReportRepository()
    asin_plans: list[MetadataDiscoveryPlan] = []

    published = MetadataReport.from_json(
        _fed_audiobook_service(
            target,
            repository,
            _fed_inspector((ClaimKind.ASIN, "B0CW1KL2QD", "embedded_tag", None)),
            (
                ProviderBinding(
                    "audnexus",
                    True,
                    lambda: _AsinAnsweringProvider("audnexus", asin_plans),
                    authority="audnexus",
                ),
            ),
        ).run("item-1", 0)
    )

    assert 1 == len(asin_plans)
    assert ["B0CW1KL2QD"] == [lane.canonical_asin for lane in asin_plans[0].lanes]
    assert [asin_plans[0].lanes[0].identity] == [
        lane.identity for lane in published.providers[0].lanes
    ]
    # The assertion is still recorded; it simply bought nothing here.
    assert "B0ASSERTED" in {
        value.canonical
        for value in published.local_claims
        if value.basis == "target_metadata"
    }


def test_an_audiobook_without_local_evidence_still_publishes_a_named_reason() -> None:
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    target = FakeTarget(
        [
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
        ]
    )
    repository = FakeReportRepository()

    published = MetadataReport.from_json(
        _audiobook_service(target, repository, FakeAudioInspector(audio_result())).run(
            "item-1", 0
        )
    )

    assert published.local_reasons == ("no_local_audio_claims",)
    assert published.audio is not None
    assert published.audio.chapters == ()


@pytest.mark.parametrize(
    ("files", "inspector_present"),
    (
        (
            (
                TargetFile("a" * 64, "part-1.m4b", "m4b"),
                TargetFile("b" * 64, "book.wav", "wav"),
            ),
            True,
        ),
        ((TargetFile("a" * 64, "book.wav", "wav"),), True),
        ((TargetFile("a" * 64, "book.m4b", "m4b"),), False),
        (
            tuple(
                TargetFile(f"{index:064x}", f"part-{index}.m4b", "m4b")
                for index in range(513)
            ),
            True,
        ),
    ),
    ids=(
        "one-unsupported-track",
        "unsupported-format",
        "no-composed-inspector",
        "past-the-track-bound",
    ),
)
def test_audiobook_shapes_outside_the_slice_stay_unsupported(
    files: tuple[TargetFile, ...], inspector_present: bool
) -> None:
    target = FakeTarget([TargetSnapshot("audiobook", "Recorded title", files)])
    audio_inspector = FakeAudioInspector(audio_result()) if inspector_present else None
    repository = FakeReportRepository()

    with pytest.raises(UnsupportedMediaError):
        _audiobook_service(target, repository, audio_inspector).run("item-1", 0)

    assert repository.values == {}
    assert audio_inspector is None or audio_inspector.inspections == 0


_COMPOSED_MOUNT = MediaMountConfiguration("audiobooks", "/media", "/library", True)


def _audio_detail(count: int) -> dict[str, object]:
    """One Audiobookshelf item detail carrying `count` ordered audio files."""
    return {
        "id": "item-1",
        "libraryId": "library-id",
        "updatedAt": 1,
        "media": {
            "metadata": {"title": "Recorded title", "authors": [{"name": "Ada"}]},
            "audioFiles": [
                {
                    "ino": f"inode-{ordinal}",
                    "metadata": {
                        "relPath": f"part-{ordinal}.m4b",
                        "path": f"/library/part-{ordinal}.m4b",
                        "size": 7,
                        "ext": ".m4b",
                    },
                }
                for ordinal in range(1, count + 1)
            ],
        },
    }


def _audio_target_files(detail: dict[str, object]) -> tuple[TargetFile, ...]:
    """Read the same detail the catalogue reads, through the same grammar.

    Each file carries the position the service declares for it, because that
    is what the published manifest records and what currentness compares.
    """
    media = cast(dict[str, object], detail["media"])
    return tuple(
        TargetFile(
            normalize_ebook_location(
                record, service_path=_COMPOSED_MOUNT.service_path, item=detail
            ).identity,
            f"part-{ordinal}.m4b",
            "m4b",
            ordinal,
        )
        for ordinal, record in enumerate(cast(list[object], media["audioFiles"]), 1)
    )


def _composed_inspector(files: tuple[TargetFile, ...]) -> FakeAudioInspector:
    digests = tuple(f"{ordinal + 200:064x}" for ordinal in range(len(files)))
    return FakeAudioInspector(
        audio_result(
            ((ClaimKind.DURATION, "43200.000", "embedded_tag", None),),
            content_digest=audio_set_content_digest(digests),
            composition=(
                AudioChapterComposition.SINGLE_FILE
                if len(files) == 1
                else AudioChapterComposition.PER_TRACK
            ),
            tracks=tuple(
                AudioTrackDigest(
                    ordinal,
                    value.identity,
                    digest,
                    "m4b",
                    value.relative_path,
                    value.sequence,
                )
                for ordinal, (value, digest) in enumerate(
                    zip(files, digests, strict=True), 1
                )
            ),
        )
    )


def _composed_audiobook() -> tuple[
    FakeTarget, FakeAudioInspector, tuple[TargetFile, ...], MetadataReport
]:
    detail = _audio_detail(3)
    files = _audio_target_files(detail)
    snapshot = TargetSnapshot("audiobook", "Recorded title", files)
    target = FakeTarget([snapshot, snapshot])
    audio_inspector = _composed_inspector(files)
    published = MetadataReport.from_json(
        _audiobook_service(target, FakeReportRepository(), audio_inspector).run(
            "item-1", 0
        )
    )
    return target, audio_inspector, files, published


def test_a_three_track_item_is_inspected_and_published_as_one_composed_unit() -> None:
    """One item, one inspection, one identity -- and the catalogue's identity.

    The pipeline binds work to the identity the catalogue stated, so a report
    naming any other identity is a report no work can publish. Both sides
    derive it from the same ordered track set through the same function, and
    this is the assertion that they still agree.
    """
    target, audio_inspector, files, published = _composed_audiobook()
    entry = catalogue_entry(
        "primary",
        LibraryConfiguration(
            "audio", "primary", "library-id", "audiobook", "audiobooks", ("catalogue",)
        ),
        _COMPOSED_MOUNT,
        {"id": "item-1", "libraryId": "library-id", "updatedAt": 1},
        _audio_detail(3),
    )

    assert audio_inspector.inspections == 1
    assert audio_inspector.tracks == [
        ("part-1.m4b", "part-2.m4b", "part-3.m4b"),
    ]
    assert published.file.identity == audio_set_identity(
        tuple(value.identity for value in files)
    )
    assert published.file.identity == entry.file_identity
    assert published.audio is not None
    assert [value.ordinal for value in published.audio.tracks] == [1, 2, 3]
    assert target.calls == 2


def _require_composed_current(
    files: tuple[TargetFile, ...],
    audio_inspector: FakeAudioInspector,
    published: MetadataReport,
) -> bool:
    return require_report_current(
        FakeTarget([TargetSnapshot("audiobook", "Recorded title", files)]),
        FakeInspector(InspectionResult("c" * 64, ())),
        _COMPOSED_MOUNT,
        ReportLimits(),
        published,
        audio_inspector=audio_inspector,
    )


def test_an_unchanged_three_track_item_is_still_current() -> None:
    _target, audio_inspector, files, published = _composed_audiobook()

    assert _require_composed_current(files, audio_inspector, published)


@pytest.mark.parametrize(
    "change",
    ("added", "removed", "reordered", "edited", "renumbered"),
    ids=(
        "a-track-was-added",
        "a-track-was-removed",
        "the-tracks-were-reordered",
        "one-track-s-bytes-changed",
        "one-track-s-declared-index-changed",
    ),
)
def test_any_move_in_the_track_set_re_prepares_the_item_exactly_once(
    change: str,
) -> None:
    """A composed report is bound to the whole ordered set, not to one file.

    Adding, removing, or reordering a track moves the composed identity; an
    edit invisible to the service moves only the digests, which is why the
    manifest is re-read as well.  A renumbered track moves neither: its bytes,
    its path, and the set's order are all what they were, and only the
    position the service declares for it changed -- which is the `sequence`
    the manifest published and the gap and disorder observations composed
    from it.
    """
    _target, audio_inspector, files, published = _composed_audiobook()
    if change == "added":
        files = (*files, *_audio_target_files(_audio_detail(4))[3:])
    elif change == "removed":
        files = files[:2]
    elif change == "reordered":
        files = (files[1], files[0], files[2])
    elif change == "renumbered":
        second = files[1]
        files = (
            files[0],
            TargetFile(second.identity, second.relative_path, second.format, 9),
            files[2],
        )
    else:
        audio_inspector.current_digest = "e" * 64

    with pytest.raises(CurrentnessError):
        _require_composed_current(files, audio_inspector, published)


class _WalledAudioInspector(FakeAudioInspector):
    """Re-digest the set until the item wall stops it, as the real one does."""

    def unchanged(self, *_arguments: object, **_keywords: object) -> bool:
        self.verifications += 1
        raise AudioItemInspectionError(
            AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED, 2
        )


def test_a_revalidation_that_outran_the_item_budget_keeps_its_typed_terminal() -> None:
    """Publication runs the currentness guard, and must not blur its outcome.

    The extraction stage maps this outcome onto the owner's
    `multi_file_budget_exceeded` and onto the ordinal that stopped it. Reported
    as a publication that did not complete, the one bound this build states
    about itself would reach the owner as an internal failure.
    """
    detail = _audio_detail(3)
    files = _audio_target_files(detail)
    snapshot = TargetSnapshot("audiobook", "Recorded title", files)
    audio_inspector = _WalledAudioInspector(_composed_inspector(files).result)

    with pytest.raises(AudioItemInspectionError) as raised:
        _audiobook_service(
            FakeTarget([snapshot, snapshot]),
            FakeReportRepository(),
            audio_inspector,
        ).run("item-1", 0)

    assert raised.value.outcome is AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
    assert raised.value.track == 2
    assert audio_inspector.verifications == 1


_SIDECARS = (
    AudioSidecarDigest("book.opf", "1" * 64),
    AudioSidecarDigest("metadata.json", "2" * 64),
)


def _audiobook_with_sidecars() -> tuple[
    FakeTarget, FakeReportRepository, FakeAudioInspector, MetadataReport
]:
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    target = FakeTarget(
        [
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
        ]
    )
    audio_inspector = FakeAudioInspector(audio_result(sidecars=_SIDECARS))
    repository = FakeReportRepository()
    published = MetadataReport.from_json(
        _audiobook_service(target, repository, audio_inspector).run("item-1", 0)
    )
    return target, repository, audio_inspector, published


def test_a_published_audiobook_report_records_the_sidecars_it_read() -> None:
    """The manifest is a currentness witness, and it must survive the document.

    A pair recorded in memory and lost on serialization would leave
    revalidation with nothing to compare against, which is the defect this
    manifest exists to close.
    """
    _, _, _, published = _audiobook_with_sidecars()
    assert published.audio is not None
    assert _SIDECARS == published.audio.sidecars
    assert MetadataReport.from_json(published.to_json()) == published
    assert [
        {"digest": "1" * 64, "name": "book.opf"},
        {"digest": "2" * 64, "name": "metadata.json"},
    ] == cast(dict, published.to_data()["audio"])["sidecars"]


@pytest.mark.parametrize(
    "current",
    [
        pytest.param((), id="every_sidecar_removed"),
        pytest.param(_SIDECARS[:1], id="one_sidecar_removed"),
        pytest.param(
            (*_SIDECARS, AudioSidecarDigest("book.nfo", "3" * 64)), id="sidecar_added"
        ),
        pytest.param(
            (AudioSidecarDigest("book.opf", "9" * 64), _SIDECARS[1]),
            id="sidecar_edited",
        ),
    ],
)
def test_a_changed_sidecar_set_makes_an_audiobook_report_stale(
    current: tuple[AudioSidecarDigest, ...],
) -> None:
    """The audio file is byte-identical in every case here.

    Before the manifest existed each of these published a second report
    against evidence that had already changed, because only the selected file
    was ever re-digested.
    """
    target, _, audio_inspector, published = _audiobook_with_sidecars()
    audio_inspector.current_sidecars = current
    target.snapshots.append(TargetSnapshot("audiobook", "Recorded title", ()))
    target.calls = 0
    target.snapshots[0] = TargetSnapshot(
        "audiobook", "Recorded title", (TargetFile("a" * 64, "book.m4b", "m4b"),)
    )

    with pytest.raises(CurrentnessError):
        require_report_current(
            target,
            FakeInspector(InspectionResult("c" * 64, ())),
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            ReportLimits(),
            published,
            audio_inspector=audio_inspector,
        )


def test_an_unchanged_audiobook_stays_current_with_its_sidecars_intact() -> None:
    target, _, audio_inspector, published = _audiobook_with_sidecars()
    target.calls = 0
    target.snapshots[0] = TargetSnapshot(
        "audiobook", "Recorded title", (TargetFile("a" * 64, "book.m4b", "m4b"),)
    )
    verifications = audio_inspector.verifications

    assert require_report_current(
        target,
        FakeInspector(InspectionResult("c" * 64, ())),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        ReportLimits(),
        published,
        audio_inspector=audio_inspector,
    )
    assert audio_inspector.verifications == verifications + 1


def test_audiobook_currentness_reruns_the_audio_inspector_not_the_epub_one() -> None:
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    target = FakeTarget(
        [
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
            TargetSnapshot("audiobook", "Recorded title", (selected,)),
        ]
    )
    epub_inspector = FakeInspector(InspectionResult("c" * 64, ()))
    audio_inspector = FakeAudioInspector(audio_result())

    assert require_report_current(
        target,
        epub_inspector,
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        ReportLimits(),
        MetadataReport.create(
            target=TargetFacts(
                "primary", "audio", "item-1", default_target_fields("Recorded title")
            ),
            file=FileFacts(0, "a" * 64, "d" * 64, "m4b"),
            local_claims=(),
            local_reasons=("no_local_audio_claims",),
            providers=(),
            # The retained manifest is what currentness re-digests, so the
            # report has to carry the one it was published with.
            audio=audio_result().audio,
        ),
        audio_inspector=audio_inspector,
    )
    assert audio_inspector.verifications == 1
    assert epub_inspector.verifications == 0


def test_an_audiobook_report_cannot_prove_currentness_without_its_inspector() -> None:
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", default_target_fields("Recorded title")
        ),
        file=FileFacts(0, "a" * 64, "d" * 64, "m4b"),
        local_claims=(),
        local_reasons=("no_local_audio_claims",),
        providers=(),
        audio=single_track_audio_facts("d" * 64),
    )
    target = FakeTarget([TargetSnapshot("audiobook", "Recorded title", ())])

    with pytest.raises(CurrentnessError):
        require_report_current(
            target,
            FakeInspector(InspectionResult("c" * 64, ())),
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            ReportLimits(),
            report,
        )
    assert target.calls == 0


def test_the_audio_ceiling_binds_the_audio_path_and_not_the_epub_one() -> None:
    """An audiobook is bounded by its own setting, not the EPUB archive's.

    Both paths read one ceiling out of the same `ReportLimits`, so the only
    thing that shows which setting reached which path is the argument the
    inspector was handed.  The two are set apart here so a path that fell
    back to the other one is a failure rather than a coincidence.
    """
    limits = ReportLimits(source_archive_bytes=1_024, audio_source_bytes=4_096)
    recording = TargetFile("a" * 64, "book.m4b", "m4b")
    audio_inspector = FakeAudioInspector(audio_result())
    _audiobook_service(
        FakeTarget(
            [
                TargetSnapshot("audiobook", "Recorded title", (recording,)),
                TargetSnapshot("audiobook", "Recorded title", (recording,)),
            ]
        ),
        FakeReportRepository(),
        audio_inspector,
        limits=limits,
    ).run("item-1", 0)

    archive = TargetFile("a" * 64, "book.epub", "epub")
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    GenerateMetadataReport(
        target=FakeTarget(
            [
                TargetSnapshot("ebook", "Written title", (archive,)),
                TargetSnapshot("ebook", "Written title", (archive,)),
            ]
        ),
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, FakeReportRepository()),
        providers=(),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ()
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=limits,
    ).run("item-1", 0)

    assert audio_inspector.ceilings == [4_096]
    assert inspector.ceilings == [1_024]


def test_initial_failure_passes_exact_partial_descriptors_to_cover_observer():
    selected = TargetFile("a" * 64, "book.epub", "epub")
    target = TargetSnapshot("ebook", "Current title", (selected,))
    descriptors = (
        ProviderCoverDescriptor(
            "catalogue", CoverSourceKind.OPEN_LIBRARY, "partial", None
        ),
    )
    partial = ProviderFailureSnapshot(
        descriptors, ProviderOperationalEvidence("2023-11-14T22:13:20.000000Z", (), ())
    )
    seen = []

    class Provider:
        def observe(self, _plan, *, deadline=None):
            raise ProviderObservationFailure(partial) from RuntimeError(
                "second lane failed"
            )

    class Observer:
        def observe_current(self, _target, local, aggregate_deadline):
            return local

        def observe(
            self, _target, providers, _local, candidate_descriptors, aggregate_deadline
        ):
            seen.append((providers, candidate_descriptors))
            return ()

    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=FakeTarget([target, target]),
        inspector=FakeInspector(InspectionResult("c" * 64, (isbn_claim(),))),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(ProviderBinding("catalogue", True, Provider),),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=Observer(),
    )
    report = MetadataReport.from_json(service.run("item-1", 0))
    assert report.providers[0].status is ProviderStatus.UNAVAILABLE
    assert seen[0][0] == report.providers
    assert seen[0][1] == descriptors
    assert seen[0][1][0] is descriptors[0]
    assert repository.closed


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize("incumbent", ("retrieved", "absent", "consumes_budget"))
def test_incumbent_precedes_metadata_and_survives_exhausted_alternative_budget(
    media, incumbent, tmp_path, monkeypatch
):
    import io
    import time
    from unittest.mock import patch

    from PIL import Image

    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.application.configuration import (
        ProviderConnectionConfiguration,
    )
    from mechanical_librarian.bootstrap.composition import _CoverEvidenceObserver
    from mechanical_librarian.domain.cover_evidence import (
        CoverDesignation,
        CoverProvenance,
    )

    image = io.BytesIO()
    Image.new("RGB", (10, 20), "red").save(image, "JPEG")
    original = image.getvalue()
    clock = [time.monotonic()]
    started = clock[0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    events = []
    metadata_calls = []
    selected = TargetFile(
        "a" * 64,
        "book.m4b" if media == "audiobook" else "book.epub",
        "m4b" if media == "audiobook" else "epub",
    )
    snapshot = TargetSnapshot(
        media,
        "Current title",
        (selected,),
        TargetCover("descriptor", "/cover.jpg") if incumbent != "absent" else None,
    )

    class Target(FakeTarget):
        def read_cover(self, cover, *, deadline=None):
            events.append(("current", deadline))
            if incumbent == "consumes_budget" and len(events) == 1:
                clock[0] += 61
            return original

    class Provider:
        def observe(self, plan, *, deadline):
            events.append(("metadata", deadline))
            metadata_calls.append((clock[0], deadline))
            clock[0] = started + 61
            return ProviderResult(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (ProviderCandidate("edition", "work", ()),),
                ),
                (
                    ProviderCoverDescriptor(
                        "catalogue",
                        CoverSourceKind.HARDCOVER,
                        cover_source_id(CoverSourceKind.HARDCOVER, "cover"),
                        "https://example.com/cover.jpg",
                        CoverProvenance(
                            "catalogue", CoverDesignation.WORK, "work", "edition"
                        ),
                    ),
                ),
            )

    target = Target([snapshot, snapshot])
    repository = FakeReportRepository()
    observer = _CoverEvidenceObserver(
        target,
        CoverArtifactStore(tmp_path / "artifacts"),
        (ProviderConnectionConfiguration("catalogue", True, True, None),),
    )
    service = GenerateMetadataReport(
        target=target,
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        audio_inspector=FakeAudioInspector(audio_result()),
        repository_factory=lambda: cast(ReportRepository, repository),
        # The configuration's default adapter: an audiobook dispatches only
        # to an authority in its dispatch set (#1247).
        providers=(
            ProviderBinding("catalogue", True, Provider, authority="hardcover"),
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "books", "primary", "library-id", media, "books", ("catalogue",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=observer,
    )
    with patch(
        "mechanical_librarian.bootstrap.composition.fetch_remote_cover"
    ) as fetch:
        report = MetadataReport.from_json(service.run("item-1", 0))
    fetch.assert_not_called()
    assert repository.closed
    current, alternative = report.cover_sources
    assert current.kind is CoverSourceKind.ABS
    assert (
        current.state
        is {
            "retrieved": CoverSourceState.RETRIEVED,
            "absent": CoverSourceState.ABSENT,
            "consumes_budget": CoverSourceState.TIMEOUT,
        }[incumbent]
    )
    assert alternative.state is CoverSourceState.TIMEOUT
    assert alternative.timeout_scope == "not_started_due_to_aggregate_deadline"
    if incumbent != "absent":
        assert events[0] == ("current", started + 60)
    # Each observation gets its 20-second share, never past the aggregate: an
    # incumbent read that consumed the window leaves exactly the aggregate.
    assert metadata_calls and all(
        deadline == min(started + 60, call + 20) for call, deadline in metadata_calls
    )
    if incumbent == "consumes_budget":
        assert [deadline for _, deadline in metadata_calls] == [started + 60]


class _SlowProvider:
    """A provider that spends its whole deadline and records it ran out (#1248).

    It advances the fake clock to exactly the deadline it was handed and
    answers every lane INCOMPLETE with the SDK's deadline cause, which is what
    an adapter records when its share elapses before it could dispatch again.
    """

    def __init__(
        self,
        name: str,
        authority: str,
        clock: list[float],
        calls: list[tuple[str, float, float]],
    ) -> None:
        self._name = name
        self._authority = authority
        self._clock = clock
        self._calls = calls

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        assert deadline is not None
        self._calls.append((self._name, self._clock[0], deadline))
        self._clock[0] = deadline
        return ProviderResult(
            provider_observation(
                self._name,
                tuple(
                    ProviderLaneResult(lane, ProviderStatus.INCOMPLETE)
                    for lane in plan.lanes
                    if lane.title_basis is None
                ),
                authority=self._authority,
            ),
            evidence=ProviderOperationalEvidence(
                incomplete_causes=(
                    ProviderIncompleteCauseRecord(ProviderIncompleteCause.DEADLINE),
                ),
            ),
        )


class _FastProvider:
    """A provider that answers every lane at once with no match."""

    def __init__(
        self,
        name: str,
        authority: str,
        clock: list[float],
        calls: list[tuple[str, float, float]],
    ) -> None:
        self._name = name
        self._authority = authority
        self._clock = clock
        self._calls = calls

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        assert deadline is not None
        self._calls.append((self._name, self._clock[0], deadline))
        return ProviderResult(
            provider_observation(
                self._name,
                tuple(
                    ProviderLaneResult(lane, ProviderStatus.NO_MATCH)
                    for lane in plan.lanes
                    if lane.title_basis is None
                ),
                authority=self._authority,
            )
        )


def _share_snapshot(media: str) -> TargetSnapshot:
    """One target of either medium with a title and an author to search on."""
    selected = (
        TargetFile("a" * 64, "book.m4b", "m4b")
        if media == "audiobook"
        else TargetFile("a" * 64, "book.epub", "epub")
    )
    return replace(_fed_snapshot(selected), media_kind=media)


def _share_service(
    media: str,
    snapshot: TargetSnapshot,
    repository: FakeReportRepository,
    bindings: tuple[ProviderBinding, ...],
    cover_observer: object,
) -> GenerateMetadataReport:
    return GenerateMetadataReport(
        target=FakeTarget([snapshot, snapshot]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        audio_inspector=_fed_inspector(),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=bindings,
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "books",
            "primary",
            "library-id",
            media,
            "books",
            tuple(sorted(binding.name for binding in bindings)),
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=cover_observer,
    )


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize("slow", (0, 1, 2, 3))
def test_each_provider_receives_at_most_its_share_of_the_aggregate(
    media: str, slow: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One slow provider no longer spends the window the next ones need (#1248).

    Every observation gets `min(aggregate, dispatch + 20)`. Two providers
    that each use their whole share still leave the next one time to answer;
    three use the whole 60 seconds, and then the aggregate binds rather than a
    fresh share.
    """
    clock = [1000.0]
    started = clock[0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls: list[tuple[str, float, float]] = []
    boundaries: list[tuple[str, float]] = []

    class CoverObserver:
        def observe_current(self, _target, local, aggregate_deadline):
            boundaries.append(("current", aggregate_deadline))
            return local

        def observe(self, _target, _providers, local, _descriptors, aggregate):
            boundaries.append(("cover", aggregate))
            return local

    # Both authorities are in the audiobook dispatch set, so #1247's filter
    # dispatches every binding for either medium.
    bindings = (
        *(
            ProviderBinding(
                f"slow-{index}",
                True,
                partial(_SlowProvider, f"slow-{index}", "hardcover", clock, calls),
                authority="hardcover",
            )
            for index in range(slow)
        ),
        *(
            ProviderBinding(
                f"fast-{index}",
                True,
                partial(_FastProvider, f"fast-{index}", "open_library", clock, calls),
                authority="open_library",
            )
            for index in range(2)
        ),
    )
    repository = FakeReportRepository()

    report = MetadataReport.from_json(
        _share_service(
            media, _share_snapshot(media), repository, bindings, CoverObserver()
        ).run("item-1", 0)
    )

    assert [binding.name for binding in bindings] == [name for name, _, _ in calls]
    assert all(deadline == min(started + 60, call + 20) for _, call, deadline in calls)
    slow_calls = calls[:slow]
    fast_calls = calls[slow:]
    assert all(deadline - call <= 20 for _, call, deadline in slow_calls)
    if slow < 3:
        assert all(call < deadline for _, call, deadline in fast_calls)
    else:
        assert all(deadline == started + 60 for _, _, deadline in fast_calls)
    # The incumbent read and the alternative covers keep the whole aggregate.
    assert [("current", started + 60), ("cover", started + 60)] == boundaries
    by_name = {value.provider: value for value in report.providers}
    for index in range(slow):
        observation = by_name[f"slow-{index}"]
        assert ProviderStatus.INCOMPLETE is observation.status
        assert (FailureCategory.TIMEOUT, FailureStage.REQUEST, False) in {
            (value.category, value.stage, value.retryable)
            for value in observation.diagnostics
        }
    for index in range(2):
        assert ProviderStatus.NO_MATCH is by_name[f"fast-{index}"].status
    assert repository.closed


def test_an_asin_feed_after_a_slow_provider_dispatches_within_its_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ASIN feed pass runs the same loop, so it draws the same share."""
    clock = [1000.0]
    started = clock[0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls: list[tuple[str, float, float]] = []
    asin_plans: list[MetadataDiscoveryPlan] = []

    class Audnexus(_AsinAnsweringProvider):
        def observe(
            self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
        ) -> ProviderResult:
            assert deadline is not None
            calls.append(("audnexus", clock[0], deadline))
            return super().observe(plan, deadline=deadline)

    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    snapshot = _fed_snapshot(selected, asin="B0ASSERTED")
    published = MetadataReport.from_json(
        _fed_audiobook_service(
            FakeTarget([snapshot, snapshot]),
            FakeReportRepository(),
            _fed_inspector(),
            (
                ProviderBinding(
                    "slow",
                    True,
                    lambda: _SlowProvider("slow", "hardcover", clock, calls),
                    authority="hardcover",
                ),
                ProviderBinding(
                    "audnexus",
                    True,
                    lambda: Audnexus("audnexus", asin_plans),
                    authority="audnexus",
                ),
            ),
        ).run("item-1", 0)
    )

    # Phase one: the slow provider spends its share; Audnexus has nothing to
    # answer. Phase two feeds Audnexus the asserted ASIN with time left.
    assert ["slow", "audnexus", "audnexus"] == [name for name, _, _ in calls]
    assert ["B0ASSERTED"] == [lane.canonical_asin for lane in asin_plans[1].lanes]
    _, fed_at, fed_deadline = calls[2]
    assert fed_at == started + 20
    assert fed_deadline == min(started + 60, fed_at + 20) == started + 40
    assert fed_at < fed_deadline
    audnexus = next(
        value for value in published.providers if value.provider == "audnexus"
    )
    assert [ProviderQueryKind.ASIN] == [lane.kind for lane in audnexus.lanes]


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
def test_cover_fetches_start_within_the_aggregate_after_a_slow_provider(
    media: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow provider leaves the alternative cover its own attempt window."""
    from unittest.mock import patch

    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.adapters.providers.remote_cover import (
        CoverDeadline,
        RemoteCoverTimeout,
    )
    from mechanical_librarian.bootstrap.composition import _CoverEvidenceObserver
    from mechanical_librarian.domain.cover_evidence import (
        CoverDesignation,
        CoverProvenance,
    )

    clock = [1000.0]
    started = clock[0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls: list[tuple[str, float, float]] = []
    fetches: list[tuple[float, CoverDeadline]] = []

    class Catalogue:
        def observe(
            self, _plan: MetadataDiscoveryPlan, *, deadline: float | None = None
        ) -> ProviderResult:
            assert deadline is not None
            calls.append(("catalogue", clock[0], deadline))
            return ProviderResult(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (ProviderCandidate("edition", "work", ()),),
                ),
                (
                    ProviderCoverDescriptor(
                        "catalogue",
                        CoverSourceKind.HARDCOVER,
                        cover_source_id(CoverSourceKind.HARDCOVER, "cover"),
                        "https://example.com/cover.jpg",
                        CoverProvenance(
                            "catalogue", CoverDesignation.WORK, "work", "edition"
                        ),
                    ),
                ),
            )

    def fetch(_url: str, *, deadline: CoverDeadline, **_options: object) -> None:
        fetches.append((clock[0], deadline))
        raise RemoteCoverTimeout("timed out")

    snapshot = _share_snapshot(media)
    target = FakeTarget([snapshot, snapshot])
    observer = _CoverEvidenceObserver(
        target,
        CoverArtifactStore(tmp_path / "artifacts"),
        (ProviderConnectionConfiguration("catalogue", True, True, None),),
    )
    repository = FakeReportRepository()
    with patch(
        "mechanical_librarian.bootstrap.composition.fetch_remote_cover",
        side_effect=fetch,
    ):
        report = MetadataReport.from_json(
            _share_service(
                media,
                snapshot,
                repository,
                (
                    ProviderBinding(
                        "slow",
                        True,
                        lambda: _SlowProvider("slow", "open_library", clock, calls),
                        authority="open_library",
                    ),
                    ProviderBinding(
                        "catalogue", True, Catalogue, authority="hardcover"
                    ),
                ),
                observer,
            ).run("item-1", 0)
        )

    assert [
        ("slow", started, started + 20),
        ("catalogue", started + 20, started + 40),
    ] == calls
    assert [(started + 20, CoverDeadline(started + 60, started + 40))] == fetches
    alternative = next(
        value
        for value in report.cover_sources
        if value.kind is CoverSourceKind.HARDCOVER
    )
    assert CoverSourceState.TIMEOUT is alternative.state
    assert "attempted" == alternative.timeout_scope
    assert repository.closed


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize(
    "failure", (RuntimeError("acquisition failed"), KeyboardInterrupt())
)
def test_early_cover_failure_releases_repository_preserving_original(media, failure):
    selected = TargetFile(
        "a" * 64,
        "book.m4b" if media == "audiobook" else "book.epub",
        "m4b" if media == "audiobook" else "epub",
    )
    snapshot = TargetSnapshot(media, "Title", (selected,))

    class Observer:
        def observe_current(self, *args):
            raise failure

        def observe(self, *args):
            raise AssertionError("alternatives must not start")

    class Repository(FakeReportRepository):
        def close(self):
            super().close()
            raise RuntimeError("close failed")

    repository = Repository()
    service = GenerateMetadataReport(
        target=FakeTarget([snapshot]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        audio_inspector=FakeAudioInspector(audio_result()),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "books", "primary", "library-id", media, "books", ()
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        cover_observer=Observer(),
    )
    with pytest.raises(type(failure)) as raised:
        service.run("item-1", 0)
    assert raised.value is failure
    assert repository.closed and not repository.values
    assert any("close failed" in note for note in failure.__notes__)


# #1247 and #1254: an audiobook dispatches only to the connected providers in
# `AUDIOBOOK_DISPATCH_ADAPTERS`; an ebook dispatches exactly as it did. Each
# binding is named for its adapter, so a name is also its authority.
_EVERY_ADAPTER = (
    "audible_catalog",
    "audiosilo",
    "audnexus",
    "google_books",
    "hardcover",
    "inventaire",
    "itunes_search",
    "open_library",
)
_EXCLUDED_ON_AUDIOBOOKS = ("google_books", "inventaire", "itunes_search")


class _UnavailableProvider:
    """Answer every asserted lane as unavailable, so each one is retryable."""

    def __init__(self, name: str, observed: list[str]) -> None:
        self._name = name
        self._observed = observed

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        self._observed.append(self._name)
        return ProviderResult(
            provider_observation(
                self._name,
                tuple(
                    ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE)
                    for lane in plan.lanes
                    if lane.title_basis is None
                ),
                authority=self._name,
            )
        )


class _ItemTarget:
    """A library target serving one snapshot per item, as often as it is read."""

    def __init__(self, snapshots: dict[str, TargetSnapshot]) -> None:
        self._snapshots = snapshots

    def read(self, item: str) -> TargetSnapshot:
        return self._snapshots[item]


def _dispatch_binding(
    name: str, constructed: list[str], observed: list[str]
) -> ProviderBinding:
    def factory() -> _UnavailableProvider:
        constructed.append(name)
        return _UnavailableProvider(name, observed)

    return ProviderBinding(name, True, factory, authority=name)


def _never_built(name: str, *, enabled: bool = True) -> ProviderBinding:
    def factory() -> _UnavailableProvider:
        pytest.fail(f"{name} was built")

    return ProviderBinding(name, enabled, factory, authority=name)


def _mixed_service(
    bindings: tuple[ProviderBinding, ...],
    *,
    audio: FakeAudioInspector | None = None,
) -> GenerateMetadataReport:
    epub = TargetFile("b" * 64, "book.epub", "epub")
    snapshot = _fed_snapshot(TargetFile("a" * 64, "book.m4b", "m4b"))
    return GenerateMetadataReport(
        target=_ItemTarget(
            {
                "audiobook": snapshot,
                "ebook": replace(snapshot, media_kind="ebook", files=(epub,)),
            }
        ),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=lambda: cast(ReportRepository, FakeReportRepository()),
        providers=bindings,
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "mixed",
            "primary",
            "library-id",
            "book",
            "media",
            tuple(sorted(binding.name for binding in bindings)),
        ),
        mount=MediaMountConfiguration("media", "/media", "/library", True),
        limits=ReportLimits(),
        audio_inspector=audio or _fed_inspector(),
    )


def _mixed_configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
        ),
        (
            LibraryConfiguration(
                "mixed", "primary", "library-id", "book", "media", _EVERY_ADAPTER
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/library", True),),
        tuple(
            ProviderConnectionConfiguration(
                name,
                True,
                False,
                None,
                name,
                "owner@example.invalid" if name == "open_library" else None,
                ("uk",) if name == "audible_catalog" else (),
            )
            for name in _EVERY_ADAPTER
        ),
    )


def test_a_mixed_library_dispatches_audiobooks_only_to_the_dispatch_set() -> None:
    """The audiobook reaches five providers; the ebook still reaches all eight."""
    constructed: dict[str, list[str]] = {"audiobook": [], "ebook": []}
    observed: dict[str, list[str]] = {"audiobook": [], "ebook": []}
    reports: dict[str, MetadataReport] = {}
    for item in ("audiobook", "ebook"):
        bindings = tuple(
            _dispatch_binding(name, constructed[item], observed[item])
            for name in _EVERY_ADAPTER
        )
        reports[item] = MetadataReport.from_json(_mixed_service(bindings).run(item, 0))

    dispatched = [
        name for name in _EVERY_ADAPTER if name in AUDIOBOOK_DISPATCH_ADAPTERS
    ]
    assert dispatched == constructed["audiobook"] == observed["audiobook"]
    assert dispatched == [value.provider for value in reports["audiobook"].providers]
    assert reports["audiobook"].audio is not None
    # Ebook dispatch is unchanged: every connected binding, in binding order.
    assert list(_EVERY_ADAPTER) == constructed["ebook"] == observed["ebook"]
    assert list(_EVERY_ADAPTER) == [
        value.provider for value in reports["ebook"].providers
    ]
    assert reports["ebook"].audio is None

    configuration = _mixed_configuration()
    jobs = {
        item: provider_recovery_jobs(
            report,
            package=(None, report.report_id, None, None, "report_only"),
            configuration=configuration,
            scope="mixed",
        )
        for item, report in reports.items()
    }
    assert dispatched == [job.adapter for job in jobs["audiobook"]]
    assert list(_EVERY_ADAPTER) == [job.adapter for job in jobs["ebook"]]


@pytest.mark.parametrize(
    "connected,dispatched",
    (
        (("audible_catalog", "google_books"), ("audible_catalog",)),
        (_EXCLUDED_ON_AUDIOBOOKS, ()),
    ),
)
def test_an_audiobooks_connections_narrow_the_dispatch_set_and_never_widen_it(
    connected: tuple[str, ...], dispatched: tuple[str, ...]
) -> None:
    observed: list[str] = []
    bindings = tuple(
        _never_built(name)
        if name in _EXCLUDED_ON_AUDIOBOOKS
        else _dispatch_binding(name, [], observed)
        for name in connected
    )

    report = MetadataReport.from_json(_mixed_service(bindings).run("audiobook", 0))

    assert list(dispatched) == observed
    assert dispatched == tuple(value.provider for value in report.providers)


def test_an_audiobook_binding_without_an_authority_is_never_dispatched() -> None:
    """No authority names no adapter in the set, so the binding fails closed."""
    binding = replace(_never_built("catalogue"), authority=None)

    report = MetadataReport.from_json(_mixed_service((binding,)).run("audiobook", 0))

    assert () == report.providers


def test_a_disabled_excluded_binding_records_nothing_on_an_audiobook() -> None:
    """Only an in-set binding states DISABLED; an excluded one states nothing."""
    bindings = tuple(
        _never_built(name, enabled=False)
        for name in ("google_books", "hardcover", "itunes_search")
    )

    report = MetadataReport.from_json(_mixed_service(bindings).run("audiobook", 0))

    assert [("hardcover", ProviderStatus.DISABLED)] == [
        (value.provider, value.status) for value in report.providers
    ]


def test_an_overflowing_audiobook_plan_limits_only_the_dispatch_set() -> None:
    """An excluded binding states no QUERY_LIMIT_EXCEEDED either."""
    bindings = tuple(_never_built(name) for name in _EVERY_ADAPTER)
    overflowing = _fed_inspector(
        *(
            (ClaimKind.ASIN, f"B00000000{index}", "embedded_tag", None)
            for index in range(1, 6)
        )
    )

    report = MetadataReport.from_json(
        _mixed_service(bindings, audio=overflowing).run("audiobook", 0)
    )

    assert [
        (name, ProviderStatus.QUERY_LIMIT_EXCEEDED)
        for name in _EVERY_ADAPTER
        if name in AUDIOBOOK_DISPATCH_ADAPTERS
    ] == [(value.provider, value.status) for value in report.providers]


def test_a_disabled_audiobook_binding_records_every_lane_as_not_dispatched() -> None:
    bindings = (_never_built("audible_catalog", enabled=False),)

    report = MetadataReport.from_json(_mixed_service(bindings).run("audiobook", 0))

    (observation,) = report.providers
    assert observation.status is ProviderStatus.DISABLED
    assert observation.lanes
    assert all(
        lane.dispatch == ProviderLaneDispatch(DispatchOutcome.NOT_DISPATCHED)
        for lane in observation.lanes
    )


class _ResultProvider:
    """Answer one plan with an assembled result, or raise the given error."""

    def __init__(self, answer: ProviderResult | BaseException) -> None:
        self._answer = answer

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer


def _audiobook_work_plan() -> MetadataDiscoveryPlan:
    from tests.support.audiobook_providers import work_plan
    from tests.support.audiobook_reports import audio_local_claims
    from tests.support.metadata_report_doubles import single_track_audio_facts

    return replace(work_plan(*audio_local_claims()), audio=single_track_audio_facts())


def _work_ladder(plan: MetadataDiscoveryPlan) -> tuple[WorkDispatchLineage, ...]:
    lane = plan.lanes[0]
    return tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(
            cast(str, lane.title), lane.authors, audiobook=True
        )
    )


def _dispatched_lane(
    status: ProviderStatus,
    *,
    authority: str = "audible_catalog",
    causes: tuple[ProviderIncompleteCause, ...] = (),
    exhausted: bool = False,
    refused: bool = False,
    rungs: int | None = None,
    near_miss: int | None = None,
    audio: bool = True,
) -> tuple[ProviderObservation, ...]:
    from mechanical_librarian.application.metadata_report import (
        WorkLadderFacts,
        observe_provider,
    )
    from mechanical_librarian.domain.metadata_report import work_ladder_diagnostics
    from tests.support.audiobook_reports import audiobook_candidate

    plan = _audiobook_work_plan()
    if not audio:
        plan = replace(plan, audio=None)
    lane = plan.lanes[0]
    ladder = _work_ladder(plan)
    admitted = status is ProviderStatus.CANDIDATES
    result = ProviderLaneResult(
        lane,
        status,
        (
            (
                audiobook_candidate(
                    plan.local_claims,
                    provider="catalogue",
                    identity="audible-product-B08G9PRS1K",
                ),
            )
            if admitted
            else ()
        ),
        diagnostics=(
            work_ladder_diagnostics(
                len(ladder) - int(refused), len(ladder) if refused else 0
            )
            if exhausted
            else ()
        ),
        dispatch_lineage=ladder[0] if admitted else None,
        exhausted_dispatch=ladder if exhausted else (),
    )
    answer = ProviderResult(
        provider_observation("catalogue", (result,), authority=authority),
        evidence=ProviderOperationalEvidence(
            incomplete_causes=tuple(
                ProviderIncompleteCauseRecord(cause) for cause in causes
            )
        ),
        work_ladders=(
            () if rungs is None else (WorkLadderFacts(lane.identity, rungs, near_miss),)
        ),
    )
    return (
        observe_provider(
            "catalogue", authority, cast(Any, _ResultProvider(answer)), plan
        ).observation,
    )


@pytest.mark.parametrize(
    ("status", "options", "outcome"),
    (
        (ProviderStatus.CANDIDATES, {"rungs": 1}, DispatchOutcome.ADMITTED),
        (ProviderStatus.NO_MATCH, {"exhausted": True}, DispatchOutcome.EMPTY),
        (
            ProviderStatus.NO_MATCH,
            {"exhausted": True, "refused": True},
            DispatchOutcome.REFUSED,
        ),
        (ProviderStatus.NO_MATCH, {}, DispatchOutcome.EMPTY_OR_REFUSED),
        (ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, {}, DispatchOutcome.OVERFLOW),
        (ProviderStatus.UNAVAILABLE, {}, DispatchOutcome.UNAVAILABLE),
        (ProviderStatus.RATE_LIMITED, {}, DispatchOutcome.UNAVAILABLE),
        (
            ProviderStatus.INCOMPLETE,
            {"causes": (ProviderIncompleteCause.DEADLINE,)},
            DispatchOutcome.STARVED,
        ),
    ),
    ids=lambda value: value.value if isinstance(value, StrEnum) else None,
)
def test_preparation_records_each_audiobook_lane_outcome_once(
    status: ProviderStatus, options: dict[str, Any], outcome: DispatchOutcome
) -> None:
    (observation,) = _dispatched_lane(status, **options)
    (lane,) = observation.lanes

    assert lane.dispatch is not None
    assert outcome is lane.dispatch.outcome
    assert options.get("rungs") == lane.dispatch.rungs
    assert lane.dispatch.near_miss_seconds is None


def test_a_walked_ladder_carries_its_depth_and_near_miss_onto_the_record() -> None:
    (observation,) = _dispatched_lane(
        ProviderStatus.NO_MATCH, exhausted=True, refused=True, rungs=3, near_miss=-900
    )

    assert (
        ProviderLaneDispatch(DispatchOutcome.REFUSED, 3, -900)
        == observation.lanes[0].dispatch
    )


@pytest.mark.parametrize(
    "causes",
    (
        (),
        (ProviderIncompleteCause.DEADLINE, ProviderIncompleteCause.DISPATCH_COUNT),
        (ProviderIncompleteCause.DEADLINE, ProviderIncompleteCause.AGGREGATE_BYTES),
        (ProviderIncompleteCause.DISPATCH_COUNT,),
        (ProviderIncompleteCause.AGGREGATE_BYTES,),
    ),
    ids=("no_cause", "dispatch_count", "aggregate_bytes", "count_only", "bytes_only"),
)
def test_an_incomplete_lane_is_starved_only_by_the_deadline_alone(
    causes: tuple[ProviderIncompleteCause, ...],
) -> None:
    """A spent byte or dispatch budget is not starvation, even beside it."""
    (observation,) = _dispatched_lane(ProviderStatus.INCOMPLETE, causes=causes)

    assert ProviderLaneDispatch(DispatchOutcome.UNAVAILABLE) == (
        observation.lanes[0].dispatch
    )


def test_a_failed_adapter_records_its_audiobook_lanes_as_unavailable() -> None:
    from mechanical_librarian.application.metadata_report import observe_provider

    plan = _audiobook_work_plan()
    observation = observe_provider(
        "catalogue",
        "audible_catalog",
        cast(Any, _ResultProvider(RuntimeError("synthetic failure"))),
        plan,
    ).observation

    assert [ProviderLaneDispatch(DispatchOutcome.UNAVAILABLE)] == [
        lane.dispatch for lane in observation.lanes
    ]


def test_an_ebook_observation_keeps_no_dispatch_record() -> None:
    (observation,) = _dispatched_lane(
        ProviderStatus.NO_MATCH, exhausted=True, rungs=3, audio=False
    )

    assert [None] == [lane.dispatch for lane in observation.lanes]
