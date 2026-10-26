"""Library and Attention statements read less per request (#1202): equality.

#1202 changed two statements without changing what either returns. A work's
attention probe became two probes, its non-cover fields first and its cover
field second, so the cover authority's consideration document and currentness
function are read only for a work no other field already put in attention. The
Attention page filters its candidate fields by the field predicate instead of
projecting the predicate as a column and filtering that afterwards. The
`_PRE_1202_*` texts below are the work probe, the count statement and the
Attention page statement exactly as they read before #1202, kept verbatim; the
Library page statement changed only through the probe, so its oracle is the
current reader run with the old probe. #1232 then read the probe, the count
and the Attention candidates from persisted attention facts; the oracles here
stayed the live statements, so they also prove that change returns the same. Every equality test here compares the
old statements with the current ones on the same database across the
authority, fetching, decision, selection, suspension, safety and policy states
that decide a field's attention.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from itertools import repeat
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import review_cases, review_sql
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _AUTOMATIC_SAFETY_ATTENTION_SQL,
    _CURRENT_COVER_ATTENTION_AUTHORITY_SQL,
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_FROM_SQL,
    _EPUB_INAPPLICABLE_ATTENTION_SQL,
    _POLICY_ASK_ATTENTION_SQL,
    _REPORT_BROWSE_FACTS_JOIN_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
    _REVIEW_SEARCH_SQL,
    _browse_scalar_sql,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    CoverReadObservation,
    LibraryLensFilter,
)
from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
)
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.unit.test_report_browse_facts import _resolve_policies

# --- Equality oracle: the statements as they read before #1202 (verbatim). ---

_PRE_1202_REVIEW_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM review_case_field_facts result WHERE "
    "result.review_case_id=rc.review_case_id AND "
    + _REVIEW_FIELD_ATTENTION_SQL
    + _EPUB_INAPPLICABLE_ATTENTION_SQL
    + ")"
)
_PRE_1202_CURRENT_REVIEW_CASE_COUNT_SQL = (
    "SELECT count(*),COALESCE(sum(CASE WHEN "
    + _browse_scalar_sql(_PRE_1202_REVIEW_ATTENTION_SQL)
    + " THEN 1 ELSE 0 END),0) "
    + _CURRENT_LIBRARY_FROM_SQL
    + _REPORT_BROWSE_FACTS_JOIN_SQL
    + "WHERE COALESCE(c.outcome,'')<>'tombstoned'"
    + _REVIEW_SEARCH_SQL
)
_PRE_1202_ATTENTION_SQL = (
    "WITH search_matches AS MATERIALIZED (SELECT "
    "c.connection_name,c.library_name,c.item_id "
    + _CURRENT_REVIEW_FROM_SQL
    + _REPORT_BROWSE_FACTS_JOIN_SQL
    + "WHERE 1"
    + _REVIEW_SEARCH_SQL
    + "),candidates AS MATERIALIZED (SELECT rc.review_case_id AS review_case_id,"
    "mr.report_id AS report_id,CASE WHEN "
    "rc.proposal_set_id=c.proposal_set_id AND rc.report_id=c.report_id "
    "THEN rc.proposal_set_id ELSE NULL END AS proposal_set_id,"
    "result.destination AS destination,"
    "CASE WHEN "
    + _browse_scalar_sql(_REVIEW_FIELD_ATTENTION_SQL)
    + _browse_scalar_sql(_EPUB_INAPPLICABLE_ATTENTION_SQL)
    + " THEN 1 ELSE 0 END AS attention,"
    "facts.title_key "
    "AS title_order,c.library_name AS library_name,c.item_id AS item_id "
    + _CURRENT_REVIEW_FROM_SQL
    + _REPORT_BROWSE_FACTS_JOIN_SQL
    + "JOIN review_case_field_facts result "
    "ON result.review_case_id=rc.review_case_id "
    "WHERE (c.connection_name,c.library_name,c.item_id) IN "
    "(SELECT connection_name,library_name,item_id FROM search_matches)"
    + "),attentive AS MATERIALIZED (SELECT review_case_id,report_id,"
    "proposal_set_id,destination,count(*) OVER (PARTITION BY review_case_id) "
    "AS attention_count,title_order,library_name,item_id "
    "FROM candidates WHERE attention=1),totals AS (SELECT count(*) AS total "
    "FROM attentive),page AS MATERIALIZED (SELECT 1 AS present,* "
    "FROM attentive" + " ORDER BY "
    "title_order,library_name,item_id,destination,review_case_id "
    "LIMIT ? OFFSET ?) "
    "SELECT rc.document,mr.document,page.proposal_set_id,page.destination,"
    "page.attention_count,"
    "(SELECT outcome.disposition FROM shadow_decision_outcomes shadow "
    "JOIN shadow_outcome_field_facts outcome "
    "ON outcome.outcome_set_id=shadow.outcome_set_id "
    "WHERE outcome.review_case_id=rc.review_case_id "
    "AND outcome.destination=result.destination "
    "ORDER BY shadow.evaluated_at DESC,shadow.outcome_set_id DESC LIMIT 1) "
    "AS disposition,"
    "(CASE WHEN "
    + _AUTOMATIC_SAFETY_ATTENTION_SQL
    + " THEN 'automatic_safety_suspended' ELSE NULL END) AS cause,"
    "CASE WHEN " + _POLICY_ASK_ATTENTION_SQL + " THEN 1 ELSE 0 END AS policy_attention,"
    "COALESCE(page.present,0),totals.total FROM totals LEFT JOIN page ON 1 "
    "LEFT JOIN review_cases rc ON rc.review_case_id=page.review_case_id "
    "LEFT JOIN metadata_reports mr ON mr.report_id=page.report_id "
    "LEFT JOIN review_case_field_facts result "
    "ON result.review_case_id=page.review_case_id AND result.destination=page.destination "
    "ORDER BY page.title_order,page.library_name,page.item_id,"
    "page.destination,page.review_case_id"
)

_LABELS = (("primary", "books-1", "Owner room books-1"),)
_SEARCHES = (None, "work-000", "books-2", "deliberately", "absent")
_LENSES: tuple[LibraryLensFilter | None, ...] = (
    None,
    "needs_attention",
    "no_attention_needed",
    "not_matched",
)
_PAGES = ((50, 0), (7, 7), (5, 28))


def _search_parameters(search: str | None) -> tuple[object, ...]:
    if search is None:
        query, pattern = None, "%"
    else:
        escaped = (
            search.casefold()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        query, pattern = search, f"%{escaped}%"
    return (query, *(pattern,) * 5, json.dumps(_LABELS), pattern)


@contextmanager
def _pre_1202_library(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the Library page and counts with the single attention probe."""
    with monkeypatch.context() as patch:
        # #1232 reads the work probe from persisted facts; the oracle is
        # still the single live probe as the page read it before #1202.
        patch.setattr(
            review_cases,
            "_LIBRARY_ATTENTION_SQL",
            _browse_scalar_sql(_PRE_1202_REVIEW_ATTENTION_SQL),
        )
        patch.setattr(
            review_cases,
            "_CURRENT_REVIEW_CASE_COUNT_SQL",
            _PRE_1202_CURRENT_REVIEW_CASE_COUNT_SQL,
        )
        yield


