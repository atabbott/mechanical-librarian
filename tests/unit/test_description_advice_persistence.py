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


def _publish_successor(repository, binding):
    from mechanical_librarian.domain.metadata_policy import human_review_destinations
    from mechanical_librarian.domain.metadata_report import TargetField
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.automatic_no_change import _automatic_no_change_inputs
    from tests.support.proposal_evidence import _report

    original, *_ = _automatic_no_change_inputs("description_preserved")
    successor = _report(
        fields=tuple(
            TargetField(field.destination, field.state, "Observed successor text.")
            if field.destination.value == "description"
            else field
            for field in original.target.fields
        ),
        local_claims=original.local_claims,
        providers=original.providers,
    )
    resolved = repository.current_resolved_metadata_policy(successor.target.library)
    proposal = evaluate_report(
        successor, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(successor, lambda: True, expected_current=original.report_id)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    return successor, binding.model_copy(
        update={
            "review_case_id": case_id,
            "report_id": successor.report_id,
            "proposal_set_id": proposal.proposal_set_id,
            "target_fingerprint": successor.target.fingerprint,
        }
    )


@pytest.mark.parametrize(
    ("phase", "outcome"),
    [
        ("requested", "interrupted"),
        ("running", "success"),
        ("running", "failed"),
        ("running", "interrupted"),
    ],
)
def test_coalesced_retry_is_consumed_and_terminal_continuation_does_not_retry(
    tmp_path, phase, outcome
):
    from tests.support.description_advice import _prepared

    repository, store, binding, original, _ = _prepared(tmp_path)
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None
    if phase == "running":
        assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    _report, fresh = _publish_successor(repository, binding)
    store.request_retry(fresh.review_case_id, 14)
    assert store.pending_retry(fresh.review_case_id)
    assert (
        store.begin_attempt(
            original.request_identity,
            fresh,
            original.engine,
            original.model,
            15,
            lambda: store.binding_is_current(fresh),
            retry=True,
        )
        is None
    )
    assert repository._connection.execute(
        "SELECT retry FROM description_preparation_pending WHERE review_case_id=?",
        (fresh.review_case_id,),
    ).fetchone() == (0,)
    if outcome == "interrupted":
        if phase == "requested":
            assert (
                store.start(attempt[0], 16, lambda: store.binding_is_current(binding))
                is False
            )
        store.interrupt_recovered(16)
        candidate = None
    else:
        candidate = (
            DescriptionAdviceCandidate.create(
                **{
                    **original.model_dump(exclude={"candidate_id"}),
                    "attempt_id": attempt[0],
                    "created_at": 16,
                }
            )
            if outcome == "success"
            else None
        )
        assert store.finish(attempt[0], candidate, 16, lambda: False) is False
    before = store.read(fresh.review_case_id)
    assert before["current"] is (candidate is None)
    assert (before["candidate"] is not None) is (candidate is not None)
    reused = store.begin_attempt(
        original.request_identity,
        fresh,
        original.engine,
        original.model,
        17,
        lambda: store.binding_is_current(fresh),
    )
    assert reused == ((attempt[0], candidate) if candidate is not None else None)
    assert store.read(fresh.review_case_id)["current"] is True
    assert (store.read(fresh.review_case_id)["candidate"] is not None) is (
        candidate is not None
    )
    assert (
        repository._connection.execute(
            "SELECT 1 FROM description_preparation_pending WHERE review_case_id=?",
            (fresh.review_case_id,),
        ).fetchone()
        is None
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (2,)
    repository.close()


@pytest.mark.parametrize("replace_pointer", [False, True])
def test_late_completion_preserves_replaced_pointer_and_newer_retry(
    tmp_path, replace_pointer
):
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from mechanical_librarian.domain.description_advice import (
        DescriptionAdviceSettings,
        description_digest,
    )
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, original, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _r, _report, _d, access, calls: captured.append(
            (access, calls)
        ),
    )
    access, calls = captured[0]
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None
    assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    report, fresh = _publish_successor(repository, binding)
    assert (
        store.begin_attempt(
            original.request_identity,
            fresh,
            original.engine,
            original.model,
            14,
            lambda: store.binding_is_current(fresh),
        )
        is None
    )
    candidate = None
    if replace_pointer:
        settings = DescriptionAdviceSettings(remove_marketing=False)
        store.bind_settings(settings)
        replacement = fresh.model_copy(
            update={"preferences_digest": description_digest(settings.model_dump())}
        )
        candidate = DescriptionAdviceService(
            store, access, store.current_settings, lambda: 15
        ).prepare(report, replacement, lambda: store.binding_is_current(replacement))
        assert (
            candidate is not None
            and candidate.request_identity != original.request_identity
        )
        assert len(calls) == 2
    store.request_retry(fresh.review_case_id, 16)
    old_candidate = DescriptionAdviceCandidate.create(
        **{
            **original.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            "created_at": 17,
        }
    )
    assert store.finish(attempt[0], old_candidate, 17, lambda: False) is False
    assert store.read(fresh.review_case_id)["candidate"]["candidate_id"] == (
        candidate.candidate_id if candidate is not None else old_candidate.candidate_id
    )
    assert store.read(fresh.review_case_id)["current"] is replace_pointer
    assert repository._connection.execute(
        "SELECT retry,requested_at FROM description_preparation_pending WHERE review_case_id=?",
        (fresh.review_case_id,),
    ).fetchone() == (1, 16)
    repository.close()
