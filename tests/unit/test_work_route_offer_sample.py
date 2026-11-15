"""The work-route offer finder (#1642).

Stores are product-built through the real provider adapters, by the producers
of the work-route measurement's own tests.
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import scripts.hardcover_series_sample as series_sample
import scripts.work_route_offer_sample as offer_sample

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.unit.test_work_route_measurement import (
    AUTHOR,
    NARRATOR,
    TITLE,
    _decide,
    _fields,
    _hardcover,
    _measure,
    _open_library,
    _publish,
    _recording,
    _report,
    _unmatched,
)

_LINK = re.compile(
    r"^http://localhost:8080/library\?lens=all&preview=(?P<case>review_case_[0-9a-f]{64})"
    r"&field=(?P<field>description|genres|narrators)"
    r"  From (Hardcover|Open Library|Hardcover and Open Library)\.$"
)
_COUNT = re.compile(
    r"^(Description|Genres|Narrators): [0-9]+ to decide, [0-9]+ already decided\.$"
)


def _link(case_id: str, field: str, providers: str) -> str:
    return (
        f"http://localhost:8080/library?lens=all&preview={case_id}&field={field}"
        f"  From {providers}."
    )


def _run(database: Path, *extra: str) -> list[str]:
    stdout = io.StringIO()
    assert offer_sample.main(["--database", str(database), *extra], stdout) == 0
    return stdout.getvalue().splitlines()


def _block(lines: list[str], name: str) -> list[str]:
    start = next(index for index, line in enumerate(lines) if line.startswith(name))
    block = lines[start:]
    return block[: block.index("")] if "" in block else block


def _store(
    database: Path,
    build: Callable[[SQLiteMetadataReportRepository], dict[str, str]],
) -> dict[str, str]:
    repository = open_repository(database)
    try:
        return build(repository)
    finally:
        repository.close()


def _measured_store(repository: SQLiteMetadataReportRepository) -> dict[str, str]:
    """The measurement test's u1 to u5: decided, kept, withdrawn, filled, duplicate."""
    cases = {
        "accepted": _publish(repository, _unmatched("u1")),
        "kept": _publish(repository, _unmatched("u2")),
        "withdrawn": _publish(repository, _unmatched("u3")),
        "filled": _publish(
            repository, _unmatched("u4", description="Already written.")
        ),
        "duplicate": _publish(
            repository, _report("u5", _hardcover({"id": 10}, {"id": 11}))
        ),
    }
    _decide(repository, cases["accepted"], "accept", "accept")
    _decide(repository, cases["kept"], "keep_current", "keep")
    _decide(repository, cases["withdrawn"], "accept", "genre", destination="genres")
    _decide(repository, cases["withdrawn"], "withdraw", "ungenre", destination="genres")
    _decide(repository, cases["filled"], "accept", "filled")
    return cases


def _labelled(database: Path, field: str) -> int:
    labels = cast("dict[str, int]", _fields(_measure(database))[field]["labels"])
    return labels["labelled"]


