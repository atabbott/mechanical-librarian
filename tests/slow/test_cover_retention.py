"""Equal-history physical storage costs, generated through the real producer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from tests.support.owner_responsiveness import OwnerResponsivenessFixture


def test_repeated_cover_histories_share_reports_and_artifacts(tmp_path: Path) -> None:
    (tmp_path / "producer").mkdir(mode=0o700)
    fixture = OwnerResponsivenessFixture(tmp_path / "producer", 24, mixed=False)
    assert fixture.repository._connection.execute(
        "SELECT count(*) FROM cover_considerations"
    ).fetchone() == (24,)
    old = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/cover-consideration-embedded-report.json"
        ).read_text()
    )
    connections = {}
    paths = {}
    for representation in ("full", "compact"):
        path = tmp_path / f"{representation}.sqlite"
        connection = sqlite3.connect(path)
        # These two equal-schema storage specimens isolate report/consideration
        # representation. The producer remains a complete production repository.
        connection.execute(schema._REPORTS_SQL)
        connection.execute(
            old["cover_considerations_sql"]
            if representation == "full"
            else schema._COVER_CONSIDERATIONS_SQL
        )
        connections[representation] = connection
        paths[representation] = path
    snapshots: list[dict[str, dict[str, int]]] = []
    expected_history = {}
    artifact_files = {
        (path.stat().st_dev, path.stat().st_ino): path.read_bytes()
        for path in (fixture.root / "artifacts").rglob("*")
        if path.is_file()
    }
    try:
        for revision in range(4):
            if revision:
                for case in fixture.cases:
                    report = fixture.reports[str(case.target["item"])]
                    fixture.covers.ensure(
                        case.review_case_id,
                        report.to_json(),
                        next(
                            value
                            for value in case.proposals
                            if value["destination"] == "cover"
                        ),
                        retry=True,
                    )
            source = fixture.repository._connection
            reports = source.execute("SELECT * FROM metadata_reports").fetchall()
            columns = [
                row[1]
                for row in source.execute("PRAGMA table_info(cover_considerations)")
            ]
            document_index = columns.index("document")
            rows = source.execute(
                "SELECT * FROM cover_considerations ORDER BY occurred_at,consideration_id"
            ).fetchall()
            expanded = dict(
                source.execute(
                    "SELECT consideration.consideration_id,consideration.document FROM cover_considerations consideration"
                )
            )
            for row in source.execute(
                "SELECT consideration.consideration_id,consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM cover_considerations consideration LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id"
            ):
                expanded[row[0]] = expand_cover_consideration(*row[1:])
            measurements = {}
            for representation, connection in connections.items():
                connection.executemany(
                    "INSERT OR IGNORE INTO metadata_reports VALUES("
                    + ",".join("?" for _ in reports[0])
                    + ")",
                    reports,
                )
                payload = []
                for row in rows:
                    values = list(row)
                    if representation == "full":
                        values[document_index] = expanded[row[0]]
                    payload.append(values)
                connection.executemany(
                    "INSERT OR IGNORE INTO cover_considerations("
                    + ",".join(columns)
                    + ") VALUES("
                    + ",".join("?" for _ in columns)
                    + ")",
                    payload,
                )
                connection.commit()
                information = paths[representation].stat()
                measurements[representation] = {
                    "considerations": connection.execute(
                        "SELECT count(*) FROM cover_considerations"
                    ).fetchone()[0],
                    "reports": connection.execute(
                        "SELECT count(*) FROM metadata_reports"
                    ).fetchone()[0],
                    "payload_bytes": connection.execute(
                        "SELECT sum(length(CAST(document AS BLOB))) FROM cover_considerations"
                    ).fetchone()[0],
                    "pages": connection.execute("PRAGMA page_count").fetchone()[0],
                    "logical_bytes": information.st_size,
                    "allocated_bytes": information.st_blocks * 512,
                }
            full, compact = measurements["full"], measurements["compact"]
            assert (
                full["considerations"]
                == compact["considerations"]
                == 24 * (revision + 1)
            )
            assert full["reports"] == compact["reports"] == 24
            for metric in (
                "payload_bytes",
                "pages",
                "logical_bytes",
                "allocated_bytes",
            ):
                assert compact[metric] < full[metric]
                if snapshots:
                    assert (
                        compact[metric] - snapshots[-1]["compact"][metric]
                        < full[metric] - snapshots[-1]["full"][metric]
                    )
            snapshots.append(measurements)
        for case in fixture.cases:
            expected_history[case.review_case_id] = (
                fixture.repository.cover_consideration_history(case.review_case_id)
            )
            assert len(expected_history[case.review_case_id]) == 7
        assert artifact_files == {
            (path.stat().st_dev, path.stat().st_ino): path.read_bytes()
            for path in (fixture.root / "artifacts").rglob("*")
            if path.is_file()
        }
        for path in (fixture.root / "artifacts").rglob("*"):
            if path.is_file():
                assert fixture.repository.artifact_is_referenced(path.name)
    finally:
        fixture.close()
        for connection in connections.values():
            connection.close()
    reopened = open_repository(fixture.database, create=False)
    try:
        for case_id, history in expected_history.items():
            assert reopened.cover_consideration_history(case_id) == history
    finally:
        reopened.close()
    print(
        json.dumps(
            {"storage_specimens": "reports-and-considerations", "revisions": snapshots},
            sort_keys=True,
        )
    )
