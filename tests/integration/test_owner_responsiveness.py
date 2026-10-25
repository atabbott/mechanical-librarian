"""Real complete-cover construction, owned reads, drift and durable decisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    BrowseFetchingChanged,
    ReviewWebError,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.owner_responsiveness import (
    OwnerResponsivenessFixture,
    running_owner_fixture,
)


def test_complete_cover_browse_avoids_full_report_validation_in_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 10)
    try:
        assert fixture.browse is fixture.repository
        assert len(fixture.cases) == 10
        facts = fixture.repository._connection.execute(
            "SELECT currentness_document FROM cover_considerations"
        ).fetchall()
        assert len(facts) == 10 and all(
            json.loads(row[0])["provider_fetching"] is False for row in facts
        )

        def forbidden(_document: str) -> MetadataReport:
            raise AssertionError("count parsed a complete report")

        with monkeypatch.context() as scoped:
            scoped.setattr(MetadataReport, "from_json", forbidden)
            total, _ = fixture.browse.count_current_review_cases(None)
            assert total == 10
        page = fixture.application.library(
            lens="all", search=None, limit=5, cursor=None
        )
        assert len(page.items) == 5
    finally:
        fixture.close()


def test_direct_external_drift_refuses_complete_browse_response(tmp_path: Path) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        capture = fixture.browse._capture_cover_read
        assert capture is not None
        original = capture()
        calls = 0

        def changing():
            nonlocal calls
            calls += 1
            return (
                original
                if calls == 1
                else (
                    original[0],
                    frozenset(
                        {
                            (
                                "primary",
                                "books-0",
                                "work-0000",
                                fixture.cases[0].report_id,
                            )
                        }
                    ),
                )
            )

        fixture.browse.bind_cover_read_observation(changing)
        with pytest.raises(
            BrowseFetchingChanged, match="review snapshot is unavailable"
        ):
            with fixture.browse.browse_observation():
                fixture.browse.read_review_library(None, 5, 0, None)
        assert calls == 2
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


def test_post_snapshot_browse_retains_admission_then_foreground_precedes_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    admission = fixture.repository.operation_admission
    assert admission is not None
    original = fixture.browse.read_review_library

    def held(*args):
        result = original(*args)
        assert not fixture.browse._connection.in_transaction
        assert admission.held
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(fixture.browse, "read_review_library", held)

    def read():
        try:
            with admission.classify("background"):
                fixture.application.library(
                    lens="all", search=None, limit=5, cursor=None
                )
        except BaseException as error:
            failures.append(error)

    order: list[str] = []

    def queued(role):
        try:
            with admission.classify(role):
                detail = fixture.application.case_detail(
                    fixture.cases[0].review_case_id
                )
                assert detail is not None
                order.append(role)
        except BaseException as error:
            failures.append(error)

    background = threading.Thread(target=queued, args=("background",))
    foreground = threading.Thread(target=queued, args=("foreground",))
    thread = threading.Thread(target=read)
    thread.start()
    try:
        assert entered.wait(5)
        background.start()
        with admission._condition:
            assert admission._condition.wait_for(
                lambda: len(admission._waiting) == 1, 5
            )
        foreground.start()
        with admission._condition:
            assert admission._condition.wait_for(
                lambda: len(admission._waiting) == 2, 5
            )
        assert order == []
        release.set()
        foreground.join(5)
        background.join(5)
        assert not foreground.is_alive() and not background.is_alive()
        assert order == ["foreground", "background"]
        case = fixture.cases[0]
        proposal = next(
            value for value in case.proposals if value["destination"] == "title"
        )
        proposal_id = proposal["proposal_id"]
        assert type(proposal_id) is str
        first, repeated = [
            fixture.application.record_decision(
                case.review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination="title",
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="held-browse-owner-choice",
            )
            for _ in range(2)
        ]
        assert first["decision_id"] == repeated["decision_id"]
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (1,)
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM human_decision_events WHERE decision_id=?",
            (first["decision_id"],),
        ).fetchone() == (1,)
        readback = fixture.application.case_detail(case.review_case_id)
        title = next(
            field for field in readback.fields if field["destination"] == "title"
        )
        assert title["human_decision"]["decision_id"] == first["decision_id"]
        assert title["needs_attention"] is False
        assert release.is_set()
    finally:
        release.set()
        thread.join(5)
        fixture.close()
    assert not thread.is_alive()
    assert failures == []


def test_forged_currentness_facts_refuse_reopen_without_repair(tmp_path: Path) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    database = fixture.database
    fixture.close()
    import sqlite3

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.adapters.persistence.schema import (
        _COVER_CONSIDERATION_NO_UPDATE_SQL,
    )

    connection = sqlite3.connect(database)
    connection.execute("DROP TRIGGER cover_considerations_no_update")
    connection.execute("UPDATE cover_considerations SET currentness_document='null'")
    connection.execute(_COVER_CONSIDERATION_NO_UPDATE_SQL)
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        open_repository(database, create=False)
    assert database.read_bytes() == before


def test_shutdown_drains_an_owned_browse_before_closing_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    entered, release, stopping, stopped = (threading.Event() for _ in range(4))
    failures: list[BaseException] = []
    closed: list[str] = []
    original_read = fixture.browse.read_review_library
    original_updates_close = fixture.application._updates.close
    original_root_close = fixture.repository.close

    def read(*args):
        result = original_read(*args)
        assert not fixture.browse._connection.in_transaction
        entered.set()
        assert release.wait(5)
        return result

    def updates_close():
        original_updates_close()
        stopping.set()

    def root_close():
        assert release.is_set()
        assert fixture.application._browse_closed
        original_root_close()
        closed.append("root")

    def browse():
        try:
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        except BaseException as error:
            failures.append(error)

    def stop():
        try:
            fixture.close()
        except BaseException as error:
            failures.append(error)
        finally:
            stopped.set()

    monkeypatch.setattr(fixture.browse, "read_review_library", read)
    monkeypatch.setattr(fixture.application._updates, "close", updates_close)
    monkeypatch.setattr(fixture.repository, "close", root_close)
    reader = threading.Thread(target=browse)
    closer = threading.Thread(target=stop)
    reader.start()
    try:
        assert entered.wait(5)
        closer.start()
        assert stopping.wait(5)
        assert not stopped.wait(0.05)
        assert closed == []
    finally:
        release.set()
        reader.join(5)
        if closer.ident is not None:
            closer.join(5)
        else:
            fixture.close()
    assert not reader.is_alive() and not closer.is_alive()
    assert failures == []
    assert closed == ["root"]


def test_complete_currentness_facts_survive_root_reopen(tmp_path: Path) -> None:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    expected = fixture.repository._connection.execute(
        "SELECT document,currentness_document FROM cover_considerations ORDER BY consideration_id"
    ).fetchall()
    fixture.close()
    reopened = open_repository(fixture.database, create=False)
    try:
        assert (
            reopened._connection.execute(
                "SELECT document,currentness_document FROM cover_considerations ORDER BY consideration_id"
            ).fetchall()
            == expected
        )
    finally:
        reopened.close()


def test_exact_pre_currentness_schema_refuses_startup_without_mutation(
    tmp_path: Path,
) -> None:
    import sqlite3

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support.metadata_report_schemas import create_pre_currentness_schema

    database = tmp_path / "predecessor.sqlite"
    connection = sqlite3.connect(database)
    create_pre_currentness_schema(connection)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(cover_considerations)")
    }
    assert "document" in columns and "currentness_document" not in columns
    connection.close()
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        open_repository(database, create=False)
    assert database.read_bytes() == before


@pytest.mark.parametrize("failed_capture", (1, 2, 3, 4))
def test_external_authority_failure_is_truthfully_unavailable(
    tmp_path: Path, failed_capture: int
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    try:
        capture = fixture.browse._capture_cover_read
        assert capture is not None
        observation = capture()
        calls = 0

        def unavailable():
            nonlocal calls
            calls += 1
            if calls == failed_capture:
                raise RuntimeError("private recovery storage detail")
            if failed_capture > 2 and calls >= 2:
                return (
                    observation[0],
                    frozenset(
                        {
                            (
                                "primary",
                                "books-0",
                                "work-0000",
                                fixture.cases[0].report_id,
                            )
                        }
                    ),
                )
            return observation

        fixture.browse.bind_cover_read_observation(unavailable)
        with pytest.raises(ReviewWebError, match="^unavailable$"):
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        assert calls == failed_capture
        assert fixture.application._active_browses == 0
        assert fixture.browse._cover_read_observation is None
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


def test_browse_generation_closes_with_owner_and_refuses_later_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
    )

    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    closed: list[str] = []
    close_root = fixture.repository.close

    def root_close():
        assert fixture.application._browse_closed
        close_root()
        closed.append("root")

    monkeypatch.setattr(fixture.repository, "close", root_close)
    fixture.close()
    assert closed == ["root"]
    with pytest.raises(ReviewApplicationError, match="unavailable"):
        fixture.application.library(lens="all", search=None, limit=5, cursor=None)


def test_mixed_complete_fixture_runs_real_preparation_and_preserves_owner_final(
    tmp_path: Path,
) -> None:
    import time

    fixture = OwnerResponsivenessFixture(tmp_path, 10, mixed=True, prepare=True)
    try:
        assert len(fixture.cases) == 9
        assert (
            fixture.application.library(
                lens="all", search=None, limit=25, cursor=None
            ).counts["all"]
            == 10
        )
        fixture.application.start_dormant()
        fixture.application.admit()
        assert fixture.preparation is not None
        for index in range(5):
            fixture.preparation.request("primary", f"books-{index}")
        deadline = time.monotonic() + 10
        while True:
            states = [
                fixture.preparation.progress("primary", f"books-{index}")
                for index in range(5)
            ]
            if all(
                state is not None
                and state.state in {"completed", "completed_with_failures"}
                for state in states
            ):
                break
            assert time.monotonic() < deadline, states
            time.sleep(0.01)
        assert sum(state.checked for state in states if state is not None) == 10
        assert sum(state.blocked for state in states if state is not None) == 1
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (1,)
        detail = fixture.application.case_detail(fixture.cases[1].review_case_id)
        title = next(
            field for field in detail.fields if field["destination"] == "title"
        )
        assert title["human_decision"] is not None
        assert title["needs_attention"] is False
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM cover_considerations WHERE currentness_document != 'null'"
        ).fetchone() == (9,)
    finally:
        fixture.close()


def test_populated_owner_http_runtime_reopens_and_serves_independent_routes(
    tmp_path: Path,
) -> None:
    import httpx

    from tests.support.owner_responsiveness import running_owner_fixture

    for works in (10, 0):
        with running_owner_fixture(tmp_path, works) as (fixture, origin):
            assert len(fixture.cases) == 9
            with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
                assert (
                    client.post(
                        "/api/v1/session/login", json={"password": "synthetic-owner"}
                    ).status_code
                    == 200
                )
                library = client.get("/api/v1/library?lens=all")
                assert library.status_code == 200
                assert library.json()["counts"]["all"] == 10
                for route in (
                    "/api/v1/attention",
                    "/api/v1/review-preparation",
                    "/api/v1/settings",
                ):
                    assert client.get(route).status_code == 200


def test_failed_fixture_activation_releases_root_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support import owner_responsiveness

    def fail(*args, **kwargs):
        raise RuntimeError("fixture activation failed")

    with monkeypatch.context() as failure:
        failure.setattr(owner_responsiveness, "compose_review_application", fail)
        with pytest.raises(RuntimeError, match="fixture activation failed"):
            OwnerResponsivenessFixture(tmp_path, 1)
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    reopened.close()


def test_persistent_browse_observes_live_cover_policy_rebinding(tmp_path: Path) -> None:
    from mechanical_librarian.application.cover_policy import CoverPolicy

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        capture = fixture.browse._capture_cover_read
        assert capture is not None
        original = capture()
        connection = fixture.browse._connection
        initial = fixture.application.attention(search=None, limit=25, cursor=None)
        fixture.covers.update_policy(CoverPolicy(prefer_minimal_composition=True))
        changed = capture()
        assert changed[0] != original[0]
        assert changed[0] == fixture.covers.current_authority()
        current = fixture.application.attention(search=None, limit=25, cursor=None)
        assert fixture.browse._connection is connection
        assert current.total < initial.total
    finally:
        fixture.close()


@pytest.mark.parametrize("route", ["library?lens=all", "attention"])
def test_disconnected_queued_browse_requests_do_not_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    import socket
    from urllib.parse import urlsplit

    import httpx
    from starlette.requests import Request

    from tests.support.owner_responsiveness import running_owner_fixture

    with running_owner_fixture(tmp_path, 3) as (fixture, origin):
        path = f"/api/v1/{route}"
        disconnected = threading.Condition()
        disconnects = 0
        receive_property = Request.receive

        def receive_for(request: Request):
            receive = receive_property.__get__(request, Request)

            async def receive_message():
                nonlocal disconnects
                message = await receive()
                if message["type"] == "http.disconnect":
                    with disconnected:
                        disconnects += 1
                        disconnected.notify_all()
                return message

            return receive_message

        monkeypatch.setattr(Request, "receive", property(receive_for))
        queued = threading.Condition()
        entrants = 0
        projections = 0
        original = fixture.application._browse_serialized

        def observe(call, check_active=None):
            nonlocal entrants, projections
            with queued:
                entrants += 1
                queued.notify_all()

            def project():
                nonlocal projections
                projections += 1
                return call()

            return original(project, check_active)

        monkeypatch.setattr(fixture.application, "_browse_serialized", observe)
        with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
            assert client.get(path).status_code == 401
            assert (
                client.post(
                    "/api/v1/session/login", json={"password": "synthetic-owner"}
                ).status_code
                == 200
            )
            assert client.get(path, params={"limit": 0}).status_code == 422
            assert entrants == 0
            cookie = client.build_request("GET", path).headers["cookie"]
            address = urlsplit(origin)
            assert address.hostname is not None and address.port is not None
            connections: list[socket.socket] = []
            responses: list[httpx.Response] = []
            failures: list[BaseException] = []

            def live_read():
                try:
                    responses.append(client.get(path))
                except BaseException as error:
                    failures.append(error)

            live = threading.Thread(target=live_read)
            fixture.application._gate.acquire()
            try:
                for _ in range(2):
                    connection = socket.create_connection(
                        (address.hostname, address.port), timeout=5
                    )
                    connections.append(connection)
                    connection.sendall(
                        f"GET {path} HTTP/1.1\r\nHost: {address.netloc}\r\nCookie: {cookie}\r\n\r\n".encode()
                    )
                with queued:
                    assert queued.wait_for(lambda: entrants == 2, timeout=5)
                for connection in connections:
                    connection.shutdown(socket.SHUT_RDWR)
                    connection.close()
                with disconnected:
                    assert disconnected.wait_for(lambda: disconnects >= 2, timeout=5)
                live.start()
                with queued:
                    assert queued.wait_for(lambda: entrants == 3, timeout=5)
                assert projections == 0
            finally:
                fixture.application._gate.release()
                for connection in connections:
                    connection.close()
                if live.ident is not None:
                    live.join(5)
            assert not live.is_alive() and not failures
            assert len(responses) == 1 and responses[0].status_code == 200
            assert projections == 1
            assert not fixture.browse._connection.in_transaction


@pytest.mark.parametrize("route", ["library?lens=all", "attention"])
def test_disconnected_active_browse_finishes_before_next_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    import socket
    from urllib.parse import urlsplit

    import httpx

    from tests.support.owner_responsiveness import running_owner_fixture

    with running_owner_fixture(tmp_path, 3) as (fixture, origin):
        entered, release, finished = (threading.Event() for _ in range(3))
        original = fixture.application._browse_serialized
        projections = 0

        def observe(call, check_active=None):
            def project():
                nonlocal projections
                projections += 1
                if projections == 1:
                    entered.set()
                    assert release.wait(5)
                result = call()
                finished.set()
                return result

            return original(project, check_active)

        monkeypatch.setattr(fixture.application, "_browse_serialized", observe)
        with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
            assert (
                client.post(
                    "/api/v1/session/login", json={"password": "synthetic-owner"}
                ).status_code
                == 200
            )
            path = f"/api/v1/{route}"
            cookie = client.build_request("GET", path).headers["cookie"]
            address = urlsplit(origin)
            assert address.hostname is not None and address.port is not None
            connection = socket.create_connection(
                (address.hostname, address.port), timeout=5
            )
            try:
                connection.sendall(
                    f"GET {path} HTTP/1.1\r\nHost: {address.netloc}\r\nCookie: {cookie}\r\n\r\n".encode()
                )
                assert entered.wait(5)
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
                assert not fixture.application._gate.acquire(blocking=False)
                assert not finished.is_set()
            finally:
                release.set()
                connection.close()
            assert client.get(path).status_code == 200
            assert finished.is_set() and projections == 2
            assert not fixture.browse._connection.in_transaction
            assert client.get("/api/v1/library?lens=invalid").status_code == 400
            assert client.get(path).status_code == 200


def test_post_open_report_tamper_refuses_where_rendered_and_at_reopen(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )

    # #1091 validated every candidate document on every read. #1199 reads
    # candidates from persisted browse facts, so an edit that bypasses
    # metadata_reports_no_update after open is refused where the row renders
    # and at the next open, and deliberately not by a read that never renders it.
    fixture = OwnerResponsivenessFixture(tmp_path, 30)
    try:
        connection = fixture.repository._connection
        report_id, document = connection.execute(
            "SELECT report_id,document FROM current_metadata_reports current "
            "JOIN metadata_reports USING(report_id) WHERE current.item_id='work-0029'"
        ).fetchone()
        payload = json.loads(document)
        payload["target"]["fields"][0]["value"] = 42
        malformed = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        connection.execute("DROP TRIGGER metadata_reports_no_update")
        connection.execute(
            "UPDATE metadata_reports SET document=?,document_digest=? WHERE report_id=?",
            (malformed, hashlib.sha256(malformed.encode()).hexdigest(), report_id),
        )
        connection.commit()
        browse = fixture.browse
        assert len(browse.list_current_attention_documents(None, 25, 0)) == 25
        assert browse.read_review_library(None, 25, 0, None).total == 30
        with pytest.raises(
            MetadataReportError, match="^review attention is unavailable$"
        ):
            browse.list_current_attention_documents("work-0029", 25, 0)
        with pytest.raises(
            MetadataReportError, match="^review library is unavailable$"
        ):
            browse.read_review_library(None, 25, 25, None)
        fixture.repository.suspend_field_effect_capability(
            scope="connection",
            connection="primary",
            destination="title",
            reason="connection_unavailable",
            changed_at=100,
        )
        assert browse.list_current_attention_documents("work-0029", 25, 0) == ()
    finally:
        fixture.close()
    with pytest.raises(
        MetadataReportError, match="^database is unavailable or incompatible$"
    ):
        open_repository(fixture.database, create=False)


def test_external_browse_capture_runs_outside_admission_and_reader_owner(
    tmp_path: Path,
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    admission = fixture.repository.operation_admission
    assert admission is not None
    capture = fixture.browse._capture_cover_read
    assert capture is not None
    captures = 0

    def observed():
        nonlocal captures
        assert not admission.held
        assert not fixture.browse._connection.in_transaction
        available = threading.Event()

        def acquire():
            with fixture.application._gate:
                available.set()

        observer = threading.Thread(target=acquire)
        observer.start()
        observer.join(2)
        assert not observer.is_alive() and available.is_set()
        captures += 1
        return capture()

    fixture.browse.bind_cover_read_observation(observed)
    try:
        fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        fixture.application.attention(search=None, limit=5, cursor=None)
        assert captures == 4
        assert fixture.browse._cover_read_observation is None
    finally:
        fixture.close()


@pytest.mark.parametrize("route", ("library?lens=all", "attention"))
def test_authenticated_browse_hint_classifies_only_the_actual_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    import httpx

    from tests.support.owner_responsiveness import running_owner_fixture

    with running_owner_fixture(tmp_path, 2) as (fixture, origin):
        admission = fixture.repository.operation_admission
        assert admission is not None
        method = (
            "read_review_library"
            if route.startswith("library")
            else "read_review_attention"
        )
        original = getattr(fixture.browse, method)
        observed = []

        def read(*args, **kwargs):
            assert admission.held and admission._active is not None
            observed.append(admission._active.work_class)
            return original(*args, **kwargs)

        monkeypatch.setattr(fixture.browse, method, read)
        with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
            path = "/api/v1/" + route
            assert (
                client.get(path, headers={"X-ML-Work-Class": "background"}).status_code
                == 401
            )
            assert observed == []
            assert (
                client.post(
                    "/api/v1/session/login", json={"password": "synthetic-owner"}
                ).status_code
                == 200
            )
            for headers in (
                {},
                {"X-ML-Work-Class": "background"},
                {"X-ML-Work-Class": "unknown"},
            ):
                assert client.get(path, headers=headers).status_code == 200
            assert observed == ["foreground", "background", "foreground"]


@pytest.mark.parametrize("destination", ["title", "cover"])
def test_durable_acknowledgement_confirms_in_the_same_foreground_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        admission = fixture.repository.operation_admission
        assert admission is not None
        case = fixture.cases[0]
        proposal = next(
            value for value in case.proposals if value["destination"] == destination
        )
        proposal_id = proposal["proposal_id"]
        assert type(proposal_id) is str
        consideration_id = None
        if destination == "cover":
            encoded = fixture.repository.current_cover_consideration_document(
                case.review_case_id
            )
            assert encoded is not None
            consideration_id = json.loads(encoded)["consideration_id"]
        original = fixture.application.case_detail
        observations = []

        def unavailable(review_case_id):
            assert admission.held
            assert admission._active is not None
            assert admission._active.work_class == "foreground"
            assert not fixture.repository._connection.in_transaction
            assert fixture.repository._connection.execute(
                "SELECT count(*) FROM human_decisions"
            ).fetchone() == (1,)
            observations.append("committed")
            raise RuntimeError("synthetic read failure after durable commit")

        monkeypatch.setattr(fixture.application, "case_detail", unavailable)

        def submit():
            return fixture.application.record_decision(
                case.review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination=destination,
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="confirmed-durable-owner-choice",
                consideration_id=consideration_id,
                cover_confirmation={
                    "expected_selection_id": None,
                    "confirm_unverified_match": False,
                }
                if destination == "cover"
                else None,
            )

        first = submit()
        assert first["confirmation"] == {"status": "unavailable"}
        assert observations == ["committed"]
        assert not admission.held

        def confirmed(review_case_id):
            assert admission.held and not fixture.repository._connection.in_transaction
            observations.append("readback")
            return original(review_case_id)

        monkeypatch.setattr(fixture.application, "case_detail", confirmed)
        repeated = submit()
        assert repeated["decision_id"] == first["decision_id"]
        confirmation = repeated["confirmation"]
        assert type(confirmation) is dict and confirmation["status"] == "confirmed"
        detail = confirmation["case"]
        assert type(detail) is dict
        assert detail["review_case_id"] == case.review_case_id
        title = next(
            field for field in detail["fields"] if field["destination"] == destination
        )
        assert title["human_decision"]["decision_id"] == first["decision_id"]
        assert not title["needs_attention"]
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (1,)
        assert observations == ["committed", "readback"]
        assert not admission.held
    finally:
        fixture.close()


def test_overlapping_generations_share_root_admission_and_failed_activation_keeps_it_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.bootstrap import composition
    from mechanical_librarian.infrastructure.settings_authority import ReviewBootstrap

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    admission = fixture.repository.operation_admission
    assert admission is not None
    predecessor = open_repository(
        fixture.database,
        authority=fixture.repository.authority,
        cross_thread=True,
        admission=admission,
    )
    successor = open_repository(
        fixture.database,
        authority=fixture.repository.authority,
        cross_thread=True,
        admission=admission,
    )
    order: list[str] = []
    failures: list[BaseException] = []
    threads: list[threading.Thread] = []

    def read(repository, name: str, background: bool) -> None:
        try:
            with (
                admission.classify("background" if background else "foreground"),
                admission.enter(),
            ):
                repository.get_review_case(fixture.cases[0].review_case_id)
                order.append(name)
        except BaseException as error:
            failures.append(error)

    try:
        assert (
            predecessor.operation_admission
            is successor.operation_admission
            is admission
        )
        with admission.classify("background"), admission.enter():
            for repository, name, background in (
                (predecessor, "predecessor-background", True),
                (successor, "successor-foreground", False),
            ):
                thread = threading.Thread(
                    target=read, args=(repository, name, background)
                )
                threads.append(thread)
                thread.start()
                with admission._condition:
                    assert admission._condition.wait_for(
                        lambda: len(admission._waiting) == len(threads), timeout=5
                    )
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        assert failures == []
        assert order == ["successor-foreground", "predecessor-background"]
        predecessor.close()
        assert successor.get_review_case(fixture.cases[0].review_case_id)

        def fail_sessions(_path):
            raise RuntimeError("synthetic successor session activation failure")

        monkeypatch.setattr(composition, "_open_review_session_store", fail_sessions)
        with pytest.raises(
            RuntimeError, match="synthetic successor session activation failure"
        ):
            composition.compose_store_runtime_generation(
                ReviewBootstrap(
                    schema_version="review-bootstrap-v2",
                    canonical_origin="https://review.invalid",
                    tls_certificate_file="/synthetic/certificate",
                    tls_private_key_file="/synthetic/key",
                ),
                fixture.database,
                tmp_path / "catalogue.sqlite",
                tmp_path / "artifacts",
                repository_authority=fixture.repository.authority,
                operation_admission=admission,
            )
        assert successor.get_review_case(fixture.cases[0].review_case_id)
        assert fixture.repository.get_review_case(fixture.cases[0].review_case_id)
        assert not admission.held
    finally:
        for thread in threads:
            thread.join(5)
        predecessor.close()
        successor.close()
        fixture.close()


@pytest.mark.parametrize("fail_write", [False, True])
def test_settings_publication_and_rollback_preserve_browse_final_recheck(
    tmp_path: Path, fail_write: bool
) -> None:
    from mechanical_librarian.adapters.persistence.documents import _canonical_json
    from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
    )
    from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        document = OwnerSettingsDocument.model_validate(
            {
                "schema_version": "owner-settings-v4",
                "revision": 1,
                "connections": [
                    {"name": "primary", "endpoint": "https://library.example.invalid"}
                ],
                "libraries": [
                    {
                        "name": "books",
                        "connection": "primary",
                        "library_id": "library",
                        "media_kind": "ebook",
                    }
                ],
            }
        )
        connection = fixture.repository._connection
        connection.execute(
            "INSERT INTO owner_settings(singleton,revision,document) VALUES(1,1,?)",
            (_canonical_json(document.model_dump()),),
        )
        connection.execute(
            "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
            ("connection.primary.key", "synthetic-target-key"),
        )
        connection.commit()
        store = fixture.repository.owner_settings_store()
        fixture.application._settings = OwnerSettingsOperations(store)
        before = fixture.covers.current_authority()
        next_value = document.model_dump()
        next_value["revision"] = 2
        next_value["cover_preferences"] = {
            "avoid_promotional_text": False,
            "avoid_tie_in_branding": False,
            "prefer_minimal_composition": True,
        }
        changed = OwnerSettingsDocument.model_validate(next_value)
        if fail_write:
            connection.execute(
                "CREATE TEMP TRIGGER refuse_settings BEFORE UPDATE ON owner_settings "
                "BEGIN SELECT RAISE(ABORT,'synthetic settings failure'); END"
            )
        capture = fixture.browse._capture_cover_read
        assert capture is not None
        captures = 0
        admission = fixture.repository.operation_admission
        assert admission is not None

        def observe():
            nonlocal captures
            captures += 1
            assert not admission.held
            if captures == 2:
                if fail_write:
                    with pytest.raises(
                        ReviewApplicationError, match="settings_unavailable"
                    ):
                        fixture.application.write_owner_settings(changed, 1, ())
                else:
                    fixture.application.write_owner_settings(changed, 1, ())
            return capture()

        fixture.browse.bind_cover_read_observation(observe)
        if fail_write:
            view = fixture.application.library(
                lens="all", search=None, limit=5, cursor=None
            )
            assert len(view.items) == 2
            assert store.read().document.revision == 1
            assert fixture.covers.current_authority() == before
        else:
            with pytest.raises(ReviewWebError, match="^unavailable$"):
                fixture.application.library(
                    lens="all", search=None, limit=5, cursor=None
                )
            assert store.read().document.revision == 2
            assert fixture.covers.current_authority() != before
        assert captures == 2
        assert fixture.browse._cover_read_observation is None
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


def test_pipeline_claim_wait_and_discovery_leave_foreground_admission_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    supervisor = fixture._preparation()
    pipeline = supervisor._pipeline
    admission = fixture.repository.operation_admission
    assert admission is not None
    opened = threading.Event()
    original_factory = pipeline._repository_factory
    original_discover = pipeline._discover
    observations: list[str] = []

    def repository():
        result = original_factory()
        opened.set()
        return result

    def discover(*args):
        assert not admission.held
        observations.append("discovery outside admission")
        return original_discover(*args)

    monkeypatch.setattr(pipeline, "_repository_factory", repository)
    monkeypatch.setattr(pipeline, "_discover", discover)
    try:
        pipeline.request("primary", "books-0")
        opened.clear()
        pipeline._claim_lock.acquire()
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(pipeline.run_next, 1)
            try:
                assert opened.wait(5)
                fixture.repository.set_review_pipeline_paused(True, 100)
                assert fixture.repository.review_pipeline_paused()
                assert not pending.done()
                fixture.repository.set_review_pipeline_paused(False, 101)
            finally:
                pipeline._claim_lock.release()
            assert pending.result(5)
        assert observations == ["discovery outside admission"]
        assert not admission.held
    finally:
        supervisor.stop()
        fixture.close()


def test_report_external_currentness_precedes_admission_and_sql_begin(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    admission = fixture.repository.operation_admission
    assert admission is not None
    entered, release = threading.Event(), threading.Event()
    report = next(iter(fixture.reports.values()))
    statements: list[tuple[int, str]] = []
    connection = fixture.repository._connection
    connection.set_trace_callback(
        lambda sql: statements.append((threading.get_ident(), sql))
    )
    publishing_thread: list[int] = []

    def currentness():
        publishing_thread.append(threading.get_ident())
        assert not admission.held
        assert not fixture.repository.operation_lock._is_owned()
        entered.set()
        assert release.wait(5)
        return True

    def publish():
        with admission.classify("background"):
            return fixture.repository.publish(report, currentness)

    try:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(publish)
            try:
                assert entered.wait(5)
                fixture.repository.set_review_pipeline_paused(True, 100)
                assert fixture.repository.review_pipeline_paused()
                assert not any(
                    thread == publishing_thread[0] and sql.startswith("BEGIN")
                    for thread, sql in statements
                )
            finally:
                release.set()
            assert pending.result(5) == report.to_json()
        assert any(
            thread == publishing_thread[0] and sql.startswith("BEGIN")
            for thread, sql in statements
        )
        statements.clear()
        with pytest.raises(MetadataReportError, match="target or media changed"):
            fixture.repository.publish(report, lambda: False)
        assert not any(sql.startswith("BEGIN") for _, sql in statements)
        assert not admission.held and not connection.in_transaction
    finally:
        release.set()
        connection.set_trace_callback(None)
        fixture.close()


def test_authored_description_acknowledgement_retries_only_postcommit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.adapters.persistence.review_sessions import (
        SQLiteReviewSessionRepository,
    )
    from mechanical_librarian.application.operation_admission import OperationAdmission
    from mechanical_librarian.application.review_application import (
        compose_review_application,
    )
    from tests.support.description_advice import _prepared

    repository, _store, binding, candidate, _ = _prepared(tmp_path, automatic=False)
    assert candidate is not None
    admission = OperationAdmission()
    repository.bind_operation_admission(admission)
    sessions = SQLiteReviewSessionRepository.open(tmp_path / "sessions.sqlite")
    application = compose_review_application(
        state_owner=repository,
        session_store=sessions,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: True,
        clock=lambda: 11,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        session_digest=lambda token: hashlib.sha256(token.encode()).hexdigest(),
        admission=admission,
        operation_lock=repository.operation_lock,
    )
    original = application.case_detail
    fail_readback = True
    reads = 0

    def readback(review_case_id):
        nonlocal reads
        reads += 1
        assert admission.held
        assert (
            admission._active is not None
            and admission._active.work_class == "foreground"
        )
        assert not repository._connection.in_transaction
        assert repository._connection.execute(
            "SELECT count(*) FROM description_decisions"
        ).fetchone() == (1,)
        if fail_readback:
            raise RuntimeError("synthetic postcommit readback failure")
        return original(review_case_id)

    monkeypatch.setattr(application, "case_detail", readback)
    try:
        first = application.select_description_advice(
            binding.review_case_id,
            candidate.candidate_id,
            binding,
            "authored-confirmation-replay",
        )
        assert first["confirmation"] == {"status": "unavailable"}
        fail_readback = False
        repeated = application.select_description_advice(
            binding.review_case_id,
            candidate.candidate_id,
            binding,
            "authored-confirmation-replay",
        )
        assert repeated["decision_id"] == first["decision_id"]
        confirmation = repeated["confirmation"]
        assert type(confirmation) is dict and confirmation["status"] == "confirmed"
        detail = confirmation["case"]
        assert (
            type(detail) is dict and detail["review_case_id"] == binding.review_case_id
        )
        assert reads == 2 and not admission.held
        assert repository._connection.execute(
            "SELECT count(*) FROM description_decisions"
        ).fetchone() == (1,)
    finally:
        application.stop()
        sessions.close()
        repository.close()


@pytest.mark.parametrize(
    "coordinated", [True, False], ids=["http", "direct-unscheduled"]
)
def test_direct_browse_waits_for_owned_observation_and_installs_its_own_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coordinated: bool
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    repository = fixture.repository
    if not coordinated:
        repository.bind_operation_admission(None)
        assert repository.operation_admission is None
    entered, captured, release = (threading.Event() for _ in range(3))
    capture = repository._capture_cover_read
    assert capture is not None
    baseline = capture()
    direct_identity: list[int] = []
    observed: list[object] = []
    direct_facts = (
        baseline[0],
        frozenset({("primary", "books-0", "work-0000", fixture.cases[0].report_id)}),
    )
    original_read = repository.read_review_library

    def capture_for_thread():
        assert not fixture.application._admission.held
        assert repository._serialization_lock is not None
        assert repository._serialization_lock.depth() == 0
        if direct_identity == [threading.get_ident()]:
            captured.set()
            return direct_facts
        return baseline

    def held_read(*args):
        result = original_read(*args)
        assert repository._cover_read_observation == baseline
        assert not repository._connection.in_transaction
        entered.set()
        assert release.wait(5)
        return result

    def direct_read():
        direct_identity.append(threading.get_ident())
        with repository.browse_observation():
            observed.append(repository._cover_read_observation)
            assert repository._cover_read_observation == direct_facts
            return repository.count_current_review_cases(None)

    def first_read():
        if coordinated:
            return len(
                fixture.application.library(
                    lens="all", search=None, limit=5, cursor=None
                ).items
            )
        with repository.browse_observation():
            return len(repository.read_review_library(None, 5, 0, None).rows)

    monkeypatch.setattr(repository, "read_review_library", held_read)
    repository.bind_cover_read_observation(capture_for_thread)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_read)
            try:
                assert entered.wait(5)
                direct = pool.submit(direct_read)
                assert captured.wait(5)
                assert not direct.done()
                assert observed == []
            finally:
                release.set()
            assert first.result(5) == 2
            direct.result(5)
        assert observed == [direct_facts]
        assert repository._cover_read_observation is None
        assert not repository._connection.in_transaction
        fixture.application.case_detail(fixture.cases[0].review_case_id)
        assert repository._cover_read_observation is None
    finally:
        release.set()
        fixture.close()


@pytest.mark.parametrize("held_capture", [1, 2, 3, 4])
@pytest.mark.parametrize("deadline_expires", [False, True], ids=["drain", "deadline"])
def test_shutdown_retains_entire_browse_lifetime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    held_capture: int,
    deadline_expires: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
    )

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    app, repository = fixture.application, fixture.repository
    entered, release, stopping = (threading.Event() for _ in range(3))
    capture = repository._capture_cover_read
    assert capture is not None
    calls = 0
    closed: list[str] = []
    original_close = repository.close
    original_updates_close = app._updates.close

    def held():
        nonlocal calls
        calls += 1
        if calls == held_capture:
            assert not app._admission.held
            assert repository._serialization_lock is not None
            assert repository._serialization_lock.depth() == 0
            entered.set()
            assert release.wait(5)
        observation = capture()
        if held_capture > 2 and calls >= 2:
            return (
                observation[0],
                frozenset(
                    {("primary", "books-0", "work-0000", fixture.cases[0].report_id)}
                ),
            )
        return observation

    def close_root():
        assert release.is_set()
        assert app._active_browses == 0
        original_close()
        closed.append("root")

    def close_updates():
        original_updates_close()
        stopping.set()

    repository.bind_cover_read_observation(held)
    monkeypatch.setattr(repository, "close", close_root)
    monkeypatch.setattr(app._updates, "close", close_updates)
    if deadline_expires:
        app._graceful_shutdown_seconds = 0
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(
                app.library, lens="all", search=None, limit=5, cursor=None
            )
            try:
                assert entered.wait(5)
                assert app.owner_settings() is not None
                stop = pool.submit(fixture.close)
                assert stopping.wait(5)
                with pytest.raises(ReviewApplicationError, match="unavailable"):
                    app.library(lens="all", search=None, limit=5, cursor=None)
                if deadline_expires:
                    with pytest.raises(ReviewApplicationError, match="unavailable"):
                        stop.result(5)
                    assert app._active_browses == 1
                    assert repository._connection.execute("SELECT 1").fetchone() == (1,)
                    assert fixture.recovery.cover_fetching_identities() == frozenset()
                else:
                    assert not stop.done()
                assert closed == []
            finally:
                release.set()
            assert len(read.result(5).items) == 2
            if not deadline_expires:
                stop.result(5)
        assert app._active_browses == 0
        if deadline_expires:
            fixture.close()
        assert closed == ["root"]
    finally:
        release.set()
        if not closed:
            fixture.close()


def test_real_external_actors_leave_same_owner_interactions_available(
    tmp_path: Path,
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 20, mixed=True, prepare=True)
    try:
        assert fixture.application._reviewer_access is fixture.reviewer_access
        fixture.application.start()
        fixture.application.request_review_scan("primary", "books-0")
        with fixture.hold_external_actors() as actors:
            assert actors.receipt["reserved_work"] == "work-0018"
            assert (
                fixture.application.library(
                    lens="all", search=None, limit=25, cursor=None
                ).counts["all"]
                == 20
            )
            assert fixture.application.owner_settings() is not None
            reviewer_access = fixture.application.reviewer_access
            assert reviewer_access is not None
            assert reviewer_access.read() is not None
            other = fixture.cases[2]
            assert other.review_case_id != actors.reserved_case_id
            proposal = next(
                value for value in other.proposals if value["destination"] == "title"
            )
            proposal_id = proposal["proposal_id"]
            assert type(proposal_id) is str
            acknowledgement = fixture.application.record_decision(
                other.review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=other.target_fingerprint,
                destination="title",
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="external-overlap-unrelated",
            )
            confirmation = acknowledgement["confirmation"]
            assert type(confirmation) is dict and confirmation["status"] == "confirmed"
            actors.confirm_unrelated(other.review_case_id)
            assert fixture.preparation is not None
            assert any(value is not None for value in fixture.preparation.status()[0])
            assert "effect_state" not in actors.receipt
            assert "reviewer_model" not in actors.receipt
        assert actors.receipt["effect_state"] == "verified"
        assert actors.receipt["verified_events"] == 1
        assert actors.receipt["reviewer_unchanged"] is True
        assert actors.receipt["census_unchanged"] is True
        assert actors.receipt["confirmed_unrelated_cases"] == [other.review_case_id]
        print(json.dumps(actors.receipt, sort_keys=True))
    finally:
        fixture.close()


def test_supported_fixture_activity_projects_real_owner_history(tmp_path: Path) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 20, mixed=True, prepare=True)
    try:
        activity = fixture.application.activity()
        assert activity["ledger"]["total"] > 0
        assert any(event["category"] == "owner" for event in activity["events"])
        assert fixture.application._activity_projection is not None
        admission = fixture.application._activity_projection._operation_admission
        assert admission is not None
        assert fixture.repository.operation_admission is not None
        assert admission is not fixture.repository.operation_admission
        permission = fixture.application._permission_projection
        assert permission is not None
        assert permission._operation_admission not in (
            admission,
            fixture.repository.operation_admission,
        )
        assert (
            permission._repository._connection
            is not fixture.application._activity_projection._repository._connection
        )
        assert permission._repository._connection.execute(
            "PRAGMA query_only"
        ).fetchone() == (1,)
        assert activity["backup_ready"] is None and activity["example"] is None
        readiness = fixture.application.activity_permission_readiness()
        assert readiness["backup_ready"] is True
        assert set(readiness) == {
            "backup_ready",
            "example",
            "prepared",
            "configured_libraries",
            "capabilities",
        }
        # The composed application's notice read states Activity's offer (#1236).
        keys = (
            "backup_ready",
            "capabilities",
            "configured_libraries",
            "lapsed_consents",
            "waiting",
        )
        consent = fixture.application.activity_consent()
        assert consent == {key: activity[key] for key in keys}
    finally:
        fixture.close()


def test_dormant_preparation_classifies_startup_and_recovery_as_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 10, prepare=True)
    supervisor = fixture.preparation
    assert supervisor is not None
    admission = fixture.repository.operation_admission
    assert admission is not None
    observed: dict[str, str] = {}
    completed = threading.Event()
    recover = supervisor._pipeline.recover
    schedule = supervisor._schedule_due

    def observe(name: str) -> None:
        assert not admission.held
        admission.acquire()
        try:
            assert admission._active is not None
            observed[name] = admission._active.work_class
        finally:
            admission.release()

    def recovering() -> None:
        observe("startup")
        recover()

    def scheduling() -> None:
        observe("schedule")
        schedule()

    def recovery_wake(_now: int) -> None:
        observe("recovery")
        completed.set()

    monkeypatch.setattr(supervisor._pipeline, "recover", recovering)
    monkeypatch.setattr(supervisor, "_schedule_due", scheduling)
    monkeypatch.setattr(supervisor, "_recovery_wake", recovery_wake)
    try:
        supervisor.start_dormant()
        supervisor.admit()
        assert completed.wait(5)
        supervisor.stop()
        assert observed == {
            "startup": "background",
            "schedule": "background",
            "recovery": "background",
        }
        observe("owner")
        assert observed["owner"] == "foreground"
    finally:
        fixture.close()


def test_active_cover_acquisition_has_collection_detail_attention_parity(tmp_path):
    from dataclasses import replace

    from tests.unit.test_provider_recovery import _job

    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    try:
        case = fixture.cases[0]
        report = fixture.reports["work-0000"]
        fixture.recovery.enqueue_jobs(
            (
                replace(
                    _job("target"),
                    target_connection=report.target.connection,
                    library=report.target.library,
                    item=report.target.item,
                    source_report_id=report.report_id,
                    package_report_id=report.report_id,
                ),
            ),
            enqueued_at=3,
        )
        assert (
            fixture.recovery.claim_one(generation=1, now=7203, token="active-cover")
            is not None
        )
        fixture.covers.ensure(
            case.review_case_id,
            report.to_json(),
            next(p for p in case.proposals if p["destination"] == "cover"),
        )
        detail = fixture.application.case_detail(case.review_case_id).to_data()
        cover = next(
            field for field in detail["fields"] if field["destination"] == "cover"
        )
        assert cover["needs_attention"] is False
        assert cover["editable"] is False
        assert cover["cover_consideration"]["provider_fetching"] is True
        assert cover["cover_consideration"]["ready"] is False
        assert cover["cover_consideration"]["candidates"]
        page = fixture.application.library(
            lens="all", search=None, limit=5, cursor=None
        )
        assert page.items[0]["attention_count"] == sum(
            field["needs_attention"] for field in detail["fields"]
        )
        attention = fixture.application.attention(search=None, limit=10, cursor=None)
        assert all(item["destination"] != "cover" for item in attention.items)
    finally:
        fixture.close()


@pytest.mark.parametrize("settings_first", [False, True])
def test_cover_confirmation_refreshes_reviewer_authority_before_transaction(
    tmp_path, monkeypatch, settings_first
):
    from concurrent.futures import ThreadPoolExecutor

    from mechanical_librarian.application.review_web import ReviewWebError
    from tests.unit.test_reviewer_access_application import _OpenRouter

    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    entered, release, competing = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    try:
        access = fixture.reviewer_access
        monkeypatch.setattr(
            access,
            "_openrouter_factory",
            lambda key, model: _OpenRouter(key, model, []),
        )
        access.configure_openrouter(
            expected_revision=1, api_key="synthetic", model="example/cover"
        )
        case = fixture.cases[0]
        before = fixture.application.prepare_cover_consideration(case.review_case_id)
        proposal = next(p for p in case.proposals if p["destination"] == "cover")
        proposal_id = proposal["proposal_id"]
        assert type(proposal_id) is str
        selected = next(c for c in before["candidates"] if not c["incumbent"])
        identity = fixture.covers._reviewer_identity
        assert identity is not None
        calls = []

        def observed_identity():
            assert not fixture.repository._connection.in_transaction
            calls.append("identity")
            if not settings_first and not entered.is_set():
                entered.set()
                assert release.wait(5)
            return identity()

        monkeypatch.setattr(fixture.covers, "_reviewer_identity", observed_identity)

        def choose(consideration, key):
            return fixture.application.record_decision(
                case.review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome="accept",
                selected_value={"kind": "cover", "value": selected["artifact_id"]},
                idempotency_key=key,
                consideration_id=consideration["consideration_id"],
                cover_confirmation={
                    "expected_selection_id": None,
                    "confirm_unverified_match": False,
                },
            )

        def disable():
            if settings_first:

                def held():
                    result = access.select_none(expected_revision=2)
                    entered.set()
                    assert release.wait(5)
                    return result

                return fixture.application.serialized(held)
            competing.set()
            return access.select_none(expected_revision=2)

        def old_choice():
            if settings_first:
                competing.set()
            return choose(before, "reviewer-race-owner-0001")

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(disable if settings_first else old_choice)
            assert entered.wait(5)
            second = pool.submit(old_choice if settings_first else disable)
            assert competing.wait(5)
            assert not second.done()
            release.set()
            first.result(timeout=5)
            if settings_first:
                with pytest.raises(ReviewWebError, match="stale"):
                    second.result(timeout=5)
                assert fixture.repository._connection.execute(
                    "SELECT count(*) FROM cover_selection_authorities"
                ).fetchone() == (0,)
                fresh = fixture.application.prepare_cover_consideration(
                    case.review_case_id
                )
                assert fresh["consideration_id"] != before["consideration_id"]
                choose(fresh, "reviewer-fresh-owner-0002")
            else:
                second.result(timeout=5)
        assert calls
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (1,)
        assert not fixture.repository._connection.in_transaction
    finally:
        release.set()
        fixture.close()


@pytest.mark.parametrize("outcome", ["keep_current", "reject"])
def test_cover_nonaccept_refuses_unverified_confirmation(tmp_path, outcome):
    from mechanical_librarian.application.review_web import ReviewWebError

    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    try:
        case = fixture.cases[0]
        proposal_id = next(
            p["proposal_id"] for p in case.proposals if p["destination"] == "cover"
        )
        assert isinstance(proposal_id, str)
        consideration = fixture.covers.current(case.review_case_id)
        assert consideration is not None
        consideration_id = consideration["consideration_id"]
        assert isinstance(consideration_id, str)

        def choose(confirm_unverified_match):
            return fixture.application.record_decision(
                case.review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome=outcome,
                selected_value=None,
                idempotency_key="nonaccept-confirmation-0001",
                consideration_id=consideration_id,
                cover_confirmation={
                    "expected_selection_id": None,
                    "confirm_unverified_match": confirm_unverified_match,
                },
            )

        with pytest.raises(ReviewWebError, match="invalid_choice"):
            choose(True)
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (0,)
        result = choose(False)
        replay = choose(False)
        assert replay["decision_id"] == result["decision_id"]
        with pytest.raises(ReviewWebError, match="idempotency_conflict"):
            choose(True)
        assert fixture.repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (1,)
    finally:
        fixture.close()


@pytest.mark.parametrize("ineligible_alternative", [False, True])
def test_retained_incumbent_readiness_preserves_admitted_actions(
    tmp_path, monkeypatch, ineligible_alternative
):
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.application.review_web import ReviewWebError
    from mechanical_librarian.domain.cover_evidence import CoverSourceKind
    from tests.support import owner_responsiveness
    from tests.unit.test_cover_packets import _typed_discovery_identity_case

    original = owner_responsiveness._qualified_cover_report

    def produce(root):
        base, extra = original(root)
        sources = tuple(
            source
            for source in base.cover_sources
            if source.kind in {CoverSourceKind.ABS, CoverSourceKind.EPUB}
        )
        if not ineligible_alternative:
            report = MetadataReport.create(
                target=base.target,
                file=base.file,
                local_claims=base.local_claims,
                local_reasons=base.local_reasons,
                providers=base.providers,
                cover_sources=sources,
            )
        else:
            typed_document, _, _, preview = _typed_discovery_identity_case(
                local_creators=("Ada Author",), provider_creators=("Different Author",)
            )
            typed = MetadataReport.from_json(typed_document)
            artifacts = CoverArtifactStore(root / "artifacts")
            for source in typed.cover_sources:
                assert source.artifact is not None
                artifacts.stage(source.artifact, b"original-3", preview)
            report = MetadataReport.create(
                target=base.target,
                file=typed.file,
                local_claims=typed.local_claims,
                local_reasons=base.local_reasons,
                providers=typed.providers,
                cover_sources=tuple(
                    sorted(
                        (*sources, *typed.cover_sources),
                        key=lambda source: source.source_id,
                    )
                ),
            )
        return report, extra

    monkeypatch.setattr(owner_responsiveness, "_qualified_cover_report", produce)
    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    try:
        case = fixture.cases[0]
        detail = fixture.application.case_detail(case.review_case_id).to_data()
        cover = next(
            field for field in detail["fields"] if field["destination"] == "cover"
        )
        consideration = cover["cover_consideration"]
        assert consideration["ready"] is True
        assert consideration["provider_fetching"] is False
        assert any(candidate["incumbent"] for candidate in consideration["candidates"])
        assert not any(
            candidate["manual_eligible"] for candidate in consideration["candidates"]
        )
        assert cover["editable"] is ineligible_alternative
        assert cover["needs_attention"] is ineligible_alternative
        page = fixture.application.library(
            lens="all", search=None, limit=5, cursor=None
        )
        assert page.items[0]["attention_count"] == sum(
            field["needs_attention"] for field in detail["fields"]
        )
        attention = fixture.application.attention(search=None, limit=10, cursor=None)
        assert (
            any(item["destination"] == "cover" for item in attention.items)
            is ineligible_alternative
        )
        proposal_id = cover["proposal_id"]
        consideration_id = consideration["consideration_id"]
        assert isinstance(proposal_id, str) and isinstance(consideration_id, str)

        def keep():
            return fixture.application.record_decision(
                case.review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="retained-incumbent-0001",
                consideration_id=consideration_id,
                cover_confirmation={
                    "expected_selection_id": None,
                    "confirm_unverified_match": False,
                },
            )

        if ineligible_alternative:
            assert "keep_current" in cover["decision_actions"]
            assert keep()["outcome"] == "keep_current"
        else:
            assert not cover["decision_actions"]
            with pytest.raises(ReviewWebError, match="unsupported"):
                keep()
    finally:
        fixture.close()


@pytest.mark.parametrize("route", ["library?lens=all", "attention"])
@pytest.mark.parametrize("transitions", [1, 2])
def test_fetching_drift_recaptures_complete_http_browse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, transitions: int
) -> None:
    import httpx

    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )
    from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob
    from tests.support.owner_responsiveness import running_owner_fixture

    with running_owner_fixture(tmp_path, 3) as (fixture, origin):
        actor = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")
        try:
            for index in range(transitions):
                report = fixture.reports[f"work-{index:04d}"]
                actor.enqueue_jobs(
                    (
                        ProviderRecoveryJob(
                            f"google-{index}",
                            "google_books",
                            f"scope-{index}",
                            1,
                            f"member-{index}",
                            report.target.connection,
                            report.target.library,
                            report.target.item,
                            "file",
                            report.report_id,
                            "proposal",
                            report.report_id,
                            "case",
                            1,
                            "admitted",
                            '["work"]',
                        ),
                    ),
                    enqueued_at=3,
                )
            capture = fixture.browse.capture_browse_observation
            read = (
                fixture.browse.read_review_library
                if route.startswith("library")
                else fixture.browse.read_review_attention
            )
            captures = projections = 0

            def counted_capture():
                nonlocal captures
                captures += 1
                assert not fixture.repository.operation_admission.held
                assert fixture.browse._cover_read_observation is None
                return capture()

            def changing(*args):
                nonlocal projections
                projections += 1
                result = read(*args)
                assert not fixture.browse._connection.in_transaction
                if projections <= transitions:
                    assert (
                        actor.claim_one(
                            generation=1, now=7203, token=f"claim-{projections}"
                        )
                        is not None
                    )
                return result

            monkeypatch.setattr(
                fixture.browse, "capture_browse_observation", counted_capture
            )
            monkeypatch.setattr(
                fixture.browse,
                "read_review_library"
                if route.startswith("library")
                else "read_review_attention",
                changing,
            )
            with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
                assert client.get(f"/api/v1/{route}").status_code == 401
                assert (
                    client.post(
                        "/api/v1/session/login", json={"password": "synthetic-owner"}
                    ).status_code
                    == 200
                )
                separator = "&" if "?" in route else "?"
                assert (
                    client.get(f"/api/v1/{route}{separator}limit=0").status_code == 422
                )
                assert captures == 0 and projections == 0
                response = client.get(f"/api/v1/{route}")
                assert response.status_code == (200 if transitions == 1 else 503)
                assert captures == 4 and projections == 2
                assert fixture.application._active_browses == 0
                assert fixture.browse._cover_read_observation is None
                assert not fixture.browse._connection.in_transaction
                stable = client.get(f"/api/v1/{route}")
                assert stable.status_code == 200
                if transitions == 1:
                    assert response.json() == stable.json()
                else:
                    assert response.json() == {
                        "schema_version": "review-error-v1",
                        "error": {"code": "unavailable"},
                    }
        finally:
            actor.close()


@pytest.mark.parametrize("component", [0, 1], ids=["policy", "replay"])
@pytest.mark.parametrize("changed_capture", [2, 3, 4])
def test_browse_authority_drift_never_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: int,
    changed_capture: int,
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        capture = fixture.browse._capture_cover_read
        assert capture is not None
        observation = capture()
        calls = reads = 0
        read = fixture.browse.read_review_library

        def counted_read(*args):
            nonlocal reads
            reads += 1
            return read(*args)

        def changing():
            nonlocal calls
            calls += 1
            authority = observation[0]
            if calls >= changed_capture:
                authority = (
                    "cover_policy_" + "a" * 64 if component == 0 else authority[0],
                    "cover_replay_" + "b" * 64 if component == 1 else authority[1],
                )
            # Simultaneous fetching drift cannot mask authority revocation.
            fetching = (
                observation[1]
                if calls == 1
                else frozenset(
                    {("primary", "books-0", "work-0000", fixture.cases[0].report_id)}
                )
            )
            return authority, fetching

        monkeypatch.setattr(fixture.browse, "read_review_library", counted_read)
        fixture.browse.bind_cover_read_observation(changing)
        with pytest.raises(ReviewWebError, match="^unavailable$"):
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        assert calls == changed_capture
        assert reads == (2 if changed_capture == 4 else 1)
        assert fixture.application._active_browses == 0
        assert fixture.browse._cover_read_observation is None
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "queued", [False, True], ids=["before-retry", "retry-admission"]
)
def test_fetching_retry_preserves_original_disconnect_check(
    tmp_path: Path, queued: bool
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    disconnected, entered, release = (threading.Event() for _ in range(3))
    admission = fixture.application._admission
    capture = fixture.browse._capture_cover_read
    assert capture is not None
    original = capture()
    calls = 0

    def observe():
        nonlocal calls
        calls += 1
        if calls == 2:
            if queued:
                entered.set()
                assert release.wait(5)
            else:
                disconnected.set()
        return (
            original
            if calls == 1
            else (
                original[0],
                frozenset(
                    {("primary", "books-0", "work-0000", fixture.cases[0].report_id)}
                ),
            )
        )

    def check_active():
        if disconnected.is_set():
            raise InterruptedError("disconnected")

    fixture.browse.bind_cover_read_observation(observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                fixture.application.library,
                lens="all",
                search=None,
                limit=5,
                cursor=None,
                check_active=check_active,
            )
            if queued:
                assert entered.wait(5)
                with admission.enter():
                    release.set()
                    with admission._condition:
                        assert admission._condition.wait_for(
                            lambda: len(admission._waiting) == 1, 5
                        )
                    disconnected.set()
                # The same callback rejects the resumed second admission.
            with pytest.raises(InterruptedError, match="disconnected"):
                future.result(5)
        assert calls == (3 if queued else 2)
        assert fixture.application._active_browses == 0
        assert not admission.held and not admission._waiting
        assert fixture.browse._cover_read_observation is None
        assert not fixture.browse._connection.in_transaction
    finally:
        release.set()
        fixture.close()


def test_malformed_recovery_capture_is_unavailable_without_retry(
    tmp_path: Path,
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        fixture.recovery._connection.execute("DROP TABLE provider_recovery_jobs")
        fixture.recovery._connection.commit()
        with pytest.raises(ReviewWebError, match="^unavailable$"):
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        assert fixture.application._active_browses == 0
        assert fixture.browse._cover_read_observation is None
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


@pytest.mark.parametrize("missing", ["initial", "final"])
def test_missing_browse_observation_is_not_fetching_drift(
    tmp_path: Path,
    missing: str,
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    try:
        capture = fixture.browse._capture_cover_read
        assert capture is not None
        observation = capture()
        if missing == "final":
            fixture.browse._capture_cover_read = None
        with pytest.raises(
            MetadataReportError, match="^review snapshot is unavailable$"
        ) as raised:
            fixture.browse.recheck_browse_observation(
                None if missing == "initial" else observation
            )
        assert type(raised.value) is MetadataReportError
    finally:
        fixture.close()


def test_concurrent_initial_library_and_attention_use_real_http(tmp_path: Path) -> None:
    with running_owner_fixture(tmp_path, 10) as (fixture, origin):
        expected = fixture.repository.count_current_review_cases(None)
        fields = fixture.repository.count_current_attention_fields(None)
        with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
            assert (
                client.post(
                    "/api/v1/session/login", json={"password": "synthetic-owner"}
                ).status_code
                == 200
            )
            start = threading.Barrier(2)

            def read(path: str) -> httpx.Response:
                start.wait(timeout=5)
                return client.get(path)

            with ThreadPoolExecutor(max_workers=2) as executor:
                library = executor.submit(read, "/api/v1/library?lens=all&limit=5")
                attention = executor.submit(read, "/api/v1/attention?limit=5")
                library_response, attention_response = (
                    library.result(),
                    attention.result(),
                )
            assert library_response.status_code == attention_response.status_code == 200
            assert library_response.json()["counts"] == {
                "all": expected[0],
                "needs_attention": expected[1],
                "no_attention_needed": expected[0] - expected[1],
            }
            assert len(library_response.json()["items"]) == 5
            assert attention_response.json()["total"] == fields
            assert len(attention_response.json()["items"]) == min(5, fields)
            for path in ("/api/v1/library", "/api/v1/attention"):
                empty = client.get(path, params={"q": "no-synthetic-work-matches"})
                assert empty.status_code == 200 and empty.json()["items"] == []
                assert (
                    client.get(path, params={"cursor": "malformed"}).status_code == 400
                )
        assert not fixture.repository._connection.in_transaction
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        fixture.repository._connection.execute("SELECT 1")
