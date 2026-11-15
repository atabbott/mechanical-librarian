"""The relaxation label finder (ADR-0092 Decision 5).

Synthetic stores carry a sentinel in every free-text slot and replay through
an injected reader, as the A6 tests do; two tests replay a product-built store
through the real child.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
import scripts.admission_rule_a6 as a6
import scripts.admission_rule_ledger as ledger
import scripts.hardcover_series_sample as series_sample
import scripts.relaxation_label_sample as sample

from mechanical_librarian.domain import shadow_decision
from tests.unit.test_admission_rule_a6 import (
    SCALAR,
    admitting_fixture,
    product_store,
    replay_all,
    title_value,
)
from tests.unit.test_admission_rule_ledger import (
    _SENTINEL,
    EBOOK_REVISION,
    Fixture,
    Sentinels,
    _arguments,
    _database,
    _no_series,
)

VERSION = shadow_decision.DECISION_POLICY_VERSION
TITLES = "Titles with two agreeing sources and no match in the book file"
_LINK = re.compile(
    r"^http://localhost:8080/library\?lens=all&preview=(?P<case>[^&\s]+)"
    r"&field=(?P<field>[A-Za-z]+)$"
)


def _store(
    path: Path,
    fixtures: Sequence[Fixture],
    *,
    version: str | None = VERSION,
    owner_tables: bool = False,
) -> Path:
    """The fixtures as a snapshot whose decisions name ``version``."""
    _database(path, "v26-like", fixtures, owner_tables=owner_tables)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE shadow_decision_outcomes SET document="
        "json_set(document,'$.decision_policy_version',?)",
        (version,),
    )
    connection.commit()
    connection.close()
    return path


def _run(database: Path, *extra: str, replay: a6.A6Reader | None = None) -> list[str]:
    stdout = io.StringIO()
    code = sample.main(
        ["--database", str(database), *extra],
        stdout,
        replay or replay_all(database, version=VERSION),
    )
    assert code == 0
    return stdout.getvalue().splitlines()


def _cases(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT review_case_id FROM review_cases "
        "WHERE review_case_id<>'stale-case' ORDER BY review_case_id"
    ).fetchall()
    connection.close()
    return [row[0] for row in rows]


def _link(case_id: str, field: str = "title") -> str:
    return f"http://localhost:8080/library?lens=all&preview={case_id}&field={field}"


def _block(lines: list[str], name: str) -> list[str]:
    """One relaxation's header and the lines under it."""
    start = next(index for index, line in enumerate(lines) if line.startswith(name))
    block = lines[start:]
    return block[: block.index("")] if "" in block else block


