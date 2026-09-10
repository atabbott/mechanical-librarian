"""Real SQLite attempt state and immutable history proofs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceCandidate,
)


def test_attempt_persisted_before_dispatch_and_success_rebinds_without_call(
    tmp_path: Path,
) -> None:
    from tests.support.description_advice import _prepared

    repository, store, binding, original, _ = _prepared(tmp_path)
    path = tmp_path / "reports.sqlite"
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None and attempt[1] is None
    with sqlite3.connect(path) as observer:
        assert observer.execute(
            "SELECT state FROM description_advice_attempts ORDER BY rowid DESC LIMIT 1"
        ).fetchone() == ("requested",)
    assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    candidate = DescriptionAdviceCandidate.create(
        **{
            **original.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            "created_at": 14,
        }
    )
    assert store.finish(
        attempt[0], candidate, 14, lambda: store.binding_is_current(binding)
    )
    assert store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        15,
        lambda: store.binding_is_current(binding),
    ) == (attempt[0], candidate)
    with sqlite3.connect(path) as observer:
        assert observer.execute(
            "SELECT count(*) FROM description_advice_attempts"
        ).fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            observer.execute("UPDATE description_advice_candidates SET document='{}'")
    repository.close()
    reopened = open_repository(path, create=False)
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["state"]
        == "succeeded"
    )
    reopened.close()


def test_base_schema_is_rejected_without_mutation(tmp_path: Path) -> None:
    from mechanical_librarian.application.metadata_report import MetadataReportError

    path = tmp_path / "predecessor.sqlite"
    objects = json.loads(
        (
            Path(__file__).parents[1]
            / "support/automatic_decision_predecessors/base-schema-before-f39.json"
        ).read_text()
    )
    with sqlite3.connect(path) as connection:
        for kind in ("table", "index", "trigger"):
            for actual_kind, _name, statement in objects:
                if actual_kind == kind and statement is not None:
                    connection.execute(statement)
    before = path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    assert path.read_bytes() == before
