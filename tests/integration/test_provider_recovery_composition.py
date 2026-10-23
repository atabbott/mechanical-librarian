from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    _provider_recovery_outbox_id,
    open_repository,
)
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    InventaireMetadataProvider,
    InventairePacer,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderRequestPacer
from mechanical_librarian.application import provider_retry_successor
from mechanical_librarian.application.catalogue import CatalogueReader
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.failure_diagnostics import (
    provider_result_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    ProviderLaneResult,
    ProviderResult,
    TargetFile,
    TargetSnapshot,
    audiobook_discovery_plan,
    metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderAttemptRecord,
    ProviderOperationalEvidence,
)
from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
)
from mechanical_librarian.application.provider_recovery_runner import RecoveryRun
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
)
from mechanical_librarian.application.review_pipeline_stages import ReviewPipelineStages
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
)
from mechanical_librarian.bootstrap import composition
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
    ProviderObservation,
    ProviderRequestAccounting,
    ProviderStatus,
    TargetFacts,
    audio_set_content_digest,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob
from tests.support.audiobook_providers import target_title_claim
from tests.support.audiobook_reports import (
    audio_local_claims,
    audio_target_fields,
    recorded_dispatch,
)
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.support.proposal_evidence import _claim, _provider


class _ReviewerAccess:
    def dispatch_cover_observation(self):
        raise ReviewerAccessApplicationError("not_configured")

    def current_cover_observation_identity(self):
        return None

    def current_description_rewrite_identity(self):
        return None

    def revision_is_current(self, _revision: int) -> bool:
        return True


def _reviewer_access(
    _database: Path, _configuration: ApplicationConfiguration
) -> ReviewerAccessOperations:
    return cast("ReviewerAccessOperations", _ReviewerAccess())


class _TargetReader:
    snapshots: dict[str, TargetSnapshot] = {}

    def __init__(self, **_kwargs) -> None:
        pass

    def read(self, item: str) -> TargetSnapshot:
        return self.snapshots[item]


class _Inspector:
    def __init__(self, _artifacts) -> None:
        pass

    def unchanged(self, *_args) -> bool:
        return True


class _GoogleProvider:
    def __init__(
        self,
        name: str,
        status: ProviderStatus,
        calls: list[ProviderStatus],
    ) -> None:
        self.name = name
        self.status = status
        self.calls = calls
        self.attempt_records = (
            (ProviderAttemptRecord(name, 1, "http_429", 37.0, 0.1, "exhausted"),)
            if status is ProviderStatus.RATE_LIMITED
            else ()
        )

    def observe(self, plan, *, deadline=None):
        del deadline
        self.calls.append(self.status)
        call_number = len(self.calls)
        lanes = tuple(
            ProviderLaneResult(
                lane,
                self.status,
                (
                    (
                        ProviderCandidate(
                            f"gb-{call_number}", f"gb-work-{call_number}", ()
                        ),
                    )
                    if self.status is ProviderStatus.CANDIDATES
                    else ()
                ),
            )
            for lane in plan.lanes
        )
        return ProviderResult(
            provider_observation(self.name, lanes, authority="google_books"),
            evidence=ProviderOperationalEvidence(attempt_records=self.attempt_records),
        )


def _recovery_configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/run/target-token"
            ),
        ),
        (
            LibraryConfiguration(
                "ebooks", "primary", "library-1", "ebook", "books", ("google",)
            ),
        ),
        (MediaMountConfiguration("books", "/container", "/service", True),),
        (ProviderConnectionConfiguration("google", True, False, None, "google_books"),),
        provider_recovery_generation=1,
        review=ReviewWebConfiguration(
            "https://review.example.invalid",
            "/run/owner-password",
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
        ),
    )


def test_retained_outbox_failure_does_not_skip_an_eligible_recovery_run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
        open_repository(database).close()
        recovery.enqueue_jobs(
            (
                ProviderRecoveryJob(
                    "google",
                    "google_books",
                    "books",
                    1,
                    "member-1",
                    "primary",
                    "ebooks",
                    "eligible-item",
                    "f" * 64,
                    "report-" + "2" * 64,
                    None,
                    "report-" + "2" * 64,
                    None,
                    None,
                    "report_only",
                    "[]",
                ),
            ),
            enqueued_at=0,
        )
        configuration = _recovery_configuration()
        composition.CoverArtifactStore(root / "artifacts", create=True)
        with (
            patch.object(composition.time, "time", return_value=7_200),
            patch.object(
                composition.ProviderRecoveryActivation,
                "drain_outbox",
                return_value=("persistence",),
            ),
            patch.object(
                composition.ScopedProviderRecoveryRunner,
                "run_one",
                autospec=True,
                return_value=RecoveryRun("idle"),
            ) as run_one,
        ):
            supervisor = composition.create_review_pipeline(
                configuration,
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _reviewer_access(database, configuration),
                provider_recovery=recovery,
            )
            with pytest.raises(ReviewPipelineError, match="persistence"):
                supervisor.recover_provider_once()
            assert 1 == run_one.call_count
            assert {"now": 7_200} == run_one.call_args.kwargs
            assert "waiting" == recovery.snapshot()[0].state
            supervisor.stop()


def test_recovery_drain_reports_an_adapter_failure_as_a_closed_pipeline_code() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
        open_repository(database).close()
        configuration = _recovery_configuration()
        composition.CoverArtifactStore(root / "artifacts", create=True)
        supervisor = composition.create_review_pipeline(
            configuration,
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            _reviewer_access(database, configuration),
            provider_recovery=recovery,
        )
        # Each wake's drain opens its own repository, so the owner's explicit
        # recovery must report the pipeline's closed code rather than the
        # adapter exception the lane would otherwise have to know.
        with patch.object(
            composition.ProviderRecoveryActivation,
            "drain_outbox",
            side_effect=MetadataReportError("database is unavailable or incompatible"),
        ):
            with pytest.raises(ReviewPipelineError, match="persistence"):
                supervisor.recover_provider_once()
        supervisor.stop()


