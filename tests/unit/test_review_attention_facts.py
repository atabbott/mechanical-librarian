"""Persisted attention facts stay equal to their derivation (#1232).

`review_attention_facts` holds, per current package, report and field, the
attention predicate's stored part; triggers on every input recompute the facts
a changed row can reach in the writer's own statement. These tests hold the
input map to the derivation's tables, replay one write per maintained input
and operation comparing the facts with a fresh derivation after each, prove
that dropping any one trigger is caught by that replay, and pin the plans and
VM-step cost of the maintenance and the reads that use it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    publish_audiobook_preview,
)
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.unit.test_browse_attention_statements import (
    _automatic_decision,
    _cover_selection,
    _hex,
    _human_decision,
    _shadow_outcome,
)
from tests.unit.test_report_browse_facts import _resolve_policies
from tests.unit.test_review_read_indexes import _plan, _representative_database

_FACTS_SQL = (
    "SELECT connection_name,library_name,item_id,report_id,destination,"
    "cover_consideration_id,cover_policy_reference,cover_refresh_admits "
    "FROM review_attention_facts"
)
# The derivation reaches these append-only parents only through a child whose
# immediate foreign key names them, so inserting one reaches no fact.
_PARENTS = {
    "review_cases": (("current_review_packages", "review_case_id"),),
    "metadata_reports": (
        ("current_review_packages", "report_id"),
        ("review_cases", "report_id"),
    ),
    "cover_considerations": (("current_cover_considerations", "consideration_id"),),
    "cover_selection_authorities": (
        ("current_cover_selection_authorities", "selection_id"),
    ),
}
_OPERATIONS = ("insert", "update", "delete")
# The fact source as main held it before #1249 removed the audiobook clause
# and let narrators ask under the authors' policy key, pinned by digest so the
# oracle cannot drift with the live text.
_AUTHORS_ASK = (
    "(result.destination='authors' AND json_extract(policy_resolution.document,"
    "'$.destinations.contributor_names')='ask') OR "
)
_NARRATORS_ASK = _AUTHORS_ASK.replace("'authors'", "'narrators'")
_PRE_1249_ATTENTION_FACT_SOURCE_SQL = _ATTENTION_FACT_SOURCE_SQL.replace(
    _NARRATORS_ASK, "", 1
).replace(
    "OR result.manual_bibliographic_attention=1",
    "OR facts.format <> 'epub' OR result.manual_bibliographic_attention=1",
    1,
)


def _facts(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return sorted(connection.execute(_FACTS_SQL).fetchall(), key=repr)


def _derived(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return sorted(connection.execute(_ATTENTION_FACT_SOURCE_SQL).fetchall(), key=repr)


@pytest.fixture(scope="module")
def _state(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A small owner store whose fields ask for the owner for several reasons."""
    root = tmp_path_factory.mktemp("attention-facts")
    root.chmod(0o700)
    (root / "owner").mkdir(mode=0o700)
    fixture = OwnerResponsivenessFixture(root / "owner", 30, mixed=True)
    path = root / "state.sqlite3"
    try:
        fixture.repository._connection.execute("VACUUM INTO ?", (str(path),))
    finally:
        fixture.close()
    yield path


def _open(state: Path, directory: Path) -> SQLiteMetadataReportRepository:
    path = directory / "application.sqlite3"
    shutil.copyfile(state, path)
    repository = open_repository(path, create=False)
    # The replay writes rows whose parents a real write would also create;
    # maintenance reads none of those parents, so the checks are not needed.
    repository._connection.execute("PRAGMA foreign_keys=OFF")
    return repository


def _one(connection: sqlite3.Connection, sql: str, *parameters: object) -> tuple:
    row = connection.execute(sql, parameters).fetchone()
    assert row is not None, sql
    return tuple(row)


def _attentive(connection: sqlite3.Connection, destination: str) -> tuple:
    """A derived fact's package, case and field, chosen from the inputs."""
    return _one(
        connection,
        "SELECT c.connection_name,c.library_name,c.item_id,c.review_case_id,"
        "derived.destination FROM (" + _ATTENTION_FACT_SOURCE_SQL + ") derived "
        "JOIN current_review_packages c ON (c.connection_name,c.library_name,"
        "c.item_id)=(derived.connection_name,derived.library_name,derived.item_id) "
        "WHERE derived.destination=? ORDER BY c.item_id LIMIT 1",
        destination,
    )


