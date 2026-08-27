from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers import hardcover_metadata
from mechanical_librarian.adapters.providers.retry_safety import ProviderAttemptRecord
from mechanical_librarian.application.configuration import (
    MediaMountConfiguration,
    ReportLimits,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    ProviderBinding,
    ProviderLaneResult,
    ReportRepository,
    TargetFile,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry,
    GenerateProviderRetrySuccessor,
    ProviderRetryResolver,
    ProviderRetrySharedFailure,
    ProviderRetrySuccessorError,
)
from mechanical_librarian.bootstrap.composition import (
    _classify_provider_recovery_failure,
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
    ProviderCandidate,
    ProviderQueryKind,
    ProviderRequestAccounting,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetFieldState,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
    WorkTitleTransformationLineage,
    WorkTitleTransformationSide,
)


class _Target:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def read(self, _item):
        return self.snapshot


class _Inspector:
    def __init__(self, current=True):
        self.current = current

    def unchanged(self, *_args):
        return self.current


class _Repository:
    def __init__(self, report):
        self.values = {report.report_id: report.to_json()}
        self.current_id = report.report_id
        self.package: tuple[str | None, str, str | None, int | None, str] = (
            None,
            report.report_id,
            None,
            None,
            "report_only",
        )

    def get(self, report_id):
        return self.values[report_id]

    def current_optional(self, *_args):
        return self.values[self.current_id]

    def current_review_package(self, *_args):
        return self.package

    def current_review_package_report(self, *_args):
        return self.package[1]

    def publish(
        self,
        report,
        currentness,
        expected_current=None,
        after_publication=None,
        *,
        expected_package=None,
    ):
        if (
            expected_current != self.current_id
            or expected_package != self.package
            or not currentness()
        ):
            raise ProviderRetrySuccessorError("stale")
        self.values[report.report_id] = report.to_json()
        self.current_id = report.report_id
        if after_publication is not None:
            after_publication(self, report)
        return report.to_json()

    def close(self):
        pass


class _Provider:
    def __init__(self, observation, attempt_records=()):
        self.observation = observation
        self.plans = []
        self.deadlines = []
        self.attempt_records = attempt_records

    def observe(self, plan, *, deadline=None):
        self.plans.append(plan)
        self.deadlines.append(deadline)
        return self.observation


@dataclass
class _Retry:
    """The published predecessor, its two lanes, its store, and its service.

    Declared rather than a `SimpleNamespace` so every member keeps the type
    the `setUp` attributes carried. An untyped namespace makes each attribute
    access `Any`, which retires the site suppressions that depend on the real
    signatures while ruff, mypy and the tests all stay silent.
    """

    claims: tuple[LocalClaim, ...]
    snapshot: TargetSnapshot
    plan: MetadataDiscoveryPlan
    failed: MetadataDiscoveryLane
    success: MetadataDiscoveryLane
    report: MetadataReport
    # Rebound by tests that need a different store; `service` reads it through
    # `self` on every call, as `self.repository` was, so the service built
    # beforehand still reaches the replacement.
    repository: _Repository

    def service(
        self,
        configured: ConfiguredProviderRetry | None,
        *,
        inspector: _Inspector | None = None,
        after_publication: Callable[
            [ReportRepository, MetadataReport], object
        ] = lambda *_args: None,
        resolver: ProviderRetryResolver | None = None,
    ) -> GenerateProviderRetrySuccessor:
        return GenerateProviderRetrySuccessor(
            target=_Target(self.snapshot),
            inspector=_Inspector() if inspector is None else inspector,
            repository_factory=lambda: cast(ReportRepository, self.repository),
            provider_resolver=(
                cast("ProviderRetryResolver", lambda *_args: configured)
                if resolver is None
                else resolver
            ),
            mount=MediaMountConfiguration("books", "/container", "/service", True),
            limits=ReportLimits(),
            after_publication=after_publication,
        )


