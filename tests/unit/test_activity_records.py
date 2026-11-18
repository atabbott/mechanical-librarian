"""What one Activity row says, and what it refuses to say.

Every case here drives the projection the installed page reads, so a change
that quietly stops naming a previous value, starts inventing one, or starts
reading the current catalogue fails here rather than in a screenshot.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.domain import readable_activity
from mechanical_librarian.domain.activity_records import (
    activity_result_label,
    readable_activity_event,
)
from mechanical_librarian.domain.metadata_policy import DESTINATIONS, OPTION_CATALOGUE
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
    policy_destination_label,
    policy_option_label,
    policy_setting_label,
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
        ("went_back", "Went back"),
        ("reject", "Rejected"),
        ("owner_entered", "Entered by you"),
        ("withdraw", "Withdrawn"),
        ("pin", "Pinned"),
        ("unpin", "Unpinned"),
        ("completed", "Cover advice completed"),
        ("unavailable", "Cover advice unavailable"),
        ("failed", "Cover advice failed"),
        ("selected", "Your cover choice recorded"),
        ("retry", "Cover retried by you"),
        ("no_change_required", "No change needed"),
        ("applying", "Applying"),
        ("claimed", "Preparing"),
        ("planned", "Planned"),
        ("reconciling", "Checking"),
        ("retryable", "Waiting to retry"),
        ("verified", "Verified"),
        ("stale", "Closed"),
        ("superseded", "Replaced"),
        ("abandoned", "Stopped"),
        ("linked", "Linked"),
        ("link_changed", "Link changed"),
        ("link_withdrawn", "Link withdrawn"),
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
        ("connection", "Waiting to retry"),
        ("connection_exhausted", "Closed"),
        ("capability", "Closed"),
        ("target_changed", "Closed because the item changed"),
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
    # One vocabulary per field: Activity names these as the browser does (#1286).
    assert destination_label("publishedYear") == "Publication year"
    assert destination_label("publishedDate") == "Publication date"
    assert destination_label("explicit") == "Explicit content"
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
    assert set_change("subtitle", "A subtitle", None).sentence == "Cleared Subtitle"
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
        "Chosen by": "You",
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


@pytest.mark.parametrize(
    "written, failure, state, result",
    (
        ("verified", None, "went_back", "Went back"),
        # Until its write back verifies, it reads in that write's own word.
        ("planned", None, "planned", "Planned"),
        ("applying", None, "applying", "Applying"),
        ("stale", "restore_unavailable", "stale", "Closed"),
        ("stale", "connection", "stale", "Waiting to retry"),
        # Not planned yet: recorded, never Kept current.
        (None, None, "recorded", "Recorded"),
    ),
)
def test_a_keep_that_goes_back_on_a_write_reads_as_that_change(
    written: str | None, failure: str | None, state: str, result: str
) -> None:
    """A keep that goes back on the case's write reads from the written value
    to the value before it; only a verified write back reads Went back
    (#1523)."""
    report = _titled("The Dispossessed")
    event = _project(
        _event(
            state="keep_current",
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": None,
                "went_back": {
                    "written": "Anarres",
                    "before": "The Dispossessed",
                    "state": written,
                    "failure": failure,
                },
            },
        ),
        report,
    )
    assert event["state"] == state
    assert activity_result_label(state, event.get("failure")).value == result
    assert event["change"] == "Anarres → The Dispossessed"
    assert event["change_detail"] == "Set Title back from Anarres to The Dispossessed"
    facts = _facts(event)
    assert {key: facts[key] for key in list(facts)[:3]} == {
        "Written value": "Anarres",
        "Value before your change": "The Dispossessed",
        "Chosen by": "You",
    }
    assert ("What happened" in facts) is (failure is not None)
    assert "Kept" not in str(event) and "Current value" not in str(event)


def test_a_cover_keep_that_goes_back_names_no_image_identity() -> None:
    """A cover's values are opaque images, so its keep reads as set_change's
    cover row does (#1523)."""
    report = _titled("The Dispossessed")
    event = _project(
        _event(
            state="keep_current",
            destination="cover",
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": None,
                "went_back": {
                    "written": "cover_" + "a" * 64,
                    "before": "cover_" + "b" * 64,
                    "state": "verified",
                    "failure": None,
                },
            },
        ),
        report,
    )
    assert event["state"] == "went_back"
    assert event["change"] == "Written cover → earlier cover"
    assert (
        event["change_detail"]
        == "Set Cover back from the written image to the earlier image"
    )
    assert "cover_" not in str(event)


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
    assert facts["Chosen by"] == "You"
    # The change column says "Left unchanged"; no fact repeats it (#1765).
    assert "Outcome" not in facts
    assert "Previous value" not in facts and "New value" not in facts