def test_generation_rollback_rejects_before_cutover_delivery() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        recovery_path = root / "recovery.sqlite"
        recovery = SQLiteProviderRecoveryStore.open(recovery_path)
        recovery.observe_failure(
            connection="google",
            adapter="google_books",
            scope="ebooks",
            generation=2,
            failure="authentication",
            now=1,
        )
        repository = open_repository(database)
        proposal_id = "proposal_set_" + "1" * 64
        report_id = "metadata_report_" + "2" * 64
        case_id = "review_case_" + "3" * 64
        outbox_id = _provider_recovery_outbox_id(
            "primary",
            "ebooks",
            "item-1",
            proposal_id,
            report_id,
            case_id,
            1,
            "admitted",
        )
        repository._connection.execute("PRAGMA foreign_keys=OFF")
        repository._connection.execute(
            "INSERT INTO provider_recovery_outbox(outbox_id,connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome,created_at,delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                outbox_id,
                "primary",
                "ebooks",
                "item-1",
                proposal_id,
                report_id,
                case_id,
                1,
                "admitted",
                1,
            ),
        )
        repository._connection.execute("PRAGMA foreign_keys=ON")
        repository._connection.commit()
        repository.close()
        metadata_before = database.read_bytes()
        recovery_before = recovery_path.read_bytes()
        configuration = ApplicationConfiguration(
            (
                TargetConnectionConfiguration(
                    "primary", "https://example.invalid", "/run/target-token"
                ),
            ),
            (
                LibraryConfiguration(
                    "ebooks", "primary", "library-1", "ebook", "books", ("google",)
                ),
            ),
            (MediaMountConfiguration("books", "/container", "/service", True),),
            (
                ProviderConnectionConfiguration(
                    "google", True, False, None, "google_books"
                ),
            ),
            provider_recovery_generation=1,
            review=ReviewWebConfiguration(
                "https://review.example.invalid",
                "/run/owner-password",
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
            ),
        )
        composition.CoverArtifactStore(root / "artifacts", create=True)
        with pytest.raises(Exception, match="generation is stale"):
            composition.create_review_pipeline(
                configuration,
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _reviewer_access(database, configuration),
                provider_recovery=recovery,
            )
        assert metadata_before == database.read_bytes()
        assert recovery_before == recovery_path.read_bytes()
        check = sqlite3.connect(database)
        assert (
            1
            == check.execute(
                "SELECT count(*) FROM provider_recovery_outbox WHERE delivered_at IS NULL"
            ).fetchone()[0]
        )
        check.close()
        recovery.close()


@pytest.mark.parametrize(
    "failed_lane_status",
    (ProviderStatus.AUTHENTICATION, ProviderStatus.AUTHORIZATION),
    ids=("authentication", "authorization"),
)
def test_restart_delivers_generation_cutover_once_after_dormant_health(
    failed_lane_status: ProviderStatus,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
        composition.CoverArtifactStore(root / "artifacts", create=True)
        repository = open_repository(database)
        repository.enter_shadow_preview(1)
        scan_id = "review_scan_" + "1" * 64
        repository._connection.execute(
            "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,library_id,producer_fingerprint,state,requested_at,updated_at,catalogue_generation,catalogue_fingerprint,total) VALUES(?,?,?,?,?,'completed',1,1,1,?,1)",
            (scan_id, "primary", "ebooks", "library-1", "2" * 64, "3" * 64),
        )
        item, file_identity = "item-1", f"{10:064x}"
        snapshot = TargetSnapshot(
            "ebook", "Current title", (TargetFile(file_identity, "book.epub", "epub"),)
        )
        _TargetReader.snapshots = {item: snapshot}
        isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
        )
        plan = metadata_discovery_plan(snapshot, (isbn,))
        source = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", item, snapshot.fields),
            file=FileFacts(0, file_identity, f"{20:064x}"),
            local_claims=(isbn,),
            local_reasons=("opening_matter_isbn",),
            providers=(
                provider_observation(
                    "google",
                    tuple(
                        ProviderLaneResult(lane, failed_lane_status)
                        for lane in plan.lanes
                    ),
                    authority="google_books",
                ),
                _provider(
                    "hardcover",
                    "hc",
                    (
                        _claim(
                            "hardcover",
                            "hc",
                            ClaimKind.TITLE,
                            "Recovered title",
                            scope=ClaimScope.WORK,
                        ),
                    ),
                    authority="hardcover",
                ),
                _provider(
                    "open-library",
                    "ol",
                    (
                        _claim(
                            "open-library",
                            "ol",
                            ClaimKind.TITLE,
                            "Recovered title",
                            scope=ClaimScope.WORK,
                        ),
                    ),
                    authority="open_library",
                ),
            ),
        )
        repository.publish(source, lambda: True)
        proposal = evaluate_report(source)
        repository.publish_proposal(proposal)
        admission = json.loads(repository.admit_review_case(proposal))
        assert "admitted" == admission["status"]
        repository.persist_shadow_outcome(admission["review_case_id"], 2)
        predecessor = repository.current_review_package("primary", "ebooks", item)
        assert predecessor is not None
        assert predecessor is not None
        work_id = "review_work_" + f"{4:064x}"
        repository._connection.execute(
            "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,library_name,library_id,item_id,file_identity,source_revision,source_fingerprint,producer_fingerprint,catalogue_generation,catalogue_fingerprint,stage,state,attempt,recovery_attempt,retry_not_before,report_id,proposal_set_id,review_case_id,failure_code,failure_root,outcome,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'complete','complete',0,0,0,?,?,?,NULL,NULL,'case',2)",
            (
                work_id,
                scan_id,
                0,
                "primary",
                "ebooks",
                "library-1",
                item,
                file_identity,
                1,
                "4" * 64,
                "2" * 64,
                1,
                "3" * 64,
                source.report_id,
                predecessor[0],
                predecessor[2],
            ),
        )
        # This models a process death after the ordinary F22 cutover has
        # committed its package/outbox transaction but before delivery.
        outbox_id = _provider_recovery_outbox_id(
            "primary",
            "ebooks",
            item,
            *predecessor,
        )
        repository._connection.execute(
            "INSERT INTO provider_recovery_outbox(outbox_id,connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome,created_at,delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
            (outbox_id, "primary", "ebooks", item, *predecessor, 3),
        )
        repository._connection.commit()
        repository.close()

        generation_one = ApplicationConfiguration(
            (
                TargetConnectionConfiguration(
                    "primary", "https://example.invalid", "/run/target-token"
                ),
            ),
            (
                LibraryConfiguration(
                    "ebooks",
                    "primary",
                    "library-1",
                    "ebook",
                    "books",
                    ("google", "hardcover", "open-library"),
                ),
            ),
            (MediaMountConfiguration("books", "/container", "/service", True),),
            (
                ProviderConnectionConfiguration(
                    "google", True, False, None, "google_books"
                ),
                ProviderConnectionConfiguration(
                    "hardcover", True, False, "/run/hardcover-token", "hardcover"
                ),
                ProviderConnectionConfiguration(
                    "open-library",
                    True,
                    False,
                    None,
                    "open_library",
                    "owner@example.invalid",
                ),
            ),
            provider_recovery_generation=1,
            review=ReviewWebConfiguration(
                "https://review.example.invalid",
                "/run/owner-password",
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
            ),
        )
        recovery.observe_failure(
            connection="google",
            adapter="google_books",
            scope="ebooks",
            generation=1,
            failure=failed_lane_status.value,
            now=0,
        )
        (dormant_health,) = recovery.snapshot()
        assert "dormant" == dormant_health.state
        assert 1 == dormant_health.generation
        assert recovery.claim_one(generation=1, now=9, token="before") is None
        generation_two = replace(generation_one, provider_recovery_generation=2)
        calls: list[ProviderStatus] = []
        clock = [10]
        with (
            patch.object(composition.time, "time", side_effect=lambda: clock[0]),
            patch.object(composition, "AudiobookshelfItemReader", _TargetReader),
            patch.object(composition, "_IsolatedEpubInspector", _Inspector),
            patch.object(composition, "google_books_client", return_value=object()),
            patch.object(
                composition,
                "GoogleBooksMetadataProvider",
                side_effect=lambda name, *_args, **_kwargs: _GoogleProvider(
                    name, ProviderStatus.CANDIDATES, calls
                ),
            ),
            patch.object(
                composition,
                "HardcoverMetadataProvider",
                side_effect=AssertionError("hardcover recovery reran"),
            ),
            patch.object(
                composition,
                "OpenLibraryMetadataProvider",
                side_effect=AssertionError("open-library recovery reran"),
            ),
        ):
            supervisor = composition.create_review_pipeline(
                generation_two,
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _reviewer_access(database, generation_two),
                provider_recovery=recovery,
            )
            supervisor.recover_provider_once()
            check = open_repository(database)
            assert predecessor != check.current_review_package(
                "primary", "ebooks", item
            )
            assert (
                0
                == check._connection.execute(
                    "SELECT count(*) FROM provider_recovery_outbox WHERE delivered_at IS NULL"
                ).fetchone()[0]
            )
            assert (
                2
                == check._connection.execute(
                    "SELECT count(*) FROM provider_recovery_outbox "
                    "WHERE delivered_at IS NOT NULL"
                ).fetchone()[0]
            )
            check.close()
            assert [ProviderStatus.CANDIDATES] == calls
            assert "recovered" == recovery.snapshot()[0].state
            supervisor.stop()
        empty = SQLiteProviderRecoveryStore.open(root / "empty-recovery.sqlite")
        assert () == empty.snapshot()
        assert empty.claim_one(generation=2, now=clock[0], token="empty") is None
        assert (
            0
            == empty._connection.execute(
                "SELECT count(*) FROM provider_recovery_members"
            ).fetchone()[0]
        )
        empty.close()


