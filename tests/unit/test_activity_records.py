"""What one Activity row says, and what it refuses to say.

Every case here drives the projection the installed page reads, so a change
that quietly stops naming a previous value, starts inventing one, or starts
reading the current catalogue fails here rather than in a screenshot.
"""

from __future__ import annotations

from typing import cast

import pytest

from mechanical_librarian.domain.activity_records import (
    activity_result_label,
    readable_activity_event,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    default_target_fields,
)
from mechanical_librarian.domain.readable_activity import (
    ABSENT,
    AUTOMATIC_AUTHORITY_UNAVAILABLE,
    CHANGE_LIMIT,
    EVIDENCE_LIMIT,
    ISBN_REFUSAL_REASONS,
    destination_label,
    keep_change,
    set_change,
    value_text,
)

_LONG = "An unbounded synthetic description. " * 40


def _report(fields: tuple[TargetField, ...]) -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Exact evaluator candidate",
        canonical="Exact evaluator candidate",
        excerpt="Exact evaluator candidate",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "books", "1234567890abcdef", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("opening_matter_title",),
        providers=(),
    )


def _titled(title: str) -> MetadataReport:
    return _report(default_target_fields(title))


def _with(destination: TargetDestination, value: object) -> MetadataReport:
    fields = tuple(
        TargetField(field.destination, TargetFieldState.VALUE, value)  # type: ignore[arg-type]
        if field.destination is destination
        else field
        for field in default_target_fields("Recorded work")
    )
    return _report(fields)


def _event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "destination": "title",
        "event_type": "review",
        "library": "books",
        "occurred_at": 1,
        "origin": "human",
        "state": "verified",
        "work_id": "1234567890abcdef",
    }
    event.update(overrides)
    return event


def _project(event: dict[str, object], report: MetadataReport | None = None):
    readable_activity_event(event, report)
    return event


def _facts(event: dict[str, object]) -> dict[str, str]:
    evidence = event["evidence"]
    assert isinstance(evidence, list)
    return {str(item["label"]): str(item["value"]) for item in evidence}


# --- the closed value vocabulary, rendered readably and safely ---------------


@pytest.mark.parametrize(
    ("state", "result"),
    (
        ("accept", "Accepted"),
        ("keep_current", "Kept current"),
        ("reject", "Rejected"),
        ("owner_entered", "Owner-entered"),
        ("withdraw", "Withdrawn"),
        ("completed", "Cover advice completed"),
        ("unavailable", "Cover advice unavailable"),
        ("failed", "Cover advice failed safely"),
        ("selected", "Owner cover choice recorded"),
        ("retry", "Manual cover retry"),
        ("no_change_required", "No change required"),
        ("applying", "Applying"),
        ("claimed", "Preparing"),
        ("planned", "Planned"),
        ("reconciling", "Checking"),
        ("retryable", "Waiting safely"),
        ("verified", "Verified"),
        ("stale", "Stale"),
        ("superseded", "Superseded"),
        ("abandoned", "Stopped safely"),
        ("unrecognized durable state", "Recorded"),
    ),
)
def test_each_durable_state_has_one_closed_owner_visible_result(
    state: str, result: str
) -> None:
    assert activity_result_label(state) == result
    projected = _project(_event(state=state))
    assert "result" not in projected


@pytest.mark.parametrize(
    ("failure", "result"),
    (
        ("connection", "Waiting safely"),
        ("connection_exhausted", "Stale"),
        ("capability", "Stale"),
    ),
)
def test_only_a_connection_stale_reads_as_waiting(failure: str, result: str) -> None:
    """A change waiting for its connection is not a stopped one (#1402)."""
    assert activity_result_label("stale", failure) == result