@pytest.mark.parametrize(
    ("state", "change", "detail", "label", "outcome"),
    (
        ("pin", "Pinned", "Pinned the Title value", None, None),
        (
            "unpin",
            "Unpinned",
            "Unpinned the Title field",
            "Current value",
            "Mechanical Librarian can ask about this field again",
        ),
    ),
    ids=("pin", "unpin"),
)
def test_pin_and_unpin_ledger_words(
    state: str, change: str, detail: str, label: str | None, outcome: str | None
) -> None:
    """A pin is not a field left unchanged, and its row names no value: the
    value a pin keeps is its answer's (ADR-0085, Amendment 2)."""
    report = _titled("The Dispossessed")
    event = _project(
        _event(
            state=state,
            record={
                "source_kind": "human_decision",
                "target_fingerprint": report.target.fingerprint,
                "selected_value": None,
            },
        ),
        report,
    )
    assert (event["change"], event["change_detail"]) == (change, detail)
    facts = _facts(event)
    if label is None:
        assert set(facts) == {"Chosen by"}
    else:
        assert facts[label] == "The Dispossessed"
    assert facts["Chosen by"] == "You"
    assert facts.get("Outcome") == outcome


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
        "Policy option": "Series memberships: Primary narrative",
        "Policy generation": "4",
    }


