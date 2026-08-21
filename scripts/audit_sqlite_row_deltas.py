#!/usr/bin/env python3
"""Measure committed exact-schema row deltas for the public audit workload."""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence.schema import (
    _create_schema,
    _validate_schema,
)

TEST_NODES = (
    "tests/unit/test_review_pipeline.py::test_one_work_progresses_through_every_persisted_stage",
    "tests/unit/test_review_pipeline.py::test_recovery_successor_uses_normal_projection_and_atomic_package_cutover",
    "tests/unit/test_review_session_persistence.py::test_sqlite_human_decision_is_atomic_idempotent_and_reopens_exactly",
    "tests/unit/test_metadata_policy_activation.py::test_atomic_cutover_rewinds_existing_evidence_and_completes_after_restart",
    "tests/unit/test_field_effects.py::test_verified_sibling_survives_failure_and_restart",
    "tests/unit/test_cover_activation.py::test_selected_original_reaches_verified_guarded_fake_service_application",
    "tests/unit/test_shadow_outcome_persistence.py::test_preview_automatic_matrix_preserves_human_policy_and_records_outcomes",
    "tests/unit/test_provider_recovery.py::test_activation_replay_after_ack_failure_keeps_recovery_state_idempotent",
)


def _baseline() -> dict[str, int]:
    connection = sqlite3.connect(":memory:")
    try:
        _create_schema(connection)
        return {
            name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def run() -> int:
    """Run the bounded scenarios and print only public aggregate row deltas."""
    baseline = _baseline()
    states: dict[tuple[str, str], dict[str, int]] = {}
    current = "bootstrap"
    original_connect = sqlite3.connect

    captured_nodes: set[str] = set()

    class AuditConnection(sqlite3.Connection):
        def close(self) -> None:
            try:
                tables = {
                    row[0]
                    for row in self.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if set(baseline).issubset(tables):
                    _validate_schema(self)
                    name = self.execute("PRAGMA database_list").fetchone()[2]
                    states[current, name] = {
                        table: self.execute(
                            f'SELECT count(*) FROM "{table}"'
                        ).fetchone()[0]
                        for table in baseline
                    }
                    captured_nodes.add(current)
            finally:
                super().close()

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = AuditConnection
        return original_connect(*args, **kwargs)

    class Plugin:
        def pytest_runtest_setup(self, item: pytest.Item) -> None:
            nonlocal current
            current = item.nodeid

    sqlite3.connect = connect  # type: ignore[assignment]
    started = time.monotonic()
    try:
        exit_code = pytest.main(["-q", *TEST_NODES], plugins=[Plugin()])
    finally:
        sqlite3.connect = original_connect
    elapsed = time.monotonic() - started
    missing = sorted(set(TEST_NODES) - captured_nodes)
    if missing:
        raise RuntimeError("row delta audit captured no exact-schema final state")
    for test in TEST_NODES:
        delta: Counter[str] = Counter()
        for (owner, _), rows in states.items():
            if owner == test:
                delta.update(
                    {table: count - baseline[table] for table, count in rows.items()}
                )
        print(
            "DELTA",
            test,
            ",".join(
                f"{table}={count}" for table, count in sorted(delta.items()) if count
            ),
            sep="\t",
        )
    print("ELAPSED_SECONDS", f"{elapsed:.2f}", sep="\t")
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(run())
