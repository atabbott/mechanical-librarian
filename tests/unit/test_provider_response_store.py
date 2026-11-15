"""The provider-response store keeps clean answers and never fails a request.

It follows the private stores' admission rules (absent or exact, owner-only,
refused without mutation) except that open reads no payload: each row proves
its integrity when it is read (#1064, ADR-0101).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import sqlite3
import stat
import zlib
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import provider_responses
from mechanical_librarian.adapters.persistence.provider_responses import (
    SQLiteProviderResponseStore,
    open_provider_response_store,
)
from mechanical_librarian.application.metadata_report import MetadataReportError

_DIGEST = "d" * 64


def _events(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines() if line]


def _store(tmp_path: Path) -> SQLiteProviderResponseStore:
    return SQLiteProviderResponseStore.open(tmp_path / "provider-responses.sqlite")


def test_an_absent_store_is_created_owner_only_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "provider-responses.sqlite"
    store = SQLiteProviderResponseStore.open(path)
    store.commit("open_library", ((_DIGEST, 7, b'{"docs":[]}'),))
    store.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    reopened = SQLiteProviderResponseStore.open(path)
    assert reopened.get("open_library", _DIGEST) == (b'{"docs":[]}', 7)
    assert reopened.get("hardcover", _DIGEST) is None
    reopened.close()


def _extra_index(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE INDEX extra ON provider_responses(payload_bytes)")


def _altered_table(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX provider_responses_by_age")
    connection.execute("DROP TABLE provider_responses")
    connection.execute("CREATE TABLE provider_responses(adapter TEXT)")


def _rowid_table(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE other(value TEXT)")


@pytest.mark.parametrize(
    "change",
    (_extra_index, _altered_table, _rowid_table),
    ids=("extra_index", "altered_table", "extra_table"),
)
def test_a_store_that_is_not_exact_is_refused_unchanged(
    tmp_path: Path, change: object
) -> None:
    path = tmp_path / "provider-responses.sqlite"
    _store(tmp_path).close()
    connection = sqlite3.connect(path)
    change(connection)  # type: ignore[operator]  # reason: each parameter is one of the three schema edits above
    connection.commit()
    connection.close()
    before = path.read_bytes()
    with pytest.raises(MetadataReportError):
        SQLiteProviderResponseStore.open(path)
    assert path.read_bytes() == before


def test_a_symlink_or_shared_file_is_refused_and_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "elsewhere.sqlite"
    _store(tmp_path).close()
    (tmp_path / "provider-responses.sqlite").rename(target)
    before = target.read_bytes()
    link = tmp_path / "provider-responses.sqlite"
    link.symlink_to(target)
    with pytest.raises(MetadataReportError):
        SQLiteProviderResponseStore.open(link)
    assert target.read_bytes() == before and link.is_symlink()

    link.unlink()
    target.rename(link)
    link.chmod(0o644)
    capsys.readouterr()
    assert open_provider_response_store(link) is None
    assert link.read_bytes() == before
    assert _events(capsys.readouterr().err) == [
        {
            "event": "provider_response_store",
            "level": "warning",
            "store_fault": "unavailable",
        }
    ]


def test_open_reads_no_payload_and_a_corrupt_row_is_a_deleted_miss(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "provider-responses.sqlite"
    store = SQLiteProviderResponseStore.open(path)
    store.commit(
        "google_books",
        (("a" * 64, 1, b"first body"), ("b" * 64, 2, b"second body")),
    )
    store.close()
    connection = sqlite3.connect(path)
    # Same length, different bytes: only the digest can tell.
    tampered = zlib.compress(b"forged body")
    connection.execute(
        "UPDATE provider_responses SET payload=?,stored_bytes=? WHERE digest=?",
        (tampered, len(tampered), "a" * 64),
    )
    connection.execute(
        "UPDATE provider_responses SET payload=x'00',stored_bytes=1 WHERE digest=?",
        ("b" * 64,),
    )
    connection.commit()
    connection.close()

    store = SQLiteProviderResponseStore.open(path)
    capsys.readouterr()
    assert store.get("google_books", "a" * 64) is None
    assert store.get("google_books", "b" * 64) is None
    assert [event["store_fault"] for event in _events(capsys.readouterr().err)] == [
        "row_corrupt",
        "row_corrupt",
    ]
    store.close()
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM provider_responses").fetchone() == (
        0,
    )
    connection.close()


def test_an_overlong_stored_value_is_refused_before_it_is_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schema bounds the body a row states, not the bytes it holds, so a
    row written from outside is measured before its value is fetched."""
    path = tmp_path / "provider-responses.sqlite"
    store = SQLiteProviderResponseStore.open(path)
    # The largest body the store accepts, at its least compressible, fits.
    largest = os.urandom(provider_responses.MAX_PAYLOAD_BYTES)
    store.commit("google_books", (("a" * 64, 1, largest), ("b" * 64, 1, b"body")))
    assert store.get("google_books", "a" * 64) == (largest, 1)
    store.close()
    connection = sqlite3.connect(path)
    overlong = b"\0" * (provider_responses._MAX_STORED_BYTES + 1)
    connection.execute(
        "UPDATE provider_responses SET payload=?,stored_bytes=? WHERE digest=?",
        (overlong, len(overlong), "b" * 64),
    )
    connection.commit()
    connection.close()

    fetched: list[object] = []
    verified = provider_responses._verified

    def spy(payload_digest: object, payload_bytes: object, stored: object) -> object:
        fetched.append(stored)
        return verified(payload_digest, payload_bytes, stored)

    monkeypatch.setattr(provider_responses, "_verified", spy)
    store = SQLiteProviderResponseStore.open(path)
    capsys.readouterr()
    assert store.get("google_books", "b" * 64) is None
    assert fetched == [None]
    assert [event["store_fault"] for event in _events(capsys.readouterr().err)] == [
        "row_corrupt"
    ]
    assert store._connection.execute(
        "SELECT digest FROM provider_responses"
    ).fetchall() == [("a" * 64,)]
    store.close()