def test_lists_each_admitted_undecided_field_under_its_relaxation(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    database = _store(
        tmp_path / "copy.sqlite3", [admitting_fixture(text), admitting_fixture(text)]
    )
    first, second = _cases(database)
    assert _run(database) == [
        "Authors with one agreeing source: 0 to decide, 0 already decided.",
        "",
        f"{TITLES}: 2 to decide, 0 already decided.",
        _link(first),
        _link(second),
        "",
        "Publishers with two agreeing sources and no match in the book file: "
        "0 to decide, 0 already decided.",
        "",
        "Languages with two agreeing sources and no match in the book file: "
        "0 to decide, 0 already decided.",
        "",
        "Publication dates to the day with one agreeing source: "
        "0 to decide, 0 already decided.",
    ]


def test_the_names_cover_every_relaxation() -> None:
    assert list(sample.NAMES) == [rule.rule for rule in a6.RELAXATIONS]


def test_a_decided_field_is_counted_not_listed_even_after_a_rescan(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    earlier = admitting_fixture(text)
    earlier.current = False
    earlier.decide(1, "title", "accept", title_value(earlier))
    # The rescanned item: a new current case nobody decided yet.
    successor = admitting_fixture(text)
    successor.item = earlier.item
    kept = admitting_fixture(text)
    kept.decide(2, "title", "keep_current")
    open_field = admitting_fixture(text)
    database = _store(tmp_path / "copy.sqlite3", [earlier, successor, kept, open_field])
    cases = _cases(database)
    assert _block(_run(database), TITLES) == [
        f"{TITLES}: 1 to decide, 2 already decided.",
        _link(cases[3]),
    ]


def test_a_withdrawn_decision_lists_the_field_again(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = admitting_fixture(text)
    fixture.decide(1, "title", "accept", title_value(fixture))
    fixture.decide(2, "title", "withdraw")
    database = _store(tmp_path / "copy.sqlite3", [fixture])
    (case_id,) = _cases(database)
    assert _block(_run(database), TITLES) == [
        f"{TITLES}: 1 to decide, 0 already decided.",
        _link(case_id),
    ]


def test_the_finder_and_the_reading_agree_on_one_store(tmp_path: Path) -> None:
    text = Sentinels()
    undecided = admitting_fixture(text)
    accepted = admitting_fixture(text, alternatives=2)
    accepted.decide(1, "title", "accept", title_value(accepted, 1))
    earlier = admitting_fixture(text)
    earlier.current = False
    earlier.decide(2, "title", "owner_entered", title_value(earlier))
    successor = admitting_fixture(text)
    successor.item = earlier.item
    database = _store(
        tmp_path / "copy.sqlite3", [undecided, accepted, earlier, successor]
    )
    lines = _run(database)
    stdout = io.StringIO()
    assert (
        ledger.main(
            [
                *_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
                "--a6-abstention",
            ],
            stdout=stdout,
            stderr=io.StringIO(),
            ancestry=lambda _commit, _revision: True,
            attention=lambda _root, _db: ledger.AttentionProbe((), 0, 0, SCALAR, True),
            series_replay=_no_series,
            decision_replay=replay_all(database, version=VERSION),
        )
        == 0
    )
    section = json.loads(stdout.getvalue())["a6_abstention"]
    for rule in a6.RELAXATIONS:
        block = _block(lines, sample.NAMES[rule.rule])
        listed = [line for line in block[1:] if _LINK.match(line)]
        pooled = next(
            row for row in section["pooled_relaxations"] if row["rule"] == rule.rule
        )
        owner = next(
            row["owner"]
            for row in section["pooled_owner_labels"]
            if row["rule"] == rule.rule
        )
        # "Already decided" is the reading's pooled label count.
        assert block[0].endswith(f", {owner['labelled']} already decided.")
        if rule.rule != "title_two_correlations_without_local":
            assert (listed, pooled["outcomes"]["admitted"]) == ([], 0)
            continue
        # The reading admits three current fields; two are on items the owner
        # decided (one on the current case, one before the rescan).
        assert (pooled["outcomes"]["admitted"], owner["labelled"]) == (3, 2)
        assert listed == [_link(_cases(database)[0])]


def test_the_limit_caps_each_relaxation(tmp_path: Path) -> None:
    text = Sentinels()
    database = _store(
        tmp_path / "copy.sqlite3", [admitting_fixture(text) for _ in range(3)]
    )
    cases = _cases(database)
    assert _block(_run(database, "--limit", "2"), TITLES) == [
        f"{TITLES}: 3 to decide, 0 already decided.",
        _link(cases[0]),
        _link(cases[1]),
        "Showing 2 of 3.",
    ]


@pytest.mark.parametrize(
    ("failed", "line"),
    [
        (1, "1 work could not be checked and is left out."),
        (2, "2 works could not be checked and are left out."),
    ],
    ids=["one-work", "two-works"],
)
def test_works_the_replay_could_not_check_are_counted(
    tmp_path: Path, failed: int, line: str
) -> None:
    text = Sentinels()
    database = _store(tmp_path / "copy.sqlite3", [admitting_fixture(text)])
    lines = _run(database, replay=replay_all(database, version=VERSION, failed=failed))
    assert lines[-2:] == ["", line]


def _refusing_replay(_root: Path, _database: Path) -> a6.A6Probe:
    raise ledger.Refusal("decision replay failed")


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("missing", series_sample._MISSING),
        ("write-ahead-log", sample._WAL),
        ("not-a-database", series_sample._UNREADABLE),
        ("another-version", sample._ENGINE),
        ("no-version", sample._ENGINE),
        ("unmirrored-checkout", sample._MIRROR),
        ("replay-refused", series_sample._UNREADABLE),
        ("replay-of-another-version", series_sample._UNREADABLE),
    ],
    ids=[
        "missing",
        "write-ahead-log",
        "not-a-database",
        "another-version",
        "no-version",
        "unmirrored-checkout",
        "replay-refused",
        "replay-of-another-version",
    ],
)
def test_refusals_are_fixed_lines_in_a_fixed_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str, message: str
) -> None:
    """Each fault is refused even when every later fault is present too."""
    order = [
        "missing",
        "write-ahead-log",
        "not-a-database",
        "another-version",
        "unmirrored-checkout",
        "replay-refused",
        "replay-of-another-version",
    ]
    rank = order.index("another-version" if fault == "no-version" else fault)

    def present(name: str) -> bool:
        return order.index(name) >= rank

    text = Sentinels()
    database = tmp_path / "copy.sqlite3"
    if fault == "not-a-database":
        database.write_bytes(_SENTINEL.encode() * 64)
    elif fault != "missing":
        version: str | None = VERSION
        if present("another-version"):
            version = None if fault == "no-version" else VERSION + "-other"
        _store(database, [admitting_fixture(text)], version=version)
    if present("write-ahead-log") and fault != "missing":
        database.with_name(database.name + "-wal").write_bytes(b"pending")
    if present("unmirrored-checkout"):
        monkeypatch.setattr(sample.a6, "table_for", lambda _version: None)
    replay = (
        _refusing_replay
        if present("replay-refused") and fault != "replay-of-another-version"
        else replay_all(database, version=VERSION + "-other")
    )
    stdout = io.StringIO()
    with pytest.raises(SystemExit) as refused:
        sample.main(["--database", str(database)], stdout, replay)
    assert refused.value.code == message
    assert stdout.getvalue() == ""