@pytest.fixture
def retry() -> _Retry:
    """The published predecessor, its two lanes, its store, and its service."""

    first = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    second = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780061120084",
        canonical="9780061120084",
        excerpt="9780061120084",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    claims = (first, second)
    snapshot = TargetSnapshot(
        "ebook", None, (TargetFile("b" * 64, "book.epub", "epub"),)
    )
    plan = metadata_discovery_plan(snapshot, claims)
    failed, success = plan.lanes
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(failed, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(
                success,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("old", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64),
        local_claims=claims,
        local_reasons=("opening_matter_isbn",),
        providers=(original,),
    )
    return _Retry(
        claims=claims,
        snapshot=snapshot,
        plan=plan,
        failed=failed,
        success=success,
        report=report,
        repository=_Repository(report),
    )


def test_retries_only_selected_failed_lane_and_preserves_sibling(retry: _Retry) -> None:
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    provider = _Provider(replacement)
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    successor = MetadataReport.from_json(
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    )
    assert (retry.failed.identity,) == tuple(
        lane.identity for lane in provider.plans[0].lanes
    )
    before = retry.report.providers[0].lanes[1].to_data()
    after = next(
        lane
        for lane in successor.providers[0].lanes
        if lane.identity == retry.success.identity
    ).to_data()
    assert before == after
    assert retry.report.report_id != successor.report_id
    assert retry.report.report_id == retry.repository.package[1]


def test_observe_wrapped_credential_error_remains_authentication_for_recovery(
    retry: _Retry,
) -> None:
    class CredentialProvider:
        def observe(self, _plan, *, deadline=None):
            del deadline
            raise CredentialError("credential")

    configured = ConfiguredProviderRetry(
        ProviderBinding("catalogue", True, CredentialProvider, "open_library"),
        "open_library",
        2,
    )
    service = retry.service(configured)
    with pytest.raises(ProviderRetrySuccessorError) as raised:
        service._observe(configured, retry.plan, (retry.failed,), deadline=1.0)
    assert isinstance(raised.value.__cause__, CredentialError)
    assert ("authentication", None) == _classify_provider_recovery_failure(raised.value)


def test_generation_wake_alone_admits_authentication_lane_and_reclassifies_result(
    retry: _Retry,
) -> None:
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(retry.failed, ProviderStatus.AUTHENTICATION),
            ProviderLaneResult(
                retry.success,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("old", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    report = MetadataReport.create(
        target=retry.report.target,
        file=retry.report.file,
        local_claims=retry.report.local_claims,
        local_reasons=retry.report.local_reasons,
        providers=(original,),
    )
    retry.repository = _Repository(report)
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    provider = _Provider(replacement)
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySuccessorError, match="lane"):
        service.run(
            report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans

    successor = MetadataReport.from_json(
        service.run(
            report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
            generation_woken=True,
        )
    )
    assert report.report_id != successor.report_id
    assert ProviderStatus.CANDIDATES == successor.providers[0].lanes[0].status

    retry.repository = _Repository(report)
    still_auth = _Provider(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, ProviderStatus.AUTHENTICATION),),
            authority="open_library",
        )
    )
    failed = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: still_auth, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySharedFailure):
        failed.run(
            report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
            generation_woken=True,
        )
    assert report.report_id == retry.repository.current_id


def test_none_returning_staging_receipt_succeeds(retry: _Retry) -> None:
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    provider = _Provider(replacement)
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    assert (
        MetadataReport.from_json(
            service.run(
                retry.report.report_id,
                (retry.failed.identity,),
                provider_name="catalogue",
                adapter="open_library",
                generation=2,
            )
        ).report_id
        == retry.repository.current_id
    )


def test_raw_adapter_authority_is_bound_and_retry_has_deadline(retry: _Retry) -> None:
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
    )
    provider = _Provider(replacement)
    successor = MetadataReport.from_json(
        retry.service(
            ConfiguredProviderRetry(
                ProviderBinding("catalogue", True, lambda: provider, "open_library"),
                "open_library",
                2,
            )
        ).run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    )
    assert "open_library" == successor.providers[0].authority
    assert 1 == len(provider.deadlines)
    assert provider.deadlines[0] > 0.0