@pytest.mark.parametrize(
    ("destination", "value", "expected"),
    (
        ("title", "A Wizard of Earthsea", "A Wizard of Earthsea"),
        ("title", None, ABSENT),
        ("title", "", ABSENT),
        ("publishedDate", "1968-11-01", "1968-11-01"),
        ("authors", ["Ursula K. Le Guin", "雨の地図"], "Ursula K. Le Guin, 雨の地図"),
        ("authors", [], ABSENT),
        (
            "series",
            [
                {"name": "Earthsea Cycle", "sequence": "1"},
                {"name": "Hainish", "sequence": None},
            ],
            "Earthsea Cycle (1), Hainish",
        ),
        ("cover", "cover_" + "a" * 64, "A recorded cover image"),
        ("title", "line\nbreak\tand control", "line break and control"),
        ("title", {"unexpected": "shape"}, ABSENT),
        ("series", [{"name": 7, "sequence": None}], ABSENT),
    ),
    ids=(
        "scalar",
        "absent",
        "empty",
        "date",
        "list",
        "empty-list",
        "series-with-and-without-index",
        "opaque-cover",
        "control-characters",
        "unknown-shape",
        "malformed-series",
    ),
)
def test_every_durable_value_shape_renders_readably(
    destination: str, value: object, expected: str
) -> None:
    assert value_text(destination, value) == expected


def test_a_long_description_is_bounded_by_the_column_that_shows_it() -> None:
    compact = set_change("description", None, _LONG).compact
    assert len(compact) <= CHANGE_LIMIT + len(f"{ABSENT} → ")
    assert compact.endswith("…")
    fact = value_text("description", _LONG)
    assert len(fact) <= EVIDENCE_LIMIT
    assert fact.endswith("…")


def test_a_destination_is_named_the_way_the_owner_reads_it() -> None:
    assert destination_label("publishedYear") == "Published year"
    assert destination_label("isbn") == "ISBN"
    # An unmapped server destination is still spaced and sentence-cased rather
    # than shown as the camel-case token it arrived as.
    assert destination_label("narratorCredits") == "Narrator credits"


# --- the three decision sentences the ticket names --------------------------


def test_a_change_reads_as_a_sentence_and_as_a_column() -> None:
    change = set_change(
        "title", "Borrowed Weather", "A Field Guide to Borrowed Weather"
    )
    assert change.compact == "Borrowed Weather → A Field Guide to Borrowed Weather"
    assert (
        change.sentence
        == "Set Title from Borrowed Weather to A Field Guide to Borrowed Weather"
    )
    assert keep_change("authors", ["Ursula K. Le Guin"]).sentence == (
        "Kept Authors as Ursula K. Le Guin"
    )
    assert set_change("subtitle", "A subtitle", None).sentence == "Removed Subtitle"
    # An unchanged value is a keep, never a set from a value to itself.
    assert set_change("title", "Same", "Same").compact == "Kept Same"


# --- one row per durable source kind ----------------------------------------


def test_an_owner_acceptance_states_the_exact_before_and_after() -> None:
    report = _titled("Paper Moons: Larkspur")
    event = _project(
        _event(
            state="accept",
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": {
                    "kind": "text",
                    "value": "Paper Moons over Larkspur Station",
                },
            },
        ),
        report,
    )
    assert event["category"] == "owner"
    assert event["field_label"] == "Title"
    assert event["change"] == (
        "Paper Moons: Larkspur → Paper Moons over Larkspur Station"
    )
    assert event["change_detail"] == (
        "Set Title from Paper Moons: Larkspur to Paper Moons over Larkspur Station"
    )
    assert _facts(event) == {
        "Previous value": "Paper Moons: Larkspur",
        "New value": "Paper Moons over Larkspur Station",
        "Chosen by": "Library owner",
    }


def test_an_owner_keep_states_one_retained_value_and_no_new_one() -> None:
    report = _titled("The Dispossessed")
    event = _project(
        _event(
            state="keep_current",
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": {"kind": "text", "value": "The Dispossessed"},
            },
        ),
        report,
    )
    assert event["change"] == "Kept The Dispossessed"
    assert event["change_detail"] == "Kept Title as The Dispossessed"
    assert "New value" not in _facts(event)


def test_an_owner_rejection_prepares_nothing_and_invents_nothing() -> None:
    report = _titled("The Left Hand of Darkness")
    event = _project(
        _event(
            state="reject",
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": None,
            },
        ),
        report,
    )
    assert event["change"] == "Left unchanged"
    facts = _facts(event)
    assert facts["Current value"] == "The Left Hand of Darkness"
    assert facts["Outcome"] == "No change was prepared"
    assert "Previous value" not in facts and "New value" not in facts