def _quiet(connection: sqlite3.Connection, destination: str) -> str:
    """A case whose owner-actionable field asks for nothing yet."""
    return str(
        _one(
            connection,
            "SELECT result.review_case_id FROM review_case_field_facts result "
            "JOIN current_review_packages c ON c.review_case_id=result.review_case_id "
            "WHERE result.destination=? AND result.has_owner_decision_actions=1 "
            "AND NOT EXISTS (SELECT 1 FROM shadow_decision_outcomes shadow "
            "WHERE shadow.review_case_id=result.review_case_id) AND NOT EXISTS (SELECT 1 FROM ("
            + _ATTENTION_FACT_SOURCE_SQL
            + ") d "
            "WHERE d.item_id=c.item_id AND d.destination=result.destination) "
            "ORDER BY c.item_id DESC LIMIT 1",
            destination,
        )[0]
    )


_Step = tuple[str, tuple[tuple[str, str], ...], Callable[[], object]]


def _steps(repository: SQLiteMetadataReportRepository) -> Iterator[_Step]:
    """One write per maintained input and operation, each changing the facts.

    Each step is yielded before it runs, so it chooses its target from the
    state every earlier step left, and only from the inputs.
    """
    connection = repository._connection
    run = connection.execute
    saved: dict[str, tuple] = {}

    def save(name: str, sql: str, *parameters: object) -> tuple:
        saved[name] = _one(connection, sql, *parameters)
        return saved[name]

    title = _attentive(connection, "title")
    yield (
        "package delete",
        (("current_review_packages", "delete"),),
        lambda: (
            save(
                "package",
                "SELECT * FROM current_review_packages WHERE item_id=?",
                title[2],
            ),
            run("DELETE FROM current_review_packages WHERE item_id=?", (title[2],)),
        ),
    )
    yield (
        "package insert",
        (("current_review_packages", "insert"),),
        lambda: run(
            "INSERT INTO current_review_packages VALUES(?,?,?,?,?,?,?,?)",
            saved["package"],
        ),
    )
    yield (
        "package names another report",
        (("current_review_packages", "update"),),
        lambda: run(
            "UPDATE current_review_packages SET report_id=(SELECT report_id "
            "FROM metadata_reports WHERE report_id<>current_review_packages.report_id "
            "ORDER BY report_id LIMIT 1) WHERE item_id=?",
            (title[2],),
        ),
    )

    def recording(case_id: str, report_id: str) -> None:
        """A recording's facts differ from an ebook's only through the fields
        an EPUB omits, so its narrators first ask for the owner (#1249)."""
        assert (
            run(
                "UPDATE review_case_field_facts SET unresolved_choice=1,"
                "has_owner_decision_actions=1 "
                "WHERE review_case_id=? AND destination='narrators'",
                (case_id,),
            ).rowcount
            == 1
        )
        run(
            "UPDATE metadata_report_browse_facts SET format='m4b' WHERE report_id=?",
            (report_id,),
        )

    # Attention reads that package under its case's report, which it no
    # longer names.
    case_report = _one(
        connection,
        "SELECT report_id FROM review_cases WHERE review_case_id=?",
        title[3],
    )[0]
    yield (
        "its case's report becomes a recording",
        (
            ("review_case_field_facts", "update"),
            ("metadata_report_browse_facts", "update"),
        ),
        lambda: recording(title[3], case_report),
    )
    title = _attentive(connection, "title")
    yield (
        "field settles",
        (("review_case_field_facts", "update"),),
        lambda: run(
            "UPDATE review_case_field_facts SET has_owner_decision_actions=0 "
            "WHERE review_case_id=? AND destination='title'",
            (title[3],),
        ),
    )
    title = _attentive(connection, "title")
    yield (
        "field fact delete",
        (("review_case_field_facts", "delete"),),
        lambda: (
            save(
                "field",
                "SELECT * FROM review_case_field_facts "
                "WHERE review_case_id=? AND destination='title'",
                title[3],
            ),
            run(
                "DELETE FROM review_case_field_facts "
                "WHERE review_case_id=? AND destination='title'",
                (title[3],),
            ),
        ),
    )
    yield (
        "field fact insert",
        (("review_case_field_facts", "insert"),),
        lambda: run(
            "INSERT INTO review_case_field_facts VALUES(?,?,?,?,?)", saved["field"]
        ),
    )
    quiet = _quiet(connection, "isbn")
    report = _one(
        connection, "SELECT report_id FROM review_cases WHERE review_case_id=?", quiet
    )[0]
    yield (
        "report becomes a recording",
        (
            ("review_case_field_facts", "update"),
            ("metadata_report_browse_facts", "update"),
        ),
        lambda: recording(quiet, report),
    )
    yield (
        "report facts delete",
        (("metadata_report_browse_facts", "delete"),),
        lambda: (
            save(
                "report",
                "SELECT * FROM metadata_report_browse_facts WHERE report_id=?",
                report,
            ),
            run(
                "DELETE FROM metadata_report_browse_facts WHERE report_id=?",
                (report,),
            ),
        ),
    )
    yield (
        "report facts insert",
        (("metadata_report_browse_facts", "insert"),),
        lambda: run(
            "INSERT INTO metadata_report_browse_facts VALUES(?,?,?,?,?)",
            saved["report"],
        ),
    )
    title = _attentive(connection, "title")
    yield (
        "owner decides",
        (("human_decisions", "insert"),),
        lambda: _human_decision(repository, title[3], "title"),
    )
    title = _attentive(connection, "title")
    yield (
        "automation decides",
        (("automatic_decisions", "insert"),),
        lambda: _automatic_decision(repository, title[3], "title"),
    )
    quiet = _quiet(connection, "description")
    yield (
        "abstention asks",
        (("shadow_outcome_field_facts", "insert"),),
        lambda: _shadow_outcome(
            repository, quiet, (("description", "abstention", "ask"),)
        ),
    )
    authored = f"description_decision_{_hex('authored')}"
    yield (
        "description authored",
        (("current_description_decisions", "insert"),),
        lambda: run(
            "INSERT INTO current_description_decisions VALUES(?,?)", (quiet, authored)
        ),
    )
    elsewhere = _attentive(connection, "title")[3]
    yield (
        "authored description names another case",
        (("current_description_decisions", "update"),),
        lambda: run(
            "UPDATE current_description_decisions SET review_case_id=?", (elsewhere,)
        ),
    )
    yield (
        "authored description names its case again",
        (("current_description_decisions", "update"),),
        lambda: run(
            "UPDATE current_description_decisions SET review_case_id=?", (quiet,)
        ),
    )
    yield (
        "authored description withdrawn",
        (("current_description_decisions", "delete"),),
        lambda: run("DELETE FROM current_description_decisions"),
    )
    yield (
        "abstention applies",
        (("shadow_outcome_field_facts", "update"),),
        lambda: run(
            "UPDATE shadow_outcome_field_facts SET disposition='applied' "
            "WHERE review_case_id=?",
            (quiet,),
        ),
    )
    yield (
        "abstention returns",
        (("shadow_outcome_field_facts", "update"),),
        lambda: run(
            "UPDATE shadow_outcome_field_facts SET disposition='abstention' "
            "WHERE review_case_id=?",
            (quiet,),
        ),
    )
    yield (
        "abstention withdrawn",
        (("shadow_outcome_field_facts", "delete"),),
        lambda: run(
            "DELETE FROM shadow_outcome_field_facts WHERE review_case_id=?", (quiet,)
        ),
    )
    quiet = _quiet(connection, "isbn")

    def pause() -> None:
        _shadow_outcome(repository, quiet, (("isbn", "applied", "automatic"),))
        run(
            "UPDATE automatic_safety_state SET paused=1,global_suspension=NULL,"
            "category_suspensions_document='[]'"
        )

    yield (
        "safety pauses an applied field",
        (
            ("shadow_outcome_field_facts", "insert"),
            ("automatic_safety_state", "update"),
        ),
        pause,
    )
    yield (
        "safety reset",
        (("automatic_safety_state", "delete"),),
        lambda: run("DELETE FROM automatic_safety_state"),
    )
    yield (
        "safety pauses",
        (("automatic_safety_state", "insert"),),
        lambda: run(
            "INSERT INTO automatic_safety_state(singleton,paused,global_suspension,"
            "category_suspensions_document,updated_at) VALUES(1,1,NULL,'[]',1)"
        ),
    )
    yield (
        "safety resumes",
        (("automatic_safety_state", "update"),),
        lambda: run("UPDATE automatic_safety_state SET paused=0"),
    )
    yield (
        "policy asks",
        (("metadata_policy_resolutions", "insert"),),
        lambda: _resolve_policies(repository, "books-1"),
    )
    yield (
        "policy automates",
        (("metadata_policy_resolutions", "update"),),
        lambda: run(
            "UPDATE metadata_policy_resolutions SET document=json_set(document,"
            "'$.destinations.isbn_form','automatic') WHERE library_name='books-1'"
        ),
    )
    yield (
        "policy asks again",
        (("metadata_policy_resolutions", "update"),),
        lambda: run(
            "UPDATE metadata_policy_resolutions SET document=json_set(document,"
            "'$.destinations.isbn_form','ask') WHERE library_name='books-1'"
        ),
    )
    asking = _one(
        connection,
        "SELECT rc.proposal_set_id FROM review_cases rc JOIN current_review_packages c "
        "ON c.review_case_id=rc.review_case_id WHERE c.library_name='books-1' "
        "ORDER BY c.item_id LIMIT 1",
    )[0]
    yield (
        "proposal facts delete",
        (("proposal_set_browse_facts", "delete"),),
        lambda: (
            save(
                "proposal",
                "SELECT * FROM proposal_set_browse_facts WHERE proposal_set_id=?",
                asking,
            ),
            run(
                "DELETE FROM proposal_set_browse_facts WHERE proposal_set_id=?",
                (asking,),
            ),
        ),
    )
    yield (
        "proposal facts insert",
        (("proposal_set_browse_facts", "insert"),),
        lambda: run(
            "INSERT INTO proposal_set_browse_facts VALUES(?,?)", saved["proposal"]
        ),
    )
    yield (
        "proposal forgets its policy",
        (("proposal_set_browse_facts", "update"),),
        lambda: run(
            "UPDATE proposal_set_browse_facts SET resolved_policy_id=NULL "
            "WHERE proposal_set_id=?",
            (asking,),
        ),
    )
    yield (
        "policy withdrawn",
        (("metadata_policy_resolutions", "delete"),),
        lambda: run("DELETE FROM metadata_policy_resolutions"),
    )
    cover = _attentive(connection, "cover")
    yield (
        "cover pointer delete",
        (("current_cover_considerations", "delete"),),
        lambda: (
            save(
                "pointer",
                "SELECT * FROM current_cover_considerations WHERE review_case_id=?",
                cover[3],
            ),
            run(
                "DELETE FROM current_cover_considerations WHERE review_case_id=?",
                (cover[3],),
            ),
        ),
    )
    yield (
        "cover pointer insert",
        (("current_cover_considerations", "insert"),),
        lambda: run(
            "INSERT INTO current_cover_considerations VALUES(?,?)", saved["pointer"]
        ),
    )
    yield (
        "cover reconsidered",
        (("current_cover_considerations", "update"),),
        lambda: (
            run(
                "INSERT INTO cover_considerations SELECT ?,"
                + ",".join(
                    "json_set(currentness_document,'$.manual_ready',0)"
                    if column == "currentness_document"
                    else column
                    for column in _columns(connection, "cover_considerations")[1:]
                )
                + " FROM cover_considerations WHERE consideration_id=?",
                (f"cover_consideration_{_hex('unready')}", saved["pointer"][1]),
            ),
            run(
                "UPDATE current_cover_considerations SET consideration_id=? "
                "WHERE review_case_id=?",
                (f"cover_consideration_{_hex('unready')}", cover[3]),
            ),
        ),
    )
    cover = _attentive(connection, "cover")
    yield (
        "owner selects a cover",
        (("current_cover_selection_authorities", "insert"),),
        lambda: _cover_selection(repository, cover[3], "owner", cover[3]),
    )
    yield (
        "owner selection withdrawn",
        (("current_cover_selection_authorities", "delete"),),
        lambda: (
            save(
                "selection",
                "SELECT * FROM current_cover_selection_authorities "
                "WHERE (connection_name,library_name,item_id)=(?,?,?)",
                *cover[:3],
            ),
            run("DELETE FROM current_cover_selection_authorities"),
        ),
    )
    yield (
        "owner selection restored",
        (("current_cover_selection_authorities", "insert"),),
        lambda: run(
            "INSERT INTO current_cover_selection_authorities VALUES(?,?,?,?)",
            saved["selection"],
        ),
    )
    stale = f"cover_selection_{_hex('stale')}"
    yield (
        "selection becomes automation for another consideration",
        (("current_cover_selection_authorities", "update"),),
        lambda: (
            run(
                "INSERT INTO cover_selection_authorities(selection_id,connection_name,"
                "library_name,item_id,kind,decision_id,consideration_id,"
                "supersedes_selection_id,occurred_at,document) "
                "VALUES(?,?,?,?,'automatic',?,?,NULL,1,?)",
                (
                    stale,
                    *cover[:3],
                    f"automatic_decision_{_hex('stale')}",
                    f"cover_consideration_{_hex('unready')}",
                    json.dumps(
                        {
                            "decision_document": json.dumps(
                                {"schema_version": "cover-automatic-decision-v3"}
                            )
                        }
                    ),
                ),
            ),
            run(
                "UPDATE current_cover_selection_authorities SET selection_id=? "
                "WHERE (connection_name,library_name,item_id)=(?,?,?)",
                (stale, *cover[:3]),
            ),
        ),
    )
    yield (
        "connection suspended",
        (("field_effect_suspensions", "insert"),),
        lambda: run(
            "INSERT INTO field_effect_suspensions(scope,connection_name,destination,"
            "reason,updated_at,document) "
            "VALUES('connection','primary','','connection_unavailable',1,'{}')"
        ),
    )
    yield (
        "suspension narrows to one field",
        (("field_effect_suspensions", "update"),),
        lambda: run(
            "UPDATE field_effect_suspensions SET scope='destination',"
            "destination='cover',reason='field_unavailable'"
        ),
    )
    yield (
        "suspension moves to another field",
        (("field_effect_suspensions", "update"),),
        lambda: run("UPDATE field_effect_suspensions SET destination='title'"),
    )
    yield (
        "suspension cleared",
        (("field_effect_suspensions", "delete"),),
        lambda: run("DELETE FROM field_effect_suspensions"),
    )


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]