def test_final_source_drift_prevents_factory_dispatch(retry: _Retry) -> None:
    replacement = provider_observation("catalogue", (), authority="open_library")
    provider = _Provider(replacement)

    class DriftingTarget:
        reads = 0

        def read(_self, _item):
            _self.reads += 1
            return (
                retry.snapshot
                if _self.reads < 2
                else TargetSnapshot("ebook", "changed", retry.snapshot.files)
            )

    service = GenerateProviderRetrySuccessor(
        target=DriftingTarget(),
        inspector=_Inspector(),
        repository_factory=lambda: cast(ReportRepository, retry.repository),
        provider_resolver=lambda *_args: ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        ),
        mount=MediaMountConfiguration("books", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )
    with pytest.raises(Exception, match="target or media"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans


def test_same_report_package_advancement_prevents_factory_dispatch(
    retry: _Retry,
) -> None:
    replacement = provider_observation("catalogue", (), authority="open_library")
    provider = _Provider(replacement)

    class AdvancingTarget:
        reads = 0

        def read(_self, _item):
            _self.reads += 1
            if _self.reads == 2:
                retry.repository.package = (
                    None,
                    retry.report.report_id,
                    None,
                    None,
                    "no_case",
                )
            return retry.snapshot

    service = GenerateProviderRetrySuccessor(
        target=AdvancingTarget(),
        inspector=_Inspector(),
        repository_factory=lambda: cast(ReportRepository, retry.repository),
        provider_resolver=lambda *_args: ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        ),
        mount=MediaMountConfiguration("books", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )
    with pytest.raises(Exception, match="target or media"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans


def test_malformed_package_prevents_factory_dispatch(retry: _Retry) -> None:
    provider = _Provider(
        provider_observation("catalogue", (), authority="open_library")
    )

    class MalformedPackageRepository(_Repository):
        def current_review_package(_self, *_args):
            raise ProviderRetrySuccessorError("persisted review package is invalid")

    repository = MalformedPackageRepository(retry.report)
    service = GenerateProviderRetrySuccessor(
        target=_Target(retry.snapshot),
        inspector=_Inspector(),
        repository_factory=lambda: cast(ReportRepository, repository),
        provider_resolver=lambda *_args: ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        ),
        mount=MediaMountConfiguration("books", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )
    with pytest.raises(Exception, match="package is invalid"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans


def test_malformed_replacement_leaves_predecessor_current(retry: _Retry) -> None:
    provider = _Provider(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, ProviderStatus.MALFORMED),),
            authority="open_library",
        )
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySuccessorError, match="result"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert retry.report.report_id == retry.repository.current_id


def test_malformed_aggregate_with_clean_lane_leaves_predecessor_current(
    retry: _Retry,
) -> None:
    clean_lane = provider_observation(
        "catalogue",
        (ProviderLaneResult(retry.failed, ProviderStatus.NO_MATCH),),
        authority="open_library",
    )
    provider = _Provider(replace(clean_lane, status=ProviderStatus.MALFORMED))
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySuccessorError, match="result"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert retry.report.report_id == retry.repository.current_id
    assert retry.report.report_id == retry.repository.package[1]


@pytest.mark.parametrize(
    ("status", "failure"),
    (
        (ProviderStatus.AUTHENTICATION, "authentication"),
        (ProviderStatus.AUTHORIZATION, "authorization"),
        (ProviderStatus.RATE_LIMITED, "rate_limited"),
        (ProviderStatus.UNAVAILABLE, "unavailable"),
        (ProviderStatus.INCOMPLETE, "incomplete"),
    ),
    ids=(
        "authentication",
        "authorization",
        "rate_limited",
        "unavailable",
        "incomplete",
    ),
)
def test_shared_terminal_matrix_is_typed_before_publication(
    retry: _Retry, status, failure
) -> None:
    retry.repository = _Repository(retry.report)
    provider = _Provider(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, status),),
            authority="open_library",
        )
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySharedFailure) as raised:
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert failure == raised.value.failure
    assert retry.report.report_id == retry.repository.current_id
    assert retry.report.report_id == retry.repository.package[1]


def test_validated_final_retry_after_is_carried_by_shared_failure(
    retry: _Retry,
) -> None:
    record = ProviderAttemptRecord("catalogue", 1, "http_429", 37.0, 0.1, "exhausted")
    provider = _Provider(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, ProviderStatus.RATE_LIMITED),),
            authority="open_library",
        ),
        (record,),
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySharedFailure) as raised:
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert ("rate_limited", 37) == (
        raised.value.failure,
        raised.value.retry_after,
    )


