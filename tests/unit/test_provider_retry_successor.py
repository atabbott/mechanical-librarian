from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers import hardcover_metadata
from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AudibleCatalogMetadataProvider,
)
from mechanical_librarian.adapters.providers.itunes_search_metadata import (
    ITunesSearchMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    MediaMountConfiguration,
    ReportLimits,
)
from mechanical_librarian.application.failure_diagnostics import (
    provider_result_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    ProviderBinding,
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderObservationCancelled,
    ProviderObservationFailure,
    ProviderObservationInterrupted,
    ProviderResult,
    ReportRepository,
    TargetFile,
    TargetSnapshot,
    audiobook_asin_feed_plan,
    audiobook_carried_plan,
    audiobook_discovery_plan,
    metadata_discovery_plan,
    observe_provider,
    provider_discovery_lane_order,
    provider_observation,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderAttemptRecord,
    ProviderOperationalEvidence,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    classify_provider_recovery_failure,
    provider_recovery_jobs,
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
from mechanical_librarian.domain.audio_inspection import AudioTrackDigest
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    AudioDiscoveryTitle,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderQueryKind,
    ProviderRequestAccounting,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    _provider_from_data,
    default_target_fields,
    work_ladder_diagnostics,
)
from mechanical_librarian.domain.provider_recovery import (
    ProviderRecoveryJob,
    RecoveryAttemptEvidence,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    admit_provider_work_identity,
    work_query_ladder,
)
from tests.support.audiobook_providers import (
    alternative_work_plan,
    audio_target,
    target_asin_claim,
    target_title_claim,
)
from tests.support.audiobook_reports import recorded_dispatch
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.support.metadata_report_schemas import synthetic_latin_work_report


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


# One retained manifest for the source report every successor case reopens:
# currentness re-proves the ordered track set, so the document has to name it.
_RETAINED_AUDIO = AudioFacts(
    tracks=(AudioTrackDigest(1, "b" * 64, "c" * 64, "m4b", "book.m4b"),)
)


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
        return ProviderResult(
            self.observation,
            evidence=ProviderOperationalEvidence(attempt_records=self.attempt_records),
        )


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
        operational_evidence: Callable[[RecoveryAttemptEvidence], None] | None = None,
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
            operational_evidence=operational_evidence,
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


def test_mixed_identifier_recovery_keeps_work_for_retry_successor(
    retry: _Retry,
) -> None:
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
    print_only = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780140328721",
        canonical="9780140328721",
        excerpt="9780140328721",
        applicability="print",
        checksum="valid_isbn_13",
        applicability_basis="print",
    )
    base_snapshot = TargetSnapshot(
        "ebook",
        "Current title",
        (TargetFile("b" * 64, "book.epub", "epub"),),
    )
    fields = tuple(
        replace(field, state=TargetFieldState.VALUE, value=("Ada Example",))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in base_snapshot.fields
    )
    claims = (title, retry.claims[0], print_only)
    snapshot = TargetSnapshot(
        "ebook",
        "Current title",
        base_snapshot.files,
        fields=fields,
    )
    plan = metadata_discovery_plan(snapshot, claims)
    eligible = next(
        lane for lane in plan.lanes if lane.matched_family == "9780306406157"
    )
    nonusable = next(
        lane for lane in plan.lanes if lane.matched_family == "9780140328721"
    )
    work = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK)
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                eligible,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("old", "work", ()),),
            ),
            ProviderLaneResult(nonusable, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(work, ProviderStatus.NO_MATCH),
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

    jobs = provider_recovery_jobs(
        report,
        package=(None, report.report_id, None, None, "report_only"),
        configuration=SimpleNamespace(
            providers=(SimpleNamespace(name="catalogue", adapter="open_library"),),
            provider_recovery_generation=2,
        ),
        scope="ebooks",
    )
    assert ProviderStatus.CANDIDATES is original.status
    assert {lane.identity for lane in plan.lanes} == {
        lane.identity for lane in original.lanes
    }
    assert ProviderStatus.NO_MATCH is next(
        lane.status for lane in original.lanes if lane.identity == work.identity
    )
    assert (nonusable.identity,) == tuple(json.loads(jobs[0].lane_ids))

    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                nonusable,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
            ),
        ),
        authority="open_library",
    )
    provider = _Provider(replacement)
    mixed = _Retry(
        claims=claims,
        snapshot=snapshot,
        plan=plan,
        failed=nonusable,
        success=eligible,
        report=report,
        repository=_Repository(report),
    )
    successor = MetadataReport.from_json(
        mixed.service(
            ConfiguredProviderRetry(
                ProviderBinding("catalogue", True, lambda: provider, "open_library"),
                "open_library",
                2,
            )
        ).run(
            report.report_id,
            tuple(json.loads(jobs[0].lane_ids)),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    )
    assert (nonusable.identity,) == tuple(
        lane.identity for lane in provider.plans[0].lanes
    )
    assert {lane.identity for lane in plan.lanes} == {
        lane.identity for lane in successor.providers[0].lanes
    }


@pytest.mark.parametrize("declared", (False, True))
def test_observe_wrapped_credential_error_remains_authentication_for_recovery(
    retry: _Retry,
    declared: bool,
) -> None:
    class CredentialProvider:
        def observe(self, _plan, *, deadline=None):
            del deadline
            if declared:
                raise ProviderObservationFailure(
                    ProviderFailureSnapshot()
                ) from CredentialError("credential")
            raise CredentialError("credential")

    configured = ConfiguredProviderRetry(
        ProviderBinding("catalogue", True, CredentialProvider, "open_library"),
        "open_library",
        2,
    )
    service = retry.service(configured)
    with pytest.raises(ProviderRetrySuccessorError) as raised:
        service._observe(
            configured,
            retry.plan,
            (retry.failed,),
            retry.report.providers[0],
            retry.report.target,
            deadline=1.0,
        )
    cause = raised.value.__cause__
    if declared:
        assert isinstance(cause, ProviderObservationFailure)
        cause = cause.__cause__
    assert isinstance(cause, CredentialError)
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
        (ProviderStatus.UNAVAILABLE, "transient"),
        (ProviderStatus.INCOMPLETE, "transient"),
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


def test_retry_observation_uses_the_same_exact_burst_rule_as_initial_report(
    retry: _Retry,
) -> None:
    original = replace(retry.report.providers[0], authority="google_books")
    source = MetadataReport.create(
        target=retry.report.target,
        file=retry.report.file,
        local_claims=retry.report.local_claims,
        local_reasons=retry.report.local_reasons,
        providers=(original,),
    )
    retry.repository = _Repository(source)
    record = ProviderAttemptRecord(
        "catalogue", 1, "burst_throttled", 100.0, 0.1, "terminal"
    )
    provider = _Provider(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, ProviderStatus.RATE_LIMITED),),
            authority="google_books",
        ),
        (record,),
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "google_books"),
            "google_books",
            2,
        )
    )
    with pytest.raises(ProviderRetrySharedFailure) as raised:
        service.run(
            source.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="google_books",
            generation=2,
        )
    assert ("burst_throttled", 100) == (
        raised.value.failure,
        raised.value.retry_after,
    )