def test_a_taste_switch_names_its_profile_version_and_no_rule() -> None:
    """A switch the taste profile chose says which version chose it, and
    nothing a rule says (#1372 PR 5, K10; ADR-0095)."""
    report = _titled("Tehanu")
    event = _project(
        _event(
            destination="cover",
            event_type="metadata",
            origin="automatic_policy",
            state="candidate",
            record={
                "source_kind": "automatic_decision",
                "target_fingerprint": report.target.fingerprint,
                "outcome": "candidate",
                "selected_value": {"kind": "cover", "value": "cover_" + "1" * 64},
                "policy_category": "cover_order",
                "policy_option": "fixed_source_order",
                "policy_generation": 4,
                "taste_version": 3,
            },
        ),
        report,
    )
    facts = _facts(event)
    assert facts["Taste profile"] == "Version 3"
    assert list(facts)[-1] == "Taste profile"


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
    assert facts["Policy option"] == "Title and subtitle: Preserve current layout"
    assert "Policy setting" not in facts


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
    # The change column says "Details unavailable"; only the reason follows.
    assert _facts(event) == {
        "Reason": "The automatic decision it records can no longer be read",
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
            "Prepared change waiting",
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
            "Prepared change stopped",
            "You stopped this change",
        ),
        (
            "failed",
            "target_failure",
            "Prepared change failed",
            "Audiobookshelf refused the change",
        ),
        (
            "superseded",
            "authority_superseded",
            "Prepared change replaced",
            "A newer decision replaced the one behind this change",
        ),
        (
            "stale",
            "matching_changed",
            "Prepared change closed",
            "Book matching changed after this was prepared",
        ),
    ),
    ids=("paused", "closed", "stopped", "failed", "replaced", "matching_changed"),
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
    # Every ended or waiting change says what happened, beside a summary
    # that takes its result column's word (#1765).
    assert facts["What happened"] == reason
    assert "Why it stopped" not in facts
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
            "Prepared change waiting",
            "This change was not sent to Audiobookshelf",
            "Mechanical Librarian checks the current value again",
        ),
        (
            "retryable",
            "not_applied",
            "Prepared change waiting",
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
    # Ended or not, the change says what happened (#1765).
    assert facts["What happened"] == reason
    assert "Why it stopped" not in facts
    assert facts.get("What happens next") == next_step
    assert ("Confirmed in Audiobookshelf" in facts) is (
        failure == "connection_exhausted"
    )


def test_a_policy_row_carries_its_own_exact_from_and_to() -> None:
    changes = [
        {
            "field": "Title and subtitle",
            "setting": "Setting",
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
    # The change column already says "1 change activated", so no fact repeats
    # it, and the row names the owner as "You".
    assert "Active policy" not in _facts(event)
    assert _facts(event)["Changed by"] == "You"


_POLICY_FIELDS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "policyFields.ts"
)
_POLICY_ENTRY = re.compile(r"(\w+):\s*(['\"])(.*?)\2", re.DOTALL)


def _block(source: str, start: str) -> str:
    begin = source.index("{", source.index(start)) + 1
    return source[begin : source.index("}", begin)]


def _policy_page_labels() -> tuple[
    dict[str, str], dict[str, str], dict[tuple[str, str], str]
]:
    source = _POLICY_FIELDS.read_text(encoding="utf-8")
    destinations = {
        code: text
        for code, _, text in _POLICY_ENTRY.findall(
            _block(source, "export const policyDestinationLabels")
        )
    }
    start = source.index("export const fields")
    catalogue = source[start : source.index("\n]\n", start)]
    fields: dict[str, str] = {}
    options: dict[tuple[str, str], str] = {}
    for entry in catalogue.split("\n  {\n")[1:]:
        named = dict((name, text) for name, _, text in _POLICY_ENTRY.findall(entry))
        fields[named["key"]] = named["label"]
        for option, _, text in _POLICY_ENTRY.findall(_block(entry, "optionLabels:")):
            options[(named["key"], option)] = text
    return fields, destinations, options


def test_a_policy_row_names_each_field_setting_and_option_as_policy_does() -> None:
    # Activity's policy rows copy the Policy page's own names
    # (`policyFields.ts`), so the two tables stay equal and close over the
    # whole policy catalogue (#1765).
    fields, destinations, options = _policy_page_labels()
    assert readable_activity._POLICY_FIELD_LABELS == fields
    assert readable_activity._POLICY_DESTINATION_LABELS == destinations
    assert readable_activity._POLICY_OPTION_LABELS == options
    assert set(fields) == set(OPTION_CATALOGUE)
    assert set(destinations) == set(DESTINATIONS)
    assert set(options) == {
        (field, option)
        for field, offered in OPTION_CATALOGUE.items()
        for option in offered
    }
    for field, label in fields.items():
        assert policy_setting_label(field) == label
    for destination, label in destinations.items():
        assert policy_destination_label(destination) == label
    for (field, option), label in options.items():
        assert policy_option_label(option, field) == label
    # A setting and an option that share a code still read differently.
    assert policy_destination_label("unsupported") == "Not supported"
    assert policy_option_label("unsupported", "explicit") == "Leave flag unchanged"


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
    # The same on every cover row, so no row says it (#1765).
    assert "Your action" not in _facts(event)


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


def test_owner_entered_row_names_the_value_it_replaced() -> None:
    """An owner entry names what its own write replaced, not the case's report.

    The report a case is pinned to predates an earlier write on the same
    case, so a second entry made after that write would otherwise read
    "Not set" for a field the earlier write had filled (#1286). The value
    the decision's plan replaced is the one its Verified row names too.
    """
    report = _titled("Recorded work")
    missing = tuple(
        TargetField(field.destination, TargetFieldState.MISSING)
        if field.destination is TargetDestination.PUBLISHER
        else field
        for field in report.target.fields
    )
    report = _report(missing)
    record = {
        "source_kind": "human_decision",
        "target_fingerprint": report.target.fingerprint,
        "selected_value": {"kind": "text", "value": "Second Press"},
    }
    unplanned = _project(
        _event(destination="publisher", state="owner_entered", record=dict(record)),
        report,
    )
    planned = _project(
        _event(
            destination="publisher",
            state="owner_entered",
            record=dict(record, pre_value="First Press"),
        ),
        report,
    )
    assert unplanned["change"] == "Not set → Second Press"
    assert planned["change"] == "First Press → Second Press"
    assert _facts(planned)["Previous value"] == "First Press"
    assert _facts(planned)["New value"] == "Second Press"


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
            "Cleared subtitle",
            "Cleared Subtitle",
        ),
        ("asin", "candidate", "B000000001", None, True, "Cleared ASIN", "Cleared ASIN"),
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
    assert set_change("isbn", "1", None).compact == "Cleared ISBN"
    assert keep_change("asin", None).compact == "Left ASIN unset"
    assert set_change("subtitle", "A", None).compact == "Cleared subtitle"
    assert set_change("publishedYear", "1", None).compact == "Cleared publication year"


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


def test_an_isbn_refusal_says_why_once() -> None:
    facts = _facts(_refusal(_titled("Recorded")))
    assert facts["Why it stayed unchanged"] == "No source supplied an ISBN"
    # The change column says "Left ISBN unchanged"; no fact repeats it (#1765).
    assert "Outcome" not in facts


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


# --- the owner's book link (#1477, S31) ---------------------------------------


def _link_record(state: str, **record: object) -> dict[str, object]:
    return _project(
        _event(
            destination="book_link",
            state=state,
            work_label="The Silent Orchard",
            record={
                "source_kind": "owner_link",
                "provider": "open_library",
                "scope": "edition",
                "title": "The Silent Orchard: Second Edition",
                **record,
            },
        ),
        None,
    )


@pytest.mark.parametrize(
    ("state", "scope", "linked_as"),
    [
        ("linked", "work", "This book"),
        ("link_changed", "edition", "This edition"),
        ("linked", "recording", "This recording"),
    ],
    ids=["linked-work", "changed-edition", "linked-recording"],
)
def test_a_book_link_names_its_record_source_and_what_follows(
    state: str, scope: str, linked_as: str
) -> None:
    event = _link_record(state, scope=scope)
    assert event["field_label"] == "Book link"
    assert event["change"] == (
        "Linked to The Silent Orchard: Second Edition on Open Library"
    )
    assert event["change_detail"] == (
        "Linked The Silent Orchard to The Silent Orchard: Second Edition on "
        f"Open Library, {linked_as.replace('This', 'as this')}."
    )
    assert _facts(event) == {
        "Source": "Open Library",
        "Linked as": linked_as,
        "What happens next": "Linking writes nothing to Audiobookshelf. "
        "This work is prepared again with the link.",
    }
    assert (
        activity_result_label(state).value
        == {
            "linked": "Linked",
            "link_changed": "Link changed",
        }[state]
    )


def test_a_withdrawn_link_names_the_link_it_ended() -> None:
    event = _link_record("link_withdrawn")
    assert event["change"] == (
        "Withdrew the link to The Silent Orchard: Second Edition"
    )
    assert event["change_detail"] == (
        "Withdrew the link from The Silent Orchard to The Silent Orchard: "
        "Second Edition on Open Library."
    )
    assert _facts(event) == {
        "Source": "Open Library",
        "What happens next": "Withdrawing writes nothing to Audiobookshelf. "
        "This work is prepared again without the link.",
    }


def test_a_link_row_without_a_work_label_reads_as_the_recorded_work() -> None:
    event = _project(
        _event(
            destination="book_link",
            state="linked",
            record={
                "source_kind": "owner_link",
                "provider": "hardcover",
                "scope": "work",
                "title": "A Title",
            },
        ),
        None,
    )
    assert event["change_detail"] == (
        "Linked Recorded work to A Title on Hardcover, as this book."
    )


# --- links from an identifiers file (#1478) -----------------------------------


@pytest.mark.parametrize(
    ("scope", "identity", "named"),
    [
        ("edition", "9780306406157", "ISBN 9780306406157"),
        ("recording", "B00TEST001", "ASIN B00TEST001"),
    ],
)
def test_a_file_link_withdrawn_singly_names_its_identifier(
    scope: str, identity: str, named: str
) -> None:
    event = _project(
        _event(
            destination="book_link",
            state="link_withdrawn",
            work_label="The Silent Orchard",
            record={
                "source_kind": "owner_link",
                "source": "file",
                "scope": scope,
                "identity": identity,
            },
        ),
        None,
    )
    assert event["change"] == f"Withdrew the link to {named}"
    assert event["change_detail"] == (
        f"Withdrew the link from The Silent Orchard to {named}, "
        "from an identifiers file."
    )
    assert _facts(event) == {
        "Source": "Identifiers file",
        "What happens next": "Withdrawing writes nothing to Audiobookshelf. "
        "This work is prepared again without the link.",
    }


def _import_record(kind: str, **counts: int) -> dict[str, object]:
    record: dict[str, object] = {
        "source_kind": "identifier_import",
        "kind": kind,
        "import_id": "identifier_import_" + "a" * 64,
        "rows": 20,
        "linked": 15,
        "affected": 15,
    }
    record.update(counts)
    return _project(
        _event(
            destination="links",
            state="imported" if kind == "imported" else "withdraw",
            work_id="Identifiers file",
            work_label="Identifiers file",
            record=record,
        ),
        None,
    )


def test_an_import_reads_as_one_row_with_its_counts() -> None:
    event = _import_record("imported")
    assert (event["work_label"], event["field_label"]) == ("Identifiers file", "Links")
    assert (event["change"], event["change_detail"]) == (
        "Linked 15 works",
        "Linked 15 works from an identifiers file.",
    )
    assert event["category"] == "owner"
    assert event["identifier_import_id"] == "identifier_import_" + "a" * 64
    assert _facts(event) == {
        "Rows in the file": "20",
        "Linked": "15",
        "Not linked": "5",
        "Chosen by": "You",
        "What happens next": "Linking writes nothing to Audiobookshelf. "
        "Each work is prepared again with its link.",
    }
    assert activity_result_label("imported").value == "Imported"


def test_one_linked_work_is_singular() -> None:
    event = _import_record("imported", rows=1, linked=1, affected=1)
    assert event["change"] == "Linked 1 work"


def test_a_withdrawal_names_what_it_withdrew_and_what_changed_since() -> None:
    event = _import_record("withdrawn", affected=13)
    assert "identifier_import_id" not in event
    assert (event["change"], event["change_detail"]) == (
        "Withdrew 13 links",
        "Withdrew 13 links with their identifiers file import.",
    )
    assert _facts(event) == {
        "Linked": "15",
        "Changed since the import": "2",
        "Chosen by": "You",
        "What happens next": "Withdrawing writes nothing to Audiobookshelf. "
        "Each work is prepared again without its link.",
    }
    assert activity_result_label("withdraw").value == "Withdrawn"
    single = _import_record("withdrawn", rows=1, linked=1, affected=1)
    assert single["change"] == "Withdrew 1 link"
    assert "Changed since the import" not in _facts(single)
