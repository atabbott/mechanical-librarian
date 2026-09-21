"""Real complete-cover construction, owned reads, drift and durable decisions."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.owner_responsiveness import OwnerResponsivenessFixture


def test_complete_cover_browse_avoids_full_report_validation_in_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 10)
    try:
        assert fixture.browse._connection is not fixture.repository._connection
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


def test_external_drift_refuses_complete_browse_response(tmp_path: Path) -> None:
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
        with pytest.raises(MetadataReportError, match="review snapshot is unavailable"):
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        assert calls == 2
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


def test_held_post_snapshot_browse_does_not_block_owner_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 2)
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    original = fixture.browse.read_review_library

    def held(*args):
        result = original(*args)
        assert not fixture.browse._connection.in_transaction
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(fixture.browse, "read_review_library", held)

    def read():
        try:
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=read)
    thread.start()
    try:
        assert entered.wait(5)
        detail = fixture.application.case_detail(fixture.cases[0].review_case_id)
        assert detail is not None
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
        assert not release.is_set()
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


@pytest.mark.parametrize("failed_capture", (1, 2))
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
            return observation

        fixture.browse.bind_cover_read_observation(unavailable)
        with pytest.raises(
            MetadataReportError, match="^review snapshot is unavailable$"
        ):
            fixture.application.library(lens="all", search=None, limit=5, cursor=None)
        assert fixture.browse._cover_read_observation is None
        assert not fixture.browse._connection.in_transaction
    finally:
        fixture.close()


def test_browse_borrower_closes_before_root_and_refuses_later_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
    )

    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    closed: list[str] = []
    close_browse = fixture.application._close_browse
    close_root = fixture.repository.close
    assert close_browse is not None

    def browse_close():
        close_browse()
        closed.append("browse")

    def root_close():
        assert closed == ["browse"]
        close_root()
        closed.append("root")

    fixture.application._close_browse = browse_close
    monkeypatch.setattr(fixture.repository, "close", root_close)
    fixture.close()
    assert closed == ["browse", "root"]
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