def test_retry_observation_keeps_specific_plus_generic_rate_limit_ambiguous(
    retry: _Retry,
) -> None:
    original = replace(retry.report.providers[0], authority="google_books")
    source = MetadataReport.create(
        target=retry.report.target,
        file=retry.report.file,
        local_claims=retry.report.local_claims,
        local_reasons=retry.report.local_reasons,
        providers=(original,),
    )
    retry.repository = _Repository(source)
    records = (
        ProviderAttemptRecord("catalogue", 1, "burst_throttled", 100.0, 0.1, "retry"),
        ProviderAttemptRecord("catalogue", 2, "http_429", 37.0, 0.1, "terminal"),
    )
    provider = _Provider(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, ProviderStatus.RATE_LIMITED),),
            authority="google_books",
        ),
        records,
    )
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "google_books"),
            "google_books",
            2,
        )
    )
    with pytest.raises(ProviderRetrySharedFailure) as raised:
        service.run(
            source.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="google_books",
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


def test_retry_successor_preserves_exact_work_dispatch_without_transformation(
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
        (),
        True,
    )
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                work,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("new", "work", ()),),
                dispatch_lineage=dispatch,
            ),
        ),
        authority="open_library",
    )

    merged = retry.service(None)._merged(plan, original, (work,), replacement)

    recovered = next(lane for lane in merged.lanes if lane.identity == work.identity)
    assert recovered.dispatch_lineage == dispatch
    assert recovered.transformation_lineage is None


def test_retry_successor_keeps_an_exhausted_ladder_and_counts_its_refusal_once(
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
    original = replace(
        original,
        diagnostics=provider_result_diagnostics(
            original, ProviderOperationalEvidence()
        ),
    )
    exhausted = tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(work.title or "", work.authors)
    )
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                work,
                ProviderStatus.NO_MATCH,
                diagnostics=work_ladder_diagnostics(0, 3),
                exhausted_dispatch=exhausted,
            ),
        ),
        authority="open_library",
    )
    # The successor lifts the replacement's refusal exactly as an initial
    # observation does, and the merge below must not lift it again.
    replacement = replace(
        replacement,
        diagnostics=provider_result_diagnostics(
            replacement, ProviderOperationalEvidence()
        ),
    )

    merged = retry.service(None)._merged(plan, original, (work,), replacement)

    recovered = next(lane for lane in merged.lanes if lane.identity == work.identity)
    assert recovered.exhausted_dispatch == exhausted
    assert recovered.diagnostics == work_ladder_diagnostics(0, 3)
    assert [(value.category, value.count) for value in merged.diagnostics] == [
        (FailureCategory.UNKNOWN, 1),
        (FailureCategory.WORK_IDENTITY_REFUSED, 3),
    ]


def test_retry_successor_replays_reordered_complete_work_authors(
    retry: _Retry,
) -> None:
    plan, work, isbn, _overflow = _overflow_source(retry)
    authors = ("Ada Example", "Bea Example")
    reordered_work = replace(work, title="Plain Work", authors=authors)
    reordered_plan = replace(
        plan,
        lanes=tuple(
            reordered_work if lane.identity == work.identity else lane
            for lane in plan.lanes
        ),
    )
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(reordered_work, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(isbn, ProviderStatus.NO_MATCH),
        ),
        authority="open_library",
    )
    claims = (
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition",
            kind=ClaimKind.TITLE,
            value="Plain Work",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        ),
        *(
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition",
                kind=ClaimKind.CREATOR,
                value=author,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=index,
            )
            for index, author in enumerate(reversed(authors), start=1)
        ),
    )
    dispatch = WorkDispatchLineage.from_query(
        work_query_ladder("Plain Work", authors)[0]
    )
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                reordered_work,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("edition", "work", claims),),
                dispatch_lineage=dispatch,
            ),
        ),
        authority="open_library",
    )

    merged = retry.service(None)._merged(
        reordered_plan, original, (reordered_work,), replacement
    )

    recovered = next(lane for lane in merged.lanes if lane.identity == work.identity)
    assert recovered.status is ProviderStatus.CANDIDATES
    assert [
        (candidate.edition_identity, candidate.work_identity)
        for candidate in recovered.candidates
    ] == [("edition", "work")]
    assert recovered.dispatch_lineage == dispatch
    assert recovered.transformation_lineage is None