def _replay(
    repository: SQLiteMetadataReportRepository,
) -> list[tuple[str, tuple[tuple[str, str], ...], bool, bool]]:
    """Run every step: (name, inputs, facts equal derivation, derivation moved)."""
    connection = repository._connection
    outcomes = []
    for name, inputs, write in _steps(repository):
        before = _derived(connection)
        write()
        after = _derived(connection)
        outcomes.append((name, inputs, _facts(connection) == after, after != before))
    connection.commit()
    return outcomes


def test_input_map_names_every_table_the_derivation_reads(tmp_path: Path) -> None:
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        connection = repository._connection
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if re.search(rf"\b{row[0]}\b", _ATTENTION_FACT_SOURCE_SQL)
        }
        assert tables == set(schema._ATTENTION_INPUTS) | set(_PARENTS)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        triggers = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        )

        def refused(table: str) -> set[str]:
            return {
                operation
                for operation in _OPERATIONS
                if any(
                    re.fullmatch(
                        f"CREATE TRIGGER \\w+ BEFORE {operation.upper()} ON {table} "
                        "BEGIN SELECT RAISE\\(ABORT, '[^']*'\\); END",
                        sql,
                    )
                    for sql in triggers.values()
                )
            }

        for table, (operations, _scope) in schema._ATTENTION_INPUTS.items():
            assert operations == tuple(
                operation
                for operation in _OPERATIONS
                if operation not in refused(table)
            ), table
            for operation in operations:
                assert re.match(
                    f"CREATE TRIGGER {table}_refresh_attention_{operation} AFTER "
                    f"{operation.upper()}( OF [a-z_,]+)? ON {table}( WHEN .+?)? BEGIN ",
                    triggers[f"{table}_refresh_attention_{operation}"],
                )
        for table, children in _PARENTS.items():
            assert refused(table) == {"update", "delete"}, table
            for child, column in children:
                assert any(
                    (row[2], row[3]) == (table, column)
                    for row in connection.execute(f"PRAGMA foreign_key_list({child})")
                ), (table, child)
        assert len([name for name in triggers if "_refresh_attention_" in name]) == sum(
            len(ops) for ops, _ in schema._ATTENTION_INPUTS.values()
        )


