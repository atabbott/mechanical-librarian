"""A description every source repeats is choosable beside its advice (#1905).

The field offers the adviser's text and Keep current while description advice
is shown for it, never enters Attention, and changes no persisted fact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    RecordHumanDecision,
    ReviewWebError,
)
from mechanical_librarian.domain.description_advice import AuthoredDescriptionDecision
from mechanical_librarian.domain.field_effect import field_value_version
from mechanical_librarian.domain.human_decision import HumanDecision
from tests.support.description_advice import (
    _guarded_application,
    _prepared,
    _preserved_description_report,
    qualify_description_advice,
)

REPEATED = (
    "Every source repeats your current description, so no review is needed. "
    "You can still keep it or choose the adviser's text."
)
_FACT = (
    "SELECT has_owner_decision_actions FROM review_case_field_facts "
    "WHERE review_case_id=? AND destination='description'"
)


def _ask_only(tmp_path: Path, **options):
    """The repeated description with no automatic decision on it."""
    return _prepared(
        tmp_path,
        report_override=_preserved_description_report(),
        ask_only_policy=True,
        automatic_policy=("cover_order", "fixed_source_order"),
        **{"automatic": False, **options},
    )


def _description(repository, case_id: str) -> dict:
    return next(
        field
        for field in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        if field["destination"] == "description"
    )


def _decide(
    repository,
    case_id: str,
    key: str,
    at: int,
    outcome: str = "keep_current",
    selected_value: dict[str, object] | None = None,
) -> HumanDecision:
    field = _description(repository, case_id)
    acknowledgement = RecordHumanDecision(repository, clock=lambda: at).run(
        case_id,
        proposal_id=field["proposal_id"],
        target_fingerprint=field["target_fingerprint"],
        destination="description",
        actor="owner",
        outcome=outcome,
        selected_value=selected_value,
        idempotency_key=key,
    )
    return HumanDecision.from_json(
        repository._connection.execute(
            "SELECT document FROM human_decisions WHERE decision_id=?",
            (acknowledgement["decision_id"],),
        ).fetchone()[0]
    )


def _retire_advice(store) -> None:
    """The adviser's model is no longer the one configured: its advice is
    out of date."""
    store.bind_identity(lambda _engine, _model: None)


def _no_attention(repository, case_id: str) -> None:
    assert repository._connection.execute(_FACT, (case_id,)).fetchone() == (0,)
    assert repository._connection.execute(
        "SELECT count(*) FROM review_attention_facts WHERE destination='description'"
    ).fetchone() == (0,)


def test_an_undecided_repeated_description_is_optional(tmp_path: Path) -> None:
    repository, _store, binding, candidate, _ = _ask_only(tmp_path)
    case_id = binding.review_case_id
    field = _description(repository, case_id)
    assert field["classification"] == "owner_choosable"
    assert field["classification_cause"] == "owner_choosable"
    assert field["classification_reason"] == REPEATED
    assert field["editable"] is True
    assert field["needs_attention"] is False
    assert field["navigation_role"] == "inspect"
    assert field["decision_actions"] == ["keep_current", "accept"]
    assert field["proposed_values"] == []
    assert field["preservation_only"] is True
    assert field["description_advice"]["current"] is True
    assert field["description_advice"]["candidate"]["candidate_id"] == (
        candidate.candidate_id
    )
    _no_attention(repository, case_id)
    repository.close()


def test_an_automatic_keep_on_a_repeated_description_becomes_changeable(
    tmp_path: Path,
) -> None:
    repository, _store, binding, _candidate, _ = _prepared(
        tmp_path, automatic=False, report_override=_preserved_description_report()
    )
    field = _description(repository, binding.review_case_id)
    assert field["automatic_decision"]["outcome"] == "keep_current"
    assert field["classification"] == "automatic_recorded"
    assert field["classification_reason"] == (
        "Automatic decided this field. You can change it here."
    )
    assert field["editable"] is True
    assert field["decision_actions"] == ["keep_current", "accept"]
    _no_attention(repository, binding.review_case_id)
    repository.close()


@pytest.mark.parametrize("advice", ["failed", "out_of_date"])
def test_without_current_advice_a_repeated_description_reads_as_before(
    tmp_path: Path, advice: str
) -> None:
    repository, store, binding, _candidate, _ = _ask_only(
        tmp_path,
        **(
            {"output_transform": lambda _response: "not json"}
            if advice == "failed"
            else {}
        ),
    )
    if advice == "out_of_date":
        _retire_advice(store)
    field = _description(repository, binding.review_case_id)
    assert field["description_advice"]["current"] is False or (
        field["description_advice"]["candidate"] is None
    )
    assert field["classification"] == "unsupported"
    assert field["classification_cause"] == "nothing_to_decide"
    assert field["classification_reason"] == "Nothing to decide for this field."
    assert field["editable"] is False
    assert field["decision_actions"] == []
    with pytest.raises(ReviewWebError) as refused:
        _decide(repository, binding.review_case_id, "keep-without-advice", 20)
    assert refused.value.code == "unsupported"
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone() == (0,)
    repository.close()


def test_keep_current_holds_writes_nothing_and_blocks_automatic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qualify_description_advice(monkeypatch)
    repository, store, binding, candidate, _ = _ask_only(tmp_path, automatic=True)
    case_id = binding.review_case_id
    keep = _decide(repository, case_id, "keep-repeated-description", 20)
    assert keep.outcome == "keep_current"
    assert keep.selected_value is None
    # Nothing was written before it, so it writes nothing.
    assert keep.to_json() not in repository.pending_field_effect_decision_documents(100)
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)
    with pytest.raises(ValueError, match="description automatic admission unavailable"):
        store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 21)
    field = _description(repository, case_id)
    assert field["classification"] == "human_recorded"
    assert field["classification_reason"] == (
        "You decided this field. You can change it here."
    )
    assert field["human_decision"]["outcome"] == "keep_current"
    assert field["editable"] is True
    _no_attention(repository, case_id)
    repository.close()


def test_keep_current_reverses_the_owner_choosing_the_advice(tmp_path: Path) -> None:
    repository, store, binding, candidate, _ = _ask_only(tmp_path)
    case_id = binding.review_case_id
    chosen = store.admit(candidate.candidate_id, binding, "human", "b" * 64, 20)
    assert _description(repository, case_id)["description_advice"]["selection"][
        "decision_id"
    ] == (chosen.decision_id)
    _decide(repository, case_id, "keep-after-choosing-advice", 21)
    assert repository._connection.execute(
        "SELECT count(*) FROM current_description_decisions"
    ).fetchone() == (0,)
    assert any(
        event["kind"] == "description_selected" and event["state"] == "superseded"
        for event in repository.get_review_case_history(case_id, 50)
    )
    field = _description(repository, case_id)
    assert field["human_decision"]["outcome"] == "keep_current"
    assert field["description_advice"]["selection"] is None
    # Choosing the advice again revokes the keep.
    again = store.admit(candidate.candidate_id, binding, "human", "c" * 64, 22)
    field = _description(repository, case_id)
    assert field["classification"] == "human_recorded"
    assert field["description_advice"]["selection"]["decision_id"] == (
        again.decision_id
    )
    assert again.to_json() in repository.pending_field_effect_decision_documents(100)
    repository.close()


def test_keep_current_and_accept_are_refused_where_not_offered(tmp_path: Path) -> None:
    repository, store, binding, _candidate, _ = _ask_only(tmp_path)
    case_id = binding.review_case_id
    field = _description(repository, case_id)
    _retire_advice(store)
    for outcome in ("keep_current", "accept"):
        with pytest.raises(ReviewWebError) as refused:
            RecordHumanDecision(repository, clock=lambda: 20).run(
                case_id,
                proposal_id=field["proposal_id"],
                target_fingerprint=field["target_fingerprint"],
                destination="description",
                actor="owner",
                outcome=outcome,
                selected_value=(
                    None
                    if outcome == "keep_current"
                    else {"kind": "text", "value": "A qualified description."}
                ),
                idempotency_key=f"refused-repeated-{outcome}",
            )
        assert refused.value.code == "unsupported"
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone() == (0,)
    repository.close()


def test_a_retained_keep_stays_valid_after_the_advice_goes(tmp_path: Path) -> None:
    repository, store, binding, _candidate, _ = _ask_only(tmp_path)
    case_id = binding.review_case_id
    keep = _decide(repository, case_id, "keep-before-advice-goes", 20)
    _retire_advice(store)
    field = _description(repository, case_id)
    assert field["human_decision"]["decision_id"] == keep.decision_id
    assert field["classification"] == "human_recorded"
    assert field["classification_reason"] == "You decided this field."
    assert field["editable"] is False
    assert field["decision_actions"] == []
    repository._connection.execute(
        "DELETE FROM current_description_advice WHERE review_case_id=?", (case_id,)
    )
    repository._connection.commit()
    assert _description(repository, case_id)["human_decision"]["decision_id"] == (
        keep.decision_id
    )
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    assert reopened._connection.execute(
        "SELECT count(*) FROM human_decisions WHERE decision_id=?",
        (keep.decision_id,),
    ).fetchone() == (1,)
    reopened.close()


@pytest.mark.parametrize("origin", ["human", "automatic_policy"])
@pytest.mark.parametrize("report", ["offered", "repeated"])
def test_keep_current_writes_back_after_the_advice_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: str, origin: str
) -> None:
    from tests.support.field_effects import CAPABILITY

    qualify_description_advice(monkeypatch)
    repository, store, binding, candidate, _ = _prepared(
        tmp_path,
        automatic=origin == "automatic_policy",
        report_override=(
            _preserved_description_report() if report == "repeated" else None
        ),
    )
    case_id = binding.review_case_id
    decision = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    application, target = _guarded_application(repository, tmp_path, authority)

    def apply(chosen, *, corrective: bool = False) -> str:
        resolved = authority.resolve(chosen)
        operation = application.plan(
            chosen,
            connection=resolved.connection,
            library=resolved.library,
            work_id=resolved.work_id,
            destination=resolved.destination,
            pre_value=resolved.pre_value,
            desired_value=resolved.decision_value,
            target_fingerprint=resolved.target_fingerprint,
            target_version=resolved.target_version,
            dependency_digests=resolved.dependency_digests,
            capability_digest=CAPABILITY,
            allow_corrective_keep_current=corrective,
        )
        assert operation is not None
        if not corrective:
            target.seed(operation.plan)
        assert application.apply(operation.plan.plan_id).state == "verified"
        return target.read(operation.plan).value

    assert apply(decision) == "Authored synthetic description."
    assert store.read(case_id)["current"] is False
    # The verified write retired the advice; its selection still offers Keep
    # current, so the owner can reverse it.
    assert _description(repository, case_id)["decision_actions"] == [
        "keep_current",
        "accept",
    ]
    keep = _decide(repository, case_id, f"keep-after-{origin}-write", 40)
    assert keep.to_json() in repository.pending_field_effect_decision_documents(100)
    restored = authority.resolve(keep)
    assert restored.pre_value == "Authored synthetic description."
    assert restored.decision_value == {"kind": "text", "value": snapshot.pre_value}
    assert apply(keep, corrective=True) == snapshot.pre_value
    repository.close()


@pytest.mark.parametrize(
    "landed",
    [
        pytest.param("advice-then-owner", id="owner-write-landed-last"),
        pytest.param("advice-only", id="owner-write-went-stale"),
    ],
)
def test_a_description_decision_follows_the_write_that_landed_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landed: str
) -> None:
    """Between the case's own owner write and its advice write, the next
    decision follows the one that landed last, by landing order, not by
    which was recorded first or the clock (#1973).

    The owner's accept is recorded before the advice is chosen and both
    plans are bound to the same library value, so the advice lands first.
    The owner's plan is then either planned again and lands after it, or
    left as it was and goes stale. An advice write can never land after
    the owner's: a changing write retires the advice.
    """
    from tests.support.field_effects import CAPABILITY

    qualify_description_advice(monkeypatch)
    repository, store, binding, candidate, _ = _prepared(tmp_path, automatic=False)
    case_id = binding.review_case_id
    source = _description(repository, case_id)["proposed_values"][0]
    owner = _decide(
        repository, case_id, "accept-a-source-description", 11, "accept", source
    )
    advice = store.admit(candidate.candidate_id, binding, "human", "a" * 64, 12)
    authority = PersistedFieldEffectAuthority(repository)
    application, target = _guarded_application(repository, tmp_path, authority)

    def plan(chosen: HumanDecision | AuthoredDescriptionDecision) -> str:
        resolved = authority.resolve(chosen)
        operation = application.plan(
            chosen,
            connection=resolved.connection,
            library=resolved.library,
            work_id=resolved.work_id,
            destination=resolved.destination,
            pre_value=resolved.pre_value,
            desired_value=resolved.decision_value,
            target_fingerprint=resolved.target_fingerprint,
            target_version=resolved.target_version,
            dependency_digests=resolved.dependency_digests,
            capability_digest=CAPABILITY,
        )
        assert operation is not None
        target.seed(operation.plan)
        return operation.plan.plan_id

    owner_plan = plan(owner)
    assert application.apply(plan(advice)).state == "verified"
    if landed == "advice-then-owner":
        owner_plan = plan(owner)
    assert application.apply(owner_plan).state == (
        "verified" if landed == "advice-then-owner" else "stale"
    )
    expected = {"advice-then-owner": owner, "advice-only": advice}[landed]
    keep = _decide(repository, case_id, "keep-after-the-landed-write", 50)
    assert keep.follows == expected.decision_id
    restored = authority.resolve(keep)
    assert restored.pre_value == (
        source["value"]
        if landed == "advice-then-owner"
        else "Authored synthetic description."
    )
    repository.close()


def _description_of_snapshot(repository, case_id: str, **changes) -> dict:
    """The field read over a snapshot with these fields replaced."""
    from dataclasses import replace

    read = repository.read_current_review_case

    class Reader:
        def __getattr__(self, name):
            return getattr(repository, name)

        def read_current_review_case(self, *args, **kwargs):
            return replace(read(*args, **kwargs), **changes)

    return next(
        field
        for field in InspectCurrentReviewCase(Reader()).run(case_id)["fields"]
        if field["destination"] == "description"
    )


def _optional(field: dict) -> None:
    assert field["classification"] == "owner_choosable"
    assert field["classification_reason"] == REPEATED
    assert field["needs_attention"] is False
    assert field["navigation_role"] == "inspect"
    assert field["editable"] is True
    assert field["decision_actions"] == ["keep_current", "accept"]


def test_a_paused_automatic_does_not_ask_about_a_repeated_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paused before its shadow run, the field is optional, not Needs you."""
    import tests.support.description_advice as support

    original = support._automatic_decisions

    def paused(environment, *args, **kwargs):
        repository = environment.repository
        persist = repository.persist_shadow_outcome

        def pause_then_persist(case_id, at):
            repository.pause_automatic_safety(True, at)
            return persist(case_id, at)

        monkeypatch.setattr(repository, "persist_shadow_outcome", pause_then_persist)
        return original(environment, *args, **kwargs)

    monkeypatch.setattr(support, "_automatic_decisions", paused)
    repository, _store, binding, _candidate, _ = _prepared(
        tmp_path, automatic=False, report_override=_preserved_description_report()
    )
    case_id = binding.review_case_id
    snapshot = repository.read_current_review_case(case_id, None)
    assert "description" in snapshot.automatic_safety_suspended_destinations
    field = _description(repository, case_id)
    assert field["automatic_decision"] is None
    _optional(field)
    _no_attention(repository, case_id)
    repository.close()


def test_an_abstention_does_not_ask_about_a_repeated_description(
    tmp_path: Path,
) -> None:
    repository, _store, binding, _candidate, _ = _ask_only(tmp_path)
    case_id = binding.review_case_id
    _optional(
        _description_of_snapshot(
            repository, case_id, shadow_abstention_destinations=("description",)
        )
    )
    _no_attention(repository, case_id)
    repository.close()


def test_a_changed_offer_wins_over_the_keep_refusal(tmp_path: Path) -> None:
    repository, store, binding, _candidate, _ = _ask_only(tmp_path)
    case_id = binding.review_case_id
    field = _description(repository, case_id)
    _retire_advice(store)
    with pytest.raises(ReviewWebError) as refused:
        RecordHumanDecision(repository, clock=lambda: 20).run(
            case_id,
            proposal_id=field["proposal_id"],
            target_fingerprint=field["target_fingerprint"],
            destination="description",
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="keep-after-the-offer-changed",
            offered_alternatives_digest="0" * 64,
        )
    assert refused.value.code == "offer_changed"
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone() == (0,)
    repository.close()


def test_a_keep_on_an_offered_description_reads_no_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the repeated shape reads the field; a source offer does not."""
    repository, _store, binding, _candidate, _ = _prepared(tmp_path, automatic=False)
    case_id = binding.review_case_id
    assert repository._connection.execute(_FACT, (case_id,)).fetchone() == (1,)
    field = _description(repository, case_id)

    def unread(self, review_case_id):
        raise AssertionError("the keep read the page")

    monkeypatch.setattr(InspectCurrentReviewCase, "run", unread)
    acknowledgement = RecordHumanDecision(repository, clock=lambda: 20).run(
        case_id,
        proposal_id=field["proposal_id"],
        target_fingerprint=field["target_fingerprint"],
        destination="description",
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="keep-an-offered-description",
    )
    assert acknowledgement["outcome"] == "keep_current"
    repository.close()


@pytest.mark.parametrize("origin", ["automatic_policy", "human"])
def test_accepting_the_adviser_written_text_needs_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    """The adviser wrote the text a source offers; accepting that source's
    text records the owner's answer with nothing to write (ADR-0105)."""
    from tests.support.field_effects import CAPABILITY

    qualify_description_advice(monkeypatch)
    offered = "A qualified description."
    repository, store, binding, candidate, _ = _prepared(
        tmp_path, automatic=origin == "automatic_policy", description=offered
    )
    case_id = binding.review_case_id
    advice = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    application, target = _guarded_application(repository, tmp_path, authority)
    resolved = authority.resolve(advice)
    operation = application.plan(
        advice,
        connection=resolved.connection,
        library=resolved.library,
        work_id=resolved.work_id,
        destination=resolved.destination,
        pre_value=resolved.pre_value,
        desired_value=resolved.decision_value,
        target_fingerprint=resolved.target_fingerprint,
        target_version=resolved.target_version,
        dependency_digests=resolved.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    target.seed(operation.plan)
    assert application.apply(operation.plan.plan_id).state == "verified"
    assert _description(repository, case_id)["proposed_values"] == [
        {"kind": "text", "value": offered}
    ]

    accept = _decide(
        repository,
        case_id,
        f"accept-the-{origin}-advice-text",
        40,
        "accept",
        {"kind": "text", "value": offered},
    )
    assert accept.follows == advice.decision_id
    assert accept.to_json() not in repository.pending_field_effect_decision_documents(
        100
    )
    assert repository._connection.execute(
        "SELECT origin,target_version FROM field_effect_no_changes WHERE decision_id=?",
        (accept.decision_id,),
    ).fetchall() == [("human", field_value_version("description", offered))]
    assert _description(repository, case_id)["no_change_required"] is True
    repository.close()
