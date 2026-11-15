"""An owner pin keeps one field's value and stops the question (#1346).

ADR-0085, Amendment 2: a `pin` decision sets the field's manual hold with a
digest of the case's value. Every later case of the work compares its value
with that digest, so the field is neither asked nor decided automatically
while the value stays, and asks again, showing the pinned value, once
Audiobookshelf holds something else. Each test here fails before the change,
where neither outcome existed. Synthetic fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import FakeCapabilityObserver
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    FIELD_EFFECT_MANUAL_HOLD_SCHEMA,
    _validate_field_effect_state,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    authority_from_decision,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
)
from mechanical_librarian.domain.human_decision import HumanDecisionError
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.genre_reports import genre_report
from tests.support.owner_values import CAPABILITY, OwnerStore, rewritten
from tests.unit.test_owner_answer_holds import (
    ORIGINAL,
    _automatic,
    _field,
    _hold,
    _queued,
    _store,
)

ORCHARD = {"kind": "text", "value": "The Silent Orchard"}
ANOTHER = "Another Example Title"
FANTASY = {"kind": "list", "value": ["Fantasy"]}


def _hold_document(
    store: OwnerStore, destination: str = "title", case_id: str | None = None
) -> dict[str, object] | None:
    row = store.repository._connection.execute(
        "SELECT document FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination=?",
        (case_id or store.case.review_case_id, destination),
    ).fetchone()
    return None if row is None else json.loads(row[0])


def _pin(store: OwnerStore, destination: str = "title") -> object:
    document = _hold_document(store, destination)
    return None if document is None else document["pin"]


def _asked(store: OwnerStore, destination: str = "title") -> bool:
    """Whether the work asks about the field, live and in the persisted facts,
    which must agree."""
    connection = store.repository._connection
    target = store.case.target
    key = (target["connection"], target["library"], target["item"], destination)
    live = connection.execute(
        "SELECT count(*) FROM (" + _ATTENTION_FACT_SOURCE_SQL + ") derived "
        "WHERE connection_name=? AND library_name=? AND item_id=? AND destination=?",
        key,
    ).fetchone()[0]
    persisted = connection.execute(
        "SELECT count(*) FROM review_attention_facts WHERE connection_name=? "
        "AND library_name=? AND item_id=? AND destination=?",
        key,
    ).fetchone()[0]
    assert live == persisted, (destination, live, persisted)
    return bool(live)


def _rows(store: OwnerStore) -> tuple[int, int, int]:
    return store.repository._connection.execute(
        "SELECT (SELECT count(*) FROM human_decisions),"
        "(SELECT count(*) FROM human_decision_events),"
        "(SELECT count(*) FROM field_effect_manual_holds)"
    ).fetchone()


def _refused(store: OwnerStore, code: str, outcome: str, **kwargs) -> None:
    before = _rows(store), _hold_document(store, kwargs.get("destination", "title"))
    with pytest.raises(HumanDecisionError) as error:
        store.decide(outcome, **kwargs)
    assert error.value.code == code
    assert (
        _rows(store),
        _hold_document(store, kwargs.get("destination", "title")),
    ) == before


def _with_state(
    report: MetadataReport, destination: str, state: TargetFieldState, **values: str
) -> MetadataReport:
    """The work read again with one field in a value-free state."""
    changed = rewritten(report, **values)
    fields = tuple(
        TargetField(field.destination, state, None)
        if field.destination.value == destination
        else field
        for field in changed.target.fields
    )
    target = changed.target
    return MetadataReport.create(
        target=TargetFacts(target.connection, target.library, target.item, fields),
        file=changed.file,
        local_claims=changed.local_claims,
        local_reasons=changed.local_reasons,
        providers=changed.providers,
    )


def _library_pinned_count(store: OwnerStore) -> int:
    items = cast(
        "list[dict[str, Any]]",
        BrowseReviewLibrary(store.repository).library(
            lens="all", search=None, limit=25, cursor=None
        )["items"],
    )
    assert len(items) == 1
    return items[0]["pinned_count"]


@pytest.fixture
def genres(tmp_path: Path):
    store = OwnerStore(tmp_path, genre_report())
    yield store
    store.close()


# AC13: a pin already in force is refused.
def test_a_repeated_pin_is_refused(genres: OwnerStore) -> None:
    genres.decide("pin")
    _refused(genres, "already_pinned", "pin")


# AC14: unpin needs a pin.
def test_unpin_is_refused_without_a_pin(genres: OwnerStore) -> None:
    _refused(genres, "not_pinned", "unpin", destination="genres")
    genres.decide("keep_current", destination="genres")
    _refused(genres, "not_pinned", "unpin", destination="genres")


# AC12 and Q1: a pin waits until the owner's change is written and read back.
@pytest.mark.parametrize(
    "stage", ["planned", "in_flight", "verified", "chosen_unwritten"]
)
def test_a_pin_is_refused_while_a_change_waits(genres: OwnerStore, stage: str) -> None:
    decision = genres.decide("owner_entered", ORCHARD)
    if stage == "planned":
        genres.plan(decision)
    elif stage == "verified":
        genres.write(decision)
    if stage != "in_flight":
        _refused(genres, "pin_change_waiting", "pin")
        return
    operation = genres.plan(decision)
    refusals: list[str] = []

    def pin_while_applying(_plan) -> None:
        before = _rows(genres)
        try:
            genres.decide("pin")
        except HumanDecisionError as error:
            refusals.append(error.code)
        assert _rows(genres) == before

    genres.target.before_apply = pin_while_applying
    assert genres.service.apply(operation.plan.plan_id).state == "verified"
    assert refusals == ["pin_change_waiting"]


# Q1: a latest Keep current changes nothing, so it does not hold a pin back.
def test_a_pin_follows_keep_current_without_waiting(genres: OwnerStore) -> None:
    genres.decide("keep_current", destination="genres")
    pin = genres.decide("pin", destination="genres")
    assert _hold(genres, "genres") == pin.decision_id


def test_a_pin_is_accepted_once_the_written_value_is_read_back(
    genres: OwnerStore,
) -> None:
    genres.write(genres.decide("owner_entered", ORCHARD))
    genres.publish(rewritten(genre_report(), title=ORCHARD["value"]))
    pin = genres.decide("pin")
    assert _hold(genres) == pin.decision_id
    assert _pin(genres) == {
        "fingerprint": _pin(genres)["fingerprint"],
        "in_force": True,
    }


@pytest.mark.parametrize("outcome", ["pin", "unpin"])
def test_withdraw_is_refused_on_a_pin_or_unpin_row(
    genres: OwnerStore, outcome: str
) -> None:
    genres.decide("pin")
    if outcome == "unpin":
        genres.decide("unpin")
    _refused(genres, "nothing_to_withdraw", "withdraw")


# AC15 and D19: the restored pin is compared with this case's value again.
def test_withdrawing_an_answer_over_a_pin_restores_the_pin_compared_again(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    pin = store.decide("pin")
    pinned = _pin(store)
    assert pinned is not None and pinned["in_force"] is True
    store.publish(rewritten(report, title=ANOTHER))
    assert _pin(store) == {**pinned, "in_force": False}  # type: ignore[dict-item]  # reason: as above.
    answer = store.decide("owner_entered", {"kind": "text", "value": "Typed"})
    assert (_hold(store), _pin(store)) == (answer.decision_id, None)
    store.decide("withdraw")
    # The work's earlier hold is the pinned case's own row, in force there;
    # restored here it is compared with this case's title and reads changed.
    assert _hold(store) == pin.decision_id
    assert _pin(store) == {**pinned, "in_force": False}  # type: ignore[dict-item]  # reason: as above.
    assert _field(store, "title")["classification_cause"] == "pin_changed"
    store.close()


# D20 (R4): an answer given on the pinned case itself overwrote the pinned
# row, so withdrawing it leaves no earlier pin to restore.
def test_withdrawing_an_answer_on_the_pinned_case_restores_no_pin(
    genres: OwnerStore,
) -> None:
    genres.decide("pin")
    genres.decide("owner_entered", ORCHARD)
    genres.decide("withdraw")
    assert _hold(genres) is None
    assert _field(genres, "title")["pin"] is None


# AC14 and R3: unpin keeps the hold and its token, and moves updated_at so
# the work's latest hold, which the next case carries, has no pin.
def test_unpin_keeps_the_hold_and_its_token(genres: OwnerStore) -> None:
    pin = genres.decide("pin")
    unpin = genres.decide("unpin")
    document = _hold_document(genres)
    assert document is not None
    assert (_hold(genres), document["pin"], document["updated_at"]) == (
        pin.decision_id,
        None,
        unpin.occurred_at,
    )
    assert document["active"] is True
    genres.publish(rewritten(genre_report(), publisher="Changed Elsewhere"))
    assert (_hold(genres), _pin(genres)) == (pin.decision_id, None)


# AC15 and D18: any other answer replaces the hold, without a pin.
@pytest.mark.parametrize(
    "outcome,value",
    [("accept", FANTASY), ("keep_current", None), ("owner_entered", FANTASY)],
)
def test_choosing_another_value_ends_the_pin(
    genres: OwnerStore, outcome: str, value: dict[str, object] | None
) -> None:
    genres.decide("pin", destination="genres")
    assert _pin(genres, "genres") is not None
    answer = genres.decide(outcome, value, destination="genres")
    assert (_hold(genres, "genres"), _pin(genres, "genres")) == (
        answer.decision_id,
        None,
    )


# R5: the authored description's second hold write keeps the pin.
def test_a_pinned_description_keeps_its_pin_over_an_authored_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.domain.human_decision import HumanDecision
    from mechanical_librarian.domain.review_case import ReviewCase
    from tests.support.description_advice import (
        _prepared,
        qualify_description_advice,
    )

    qualify_description_advice(monkeypatch)
    repository, store, binding, original, _ = _prepared(tmp_path)
    store.admit(original.candidate_id, binding, "human", "e" * 64, 11)
    connection = repository._connection
    assert connection.execute(
        "SELECT count(*) FROM current_description_decisions WHERE review_case_id=?",
        (binding.review_case_id,),
    ).fetchone() == (1,)
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(binding.review_case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "description"
    )
    pin = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="description",
        actor="owner",
        outcome="pin",
        selected_value=None,
        idempotency_key="owner-pins-description",
        occurred_at=12,
    )
    repository.record_human_decision(pin)
    assert connection.execute(
        "SELECT count(*) FROM current_description_decisions WHERE review_case_id=?",
        (binding.review_case_id,),
    ).fetchone() == (0,)
    document = json.loads(
        connection.execute(
            "SELECT document FROM field_effect_manual_holds WHERE review_case_id=? "
            "AND destination='description'",
            (binding.review_case_id,),
        ).fetchone()[0]
    )
    assert document["decision_id"] == pin.decision_id
    assert document["pin"] is not None and document["pin"]["in_force"] is True
    repository.close()


# AC5 and D5: a value edited elsewhere asks again, with the pinned value, and
# Automatic still records nothing.
def test_a_changed_value_asks_again_with_the_pinned_value(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    pinned = store.case.review_case_id
    store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    assert store.case.review_case_id != pinned
    assert _pin(store)["in_force"] is False
    field = _field(store, "title")
    assert (field["classification"], field["classification_cause"]) == (
        "needs_attention",
        "pin_changed",
    )
    assert field["pin"] == {
        "state": "changed",
        "value": {"destination": "title", "state": "value", "value": ORIGINAL},
    }
    assert field["pin_control"] == {"pin": True, "unpin": True}
    assert _asked(store) is True
    assert [
        decision
        for decision in _automatic(store, "title")
        if decision.review_case_id == store.case.review_case_id
    ] == []
    assert _queued(store) is None
    store.close()


# AC5 and D6.
def test_a_value_that_returns_puts_the_pin_back_in_force(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    assert _pin(store)["in_force"] is False
    assert _asked(store) is True
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _pin(store)["in_force"] is True
    assert _asked(store) is False
    assert _field(store, "title")["classification_cause"] == "pinned"
    store.close()


# C.3 and D11: missing, null and empty are one empty value.
@pytest.mark.parametrize("state", [TargetFieldState.EMPTY, TargetFieldState.NULL])
def test_an_empty_field_pinned_empty_stays_pinned_however_it_is_stored(
    tmp_path: Path, state: TargetFieldState
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    subtitle = next(
        field for field in report.target.fields if field.destination.value == "subtitle"
    )
    assert subtitle.state is TargetFieldState.MISSING
    store.decide("pin", destination="subtitle")
    store.publish(_with_state(report, "subtitle", state, publisher="Changed Elsewhere"))
    assert _pin(store, "subtitle")["in_force"] is True
    assert _asked(store, "subtitle") is False
    field = _field(store, "subtitle")
    assert field["classification_reason"] == (
        "You pinned this field empty. Mechanical Librarian will not ask about it "
        "or fill it unless it changes in Audiobookshelf."
    )
    store.close()


# C.3 and D12 (R6): nothing can be compared with an unreadable value.
def test_an_unreadable_later_value_reads_as_changed(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.publish(_with_state(report, "title", TargetFieldState.UNLOADED))
    assert _pin(store)["in_force"] is False
    field = _field(store, "title")
    assert field["pin"]["state"] == "changed"
    # Nothing readable to pin again, so only Unpin is offered.
    assert field["pin_control"] == {"pin": False, "unpin": True}
    store.close()


# AC6 and D1.
def test_a_pinned_title_is_not_asked_again_under_ask_me(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    assert _asked(store) is True
    store.decide("pin")
    assert _asked(store) is False
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _asked(store) is False
    field = _field(store, "title")
    assert (field["classification"], field["classification_cause"]) == (
        "human_recorded",
        "pinned",
    )
    store.close()


# AC6 and D2: Automatic's abstention asks today; a pin stops it.
def test_a_pinned_title_is_not_asked_again_when_automatic_abstains(
    tmp_path: Path,
) -> None:
    store, _, _ = _store(tmp_path)
    report = isbn_title_report(empty_target_title=True, titles=(None,))
    store.publish(report)
    assert _field(store, "title")["classification_cause"] == "automatic_abstained"
    assert _asked(store) is True
    store.decide("pin")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _asked(store) is False
    assert _field(store, "title")["classification_cause"] == "pinned"
    store.close()


# AC11: a field with nothing to choose still asks once its pinned value changes.
def test_a_changed_pin_joins_attention_without_alternatives(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    publisher = _field(store, "publisher")
    assert publisher["classification"] == "unsupported"
    assert publisher["pin_control"] is None
    assert _asked(store, "publisher") is False
    # The API accepts a pin on a contextual row (R8).
    store.decide("pin", destination="publisher")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _asked(store, "publisher") is True
    field = _field(store, "publisher")
    assert (field["classification"], field["classification_cause"]) == (
        "needs_attention",
        "pin_changed",
    )
    store.close()


# AC11 and D10: a suspended field waits, as an ordinary question does.
def test_a_changed_pin_on_a_suspended_field_waits(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.repository.suspend_field_effect_capability(
        scope="destination",
        connection=report.target.connection,
        destination="title",
        reason="field_unavailable",
        changed_at=store.clock(),
    )
    store.publish(rewritten(report, title=ANOTHER))
    assert _pin(store)["in_force"] is False
    assert _asked(store) is False
    assert _field(store, "title")["classification_cause"] != "pin_changed"
    store.repository.clear_field_effect_capability_suspension(
        scope="destination",
        connection=report.target.connection,
        destination="title",
        reason="field_unavailable",
        observed_at=store.clock(),
    )
    assert _asked(store) is True
    store.close()


# AC4: the hold document's pin key is closed and exactly typed.
@pytest.mark.parametrize(
    "pin",
    [
        "missing_key",
        {"fingerprint": "a" * 64, "in_force": True, "extra": 1},
        {"fingerprint": "a" * 63, "in_force": True},
        {"fingerprint": "a" * 64, "in_force": 1},
        {"fingerprint": "a" * 64, "in_force": "true"},
    ],
    ids=[
        "missing_key",
        "extra_key",
        "short_fingerprint",
        "integer_in_force",
        "string_in_force",
    ],
)
def test_the_hold_document_refuses_a_malformed_pin(
    genres: OwnerStore, pin: object
) -> None:
    genres.decide("pin")
    connection = genres.repository._connection
    _validate_field_effect_state(connection)
    document = _hold_document(genres)
    assert document is not None
    assert document["schema_version"] == FIELD_EFFECT_MANUAL_HOLD_SCHEMA
    if pin == "missing_key":
        del document["pin"]
    else:
        document["pin"] = pin
    connection.execute("BEGIN")
    try:
        connection.execute(
            "UPDATE field_effect_manual_holds SET document=? "
            "WHERE review_case_id=? AND destination='title'",
            (canonical_effect_json(document), genres.case.review_case_id),
        )
        with pytest.raises(MetadataReportError) as refused:
            _validate_field_effect_state(connection)
    finally:
        connection.execute("ROLLBACK")
    cause = refused.value.__cause__
    assert isinstance(cause, FieldEffectError)
    assert cause.args[0] == "invalid_manual_hold"


# AC9 and AC16.
def test_a_pinned_field_reads_pinned_and_offers_unpin(genres: OwnerStore) -> None:
    assert _field(genres, "title")["pin_control"] == {"pin": True, "unpin": False}
    genres.decide("pin")
    field = _field(genres, "title")
    assert field["pin"] == {"state": "in_force", "value": field["current"]}
    assert field["pin_control"] == {"pin": False, "unpin": True}
    assert field["manual_hold"] is False
    assert (field["classification"], field["classification_cause"]) == (
        "human_recorded",
        "pinned",
    )
    assert field["classification_reason"] == (
        "You pinned this value. Mechanical Librarian will not ask about it or "
        "change it unless it changes in Audiobookshelf."
    )
    assert _field(genres, "cover")["pin_control"] is None


# AC9 and D21: a pin is never released by Resume; Unpin is the control.
def test_resume_is_refused_while_pinned(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    assert _automatic(store, "title")[-1].review_case_id == store.case.review_case_id
    pin = store.decide("pin")
    # A held answer beside an automatic decision would show Resume here.
    assert _hold(store) == pin.decision_id
    assert _field(store, "title")["manual_hold"] is False
    # The SQLite store serves every port in the served composition; the cast
    # stands in for the composition's separate session store.
    repository = cast("Any", store.repository)
    library = (report.target.connection, report.target.library)
    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda value: False,
        session_digest=lambda token: token,
        client_digest=lambda value: value,
        clock=store.clock,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        field_application=ContinuousGuardedApplication(
            repository,
            {library: store.service},
            {library: FakeCapabilityObserver(CAPABILITY)},
            PersistedFieldEffectAuthority(repository),
            (library,),
            store.clock,
            lambda *_: None,
        ),
    )
    with pytest.raises(GuardedApplicationError) as refused:
        application.return_to_automatic(store.case.review_case_id, "title")
    assert refused.value.args[0] == "manual_hold"
    assert _hold(store) == pin.decision_id
    store.close()


# AC11 and D9.
def test_the_changed_field_suggests_the_pinned_value(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    control = _field(store, "title")["owner_control"]
    assert {"rule": "pinned", "value": ORIGINAL} in control["entry"]["suggestions"]
    answer = store.decide("owner_entered", {"kind": "text", "value": ORIGINAL})
    assert (_hold(store), _pin(store)) == (answer.decision_id, None)
    store.close()


# AC12: the page offers no pin while a change of the field waits.
def test_pin_control_is_withheld_while_a_change_waits(genres: OwnerStore) -> None:
    decision = genres.decide("owner_entered", ORCHARD)
    assert _field(genres, "title")["pin_control"] is None
    genres.plan(decision)
    assert _field(genres, "title")["pin_control"] is None
    assert _field(genres, "genres")["pin_control"] == {"pin": True, "unpin": False}


# AC17: each work counts its pinned fields.
def test_the_library_counts_pinned_fields(genres: OwnerStore) -> None:
    assert _library_pinned_count(genres) == 0
    genres.decide("pin")
    assert _library_pinned_count(genres) == 1
    genres.decide("pin", destination="genres")
    assert _library_pinned_count(genres) == 2
    genres.decide("unpin")
    assert _library_pinned_count(genres) == 1


# AC7 and D24: the projection a title recovery requeues mints nothing over a
# pin carried to a later case.
def test_title_recovery_records_nothing_for_a_pinned_title(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.repository.pause_automatic_safety(True, store.clock())
    store.publish(report)
    pin = store.decide("pin")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert (_hold(store), _pin(store)["in_force"]) == (pin.decision_id, True)
    store.repository.pause_automatic_safety(False, store.clock())
    store.repository.persist_shadow_outcome(store.case.review_case_id, store.clock())
    assert _automatic(store, "title") == []
    store.close()


# AC3 and D29.
def test_pin_and_unpin_plan_nothing(genres: OwnerStore) -> None:
    pin = genres.decide("pin")
    unpin = genres.decide("unpin")
    repository = genres.repository
    pending = {
        json.loads(document)["decision_id"]
        for document in repository.pending_field_effect_decision_documents(32)
    }
    assert {pin.decision_id, unpin.decision_id}.isdisjoint(pending)
    assert repository.pending_field_effect_decision_counts() == ()
    assert authority_from_decision(pin) is None
    assert authority_from_decision(unpin, allow_corrective_keep_current=True) is None
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)


# AC7 and D3: a later case Automatic would decide records nothing for the
# pinned field, and nothing is queued.
def test_automatic_records_nothing_for_a_pinned_field(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    assert _queued(store) is not None
    store.decide("pin")
    assert _queued(store) is None
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert [
        decision
        for decision in _automatic(store, "title")
        if decision.review_case_id == store.case.review_case_id
    ] == []
    assert _queued(store) is None
    assert _asked(store) is False
    store.close()


# D7: a changed pin is pinned again at the new value.
def test_pin_this_value_pins_the_new_value(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    first = _pin(store)
    store.publish(rewritten(report, title=ANOTHER))
    again = store.decide("pin")
    second = _pin(store)
    assert _hold(store) == again.decision_id
    assert second["in_force"] is True
    assert second["fingerprint"] != first["fingerprint"]
    assert _field(store, "title")["pin"] == {
        "state": "in_force",
        "value": {"destination": "title", "state": "value", "value": ANOTHER},
    }
    assert _asked(store) is False
    store.close()


# D8 and D22: after Unpin the field asks as an ordinary field, and Resume is
# offered once Automatic records a decision for it.
def test_unpin_lets_the_field_be_asked_again(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.repository.pause_automatic_safety(True, store.clock())
    store.publish(report)
    pin = store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    store.decide("unpin")
    assert _field(store, "title")["pin"] is None
    assert _asked(store) is True
    store.repository.pause_automatic_safety(False, store.clock())
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert (_hold(store), _pin(store)) == (pin.decision_id, None)
    assert _automatic(store, "title")[-1].review_case_id == store.case.review_case_id
    assert _field(store, "title")["manual_hold"] is True
    store.close()


# D33: a replay with the same key returns the original pin.
def test_a_pin_replays_by_idempotency_key(genres: OwnerStore) -> None:
    pin = genres.decide("pin")
    before = _rows(genres)
    assert genres.repository.record_human_decision(pin) is not None
    assert _rows(genres) == before
    assert _hold(genres) == pin.decision_id


# D34: a pin made in Preview holds once Automatic is turned on.
def test_a_pin_in_preview_is_kept_after_automatic_is_turned_on(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, posture="preview")
    first = store.publish(report)
    assert _automatic(store, "title") == []
    pin = store.decide("pin")
    store.repository.activate_qualified_automatic(store.clock())
    store.repository.persist_shadow_outcome(first.review_case_id, store.clock())
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _automatic(store, "title") == []
    assert (_hold(store), _pin(store)["in_force"]) == (pin.decision_id, True)
    assert _asked(store) is False
    store.close()
