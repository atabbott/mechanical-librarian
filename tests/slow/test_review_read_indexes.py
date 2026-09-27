from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    _encode_cursor,
)
from tests.unit import test_review_read_indexes as support


def test_measured_indexes_bound_representative_library_reads() -> None:
    with tempfile.TemporaryDirectory() as directory:
        connection = support._representative_database(
            Path(directory) / "application.sqlite3"
        )
        try:
            lineage_plan = support._plan(
                connection,
                support._LATEST_PREPARATION_WORK_SQL,
                ("primary", "books", "item-0000"),
            )
            shadow_plan = support._plan(
                connection,
                support._SHADOW_OUTCOME_HISTORY_SQL,
                ("review_case_" + "0" * 64, 100),
            )
            count_plan = support._plan(
                connection,
                support._CURRENT_REVIEW_CASE_COUNT_SQL,
                (None, None, None, "", "", "", "", "", ""),
            )
            assert any(
                "USING INDEX review_preparation_work_lineage_history" in step
                for step in lineage_plan
            )
            assert not any("TEMP B-TREE" in step for step in lineage_plan)
            assert any(
                "USING INDEX shadow_decision_outcomes_case_history" in step
                or "USING COVERING INDEX shadow_decision_outcomes_case_history" in step
                for step in shadow_plan
            )
            assert not any("TEMP B-TREE" in step for step in shadow_plan)
            assert any("SEARCH result USING PRIMARY KEY" in step for step in count_plan)
            assert any("shadow_outcome_field_facts_case" in step for step in count_plan)
            assert not any("result VIRTUAL TABLE" in step for step in count_plan)

            result, steps, elapsed = support._count_with_step_limit(
                connection, 1_000_000
            )
            assert result == (
                support._WORKS,
                support._representative_owner_action_count(support._WORKS)
                - support._representative_human_decision_count(support._WORKS),
            )
            assert steps <= 1_000_000
            assert elapsed < 1.0

            sizes = dict(
                connection.execute(
                    "SELECT name,sum(pgsize) FROM dbstat WHERE name IN "
                    "('review_preparation_work_lineage_history',"
                    "'shadow_decision_outcomes_case_history') GROUP BY name"
                )
            )
            assert set(sizes) == {
                "review_preparation_work_lineage_history",
                "shadow_decision_outcomes_case_history",
            }
            assert all(
                type(size) is int and size <= 1_048_576 for size in sizes.values()
            )

            connection.execute("DROP INDEX review_preparation_work_lineage_history")
            connection.commit()
            unindexed_lineage = support._plan(
                connection,
                support._LATEST_PREPARATION_WORK_SQL + " /* unindexed */",
                ("primary", "books", "item-0000"),
            )
            assert any(
                "SCAN review_preparation_work" in step for step in unindexed_lineage
            )
            assert any("TEMP B-TREE" in step for step in unindexed_lineage)
            with pytest.raises(sqlite3.OperationalError, match="interrupted"):
                support._call_with_step_limit(
                    connection,
                    2_000_000,
                    lambda: support._read_each_preparation_lineage(connection),
                )

            connection.execute("DROP INDEX shadow_decision_outcomes_case_history")
            connection.commit()
            with pytest.raises(sqlite3.OperationalError, match="interrupted"):
                support._call_with_step_limit(
                    connection,
                    2_000_000,
                    lambda: support._read_each_shadow_history(connection),
                )
        finally:
            connection.close()