@pytest.mark.parametrize(
    "adapter,burst",
    (("google_books", False), ("inventaire", False), ("google_books", True)),
)
def test_three_work_shared_failure_then_real_f22_cutover(
    adapter: str, burst: bool
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
        composition.CoverArtifactStore(root / "artifacts", create=True)
        repository = open_repository(database)
        repository.enter_shadow_preview(1)
        scan_id = "review_scan_" + "1" * 64
        repository._connection.execute(
            "INSERT INTO review_preparation_scans(scan_id,connection_name,"
            "library_name,library_id,producer_fingerprint,state,requested_at,"
            "updated_at,catalogue_generation,catalogue_fingerprint,total) "
            "VALUES(?,?,?,?,?,'completed',1,1,1,?,3)",
            (scan_id, "primary", "ebooks", "library-1", "2" * 64, "3" * 64),
        )

        reports: list[MetadataReport] = []
        snapshots: dict[str, TargetSnapshot] = {}
        predecessor_packages: dict[str, tuple] = {}
        for index in range(3):
            item = f"item-{index + 1}"
            file_identity = f"{index + 10:064x}"
            snapshot = TargetSnapshot(
                "ebook",
                f"Current title {index + 1}",
                (TargetFile(file_identity, f"book-{index + 1}.epub", "epub"),),
            )
            snapshots[item] = snapshot
            isbn = LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780306406157",
                canonical="9780306406157",
                excerpt="9780306406157",
                applicability="ebook",
                checksum="valid_isbn_13",
                applicability_basis="ebook",
            )
            plan = metadata_discovery_plan(snapshot, (isbn,))
            google = provider_observation(
                "google",
                tuple(
                    ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE)
                    for lane in plan.lanes
                ),
                authority=adapter,
            )
            title = f"Recovered title {index + 1}"
            hardcover = _provider(
                "hardcover",
                f"hc-{index}",
                (
                    _claim(
                        "hardcover",
                        f"hc-{index}",
                        ClaimKind.TITLE,
                        title,
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority="hardcover",
            )
            open_library = _provider(
                "open-library",
                f"ol-{index}",
                (
                    _claim(
                        "open-library",
                        f"ol-{index}",
                        ClaimKind.TITLE,
                        title,
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority="open_library",
            )
            report = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", item, snapshot.fields),
                file=FileFacts(0, file_identity, f"{index + 20:064x}"),
                local_claims=(isbn,),
                local_reasons=("opening_matter_isbn",),
                providers=tuple(
                    sorted(
                        (google, hardcover, open_library),
                        key=lambda value: value.provider,
                    )
                ),
            )
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            admission = json.loads(repository.admit_review_case(proposal))
            assert "admitted" == admission["status"]
            repository.persist_shadow_outcome(admission["review_case_id"], 2)
            package = repository.current_review_package("primary", "ebooks", item)
            assert package is not None
            assert package is not None
            predecessor_packages[item] = package
            work_id = "review_work_" + f"{index + 4:064x}"
            repository._connection.execute(
                "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,"
                "connection_name,library_name,library_id,item_id,file_identity,"
                "source_revision,source_fingerprint,producer_fingerprint,"
                "catalogue_generation,catalogue_fingerprint,stage,state,attempt,"
                "recovery_attempt,retry_not_before,report_id,proposal_set_id,"
                "review_case_id,failure_code,failure_root,outcome,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'complete','complete',0,0,0,"
                "?,?,?,NULL,NULL,'case',2)",
                (
                    work_id,
                    scan_id,
                    index,
                    "primary",
                    "ebooks",
                    "library-1",
                    item,
                    file_identity,
                    1,
                    "4" * 64,
                    "2" * 64,
                    1,
                    "3" * 64,
                    report.report_id,
                    package[0],
                    package[2],
                ),
            )
            reports.append(report)
        repository._connection.commit()
        repository.close()

        configuration = ApplicationConfiguration(
            (
                TargetConnectionConfiguration(
                    "primary", "https://example.invalid", "/run/target-token"
                ),
            ),
            (
                LibraryConfiguration(
                    "ebooks",
                    "primary",
                    "library-1",
                    "ebook",
                    "books",
                    ("google", "hardcover", "open-library"),
                ),
            ),
            (MediaMountConfiguration("books", "/container", "/service", True),),
            (
                ProviderConnectionConfiguration("google", True, False, None, adapter),
                ProviderConnectionConfiguration(
                    "hardcover", True, False, "/run/hardcover-token", "hardcover"
                ),
                ProviderConnectionConfiguration(
                    "open-library",
                    True,
                    False,
                    None,
                    "open_library",
                    "owner@example.invalid",
                ),
            ),
            provider_recovery_generation=1,
            review=ReviewWebConfiguration(
                "https://review.example.invalid",
                "/run/owner-password",
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
            ),
        )
        recovery.observe_failure(
            connection="google",
            adapter=adapter,
            scope="ebooks",
            generation=1,
            failure="transient",
            now=0,
        )
        jobs = tuple(
            sorted(
                (
                    job
                    for report in reports
                    for job in composition._provider_recovery_jobs(
                        report,
                        package=predecessor_packages[report.target.item],
                        configuration=configuration,
                        scope="ebooks",
                    )
                ),
                key=lambda job: (
                    job.connection,
                    job.adapter,
                    job.scope,
                    job.generation,
                    job.member_id,
                ),
            )
        )
        assert 3 == recovery.enqueue_jobs(jobs, enqueued_at=0)

        calls: list[ProviderStatus] = []
        outcomes = [
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.CANDIDATES,
            ProviderStatus.CANDIDATES,
            ProviderStatus.CANDIDATES,
        ]
        clock = [7_200]
        _TargetReader.snapshots = snapshots

        burst_pacer = ProviderRequestPacer(
            clock=lambda: float(clock[0]), sleep=lambda _: None
        )
        physical: list[httpx.Request] = []

        def burst_response(request: httpx.Request) -> httpx.Response:
            physical.append(request)
            return (
                httpx.Response(
                    403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
                )
                if clock[0] < 7_282
                else httpx.Response(200, json={"totalItems": 0})
            )

        if burst:
            clock[0] = 7_182
            initial = GoogleBooksMetadataProvider(
                "google",
                httpx.Client(transport=httpx.MockTransport(burst_response)),
                65_536,
                pacer=burst_pacer,
                clock=lambda: float(clock[0]),
            ).observe(
                metadata_discovery_plan(snapshots["item-1"], reports[0].local_claims)
            )
            assert initial.observation.status is ProviderStatus.RATE_LIMITED
            assert len(physical) == 1
            clock[0] = 7_200

        def google_provider(name, *_args, **_kwargs):
            status = outcomes.pop(0)
            if burst:
                calls.append(status)
                assert _kwargs["pacer"] is burst_pacer
                return GoogleBooksMetadataProvider(
                    name,
                    httpx.Client(transport=httpx.MockTransport(burst_response)),
                    65_536,
                    pacer=burst_pacer,
                    clock=lambda: float(clock[0]),
                )
            if adapter == "google_books":
                return _GoogleProvider(name, status, calls)
            calls.append(status)

            def respond(request: httpx.Request) -> httpx.Response:
                assert request.url.path == "/api/entities/by-uris"
                if status is ProviderStatus.RATE_LIMITED:
                    return httpx.Response(429, headers={"Retry-After": "37"})
                return httpx.Response(
                    200,
                    json={
                        "entities": {
                            "inv:edition-one": {
                                "uri": "inv:edition-one",
                                "claims": {
                                    "wdt:P212": ["9780306406157"],
                                    "wdt:P1476": ["Recovered title"],
                                },
                            }
                        },
                        "redirects": {"isbn:9780306406157": "inv:edition-one"},
                    },
                )

            return InventaireMetadataProvider(
                name,
                httpx.Client(transport=httpx.MockTransport(respond)),
                65_536,
                pacer=InventairePacer(0.001, sleep=lambda _: None),
                sleep=lambda _: None,
            )

        with (
            patch.object(composition.time, "time", side_effect=lambda: clock[0]),
            # The real successor deadline and synthetic SDK must share a clock
            # domain, independent of the host's monotonic uptime.
            (
                patch.object(
                    provider_retry_successor,
                    "time",
                    SimpleNamespace(monotonic=lambda: float(clock[0])),
                )
                if burst
                else nullcontext()
            ),
            patch.object(composition, "GoogleBooksPacer", return_value=burst_pacer),
            patch.object(composition, "AudiobookshelfItemReader", _TargetReader),
            patch.object(composition, "_IsolatedEpubInspector", _Inspector),
            patch.object(composition, "google_books_client", return_value=object()),
            patch.object(
                composition,
                "GoogleBooksMetadataProvider"
                if adapter == "google_books"
                else "InventaireMetadataProvider",
                side_effect=google_provider,
            ),
            patch.object(
                composition,
                "HardcoverMetadataProvider",
                side_effect=AssertionError("hardcover recovery reran"),
            ),
            patch.object(
                composition,
                "OpenLibraryMetadataProvider",
                side_effect=AssertionError("open-library recovery reran"),
            ),
        ):
            supervisor = composition.create_review_pipeline(
                configuration,
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _reviewer_access(database, configuration),
                provider_recovery=recovery,
            )
            supervisor.recover_provider_once()
            check = open_repository(database)
            assert predecessor_packages == {
                item: check.current_review_package("primary", "ebooks", item)
                for item in snapshots
            }
            assert (
                3
                == check._connection.execute(
                    "SELECT count(*) FROM metadata_reports"
                ).fetchone()[0]
            )
            check.close()
            assert [ProviderStatus.RATE_LIMITED] == calls
            assert "waiting" == recovery.snapshot()[0].state
            diagnostic = recovery._connection.execute(
                "SELECT outcome,job_attempt,physical_requests,request_count_status "
                "FROM provider_recovery_events WHERE job_attempt IS NOT NULL"
            ).fetchall()
            assert len(diagnostic) == 1
            assert diagnostic[0][0] == ("burst_throttled" if burst else "rate_limited")
            assert diagnostic[0][1] == (0 if burst else 1)
            assert diagnostic[0][3] == "observed"
            if burst:
                assert diagnostic[0][2] == 0  # Existing pacer denied actual HTTP.

            if burst:
                assert len(physical) == 1
                assert recovery.snapshot()[0].next_eligible_at == 7_282
            clock[0] = 7_282 if burst else 7_237
            with patch.object(
                recovery, "authorize_fanout", side_effect=KeyboardInterrupt
            ):
                with pytest.raises(KeyboardInterrupt):
                    supervisor.recover_provider_once()
            crash_check = open_repository(database)
            crash_packages = {
                item: crash_check.current_review_package("primary", "ebooks", item)
                for item in snapshots
            }
            crash_check.close()
            assert 1 == sum(
                crash_packages[item] != predecessor_packages[item] for item in snapshots
            )
            assert [ProviderStatus.RATE_LIMITED, ProviderStatus.CANDIDATES] == calls
            assert "checking" == recovery.snapshot()[0].state

            # The completed exact successor is recognized after lease expiry;
            # the canary provider is not contacted again before sibling fanout.
            clock[0] = 7_582 if burst else 7_537
            supervisor.recover_provider_once()
            check = open_repository(database)
            current_packages = {
                item: check.current_review_package("primary", "ebooks", item)
                for item in snapshots
            }
            for item, package in current_packages.items():
                assert package is not None
                assert package is not None
                assert predecessor_packages[item] != package
                assert predecessor_packages[item][1] != package[1]
                assert package[4] in {"admitted", "superseded"}
            assert (
                3
                == check._connection.execute(
                    "SELECT count(*) FROM review_preparation_work "
                    "WHERE stage='complete' AND state='complete' "
                    "AND recovery_source_report_id IS NOT NULL"
                ).fetchone()[0]
            )
            check.close()
            assert 4 == len(calls)
            assert 3 == calls.count(ProviderStatus.CANDIDATES)
            assert "recovered" == recovery.snapshot()[0].state
            diagnostics = recovery._connection.execute(
                "SELECT outcome,request_count_status FROM provider_recovery_events "
                "WHERE job_attempt IS NOT NULL ORDER BY sequence"
            ).fetchall()
            assert len(diagnostics) == 4
            # The interrupted, already committed package is not fetched again;
            # its earlier request count cannot be reconstructed after restart.
            assert diagnostics[1] == ("completed", "unknown")
            assert all(status == "observed" for _, status in diagnostics[2:])
            assert all(
                outcome in {"completed", "no_match"} for outcome, _ in diagnostics[1:]
            )
            assert [] == outcomes
            supervisor.stop()


@pytest.mark.parametrize("audio", (False, True))
def test_google_remaining_burst_is_committed_without_renewal(
    tmp_path: Path, audio: bool
) -> None:

    now = [0.0]
    dispatched: list[httpx.Request] = []
    pacer = ProviderRequestPacer(
        clock=lambda: now[0], sleep=lambda _: pytest.fail("hold must not sleep")
    )
    isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    snapshot = TargetSnapshot(
        "ebook", "Synthetic title", (TargetFile("f" * 64, "book.epub", "epub"),)
    )
    claims: tuple[LocalClaim, ...] = (isbn,)
    audio_facts = None
    if audio:
        snapshot = TargetSnapshot(
            "audiobook",
            "Project Hail Mary",
            (TargetFile("f" * 64, "book.m4b", "m4b"),),
            fields=audio_target_fields(),
        )
        claims = tuple(
            sorted(
                (*audio_local_claims(), target_title_claim()),
                key=lambda claim: claim.reference,
            )
        )
        audio_facts = single_track_audio_facts()
        plan = audiobook_discovery_plan(snapshot, claims, audio=audio_facts)
    else:
        plan = metadata_discovery_plan(snapshot, claims)

    def handler(request: httpx.Request) -> httpx.Response:
        dispatched.append(request)
        return httpx.Response(
            403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
        )

    def observe():
        result = GoogleBooksMetadataProvider(
            "google",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=pacer,
            clock=lambda: now[0],
        ).observe(plan)
        return replace(
            result.observation,
            diagnostics=provider_result_diagnostics(
                result.observation, result.evidence
            ),
        )

    first = observe()
    assert len(dispatched) == 1
    assert provider_recovery_decision(
        first, now=0, google_burst_remaining=pacer.remaining_burst
    ) == ("burst_throttled", 100)
    now[0] = 18
    inherited = observe()
    assert inherited.request_accounting.initial_dispatches == 0
    assert inherited.accessed_at is None
    assert provider_recovery_decision(
        first, now=18, google_burst_remaining=pacer.remaining_burst
    ) == ("burst_throttled", 82)
    assert provider_recovery_decision(
        inherited, now=18, google_burst_remaining=pacer.remaining_burst
    ) == ("burst_throttled", 82)
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-one", snapshot.fields),
        file=FileFacts(
            0,
            "f" * 64,
            audio_set_content_digest(
                tuple(track.digest for track in audio_facts.tracks)
            )
            if audio_facts is not None
            else "e" * 64,
            "m4b" if audio else "epub",
        ),
        local_claims=claims,
        local_reasons=("no_local_audio_claims",) if audio else ("opening_matter_isbn",),
        providers=(recorded_dispatch(inherited) if audio else inherited,),
        audio=audio_facts,
    )
    store = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")

    def unreachable(*_args, **_kwargs):
        raise AssertionError("unrelated stage reached")

    catalogue = SimpleNamespace(
        latest_entry=lambda *_args: SimpleNamespace(
            file_identity="f" * 64, source_fingerprint="a" * 64, eligible=True
        ),
        close=lambda: None,
    )
    repository = open_repository(tmp_path / "reports.sqlite")
    repository.publish(report, lambda: True)
    stages = ReviewPipelineStages(
        configuration=_recovery_configuration(),
        catalogue=lambda _: catalogue,
        catalogue_reader=cast("CatalogueReader", catalogue),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=lambda: None,
        generate_report=lambda *_args: report.to_json(),
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=store,
        drain_outbox=unreachable,
        discovery_lock=Lock(),
        clock=lambda: 18,
        google_burst_remaining=pacer.remaining_burst,
    )
    if audio:
        action = PipelineAction(
            "action",
            "scan",
            "extraction",
            1,
            "primary",
            "ebooks",
            "library-1",
            "b" * 64,
            item_id="item-one",
            file_identity="f" * 64,
            source_fingerprint="a" * 64,
            source_revision=1,
            catalogue_generation=1,
            catalogue_fingerprint="c" * 64,
        )
        assert stages.extract(repository, action, False) == (report.report_id, True)
    else:
        assert ProviderStatus.RATE_LIMITED in stages.providers(
            repository, report.report_id
        )
    repository.close()
    if audio:
        # Google Books is outside the audiobook dispatch set (#1247), so a
        # retained audiobook observation of it is no health evidence: the
        # library-wide row that would hold its ebook recovery is never written.
        assert () == store.snapshot()
        store.close()
    else:
        assert store.snapshot()[0].next_eligible_at == 100
        store.close()
        reopened = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")
        assert reopened.snapshot()[0].next_eligible_at == 100
        assert reopened.snapshot()[0].state == "waiting"
        reopened.close()
    assert len(dispatched) == 1
    assert pacer.remaining_burst() == 82
    now[0] = 100
    assert provider_recovery_decision(
        first, now=100, google_burst_remaining=pacer.remaining_burst
    ) == ("burst_throttled", 1)


@pytest.mark.parametrize(
    "status,authority,categories,physical,reader,expected",
    (
        (
            ProviderStatus.AUTHENTICATION,
            "google_books",
            ("burst_throttle",),
            0,
            True,
            ("authentication", None),
        ),
        (
            ProviderStatus.AUTHORIZATION,
            "google_books",
            ("burst_throttle",),
            0,
            True,
            ("authorization", None),
        ),
        (
            ProviderStatus.RATE_LIMITED,
            "google_books",
            ("daily_quota_exhausted",),
            0,
            True,
            ("quota_exhausted", 28_782),
        ),
        (
            ProviderStatus.RATE_LIMITED,
            "google_books",
            ("burst_throttle", "quota_rate_limit"),
            2,
            True,
            ("rate_limited", None),
        ),
        (
            ProviderStatus.RATE_LIMITED,
            "google_books",
            ("quota_rate_limit",),
            1,
            True,
            ("rate_limited", None),
        ),
        (
            ProviderStatus.RATE_LIMITED,
            "hardcover",
            ("quota_rate_limit",),
            0,
            True,
            ("rate_limited", None),
        ),
        (
            ProviderStatus.RATE_LIMITED,
            "google_books",
            ("quota_rate_limit",),
            0,
            False,
            ("rate_limited", None),
        ),
    ),
)
def test_trusted_google_hold_preserves_failure_precedence(
    status, authority, categories, physical, reader, expected
) -> None:

    observation = ProviderObservation(
        "google",
        status,
        authority=authority,
        diagnostics=tuple(
            sorted(
                (
                    FailureDiagnostic(
                        FailureCategory(category),
                        FailureComponent.PROVIDER,
                        FailureStage.RESPONSE,
                    )
                    for category in categories
                ),
                key=lambda value: value.category.value,
            )
        ),
        request_accounting=ProviderRequestAccounting(
            logical_requests=max(1, physical),
            initial_dispatches=physical,
            undispatched_incomplete=0 if physical else 1,
        ),
    )
    assert (
        provider_recovery_decision(
            observation, now=18, google_burst_remaining=(lambda: 82) if reader else None
        )
        == expected
    )


@pytest.mark.parametrize("scenario", ("unchanged", "changed", "staged"))
def test_composed_audio_recovery_currentness_and_staged_cutover(
    tmp_path: Path, scenario: str
) -> None:
    from mechanical_librarian.application.provider_recovery_activation import (
        ProviderRecoveryActivation,
    )
    from mechanical_librarian.domain.provider_recovery import ProviderRecovery
    from tests.unit.test_provider_retry_successor import _RETAINED_AUDIO

    database = tmp_path / "application.sqlite"
    recovery = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")
    composition.CoverArtifactStore(tmp_path / "artifacts", create=True)
    asin = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed="B08G9PRS1K",
        canonical="B08G9PRS1K",
        excerpt="B08G9PRS1K",
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=1,
    )
    snapshot = TargetSnapshot(
        "audiobook", None, (TargetFile("b" * 64, "book.m4b", "m4b"),)
    )
    lane = audiobook_discovery_plan(snapshot, (asin,)).lanes[0]
    names = (
        ("source-a", "source-b", "source-c", "source-d", "source-e")
        if scenario == "staged"
        else ("source-a", "source-b")
    )
    observations = tuple(
        recorded_dispatch(
            provider_observation(
                name,
                (
                    ProviderLaneResult(
                        lane,
                        ProviderStatus.NO_MATCH
                        if scenario != "staged" and name == names[-1]
                        else ProviderStatus.UNAVAILABLE,
                    ),
                ),
                authority="audnexus",
            )
        )
        for name in names
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item", snapshot.fields),
        file=FileFacts(0, "b" * 64, "c" * 64, "m4b"),
        local_claims=(asin,),
        local_reasons=("embedded_tag_asin",),
        providers=observations,
        audio=_RETAINED_AUDIO,
    )
    repository = open_repository(database)
    repository.enter_shadow_preview(1)
    repository.publish(report, lambda: True)
    package = repository.current_review_package("primary", "audio", "item")
    assert package is not None
    scan = "review_scan_" + "1" * 64
    repository._connection.execute(
        "INSERT INTO review_preparation_scans("
        "scan_id,connection_name,library_name,library_id,producer_fingerprint,"
        "state,requested_at,updated_at,catalogue_generation,catalogue_fingerprint,"
        "total) VALUES(?,?,?,?,?,'completed',1,1,1,?,1)",
        (scan, "primary", "audio", "library-1", "2" * 64, "3" * 64),
    )
    repository._connection.execute(
        "INSERT INTO review_preparation_work("
        "work_id,scan_id,ordinal,connection_name,library_name,library_id,item_id,"
        "file_identity,source_revision,source_fingerprint,producer_fingerprint,"
        "catalogue_generation,catalogue_fingerprint,stage,state,attempt,"
        "recovery_attempt,retry_not_before,report_id,outcome,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'complete','complete',0,0,0,?,"
        "'evidence_only',2)",
        (
            "review_work_" + "4" * 64,
            scan,
            0,
            "primary",
            "audio",
            "library-1",
            "item",
            "b" * 64,
            1,
            "4" * 64,
            "2" * 64,
            1,
            "3" * 64,
            report.report_id,
        ),
    )
    repository._connection.commit()
    repository.close()
    configuration = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/run/target-token"
            ),
        ),
        (
            LibraryConfiguration(
                "audio", "primary", "library-1", "audiobook", "books", names
            ),
        ),
        (MediaMountConfiguration("books", "/container", "/service", True),),
        tuple(
            ProviderConnectionConfiguration(name, True, False, None, "audnexus")
            for name in names
        ),
        provider_recovery_generation=1,
        review=ReviewWebConfiguration(
            "https://review.example.invalid",
            "/run/owner-password",
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
        ),
    )
    for name in names[:-1]:
        recovery.observe_failure(
            connection=name,
            adapter="audnexus",
            scope="audio",
            generation=1,
            failure="transient",
            now=0,
        )
    jobs = tuple(
        job
        for job in composition._provider_recovery_jobs(
            report, package=package, configuration=configuration, scope="audio"
        )
        if job.connection != names[-1]
    )
    assert len(jobs) == (4 if scenario == "staged" else 1)
    assert recovery.enqueue_jobs(jobs, enqueued_at=0) == len(jobs)

    def unreachable(*_args, **_kwargs):
        raise AssertionError("completed or staged recovery replayed observation")

    activation = ProviderRecoveryActivation(
        configuration=configuration,
        repository_factory=lambda: open_repository(database),
        observers=unreachable,
        provider_resolver=unreachable,
        pipeline=lambda: None,
        enqueue=unreachable,
        clock=lambda: 7200,
    )
    staged = None
    if scenario == "staged":
        staged = MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=(
                *observations[:-1],
                recorded_dispatch(
                    provider_observation(
                        names[-1],
                        (ProviderLaneResult(lane, ProviderStatus.NO_MATCH),),
                        authority="audnexus",
                    )
                ),
            ),
            audio=report.audio,
        )
        repository = open_repository(database)
        repository.publish(
            staged,
            lambda: True,
            expected_current=report.report_id,
            after_publication=lambda bound, value: bound.stage_provider_retry_successor(
                report.report_id, value, 3
            ),
        )
        assert repository.current_review_package("primary", "audio", "item") == package
        repository.close()
        assert all(activation._admit(job) == (True, staged.report_id) for job in jobs)

    calls = []
    inspections = []

    class Provider:
        def observe(self, plan, *, deadline=None):
            calls.append(tuple(value.identity for value in plan.lanes))
            return ProviderResult(
                provider_observation(
                    names[0],
                    tuple(
                        ProviderLaneResult(value, ProviderStatus.NO_MATCH)
                        for value in plan.lanes
                    ),
                    authority="audnexus",
                )
            )

    def unchanged(inspector, *_args):
        assert isinstance(inspector, composition._IsolatedAudioInspector)
        inspections.append(inspector)
        return scenario != "changed"

    def provider(name, *_args, **_kwargs):
        assert scenario == "unchanged"
        assert name == names[0]  # The successful sibling is never reconstructed.
        return Provider()

    _TargetReader.snapshots = {"item": snapshot}
    with (
        patch.object(composition.time, "time", return_value=7200),
        patch.object(composition, "AudiobookshelfItemReader", _TargetReader),
        patch.object(composition, "_IsolatedEpubInspector", _Inspector),
        patch.object(composition, "audnexus_client", return_value=object()) as client,
        patch.object(
            composition, "AudnexusMetadataProvider", side_effect=provider
        ) as factory,
        patch.object(composition._IsolatedAudioInspector, "unchanged", unchanged),
    ):
        supervisor = composition.create_review_pipeline(
            configuration,
            database,
            tmp_path / "catalogue.sqlite",
            tmp_path / "artifacts",
            _reviewer_access(database, configuration),
            provider_recovery=recovery,
        )
        try:
            supervisor.recover_provider_once()
            repository = open_repository(database)
            try:
                current = repository.current_review_package("primary", "audio", "item")
                documents = repository._connection.execute(
                    "SELECT document FROM metadata_reports"
                ).fetchall()
                assert report.to_json() in [row[0] for row in documents]
                if scenario == "changed":
                    assert current == package
                    assert len(documents) == 1
                    assert len(inspections) == 1
                    assert calls == []
                    client.assert_not_called()
                    factory.assert_not_called()
                    assert (
                        repository.completed_provider_retry_successor(report.report_id)
                        is None
                    )
                    assert recovery._connection.execute(
                        "SELECT state FROM provider_recovery_jobs"
                    ).fetchall() == [("stale",)]
                else:
                    assert current is not None and current != package
                    assert len(documents) == 2
                    successor_id = repository.completed_provider_retry_successor(
                        report.report_id
                    )
                    assert successor_id == current[1]
                    assert repository._connection.execute(
                        "SELECT stage,state FROM review_preparation_work WHERE recovery_source_report_id=?",
                        (report.report_id,),
                    ).fetchall() == [("complete", "complete")]
                    if staged is not None:
                        assert successor_id == staged.report_id
                        assert calls == []
                        # F22 still validates audio for proposal and admission.
                        assert inspections
                        client.assert_not_called()
                        factory.assert_not_called()
                        assert recovery._connection.execute(
                            "SELECT state,count(*) FROM provider_recovery_jobs WHERE source_report_id=? GROUP BY state",
                            (report.report_id,),
                        ).fetchall() == [("complete", 1), ("queued", 3)]
                        assert recovery._connection.execute(
                            "SELECT count(*) FROM provider_recovery_jobs WHERE source_report_id=?",
                            (staged.report_id,),
                        ).fetchone() == (4,)
                    else:
                        assert len(inspections) == 4
                        assert calls == [(lane.identity,)]
                        factory.assert_called_once()
                        successor = MetadataReport.from_json(
                            next(
                                row[0]
                                for row in documents
                                if row[0] != report.to_json()
                            )
                        )
                        assert successor.providers[1] == observations[1]
                        assert successor.providers[0].status is ProviderStatus.NO_MATCH
            finally:
                repository.close()
            if staged is not None:
                for job in jobs:
                    assert activation._admit(job) == (False, None)
                    activation.retry_member(
                        ProviderRecovery(
                            job.connection,
                            job.adapter,
                            job.scope,
                            1,
                            "checking",
                            1,
                            None,
                        ),
                        job,
                        lambda: None,
                    )
        finally:
            supervisor.stop()


