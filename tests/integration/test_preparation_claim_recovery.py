"""Real SQLite claims remain owned across committed handoff and failed settlement."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from tests.support.review_cases import _report

_PRODUCER = "f" * 64


@pytest.fixture
def root(
    tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[SQLiteMetadataReportRepository]:
    repository = open_repository(tmp_path / "application.sqlite3")
    report = _report()
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    discovery = repository.claim_review_pipeline_action(2)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        tuple(
            CatalogueEntry(
                "primary",
                "fiction",
                "lib-1",
                f"item-{index}",
                1,
                "Synthetic work",
                ("Example Author",),
                True,
                False,
                "singular-supported",
                report.file.identity,
                "/item-1.epub",
                "epub",
                "books",
            )
            for index in range(1, getattr(request, "param", 1) + 1)
        ),
        3,
    )
    try:
        yield repository
    finally:
        repository.close()


def _pipeline(
    root: SQLiteMetadataReportRepository,
    path: Path,
    *,
    blocked_settlement: bool = False,
) -> ReviewPreparationPipeline:
    def extract(repository: ReviewPipelineRepository, _: PipelineAction, __: bool):
        concrete = cast("SQLiteMetadataReportRepository", repository)
        report = _report()
        concrete.publish(report, lambda: True)
        return report.report_id, False

    def factory() -> SQLiteMetadataReportRepository:
        repository = open_repository(path, authority=root.authority)
        if blocked_settlement:
            repository._connection.execute("PRAGMA busy_timeout=1")
        return repository

    return ReviewPreparationPipeline(
        repository_factory=factory,
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=extract,
        providers=lambda *_: (),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: ("no_case", None),
        project=lambda *_: True,
        clock=lambda: 10,
    )


@pytest.mark.parametrize("boundary", ("notification", "dwell", "outbox"))
def test_committed_successor_is_settled_instead_of_completed_predecessor(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    boundary: str,
) -> None:
    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path)

    def fail(*_: object) -> None:
        raise RuntimeError("private post-commit value")

    if boundary == "notification":
        pipeline.attach_updates(fail)
    elif boundary == "dwell":
        pipeline._cooperative_wait = fail
    else:
        pipeline._after_advance = fail
    assert pipeline.run_next()
    stage, state, report_id, attempt = root._connection.execute(
        "SELECT stage,state,report_id,attempt FROM review_preparation_work"
    ).fetchone()
    assert stage == "provider"
    assert state == ("queued" if boundary == "outbox" else "failed")
    assert report_id == _report().report_id
    assert attempt == 1
    assert pipeline.owned_claims == ()
    assert not pipeline.recovery_required


def test_reader_blocked_parser_settlement_retains_claim_and_stops_admission(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path, blocked_settlement=True)
    reader = sqlite3.connect(path)

    def refuse(*_: object):
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM review_preparation_work").fetchall()
        raise ReviewPipelineError("parser_refused", root="private input path")

    pipeline._extract = refuse
    try:
        with pytest.raises(ReviewPipelineError, match="persistence"):
            pipeline.run_next()
        assert pipeline.recovery_required
        assert len(pipeline.owned_claims) == 1
        owned = pipeline.owned_claims[0]
        assert owned.stage == "extraction" and owned.attempt == 1
        (incident,) = pipeline.recovery_incidents
        assert incident.code == "parser_refused"
        assert incident.settlement_code == "persistence"
        assert incident.scope == "runtime"
        assert "private" not in str(incident.to_data())
        assert root._connection.execute(
            "SELECT stage,state,attempt FROM review_preparation_work"
        ).fetchone() == ("extraction", "running", 1)
        pipeline.resume_claiming()
        assert not pipeline.run_next()
        assert pipeline.owned_claims == (owned,)
        assert not caplog.records
    finally:
        reader.rollback()
        reader.close()


def test_unsettled_committed_successor_restarts_without_repeating_extraction(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path, blocked_settlement=True)
    reader = sqlite3.connect(path)
    extracted: list[bool] = []
    original = pipeline._extract

    def extract(*args):
        extracted.append(True)
        return original(*args)

    def interrupted_dwell() -> None:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM review_preparation_work").fetchall()
        raise RuntimeError("private notification payload")

    pipeline._extract = extract
    pipeline._cooperative_wait = interrupted_dwell
    try:
        with pytest.raises(ReviewPipelineError, match="persistence"):
            pipeline.run_next()
        (owned,) = pipeline.owned_claims
        assert owned.stage == "provider" and owned.attempt == 1
        assert root._connection.execute(
            "SELECT stage,state,attempt,report_id FROM review_preparation_work"
        ).fetchone() == ("provider", "running", 1, _report().report_id)
        assert extracted == [True]
        assert pipeline.recovery_required
    finally:
        reader.rollback()
        reader.close()
    restarted = _pipeline(root, path)
    restarted._extract = lambda *_: (_ for _ in ()).throw(
        AssertionError("committed extraction must not repeat")
    )
    providers: list[str] = []

    def record_provider(_, report):
        providers.append(report)
        return ()

    restarted._providers = record_provider
    restarted.recover()
    assert restarted.run_next(maximum_actions=1)
    assert providers == [_report().report_id]
    assert root._connection.execute(
        "SELECT stage,state,attempt,report_id FROM review_preparation_work"
    ).fetchone() == ("proposal", "queued", 0, _report().report_id)
    assert not restarted.recovery_required
    assert restarted.owned_claims == ()


@pytest.mark.parametrize("terminal", (False, True))
def test_publication_commit_transfers_owner_before_aggregate_readback(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: bool,
) -> None:
    pipeline = _pipeline(root, tmp_path / "application.sqlite3")

    def extract(
        repository: ReviewPipelineRepository, action: PipelineAction, claim_next: bool
    ):
        concrete = cast("SQLiteMetadataReportRepository", repository)
        report = _report()

        def readback(_: str) -> str:
            if terminal:
                assert pipeline.owned_claims == ()
            else:
                (owned,) = pipeline.owned_claims
                assert owned.stage == "proposal"
                assert owned.report_id == report.report_id
            raise RuntimeError("private aggregate readback failure")

        monkeypatch.setattr(concrete._reports, "get", readback)
        concrete.publish(
            report,
            lambda: True,
            after_publication=lambda *_: (
                concrete.complete_review_extraction_publication(
                    action,
                    report.report_id,
                    10,
                    provider_failure_code="evidence_only" if terminal else None,
                    claim_next=claim_next,
                )
            ),
        )
        raise AssertionError("readback must fail after ownership transfers")

    pipeline._extract = extract
    assert pipeline.run_next()
    assert pipeline.owned_claims == ()
    assert not pipeline.recovery_required
    assert root._connection.execute(
        "SELECT stage,state,report_id FROM review_preparation_work"
    ).fetchone() == (
        "complete" if terminal else "proposal",
        "complete" if terminal else "failed",
        _report().report_id,
    )


@pytest.mark.parametrize("boundary", ["parser", "currentness"])
def test_stop_before_publication_commits_checkpoint_without_successor_claim(
    root: SQLiteMetadataReportRepository, tmp_path: Path, boundary: str
) -> None:
    import threading

    pipeline = _pipeline(root, tmp_path / "application.sqlite3")
    reached = threading.Event()
    proceed = threading.Event()
    failures: list[BaseException] = []
    extractions: list[str] = []

    def wait() -> None:
        reached.set()
        assert proceed.wait(5)

    def extract(repository, action, claim_next):
        extractions.append(action.action_id)
        if boundary == "parser":
            wait()
        report = _report()

        def currentness() -> bool:
            # External currentness must not hold the publication claim gate.
            assert pipeline._claim_lock.acquire(blocking=False)
            pipeline._claim_lock.release()
            if boundary == "currentness":
                wait()
            return True

        repository.publish(
            report,
            currentness,
            after_publication=lambda *_: (
                repository.complete_review_extraction_publication(
                    action, report.report_id, 10, claim_next=claim_next
                )
            ),
        )
        return report.report_id, True

    pipeline._extract = extract

    def run() -> None:
        try:
            pipeline.run_next()
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert reached.wait(5)
        pipeline.stop_for_recovery("persistence")
    finally:
        proceed.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failures == []
    assert len(extractions) == 1
    assert pipeline.recovery_required
    assert pipeline.owned_claims == ()
    assert root._connection.execute(
        "SELECT stage,state,attempt,report_id FROM review_preparation_work"
    ).fetchone() == ("proposal", "queued", 0, _report().report_id)
    assert not pipeline.run_next()
    restarted = _pipeline(root, tmp_path / "application.sqlite3")
    restarted.recover()
    restarted._extract = lambda *_: (_ for _ in ()).throw(
        AssertionError("repeated extraction")
    )
    assert restarted.run_next(maximum_actions=1)
    assert len(extractions) == 1


@pytest.mark.parametrize("root", [3], indirect=True)
@pytest.mark.parametrize("blocked_settlement", [False, True])
def test_two_real_lanes_stop_with_exact_failed_owner_and_drain_other_checkpoint(
    root: SQLiteMetadataReportRepository, tmp_path: Path, blocked_settlement: bool
) -> None:
    import asyncio
    import threading
    from dataclasses import replace

    from mechanical_librarian.application.live_updates import LiveUpdateChannel
    from mechanical_librarian.domain.metadata_report import MetadataReport

    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path)
    updates = LiveUpdateChannel()
    broken = updates.subscribe()
    healthy = updates.subscribe(0)
    failed_wakes: list[bool] = []
    healthy_wakes: list[bool] = []
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()

    def wake_closed_loop() -> None:
        failed_wakes.append(True)
        closed_loop.call_soon_threadsafe(lambda: None)

    broken.wake_with(wake_closed_loop)
    healthy.wake_with(lambda: healthy_wakes.append(True))
    pipeline.attach_updates(updates.publish)
    initial_failed_wakes = len(failed_wakes)
    both = threading.Barrier(2)
    stopped = threading.Event()
    readers: dict[int, sqlite3.Connection] = {}
    executions: list[str] = []
    failures: list[BaseException] = []

    def extract(repository, action, claim_next):
        executions.append(action.item_id)
        both.wait(timeout=5)
        if blocked_settlement and action.item_id == "item-1":
            repository._connection.execute("PRAGMA busy_timeout=1")
            reader = sqlite3.connect(path)
            readers[threading.get_ident()] = reader
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM review_preparation_work").fetchall()
            raise ReviewPipelineError("parser_refused")
        assert action.item_id in {"item-1", "item-2"}
        if blocked_settlement:
            assert stopped.wait(5)
        original = _report()
        report = MetadataReport.create(
            target=replace(original.target, item=action.item_id),
            file=original.file,
            local_claims=original.local_claims,
            local_reasons=original.local_reasons,
            providers=(),
        )
        repository.publish(
            report,
            lambda: True,
            after_publication=(
                lambda *_: repository.complete_review_extraction_publication(
                    action, report.report_id, 10, claim_next=claim_next
                )
            )
            if blocked_settlement
            else None,
        )
        return report.report_id, blocked_settlement

    pipeline._extract = extract

    def run() -> None:
        try:
            pipeline.run_next(maximum_actions=64 if blocked_settlement else 1)
        except BaseException as error:
            failures.append(error)
        finally:
            reader = readers.pop(threading.get_ident(), None)
            if reader is not None:
                reader.rollback()
                reader.close()
                stopped.set()

    workers = [threading.Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert not any(worker.is_alive() for worker in workers)
    assert len(failed_wakes) > initial_failed_wakes
    assert healthy_wakes and healthy.take()[-1].sequence > 0
    updates.close()
    assert broken.closed and healthy.closed
    assert sorted(executions) == ["item-1", "item-2"]
    if not blocked_settlement:
        assert failures == []
        assert not pipeline.recovery_required and not pipeline.owned_claims
        assert root._connection.execute(
            "SELECT item_id,stage,state,attempt FROM review_preparation_work ORDER BY item_id"
        ).fetchall() == [
            ("item-1", "provider", "queued", 0),
            ("item-2", "provider", "queued", 0),
            ("item-3", "extraction", "queued", 0),
        ]
        return
    assert len(failures) == 1
    assert isinstance(failures[0], ReviewPipelineError)
    assert str(failures[0]) == "persistence"
    assert pipeline.recovery_required
    (owned,) = pipeline.owned_claims
    assert (owned.item_id, owned.stage, owned.attempt) == ("item-1", "extraction", 1)
    assert root._connection.execute(
        "SELECT item_id,stage,state,attempt FROM review_preparation_work ORDER BY item_id"
    ).fetchall() == [
        ("item-1", "extraction", "running", 1),
        ("item-2", "proposal", "queued", 0),
        ("item-3", "extraction", "queued", 0),
    ]
    (incident,) = pipeline.recovery_incidents
    assert (incident.code, incident.settlement_code) == (
        "parser_refused",
        "persistence",
    )
    pipeline.resume_claiming()
    assert not pipeline.run_next()
    restarted = _pipeline(root, path)
    restarted.recover()
    assert root._connection.execute(
        "SELECT item_id,stage,state,attempt FROM review_preparation_work ORDER BY item_id"
    ).fetchall() == [
        ("item-1", "extraction", "queued", 1),
        ("item-2", "proposal", "queued", 0),
        ("item-3", "extraction", "queued", 0),
    ]
    assert executions.count("item-2") == 1


@pytest.mark.parametrize(
    "boundary",
    ["aggregate", "application", "close", "parse", "audio_recovery", "release"],
)
@pytest.mark.parametrize("terminal", [False, True])
def test_actual_report_application_and_stage_faults_use_committed_owner(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    terminal: bool,
) -> None:
    import threading

    from mechanical_librarian.application.catalogue import CatalogueReader
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
        TargetFile,
        TargetSnapshot,
    )
    from mechanical_librarian.application.provider_recovery_evidence import (
        ProviderFailureObserver,
    )
    from mechanical_librarian.application.review_pipeline_stages import (
        ReviewPipelineStages,
    )
    from mechanical_librarian.bootstrap.composition import _NonClosingRepository
    from mechanical_librarian.domain.audio_inspection import AudioTrackDigest
    from mechanical_librarian.domain.metadata_report import MetadataReport
    from tests.support.metadata_report_doubles import (
        FakeAudioInspector,
        FakeInspector,
        FakeTarget,
        audio_result,
    )

    pipeline = _pipeline(root, tmp_path / "application.sqlite3")
    published: list[str] = []
    hit: list[str] = []
    inspectors: list[FakeInspector] = []

    def fail() -> None:
        assert pipeline._claim_lock.locked() is (boundary == "release")
        if terminal:
            assert pipeline.owned_claims == ()
        else:
            (owned,) = pipeline.owned_claims
            assert owned.stage == "proposal"
            assert owned.report_id == published[0]
        hit.append(boundary)
        raise RuntimeError("private fault value")

    def generate(action, claim_next, repository):
        audio = boundary == "audio_recovery"
        selected = TargetFile(
            action.file_identity,
            "book.m4b" if audio else "book.epub",
            "m4b" if audio else "epub",
        )
        snapshot = TargetSnapshot(
            "audiobook" if audio else "ebook", "Synthetic work", (selected,)
        )
        inspector = FakeInspector(InspectionResult("c" * 64, ()))
        inspectors.append(inspector)
        borrowed = _NonClosingRepository(repository)

        def receipt(_bound, report):
            published.append(report.report_id)
            repository.complete_review_extraction_publication(
                action,
                report.report_id,
                10,
                provider_failure_code="evidence_only" if terminal else None,
                claim_next=claim_next,
            )

        if boundary == "aggregate":
            monkeypatch.setattr(repository._reports, "get", lambda _: fail())
        elif boundary == "application":
            monkeypatch.setattr(borrowed, "get", lambda _: fail())
        elif boundary == "close":
            monkeypatch.setattr(borrowed, "close", fail)
        if boundary == "release":
            from mechanical_librarian.adapters.persistence.transactions import (
                TransactionScope,
            )

            release = TransactionScope._release_admission
            fired = False

            def release_once(scope) -> None:
                nonlocal fired
                release(scope)
                if not fired and scope._savepoint.startswith("publish_"):
                    fired = True
                    fail()

            monkeypatch.setattr(TransactionScope, "_release_admission", release_once)
        service = GenerateMetadataReport(
            target=FakeTarget([snapshot] * 4),
            inspector=inspector,
            repository_factory=lambda: borrowed,
            providers=(),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "fiction",
                "primary",
                "lib-1",
                "audiobook" if audio else "ebook",
                "books",
                (),
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
            audio_inspector=FakeAudioInspector(
                audio_result(
                    tracks=(
                        AudioTrackDigest(
                            1, action.file_identity, "d" * 64, "m4b", "book.m4b"
                        ),
                    )
                )
            )
            if audio
            else None,
            publication_receipt=receipt,
        )
        serialized = service.run(action.item_id, 0, action.file_identity)
        if boundary == "parse":
            monkeypatch.setattr(MetadataReport, "from_json", lambda _: fail())
        return serialized

    def unused(*_args, **_kwargs):
        raise AssertionError("unrelated stage")

    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unused,
        catalogue_reader=cast(CatalogueReader, None),
        probe_connection=unused,
        connections=frozenset(),
        repository_factory=unused,
        recover_artifacts=lambda: None,
        generate_report=generate,
        create_proposal_set=unused,
        admit_review_case=unused,
        cover_considerations=unused,
        recovery_observer=cast(ProviderFailureObserver, None),
        drain_outbox=unused,
        discovery_lock=threading.Lock(),
        clock=lambda: 10,
    )
    monkeypatch.setattr(stages, "_require_eligible_entry", lambda _: None)
    if boundary == "audio_recovery":
        monkeypatch.setattr(stages, "_record_recovery", lambda _: fail())
    pipeline._extract = stages.extract
    assert pipeline.run_next()
    assert hit == [boundary]
    assert len(published) == 1
    assert pipeline.owned_claims == ()
    assert not pipeline.recovery_required
    assert not pipeline._claim_lock.locked()
    assert root._connection.execute(
        "SELECT stage,state,report_id FROM review_preparation_work"
    ).fetchone() == (
        "complete" if terminal else "proposal",
        "complete" if terminal else "failed",
        published[0],
    )
    assert inspectors[0].inspections == (0 if boundary == "audio_recovery" else 1)


@pytest.mark.parametrize("root", [3], indirect=True)
def test_publication_gate_and_existing_output_writer_cannot_invert_locks(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from mechanical_librarian.adapters.persistence.transactions import TransactionScope

    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path)
    first = root.claim_review_pipeline_action(4)
    second = root.claim_review_pipeline_action(4)
    assert first is not None and second is not None
    report = _report()
    root.publish(report, lambda: True)
    publisher_open = threading.Event()
    writer_ready = threading.Event()
    publication_waiting = threading.Event()
    failures: list[BaseException] = []
    begin = TransactionScope.begin

    def begin_scope(scope) -> None:
        if (
            threading.current_thread().name == "publication"
            and scope._savepoint.startswith("publish_")
        ):
            assert pipeline._claim_lock.locked()
            publication_waiting.set()
        begin(scope)

    monkeypatch.setattr(TransactionScope, "begin", begin_scope)

    def writer() -> None:
        repository = open_repository(path, authority=root.authority)
        repository.bind_review_pipeline_owner(
            pipeline._own, pipeline._publication_claim_guard
        )
        pipeline._own(first)
        try:
            assert publisher_open.wait(5)
            repository.begin_review_pipeline_output()
            writer_ready.set()
            assert publication_waiting.wait(5)
            assert (
                pipeline._advance(repository, first, report.report_id, claim_next=True)
                is None
            )
        except BaseException as error:
            failures.append(error)
        finally:
            repository.bind_review_pipeline_owner(None)
            repository.close()

    def publisher() -> None:
        repository = open_repository(path, authority=root.authority)
        repository.bind_review_pipeline_owner(
            pipeline._own, pipeline._publication_claim_guard
        )
        pipeline._own(second)
        publisher_open.set()
        try:
            assert writer_ready.wait(5)
            repository.publish(
                report,
                lambda: True,
                after_publication=lambda *_: (
                    repository.complete_review_extraction_publication(
                        second, report.report_id, 10, claim_next=True
                    )
                ),
            )
        except BaseException as error:
            failures.append(error)
        finally:
            repository.bind_review_pipeline_owner(None)
            repository.close()

    # Openers acquire the root's physical lock too. Start the publication
    # connection before the output writer owns that lock on Linux.
    workers = [
        threading.Thread(target=writer, name="writer"),
        threading.Thread(target=publisher, name="publication"),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert not any(worker.is_alive() for worker in workers)
    assert failures == []
    assert publication_waiting.is_set()
    assert not pipeline._claim_lock.locked()
    assert not pipeline.recovery_required


def test_publication_winning_gate_transfers_owner_before_stop_linearizes(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    pipeline = _pipeline(root, tmp_path / "application.sqlite3")
    stop_started = threading.Event()
    stopped = threading.Event()
    transferred = threading.Event()
    original_owner = pipeline._own
    observed: list[PipelineAction | None] = []
    failures: list[BaseException] = []

    def own(action) -> None:
        original_owner(action)
        if action is not None and action.stage == "proposal":
            assert not pipeline.recovery_required
            observed.append(action)
            transferred.set()

    monkeypatch.setattr(pipeline, "_own", own)

    def stop() -> None:
        stop_started.set()
        pipeline.stop_for_recovery("persistence")
        assert transferred.is_set()
        stopped.set()

    stopper = threading.Thread(target=stop)

    def extract(repository, action, claim_next):
        report = _report()

        def receipt(*_):
            assert pipeline._claim_lock.locked()
            stopper.start()
            assert stop_started.wait(5)
            assert not stopped.is_set()
            repository.complete_review_extraction_publication(
                action, report.report_id, 10, claim_next=claim_next
            )

        repository.publish(report, lambda: True, after_publication=receipt)
        assert stopped.wait(5)
        raise KeyboardInterrupt()

    pipeline._extract = extract
    try:
        pipeline.run_next()
    except BaseException as error:
        failures.append(error)
    finally:
        stopper.join(5)
    assert not stopper.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], KeyboardInterrupt)
    assert pipeline.owned_claims == tuple(observed)
    assert len(observed) == 1
    assert pipeline.recovery_required
    assert not pipeline._claim_lock.locked()
    assert root._connection.execute(
        "SELECT stage,state,attempt,report_id FROM review_preparation_work"
    ).fetchone() == ("proposal", "running", 1, _report().report_id)


@pytest.mark.parametrize("dormant", [False, True])
@pytest.mark.parametrize("boundary", ["observation", "recovery"])
def test_actual_supervisor_startup_failure_latches_closed_admission(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dormant: bool,
    boundary: str,
) -> None:
    from mechanical_librarian.application.review_pipeline import (
        ReviewPreparationSupervisor,
    )

    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path)
    pipeline._observation_repository_factory = lambda: open_repository(
        path, authority=root.authority, cross_thread=True
    )
    before = root._connection.execute(
        "SELECT stage,state,attempt FROM review_preparation_work"
    ).fetchall()

    def fail(*_args):
        raise RuntimeError("private startup failure detail")

    if boundary == "observation":
        pipeline._observation_repository_factory = fail
    else:
        monkeypatch.setattr(
            SQLiteMetadataReportRepository, "recover_review_pipeline", fail
        )
    supervisor = ReviewPreparationSupervisor(pipeline)
    try:
        if dormant and boundary == "recovery":
            supervisor.start_dormant()
            supervisor.admit()
            assert pipeline._recovery_stop.wait(5)
        else:
            with pytest.raises((ReviewPipelineError, RuntimeError)):
                (supervisor.start_dormant if dormant else supervisor.start)()
        assert supervisor.recovery_required
        supervisor.resume_admission()
        assert not pipeline.run_next()
        assert pipeline.owned_claims == ()
        assert (
            root._connection.execute(
                "SELECT stage,state,attempt FROM review_preparation_work"
            ).fetchall()
            == before
        )
        assert pipeline.recovery_incidents
        for incident in pipeline.recovery_incidents:
            assert incident.stage == "discovery"
            assert incident.code == "persistence"
            assert incident.scope == "runtime"
            assert "private" not in str(incident.to_data())
    finally:
        supervisor.stop()


def test_actual_supervisor_timeout_retains_exact_active_claim_before_close(
    root: SQLiteMetadataReportRepository, tmp_path: Path
) -> None:
    import threading
    import time

    from mechanical_librarian.application.review_pipeline import (
        ReviewPreparationSupervisor,
    )

    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path)
    pipeline._observation_repository_factory = lambda: open_repository(
        path, authority=root.authority, cross_thread=True
    )
    started = threading.Event()
    release = threading.Event()

    def blocked(*_args):
        started.set()
        assert release.wait(5)
        raise ReviewPipelineError("parser_refused")

    pipeline._extract = blocked
    supervisor = ReviewPreparationSupervisor(pipeline, graceful_shutdown_seconds=1)
    try:
        supervisor.start()
        assert started.wait(5)
        (owner,) = pipeline.owned_claims
        with pytest.raises(ReviewPipelineError, match="shutdown_timeout"):
            supervisor.stop(deadline=time.monotonic())
        assert pipeline.owned_claims == (owner,)
        assert owner.stage == "extraction"
        assert pipeline._observation_repository is not None
        assert root._connection.execute(
            "SELECT stage,state,attempt FROM review_preparation_work"
        ).fetchone() == (owner.stage, "running", owner.attempt)
    finally:
        release.set()
        supervisor.stop()
    assert not pipeline.owned_claims
    assert pipeline._observation_repository is None
    assert root._connection.execute(
        "SELECT stage,state FROM review_preparation_work"
    ).fetchone() == ("extraction", "failed")


@pytest.mark.parametrize("root", [3], indirect=True)
@pytest.mark.parametrize("other_failure", ["ordinary", "target"])
def test_other_owned_lane_settles_after_recovery_stop(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    other_failure: str,
) -> None:
    import threading
    from dataclasses import replace

    from mechanical_librarian.application.catalogue import TargetConnectionError
    from mechanical_librarian.domain.metadata_report import MetadataReport

    path = tmp_path / "application.sqlite3"
    pipeline = _pipeline(root, path)
    both_claimed = threading.Barrier(2)
    successor_owned = threading.Event()
    stopped = threading.Event()
    readers: dict[int, sqlite3.Connection] = {}
    extractions: list[str] = []
    failures: list[BaseException] = []
    settlements: list[str | None] = []
    original_fail = SQLiteMetadataReportRepository.fail_review_pipeline_action

    def fail(bound, action, *args):
        settlements.append(action.item_id)
        return original_fail(bound, action, *args)

    monkeypatch.setattr(
        SQLiteMetadataReportRepository, "fail_review_pipeline_action", fail
    )
    original = _report()
    report = MetadataReport.create(
        target=replace(original.target, item="item-2"),
        file=original.file,
        local_claims=original.local_claims,
        local_reasons=original.local_reasons,
        providers=(),
    )

    def extract(repository, action, claim_next):
        extractions.append(action.item_id)
        both_claimed.wait(timeout=5)
        if action.item_id == "item-1":
            assert successor_owned.wait(5)
            repository._connection.execute("PRAGMA busy_timeout=1")
            reader = sqlite3.connect(path)
            readers[threading.get_ident()] = reader
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM review_preparation_work").fetchall()
            raise ReviewPipelineError("parser_refused")
        assert action.item_id == "item-2"
        repository.publish(report, lambda: True)
        return report.report_id, False

    def dwell() -> None:
        owned = pipeline._owned()
        assert owned is not None and (owned.item_id, owned.stage) == (
            "item-2",
            "provider",
        )
        successor_owned.set()
        assert stopped.wait(5)
        assert pipeline.recovery_required
        if other_failure == "target":
            raise TargetConnectionError("unavailable")
        raise ReviewPipelineError("malformed_retained_data")

    pipeline._extract = extract
    pipeline._cooperative_wait = dwell

    def run() -> None:
        try:
            pipeline.run_next()
        except BaseException as error:
            failures.append(error)
        finally:
            reader = readers.pop(threading.get_ident(), None)
            if reader is not None:
                reader.rollback()
                reader.close()
                stopped.set()

    workers = [threading.Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert not any(worker.is_alive() for worker in workers)
    assert len(failures) == 1 and isinstance(failures[0], ReviewPipelineError)
    assert sorted(extractions) == ["item-1", "item-2"]
    assert settlements.count("item-1") == 1
    assert settlements.count("item-2") == (1 if other_failure == "ordinary" else 0)
    (owner,) = pipeline.owned_claims
    assert (owner.item_id, owner.stage, owner.attempt) == ("item-1", "extraction", 1)
    (incident,) = pipeline.recovery_incidents
    assert (incident.code, incident.stage, incident.settlement_code) == (
        "parser_refused",
        "extraction",
        "persistence",
    )
    rows = root._connection.execute(
        "SELECT item_id,stage,state,attempt FROM review_preparation_work ORDER BY item_id"
    ).fetchall()
    assert rows == [
        ("item-1", "extraction", "running", 1),
        (
            "item-2",
            "provider",
            "failed" if other_failure == "ordinary" else "queued",
            1 if other_failure == "ordinary" else 0,
        ),
        ("item-3", "extraction", "queued", 0),
    ]
    assert root.get(report.report_id) == report.to_json()
    pipeline.resume_claiming()
    assert not pipeline.run_next()
    restarted = _pipeline(root, path)
    restarted.recover()
    assert root.get(report.report_id) == report.to_json()
    if other_failure == "target":
        condition = root.begin_target_connection_check("primary", 11)
        assert condition is not None
        assert root.finish_target_connection_check("primary", condition.epoch, None, 12)
    providers: list[str] = []

    def refuse_uncommitted(_repository, action, _claim_next):
        assert action.item_id != "item-2", "committed extraction repeated"
        raise ReviewPipelineError("parser_refused")

    def provider(_repository, report_id):
        providers.append(report_id)
        return ()

    restarted._extract = refuse_uncommitted
    restarted._providers = provider
    while restarted.run_next():
        pass
    assert providers == ([report.report_id] if other_failure == "target" else [])
    assert not restarted.owned_claims and not restarted.recovery_required


@pytest.mark.parametrize("claim", ("ordinary", "exact", "successor", "blocked"))
def test_unknown_failure_correlates_owned_recovery_claim(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    claim: str,
) -> None:
    import json

    from mechanical_librarian.application.review_pipeline import (
        preparation_failure_reference,
    )

    first = root.claim_review_pipeline_action(4)
    assert first is not None and first.recovery_attempt == 0
    root.fail_review_pipeline_action(first, "transient", True, 5)
    pipeline = _pipeline(
        root, tmp_path / "application.sqlite3", blocked_settlement=claim == "blocked"
    )
    pipeline._clock = lambda: 20
    reader = sqlite3.connect(tmp_path / "application.sqlite3")

    def fail(*_: object):
        if claim == "blocked":
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM review_preparation_work").fetchall()
        raise ValueError("PRIVATE credential /media/file metadata")

    if claim == "successor":
        pipeline._cooperative_wait = fail
    else:
        pipeline._extract = fail
    try:
        if claim == "exact":
            action = root.claim_review_pipeline_work(first.action_id, 20)
            assert action is not None and action.recovery_attempt == 1
            pipeline._execute_claimed_action(root, action, claim_next=False)
        elif claim == "blocked":
            with pytest.raises(ReviewPipelineError, match="persistence"):
                pipeline.run_next()
        else:
            assert pipeline.run_next()
        row = root._connection.execute(
            "SELECT work_id,stage,attempt,recovery_attempt,failure_code,state FROM review_preparation_work"
        ).fetchone()
        records = [
            r
            for r in caplog.records
            if r.name == "mechanical_librarian.application.review_pipeline"
        ]
        assert len(records) == 1
        payload = json.loads(records[0].message)
        assert payload["observation"] == "before_fail"
        assert payload["execution_stage"] == "extraction"
        assert payload["owned_stage"] == (
            "provider" if claim == "successor" else "extraction"
        )
        assert payload["recovery_attempt"] == row[3] == 1
        assert payload["reference"] == preparation_failure_reference(
            row[0], row[1], row[2], row[3], "unknown"
        )
        assert payload["reference"] != preparation_failure_reference(
            row[0], row[1], row[2], 0, "unknown"
        )
        assert payload["exceptions"] == [{"module": "builtins", "type": "ValueError"}]
        assert any(
            frame["function"] == "_execute_claimed_action"
            for frame in payload["frames"]
        )
        assert "PRIVATE" not in records[0].message
        if claim == "blocked":
            (incident,) = pipeline.recovery_incidents
            assert incident.diagnostic_reference == payload["restart_reference"]
            assert row[-1] == "running"
            assert pipeline.owned_claims[0].recovery_attempt == 1
            assert not pipeline.run_next()
        else:
            assert row[-2:] == ("unknown", "failed")
            assert payload["reference"] in json.dumps(root.operational_snapshot())
            assert not pipeline.owned_claims and not pipeline.recovery_required
            assert not pipeline.run_next()
    finally:
        reader.rollback()
        reader.close()
    root.close()
    reopened = open_repository(tmp_path / "application.sqlite3", create=False)
    try:
        assert reopened._connection.execute("PRAGMA integrity_check").fetchone() == (
            "ok",
        )
        assert reopened._connection.execute(
            "SELECT state FROM review_preparation_work"
        ).fetchone() == (row[-1],)
    finally:
        reopened.close()


@pytest.mark.parametrize("boundary", ("projection", "sink"))
def test_diagnostic_failure_does_not_replace_unknown_settlement(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    import mechanical_librarian.application.review_pipeline as module

    pipeline = _pipeline(root, tmp_path / "application.sqlite3")

    def fail(*_: object, **__: object):
        raise RuntimeError("PRIVATE")

    pipeline._extract = fail
    if boundary == "projection":
        monkeypatch.setattr(module, "preparation_failure_reference", fail)
    else:
        monkeypatch.setattr(module._LOGGER, "warning", fail)
    assert pipeline.run_next()
    assert root._connection.execute(
        "SELECT stage,state,failure_code,failure_root FROM review_preparation_work"
    ).fetchone() == ("extraction", "failed", "unknown", None)
    assert not pipeline.owned_claims and not pipeline.recovery_required
    assert not pipeline.run_next()


@pytest.mark.parametrize("kind", ("media", "credential", "target"))
def test_declared_failures_do_not_emit_unknown_diagnostics(
    root: SQLiteMetadataReportRepository,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    from mechanical_librarian.application.catalogue import TargetConnectionError
    from mechanical_librarian.application.metadata_report import CredentialError

    failure = (
        ReviewPipelineError("parser_refused")
        if kind == "media"
        else CredentialError("PRIVATE", provider="hardcover-main")
        if kind == "credential"
        else TargetConnectionError("unavailable")
    )
    pipeline = _pipeline(root, tmp_path / "application.sqlite3")

    def fail(*_: object):
        raise failure

    pipeline._extract = fail
    assert pipeline.run_next()
    assert not caplog.records
    assert not pipeline.owned_claims and not pipeline.recovery_required
    state, code = root._connection.execute(
        "SELECT state,failure_code FROM review_preparation_work"
    ).fetchone()
    if kind == "target":
        assert state == "queued" and code is None
        assert pipeline.connection_conditions()[0].code == "unavailable"
    else:
        assert state == "failed"
        assert code == ("parser_refused" if kind == "media" else "authentication")