def test_merge_uses_one_timestamp_and_retains_bounded_evidence(retry: _Retry) -> None:
    diagnostic = FailureDiagnostic(
        FailureCategory.UNKNOWN, FailureComponent.PROVIDER, FailureStage.RESPONSE
    )
    lane_diagnostic = FailureDiagnostic(
        FailureCategory.LOOKUP_CONSTRUCTION,
        FailureComponent.PROVIDER,
        FailureStage.LOOKUP,
        retryable=False,
    )
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.UNAVAILABLE,
                accessed_at="2026-08-14T12:00:00.000000Z",
            ),
            ProviderLaneResult(
                retry.success,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("old", "work", ()),),
                "2026-08-14T12:00:00.000000Z",
                diagnostics=(lane_diagnostic,),
            ),
        ),
        authority="open_library",
        diagnostics=(diagnostic,),
        request_accounting=ProviderRequestAccounting(2, 2),
    )
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
                "2026-08-14T12:01:00.000000Z",
            ),
        ),
        authority="open_library",
        diagnostics=(diagnostic,),
        request_accounting=ProviderRequestAccounting(1, 1),
    )
    merged = retry.service(None)._merged(
        retry.plan, original, (retry.failed,), replacement
    )
    assert "2026-08-14T12:01:00.000000Z" == merged.accessed_at
    assert (
        next(
            lane for lane in original.lanes if lane.identity == retry.success.identity
        ).to_data()
        == next(
            lane for lane in merged.lanes if lane.identity == retry.success.identity
        ).to_data()
    )
    assert 2 == merged.diagnostics[0].count
    assert ProviderRequestAccounting(3, 3) == merged.request_accounting


def test_retained_work_overflow_survives_selected_isbn_no_match(retry: _Retry) -> None:
    plan, work, isbn, original = _overflow_source(retry)
    replacement = provider_observation(
        "catalogue",
        (ProviderLaneResult(isbn, ProviderStatus.NO_MATCH),),
        authority="open_library",
    )
    merged = retry.service(None)._merged(plan, original, (isbn,), replacement)
    assert original.edition_enrichment_overflow == merged.edition_enrichment_overflow


def test_retry_that_erases_retained_work_overflow_fails_closed(retry: _Retry) -> None:
    plan, work, isbn, original = _overflow_source(retry)
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                isbn, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)
            ),
        ),
        authority="open_library",
    )
    before = original.to_data()
    with pytest.raises(ProviderRetrySuccessorError, match="overflow"):
        retry.service(None)._merged(plan, original, (isbn,), replacement)
    assert before == original.to_data()


def test_retry_successor_preserves_exact_work_dispatch_and_transformation_lineage(
    retry: _Retry,
) -> None:
    plan, work, isbn, _overflow = _overflow_source(retry)
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(work, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(isbn, ProviderStatus.NO_MATCH),
        ),
        authority="open_library",
    )
    dispatch = WorkDispatchLineage(
        WorkDispatchRung.NORMALIZED,
        (WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,),
        True,
    )
    transformation = WorkTitleTransformationLineage(
        WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
        WorkTitleTransformationSide.LOCAL,
    )
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                work,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
                dispatch_lineage=dispatch,
                transformation_lineage=transformation,
            ),
        ),
        authority="open_library",
    )

    merged = retry.service(None)._merged(plan, original, (work,), replacement)

    recovered = next(lane for lane in merged.lanes if lane.identity == work.identity)
    assert recovered.dispatch_lineage == dispatch
    assert recovered.transformation_lineage == transformation