def test_production_surfaces_use_measured_indexes_within_scale_budget() -> None:
    with tempfile.TemporaryDirectory() as directory:
        connection = support._representative_database(
            Path(directory) / "application.sqlite3"
        )
        repository = SQLiteMetadataReportRepository(connection)
        browse = BrowseReviewLibrary(repository)
        case_id = str(
            connection.execute(
                "SELECT review_case_id FROM review_cases LIMIT 1"
            ).fetchone()[0]
        )
        calls: tuple[tuple[str, Callable[[], object]], ...] = (
            (
                "Library",
                lambda: browse.library(lens="all", search=None, limit=50, cursor=None),
            ),
            (
                "Library attention lens",
                lambda: browse.library(
                    lens="needs_attention", search=None, limit=50, cursor=None
                ),
            ),
            (
                "Library search",
                lambda: browse.library(
                    lens="all",
                    search="Representative title 0198",
                    limit=50,
                    cursor=None,
                ),
            ),
            (
                "Attention",
                lambda: browse.attention(search=None, limit=50, cursor=None),
            ),
        )
        for name, call in calls:
            result, steps, elapsed = support._call_with_step_limit(
                connection, support._READ_VM_STEP_BUDGET, call
            )
            assert result is not None, name
            assert steps <= support._READ_VM_STEP_BUDGET, name
            assert elapsed < support._READ_WALL_BUDGET_SECONDS, name

            statements: list[str] = []
            connection.set_trace_callback(statements.append)
            try:
                call()
            finally:
                connection.set_trace_callback(None)
            materialized = next(
                statement
                for statement in statements
                if "CANDIDATES AS MATERIALIZED (SELECT" in statement.upper()
            )
            materialized_plan = support._plan(connection, materialized, ())
            assert any(
                "SEARCH current_cover_authority USING INDEX" in step
                for step in materialized_plan
            ), name
            assert any(
                "SEARCH authority_consideration USING INDEX" in step
                for step in materialized_plan
            ), name
            assert not any(
                "SCAN current_cover_authority" in step
                or "SCAN authority_consideration" in step
                for step in materialized_plan
            ), name

        plan_calls = calls + (
            (
                "selected work and history",
                lambda: repository.read_current_review_case(case_id, 100),
            ),
        )
        for name, call in plan_calls:
            field_fact_statements = tuple(
                statement
                for statement in support._traced_selects(connection, call)
                if "shadow_outcome_field_facts" in statement
            )
            assert field_fact_statements, name
            for statement in field_fact_statements:
                assert any(
                    "shadow_outcome_field_facts_case" in step
                    for step in support._plan(connection, statement, ())
                ), name
        history_statements = tuple(
            statement
            for statement in support._traced_selects(
                connection,
                lambda: repository.read_current_review_case(case_id, 100),
            )
            if "FROM shadow_decision_outcomes " in statement
        )
        assert history_statements
        assert all(
            any(
                "shadow_decision_outcomes_case_history" in step
                for step in support._plan(connection, statement, ())
            )
            for statement in history_statements
        )
        repository.close()


def test_deep_browse_pages_sort_thin_keys_before_loading_documents() -> None:
    """A page's document payload stays fixed past 2,000 attention rows."""
    with tempfile.TemporaryDirectory() as directory:
        connection = support._representative_database(
            Path(directory) / "application.sqlite3", works=4_100
        )
        repository = SQLiteMetadataReportRepository(connection)
        browse = BrowseReviewLibrary(repository)
        try:
            calls = (
                (
                    "library",
                    lambda cursor: browse.library(
                        lens="all", search=None, limit=50, cursor=cursor
                    ),
                ),
                (
                    "attention",
                    lambda cursor: browse.attention(
                        search=None, limit=50, cursor=cursor
                    ),
                ),
            )
            for surface, call in calls:
                first = call(None)
                page = first
                seen = 0
                while True:
                    page_items = page["items"]
                    assert type(page_items) is list
                    seen += len(page_items)
                    next_cursor = page["next_cursor"]
                    assert next_cursor is None or type(next_cursor) is str
                    if next_cursor is None:
                        break
                    page = call(next_cursor)
                cursor = _encode_cursor(
                    1_500,
                    surface,
                    "all" if surface == "library" else "needs_attention",
                    None,
                )
                deep = call(cursor)
                first_items = first["items"]
                deep_items = deep["items"]
                assert type(first_items) is list
                assert type(deep_items) is list
                assert len(first_items) == 50
                assert len(deep_items) == 50
                if surface == "attention":
                    total = first["total"]
                    assert type(total) is int
                    assert total >= 2_000
                else:
                    counts = first["counts"]
                    assert type(counts) is dict
                    total = counts["all"]
                    assert type(total) is int
                assert seen == total
                statements: list[str] = []
                connection.set_trace_callback(statements.append)
                try:
                    call(cursor)
                finally:
                    connection.set_trace_callback(None)
                statement = next(
                    value
                    for value in statements
                    if "CANDIDATES AS MATERIALIZED (SELECT" in value.upper()
                )
                sorter, documents = statement.split(") SELECT rc.document", 1)
                assert "rc.document" not in sorter
                assert "mr.document AS" not in sorter
                assert documents.startswith(",")
        finally:
            repository.close()