def test_retry_successor_merges_and_replays_the_exact_latin_lineage(
    retry: _Retry,
) -> None:
    plan, work, isbn, _overflow = _overflow_source(retry)
    title = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    authors = ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",)
    latin_work = replace(work, title=title, authors=authors)
    latin_plan = replace(
        plan,
        lanes=tuple(
            latin_work if lane.identity == work.identity else lane
            for lane in plan.lanes
        ),
    )
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(latin_work, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(isbn, ProviderStatus.NO_MATCH),
        ),
        authority="open_library",
    )
    claims = (
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition",
            kind=ClaimKind.TITLE,
            value="Cafe",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        ),
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition",
            kind=ClaimKind.CREATOR,
            value="Jose",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=1,
        ),
    )
    admission = admit_provider_work_identity(
        local_title=title,
        local_authors=authors,
        provider_title="Cafe",
        provider_authors=("Jose",),
        provider_work_identity="work",
    )
    assert admission.transformation is not None
    dispatch = WorkDispatchLineage.from_query(work_query_ladder(title, authors)[-1])
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                latin_work,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("edition", "work", claims),),
                dispatch_lineage=dispatch,
                transformation_lineage=admission.transformation,
            ),
        ),
        authority="open_library",
    )

    merged = retry.service(None)._merged(
        latin_plan, original, (latin_work,), replacement
    )
    replayed = _provider_from_data(merged.to_data())
    recovered = next(lane for lane in replayed.lanes if lane.identity == work.identity)

    assert replayed == merged
    assert recovered.dispatch_lineage == dispatch
    assert recovered.transformation_lineage == admission.transformation

    template = synthetic_latin_work_report()
    target = replace(retry.report.target, fields=template.target.fields)
    source_title = LocalClaim.create(
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
    local_claims = (source_title, *retry.report.local_claims)
    qualified = MetadataReport.create(
        target=target,
        file=retry.report.file,
        local_claims=local_claims,
        local_reasons=retry.report.local_reasons,
        providers=(replayed,),
        audio=retry.report.audio,
    )
    qualified_provider = qualified.providers[0]
    qualified_lane = next(
        lane for lane in qualified_provider.lanes if lane.identity == work.identity
    )
    assert qualified_lane.dispatch_lineage == dispatch
    assert qualified_lane.transformation_lineage == admission.transformation

    assert recovered.transformation_lineage is not None
    incomplete = replace(
        recovered.transformation_lineage,
        applications=recovered.transformation_lineage.applications[:1],
    )
    forged = replace(
        qualified_provider,
        lanes=tuple(
            replace(lane, transformation_lineage=incomplete)
            if lane.identity == work.identity
            else lane
            for lane in qualified_provider.lanes
        ),
    )
    with pytest.raises(ValueError, match="work identity lineage is inconsistent"):
        MetadataReport.create(
            target=target,
            file=retry.report.file,
            local_claims=local_claims,
            local_reasons=retry.report.local_reasons,
            providers=(forged,),
            audio=retry.report.audio,
        )


@pytest.mark.parametrize(
    ("candidate_title", "include_neutral_creator"),
    (("Current title", True), ("Ada_Example_-_Current title", False)),
    ids=("mixed_creators", "query_only_filename_prefix"),
)
def test_retry_successor_rejects_invalid_work_identity_before_publication(
    retry: _Retry,
    candidate_title: str,
    include_neutral_creator: bool,
) -> None:
    plan, work, isbn, _overflow = _overflow_source(retry)
    fields = tuple(
        replace(field, state=TargetFieldState.VALUE, value=("Ada Example",))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in TargetSnapshot("ebook", "Current title", ()).fields
    )
    snapshot = TargetSnapshot(
        "ebook", "Current title", retry.snapshot.files, fields=fields
    )
    original = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(work, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(isbn, ProviderStatus.NO_MATCH),
        ),
        authority="open_library",
    )
    source = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item", fields),
        file=retry.report.file,
        local_claims=plan.local_claims,
        local_reasons=("opening_matter_isbn",),
        providers=(original,),
    )
    retry.snapshot = snapshot
    retry.repository = _Repository(source)
    claims = [
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition",
            kind=ClaimKind.TITLE,
            value=candidate_title,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        ),
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition",
            kind=ClaimKind.CREATOR,
            value="Ada Example",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=1,
        ),
    ]
    if include_neutral_creator:
        claims.append(
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition",
                kind=ClaimKind.CREATOR,
                value="Ada Example",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=None,
                raw_order=2,
            )
        )
    dispatch = WorkDispatchLineage.from_query(
        work_query_ladder("Current title", ("Ada Example",))[0]
    )
    replacement = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(
                work,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("edition", "work", tuple(claims)),),
                dispatch_lineage=dispatch,
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

    with pytest.raises(ProviderRetrySuccessorError, match="source is invalid"):
        service.run(
            source.report_id,
            (work.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )


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


def test_an_audio_source_rebuilds_its_asin_plan_and_redispatches() -> None:
    """Recovery over an audiobook rebuilds the plan the source was observed under.

    The lane identities are deterministic in the local claims, so the rebuilt
    ASIN lane is the same lane the failed observation recorded and the selected
    lane reconciles without any change to the successor's own machinery.
    """
    asin = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed="B0SYNE7P1K",
        canonical="B0SYNE7P1K",
        excerpt="B0SYNE7P1K",
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=1,
    )
    snapshot = TargetSnapshot(
        "audiobook", None, (TargetFile("b" * 64, "book.m4b", "m4b"),)
    )
    plan = audiobook_discovery_plan(snapshot, (asin,))
    (lane,) = plan.lanes
    original = recorded_dispatch(
        provider_observation(
            "enrichment",
            (ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE),),
            authority="audnexus",
        )
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64, "m4b"),
        local_claims=(asin,),
        local_reasons=("embedded_tag_asin",),
        providers=(original,),
        audio=_RETAINED_AUDIO,
    )
    repository = _Repository(report)
    replacement = provider_observation(
        "enrichment",
        (
            ProviderLaneResult(
                lane,
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("audnexus-book-B0SYNE7P1K", "unresolved", ()),),
            ),
        ),
        authority="audnexus",
    )
    provider = _Provider(replacement)
    service = GenerateProviderRetrySuccessor(
        target=_Target(snapshot),
        inspector=_Inspector(),
        repository_factory=lambda: cast(ReportRepository, repository),
        audio_inspector=cast("Any", _Inspector()),
        provider_resolver=cast(
            "ProviderRetryResolver",
            lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("enrichment", True, lambda: provider, "audnexus"),
                "audnexus",
                2,
            ),
        ),
        mount=MediaMountConfiguration("audiobooks", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )

    successor = MetadataReport.from_json(
        service.run(
            report.report_id,
            (lane.identity,),
            provider_name="enrichment",
            adapter="audnexus",
            generation=2,
        )
    )

    assert (lane.identity,) == tuple(
        value.identity for value in provider.plans[0].lanes
    )
    assert (ProviderQueryKind.ASIN,) == tuple(
        value.kind for value in provider.plans[0].lanes
    )
    assert (ProviderStatus.CANDIDATES,) == tuple(
        value.status for value in successor.providers[0].lanes
    )
    assert report.report_id != successor.report_id


def test_an_excluded_audiobook_retry_closes_stale_before_any_egress() -> None:
    """A job queued before the dispatch set (#1247) closes alone, as stale.

    The source is a retained audiobook report with an unavailable Google Books
    lane, which preparation no longer produces. Its retry is refused once the
    source is read: no resolver, target, inspector or factory is reached, and
    the stale classification closes only that job with no failure evidence,
    so no ebook retry for the same scope waits on it.
    """
    snapshot, claims, plan = _fed_audio_source(asserted=False)
    (lane,) = plan.lanes
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(
            recorded_dispatch(
                provider_observation(
                    "books",
                    (ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE),),
                    authority="google_books",
                )
            ),
        ),
        audio=_RETAINED_AUDIO,
    )

    class _SourceOnly(_Repository):
        def current_optional(self, *_args):
            pytest.fail("the current report was read")

        def current_review_package(self, *_args):
            pytest.fail("the current package was read")

    class _Unreachable:
        def read(self, *_args: object) -> object:
            pytest.fail("the target was read")

        def unchanged(self, *_args: object) -> bool:
            pytest.fail("the media was inspected")

    def resolver(*_args: object) -> ConfiguredProviderRetry:
        pytest.fail("the provider was resolved")

    repository = _SourceOnly(report)
    service = GenerateProviderRetrySuccessor(
        target=cast("Any", _Unreachable()),
        inspector=cast("Any", _Unreachable()),
        repository_factory=lambda: cast(ReportRepository, repository),
        audio_inspector=cast("Any", _Unreachable()),
        provider_resolver=resolver,
        mount=MediaMountConfiguration("audiobooks", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )

    with pytest.raises(CurrentnessError) as raised:
        service.run(
            report.report_id,
            (lane.identity,),
            provider_name="books",
            adapter="google_books",
            generation=2,
        )

    assert ("stale", None) == classify_provider_recovery_failure(raised.value)
    assert {report.report_id} == set(repository.values)


def test_an_excluded_ebook_retry_closes_stale_before_any_egress(
    retry: _Retry,
) -> None:
    """A job queued before the ebook dispatch set (#1486) closes alone, as stale."""

    def resolver(*_args: object) -> ConfiguredProviderRetry:
        pytest.fail("the provider was resolved")

    with pytest.raises(CurrentnessError) as raised:
        retry.service(None, resolver=resolver).run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="audible_catalog",
            generation=2,
        )

    assert ("stale", None) == classify_provider_recovery_failure(raised.value)
    assert {retry.report.report_id} == set(retry.repository.values)


def _fed_audio_source(
    *, asserted: bool
) -> tuple[TargetSnapshot, tuple[LocalClaim, ...], MetadataDiscoveryPlan]:
    """One audiobook with no local ASIN, optionally asserting one."""
    fields = tuple(
        TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Corin Halloway",)
        )
        if field.destination is TargetDestination.AUTHORS
        else (
            TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, "B0ASSERTED")
            if asserted and field.destination is TargetDestination.ASIN
            else field
        )
        for field in default_target_fields("Ember Tide Protocol")
    )
    snapshot = TargetSnapshot(
        "audiobook",
        "Ember Tide Protocol",
        (TargetFile("b" * 64, "book.m4b", "m4b"),),
        fields=fields,
    )
    claims: tuple[LocalClaim, ...] = (
        target_title_claim(),
        *((target_asin_claim("B0ASSERTED"),) if asserted else ()),
        LocalClaim.create(
            kind=ClaimKind.DURATION,
            observed="43200.000",
            canonical="43200.000",
            excerpt="43200.000",
            applicability="not_applicable",
            basis="embedded_tag",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            occurrence=1,
        ),
    )
    return snapshot, claims, audiobook_discovery_plan(snapshot, claims)


