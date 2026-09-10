"""Real SQLite attempt state and immutable history proofs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceOutput,
    DescriptionAdviceSettings,
    description_digest,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.automatic_no_change import _automatic_no_change_inputs


def test_attempt_persisted_before_dispatch_and_success_rebinds_without_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite"
    repository = open_repository(path)
    report, *_ = _automatic_no_change_inputs("description_preserved")
    proposal = evaluate_report(report)
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case = json.loads(repository.admit_review_case(proposal))
    binding = DescriptionAdviceBinding(
        review_case_id=case["review_case_id"],
        report_id=report.report_id,
        proposal_set_id=proposal.proposal_set_id,
        target_fingerprint=report.target.fingerprint,
        policy_generation=1,
        access_revision=1,
        preferences_digest=description_digest(DescriptionAdviceSettings().model_dump()),
    )
    store = repository.description_advice_store()
    attempt = store.begin_attempt(
        "a" * 64, binding, "openrouter", "example/model", 1, lambda: True
    )
    assert attempt is not None and attempt[1] is None
    with sqlite3.connect(path) as observer:
        assert observer.execute(
            "SELECT state FROM description_advice_attempts"
        ).fetchone() == ("requested",)
    assert store.start(attempt[0], 2, lambda: True)
    candidate = DescriptionAdviceCandidate.create(
        attempt_id=attempt[0],
        request_identity="a" * 64,
        request_digest="b" * 64,
        instruction_digest=hashlib.sha256(DESCRIPTION_INSTRUCTION.encode()).hexdigest(),
        schema_digest=hashlib.sha256(DESCRIPTION_OUTPUT_SCHEMA.encode()).hexdigest(),
        preferences_digest="c" * 64,
        profile_digest="d" * 64,
        engine="openrouter",
        model="example/model",
        access_revision=1,
        output=DescriptionAdviceOutput(
            description="Authored synthetic text.", disposition="ready"
        ),
        created_at=3,
    )
    assert store.finish(attempt[0], candidate, 3, lambda: True)
    assert store.begin_attempt(
        "a" * 64, binding, "openrouter", "example/model", 4, lambda: True
    ) == (attempt[0], candidate)
    with sqlite3.connect(path) as observer:
        assert observer.execute(
            "SELECT count(*) FROM description_advice_attempts"
        ).fetchone() == (1,)
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
