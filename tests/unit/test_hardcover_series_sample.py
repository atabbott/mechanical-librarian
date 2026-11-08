"""The Hardcover series sample lists exactly the fields the product holds (#1811).

Every report reaches the store through the real producers and repository, as
the work-route measurement's tests do: Hardcover's and Open Library's answers
are parsed by their adapters, and owner and automatic decisions are recorded
through the repository's public seams. No row is written by hand.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest
import scripts.hardcover_series_sample as sample

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_FROM_SQL,
    _REVIEW_FIELD_ATTENTION_CLEAR_SQL,
    _active_generation_automatic_sql,
    _episode_decision_sql,
)
from mechanical_librarian.adapters.persistence.schema import _SINGLE_SOURCE_CHOICE_SQL
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
    observe_provider,
)
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderClaim,
    ProviderObservation,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.reviewer_decision_summary import correlation_identity
from tests.support.audiobook_reports import publish_audiobook_preview
from tests.support.hardcover_work_route import hardcover_work_report
from tests.support.review_cases import _report as title_report
from tests.support.same_name_series import same_name_series_report

TITLE = "The Lantern Ledger"
AUTHOR = "Target Author"
OWNER = (TargetSeries("Saltmarsh Chronicles", "1"),)
HARDCOVER_SERIES = "Tidewater Cycle"
OPEN_LIBRARY_SERIES = "Glasswork Trilogy"
_HARDCOVER = correlation_identity("provider:hardcover")
_OPEN_LIBRARY_PAYLOAD = (
    Path(__file__).parents[1]
    / "support"
    / "audiobook_provider_payloads"
    / "open_library_original_date.json"
)
_ONE = "Only Hardcover disputes the series of 1 ebook. Choose its series in Library."
_EMPTY = "No ebook has a series that only Hardcover disputes."
_KEPT = "  Automatic kept your series."
_OTHER_ONE = (
    "One source other than Hardcover disputes the series of 1 more ebook. "
    "It is not listed."
)


def _hardcover_ebook(series: str = HARDCOVER_SERIES) -> MetadataReport:
    """An EPUB whose only source is Hardcover's adapter, naming `series`."""
    return same_name_series_report("ebook", name=series, ebook_title=TITLE)


def _open_library(base: MetadataReport, series: str) -> ProviderObservation:
    """Open Library's answer for `base`'s ISBN lane, through the real adapter."""
    responses = json.loads(
        _OPEN_LIBRARY_PAYLOAD.read_text()
        .replace("Synthetic Journey", TITLE)
        .replace("Example Author", AUTHOR)
    )
    responses["/works/OL5W.json"]["series"] = [series]
    responses["/works/OL5W.json"]["series_number"] = ["1"]
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", TITLE, (), fields=base.target.fields),
        base.local_claims,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenLibraryMetadataProvider(
            "open_library",
            client,
            262_144,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
        )
        return observe_provider(
            "open_library", "open_library", provider, plan
        ).observation


def _item(
    base: MetadataReport,
    item: str,
    *,
    series: tuple[TargetSeries, ...] | None = OWNER,
    providers: tuple[ProviderObservation, ...] | None = None,
) -> MetadataReport:
    """`base` as library item `item`, with the owner's series and `providers`."""
    fields = tuple(
        TargetField(field.destination, TargetFieldState.VALUE, series)
        if field.destination is TargetDestination.SERIES and series is not None
        else field
        for field in base.target.fields
    )
    report = MetadataReport.create(
        target=replace(base.target, item=item, fields=fields),
        file=replace(base.file, identity=hashlib.sha256(item.encode()).hexdigest()),
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=tuple(
            sorted(
                base.providers if providers is None else providers,
                key=lambda value: value.provider,
            )
        ),
        audio=base.audio,
    )
    return MetadataReport.from_json(report.to_json())