def _fed_successor(
    snapshot: TargetSnapshot, report: MetadataReport, provider: _Provider
) -> GenerateProviderRetrySuccessor:
    return GenerateProviderRetrySuccessor(
        target=_Target(snapshot),
        inspector=_Inspector(),
        repository_factory=lambda: cast(ReportRepository, _Repository(report)),
        audio_inspector=cast("Any", _Inspector()),
        provider_resolver=cast(
            "ProviderRetryResolver",
            lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("enrichment", True, lambda: provider, "audnexus"),
                "audnexus",
                2,
            ),
        ),
        mount=MediaMountConfiguration("audiobooks", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )


def test_an_audio_source_rebuilds_its_asin_feed_plan_for_a_per_asin_provider() -> None:
    """A fed lane is recovered under the feed that built it, not the source plan.

    The report's own plan for this item is a search: it carries no local ASIN.
    The retained observation is nonetheless all ASIN lanes, because phase two
    fed it the asserted ASIN. Recovery rebuilds that same feed from the
    retained document, so the selected lane reconciles exactly.
    """
    snapshot, claims, plan = _fed_audio_source(asserted=True)
    feed = audiobook_asin_feed_plan(snapshot, plan, ())
    assert feed is not None
    (lane,) = feed.lanes
    original = recorded_dispatch(
        provider_observation(
            "enrichment",
            (ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE),),
            authority="audnexus",
        )
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(original,),
        audio=_RETAINED_AUDIO,
    )
    provider = _Provider(
        provider_observation(
            "enrichment",
            (
                ProviderLaneResult(
                    lane,
                    ProviderStatus.CANDIDATES,
                    (ProviderCandidate("audnexus-book-B0ASSERTED", "unresolved", ()),),
                ),
            ),
            authority="audnexus",
        )
    )

    successor = MetadataReport.from_json(
        _fed_successor(snapshot, report, provider).run(
            report.report_id,
            (lane.identity,),
            provider_name="enrichment",
            adapter="audnexus",
            generation=2,
        )
    )

    assert (lane.identity,) == tuple(
        value.identity for value in provider.plans[0].lanes
    )
    assert (ProviderQueryKind.ASIN,) == tuple(
        value.kind for value in provider.plans[0].lanes
    )
    assert claims == provider.plans[0].local_claims
    assert (ProviderStatus.CANDIDATES,) == tuple(
        value.status for value in successor.providers[0].lanes
    )


def test_an_unrebuildable_asin_feed_fails_closed_before_any_dispatch() -> None:
    """Nothing asserts or promotes this ASIN, so no plan can name it again."""
    snapshot, claims, plan = _fed_audio_source(asserted=False)
    assert [ProviderQueryKind.WORK] == [value.kind for value in plan.lanes]
    lane = MetadataDiscoveryLane(
        "lane_" + "e" * 64,
        ProviderQueryKind.ASIN,
        plan.lanes[0].input_references,
        canonical_asin="B0PHANTOM1",
    )
    original = recorded_dispatch(
        provider_observation(
            "enrichment",
            (ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE),),
            authority="audnexus",
        )
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(original,),
        audio=_RETAINED_AUDIO,
    )
    provider = _Provider(original)

    with pytest.raises(ProviderRetrySuccessorError, match="source is invalid"):
        _fed_successor(snapshot, report, provider).run(
            report.report_id,
            (lane.identity,),
            provider_name="enrichment",
            adapter="audnexus",
            generation=2,
        )

    assert [] == provider.plans


@pytest.mark.parametrize(
    "carrier",
    (
        ProviderObservationFailure,
        ProviderObservationCancelled,
        ProviderObservationInterrupted,
    ),
)
def test_declared_partial_failure_publishes_no_retry_successor(retry, carrier):
    partial = ProviderFailureSnapshot(
        evidence=ProviderOperationalEvidence(
            "2023-11-14T22:13:20.000000Z",
            (
                ProviderAttemptRecord(
                    "catalogue", 1, "http_429", 37.0, 0.1, "exhausted"
                ),
            ),
            (),
        )
    )
    cause = RuntimeError("original provider cause")
    failure = carrier(partial)

    class Provider:
        def observe(self, _plan, *, deadline=None):
            raise failure from cause

    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, Provider, "open_library"),
            "open_library",
            2,
        )
    )
    values = dict(retry.repository.values)
    package = retry.repository.package
    expected = (
        ProviderRetrySuccessorError
        if carrier is ProviderObservationFailure
        else carrier
    )
    with pytest.raises(expected) as caught:
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    dispatch_failure = (
        caught.value.__cause__
        if carrier is ProviderObservationFailure
        else caught.value
    )
    assert dispatch_failure is failure
    assert dispatch_failure.__cause__ is cause
    assert dispatch_failure.snapshot is partial
    assert retry.repository.values == values
    assert retry.repository.package == package
    assert retry.repository.current_id == retry.report.report_id


def test_lookup_hint_retry_reconstructs_identical_context_and_asin_lanes() -> None:
    from tests.support.audiobook_providers import audio_target
    from tests.support.audiobook_reports import recording_lookup_report

    source = recording_lookup_report()
    snapshot = replace(
        audio_target(), files=(TargetFile(source.file.identity, "book.m4b", "m4b"),)
    )
    plan = audiobook_discovery_plan(snapshot, source.local_claims, audio=source.audio)
    feed = audiobook_asin_feed_plan(snapshot, plan, source.providers)
    assert feed is not None
    original = recorded_dispatch(
        provider_observation(
            "enrichment",
            tuple(
                ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE)
                for lane in feed.lanes
            ),
            authority="audnexus",
        )
    )
    report = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(*source.providers, original),
        audio=source.audio,
    )
    replacement = provider_observation(
        "enrichment",
        tuple(ProviderLaneResult(lane, ProviderStatus.NO_MATCH) for lane in feed.lanes),
        authority="audnexus",
    )
    provider = _Provider(replacement)
    successor = MetadataReport.from_json(
        _fed_successor(snapshot, report, provider).run(
            report.report_id,
            tuple(lane.identity for lane in feed.lanes),
            provider_name="enrichment",
            adapter="audnexus",
            generation=2,
        )
    )
    assert provider.plans == [feed]
    assert (
        successor.providers[0].recording_lookup_candidates
        == source.providers[0].recording_lookup_candidates
    )