def test_an_automatic_decision_names_the_policy_that_chose_it() -> None:
    report = _titled("A Wizard of Earthsea")
    event = _project(
        _event(
            destination="series",
            event_type="metadata",
            origin="automatic_policy",
            state="candidate",
            record={
                "source_kind": "automatic_decision",
                "target_fingerprint": report.target.fingerprint,
                "outcome": "candidate",
                "selected_value": {
                    "kind": "series",
                    "value": [{"name": "Earthsea Cycle", "sequence": "1"}],
                },
                "policy_category": "series",
                "policy_option": "primary_narrative",
                "policy_generation": 4,
            },
        ),
        report,
    )
    assert event["category"] == "automatic"
    assert event["change"] == "Set series to Earthsea Cycle (1)"
    assert event["change_detail"] == "Set Series from Not set to Earthsea Cycle (1)"
    assert _facts(event) == {
        "Previous value": ABSENT,
        "New value": "Earthsea Cycle (1)",
        "Chosen by": "Automatic policy",
        "Policy setting": "Series: Primary narrative",
        "Policy generation": "4",
    }


def test_an_automatic_keep_shows_the_unchanged_value_on_both_sides() -> None:
    report = _titled("The Farthest Shore")
    event = _project(
        _event(
            event_type="metadata",
            origin="automatic_policy",
            state="keep_current",
            record={
                "source_kind": "automatic_decision",
                "target_fingerprint": report.target.fingerprint,
                "outcome": "keep_current",
                "selected_value": None,
                "policy_category": "title_layout",
                "policy_option": "preserve",
                "policy_generation": 4,
            },
        ),
        report,
    )

    facts = _facts(event)
    assert event["change_detail"] == "Kept Title as The Farthest Shore"
    assert event["change"] == "Kept The Farthest Shore"
    assert facts["Previous value"] == "The Farthest Shore"
    assert facts["New value"] == "The Farthest Shore"
    assert facts["Policy setting"] == "Title layout: Leave as it is"


def test_unavailable_automatic_authority_has_one_value_free_diagnostic() -> None:
    event = _project(
        _event(
            event_type="metadata",
            origin="automatic_policy",
            state="candidate",
            record={
                "source_kind": "degraded",
                "degraded_reason": AUTOMATIC_AUTHORITY_UNAVAILABLE,
            },
        )
    )

    assert event["diagnostic"] == {
        "event_class": "automatic_decision",
        "reason": AUTOMATIC_AUTHORITY_UNAVAILABLE,
    }
    assert _facts(event) == {
        "Record status": "This retained event cannot be fully projected",
        "Disclosure": "The automatic authority is no longer available",
    }


def test_a_no_change_decision_says_the_value_already_matched() -> None:
    report = _titled("The Tombs of Atuan")
    event = _project(
        _event(
            event_type="metadata",
            origin="automatic_policy",
            state="no_change_required",
            record={
                "source_kind": "field_effect_no_change",
                "target_fingerprint": report.target.fingerprint,
            },
        ),
        report,
    )
    assert event["change"] == "Kept The Tombs of Atuan"
    assert _facts(event)["Confirmed in Audiobookshelf"] == "No change was needed"


@pytest.mark.parametrize(
    ("state", "confirmation"),
    (
        ("planned", "Not confirmed yet"),
        ("applying", "Not confirmed yet"),
        ("verified", "Matches the new value"),
    ),
    ids=("planned", "applying", "verified"),
)
def test_a_write_distinguishes_its_lifecycle_while_keeping_one_change(
    state: str, confirmation: str
) -> None:
    event = _project(
        _event(
            destination="series",
            event_type="metadata",
            origin="automatic_policy",
            state=state,
            record={
                "source_kind": "field_effect",
                "pre_value": None,
                "desired_value": [{"name": "Earthsea Cycle", "sequence": None}],
            },
        )
    )
    assert event["change"] == f"{ABSENT} → Earthsea Cycle"
    assert _facts(event)["Confirmed in Audiobookshelf"] == confirmation


