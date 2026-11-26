"""Every owner answer holds its field from Automatic (ADR-0085, Amendment 1).

An owner's decision on a field always overrides Automatic for that field
(docs/product/automation-policy.md). Before the amendment a hold was set only
where an automatic decision sat on the same case as the answer, so an answer
given after Automatic's write had been read back, in Preview, while automatic
decisions were paused, or while the field was Ask me set no hold, and
Automatic wrote over it on a later read of the work. Each test here failed on
main 5c3606d, where the audit of automatic decision paths proved the defect,
except the two marked as kept green and the withdrawal cases that hold
nothing. Synthetic fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.genre_reports import genre_report
from tests.support.owner_field_record import (
    hold_token,
    record_recomputes,  # noqa: F401
    resume,
)
from tests.support.owner_values import OwnerStore, rewritten

ORIGINAL = "Example Series 03 - Example Title"  # what the library holds
SOURCES = "Example Title"  # what the sources agree on
OWN = {"kind": "text", "value": "My Own Title"}
GENRES = genre_report(current=("Drama",), genres=("Fantasy",))


def _profile(name: str, title: str) -> MetadataPolicyProfile:
    base = MetadataPolicyProfile.builtin()
    destinations = {
        key: "unsupported" if key == "explicit" else "ask"
        for key, _ in base.destinations
    }
    destinations["title_layout"] = title
    destinations["publisher_preference"] = "automatic"
    return MetadataPolicyProfile.create(name, dict(base.options), destinations)


def _activate(
    store: OwnerStore,
    profile: MetadataPolicyProfile,
    previous: MetadataPolicyProfile,
    library: str,
) -> None:
    repository = store.repository
    resolved = resolve_policy(profile, library)
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state"
    ).fetchone()[0]
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        previous.policy_id,
        ((library, resolved.resolved_id, resolved.canonical_json),),
        store.clock(),
        expected_active_generation=generation,
    )
    store.resolved = resolved


def _store(
    tmp_path: Path, *, title: str = "automatic", posture: str = "automatic"
) -> tuple[OwnerStore, MetadataReport, MetadataPolicyProfile]:
    report = isbn_title_report()
    store = OwnerStore(tmp_path, None)
    store.repository.enter_shadow_preview(1, ())
    profile = _profile("First policy", title)
    _activate(store, profile, MetadataPolicyProfile.builtin(), report.target.library)
    if posture == "automatic":
        store.repository.activate_qualified_automatic(store.clock())
    return store, report, profile


def _automatic(store: OwnerStore, destination: str) -> list[AutomaticDecision]:
    return [
        AutomaticDecision.from_json(row[0])
        for row in store.repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination=? "
            "ORDER BY rowid",
            (destination,),
        )
    ]


def _queued(store: OwnerStore, destination: str = "title") -> AutomaticDecision | None:
    """The automatic decision the scheduler would write next."""
    pending = {
        json.loads(document)["decision_id"]
        for document in store.repository.pending_field_effect_decision_documents(32)
    }
    queued = [
        value
        for value in _automatic(store, destination)
        if value.decision_id in pending
    ]
    return queued[-1] if queued else None


def _hold(store: OwnerStore, destination: str = "title") -> str | None:
    return hold_token(store.repository, store.case.review_case_id, destination)


def _field(store: OwnerStore, destination: str) -> dict[str, object]:
    detail = InspectCurrentReviewCase(store.repository).run(store.case.review_case_id)
    return next(
        value for value in detail["fields"] if value["destination"] == destination
    )


def _automatic_writes_first(store: OwnerStore, report: MetadataReport) -> None:
    store.publish(report)
    queued = _queued(store)
    assert queued is not None
    first = store.write(queued)
    assert (first.plan.pre_value, first.plan.desired_value) == (ORIGINAL, SOURCES)
    store.publish(rewritten(report, title=SOURCES))  # the read-back
    assert _queued(store) is None


def _held_from_automatic(store: OwnerStore, token: str) -> None:
    """The current case holds the field with the owner's answer, Automatic's
    decision on it waits for Resume, and the case shows Resume."""
    assert _hold(store) == token
    assert _queued(store) is None
    assert _automatic(store, "title")[-1].review_case_id == store.case.review_case_id
    assert _field(store, "title")["manual_hold"] is True


# 1. The ordinary case: Automatic changed the title, the owner types another.
def test_a_title_typed_after_an_automatic_change_stays(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    _automatic_writes_first(store, report)
    decision = store.decide("owner_entered", OWN)
    # The answer holds the field, and Resume shows wherever the field is
    # Automatic, with or without an automatic decision on this case (V1).
    assert _hold(store) == decision.decision_id
    assert _field(store, "title")["manual_hold"] is True
    store.write(decision)
    store.publish(rewritten(report, title=OWN["value"]))  # the read-back
    _held_from_automatic(store, decision.decision_id)
    store.publish(rewritten(report, title=OWN["value"], publisher="Changed Elsewhere"))
    _held_from_automatic(store, decision.decision_id)
    store.close()


# 2. The owner puts the original title back ("Use previous value").
def test_the_original_title_put_back_stays(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    _automatic_writes_first(store, report)
    decision = store.decide("owner_entered", {"kind": "text", "value": ORIGINAL})
    store.write(decision)
    store.publish(rewritten(report, title=ORIGINAL))  # the first case recurs
    assert _hold(store) == decision.decision_id
    assert _queued(store) is None
    store.publish(rewritten(report, title=ORIGINAL, publisher="Changed Elsewhere"))
    _held_from_automatic(store, decision.decision_id)
    store.close()


# 3. Kept in Preview, then Automatic is turned on.
def test_a_title_kept_in_preview_stays_after_automatic_is_turned_on(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, posture="preview")
    first = store.publish(report)
    assert _automatic(store, "title") == []
    decision = store.decide("keep_current", destination="title")
    store.repository.activate_qualified_automatic(store.clock())
    store.repository.persist_shadow_outcome(first.review_case_id, store.clock())
    assert _automatic(store, "title") == []  # the answered case stays the owner's
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    _held_from_automatic(store, decision.decision_id)
    store.close()


# 4. Typed in Preview and written, then Automatic is turned on.
def test_a_title_typed_in_preview_stays_after_automatic_is_turned_on(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    decision = store.decide("owner_entered", OWN)
    store.write(decision)
    store.repository.activate_qualified_automatic(store.clock())
    store.publish(rewritten(report, title=OWN["value"]))  # the read-back
    _held_from_automatic(store, decision.decision_id)
    store.close()


# 5. Kept while automatic decisions were paused, then resumed.
def test_a_title_kept_while_paused_stays_after_resume(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.repository.pause_automatic_safety(True, store.clock())
    store.publish(report)
    assert _automatic(store, "title") == []
    decision = store.decide("keep_current", destination="title")
    store.repository.pause_automatic_safety(False, store.clock())
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    _held_from_automatic(store, decision.decision_id)
    store.close()


# 6. Kept while Title was Ask me, then Title is set to Automatic.
def test_a_title_kept_under_ask_me_stays_once_title_is_automatic(
    tmp_path: Path,
) -> None:
    store, report, ask = _store(tmp_path, title="ask")
    store.publish(report)
    assert _automatic(store, "title") == []
    decision = store.decide("keep_current", destination="title")
    assert _field(store, "title")["manual_hold"] is False
    _activate(store, _profile("Second policy", "automatic"), ask, report.target.library)
    store.publish(report)  # prepared again under the new policy, no re-read
    _held_from_automatic(store, decision.decision_id)
    store.close()


# 7. Genres kept in Preview, then Automatic is turned on (the rich engine).
def test_genres_kept_in_preview_stay_after_automatic_is_turned_on(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, None)
    library = GENRES.target.library
    options = dict(DEFAULT_OPTIONS)
    options["genres"] = "replace"
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["genres"] = "automatic"
    profile = MetadataPolicyProfile.create("Genres automatic", options, destinations)
    store.resolved = resolve_policy(profile, library)
    repository = store.repository
    repository.enter_shadow_preview(1, ())
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((library, store.resolved.resolved_id, store.resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    first = store.publish(GENRES)
    decision = store.decide("keep_current", destination="genres")
    repository.activate_qualified_automatic(store.clock())
    repository.persist_shadow_outcome(first.review_case_id, store.clock())
    assert _queued(store, "genres") is None
    store.publish(rewritten(GENRES, publisher="Changed Elsewhere"))
    assert _hold(store, "genres") == decision.decision_id
    assert _queued(store, "genres") is None
    assert _field(store, "genres")["manual_hold"] is True
    store.close()


# Kept green from the audit: genres chosen after Automatic's write stay.
def test_genres_chosen_after_an_automatic_change_stay(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, None)
    store.automatic("genres", "replace", GENRES.target.library)
    store.publish(GENRES)
    queued = _queued(store, "genres")
    assert queued is not None
    first = store.write(queued)
    assert (first.plan.pre_value, first.plan.desired_value) == (["Drama"], ["Fantasy"])
    store.publish(rewritten(GENRES, genres=("Fantasy",)))  # the read-back
    decision = store.decide(
        "owner_entered", {"kind": "list", "value": ["Drama"]}, destination="genres"
    )
    store.write(decision)
    assert _hold(store, "genres") == decision.decision_id
    store.publish(rewritten(GENRES, genres=("Drama",)))
    assert _queued(store, "genres") is None
    store.publish(rewritten(GENRES, genres=("Drama",), publisher="Changed Elsewhere"))
    assert _queued(store, "genres") is None
    store.close()


# Kept green from the audit: a pause stops a write Automatic had decided.
def test_a_pause_refuses_a_write_already_decided(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    queued = _queued(store)
    assert queued is not None
    operation = store.plan(queued)
    store.repository.pause_automatic_safety(True, store.clock())
    with pytest.raises(GuardedApplicationError) as refused:
        store.service.apply(operation.plan.plan_id)
    assert refused.value.args[0] == "authority"
    assert store.patches == []
    store.close()


# A withdrawal holds nothing: with no earlier hold the field is as before.
def test_a_withdrawn_answer_leaves_no_hold(tmp_path: Path) -> None:
    store, report, ask = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("keep_current", destination="title")
    store.decide("withdraw", destination="title")
    assert _hold(store) is None
    _activate(store, _profile("Second policy", "automatic"), ask, report.target.library)
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _hold(store) is None
    assert _queued(store) is not None
    store.close()


# A withdrawal gives the field back to the work's earlier hold, which the
# withdrawn answer had replaced on this case.
def test_a_withdrawn_answer_restores_the_earlier_hold(tmp_path: Path) -> None:
    store, report, ask = _store(tmp_path, title="ask")
    store.publish(report)
    earlier = store.decide("keep_current", destination="title")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _hold(store) == earlier.decision_id  # carried to the successor
    later = store.decide("owner_entered", OWN)
    assert _hold(store) == later.decision_id
    store.decide("withdraw", destination="title")
    assert _hold(store) == earlier.decision_id
    _activate(store, _profile("Second policy", "automatic"), ask, report.target.library)
    store.publish(rewritten(report, publisher="Changed Again"))
    _held_from_automatic(store, earlier.decision_id)
    store.close()


# Resume releases the answer's hold, and Automatic acts on the field again.
def test_resume_releases_an_answer_held_without_an_automatic_decision(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    decision = store.decide("keep_current", destination="title")
    store.repository.activate_qualified_automatic(store.clock())
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    _held_from_automatic(store, decision.decision_id)
    assert resume(store.repository, store.case.review_case_id, "title", store.clock())
    assert _hold(store) is None
    assert _queued(store) is not None
    store.close()


# A withdrawal that leaves the field no answer closes the Resume still out
# over it, naming the withdrawal, so no return finishes on an answer the
# owner took back (ADR-0108).
def test_a_withdrawal_leaving_no_answer_closes_its_pending_return(
    tmp_path: Path,
) -> None:
    # Answered in Preview, so Automatic decided nothing here; Resume shows
    # once Automatic is on and the field is Automatic.
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    decision = store.decide("keep_current", destination="title")
    store.repository.activate_qualified_automatic(store.clock())
    scan = store.repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "library-ebooks",
        "a" * 64,
        store.clock(),
    )
    at = store.clock()
    assert store.repository.request_owner_field_resume(
        store.case.review_case_id, "title", decision.decision_id, scan.scan_id, at, at
    )
    withdrawal = store.decide("withdraw", destination="title")
    assert _hold(store) is None
    assert store.repository._connection.execute(
        "SELECT kind,answer_id FROM owner_field_events ORDER BY rowid"
    ).fetchall() == [
        ("resume_requested", decision.decision_id),
        ("resume_superseded", withdrawal.decision_id),
    ]
    assert _field(store, "title")["manual_hold"] is False
    store.close()


# A withdrawal still keeps Automatic off the field, also where it gives the
# field back to the work's earlier hold on a case Automatic decided in an
# earlier generation (ADR-0085, Amendment 4).
def test_a_withdrawal_that_restores_an_earlier_hold_still_refuses_automatic(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    earlier = store.decide("keep_current", destination="title")
    successor = rewritten(report, publisher="Changed Elsewhere")
    store.publish(successor)
    assert _hold(store) == earlier.decision_id  # carried to the successor
    decided = _automatic(store, "title")[-1]
    assert decided.review_case_id == store.case.review_case_id
    store.decide("owner_entered", OWN)
    # Leaving Automatic re-admits the case in Preview; Automatic's decision is
    # then of an earlier generation, so the answer can be withdrawn, which
    # gives the field back to the earlier hold.
    assert store.repository.enter_shadow_preview(store.clock(), ()) == "preview"
    store.publish(successor)
    assert store.case.review_case_id == decided.review_case_id
    store.decide("withdraw", destination="title")
    assert _hold(store) == earlier.decision_id
    # The preparation pipeline settles the case's reconciliation, then
    # Automatic is turned on again: the withdrawal still keeps it off the field.
    connection = store.repository._connection
    (transition,) = connection.execute(
        "SELECT transition_id FROM metadata_policy_transitions "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    connection.execute(
        "UPDATE metadata_policy_transition_items SET state='complete' "
        "WHERE transition_id=?",
        (transition,),
    )
    connection.execute(
        "UPDATE metadata_policy_transitions SET state='completed',completed=total,failed=0 "
        "WHERE transition_id=?",
        (transition,),
    )
    connection.commit()
    store.repository.activate_qualified_automatic(store.clock())
    store.repository.persist_shadow_outcome(store.case.review_case_id, store.clock())
    assert _automatic(store, "title")[-1].decision_id == decided.decision_id
    assert _queued(store) is None
    store.close()