@pytest.mark.parametrize(
    ("status", "outcome", "state"),
    [
        (ProviderStatus.MALFORMED, "terminal_response", "exhausted"),
        (ProviderStatus.UNAVAILABLE, "transient", "waiting"),
        (ProviderStatus.AUTHENTICATION, "authentication", "waiting"),
        (ProviderStatus.RATE_LIMITED, "rate_limited", "waiting"),
        (ProviderStatus.NO_MATCH, "no_match", "recovered"),
        (ProviderStatus.NO_MATCH, "terminal_failure", "waiting"),
    ],
)
def test_actual_successor_runner_retains_one_accounted_settlement(
    retry: _Retry,
    tmp_path: Path,
    status: ProviderStatus,
    outcome: str,
    state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )
    from mechanical_librarian.application.provider_recovery_runner import (
        ScopedProviderRecoveryRunner,
    )

    observation = replace(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, status),),
            authority="open_library",
        ),
        request_accounting=ProviderRequestAccounting(1, 1),
    )
    provider = _Provider(observation)
    evidence: list[RecoveryAttemptEvidence] = []
    service = retry.service(
        ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library",
            2,
        ),
        operational_evidence=evidence.append,
    )
    store = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")
    store.observe_failure(
        connection="catalogue",
        adapter="open_library",
        scope="ebooks",
        generation=2,
        failure="transient",
        now=0,
        retry_after=1,
    )
    job = ProviderRecoveryJob(
        "catalogue",
        "open_library",
        "ebooks",
        2,
        "member",
        "primary",
        "ebooks",
        "item",
        "file",
        retry.report.report_id,
        None,
        retry.report.report_id,
        None,
        None,
        "report_only",
        json.dumps([retry.failed.identity]),
    )
    store.enqueue_jobs((job,), enqueued_at=0)

    def invoke(*_args: object) -> RecoveryAttemptEvidence | None:
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
        return evidence[-1] if evidence else None

    callback: Callable[..., RecoveryAttemptEvidence | None] = invoke
    if outcome == "terminal_failure":
        from mechanical_librarian.application.provider_recovery_activation import (
            ProviderRecoveryActivation,
        )
        from mechanical_librarian.application.review_pipeline import ReviewPipelineError

        class FailingPipeline:
            def run_recovery_successor(self, *_args):
                raise ReviewPipelineError("projection")

        activation = ProviderRecoveryActivation(
            configuration=cast(Any, None),
            repository_factory=lambda: cast(Any, retry.repository),
            observers=cast(Any, None),
            provider_resolver=cast(Any, None),
            pipeline=lambda: cast(Any, FailingPipeline()),
            enqueue=cast(Any, None),
            clock=lambda: 1,
        )
        monkeypatch.setattr(activation, "_admit", lambda _: (True, None))

        def generate(_pipeline, _health, _job, _heartbeat, capture):
            capture(invoke())
            return retry.repository.current_id

        monkeypatch.setattr(activation, "_generate_successor", generate)
        callback = activation.retry_member

    runner = ScopedProviderRecoveryRunner(
        store=store,
        generation=2,
        retry=callback,
        classify_failure=_classify_provider_recovery_failure,
    )
    assert runner.run_one(now=1, token="owner").state == state
    assert store._connection.execute(
        "SELECT outcome,job_attempt,physical_requests,request_count_status FROM provider_recovery_events WHERE job_attempt IS NOT NULL"
    ).fetchall() == [(outcome, 1, 1, "observed")]
    assert len(provider.plans) == 1
    if outcome == "terminal_failure":
        assert store.snapshot()[0].state == "waiting"
        next_eligible = store.snapshot()[0].next_eligible_at
        assert next_eligible is not None and next_eligible > 1
        assert retry.repository.package[1] == retry.report.report_id
        assert store._connection.execute(
            "SELECT state,attempt FROM provider_recovery_jobs"
        ).fetchall() == [("queued", 1)]
    if status is ProviderStatus.NO_MATCH:
        assert retry.repository.current_id != retry.report.report_id
    else:
        assert retry.repository.current_id == retry.report.report_id
        assert evidence == []
    store.close()


def test_real_publication_failure_retains_accounting_without_success_or_new_report(
    retry: _Retry, tmp_path: Path
) -> None:
    from mechanical_librarian.application.provider_recovery_evidence import (
        settled_recovery_evidence,
    )
    from mechanical_librarian.application.review_pipeline import ReviewPipelineError

    path = tmp_path / "reports.sqlite"
    repository = open_repository(path)
    repository.publish(retry.report, lambda: True)
    repository.close()
    observation = replace(
        provider_observation(
            "catalogue",
            (ProviderLaneResult(retry.failed, ProviderStatus.NO_MATCH),),
            authority="open_library",
        ),
        request_accounting=ProviderRequestAccounting(1, 1),
    )
    provider = _Provider(observation)
    successful: list[RecoveryAttemptEvidence] = []

    def fail_stage(*_args: object) -> None:
        raise ReviewPipelineError("projection")

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
        after_publication=fail_stage,
        operational_evidence=successful.append,
    )
    with pytest.raises(ProviderRetrySuccessorError) as raised:
        service.run(
            retry.report.report_id,
            (retry.failed.identity,),
            provider_name="catalogue",
            adapter="open_library",
            generation=2,
        )
    classification = _classify_provider_recovery_failure(raised.value)
    assert classification == ("terminal", None)
    assert settled_recovery_evidence(
        raised.value, classification[0]
    ) == RecoveryAttemptEvidence("terminal_failure", 1, "observed")
    assert successful == []
    repository = open_repository(path)
    assert repository._connection.execute(
        "SELECT count(*) FROM metadata_reports"
    ).fetchone() == (1,)
    assert (
        repository.current_review_package_report("primary", "ebooks", "item")
        == retry.report.report_id
    )
    repository.close()


def _alternative_lanes() -> tuple[
    MetadataDiscoveryPlan, MetadataDiscoveryLane, MetadataDiscoveryLane
]:
    plan = alternative_work_plan()
    asserted, alternative = provider_discovery_lane_order(plan)
    return plan, asserted, alternative


def test_a_suppressed_alternative_lane_is_planned_but_never_retried(
    retry: _Retry,
) -> None:
    plan, asserted, alternative = _alternative_lanes()
    original = provider_observation(
        "audible",
        (ProviderLaneResult(asserted, ProviderStatus.UNAVAILABLE),),
        authority="audible_catalog",
    )
    service = retry.service(None)

    assert (asserted,) == service._selected(plan, original, (asserted.identity,))
    with pytest.raises(ProviderRetrySuccessorError, match="lane is invalid"):
        service._selected(plan, original, (alternative.identity,))
    # Only an alternative lane may be planned and unobserved.
    with pytest.raises(ProviderRetrySuccessorError, match="source is invalid"):
        service._selected(
            plan,
            provider_observation(
                "audible",
                (ProviderLaneResult(alternative, ProviderStatus.UNAVAILABLE),),
                authority="audible_catalog",
            ),
            (alternative.identity,),
        )


def test_a_retried_asserted_no_match_dispatches_no_new_alternative(
    retry: _Retry,
) -> None:
    """A stated limitation: the successor merges over the lanes it observed.

    The asserted lane turning NO_MATCH inside a retry would have permitted the
    alternative, but the successor dispatches only the selected lanes and
    merges over the observed ones, so the alternative stays absent.
    """
    plan, asserted, _alternative = _alternative_lanes()
    original = provider_observation(
        "audible",
        (ProviderLaneResult(asserted, ProviderStatus.UNAVAILABLE),),
        authority="audible_catalog",
    )
    replacement = provider_observation(
        "audible",
        (
            ProviderLaneResult(
                asserted,
                ProviderStatus.NO_MATCH,
                diagnostics=work_ladder_diagnostics(1, 0),
                exhausted_dispatch=tuple(
                    WorkDispatchLineage.from_query(query)
                    for query in work_query_ladder(
                        asserted.title or "", asserted.authors, audiobook=True
                    )
                ),
            ),
        ),
        authority="audible_catalog",
    )

    merged = retry.service(None)._merged(plan, original, (asserted,), replacement)

    assert [(asserted.identity, ProviderStatus.NO_MATCH)] == [
        (lane.identity, lane.status) for lane in merged.lanes
    ]