@pytest.mark.parametrize(
    ("state", "failure", "compact", "reason"),
    (
        (
            "retryable",
            "target_changed",
            "Prepared change paused",
            "The current value changed after this was prepared",
        ),
        (
            "stale",
            "target_changed",
            "Prepared change closed",
            "The current value changed after this was prepared",
        ),
        (
            "abandoned",
            "owner_abandoned",
            "Prepared change closed",
            "You stopped this change",
        ),
    ),
    ids=("paused", "closed", "stopped"),
)
def test_a_stopped_write_says_why_and_never_claims_a_new_value(
    state: str, failure: str, compact: str, reason: str
) -> None:
    event = _project(
        _event(
            event_type="safety",
            state=state,
            record={
                "source_kind": "field_effect",
                "pre_value": "The Lathe of Heaven",
                "desired_value": "The Lathe of Heaven (1971)",
                "failure": failure,
            },
        )
    )
    assert event["category"] == "recovery"
    assert event["change"] == compact
    facts = _facts(event)
    assert facts["Prepared value"] == "The Lathe of Heaven (1971)"
    # A paused retry has not stopped, so it says what happened (#1402).
    assert facts["What happened" if state == "retryable" else "Why it stopped"] == (
        reason
    )
    assert ("Why it stopped" in facts) is (state != "retryable")
    assert "New value" not in facts
    assert (
        facts.get("What happens next")
        == "Mechanical Librarian checks the current value again"
    ) or state != "retryable"


@pytest.mark.parametrize(
    ("state", "failure", "compact", "reason", "next_step"),
    (
        (
            "retryable",
            "not_sent",
            "Prepared change paused",
            "This change was not sent to Audiobookshelf",
            "Mechanical Librarian checks the current value again",
        ),
        (
            "retryable",
            "not_applied",
            "Prepared change paused",
            "Audiobookshelf did not apply the change",
            "Mechanical Librarian checks the current value again",
        ),
        (
            "stale",
            "connection",
            "Prepared change waiting",
            "Audiobookshelf could not be reached for this book",
            "Mechanical Librarian prepares it again once Audiobookshelf can be "
            "reached. Prepared again 2 of 3 times.",
        ),
        (
            "stale",
            "connection_exhausted",
            "Prepared change closed",
            "Audiobookshelf could not be reached for this book, and this change "
            "stopped after it was prepared again 3 times",
            None,
        ),
    ),
    ids=("not_sent", "not_applied", "waiting", "bound"),
)
def test_an_unsent_or_unreachable_write_says_what_happened_and_what_follows(
    state: str, failure: str, compact: str, reason: str, next_step: str | None
) -> None:
    event = _project(
        _event(
            event_type="safety",
            state=state,
            record={
                "source_kind": "field_effect",
                "pre_value": "The Lathe of Heaven",
                "desired_value": "The Lathe of Heaven (1971)",
                "failure": failure,
                **({"connection_stales": 3} if failure == "connection" else {}),
            },
        )
    )
    assert event["change"] == compact
    facts = _facts(event)
    # A change that has not stopped never says why it stopped.
    stopped = state == "stale" and failure != "connection"
    assert facts["Why it stopped" if stopped else "What happened"] == reason
    assert ("Why it stopped" in facts) is stopped
    assert facts.get("What happens next") == next_step
    assert ("Confirmed in Audiobookshelf" in facts) is (
        failure == "connection_exhausted"
    )


def test_a_policy_row_carries_its_own_exact_from_and_to() -> None:
    changes = [
        {
            "field": "Title layout",
            "setting": "Review mode",
            "previous": "Ask me",
            "next": "Automatic",
        }
    ]
    event = _project(
        _event(
            destination="policy",
            event_type="policy",
            origin="policy",
            state="completed",
            record={
                "source_kind": "policy_transition",
                "generation": "Policy generation 2",
                "policy_changes": changes,
            },
        )
    )
    assert event["category"] == "policy"
    assert event["change"] == "1 change activated"
    assert event["policy_changes"] == changes
    assert _facts(event)["Active policy"] == "Updated with all 1 change"


def test_other_categories_never_inherit_policy_detail_bulk() -> None:
    event = _project(
        _event(
            record={
                "source_kind": "field_effect",
                "pre_value": "Before",
                "desired_value": "After",
            }
        )
    )
    # Only a Policy record grows the field; nothing else carries the key at
    # all, so no other category can inherit the bulk by defaulting to empty.
    assert "policy_changes" not in event
    assert len(_facts(event)) <= 4


