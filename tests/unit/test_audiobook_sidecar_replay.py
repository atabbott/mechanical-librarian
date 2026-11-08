"""The #1481 sidecar replay over a synthetic snapshot (R1).

The snapshot holds reports in the pre-#1481 shape, where a manifest entry is
only a name and a digest.  Every name and value here is synthetic.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest
import scripts.audiobook_sidecar_replay as replay


def _report(
    sidecars: tuple[str, ...], *, claims: tuple[str, ...] = ("embedded_tag",)
) -> str:
    return json.dumps(
        {
            "audio": {
                "sidecars": [{"digest": "a" * 64, "name": name} for name in sidecars],
                "tracks": [{"name": "book.m4b"}],
            },
            "local_claims": [{"basis": basis} for basis in claims],
        }
    )


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    database = tmp_path / "application.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE metadata_reports(report_id TEXT PRIMARY KEY, document TEXT);"
        "CREATE TABLE current_review_packages(report_id TEXT);"
    )
    documents = (
        _report(("metadata.json",), claims=("sidecar", "embedded_tag")),
        _report(("metadata.abs",)),
        _report(("book.opf", "metadata.json"), claims=("sidecar",)),
        _report(("book.metadata.json", "notes.metadata.json")),
        _report(()),
        json.dumps({"local_claims": []}),
    )
    for index, document in enumerate(documents):
        connection.execute(
            "INSERT INTO metadata_reports VALUES (?,?)", (f"r{index}", document)
        )
        connection.execute(
            "INSERT INTO current_review_packages VALUES (?)", (f"r{index}",)
        )
    connection.commit()
    connection.close()
    return database


def test_the_replay_counts_record_only_reports_as_changed(snapshot: Path) -> None:
    output = io.StringIO()
    assert replay.main(["--database", str(snapshot)], output) == 0

    assert json.loads(output.getvalue()) == {
        "schema_version": "audiobook-sidecar-replay-v1",
        "audiobook_reports": 5,
        "entries_by_kind": {
            "audiobookshelf_record": 3,
            "libation": 1,
            "opf": 1,
            "unrecognised": 1,
        },
        "reports_with_sidecar_claims": 2,
        "changed": 2,
        "undetermined": 2,
        "unaffected": 1,
    }
    assert ".opf" not in output.getvalue()
    assert "notes" not in output.getvalue()


def test_the_replay_opens_the_snapshot_read_only(snapshot: Path) -> None:
    before = snapshot.read_bytes()
    connection = replay._open_read_only(snapshot)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE written(value TEXT)")
    finally:
        connection.close()
    replay.main(["--database", str(snapshot)], io.StringIO())
    assert snapshot.read_bytes() == before
