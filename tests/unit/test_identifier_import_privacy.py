"""No row text, path or item ID of an identifiers file is kept or echoed (#1478).

The file names works by sentinel paths, item IDs and identifiers that match
nothing; none of them reaches an answer, a stderr event or any stored row,
whether the file is previewed, imported or refused.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit.test_identifier_import_web import SENTINELS, _Live, identifier_web


@pytest.fixture
def live(tmp_path: Path) -> Iterator[_Live]:
    with identifier_web(tmp_path) as web:
        yield web


def test_no_row_text_path_or_item_reaches_an_answer_an_event_or_the_store(
    live: _Live, capsys: pytest.CaptureFixture[str]
) -> None:
    preview = live.preview()
    imported = live.confirm(preview.json()["preview_id"])
    status = live.client.get(
        f"/api/v1/identifier-imports/{imported.json()['import_id']}"
    )
    refused = live.preview(b"path\tidentifier\n" + SENTINELS[0].encode() + b"\x07\n")
    text = json.dumps([preview.json(), imported.json(), status.json(), refused.json()])
    stderr = capsys.readouterr().err
    connection = sqlite3.connect(live.root / "state.sqlite3")
    try:
        stored = "\n".join(
            line for line in connection.iterdump() if line.startswith("INSERT")
        )
    finally:
        connection.close()
    for sentinel in SENTINELS:
        for surface in (text, stderr, stored):
            assert sentinel.lower() not in surface.lower()
    events = [json.loads(line) for line in stderr.splitlines() if "identifier" in line]
    assert {event["outcome"] for event in events} >= {"previewed", "recorded"}