@pytest.mark.parametrize(
    ("state", "compact"),
    (
        ("selected", "Chose a different cover"),
        ("kept_incumbent", "Kept the current cover"),
        ("unavailable", "Cover advice unavailable"),
        ("stale", "Prepared cover closed"),
    ),
    ids=("selected", "kept", "unavailable", "stale"),
)
def test_a_cover_record_describes_the_choice_rather_than_the_image(
    state: str, compact: str
) -> None:
    event = _project(
        _event(
            destination="cover",
            state=state,
            record={"source_kind": "cover_safeguard", "kind": state},
        )
    )
    assert event["change"] == compact
    assert "cover_" not in str(event["evidence"])


# --- the values are the event's, not the catalogue's -------------------------


def test_a_later_catalogue_change_does_not_move_a_historical_write() -> None:
    """A write's before and after come from its own event document.

    The same event is projected twice against two different reports -- the one
    it was made against and one recorded after the library moved on. If the
    projection ever reached for a current value, the second row would differ.
    """
    original = _titled("Paper Moons: Larkspur")
    moved_on = _titled("Something an editor renamed it to later")
    assert original.target.fingerprint != moved_on.target.fingerprint
    record = {
        "source_kind": "field_effect",
        "pre_value": "Paper Moons: Larkspur",
        "desired_value": "Paper Moons over Larkspur Station",
    }
    before = _project(_event(record=dict(record)), original)
    after = _project(_event(record=dict(record)), moved_on)
    assert before == after
    assert before["change"] == (
        "Paper Moons: Larkspur → Paper Moons over Larkspur Station"
    )


def test_a_decision_refuses_a_report_its_own_fingerprint_does_not_name() -> None:
    """A previous value is only ever the one the decision was made against.

    The decision's fingerprint is what admits the report. Hand it a different
    report and the row says the value is not recorded rather than reporting
    whatever that other report happened to hold.
    """
    made_against = _titled("Paper Moons: Larkspur")
    someone_elses = _titled("A different work entirely")
    record = {
        "source_kind": "human_decision",
        "target_fingerprint": made_against.target.fingerprint,
        "selected_value": {
            "kind": "text",
            "value": "Paper Moons over Larkspur Station",
        },
    }
    matched = _project(_event(state="accept", record=dict(record)), made_against)
    mismatched = _project(_event(state="accept", record=dict(record)), someone_elses)
    assert _facts(matched)["Previous value"] == "Paper Moons: Larkspur"
    assert _facts(mismatched)["Previous value"] == ABSENT
    assert "A different work entirely" not in str(mismatched)


def test_a_reported_series_reaches_the_row_with_its_index() -> None:
    report = _with(
        TargetDestination.SERIES,
        (TargetSeries("Earthsea Cycle", "1"), TargetSeries("Hainish", None)),
    )
    event = _project(
        _event(
            destination="series",
            state="keep_current",
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": None,
            },
        ),
        report,
    )
    assert event["change"] == "Kept Earthsea Cycle (1), Hainish"


def test_an_unrecognised_record_is_bounded_rather_than_guessed() -> None:
    event = _project(_event(event_type="unknown", origin="x" * 200, record=None))
    assert event["category"] == "recovery"
    assert event["change"] == "Recorded"
    assert _facts(event) == {"Chosen by": "Not recorded"}


# --- automatic choices name their field and value (#1281) ------------------


def _automatic(
    destination: str,
    outcome: str,
    report: MetadataReport | None,
    selected: object = None,
    *,
    chosen: bool = True,
) -> dict[str, object]:
    return _project(
        _event(
            destination=destination,
            event_type="metadata",
            origin="automatic_policy",
            state="recorded",
            record={
                "source_kind": "automatic_decision",
                "target_fingerprint": None
                if report is None
                else report.target.fingerprint,
                "outcome": outcome,
                "selected_value": {"kind": "text", "value": selected}
                if chosen
                else None,
                "policy_category": "title_layout",
                "policy_option": "preserve",
            },
        ),
        report,
    )