def test_a_failed_alternative_lane_is_retried_beside_its_asserted_sibling(
    retry: _Retry,
) -> None:
    plan, asserted, alternative = _alternative_lanes()
    original = provider_observation(
        "audible",
        (
            ProviderLaneResult(asserted, ProviderStatus.NO_MATCH),
            ProviderLaneResult(alternative, ProviderStatus.UNAVAILABLE),
        ),
        authority="audible_catalog",
    )
    replacement = provider_observation(
        "audible",
        (ProviderLaneResult(alternative, ProviderStatus.NO_MATCH),),
        authority="audible_catalog",
    )
    service = retry.service(None)

    selected = service._selected(plan, original, (alternative.identity,))
    merged = service._merged(plan, original, selected, replacement)

    assert {
        (asserted.identity, ProviderStatus.NO_MATCH),
        (alternative.identity, ProviderStatus.NO_MATCH),
    } == {(lane.identity, lane.status) for lane in merged.lanes}


def test_an_unselected_lane_keeps_its_recording_lookup_hints(
    retry: _Retry,
) -> None:
    """Retrying the alternative never rewrites the asserted lane's hints (#1257)."""
    from mechanical_librarian.domain.metadata_report import RecordingLookupHint

    plan, asserted, alternative = _alternative_lanes()
    ladder = tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(
            asserted.title or "", asserted.authors, audiobook=True
        )
    )
    hints = (RecordingLookupHint("B000000011", 58_200, ladder[0]),)
    original = provider_observation(
        "audible",
        (
            ProviderLaneResult(
                asserted,
                ProviderStatus.NO_MATCH,
                diagnostics=work_ladder_diagnostics(0, len(ladder)),
                exhausted_dispatch=ladder,
                recording_lookup_hints=hints,
            ),
            ProviderLaneResult(alternative, ProviderStatus.UNAVAILABLE),
        ),
        authority="audible_catalog",
    )
    replacement = provider_observation(
        "audible",
        (ProviderLaneResult(alternative, ProviderStatus.NO_MATCH),),
        authority="audible_catalog",
    )
    service = retry.service(None)

    selected = service._selected(plan, original, (alternative.identity,))
    merged = service._merged(plan, original, selected, replacement)

    recovered = next(
        lane for lane in merged.lanes if lane.identity == asserted.identity
    )
    assert hints == recovered.recording_lookup_hints
    assert ladder == recovered.exhausted_dispatch


def test_a_retry_admitting_a_siblings_hinted_product_still_merges(
    retry: _Retry,
) -> None:
    """The alternative's retry may admit what the asserted lane hinted (#1257).

    The merged observation then retains that ASIN, so the unselected lane's
    hint is dropped as the original preparation would have dropped it, and the
    successor is a valid report rather than a changed sibling.
    """
    from mechanical_librarian.domain.metadata_report import RecordingLookupHint
    from tests.support.audiobook_providers import local_runtime_claim

    target = audio_target("Asserted Title", ("Ada Author",))
    claims = (target_title_claim("Asserted Title"), local_runtime_claim("58244.201"))
    audio = replace(
        single_track_audio_facts(),
        discovery_titles=(AudioDiscoveryTitle("path_leaf", "The Real Title"),),
    )
    plan = audiobook_discovery_plan(target, claims, audio=audio)
    asserted, alternative = provider_discovery_lane_order(plan)
    ladder = tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(
            asserted.title or "", asserted.authors, audiobook=True
        )
    )
    original = recorded_dispatch(
        provider_observation(
            "audible",
            (
                ProviderLaneResult(
                    asserted,
                    ProviderStatus.NO_MATCH,
                    diagnostics=work_ladder_diagnostics(0, len(ladder)),
                    exhausted_dispatch=ladder,
                    recording_lookup_hints=(
                        RecordingLookupHint("B000000011", 58_200, ladder[0]),
                    ),
                ),
                ProviderLaneResult(alternative, ProviderStatus.UNAVAILABLE),
            ),
            authority="audible_catalog",
        )
    )

    def found(_request: httpx.Request) -> httpx.Response:
        product = {
            "asin": "B000000011",
            "title": "The Real Title",
            "authors": [{"name": "Ada Author"}],
            "runtime_length_min": 970,
        }
        return httpx.Response(200, json={"total_results": 1, "products": [product]})

    configured = ConfiguredProviderRetry(
        ProviderBinding(
            "audible",
            True,
            lambda: AudibleCatalogMetadataProvider(
                "audible",
                httpx.Client(transport=httpx.MockTransport(found)),
                262_144,
                5.0,
                marketplaces=("us",),
                clock=lambda: 0.0,
                wall_clock=lambda: 1_700_000_000.0,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            ),
            "audible_catalog",
        ),
        "audible_catalog",
        2,
    )
    service = retry.service(configured)
    selected = service._selected(plan, original, (alternative.identity,))
    replacement, _delay, _evidence = service._observe(
        configured,
        plan,
        selected,
        original,
        retry.report.target,
        deadline=1_700_000_100.0,
    )
    merged = service._merged(plan, original, selected, replacement)

    assert ["audible-product-B000000011"] == [c.identity for c in merged.candidates]
    assert all(not lane.recording_lookup_hints for lane in merged.lanes)
    successor = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", target.fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(merged,),
        audio=audio,
    )
    assert successor == MetadataReport.from_json(successor.to_json())


def test_a_hint_feed_retry_rebuilds_the_identical_asin_lanes() -> None:
    """Retry rebuilds the feed from the persisted hints alone (#1257)."""
    from tests.support.audiobook_reports import recording_lookup_hint_report

    source = recording_lookup_hint_report(hint_asins=("B000000011", "B000000012"))
    snapshot = replace(
        audio_target(), files=(TargetFile(source.file.identity, "book.m4b", "m4b"),)
    )
    plan = audiobook_discovery_plan(snapshot, source.local_claims, audio=source.audio)
    feed = audiobook_asin_feed_plan(snapshot, plan, source.providers)
    assert feed is not None
    assert {"B000000011", "B000000012"} == {lane.canonical_asin for lane in feed.lanes}
    original = recorded_dispatch(
        provider_observation(
            "enrichment",
            tuple(
                ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE)
                for lane in feed.lanes
            ),
            authority="audnexus",
        )
    )
    report = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(*source.providers, original),
        audio=source.audio,
    )
    replacement = provider_observation(
        "enrichment",
        tuple(ProviderLaneResult(lane, ProviderStatus.NO_MATCH) for lane in feed.lanes),
        authority="audnexus",
    )
    provider = _Provider(replacement)
    successor = MetadataReport.from_json(
        _fed_successor(snapshot, report, provider).run(
            report.report_id,
            tuple(lane.identity for lane in feed.lanes),
            provider_name="enrichment",
            adapter="audnexus",
            generation=2,
        )
    )
    assert provider.plans == [feed]
    audible = next(
        value for value in successor.providers if value.provider == "audible"
    )
    assert (
        audible.lanes[0].recording_lookup_hints
        == source.providers[0].lanes[0].recording_lookup_hints
    )


class _FaultingProvider:
    """A provider whose dispatch raises before any lane is sent."""

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        raise RuntimeError("synthetic dispatch fault")


