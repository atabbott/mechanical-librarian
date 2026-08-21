"""Public-safe workload audit command contract."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def _module() -> object:
    path = Path("scripts/audit_sqlite_row_deltas.py")
    specification = importlib.util.spec_from_file_location("row_delta_audit", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_row_delta_audit_names_eight_public_persistence_scenarios() -> None:
    path = Path("scripts/audit_sqlite_row_deltas.py")
    module = _module()

    assert len(module.TEST_NODES) == 8
    assert all(node.startswith("tests/unit/") for node in module.TEST_NODES)
    assert "set_trace_callback" not in path.read_text(encoding="utf-8")


def test_row_delta_audit_emits_committed_delta_for_real_public_workload() -> None:
    completed = subprocess.run(
        (sys.executable, "scripts/audit_sqlite_row_deltas.py"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    deltas = [
        line for line in completed.stdout.splitlines() if line.startswith("DELTA\t")
    ]
    assert len(deltas) == 8
    assert any("review_case_field_facts=16" in line for line in deltas)
    assert any("cover_considerations=2" in line for line in deltas)


def test_row_delta_audit_rejects_missing_exact_schema_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module.pytest, "main", lambda *_args, **_kwargs: 0)

    with pytest.raises(RuntimeError, match="captured no exact-schema final state"):
        module.run()