@pytest.mark.parametrize(
    ("destination", "outcome", "current", "selected", "chosen", "compact", "sentence"),
    (
        (
            "title",
            "keep_current",
            "Secret History",
            None,
            False,
            "Kept Secret History",
            "Kept Title as Secret History",
        ),
        (
            "isbn",
            "ignore",
            "9780306406157",
            None,
            False,
            "Left ISBN unchanged",
            "Left ISBN unchanged",
        ),
        (
            "subtitle",
            "ignore",
            None,
            None,
            False,
            "Left subtitle unchanged",
            "Left Subtitle unchanged",
        ),
        (
            "cover",
            "candidate",
            None,
            "cover_" + "c" * 64,
            True,
            "Current cover → selected cover",
            "Set Cover from the current image to the selected image",
        ),
        (
            "subtitle",
            "candidate",
            "A subtitle",
            None,
            True,
            "Removed subtitle",
            "Removed Subtitle",
        ),
        ("asin", "candidate", "B000000001", None, True, "Removed ASIN", "Removed ASIN"),
        (
            "title",
            "candidate",
            "Same",
            "Same ",
            True,
            "Kept Same",
            "Kept Title as Same",
        ),
        (
            "isbn",
            "candidate",
            None,
            "9781234567897",
            True,
            "Set ISBN to 9781234567897",
            "Set ISBN from Not set to 9781234567897",
        ),
        (
            "publisher",
            "candidate",
            "Old Press",
            "New Press",
            True,
            "Set publisher to New Press",
            "Set Publisher from Old Press to New Press",
        ),
        (
            "isbn",
            "candidate",
            None,
            None,
            False,
            "Chose an ISBN value automatically",
            "Chose an ISBN value automatically",
        ),
        (
            "title",
            "candidate",
            "Recorded",
            None,
            False,
            "Chose a title value automatically",
            "Chose a Title value automatically",
        ),
        (
            "authors",
            "unrecognised",
            None,
            None,
            False,
            "Chose an authors value automatically",
            "Chose an Authors value automatically",
        ),
    ),
    ids=(
        "keep",
        "ignore-acronym",
        "ignore",
        "cover",
        "removed",
        "removed-acronym",
        "display-equal",
        "set-acronym",
        "set",
        "unavailable-acronym",
        "unavailable",
        "unknown-outcome",
    ),
)
def test_an_automatic_choice_names_its_field_and_value(  # noqa: PLR0913
    destination: str,
    outcome: str,
    current: object,
    selected: object,
    chosen: bool,
    compact: str,
    sentence: str,
) -> None:
    target = TargetDestination(destination)
    report = _with(target, current) if current is not None else _titled("Recorded")
    event = _automatic(destination, outcome, report, selected, chosen=chosen)
    assert (event["change"], event["change_detail"]) == (compact, sentence)


def test_an_owner_row_keeps_its_arrow_while_absent_values_name_the_field() -> None:
    assert set_change("isbn", "1", "2").compact == "1 → 2"
    assert set_change("isbn", "1", None).compact == "Removed ISBN"
    assert keep_change("asin", None).compact == "Left ASIN unset"
    assert set_change("subtitle", "A", None).compact == "Removed subtitle"
    assert set_change("publishedYear", "1", None).compact == "Removed published year"


# Two descriptions that read alike for longer than a compact change shows.
_SHARED = "A long synthetic description that keeps going past the change limit " * 2
_EARLIER, _LATER = _SHARED + "and ends one way.", _SHARED + "and ends another way."
_SHOWN = _SHARED.strip()[: CHANGE_LIMIT - 1].rstrip() + "\u2026"


@pytest.mark.parametrize(
    ("named", "compact"),
    ((False, f"{_SHOWN} \u2192 {_SHOWN}"), (True, f"Set description to {_SHOWN}")),
    ids=("owner", "named"),
)
def test_values_that_differ_past_the_change_limit_are_a_change(
    named: bool, compact: str
) -> None:
    change = set_change("description", _EARLIER, _LATER, named=named)
    assert change.compact == compact
    assert change.sentence.startswith("Set Description from ")


def test_an_automatic_candidate_that_differs_late_is_a_change_and_not_a_check() -> None:
    report = _with(TargetDestination("description"), _EARLIER)
    event = _automatic("description", "candidate", report, _LATER)
    assert "check_group" not in event
    assert not str(event["change"]).startswith("Kept")
    assert event["change"] == f"Set description to {_SHOWN}"


def _no_change(report: MetadataReport | None, origin: str) -> dict[str, object]:
    return _project(
        _event(
            event_type="metadata",
            origin=origin,
            state="no_change_required",
            record={
                "source_kind": "field_effect_no_change",
                "target_fingerprint": None
                if report is None
                else report.target.fingerprint,
            },
        ),
        report,
    )