_REPLACE = re.compile(
    r"\bOR\s+REPLACE\b|\bREPLACE\s+INTO\b|\bON\s+CONFLICT\s+REPLACE\b"
)


def _literal(node: ast.expr) -> str | None:
    """A string constant, or a `+` chain of them, as the statement it builds."""
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return None if left is None or right is None else left + right
    return None


def test_no_write_replaces_a_row_without_its_delete_trigger(tmp_path: Path) -> None:
    """REPLACE deletes a conflicting row without firing its delete triggers.

    Unless recursive triggers are on, that row's facts would be left behind.
    Every string literal in `src` and `scripts` is read, with adjacent and `+`
    joined literals as one, in the upper case this repository writes SQL in so
    that prose is not mistaken for it. A statement assembled from a name or an
    f-string field at run time is not seen, which is why the created schema is
    also read for a table-level conflict clause.
    """
    root = Path(__file__).resolve().parents[2]
    for path in (*(root / "src").rglob("*.py"), *(root / "scripts").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.expr) and (text := _literal(node)) is not None:
                assert not _REPLACE.search(text), (path, text)
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        for (sql,) in repository._connection.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        ):
            assert not _REPLACE.search(sql), sql


def test_every_maintained_write_keeps_facts_equal_to_their_derivation(
    _state: Path, tmp_path: Path
) -> None:
    with closing(_open(_state, tmp_path)) as repository:
        outcomes = _replay(repository)
    assert [name for name, _, equal, _ in outcomes if not equal] == []
    assert [name for name, _, _, moved in outcomes if not moved] == []
    covered = {pair for _, inputs, _, _ in outcomes for pair in inputs}
    assert covered == {
        (table, operation)
        for table, (operations, _) in schema._ATTENTION_INPUTS.items()
        for operation in operations
    }