def _empty_configured(adapter: str) -> ConfiguredProviderRetry:
    """A real Audible or iTunes adapter whose every search finds nothing."""

    def empty(_request: httpx.Request) -> httpx.Response:
        if adapter == "audible_catalog":
            return httpx.Response(200, json={"total_results": 0, "products": []})
        return httpx.Response(
            200,
            headers={"content-type": "text/javascript; charset=utf-8"},
            json={"resultCount": 0, "results": []},
        )

    def client() -> AudibleCatalogMetadataProvider | ITunesSearchMetadataProvider:
        bounds = httpx.Client(transport=httpx.MockTransport(empty))
        if adapter == "audible_catalog":
            return AudibleCatalogMetadataProvider(
                adapter,
                bounds,
                262_144,
                5.0,
                marketplaces=("us",),
                clock=lambda: 0.0,
                wall_clock=lambda: 1_700_000_000.0,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
        return ITunesSearchMetadataProvider(
            adapter,
            bounds,
            262_144,
            5.0,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )

    return ConfiguredProviderRetry(
        ProviderBinding(adapter, True, client, adapter), adapter, 2
    )


@pytest.mark.parametrize("adapter", ("audible_catalog", "itunes_search"))
def test_a_faulted_dispatch_records_no_alternative_and_its_retry_succeeds(
    retry: _Retry, adapter: str
) -> None:
    """A lane the adapter never sent is not recorded, so no retry replays it."""
    plan, asserted, alternative = _alternative_lanes()

    initial = observe_provider(adapter, adapter, _FaultingProvider(), plan)

    assert [(asserted.identity, ProviderStatus.UNAVAILABLE)] == [
        (lane.identity, lane.status) for lane in initial.observation.lanes
    ]
    assert alternative.identity not in {
        lane.identity for lane in initial.observation.lanes
    }
    configured = _empty_configured(adapter)
    service = retry.service(configured)
    selected = service._selected(plan, initial.observation, (asserted.identity,))
    observation, _delay, _evidence = service._observe(
        configured,
        plan,
        selected,
        initial.observation,
        retry.report.target,
        deadline=1_700_000_100.0,
    )

    assert [(asserted.identity, ProviderStatus.NO_MATCH)] == [
        (lane.identity, lane.status) for lane in observation.lanes
    ]


@pytest.mark.parametrize("adapter", ("audible_catalog", "itunes_search"))
def test_a_faulted_collapsed_plan_keeps_the_alternative_only_where_it_is_sent(
    retry: _Retry, adapter: str
) -> None:
    """A rungless title plans the alternative alone, which only Audible sends.

    Audible's fault is recorded on that lane and recovered from it; iTunes
    never sends it, so its fault records no lane and queues no retry.
    """
    target = audio_target("Part II", ("Ada Author",))
    claims = (target_title_claim("Part II"),)
    audio = replace(
        single_track_audio_facts(),
        discovery_titles=(AudioDiscoveryTitle("path_leaf", "The Real Title"),),
    )
    plan = audiobook_discovery_plan(target, claims, audio=audio)
    (alternative,) = plan.lanes
    assert "path_leaf" == alternative.title_basis

    initial = observe_provider(adapter, adapter, _FaultingProvider(), plan)
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", target.fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(initial.observation,),
        audio=audio,
    )
    jobs = provider_recovery_jobs(
        report,
        package=(None, report.report_id, None, None, "report_only"),
        configuration=SimpleNamespace(
            providers=(SimpleNamespace(name=adapter, adapter=adapter),),
            provider_recovery_generation=2,
        ),
        scope="audio",
    )

    if adapter == "itunes_search":
        assert () == initial.observation.lanes
        assert () == jobs
        return
    assert [(alternative.identity, ProviderStatus.UNAVAILABLE)] == [
        (lane.identity, lane.status) for lane in initial.observation.lanes
    ]
    assert [[alternative.identity]] == [json.loads(job.lane_ids) for job in jobs]
    configured = _empty_configured(adapter)
    service = retry.service(configured)
    selected = service._selected(plan, initial.observation, (alternative.identity,))
    observation, _delay, _evidence = service._observe(
        configured,
        plan,
        selected,
        initial.observation,
        retry.report.target,
        deadline=1_700_000_100.0,
    )

    assert [(alternative.identity, ProviderStatus.NO_MATCH)] == [
        (lane.identity, lane.status) for lane in observation.lanes
    ]


def test_a_retry_keeps_an_unselected_dispatch_record_and_recomputes_its_own(
    retry: _Retry,
) -> None:
    """Only the retried lane's record is new; its sibling's stays byte-identical."""
    from mechanical_librarian.domain.metadata_report import (
        DispatchOutcome,
        ProviderLaneDispatch,
    )
    from tests.support.audiobook_providers import local_runtime_claim

    target = audio_target("Asserted Title", ("Ada Author",))
    claims = (target_title_claim("Asserted Title"), local_runtime_claim("58244.201"))
    audio = replace(
        single_track_audio_facts(),
        discovery_titles=(AudioDiscoveryTitle("path_leaf", "The Real Title"),),
    )
    plan = audiobook_discovery_plan(target, claims, audio=audio)
    asserted, alternative = provider_discovery_lane_order(plan)
    ladder = tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(
            asserted.title or "", asserted.authors, audiobook=True
        )
    )
    walked = ProviderLaneDispatch(DispatchOutcome.REFUSED, len(ladder), -1200)
    prepared = recorded_dispatch(
        provider_observation(
            "audible",
            (
                ProviderLaneResult(
                    asserted,
                    ProviderStatus.NO_MATCH,
                    diagnostics=work_ladder_diagnostics(0, len(ladder)),
                    exhausted_dispatch=ladder,
                ),
                ProviderLaneResult(alternative, ProviderStatus.UNAVAILABLE),
            ),
            authority="audible_catalog",
        )
    )
    original = replace(
        prepared,
        lanes=tuple(
            replace(lane, dispatch=walked)
            if lane.identity == asserted.identity
            else lane
            for lane in prepared.lanes
        ),
    )
    requests: list[httpx.Request] = []

    def empty(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"total_results": 0, "products": []})

    configured = ConfiguredProviderRetry(
        ProviderBinding(
            "audible",
            True,
            lambda: AudibleCatalogMetadataProvider(
                "audible",
                httpx.Client(transport=httpx.MockTransport(empty)),
                262_144,
                5.0,
                marketplaces=("us",),
                clock=lambda: 0.0,
                wall_clock=lambda: 1_700_000_000.0,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            ),
            "audible_catalog",
        ),
        "audible_catalog",
        2,
    )
    service = retry.service(configured)
    selected = service._selected(plan, original, (alternative.identity,))
    replacement, _delay, _evidence = service._observe(
        configured,
        plan,
        selected,
        original,
        retry.report.target,
        deadline=1_700_000_100.0,
    )
    merged = service._merged(plan, original, selected, replacement)

    lanes = {lane.identity: lane for lane in merged.lanes}
    before = {lane.identity: lane for lane in original.lanes}
    assert before[asserted.identity].to_data() == lanes[asserted.identity].to_data()
    assert ProviderLaneDispatch(DispatchOutcome.UNAVAILABLE) == (
        before[alternative.identity].dispatch
    )
    retried = lanes[alternative.identity]
    assert retried.dispatch == ProviderLaneDispatch(
        DispatchOutcome.EMPTY, len(requests), None
    )
    assert retried.exhausted_dispatch and len(requests) == len(
        retried.exhausted_dispatch
    )
    successor = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", target.fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(merged,),
        audio=audio,
    )
    assert successor == MetadataReport.from_json(successor.to_json())


