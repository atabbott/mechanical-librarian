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