@pytest.mark.parametrize("trigger", sorted(schema._ATTENTION_INPUT_TRIGGERS))
def test_a_missing_maintenance_trigger_is_caught(
    _state: Path, tmp_path: Path, trigger: str
) -> None:
    with closing(_open(_state, tmp_path)) as repository:
        repository._connection.execute(f"DROP TRIGGER {trigger}")
        outcomes = _replay(repository)
    table, operation = trigger.split("_refresh_attention_")
    assert any(
        not equal for _, inputs, equal, _ in outcomes if (table, operation) in inputs
    ), trigger


def test_open_refuses_facts_that_disagree_with_their_derivation(
    _state: Path, tmp_path: Path
) -> None:
    for tamper in (
        "DELETE FROM review_attention_facts WHERE item_id="
        "(SELECT min(item_id) FROM review_attention_facts)",
        "UPDATE review_attention_facts SET cover_refresh_admits=1-cover_refresh_admits "
        "WHERE cover_refresh_admits IS NOT NULL",
        "UPDATE review_attention_facts SET cover_policy_reference="
        "json_quote(cover_policy_reference) WHERE cover_policy_reference IS NOT NULL",
        "INSERT INTO review_attention_facts SELECT connection_name,library_name,"
        "item_id,report_id,'asin',NULL,NULL,NULL FROM review_attention_facts LIMIT 1",
    ):
        path = tmp_path / "tampered.sqlite3"
        shutil.copyfile(_state, path)
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute(tamper).rowcount
            connection.commit()
        before = path.read_bytes()
        with pytest.raises(
            MetadataReportError, match="^database is unavailable or incompatible$"
        ):
            open_repository(path, create=False)
        assert path.read_bytes() == before, tamper


