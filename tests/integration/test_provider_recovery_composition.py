from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
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
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    InventaireMetadataProvider,
    InventairePacer,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    ProviderLaneResult,
    ProviderResult,
    TargetFile,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderAttemptRecord,
    ProviderOperationalEvidence,
)
from mechanical_librarian.application.provider_recovery_runner import RecoveryRun
from mechanical_librarian.application.review_pipeline import ReviewPipelineError
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderStatus,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob
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


@pytest.mark.parametrize("adapter", ("google_books", "inventaire"))
def test_three_work_shared_failure_then_real_f22_cutover(adapter: str) -> None:
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

        def google_provider(name, *_args, **_kwargs):
            status = outcomes.pop(0)
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

            clock[0] = 7_237
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
            clock[0] = 7_537
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
            assert [] == outcomes
            supervisor.stop()
