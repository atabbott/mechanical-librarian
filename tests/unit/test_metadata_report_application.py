"""Metadata-report application boundaries: currentness across every
destination, separate extraction and provider timings, diagnostic
merge on reopen, and publication preconditions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

import pytest

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
    MetadataReportError,
    ProviderBinding,
    ReportRepository,
    TargetCover,
    TargetFile,
    TargetSnapshot,
    UnsupportedMediaError,
)
from mechanical_librarian.application.operations import StageTimer, StageTiming
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
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from tests.support.metadata_report_doubles import (
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
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