def test_it_is_read_only_and_prints_links_and_counts_only(tmp_path: Path) -> None:
    text = Sentinels()
    decided = admitting_fixture(text)
    decided.decide(1, "title", "accept", title_value(decided))
    database = _store(tmp_path / "copy.sqlite3", [admitting_fixture(text), decided])
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    lines = _run(database, replay=replay_all(database, version=VERSION, failed=1))
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not database.with_name(database.name + "-wal").exists()
    cases = set(_cases(database))
    names = "|".join(re.escape(name) for name in sample.NAMES.values())
    count = re.compile(rf"^({names}): [0-9]+ to decide, [0-9]+ already decided\.$")
    for line in lines:
        link = _LINK.match(line)
        if link is not None:
            assert link["case"] in cases and link["field"] == "title"
            continue
        assert _SENTINEL not in line and str(tmp_path) not in line
        assert (
            line == ""
            or count.match(line)
            or line == "1 work could not be checked and is left out."
        ), line


def test_the_real_child_reads_a_product_store_of_this_version(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    (case_id,) = product_store(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    stdout = io.StringIO()
    assert sample.main(["--database", str(database)], stdout) == 0
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    lines = stdout.getvalue().splitlines()
    assert _block(lines, TITLES) == [
        f"{TITLES}: 1 to decide, 0 already decided.",
        _link(case_id),
    ]
    assert "Vexmorin" not in stdout.getvalue()
    decided = tmp_path / "decided" / "application.sqlite3"
    decided.parent.mkdir(mode=0o700)
    product_store(decided, decide="accept")
    stdout = io.StringIO()
    assert sample.main(["--database", str(decided)], stdout) == 0
    assert _block(stdout.getvalue().splitlines(), TITLES) == [
        f"{TITLES}: 0 to decide, 1 already decided."
    ]
    # Negative control: the same store, its decisions stamped by another
    # decision-policy version, is refused before any replay.
    foreign = tmp_path / "foreign.sqlite3"
    shutil.copyfile(database, foreign)
    connection = sqlite3.connect(foreign)
    # The copy only: lift the append-only guard and the version check.
    for (trigger,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='shadow_decision_outcomes'"
    ).fetchall():
        connection.execute(f'DROP TRIGGER "{trigger}"')
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(
        "UPDATE shadow_decision_outcomes SET document="
        "json_set(document,'$.decision_policy_version',?)",
        (VERSION + "-other",),
    )
    connection.commit()
    connection.close()
    with pytest.raises(SystemExit) as refused:
        sample.main(["--database", str(foreign)], io.StringIO())
    assert refused.value.code == sample._ENGINE


_ORIGIN = "give the address as http://host:port or https://host"
_LIMIT = "give a whole number from 1 to 500"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--origin", "ftp://localhost"], _ORIGIN),
        (["--origin", "http://localhost/library"], _ORIGIN),
        (["--limit", "0"], _LIMIT),
        (["--limit", "501"], _LIMIT),
        (["--limit", "many"], _LIMIT),
    ],
    ids=[
        "not-http",
        "with-a-path",
        "limit-zero",
        "limit-too-large",
        "limit-not-a-number",
    ],
)
def test_a_bad_origin_or_limit_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    with pytest.raises(SystemExit) as refused:
        sample.main(["--database", str(tmp_path / "copy.sqlite3"), *arguments])
    assert refused.value.code == 2
    assert message in capsys.readouterr().err