def _overflow_source(retry: _Retry):
    title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Current title",
        canonical="Current title",
        excerpt="Current title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    fields = tuple(
        replace(field, state=TargetFieldState.VALUE, value=("Ada Example",))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in TargetSnapshot("ebook", "Current title", ()).fields
    )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Current title", (), fields=fields),
        (title, retry.claims[0]),
    )
    work = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK)
    isbn = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN)
    payload = {
        "data": {
            "editions": [
                {
                    "id": index,
                    "book_id": 10,
                    "isbn_10": None,
                    "isbn_13": None,
                    "asin": None,
                    "title": None,
                    "subtitle": None,
                    "release_date": None,
                    "image": None,
                    "publisher": None,
                    "language": None,
                    "contributions": [],
                }
                for index in range(1, 7)
            ],
            "books": [
                {
                    "id": 10,
                    "title": "Current title",
                    "description": None,
                    "release_date": None,
                    "image": None,
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": "Author",
                            "author": {"id": 7, "name": "Ada Example"},
                        }
                    ],
                    "book_series": [],
                    "taggings": [],
                }
            ],
            "tags": [],
        }
    }
    witness = hardcover_metadata._parse_edition_enrichment_overflow(
        json.dumps(payload).encode(), "catalogue"
    )
    assert witness is not None
    return (
        plan,
        work,
        isbn,
        provider_observation(
            "catalogue",
            (
                ProviderLaneResult(
                    work,
                    ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                    edition_enrichment_overflow=witness,
                ),
                ProviderLaneResult(isbn, ProviderStatus.UNAVAILABLE),
            ),
            authority="open_library",
        ),
    )


def test_real_sqlite_successor_staging_retains_the_source_package(
    retry: _Retry,
) -> None:
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    provider = _Provider(replacement)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        source_repository = open_repository(path)
        try:
            source_repository.publish(retry.report, lambda: True)
        finally:
            source_repository.close()
        service = GenerateProviderRetrySuccessor(
            target=_Target(retry.snapshot),
            inspector=_Inspector(),
            repository_factory=lambda: cast(ReportRepository, open_repository(path)),
            provider_resolver=lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("catalogue", True, lambda: provider, "open_library"),
                "open_library",
                2,
            ),
            mount=MediaMountConfiguration("books", "/container", "/service", True),
            limits=ReportLimits(),
            after_publication=lambda *_args: None,
        )
        successor = MetadataReport.from_json(
            service.run(
                retry.report.report_id,
                (retry.failed.identity,),
                provider_name="catalogue",
                adapter="open_library",
                generation=2,
            )
        )
        repository = open_repository(path)
        try:
            assert retry.report.report_id == repository.current_review_package_report(
                retry.report.target.connection,
                retry.report.target.library,
                retry.report.target.item,
            )
            assert successor.to_json() == repository.current(
                successor.target.connection,
                successor.target.library,
                successor.target.item,
                successor.file.identity,
            )
        finally:
            repository.close()


def test_recreated_equivalent_resolver_factory_is_current(retry: _Retry) -> None:
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                retry.failed,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    provider = _Provider(replacement)

    def resolver(*_args):
        return ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )

    successor = retry.service(None, resolver=resolver).run(
        retry.report.report_id,
        (retry.failed.identity,),
        provider_name="catalogue",
        adapter="open_library",
        generation=2,
    )
    assert retry.report.report_id != MetadataReport.from_json(successor).report_id


def test_full_currentness_failure_prevents_provider_factory_dispatch(
    retry: _Retry,
) -> None:
    provider = _Provider(
        provider_observation("catalogue", (), authority="open_library")
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        ),
        inspector=_Inspector(False),
    )
    with pytest.raises(Exception, match="target or media"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans


def test_rejects_successful_lane_before_provider_dispatch(retry: _Retry) -> None:
    provider = _Provider(
        provider_observation("catalogue", (), authority="open_library")
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySuccessorError, match="lane"):
        service.run(
            retry.report.report_id,
            (retry.success.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans


def test_rebound_generation_and_provider_failure_leave_predecessor_current(
    retry: _Retry,
) -> None:
    provider = _Provider(
        provider_observation("catalogue", (), authority="open_library")
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySuccessorError, match="configuration"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=3,
        )
    assert retry.report.report_id == retry.repository.current_id

    class FailingProvider:
        def observe(self, *_args, **_kwargs):
            raise RuntimeError("offline")

    failing = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, FailingProvider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(ProviderRetrySuccessorError, match="dispatch"):
        failing.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert retry.report.report_id == retry.repository.current_id


def test_rejects_report_current_but_not_current_package_before_dispatch(
    retry: _Retry,
) -> None:
    provider = _Provider(
        provider_observation("catalogue", (), authority="open_library")
    )
    retry.repository.package = ("proposal", "not-the-source", "case", 2, "admitted")
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        )
    )
    with pytest.raises(Exception, match="target or media"):
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    assert [] == provider.plans
