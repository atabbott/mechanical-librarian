"""#2024: extraction retries a briefly busy store and fails a lasting fault plainly.

A real catalogue file and a real application database sit under the real
extraction stage, the real report generation and the real pipeline. Busy
contention comes from a second connection holding a real lock; the lasting
faults are a real trigger abort and SQLite errors raised at the read-only
validation seam with their codes set.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn, cast

import pytest

from mechanical_librarian.adapters.persistence import catalogue as catalogue_module
from mechanical_librarian.adapters.persistence.catalogue import (
    CatalogueSchemaAdmission,
    SQLiteCatalogueRepository,
    open_catalogue,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    NonClosingRepository,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.reports import SQLiteReportRepository
from mechanical_librarian.application.catalogue import CatalogueEntry, CatalogueReader
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    ReportStorageError,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    ProviderFailureObserver,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
    preparation_failure_reference,
)
from mechanical_librarian.application.review_pipeline_stages import (
    ReviewPipelineStages,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.metadata_report_doubles import FakeInspector, FakeTarget
from tests.support.review_cases import _report

_PRODUCER = "f" * 64
_FINGERPRINT = "a" * 64
_WORK = "SELECT stage,state,failure_code,retry_not_before FROM review_preparation_work"


def _entries() -> tuple[CatalogueEntry, ...]:
    return (
        CatalogueEntry(
            "primary",
            "fiction",
            "lib-1",
            "item-1",
            1,
            "Synthetic work",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            _report().file.identity,
            "/item-1.epub",
            "epub",
            "books",
        ),
    )


class _Harness:
    def __init__(self, directory: Path) -> None:
        directory.chmod(0o700)
        self.path = directory / "application.sqlite3"
        self.catalogue_path = directory / "catalogue.sqlite"
        self.root = open_repository(self.path)
        self.now = 10
        self.admission = CatalogueSchemaAdmission()
        catalogue = open_catalogue(self.catalogue_path, create=True)
        catalogue.acquire_refresh()
        catalogue.publish(_FINGERPRINT, _entries())
        catalogue.close()
        self.root.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
        discovery = self.root.claim_review_pipeline_action(2)
        assert discovery is not None
        self.root.complete_review_discovery(discovery, 1, _FINGERPRINT, _entries(), 3)
        self.published: list[str] = []
        self.before_publication: Callable[[], None] = lambda: None
        self.pipeline = self._pipeline()

    def _generate(
        self,
        action: PipelineAction,
        claim_next: bool,
        repository: ReviewPipelineRepository,
    ) -> str:
        concrete = cast("SQLiteMetadataReportRepository", repository)
        snapshot = TargetSnapshot(
            "ebook",
            "Synthetic work",
            (TargetFile(cast(str, action.file_identity), "book.epub", "epub"),),
        )

        def receipt(_bound: object, report: MetadataReport) -> None:
            self.published.append(report.report_id)
            concrete.complete_review_extraction_publication(
                action,
                self.published[-1],
                self.now,
                claim_next=claim_next,
            )

        service = GenerateMetadataReport(
            target=FakeTarget([snapshot] * 4),
            inspector=FakeInspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: NonClosingRepository(concrete),
            providers=(),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "fiction", "primary", "lib-1", "ebook", "books", ()
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
            publication_receipt=receipt,
        )
        self.before_publication()
        return service.run(cast(str, action.item_id), 0, action.file_identity)

    def _catalogue(self, create: bool) -> SQLiteCatalogueRepository:
        return open_catalogue(
            self.catalogue_path, create=create, admission=self.admission
        )

    def _pipeline(self) -> ReviewPreparationPipeline:
        def unused(*_arguments: object, **_keywords: object) -> NoReturn:
            raise AssertionError("unrelated stage")

        stages = ReviewPipelineStages(
            configuration=ApplicationConfiguration((), (), (), ()),
            catalogue=self._catalogue,
            catalogue_reader=cast(CatalogueReader, None),
            probe_connection=unused,
            connections=frozenset(),
            repository_factory=unused,
            recover_artifacts=lambda: None,
            generate_report=self._generate,
            create_proposal_set=unused,
            admit_review_case=unused,
            cover_considerations=unused,
            recovery_observer=cast(ProviderFailureObserver, None),
            drain_outbox=unused,
            discovery_lock=threading.Lock(),
            clock=lambda: self.now,
        )
        return ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(
                self.path, authority=self.root.authority
            ),
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=unused,
            extract=stages.extract,
            providers=lambda *_: (),
            propose=unused,
            admit=lambda *_: ("no_case", None),
            project=lambda *_: True,
            clock=lambda: self.now,
        )

    def work(self) -> tuple[object, ...]:
        return cast(tuple[object, ...], self.root._connection.execute(_WORK).fetchone())

    def run_once(self) -> None:
        assert self.pipeline.run_next(maximum_actions=1)
        assert not self.pipeline.recovery_required
        assert self.pipeline.owned_claims == ()


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    value = _Harness(tmp_path)
    try:
        yield value
    finally:
        value.root.close()


def _storage_events(err: str) -> list[dict[str, object]]:
    records = [json.loads(line) for line in err.splitlines()]
    assert all(record["event"] == "preparation_storage_failure" for record in records)
    for record in records:
        del record["reference"]
    return records


def _exclusive_holder(path: Path) -> sqlite3.Connection:
    holder = sqlite3.connect(path, timeout=0, isolation_level=None)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def test_busy_catalogue_delays_the_work_and_the_next_pass_extracts_it(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    monkeypatch.setattr(catalogue_module, "_BUSY_TIMEOUT_SECONDS", 0.05)
    holder = _exclusive_holder(harness.catalogue_path)
    try:
        harness.run_once()
    finally:
        holder.close()
    # The stage's ordinary transient retry: delayed ten seconds, not failed.
    assert harness.work() == ("extraction", "delayed", "transient", 20)
    assert harness.published == []
    assert _storage_events(capsys.readouterr().err) == [
        {
            "attempt": 1,
            "code": "transient",
            "event": "preparation_storage_failure",
            "level": "warning",
            "recovery_attempt": 0,
            "sqlite_error_code": sqlite3.SQLITE_BUSY,
            "store": "catalogue",
        }
    ]
    harness.now = 20
    harness.run_once()
    assert len(harness.published) == 1
    assert harness.work()[:3] == ("proposal", "queued", None)
    assert capsys.readouterr().err == ""


def test_a_busy_catalogue_on_every_retry_fails_with_the_logged_reference(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    monkeypatch.setattr(catalogue_module, "_BUSY_TIMEOUT_SECONDS", 0.05)
    holder = _exclusive_holder(harness.catalogue_path)
    try:
        for _ in range(3):
            harness.run_once()
            harness.now += 10
    finally:
        holder.close()
    stage, state, code, _ = harness.work()
    assert (stage, state, code) == ("extraction", "failed", "transient")
    work_id, attempt, recovery_attempt = harness.root._connection.execute(
        "SELECT work_id,attempt,recovery_attempt FROM review_preparation_work"
    ).fetchone()
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [line["code"] for line in lines] == ["transient"] * 3
    # Status derives the failed work's reference from its settled row; the last
    # storage line names the same one, so support can find it (#2024).
    assert lines[-1]["reference"] == preparation_failure_reference(
        work_id, "extraction", attempt, recovery_attempt, "transient"
    )


@pytest.mark.parametrize(
    "code",
    (sqlite3.SQLITE_CORRUPT, None),
    ids=("corrupt", "no-code"),
)
def test_lasting_catalogue_failure_fails_the_work_as_persistence(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: int | None,
) -> None:
    capsys.readouterr()

    def validate(_repository: SQLiteCatalogueRepository) -> None:
        error = sqlite3.DatabaseError("SQL_PRIVATE_SENTINEL")
        if code is not None:
            error.sqlite_errorcode = code
        raise error

    monkeypatch.setattr(
        SQLiteCatalogueRepository, "_validate_existing_readonly", validate
    )
    harness.run_once()
    assert harness.work() == ("extraction", "failed", "persistence", 0)
    assert harness.published == []
    err = capsys.readouterr().err
    assert "SQL_PRIVATE_SENTINEL" not in err
    assert _storage_events(err) == [
        {
            "attempt": 1,
            "code": "persistence",
            "event": "preparation_storage_failure",
            "level": "warning",
            "recovery_attempt": 0,
            "sqlite_error_code": code,
            "store": "catalogue",
        }
    ]


def test_busy_report_publication_delays_the_work_and_then_publishes(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    blocker = sqlite3.connect(harness.path, timeout=0, isolation_level=None)
    failures: list[BaseException] = []
    publish = SQLiteReportRepository.publish

    def contend() -> None:
        # A real writer holds the application database through publication;
        # only this attempt waits 1 ms instead of the production timeout.
        blocker.execute("BEGIN IMMEDIATE")

    def publish_once(repository: SQLiteReportRepository, *args, **kwargs) -> str:
        connection = repository._connection
        (timeout,) = connection.execute("PRAGMA busy_timeout").fetchone()
        if blocker.in_transaction:
            connection.execute("PRAGMA busy_timeout=1")
        try:
            return publish(repository, *args, **kwargs)
        except ReportStorageError as error:
            failures.append(error)
            raise
        finally:
            connection.execute(f"PRAGMA busy_timeout={timeout}")
            if blocker.in_transaction:
                blocker.execute("ROLLBACK")

    monkeypatch.setattr(SQLiteReportRepository, "publish", publish_once)
    harness.before_publication = contend
    try:
        harness.run_once()
    finally:
        if blocker.in_transaction:
            blocker.execute("ROLLBACK")
    monkeypatch.undo()
    blocker.close()
    (failure,) = failures
    assert failure.busy is True
    cause = failure.__cause__
    assert isinstance(cause, sqlite3.OperationalError)
    assert cause.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert harness.work() == ("extraction", "delayed", "transient", 20)
    assert harness.published == []
    assert harness.root._connection.execute(
        "SELECT count(*) FROM metadata_reports"
    ).fetchone() == (0,)
    assert _storage_events(capsys.readouterr().err) == [
        {
            "attempt": 1,
            "code": "transient",
            "event": "preparation_storage_failure",
            "level": "warning",
            "recovery_attempt": 0,
            "sqlite_error_code": sqlite3.SQLITE_BUSY,
            "store": "reports",
        }
    ]
    harness.before_publication = lambda: None
    harness.now = 20
    harness.run_once()
    assert len(harness.published) == 1
    assert harness.work()[:3] == ("proposal", "queued", None)
    assert capsys.readouterr().err == ""


def test_lasting_report_publication_failure_fails_the_work_as_persistence(
    harness: _Harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    writer = sqlite3.connect(harness.path)
    writer.execute(
        "CREATE TRIGGER storage_abort BEFORE INSERT ON metadata_reports "
        "BEGIN SELECT RAISE(ABORT,'SQL_PRIVATE_SENTINEL'); END"
    )
    writer.commit()
    writer.close()
    harness.run_once()
    assert harness.work() == ("extraction", "failed", "persistence", 0)
    err = capsys.readouterr().err
    assert "SQL_PRIVATE_SENTINEL" not in err
    assert _storage_events(err) == [
        {
            "attempt": 1,
            "code": "persistence",
            "event": "preparation_storage_failure",
            "level": "warning",
            "recovery_attempt": 0,
            "sqlite_error_code": sqlite3.SQLITE_CONSTRAINT_TRIGGER,
            "store": "reports",
        }
    ]