def test_the_schema_refuses_a_fetch_time_past_the_report_range(tmp_path: Path) -> None:
    path = tmp_path / "provider-responses.sqlite"
    store = SQLiteProviderResponseStore.open(path)
    store.commit("audnexus", ((_DIGEST, 1, b"body"),))
    store.close()
    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE provider_responses SET fetched_at_us=253402300800000000"
        )
    connection.close()


def test_a_store_that_cannot_be_used_degrades_with_one_event(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.commit("inventaire", ((_DIGEST, 1, b"body"),))
    blocker = sqlite3.connect(tmp_path / "provider-responses.sqlite", timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    capsys.readouterr()
    # Another writer holds the lock past the busy timeout: the commit is
    # skipped, and a read still answers.
    store.commit("inventaire", (("e" * 64, 2, b"other"),))
    blocker.execute("ROLLBACK")
    blocker.close()
    assert store.get("inventaire", _DIGEST) == (b"body", 1)
    assert store.get("inventaire", "e" * 64) is None
    store.close()
    assert store.get("inventaire", _DIGEST) is None
    store.commit("inventaire", (("f" * 64, 3, b"late"),))
    assert [
        (event["store_fault"], event["adapter"])
        for event in _events(capsys.readouterr().err)
    ] == [
        ("write_failed", "inventaire"),
        ("read_failed", "inventaire"),
        ("write_failed", "inventaire"),
    ]


def test_a_hit_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.commit("itunes_search", ((_DIGEST, 1, b"body"),))
    connection = store._connection
    before = connection.total_changes
    assert store.get("itunes_search", _DIGEST) == (b"body", 1)
    assert connection.total_changes == before
    store.close()


def test_a_newer_answer_replaces_the_held_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.commit("hardcover", ((_DIGEST, 1, b"old"),))
    store.commit("hardcover", ((_DIGEST, 2, b"new"),))
    assert store.get("hardcover", _DIGEST) == (b"new", 2)
    assert store._connection.execute(
        "SELECT count(*) FROM provider_responses"
    ).fetchone() == (1,)
    store.close()


def test_the_ceiling_removes_the_oldest_answers_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    bodies = {key: os.urandom(400) for key in "abcd"}
    sizes = {key: len(zlib.compress(body)) for key, body in bodies.items()}
    monkeypatch.setattr(
        provider_responses, "MAX_STORE_BYTES", sizes["c"] + sizes["d"] + 1
    )
    store.commit("audiosilo", (("c" * 64, 3, bodies["c"]), ("a" * 64, 1, bodies["a"])))
    store.commit("audiosilo", (("b" * 64, 2, bodies["b"]),))
    store.commit("audiosilo", (("d" * 64, 4, bodies["d"]),))
    held = {
        key
        for key in "abcd"
        if store.get("audiosilo", key * 64) == (bodies[key], " abcd".index(key))
    }
    assert held == {"c", "d"}
    store.close()


def test_the_size_sum_and_the_oldest_first_removal_read_only_the_index(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for query in (
        "SELECT coalesce(sum(stored_bytes),0) FROM provider_responses",
        "SELECT adapter,digest,stored_bytes FROM provider_responses "
        "ORDER BY fetched_at_us LIMIT 64",
    ):
        plan = " ".join(
            row[-1] for row in store._connection.execute("EXPLAIN QUERY PLAN " + query)
        )
        assert "COVERING INDEX provider_responses_by_age" in plan, plan
    store.close()


def test_an_older_answer_never_replaces_a_newer_one(tmp_path: Path) -> None:
    """An observation that finishes late cannot undo a newer one."""
    store = _store(tmp_path)
    digest = "a" * 64
    store.commit("open_library", ((digest, 2, b"newer"),))
    store.commit("open_library", ((digest, 1, b"older"),))
    assert store.get("open_library", digest) == (b"newer", 2)
    store.commit("open_library", ((digest, 2, b"same time"),))
    assert store.get("open_library", digest) == (b"same time", 2)
    store.close()


def test_the_store_never_replaces_a_row_wholesale() -> None:
    # The SQL is written in upper case; an upsert keeps the row's key.
    assert "REPLACE" not in inspect.getsource(provider_responses)


def _write_many(path: str, prefix: str) -> None:
    store = SQLiteProviderResponseStore.open(Path(path))
    for index in range(20):
        key = hashlib.sha256(f"{prefix}{index}".encode()).hexdigest()
        store.commit("open_library", ((key, index, prefix.encode() * 50),))
    store.close()


def test_two_processes_write_at_once(tmp_path: Path) -> None:
    path = tmp_path / "provider-responses.sqlite"
    _store(tmp_path).close()
    workers = [
        multiprocessing.Process(target=_write_many, args=(str(path), prefix))
        for prefix in ("left", "right")
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
        assert worker.exitcode == 0
    store = SQLiteProviderResponseStore.open(path)
    assert store._connection.execute(
        "SELECT count(*) FROM provider_responses"
    ).fetchone() == (40,)
    store.close()