def test_open_refuses_the_predecessor_without_attention_facts(
    _state: Path, tmp_path: Path
) -> None:
    path = tmp_path / "predecessor.sqlite3"
    shutil.copyfile(_state, path)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "".join(
                f"DROP {kind.upper()} {name};"
                for kind, name, _ in reversed(schema._ATTENTION_FACT_OBJECTS)
            )
        )
    before = path.read_bytes()
    with pytest.raises(
        MetadataReportError, match="^database is unavailable or incompatible$"
    ):
        open_repository(path, create=False)
    assert path.read_bytes() == before


def _bound(statement: str) -> str:
    """A trigger body statement with its row references as parameters."""
    return re.sub(r"\b(?:NEW|OLD)\.\w+", "?", statement)


def test_maintenance_reaches_its_scope_by_index() -> None:
    """Each refresh recomputes the facts its scope names, never the library.

    A safety change and a policy activation are global inputs and are left out.
    Plans depend on the schema alone, so a small store fixes the plan an
    owner-scale store gets.
    """
    with tempfile.TemporaryDirectory() as directory:
        connection = _representative_database(
            Path(directory) / "application.sqlite3", works=40
        )
        try:
            body = schema._ATTENTION_REFRESH_SQL.split(" BEGIN ", 1)[1]
            delete, insert = (part.strip() for part in body.split("; ")[:2])
            statements = [
                _bound(delete),
                _bound(insert.split(") ", 1)[1].removesuffix("; END")),
                *(
                    _bound(scope.format(row="NEW"))
                    for table, (_operations, scope) in schema._ATTENTION_INPUTS.items()
                    # Global inputs: a safety change reaches every automatic
                    # field, and a policy activation every proposal it resolves.
                    if table
                    not in ("automatic_safety_state", "metadata_policy_resolutions")
                ),
            ]
            plans = [
                _plan(connection, statement, (None,) * statement.count("?"))
                for statement in statements
            ]
            for plan in plans:
                assert not any(
                    re.match(r"SCAN (c|rc|result|mr|facts|policy|outcome)\b", step)
                    for step in plan
                ), plan
        finally:
            connection.close()


