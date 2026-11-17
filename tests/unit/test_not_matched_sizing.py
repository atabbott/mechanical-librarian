"""The Not matched sizing script counts the lens by medium and reason (#1477 S32)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import cast

import scripts.not_matched_sizing as sizing

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
)
from tests.support.audiobook_reports import recording_lookup_hint_report
from tests.support.review_cases import _report as _plain_report

_SENTINEL_TITLE = "Sizing Sentinel Title"


def _unmatched(item: str, identity: str, status: ProviderStatus) -> MetadataReport:
    source = _plain_report(_SENTINEL_TITLE, "4", "5", actionable=False)
    return MetadataReport.create(
        target=TargetFacts(
            source.target.connection, source.target.library, item, source.target.fields
        ),
        file=FileFacts(0, identity * 64, source.file.content_digest),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(ProviderObservation("hardcover-main", status),),
    )


def _snapshot(directory: Path) -> tuple[Path, int]:
    """A synthetic application database written by the real producer."""

    database = directory / "application.sqlite3"
    repository = open_repository(database)
    for item, identity, status in (
        ("item-no-match", "1", ProviderStatus.NO_MATCH),
        ("item-unavailable", "2", ProviderStatus.UNAVAILABLE),
        ("item-rate-limited", "3", ProviderStatus.RATE_LIMITED),
    ):
        repository.publish(_unmatched(item, identity, status), lambda: True)
    repository.publish(recording_lookup_hint_report(), lambda: True)
    repository.publish(_plain_report(_SENTINEL_TITLE, "6", "7"), lambda: True)
    lens = BrowseReviewLibrary(repository).library(
        lens="not_matched", search=None, limit=50, cursor=None
    )
    repository.close()
    return database, cast("dict[str, int]", lens["counts"])["not_matched"]


def test_counts_are_the_not_matched_lens_by_medium_and_reason(tmp_path: Path) -> None:
    database, lens_count = _snapshot(tmp_path)
    connection = sizing.open_read_only(database)
    try:
        document = sizing.not_matched_sizing(connection)
    finally:
        connection.close()

    assert document == {
        "schema_version": "not-matched-sizing-v1",
        "current_works": {"ebook": 4, "audiobook": 1},
        "provider_match_state": {
            "ebook": {"matched": 1, "no_match": 1, "unavailable": 2},
            "audiobook": {"refused": 1},
        },
        "not_matched": {
            "ebook": {"total": 3, "no_match": 1, "unavailable": 2},
            "audiobook": {"total": 1, "refused": 1},
        },
    }
    not_matched = cast("dict[str, dict[str, int]]", document["not_matched"])
    # Control: the same works Library's own Not matched lens counts.
    assert lens_count == 4
    assert sum(medium["total"] for medium in not_matched.values()) == lens_count


def test_main_prints_aggregates_only_and_writes_nothing(tmp_path: Path) -> None:
    database, _ = _snapshot(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    siblings = sorted(path.name for path in tmp_path.iterdir())
    stdout = io.StringIO()

    assert sizing.main(["--database", str(database)], stdout=stdout) == 0

    text = stdout.getvalue()
    assert json.loads(text)["current_works"] == {"ebook": 4, "audiobook": 1}
    for value in (_SENTINEL_TITLE, "item-", "hardcover", str(tmp_path)):
        assert value not in text
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == siblings


def test_a_file_that_is_not_an_application_database_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite3"
    other.write_bytes(b"not a database")
    missing = tmp_path / "missing.sqlite3"

    assert sizing.main(["--database", str(other)], stdout=io.StringIO()) == 2
    assert sizing.main(["--database", str(missing)], stdout=io.StringIO()) == 2
    assert not missing.exists()
