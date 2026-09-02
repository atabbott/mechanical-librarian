"""Metadata-report application boundaries: currentness across every
destination, separate extraction and provider timings, diagnostic
merge on reopen, and publication preconditions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

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
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    GenerateMetadataReport,
    InspectionResult,
    MetadataDiscoveryPlan,
    MetadataLookup,
    MetadataReportError,
    ProviderBinding,
    ProviderLaneResult,
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
from mechanical_librarian.domain.audio_inspection import (
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
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
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldSource,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
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
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    work_query_ladder,
)
from tests.support.metadata_report_doubles import (
    FakeAudioInspector,
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
    audio_result,
)


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
        def observe(self, *_args, **_kwargs):
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
        def observe(self, *_args, **_kwargs):
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
            return ProviderObservation("other", ProviderStatus.NO_MATCH)

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
            return ProviderObservation("catalogue", ProviderStatus.NO_MATCH)

    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Title", (selected,))
    timings = Timings()
    ticks = iter((0, 2_000_000, 3_000_000, 8_000_000))
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
    ] == [("extraction", "none", 2), ("provider_search", "provider", 5)]


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
    """A broken diagnostic cannot cost the lane the handler promises.

    The provider handler converts any `observe` failure into one unavailable
    lane so a single adapter cannot deny the owner the whole report. It read
    the optional access time with `getattr(provider, "accessed_at", None)`,
    whose default absorbs only `AttributeError`, so a property raising
    anything else propagated out of the handler and aborted the report --
    the opposite of the invariant the handler was annotated with. An answer
    that is not an access time reaches the domain rule and fails the same
    way, so both the read and the check are guarded now (#698).
    """
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
            @property
            def accessed_at(self):
                if isinstance(reading, BaseException):
                    raise reading
                return reading

            def observe(self, _plan, *, deadline):
                raise RuntimeError("adapter transport failed")

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
            return ProviderObservation(
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


def test_application_shares_aggregate_deadline_with_metadata_and_cover_providers() -> (
    None
):
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", "Current title", (selected,))
    target = FakeTarget([snapshot, snapshot])
    inspector = FakeInspector(InspectionResult("c" * 64, ()))
    repository = FakeReportRepository()
    observed: list[tuple[str, float]] = []

    class Provider:
        def observe(self, _plan, *, deadline):
            observed.append(("metadata", deadline))
            return ProviderObservation("catalogue", ProviderStatus.NO_MATCH)

    class CoverObserver:
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
    assert ["metadata", "cover"] == [boundary for boundary, _ in observed]
    assert observed[0][1] == observed[1][1]


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
            return ProviderObservation("catalogue", ProviderStatus.INCOMPLETE)

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
            return ProviderObservation(
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
            return ProviderObservation(
                "catalogue",
                ProviderStatus.NO_MATCH,
                query_kind=(
                    plan.lanes[0].kind if plan.lanes else ProviderQueryKind.NONE
                ),
                authority="audible_catalog",
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
    ) -> ProviderObservation:
        self._plans.append(plan)
        lanes = tuple(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK
        )
        if not lanes:
            return ProviderObservation(
                self._name,
                ProviderStatus.INSUFFICIENT_INPUT,
                authority="audible_catalog",
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
        return provider_observation(
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


class _AsinAnsweringProvider:
    """A per-ASIN provider: a plan naming no product names nothing it can answer."""

    def __init__(self, name: str, plans: list[MetadataDiscoveryPlan]) -> None:
        self._name = name
        self._plans = plans

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderObservation:
        self._plans.append(plan)
        lanes = tuple(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ASIN
        )
        if not lanes:
            return ProviderObservation(
                self._name, ProviderStatus.INSUFFICIENT_INPUT, authority="audnexus"
            )
        return provider_observation(
            self._name,
            tuple(ProviderLaneResult(lane, ProviderStatus.NO_MATCH) for lane in lanes),
            authority="audnexus",
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
    """Read the same detail the catalogue reads, through the same grammar."""
    media = cast(dict[str, object], detail["media"])
    return tuple(
        TargetFile(
            normalize_ebook_location(
                record, service_path=_COMPOSED_MOUNT.service_path, item=detail
            ).identity,
            f"part-{ordinal}.m4b",
            "m4b",
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
    ("added", "removed", "reordered", "edited"),
    ids=(
        "a-track-was-added",
        "a-track-was-removed",
        "the-tracks-were-reordered",
        "one-track-s-bytes-changed",
    ),
)
def test_any_move_in_the_track_set_re_prepares_the_item_exactly_once(
    change: str,
) -> None:
    """A composed report is bound to the whole ordered set, not to one file.

    Adding, removing, or reordering a track moves the composed identity; an
    edit invisible to the service moves only the digests, which is why the
    manifest is re-read as well.
    """
    _target, audio_inspector, files, published = _composed_audiobook()
    if change == "added":
        files = (*files, *_audio_target_files(_audio_detail(4))[3:])
    elif change == "removed":
        files = files[:2]
    elif change == "reordered":
        files = (files[1], files[0], files[2])
    else:
        audio_inspector.current_digest = "e" * 64

    with pytest.raises(CurrentnessError):
        _require_composed_current(files, audio_inspector, published)


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
        audio=AudioFacts(),
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