class _StoreRuntime:
    """One real store-authority runtime whose activations reach reconcile."""

    def __init__(self, root: Path, generation: int) -> None:
        from argon2 import PasswordHasher

        from mechanical_librarian.adapters.persistence.documents import _canonical_json
        from mechanical_librarian.application.operation_admission import (
            OperationAdmission,
        )
        from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
        from mechanical_librarian.infrastructure.settings_authority import (
            ReviewBootstrap,
        )
        from mechanical_librarian.interfaces.web.runtime_dispatch import (
            MutableRuntimeDispatcher,
        )

        self.root = root
        self.database = root / "reports.sqlite"
        self.admission = OperationAdmission()
        self.control = open_repository(
            self.database, create=True, cross_thread=True, admission=self.admission
        )
        document = OwnerSettingsDocument.model_validate(
            {
                "schema_version": "owner-settings-v4",
                "revision": 1,
                "connections": [
                    {"name": "primary", "endpoint": "https://library.example.invalid"}
                ],
                "providers": [
                    {"name": "google", "adapter": "google_books", "enabled": True}
                ],
                "libraries": [
                    {
                        "name": "books",
                        "connection": "primary",
                        "library_id": "library-1",
                        "media_kind": "ebook",
                        "provider_connections": ["google"],
                    }
                ],
                "provider_recovery_generation": generation,
            }
        )
        self.control._connection.execute(
            "INSERT INTO owner_settings(singleton,revision,document) VALUES(1,1,?)",
            (_canonical_json(document.model_dump()),),
        )
        self.control._connection.executemany(
            "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
            (
                ("connection.primary.key", "synthetic-target-key"),
                ("owner.password.verifier", PasswordHasher().hash("synthetic-owner")),
            ),
        )
        self.control._connection.commit()
        self.bootstrap = ReviewBootstrap(
            schema_version="review-bootstrap-v2",
            canonical_origin="https://review.example.invalid",
            tls_certificate_file="/synthetic/certificate",
            tls_private_key_file="/synthetic/key",
        )
        composition.CoverArtifactStore(root / "artifacts", create=True)
        self.recovery_path = composition._private_root(self.database) / (
            "provider-recovery.sqlite"
        )
        self.applications: list[object] = []
        self.dispatcher: MutableRuntimeDispatcher | None = None

    def seed(self) -> None:
        """Waiting rate-limited health with queued jobs at the current generation."""
        store = SQLiteProviderRecoveryStore.open(self.recovery_path)
        generation = self.generation()
        store.observe_failure(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=generation,
            failure="rate_limited",
            now=100,
            retry_after=3_600,
        )
        store.enqueue_jobs(
            tuple(
                ProviderRecoveryJob(
                    "google",
                    "google_books",
                    "books",
                    generation,
                    f"member-{index}",
                    "primary",
                    "books",
                    f"item-{index}",
                    "f" * 64,
                    "report-" + "2" * 64,
                    None,
                    "report-" + "2" * 64,
                    None,
                    None,
                    "report_only",
                    "[]",
                )
                for index in range(3)
            ),
            enqueued_at=100,
        )
        store.close()

    def generation(self) -> int:
        return int(
            json.loads(
                self.control._connection.execute(
                    "SELECT document FROM owner_settings WHERE singleton=1"
                ).fetchone()[0]
            )["provider_recovery_generation"]
        )

    def start(self):
        from mechanical_librarian.interfaces.web.runtime_dispatch import (
            MutableRuntimeDispatcher,
        )

        activation = composition._StoreRuntimeActivation(
            self.bootstrap,
            self.database,
            self.root / "catalogue.sqlite",
            self.root / "artifacts",
            self.control.owner_settings_store(),
            self.control.authority,
            operation_admission=self.admission,
        )
        runtime, _ = composition.compose_store_runtime_generation(
            self.bootstrap,
            self.database,
            self.root / "catalogue.sqlite",
            self.root / "artifacts",
            repository_authority=self.control.authority,
            operation_admission=self.admission,
            activation=activation,
        )
        self.dispatcher = MutableRuntimeDispatcher(runtime)
        activation.bind(self.dispatcher)
        self.dispatcher.start()
        return self.applications[-1]

    def stop(self) -> None:
        if self.dispatcher is not None:
            current = self.dispatcher.current()
            current.stop_admission()
            current.begin_drain()
            current.close()
            self.dispatcher = None

    def recovery_state(self) -> dict[str, list[list[object]]]:
        check = sqlite3.connect(self.recovery_path)
        try:
            return json.loads(
                json.dumps(
                    {
                        table: [
                            list(row)
                            for row in check.execute(
                                f"SELECT * FROM {table} ORDER BY 1,2,3,4,5"
                            )
                        ]
                        for table in (
                            "provider_recovery_health",
                            "provider_recovery_jobs",
                            "provider_recovery_events",
                        )
                    }
                )
            )
        finally:
            check.close()

    def close(self) -> None:
        self.stop()
        self.control.close()