def _oracle_attention(
    repository: SQLiteMetadataReportRepository,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[tuple[object, ...], int]:
    rows = repository._connection.execute(
        _PRE_1202_ATTENTION_SQL,
        (
            *_search_parameters(search),
            *repository._cover_read_authority(),
            limit,
            offset,
        ),
    ).fetchall()
    return (
        tuple(
            (row[0], row[1], row[3], row[4], row[5], row[6], bool(row[7]))
            for row in rows
            if row[8] == 1
        ),
        rows[0][9],
    )


def _library(
    repository: SQLiteMetadataReportRepository,
) -> tuple[object, ...]:
    """Every Library page, lens and count this state answers."""
    values: list[object] = []
    for search in _SEARCHES:
        values.append(repository.count_current_review_cases(search, _LABELS))
        for limit, offset in _PAGES:
            for lens in _LENSES:
                page = repository.read_current_library_page(
                    search, limit, offset, lens, _LABELS
                )
                values.append(
                    (
                        page.rows,
                        page.total,
                        page.attention_total,
                        page.not_matched_total,
                        page.match_reasons,
                    )
                )
    return tuple(values)


def _assert_equal_to_oracle(
    repository: SQLiteMetadataReportRepository, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    """Compare every page and count with the pre-#1202 statements."""
    current = _library(repository)
    with _pre_1202_library(monkeypatch):
        assert _library(repository) == current
    for search in _SEARCHES:
        for limit, offset in _PAGES:
            page = repository.read_current_attention_page(
                search, limit, offset, _LABELS
            )
            assert (page.rows, page.total) == _oracle_attention(
                repository, search, limit, offset
            ), (search, limit, offset)
        assert (
            repository.count_current_attention_fields(search, _LABELS)
            == (_oracle_attention(repository, search, 1, 0)[1])
        ), search
    rows = repository._connection.execute(
        _PRE_1202_ATTENTION_SQL,
        (*_search_parameters(None), *repository._cover_read_authority(), 10_000, 0),
    ).fetchall()
    cases = {
        document: case
        for case, document in repository._connection.execute(
            "SELECT review_case_id,document FROM review_cases"
        )
    }
    for destination in (None, "cover", "title", "description"):
        # The cases a field-effect suspension tells the owner about.
        assert repository._field_effects._current_attention_review_case_ids(
            "primary", destination
        ) == tuple(
            sorted(
                {
                    cases[row[0]]
                    for row in rows
                    if row[8] == 1 and destination in (None, row[3])
                }
            )
        ), destination
    library = repository.read_current_library_page(None, 1, 0, None, _LABELS)
    attention = repository.read_current_attention_page(None, 1, 0, _LABELS)
    return library.attention_total, attention.total


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _case_ids(repository: SQLiteMetadataReportRepository) -> list[str]:
    return [
        row[0]
        for row in repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages "
            "WHERE review_case_id IS NOT NULL ORDER BY item_id"
        )
    ]


def _human_decision(
    repository: SQLiteMetadataReportRepository, case_id: str, destination: str
) -> None:
    key = _hex(f"human:{case_id}:{destination}")
    repository._connection.execute(
        "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,"
        "target_fingerprint,destination,actor,outcome,selected_value_document,"
        "idempotency_digest,request_digest,occurred_at,document) "
        "VALUES(?,?,?,?,?,'owner','accept','null',?,?,1,'{}')",
        (
            f"human_decision_{key}",
            case_id,
            f"review_proposal_{key}",
            key,
            destination,
            key,
            key,
        ),
    )


def _automatic_decision(
    repository: SQLiteMetadataReportRepository, case_id: str, destination: str
) -> None:
    key = _hex(f"automatic:{case_id}:{destination}")
    repository._connection.execute(
        "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
        "idempotency_digest,occurred_at,document) VALUES(?,?,?,?,1,'{}')",
        (f"automatic_decision_{key}", case_id, destination, key),
    )


def _consideration(
    repository: SQLiteMetadataReportRepository, case_id: str
) -> tuple[str, dict[str, object]]:
    consideration_id, currentness = repository._connection.execute(
        "SELECT consideration.consideration_id,consideration.currentness_document "
        "FROM current_cover_considerations current_cover JOIN cover_considerations "
        "consideration USING(consideration_id) WHERE current_cover.review_case_id=?",
        (case_id,),
    ).fetchone()
    return consideration_id, json.loads(currentness)


def _cover_selection(
    repository: SQLiteMetadataReportRepository,
    case_id: str,
    kind: str,
    consideration_case_id: str,
) -> None:
    """Select a cover for the case's work, bound to another case's consideration."""
    consideration_id, _ = _consideration(repository, consideration_case_id)
    _, target = _consideration(repository, case_id)
    key = _hex(f"selection:{case_id}:{kind}")
    document = (
        json.dumps({"schema_version": "cover-selection-authority-v2"})
        if kind == "owner"
        else json.dumps(
            {
                "decision_document": json.dumps(
                    {"schema_version": "cover-automatic-decision-v3"}
                )
            }
        )
    )
    decision = (
        f"human_decision_{key}" if kind == "owner" else f"automatic_decision_{key}"
    )
    target_key = (target["connection"], target["library"], target["item"])
    repository._connection.execute(
        "INSERT INTO cover_selection_authorities(selection_id,connection_name,"
        "library_name,item_id,kind,decision_id,consideration_id,"
        "supersedes_selection_id,occurred_at,document) "
        "VALUES(?,?,?,?,?,?,?,NULL,1,?)",
        (
            f"cover_selection_{key}",
            *target_key,
            kind,
            decision,
            consideration_id,
            document,
        ),
    )
    repository._connection.execute(
        "INSERT INTO current_cover_selection_authorities"
        "(connection_name,library_name,item_id,selection_id) VALUES(?,?,?,?)",
        (*target_key, f"cover_selection_{key}"),
    )


def _shadow_outcome(
    repository: SQLiteMetadataReportRepository,
    case_id: str,
    outcomes: tuple[tuple[str, str, str], ...],
) -> None:
    """Project shadow outcome facts: (destination, disposition, policy)."""
    report_id, proposal_set_id = repository._connection.execute(
        "SELECT report_id,proposal_set_id FROM review_cases WHERE review_case_id=?",
        (case_id,),
    ).fetchone()
    key = _hex(f"shadow:{case_id}")
    repository._connection.execute(
        "INSERT INTO shadow_decision_outcomes(outcome_set_id,review_case_id,"
        "report_id,proposal_set_id,resolved_policy_id,source_decision_set_id,"
        "evaluated_at,document) VALUES(?,?,?,?,?,'synthetic',1,?)",
        (
            f"shadow_outcome_set_{key}",
            case_id,
            report_id,
            proposal_set_id,
            f"resolved_policy_{key}",
            json.dumps(
                {
                    "schema_version": "shadow-outcome-set-v10",
                    "decision_policy_version": DECISION_POLICY_VERSION,
                    "decision_policy_sha256": DECISION_POLICY_SHA256,
                    "outcomes": [
                        {
                            "destination": destination,
                            "disposition": disposition,
                            "policy_destination": policy,
                            "policy_category": destination,
                        }
                        for destination, disposition, policy in outcomes
                    ],
                }
            ),
        ),
    )


def test_split_cover_probe_rests_on_the_authority_admitting_every_other_field() -> None:
    # The non-cover probe omits the cover authority because the authority's
    # first disjunct admits every non-cover field; the split is exact only
    # while that holds.
    assert _CURRENT_COVER_ATTENTION_AUTHORITY_SQL.startswith(
        "(result.destination<>'cover' OR "
    )
    assert review_sql._REVIEW_FIELD_ATTENTION_SQL == (
        review_sql._REVIEW_FIELD_ATTENTION_REASON_SQL
        + " AND "
        + _CURRENT_COVER_ATTENTION_AUTHORITY_SQL
        + review_sql._REVIEW_FIELD_ATTENTION_CLEAR_SQL
    )
    # The bound authority parameters appear once, in the cover probe only.
    assert review_sql._REVIEW_ATTENTION_SQL.count("?") == (
        _PRE_1202_REVIEW_ATTENTION_SQL.count("?")
    )


def test_library_and_attention_equal_the_pre_1202_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 30, mixed=True)
    try:
        repository = fixture.repository
        connection = repository._connection
        policy, replay = repository._cover_read_authority()
        assert policy is not None and replay is not None
        cases = _case_ids(repository)
        assert len(cases) == 29
        identities = [
            (
                str(value["connection"]),
                str(value["library"]),
                str(value["item"]),
                str(value["report_id"]),
            )
            for value in (_consideration(repository, case)[1] for case in cases)
        ]
        fetching = frozenset(identities[::4])
        observations: tuple[CoverReadObservation, ...] = (
            ((policy, replay), frozenset()),
            ((policy, replay), fetching),
            ((policy, f"cover_replay_{_hex('another replay')}"), fetching),
            ((f"cover_policy_{_hex('another policy')}", replay), frozenset()),
        )

        def compare_every_authority() -> set[tuple[int, int]]:
            totals = set()
            for observation in observations:
                repository.bind_cover_read_observation(repeat(observation).__next__)
                with repository.browse_observation():
                    totals.add(_assert_equal_to_oracle(repository, monkeypatch))
            return totals

        states: dict[str, set[tuple[int, int]]] = {}
        states["fixture"] = compare_every_authority()
        _human_decision(repository, cases[2], "cover")
        _human_decision(repository, cases[3], "description")
        _automatic_decision(repository, cases[4], "cover")
        _automatic_decision(repository, cases[5], "series")
        _cover_selection(repository, cases[6], "owner", cases[6])
        _cover_selection(repository, cases[7], "automatic", cases[7])
        _cover_selection(repository, cases[8], "automatic", cases[9])
        _shadow_outcome(
            repository,
            cases[10],
            (("description", "abstention", "ask"), ("title", "applied", "automatic")),
        )
        _shadow_outcome(repository, cases[11], (("genres", "applied", "automatic"),))
        connection.commit()
        states["decided"] = compare_every_authority()
        _resolve_policies(repository, "books-1")
        states["policy ask"] = compare_every_authority()
        # An EPUB has no narrators or explicit field to ask about, however
        # either field's facts read.
        connection.executemany(
            "UPDATE review_case_field_facts SET unresolved_choice=1,"
            "has_owner_decision_actions=1 WHERE review_case_id=? AND destination=?",
            ((cases[12], "narrators"), (cases[13], "explicit")),
        )
        connection.commit()
        states["epub narrators and explicit"] = compare_every_authority()
        assert states["epub narrators and explicit"] == states["policy ask"]

        # A refresh admits a cover only while its choice is unresolved.
        def refreshing_total() -> int:
            repository.bind_cover_read_observation(repeat(observations[2]).__next__)
            with repository.browse_observation():
                return repository.read_current_attention_page(None, 1, 0, _LABELS).total

        refreshing = refreshing_total()
        choices = connection.execute(
            "SELECT unresolved_choice,review_case_id FROM review_case_field_facts "
            "WHERE destination='cover'"
        ).fetchall()
        connection.execute(
            "UPDATE review_case_field_facts SET unresolved_choice=0 "
            "WHERE destination='cover'"
        )
        connection.commit()
        states["cover choice resolved"] = compare_every_authority()
        assert refreshing_total() < refreshing
        connection.executemany(
            "UPDATE review_case_field_facts SET unresolved_choice=? "
            "WHERE destination='cover' AND review_case_id=?",
            choices,
        )
        connection.commit()
        for suspension in (
            ("destination", "primary", "cover"),
            ("destination", "primary", "title"),
            ("connection", "primary", ""),
        ):
            connection.execute(
                "INSERT INTO field_effect_suspensions(scope,connection_name,"
                "destination,reason,updated_at,document) "
                "VALUES(?,?,?,'field_unavailable',1,'{}')",
                suspension,
            )
            connection.commit()
            states[f"suspended {suspension}"] = compare_every_authority()
            connection.execute("DELETE FROM field_effect_suspensions")
            connection.commit()
        connection.execute(
            "INSERT OR REPLACE INTO automatic_safety_state(singleton,paused,"
            "global_suspension,category_suspensions_document,updated_at) "
            "VALUES(1,1,NULL,'[]',1)"
        )
        connection.commit()
        states["safety paused"] = compare_every_authority()
        # The oracle is checked against states that decide attention
        # differently, so it is not compared only where every answer agrees:
        # the authority and fetching facts move the totals within a state, and
        # decisions, policy and suspensions move them between states.
        assert len(states["fixture"]) > 1, states
        assert states["decided"] != states["fixture"], states
        assert states["policy ask"] != states["decided"], states
        assert states["suspended ('connection', 'primary', '')"] == {(0, 0)}
        assert len(set().union(*states.values())) >= 10, states
    finally:
        fixture.close()