def test_lists_exactly_the_fields_the_measurement_would_compare(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    cases = _store(database, _measured_store)
    both, hardcover = "Hardcover and Open Library", "Hardcover"
    assert _run(database) == [
        "Description: 1 to decide, 2 already decided.",
        _link(cases["withdrawn"], "description", both),
        "",
        "Genres: 4 to decide, 0 already decided.",
        _link(cases["accepted"], "genres", hardcover),
        _link(cases["kept"], "genres", hardcover),
        _link(cases["withdrawn"], "genres", hardcover),
        _link(cases["filled"], "genres", hardcover),
        "",
        "Narrators: 0 to decide, 0 already decided.",
    ]
    # "Already decided" is the measurement's own label count.
    assert (_labelled(database, "description"), _labelled(database, "genres")) == (
        2,
        0,
    )
    assert _labelled(database, "narrators") == 0


def test_deciding_every_listed_field_adds_that_many_labels(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    _store(database, _measured_store)
    before = {
        field: _labelled(database, field)
        for field in ("description", "genres", "narrators")
    }
    listed = [
        (match["case"], match["field"])
        for line in _run(database)
        if (match := _LINK.match(line)) is not None
    ]
    assert len(listed) == 5

    def decide(repository: SQLiteMetadataReportRepository) -> dict[str, str]:
        for index, (case_id, field) in enumerate(listed):
            _decide(repository, case_id, "accept", f"listed-{index}", destination=field)
        return {}

    _store(database, decide)
    for field in ("description", "genres", "narrators"):
        added = sum(1 for _case, name in listed if name == field)
        assert _labelled(database, field) == before[field] + added
    assert [line for line in _run(database) if _LINK.match(line)] == []
    assert _block(_run(database), "Genres") == [
        "Genres: 0 to decide, 4 already decided."
    ]


def test_a_withdrawn_decision_lists_the_field_again(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"

    def build(repository: SQLiteMetadataReportRepository) -> dict[str, str]:
        case_id = _publish(repository, _unmatched("u1"))
        _decide(repository, case_id, "accept", "accept", destination="genres")
        _decide(repository, case_id, "withdraw", "withdraw", destination="genres")
        return {"case": case_id}

    cases = _store(database, build)
    assert _block(_run(database), "Genres") == [
        "Genres: 1 to decide, 0 already decided.",
        _link(cases["case"], "genres", "Hardcover"),
    ]


def test_a_recording_admitted_audiobook_is_not_listed(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"

    def build(repository: SQLiteMetadataReportRepository) -> dict[str, str]:
        recorded = _report(
            "r1",
            _recording(),
            _hardcover({"description": "A tide that keeps time.", "genre": "Sea"}),
            _open_library("2001"),
        )
        return {"case": _publish(repository, recorded)}

    _store(database, build)
    assert [line for line in _run(database) if _COUNT.match(line)] == [
        "Description: 0 to decide, 0 already decided.",
        "Genres: 0 to decide, 0 already decided.",
        "Narrators: 0 to decide, 0 already decided.",
    ]


def _hardcover_only() -> MetadataReport:
    return _report("p1", _hardcover({"description": "A tide that keeps time."}))


def _open_library_only() -> MetadataReport:
    return _report("p1", _open_library("2001"))


@pytest.mark.parametrize(
    ("report", "providers"),
    [
        (_hardcover_only, "Hardcover"),
        (_open_library_only, "Open Library"),
        (lambda: _unmatched("p1"), "Hardcover and Open Library"),
    ],
    ids=["hardcover", "open-library", "both"],
)
def test_each_link_names_the_providers_that_offer_the_value(
    tmp_path: Path, report: Callable[[], MetadataReport], providers: str
) -> None:
    database = tmp_path / "application.sqlite3"
    cases = _store(
        database, lambda repository: {"case": _publish(repository, report())}
    )
    assert _block(_run(database), "Description") == [
        "Description: 1 to decide, 0 already decided.",
        _link(cases["case"], "description", providers),
    ]


def test_the_limit_caps_each_field(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    cases = _store(database, _measured_store)
    assert _block(_run(database, "--limit", "2"), "Genres") == [
        "Genres: 4 to decide, 0 already decided.",
        _link(cases["accepted"], "genres", "Hardcover"),
        _link(cases["kept"], "genres", "Hardcover"),
        "Showing 2 of 4.",
    ]


def test_it_is_read_only_and_prints_links_and_counts_only(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    _store(database, _measured_store)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    lines = _run(database, "--limit", "3")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    output = "\n".join(lines)
    for secret in (
        TITLE,
        AUTHOR,
        NARRATOR,
        "A tide that keeps time.",
        "Sea Fantasy",
        "Already written.",
        '"primary"',
        "u1",
        str(tmp_path),
    ):
        assert secret not in output
    for line in lines:
        assert (
            line == ""
            or _LINK.match(line)
            or _COUNT.match(line)
            or re.fullmatch(r"Showing [0-9]+ of [0-9]+\.", line)
        ), line


def test_a_missing_or_unreadable_database_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as missing:
        offer_sample.main(["--database", str(tmp_path / "absent.sqlite3")])
    assert missing.value.code == series_sample._MISSING
    foreign = tmp_path / "foreign.sqlite3"
    sqlite3.connect(foreign).close()
    with pytest.raises(SystemExit) as unreadable:
        offer_sample.main(["--database", str(foreign)], io.StringIO())
    assert unreadable.value.code == series_sample._UNREADABLE
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"not a database" * 64)
    with pytest.raises(SystemExit) as corrupt:
        offer_sample.main(["--database", str(garbage)], io.StringIO())
    assert corrupt.value.code == series_sample._UNREADABLE