@pytest.mark.parametrize("raises", (False, True), ids=("returned", "raised"))
def test_a_recovery_dispatch_names_its_failures_whether_it_returns_or_raises(
    retry: _Retry,
    raises: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A raised dispatch's partial evidence still names its failure (#1299)."""
    import hashlib
    import time

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    evidence = ProviderOperationalEvidence(
        attempt_records=(
            ProviderAttemptRecord("catalogue", 1, "http_503", None, 0.5, "exhausted"),
        )
    )

    class FailingProvider:
        def observe(self, plan, *, deadline=None):
            clock[0] += 2
            if raises:
                raise ProviderObservationFailure(
                    ProviderFailureSnapshot(evidence=evidence)
                )
            return ProviderResult(
                provider_observation(
                    "catalogue",
                    tuple(
                        ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE)
                        for lane in plan.lanes
                    ),
                    authority="open_library",
                ),
                evidence=evidence,
            )

    configured = ConfiguredProviderRetry(
        ProviderBinding("catalogue", True, FailingProvider, "open_library"),
        "open_library",
        2,
    )
    service = retry.service(configured)
    try:
        service._observe(
            configured,
            retry.plan,
            (retry.failed,),
            retry.report.providers[0],
            retry.report.target,
            deadline=clock[0] + 60.0,
        )
    except ProviderRetrySuccessorError:
        assert raises

    target = retry.report.target
    material = f"{target.connection}\0{target.library}\0{target.item}".encode()
    assert [
        {
            "event": "provider_request_failed",
            "level": "warning",
            "adapter": "open_library",
            "provider": "catalogue",
            "item_ref": "item-" + hashlib.sha256(material).hexdigest()[:16],
            "phase": "recovery",
            "lane_kind": "isbn",
            "query_family": "identifier",
            "outcome_class": "http_non_success",
            "http_status": 503,
            "attempt_outcome": "exhausted",
            "elapsed_ms": 2000,
            "deadline_ms": 60000,
            "deadline_kind": "recovery_window",
            "count": 1,
        }
    ] == [json.loads(line) for line in capsys.readouterr().err.splitlines()]


def test_a_recovery_window_that_starves_an_audiobook_lane_names_the_stop(
    retry: _Retry,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The event reads the lane dispatch the successor records (#1299)."""
    import time

    from mechanical_librarian.application.provider_evidence import (
        ProviderIncompleteCause,
        ProviderIncompleteCauseRecord,
    )
    from mechanical_librarian.domain.metadata_report import DispatchOutcome

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    target = audio_target("Asserted Title", ("Ada Author",))
    claims = (target_title_claim("Asserted Title"),)
    audio = replace(
        single_track_audio_facts(),
        discovery_titles=(AudioDiscoveryTitle("path_leaf", "The Real Title"),),
    )
    plan = audiobook_discovery_plan(target, claims, audio=audio)
    asserted, alternative = provider_discovery_lane_order(plan)
    original = recorded_dispatch(
        provider_observation(
            "audible",
            tuple(
                ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE)
                for lane in (asserted, alternative)
            ),
            authority="audible_catalog",
        )
    )

    class StarvedProvider:
        def observe(self, plan, *, deadline=None):
            clock[0] += 1.5
            return ProviderResult(
                provider_observation(
                    "audible",
                    tuple(
                        ProviderLaneResult(lane, ProviderStatus.INCOMPLETE)
                        for lane in plan.lanes
                    ),
                    authority="audible_catalog",
                ),
                evidence=ProviderOperationalEvidence(
                    incomplete_causes=(
                        ProviderIncompleteCauseRecord(ProviderIncompleteCause.DEADLINE),
                    )
                ),
            )

    configured = ConfiguredProviderRetry(
        ProviderBinding("audible", True, StarvedProvider, "audible_catalog"),
        "audible_catalog",
        2,
    )
    service = retry.service(configured)
    selected = service._selected(plan, original, (alternative.identity,))
    replacement, _delay, _evidence = service._observe(
        configured, plan, selected, original, retry.report.target, deadline=130.0
    )

    (lane,) = replacement.lanes
    assert DispatchOutcome.STARVED is cast("Any", lane.dispatch).outcome
    (event,) = (json.loads(line) for line in capsys.readouterr().err.splitlines())
    assert {
        "event": "provider_deadline_stop",
        "level": "warning",
        "adapter": "audible_catalog",
        "provider": "audible",
        "phase": "recovery",
        "lane_kind": "work",
        "query_family": "path_leaf",
        "outcome_class": "timeout",
        "http_status": None,
        "stop": "before_dispatch",
        "elapsed_ms": 1500,
        "deadline_ms": 30000,
        "deadline_kind": "recovery_window",
        "count": 1,
    } == {key: value for key, value in event.items() if key != "item_ref"}


def test_an_audible_retry_rebuilds_the_carried_lane_from_the_source_claim() -> None:
    """A failed carried lookup is asked again under the lane it failed under (#1316).

    The source plan for this item is a search; the catalogue was handed it with
    the carried product lane added. Recovery rebuilds that lane from the
    retained `prior_admission` claim rather than refusing it as unplanned.
    """
    snapshot, source_claims, _plan = _fed_audio_source(asserted=False)
    carried_claim = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed="B0CARRIED1",
        canonical="B0CARRIED1",
        excerpt="B0CARRIED1",
        applicability="not_applicable",
        basis="prior_admission",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    claims = (*source_claims, carried_claim)
    plan = audiobook_carried_plan(
        audiobook_discovery_plan(snapshot, claims, audio=_RETAINED_AUDIO)
    )
    lookup = next(value for value in plan.lanes if value.kind is ProviderQueryKind.ASIN)
    search = next(value for value in plan.lanes if value.kind is ProviderQueryKind.WORK)
    original = recorded_dispatch(
        provider_observation(
            "audible",
            (
                ProviderLaneResult(lookup, ProviderStatus.UNAVAILABLE),
                ProviderLaneResult(search, ProviderStatus.NO_MATCH),
            ),
            authority="audible_catalog",
        )
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(original,),
        audio=_RETAINED_AUDIO,
    )
    provider = _Provider(
        provider_observation(
            "audible",
            (ProviderLaneResult(lookup, ProviderStatus.NO_MATCH),),
            authority="audible_catalog",
        )
    )
    service = GenerateProviderRetrySuccessor(
        target=_Target(snapshot),
        inspector=_Inspector(),
        repository_factory=lambda: cast(ReportRepository, _Repository(report)),
        audio_inspector=cast("Any", _Inspector()),
        provider_resolver=cast(
            "ProviderRetryResolver",
            lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("audible", True, lambda: provider, "audible_catalog"),
                "audible_catalog",
                2,
            ),
        ),
        mount=MediaMountConfiguration("audiobooks", "/container", "/service", True),
        limits=ReportLimits(),
        after_publication=lambda *_args: None,
    )

    successor = MetadataReport.from_json(
        service.run(
            report.report_id,
            (lookup.identity,),
            provider_name="audible",
            adapter="audible_catalog",
            generation=2,
        )
    )

    assert ((lookup.identity, "B0CARRIED1"),) == tuple(
        (value.identity, value.canonical_asin) for value in provider.plans[0].lanes
    )
    assert (carried_claim.reference,) == provider.plans[0].lanes[0].input_references
    statuses = {value.identity: value.status for value in successor.providers[0].lanes}
    assert ProviderStatus.NO_MATCH is statuses[lookup.identity]
    assert ProviderStatus.NO_MATCH is statuses[search.identity]