def _owner(state: str, report: MetadataReport | None) -> dict[str, object]:
    return _project(
        _event(
            state=state,
            record={
                "source_kind": "human_decision",
                "target_fingerprint": None
                if report is None
                else report.target.fingerprint,
                "selected_value": None,
            },
        ),
        report,
    )


def _record(kind: str, state: str, report: MetadataReport | None) -> dict[str, object]:
    return _project(_event(state=state, record={"source_kind": kind}), report)


def _refusal(report: MetadataReport | None) -> dict[str, object]:
    record = {"source_kind": "isbn_refusal", "reason_code": "source_value_absent"}
    return _project(_event(destination="isbn", record=record), report)


_CHECKED = _titled("Recorded")


@pytest.mark.parametrize(
    ("project", "checked"),
    (
        (lambda r: _no_change(r, "automatic_policy"), True),
        (lambda r: _automatic("title", "keep_current", r, chosen=False), True),
        (lambda r: _automatic("title", "ignore", r, chosen=False), True),
        (lambda r: _automatic("title", "candidate", r, "Recorded"), True),
        (_refusal, True),
        (lambda r: _automatic("title", "candidate", r, "Another title"), False),
        (lambda r: _automatic("title", "candidate", r, None), False),
        (lambda r: _automatic("title", "candidate", r, chosen=False), False),
        (lambda r: _no_change(r, "human"), False),
        (lambda r: _no_change(r, "automatic_pipeline"), False),
        (lambda r: _owner("keep_current", r), False),
        (lambda r: _owner("reject", r), False),
        (lambda r: _record("field_effect", "verified", r), False),
        (lambda r: _record("field_effect", "planned", r), False),
        (lambda r: _record("cover_safeguard", "kept_incumbent", r), False),
        (lambda r: _record("description_preparation", "succeeded", r), False),
        (lambda r: _record("description_decision", "selected", r), False),
        (lambda r: _record("policy_transition", "completed", r), False),
        (lambda r: _record("degraded", "candidate", r), False),
        (lambda r: _record("unrecognised", "recorded", r), False),
    ),
    ids=(
        "automatic-no-change",
        "keep",
        "ignore",
        "equal-candidate",
        "isbn-refusal",
        "changing-candidate",
        "removing-candidate",
        "unavailable-candidate",
        "owner-no-change",
        "pipeline-no-change",
        "owner-keep",
        "owner-reject",
        "verified-write",
        "planned-write",
        "cover",
        "description-preparation",
        "description-decision",
        "policy",
        "degraded",
        "unknown",
    ),
)
def test_only_an_automatic_check_carries_its_preparation_key(
    project, checked: bool
) -> None:
    gathered = project(_CHECKED)
    assert gathered.get("check_group") == (_CHECKED.report_id if checked else None)
    assert ("check_group" in gathered) is checked
    # A check without its report has no preparation to be gathered under.
    assert "check_group" not in project(None)


def test_two_preparations_give_two_keys() -> None:
    first, second = _titled("Recorded"), _titled("Recorded again")
    keys = {
        _automatic("title", "keep_current", report, chosen=False)["check_group"]
        for report in (first, second)
    }
    assert keys == {first.report_id, second.report_id}


# --- every fact fits the column the API bounds it to (#1278) -----------------

_MAXIMUM = "A synthetic description well past any evidence bound. " * 40
_MAXIMUM_MODEL = "m" * 256  # the description-advice model bound