@contextmanager
def _quiet_store_runtime(root: Path, generation: int):
    """Hold every background lane closed so only owner actions touch the store."""
    runtime = _StoreRuntime(root, generation)
    compose = composition.compose_review_application

    def capture(**kwargs):
        application = compose(**kwargs)
        runtime.applications.append(application)
        return application

    with (
        patch.object(composition.time, "time", return_value=50_000),
        patch.object(composition, "compose_review_application", side_effect=capture),
        patch.object(
            composition.ReviewPreparationSupervisor, "admit", return_value=None
        ),
        patch.object(
            composition.ContinuousGuardedApplication, "admit", return_value=None
        ),
    ):
        try:
            yield runtime
        finally:
            runtime.close()


def _raised_generation(application, generation: int, via: str) -> None:
    from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument

    if via == "retry":
        application.retry_provider_recovery(generation)
        return
    view = application.owner_settings()
    application.write_owner_settings(
        OwnerSettingsDocument.model_validate(
            {
                **view.document.model_dump(),
                "revision": view.document.revision + 1,
                "provider_recovery_generation": generation + 1,
            }
        ),
        view.document.revision,
        (),
    )


def test_retry_now_has_exactly_the_effect_of_raising_the_generation_field() -> None:
    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
    )

    states: dict[str, dict[str, list[list[object]]]] = {}
    for via in ("settings", "retry"):
        with (
            tempfile.TemporaryDirectory() as temporary,
            _quiet_store_runtime(Path(temporary), 2) as runtime,
        ):
            runtime.seed()
            application = runtime.start()
            before = runtime.recovery_state()
            assert {2} == {row[3] for row in before["provider_recovery_health"]}
            _raised_generation(application, 2, via)
            assert 3 == runtime.generation()
            states[via] = runtime.recovery_state()
            if via == "retry":
                successor = runtime.applications[-1]
                assert successor is not application
                for stale in (2, 1):
                    with pytest.raises(
                        ReviewApplicationError, match="provider_recovery_stale"
                    ):
                        successor.retry_provider_recovery(stale)
                assert 3 == runtime.generation()
                assert states[via] == runtime.recovery_state()
    assert states["settings"] == states["retry"]
    health = states["retry"]["provider_recovery_health"]
    assert [["google", "google_books", "books", 3, "waiting", 0, 50_000]] == [
        row[:7] for row in health
    ]
    jobs = states["retry"]["provider_recovery_jobs"]
    assert 3 == len(jobs) and {(3, "queued", 0)} == {
        (row[3], row[16], row[17]) for row in jobs
    }