def test_one_decision_refreshes_one_field_at_any_library_size() -> None:
    """A decision's maintenance costs the same at 40 works and at 400."""
    costs = []
    for works in (40, 400):
        with tempfile.TemporaryDirectory() as directory:
            connection = _representative_database(
                Path(directory) / "application.sqlite3", works=works
            )
            try:
                case_id = connection.execute(
                    "SELECT review_case_id FROM current_review_packages "
                    "WHERE review_case_id IS NOT NULL ORDER BY item_id LIMIT 1"
                ).fetchone()[0]
                steps = 0

                def progress() -> int:
                    nonlocal steps
                    steps += 100
                    return 0

                connection.set_progress_handler(progress, 100)
                connection.execute(
                    "INSERT INTO human_decisions(decision_id,review_case_id,"
                    "proposal_id,target_fingerprint,destination,actor,outcome,"
                    "selected_value_document,idempotency_digest,request_digest,"
                    "occurred_at,document) VALUES(?,?,?,?,'title','owner','accept',"
                    "'null',?,?,1,'{}')",
                    (
                        f"human_decision_{_hex(case_id)}",
                        case_id,
                        f"review_proposal_{_hex(case_id)}",
                        _hex(case_id),
                        _hex(case_id),
                        _hex(case_id),
                    ),
                )
                connection.set_progress_handler(None, 0)
                assert _facts(connection) == _derived(connection)
                costs.append(steps)
            finally:
                connection.close()
    assert costs[1] <= costs[0] * 1.5 + 200, costs


def test_update_guards_name_exactly_the_columns_the_facts_read(
    tmp_path: Path,
) -> None:
    """Derived from the source statement and each scope, not from reading them."""
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        connection = repository._connection
        reads: dict[str, set[str]] = {}

        def record(
            action: int, table: str | None, column: str | None, *_: object
        ) -> int:
            if action == sqlite3.SQLITE_READ and table and column:
                reads.setdefault(table, set()).add(column)
            return sqlite3.SQLITE_OK

        connection.set_authorizer(record)
        connection.execute("EXPLAIN " + _ATTENTION_FACT_SOURCE_SQL).fetchall()
        connection.set_authorizer(None)
    updated = {
        table: reads.get(table, set()) | set(re.findall(r"\{row\}\.(\w+)", scope))
        for table, (operations, scope) in schema._ATTENTION_INPUTS.items()
        if "update" in operations
    }
    assert {
        table: set(columns)
        for table, columns in schema._ATTENTION_UPDATED_COLUMNS.items()
    } == updated
    # Every guarded column is compared by its stored value alone.
    for table, columns in schema._ATTENTION_UPDATED_COLUMNS.items():
        assert all(
            f"OLD.{name} IS NOT NEW.{name} COLLATE BINARY"
            in schema._ATTENTION_INPUT_TRIGGERS[f"{table}_refresh_attention_update"]
            for name in columns
        ), table


def test_a_write_that_changes_no_read_column_refreshes_no_fact(
    _state: Path, tmp_path: Path
) -> None:
    with closing(_open(_state, tmp_path)) as repository:
        connection = repository._connection
        _replay(repository)
        _resolve_policies(repository, "books-1")
        connection.execute(
            "INSERT INTO field_effect_suspensions(scope,connection_name,destination,"
            "reason,updated_at,document) "
            "VALUES('connection','primary','','connection_unavailable',1,'{}')"
        )

        def changes(sql: str, *parameters: object) -> tuple[int, int]:
            before = connection.total_changes
            rows = connection.execute(sql, parameters).rowcount
            return rows, connection.total_changes - before

        # Each input rewritten to its own values: only the rows themselves.
        rewritten = []
        for table in schema._ATTENTION_UPDATED_COLUMNS:
            assignments = ",".join(
                f"{name}={name}" for name in _columns(connection, table)
            )
            rows, changed = changes(f"UPDATE {table} SET {assignments}")
            assert changed == rows, table
            if rows:
                rewritten.append(table)
        assert {
            "current_review_packages",
            "automatic_safety_state",
            "field_effect_suspensions",
            "metadata_policy_resolutions",
        } <= set(rewritten), rewritten
        # A repeated failure re-records only a suspension's reason and time.
        assert changes(
            "UPDATE field_effect_suspensions SET reason='insufficient_permission',"
            "updated_at=2,document='{\"again\":1}'"
        ) == (1, 1)
        connection.execute("DELETE FROM field_effect_suspensions")
        # Re-recording the safety state writes every column; only its time moves.
        decisions = repository._decisions
        state = decisions.automatic_safety_state()
        _shadow_outcome(
            repository, _quiet(connection, "isbn"), (("isbn", "applied", "automatic"),)
        )
        before = connection.total_changes
        decisions._set_automatic_safety_state(state, 2)
        assert connection.total_changes - before == 1
        facts = _facts(connection)
        decisions._set_automatic_safety_state(
            type(state)(
                not state.paused, state.global_suspension, state.category_suspensions
            ),
            3,
        )
        assert connection.total_changes - before > 2
        assert _facts(connection) == _derived(connection) != facts