def _audiobook(item: str) -> MetadataReport:
    """An m4b whose populated series only Hardcover's work candidate contradicts."""
    base = hardcover_work_report()
    providers = tuple(
        replace(
            provider,
            candidates=tuple(
                replace(
                    candidate,
                    claims=candidate.claims
                    + (
                        ProviderClaim.create(
                            provider=provider.provider,
                            candidate=candidate.identity,
                            kind=ClaimKind.SERIES,
                            value=HARDCOVER_SERIES,
                            state=FieldState.VALID,
                            scope=ClaimScope.WORK,
                            applicability="not_applicable",
                            raw_order=90,
                        ),
                    ),
                )
                for candidate in provider.candidates
            ),
        )
        if provider.provider == "hardcover"
        else provider
        for provider in base.providers
    )
    return _item(base, item, providers=providers)


def _series(report: MetadataReport) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    result = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.SERIES
    )
    return (
        result.outcome.value,
        result.reasons,
        tuple(c for a in result.alternatives for c in a.correlations),
    )


def _publish(
    repository: SQLiteMetadataReportRepository,
    report: MetadataReport,
    replacing: MetadataReport | None = None,
) -> str:
    repository.publish(
        report, lambda: True, None if replacing is None else replacing.report_id
    )
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    return cast(
        "str", json.loads(repository.admit_review_case(proposal))["review_case_id"]
    )


def _run(database: Path, *extra: str) -> list[str]:
    stdout = io.StringIO()
    assert sample.main(["--database", str(database), *extra], stdout) == 0
    return stdout.getvalue().splitlines()


def _link(case_id: str, origin: str = "http://localhost:8080") -> str:
    return f"{origin}/library?lens=all&preview={case_id}&field=series"


def _decide(
    repository: SQLiteMetadataReportRepository,
    case_id: str,
    outcome: str,
    key: str,
) -> None:
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "series"
    )
    result = cast("dict[str, object]", proposal["result"])
    alternatives = cast("list[dict[str, object]]", result["alternatives"])
    selected = (
        cast("dict[str, object]", alternatives[0]["value"])
        if outcome == "accept"
        else None
    )
    repository.record_human_decision(
        HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="series",
            actor="owner",
            outcome=outcome,
            selected_value=selected,
            idempotency_key=f"hardcover-series-sample-{key}",
            occurred_at=1_000,
        )
    )