def _outcome(read: Callable[[], object]) -> object:
    try:
        return read()
    except MetadataReportError as error:
        return ("refused", str(error))


def test_package_and_case_reports_are_read_as_before_when_they_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Library reads a package's own report and Attention its case's (#1232)."""
    fixture = OwnerResponsivenessFixture(tmp_path, 30, mixed=True)
    try:
        repository = fixture.repository
        connection = repository._connection
        packages = connection.execute(
            "SELECT item_id,report_id FROM current_review_packages "
            "WHERE review_case_id IS NOT NULL ORDER BY item_id"
        ).fetchall()
        # A cover-only work leaves Library's attention under another report,
        # and a work with a title field keeps it there as well as under its own.
        moves = ((packages[0], packages[1]), (packages[2], packages[3]))
        for (item, _), (_, other) in moves:
            connection.execute(
                "UPDATE current_review_packages SET report_id=? WHERE item_id=?",
                (other, item),
            )
        connection.commit()
        facts = {
            (item, report): connection.execute(
                "SELECT group_concat(destination) FROM (SELECT destination "
                "FROM review_attention_facts WHERE item_id=? AND report_id=? "
                "ORDER BY destination)",
                (item, report),
            ).fetchone()[0]
            for (item, own), (_, other) in moves
            for report in (own, other)
        }
        assert list(facts.values()) == ["cover", None, "cover,title", "title"]
        documents = dict(
            connection.execute("SELECT review_case_id,document FROM review_cases")
        )

        def page(search: str | None, lens: LibraryLensFilter | None) -> object:
            read = repository.read_current_library_page(search, 50, 0, lens, _LABELS)
            return read.rows, read.total, read.attention_total, read.not_matched_total

        def library() -> list[object]:
            values = []
            for search in _SEARCHES:
                values.append(
                    _outcome(
                        partial(repository.count_current_review_cases, search, _LABELS)
                    )
                )
                for lens in _LENSES:
                    values.append(_outcome(partial(page, search, lens)))
            return values

        policy, replay = repository._cover_read_authority()
        assert policy is not None and replay is not None
        observations: tuple[CoverReadObservation, ...] = (
            ((policy, replay), frozenset()),
            ((f"cover_policy_{_hex('another policy')}", replay), frozenset()),
        )
        for observation in observations:
            repository.bind_cover_read_observation(repeat(observation).__next__)
            with repository.browse_observation():
                current = library()
                with _pre_1202_library(monkeypatch):
                    assert library() == current
                assert ("refused", "review library is unavailable") in current
                for search in _SEARCHES:
                    rows = connection.execute(
                        _PRE_1202_ATTENTION_SQL,
                        (
                            *_search_parameters(search),
                            *repository._cover_read_authority(),
                            10_000,
                            0,
                        ),
                    ).fetchall()
                    assert (
                        repository.count_current_attention_fields(search)
                        == (rows[0][9])
                    )
                    if search is None:
                        per_case = Counter(row[0] for row in rows if row[8] == 1)
                        for case, document in documents.items():
                            assert (
                                repository._review_projections._current_attention_count(
                                    case
                                )
                                == (per_case[document])
                            ), case
    finally:
        fixture.close()