@pytest.mark.parametrize("path", ["retry_now", "settings_save"])
def test_a_failed_settings_commit_never_moves_recovery_past_a_restart(
    path: str,
) -> None:
    from mechanical_librarian.application.owner_settings import (
        OwnerSettingsOperations,
    )
    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
    )
    from mechanical_librarian.domain.owner_settings import (
        OwnerSettingsDocument,
        OwnerSettingsError,
    )

    with (
        tempfile.TemporaryDirectory() as temporary,
        _quiet_store_runtime(Path(temporary), 1) as runtime,
    ):
        runtime.seed()
        application = runtime.start()
        before = runtime.recovery_state()

        def refuse(*_args, **_kwargs):
            raise OwnerSettingsError("owner settings are unavailable")

        def save_raised_generation() -> object:
            view = application.owner_settings()
            current = cast("OwnerSettingsDocument", getattr(view, "document"))
            raised = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "provider_recovery_generation": 2,
                }
            )
            return application.write_owner_settings(raised, current.revision, ())

        # The commit fails after the successor runtime was composed, which is
        # where the base reconciled recovery to the raised generation.
        with patch.object(OwnerSettingsOperations, "write", refuse):
            with pytest.raises(ReviewApplicationError, match="settings_unavailable"):
                if path == "retry_now":
                    application.retry_provider_recovery(1)
                else:
                    save_raised_generation()
        assert 1 == runtime.generation()
        assert before == runtime.recovery_state()
        runtime.stop()
        # The restart a stuck owner reaches for now starts.
        restarted = runtime.start()
        assert restarted.preparation().to_data()["provider_throttling"] == {
            "generation": 1,
            "retry": "available",
            "entries": [
                {
                    "library": "books",
                    "adapter": "google_books",
                    "state": "waiting",
                    "reasons": ["rate_limited"],
                    "deferred": 3,
                    "next_retry_at": 3_700,
                    "remedy": "add_api_key",
                }
            ],
        }


def test_paused_preparation_still_runs_scheduled_provider_recovery() -> None:
    """Pause stops new scans; the recovery lane keeps its own schedule (#1282)."""
    import threading

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
        open_repository(database).close()
        configuration = _recovery_configuration()
        composition.CoverArtifactStore(root / "artifacts", create=True)
        woke = threading.Event()
        observed: list[bool] = []

        def run_one(_runner, *, now: int) -> RecoveryRun:
            observed.append(supervisor.paused())
            woke.set()
            return RecoveryRun("idle")

        with patch.object(
            composition.ScopedProviderRecoveryRunner, "run_one", autospec=True
        ) as patched:
            patched.side_effect = run_one
            supervisor = composition.create_review_pipeline(
                configuration,
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _reviewer_access(database, configuration),
                provider_recovery=recovery,
            )
            supervisor.set_paused(True)
            supervisor.start()
            try:
                assert woke.wait(5)
            finally:
                supervisor.stop()
        assert observed and all(observed)