def _seeded_store(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """One store holding the listed field and every control beside it."""
    hardcover = _hardcover_ebook()
    reports = {
        # The Lantern Ledger: Saltmarsh Chronicles; Hardcover alone, Tidewater Cycle.
        "listed": _item(hardcover, "lantern-ledger"),
        "title_only": _item(
            title_report(competing=False), "opening-matter", series=None
        ),
        "audiobook": _audiobook("ember-tide"),
        "two_agree": _item(
            hardcover,
            "two-agree",
            providers=(
                *hardcover.providers,
                _open_library(hardcover, HARDCOVER_SERIES),
            ),
        ),
        "two_differ": _item(
            hardcover,
            "two-differ",
            providers=(
                *hardcover.providers,
                _open_library(hardcover, OPEN_LIBRARY_SERIES),
            ),
        ),
        # Harbor of Glass: Saltmarsh Chronicles; Open Library alone, Glasswork Trilogy.
        "open_library_only": _item(
            hardcover,
            "harbor-of-glass",
            providers=(_open_library(hardcover, OPEN_LIBRARY_SERIES),),
        ),
        "empty_series": _item(hardcover, "empty-series", series=None),
    }
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        cases = {name: _publish(repository, report) for name, report in reports.items()}
    finally:
        repository.close()
    return database, cases


def test_the_fixtures_reach_the_shapes_each_case_claims() -> None:
    """The dry proof: each fixture's series result is the shape its row names."""
    hardcover = _hardcover_ebook()
    open_library = correlation_identity("provider:open_library")
    single = ("insufficient_independent_corroboration",)
    assert _series(_item(hardcover, "a")) == (
        "unresolved_choice",
        single,
        (_HARDCOVER,),
    )
    assert _series(_audiobook("b")) == ("unresolved_choice", single, (_HARDCOVER,))
    assert _series(
        _item(
            hardcover, "c", providers=(_open_library(hardcover, OPEN_LIBRARY_SERIES),)
        )
    ) == ("unresolved_choice", single, (open_library,))
    assert _series(
        _item(
            hardcover,
            "d",
            providers=(
                *hardcover.providers,
                _open_library(hardcover, HARDCOVER_SERIES),
            ),
        )
    ) == ("propose_change", ("unique_typed_candidate",), (_HARDCOVER, open_library))
    assert _series(
        _item(
            hardcover,
            "e",
            providers=(
                *hardcover.providers,
                _open_library(hardcover, OPEN_LIBRARY_SERIES),
            ),
        )
    ) == ("unresolved_choice", ("competing_evidence",), (_HARDCOVER, open_library))
    assert _series(_item(hardcover, "f", series=None)) == (
        "propose_change",
        ("unique_typed_candidate",),
        (_HARDCOVER,),
    )
    title = next(
        value
        for value in evaluate_report(title_report(competing=False)).results
        if value.destination is TargetDestination.TITLE
    )
    assert title.reasons == single


def test_lists_exactly_the_hardcover_only_ebook_series_fields(tmp_path: Path) -> None:
    database, cases = _seeded_store(tmp_path)

    assert _run(database) == [_ONE, _link(cases["listed"]), _OTHER_ONE]


@pytest.mark.parametrize(
    "outcome",
    ("accept", "keep_current", "reject"),
    ids=("accept", "keep_current", "reject"),
)
def test_a_decided_field_drops_out_and_a_withdrawn_one_returns(
    tmp_path: Path, outcome: str
) -> None:
    """Each decision settles the field; withdrawing an accept reopens it.

    A series takes no owner-entered value (`OWNER_TEXT_DESTINATIONS`), and a
    historical reject cannot be withdrawn.
    """
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        case_id = _publish(repository, _item(_hardcover_ebook(), "lantern-ledger"))
        _decide(repository, case_id, outcome, "decide")
    finally:
        repository.close()
    assert _run(database) == [_EMPTY]
    if outcome != "accept":
        return

    repository = open_repository(database, create=False)
    try:
        _decide(repository, case_id, "withdraw", "withdraw")
    finally:
        repository.close()
    assert _run(database) == [_ONE, _link(case_id)]


@pytest.mark.parametrize(
    ("option", "kept"),
    (
        ("primary_narrative", True),
        ("source_order", True),
        ("all_qualified", True),
        ("add_qualified", False),
        ("replace_qualified", False),
    ),
    ids=(
        "primary_narrative",
        "source_order",
        "all_qualified",
        "add_qualified",
        "replace_qualified",
    ),
)
def test_a_field_automatic_kept_is_listed_and_marked_until_the_owner_decides(
    tmp_path: Path, option: str, kept: bool
) -> None:
    """Automatic keeping the owner's series leaves the label to the owner.

    Under qualified Automatic the series options that decide this held field
    keep the current series; the others record nothing, and none takes
    Hardcover's value. An owner decision afterwards settles the field.
    """
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        case_id, _ = publish_audiobook_preview(
            repository,
            _item(_hardcover_ebook(), "lantern-ledger"),
            ("series",),
            selected_options={"series": option},
        )
    finally:
        repository.close()
    before = _run(database)
    assert before == [_ONE, _link(case_id)]
    assert not before[1].endswith(_KEPT)

    repository = open_repository(database, create=False)
    try:
        repository.persist_shadow_outcome(case_id, 4)
        decisions = [
            json.loads(value) for value in repository.get_review_case_decisions(case_id)
        ]
    finally:
        repository.close()
    assert [(value["destination"], value["outcome"]) for value in decisions] == (
        [("series", "keep_current")] if kept else []
    )
    assert _run(database) == [_ONE, _link(case_id) + (_KEPT if kept else "")]

    repository = open_repository(database, create=False)
    try:
        _decide(repository, case_id, "keep_current", "after-automatic")
    finally:
        repository.close()
    assert _run(database) == [_EMPTY]


@pytest.mark.parametrize("scope", ("destination", "connection"))
def test_a_suspended_field_stays_dropped(tmp_path: Path, scope: str) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        case_id = _publish(repository, _item(_hardcover_ebook(), "lantern-ledger"))
    finally:
        repository.close()
    assert _run(database) == [_ONE, _link(case_id)]

    repository = open_repository(database, create=False)
    try:
        repository.suspend_field_effect_capability(
            scope=scope,
            connection="primary",
            destination="series",
            reason="field_unavailable",
            changed_at=1_000,
        )
    finally:
        repository.close()
    assert _run(database) == [_EMPTY]


@pytest.mark.parametrize(
    "successor_qualifies", (False, True), ids=("superseded", "successor")
)
def test_only_the_current_case_is_listed(
    tmp_path: Path, successor_qualifies: bool
) -> None:
    listed = _item(_hardcover_ebook(), "lantern-ledger")
    agreeing = _item(_hardcover_ebook(OWNER[0].name), "lantern-ledger")
    first, second = (agreeing, listed) if successor_qualifies else (listed, agreeing)
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        first_case = _publish(repository, first)
        second_case = _publish(repository, second, first)
    finally:
        repository.close()
    assert first_case != second_case

    if successor_qualifies:
        assert _run(database) == [_ONE, _link(second_case)]
    else:
        assert _run(database) == [_EMPTY]


def test_a_series_asked_about_by_policy_is_still_listed(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        case_id, _ = publish_audiobook_preview(
            repository,
            _item(_hardcover_ebook(), "lantern-ledger"),
            ("isbn_form",),
            selected_destinations={"series": "ask"},
        )
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
    finally:
        repository.close()

    assert "series" in {item["destination"] for item in attention["items"]}
    assert _run(database) == [_ONE, _link(case_id)]


def test_orders_oldest_first_and_caps_with_the_full_count(tmp_path: Path) -> None:
    hardcover = _hardcover_ebook()
    reports = [
        _item(hardcover, item) for item in ("first-tide", "second-tide", "third-tide")
    ]
    # Admitted in descending identifier order, so neither identifier order nor
    # newest first can pass for oldest first.
    reports.sort(
        key=lambda value: (
            ReviewCase.create(evaluate_report(value), value).review_case_id
        ),
        reverse=True,
    )
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    try:
        admitted = [_publish(repository, report) for report in reports]
    finally:
        repository.close()
    assert admitted == sorted(admitted, reverse=True)
    header = (
        "Only Hardcover disputes the series of 3 ebooks. Choose each series in Library."
    )

    assert _run(database) == [header, *map(_link, admitted)]
    assert _run(database, "--limit", "2") == [
        header,
        *map(_link, admitted[:2]),
        "Showing the oldest 2 of 3.",
    ]


def test_an_empty_store_prints_the_empty_line(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    stdout = io.StringIO()

    assert sample.main(["--database", str(database)], stdout) == 0
    assert stdout.getvalue() == _EMPTY + "\n"


def test_main_is_read_only_and_prints_links_and_counts_only(tmp_path: Path) -> None:
    database, cases = _seeded_store(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    siblings = sorted(path.name for path in tmp_path.iterdir())

    outputs = [
        _run(database),
        _run(database, "--limit", "1"),
        _run(database, "--origin", "https://library.home.example/"),
    ]

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == siblings
    connection = sample._open_read_only(database)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE probe(value)")
    finally:
        connection.close()
    assert outputs[2][1] == _link(cases["listed"], "https://library.home.example")
    link = re.compile(
        r"^(http://localhost:8080|https://library\.home\.example)/library\?lens=all"
        r"&preview=review_case_[0-9a-f]{64}&field=series$"
    )
    # No field here is Automatic's, so no line carries the marker or a trailing
    # space.
    for lines in outputs:
        text = "\n".join(lines)
        for secret in (
            TITLE,
            AUTHOR,
            "Saltmarsh",
            "Tidewater",
            "Glasswork",
            "Ember Tide",
            "lantern-ledger",
            "harbor-of-glass",
            "ebooks",
            "primary",
            "report_",
            "correlation_",
            str(tmp_path),
        ):
            assert secret not in text
        for line in lines:
            assert line in {_ONE, _OTHER_ONE} or link.fullmatch(line), line
            assert not line.endswith((" ", _KEPT.strip())), line


def test_the_read_only_uri_encodes_filename_syntax(tmp_path: Path) -> None:
    (tmp_path / "seed").mkdir(mode=0o700)
    seeded, cases = _seeded_store(tmp_path / "seed")
    database = tmp_path / "application#copy.sqlite3"
    shutil.copyfile(seeded, database)

    assert _run(database) == [_ONE, _link(cases["listed"]), _OTHER_ONE]
    assert not (tmp_path / "application").exists()


def test_main_refuses_a_missing_or_unreadable_store(tmp_path: Path) -> None:
    absent = tmp_path / "absent.sqlite3"
    empty = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty).close()
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"not a database" * 100)
    expected = {
        absent: "No file at the --database path. Give it a copy of application.sqlite3.",
        empty: (
            "This checkout cannot read that database. "
            "Copy it from an install built from this checkout."
        ),
        garbage: (
            "This checkout cannot read that database. "
            "Copy it from an install built from this checkout."
        ),
    }
    for path, message in expected.items():
        stdout = io.StringIO()
        with pytest.raises(SystemExit) as refused:
            sample.main(["--database", str(path)], stdout)
        assert refused.value.code == message
        assert str(tmp_path) not in message
        assert stdout.getvalue() == ""


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    (
        ("--origin", "ftp://x", "give the address as http://host:port or https://host"),
        (
            "--origin",
            "http://host/path",
            "give the address as http://host:port or https://host",
        ),
        (
            "--origin",
            "http://host?x=1",
            "give the address as http://host:port or https://host",
        ),
        (
            "--origin",
            "http://host:port",
            "give the address as http://host:port or https://host",
        ),
        ("--limit", "0", "give a whole number from 1 to 500"),
        ("--limit", "501", "give a whole number from 1 to 500"),
        ("--limit", "ten", "give a whole number from 1 to 500"),
    ),
    ids=(
        "ftp",
        "path",
        "query",
        "port_name",
        "limit_zero",
        "limit_501",
        "limit_word",
    ),
)
def test_flags_refuse_a_bad_origin_or_limit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
    message: str,
) -> None:
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    with pytest.raises(SystemExit) as refused:
        sample.main(["--database", str(database), flag, value], io.StringIO())

    assert refused.value.code == 2
    assert f"argument {flag}: {message}" in capsys.readouterr().err


def test_the_statement_carries_the_product_fragments_verbatim() -> None:
    """The exclusion is the clear fragment's owner and suspension clauses.

    Both are sliced from `_REVIEW_FIELD_ATTENTION_CLEAR_SQL` unchanged and
    together are that fragment less its description, automatic and cover
    clauses, so a field Automatic decided is never excluded.
    """
    owner = sample._OWNER_DECISION_CLEAR_SQL
    suspension = sample._SUSPENSION_CLEAR_SQL
    for fragment in (
        _CURRENT_REVIEW_FROM_SQL,
        _SINGLE_SOURCE_CHOICE_SQL,
        owner + suspension,
        _active_generation_automatic_sql("automatic"),
    ):
        assert sample._FIELDS_SQL.count(fragment) == 1
    assert _REVIEW_FIELD_ATTENTION_CLEAR_SQL.startswith(owner)
    assert _REVIEW_FIELD_ATTENTION_CLEAR_SQL.endswith(suspension)
    assert _episode_decision_sql("decision") in owner
    assert "automatic_decisions" not in owner + suspension
    assert _REVIEW_FIELD_ATTENTION_CLEAR_SQL not in sample._FIELDS_SQL