def _bounded_records() -> list[tuple[dict[str, object], MetadataReport | None]]:
    report = _with(TargetDestination.DESCRIPTION, _MAXIMUM)
    pinned = report.target.fingerprint
    write = {"pre_value": _MAXIMUM, "desired_value": _MAXIMUM[::-1]}
    selected = {"kind": "text", "value": _MAXIMUM[::-1]}
    records: list[tuple[dict[str, object], MetadataReport | None]] = [
        ({"state": state, "record": {"source_kind": "field_effect", **write}}, None)
        for state in ("planned", "applying", "reconciling", "verified")
    ]
    records += [
        (
            {
                "state": state,
                "record": {"source_kind": "field_effect", **write, "failure": failure},
            },
            None,
        )
        for state, failure in (
            ("retryable", "target_changed"),
            ("stale", "target_changed"),
            ("abandoned", "owner_abandoned"),
        )
    ]
    records += [
        (
            {
                "destination": "title",
                "state": state,
                "record": {
                    "source_kind": "field_effect",
                    **write,
                    "subtitle_companion": dict(write),
                },
            },
            None,
        )
        for state in ("planned", "reconciling", "verified")
    ]
    records += [
        (
            {
                "state": state,
                "record": {
                    "source_kind": "human_decision",
                    "target_fingerprint": pinned,
                    "selected_value": selected
                    if state in {"accept", "owner_entered"}
                    else None,
                },
            },
            report,
        )
        for state in (
            "accept",
            "keep_current",
            "reject",
            "owner_entered",
            "withdraw",
        )
    ]
    records += [
        (
            {
                "state": state,
                "record": {
                    "source_kind": "automatic_decision",
                    "target_fingerprint": pinned,
                    "outcome": state,
                    "selected_value": selected if state == "candidate" else None,
                    "policy_category": "description",
                    "policy_option": "longest",
                    "policy_generation": 4,
                },
            },
            report,
        )
        for state in ("candidate", "keep_current")
    ]
    records.append(
        (
            {
                "state": "candidate",
                "record": {
                    "source_kind": "automatic_decision",
                    "target_fingerprint": pinned,
                    "outcome": "candidate",
                    "selected_value": {"kind": "text", "value": _MAXIMUM},
                    "policy_category": "description",
                    "policy_option": "longest",
                },
            },
            report,
        )
    )
    records += [
        (
            {
                "state": state,
                "record": {"source_kind": "description_preparation"},
            },
            None,
        )
        for state in ("requested", "running", "succeeded", "failed", "interrupted")
    ]
    records += [
        (
            {
                "state": "selected",
                "record": {
                    "source_kind": "description_decision",
                    "selected_value": selected,
                    "advised": True,
                    "engine": "openrouter",
                    "model": _MAXIMUM_MODEL,
                },
            },
            None,
        ),
        (
            {
                "state": "no_change_required",
                "record": {
                    "source_kind": "field_effect_no_change",
                    "target_fingerprint": pinned,
                },
            },
            report,
        ),
        (
            {
                "state": "completed",
                "record": {
                    "source_kind": "policy_transition",
                    "generation": 4,
                    "policy_changes": [],
                },
            },
            None,
        ),
        (
            {
                "state": "candidate",
                "record": {
                    "source_kind": "degraded",
                    "degraded_reason": AUTOMATIC_AUTHORITY_UNAVAILABLE,
                },
            },
            None,
        ),
        ({"record": None}, None),
    ]
    records += [
        (
            {
                "destination": "cover",
                "state": state,
                "record": {"source_kind": "cover_safeguard", "kind": state},
            },
            None,
        )
        for state in ("selected", "kept_incumbent", "unavailable", "stale")
    ]
    records += [
        ({"record": {"source_kind": "isbn_refusal", "reason_code": reason}}, None)
        for reason in ISBN_REFUSAL_REASONS
    ]
    return records


def test_every_fact_fits_the_evidence_bound_for_a_maximum_length_value() -> None:
    """No fact may outgrow the 512-character bound the Activity API enforces.

    A verified write once restated its bounded value behind a prefix, and the
    response model rejected the whole ledger (#1278). Every source kind and
    every state it projects is driven here with a value far past the bound.
    """
    projected = [
        _project(_event(**{"destination": "description", **overrides}), report)
        for overrides, report in _bounded_records()
    ]
    kinds = {
        cast(dict[str, object], overrides["record"] or {}).get("source_kind")
        for overrides, _ in _bounded_records()
    }
    assert kinds == {
        "field_effect",
        "human_decision",
        "description_preparation",
        "description_decision",
        "automatic_decision",
        "isbn_refusal",
        "field_effect_no_change",
        "cover_safeguard",
        "policy_transition",
        "degraded",
        None,
    }
    oversized = [
        (event["change"], fact)
        for event in projected
        for fact in cast(list[dict[str, str]], event["evidence"])
        if len(fact["value"]) > EVIDENCE_LIMIT or len(fact["label"]) > 64
    ]
    assert oversized == []
    verified = _facts(projected[3])
    assert verified["Confirmed in Audiobookshelf"] == "Matches the new value"