def _predecessor(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return sorted(
        connection.execute(_PRE_1249_ATTENTION_FACT_SOURCE_SQL).fetchall(), key=repr
    )


def test_the_predecessor_oracle_is_mains_fact_source_before_1249() -> None:
    assert (
        hashlib.sha256(_PRE_1249_ATTENTION_FACT_SOURCE_SQL.encode()).hexdigest()
        == "1513216e8213975ceedf17f5ddd01cf7f3ba4c8ab6c68678f9013399bc7b9d86"
    )
    # Exactly two edits: the audiobook clause leaves, and narrators asks
    # beside authors.
    assert _ATTENTION_FACT_SOURCE_SQL.count(_NARRATORS_ASK) == 1
    assert _PRE_1249_ATTENTION_FACT_SOURCE_SQL.count(_AUTHORS_ASK) == 1
    assert (
        _PRE_1249_ATTENTION_FACT_SOURCE_SQL.replace(
            "OR facts.format <> 'epub' ", "", 1
        ).replace(_AUTHORS_ASK, _AUTHORS_ASK + _NARRATORS_ASK, 1)
        == _ATTENTION_FACT_SOURCE_SQL
    )


def test_ebook_facts_equal_the_predecessor_derivation(_state: Path) -> None:
    """Removing the audiobook clause moves no ebook fact (#1249)."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.sqlite3"
        shutil.copyfile(_state, path)
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM metadata_report_browse_facts WHERE format<>'epub'"
            ).fetchone() == (0,)
            assert _facts(connection) == _derived(connection)
            assert _derived(connection) == _predecessor(connection)
            # Negative control: one recording separates the two derivations.
            report = _one(
                connection,
                "SELECT report_id FROM review_cases WHERE review_case_id=?",
                _quiet(connection, "isbn"),
            )[0]
            connection.execute(
                "UPDATE metadata_report_browse_facts SET format='m4b' "
                "WHERE report_id=?",
                (report,),
            )
            assert _derived(connection) != _predecessor(connection)
        connection = _representative_database(path.with_name("r.sqlite3"), works=40)
        try:
            assert _derived(connection)
            assert _derived(connection) == _predecessor(connection)
        finally:
            connection.close()


def test_open_refuses_a_store_maintained_by_the_predecessor_source(
    _state: Path, tmp_path: Path
) -> None:
    path = tmp_path / "predecessor.sqlite3"
    shutil.copyfile(_state, path)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "DROP TRIGGER review_attention_refresh_apply;"
            + schema._ATTENTION_REFRESH_SQL.replace(
                _ATTENTION_FACT_SOURCE_SQL, _PRE_1249_ATTENTION_FACT_SOURCE_SQL, 1
            )
            + ";"
        )
    before = path.read_bytes()
    with pytest.raises(
        MetadataReportError, match="^database is unavailable or incompatible$"
    ):
        open_repository(path, create=False)
    assert path.read_bytes() == before


def test_a_recordings_settled_fill_has_no_attention_fact(tmp_path: Path) -> None:
    """A settled ASIN fill under Ask asks for nothing, as an ebook's would."""
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        connection = repository._connection
        # Activation needs one Automatic option; isbn forms no field here.
        case_id, _ = publish_audiobook_preview(
            repository, admitted_audiobook_report(), ("isbn_form",)
        )
        assert _one(
            connection,
            "SELECT unresolved_choice,has_owner_decision_actions "
            "FROM review_case_field_facts WHERE review_case_id=? AND destination='asin'",
            case_id,
        ) == (0, 1)
        assert _facts(connection) == _derived(connection)
        assert "asin" not in {row[4] for row in _facts(connection)}
        # Control: the predecessor derivation counted it on the format alone.
        assert "asin" in {row[4] for row in _predecessor(connection)}
