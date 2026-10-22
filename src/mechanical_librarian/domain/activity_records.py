"""Compose one Activity row's owner-readable words from its own authority.

The persistence projection hands each event a closed ``record`` of the exact
values its own immutable document carries, and the report that document is
pinned to. This module turns that pair into what the owner reads: which work
or setting changed, the exact change, who or what chose it, and what came of
it. It reads nothing else -- no catalogue, no lineage, no other event -- so a
row projected today and the same row projected after the library has moved on
say the same thing.

Two rules shape the vocabulary. A record that was not a change never grows
empty previous/new fields: a paused write says why it paused and what happens
next, a closed preparation says why it closed. And no fact is stated twice --
the lifecycle word that used to appear beside every row's own state is gone,
because it was that state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import canonical_target_fields
from mechanical_librarian.domain.readable_activity import (
    ABSENT,
    AUTOMATIC_AUTHORITY_UNAVAILABLE,
    ISBN_REFUSAL_REASONS,
    ReadableChange,
    chooser_label,
    destination_label,
    failure_reason,
    field_phrase,
    keep_change,
    policy_option_label,
    policy_setting_label,
    set_change,
    value_text,
)

__all__ = [
    "ACTIVITY_CATEGORIES",
    "ActivityResult",
    "activity_result_label",
    "activity_search_text",
    "readable_activity_event",
    "project_readable_activity_event",
]

ACTIVITY_CATEGORIES = ("owner", "automatic", "policy", "recovery")


class ActivityResult(StrEnum):
    """The closed result vocabulary the Activity owner can read and search."""

    ACCEPTED = "Accepted"
    KEPT_CURRENT = "Kept current"
    REJECTED = "Rejected"
    COVER_ADVICE_COMPLETED = "Cover advice completed"
    COVER_ADVICE_UNAVAILABLE = "Cover advice unavailable"
    COVER_ADVICE_FAILED_SAFELY = "Cover advice failed safely"
    OWNER_COVER_CHOICE_RECORDED = "Owner cover choice recorded"
    MANUAL_COVER_RETRY = "Manual cover retry"
    NO_CHANGE_REQUIRED = "No change required"
    APPLYING = "Applying"
    PREPARING = "Preparing"
    PLANNED = "Planned"
    CHECKING = "Checking"
    WAITING_SAFELY = "Waiting safely"
    VERIFIED = "Verified"
    STALE = "Stale"
    SUPERSEDED = "Superseded"
    STOPPED_SAFELY = "Stopped safely"
    RECORDED = "Recorded"


# The write lifecycle states that are still moving or already closed without
# an applied value. Neither is a completed change, so neither claims one.
_UNSETTLED = frozenset({"planned", "claimed", "applying", "reconciling"})

# The owner's four categories are the durable event type said in the owner's
# words, not a second classification that could come to disagree with the one
# the ledger is filtered by. `review` was never the owner's word for a
# verified write whose authority happened to be human, and `safety` was never
# their word for a change the system recovered from on its own.
_CATEGORIES = {
    "review": "owner",
    "metadata": "automatic",
    "policy": "policy",
    "safety": "recovery",
    "unknown": "recovery",
}

_RESULT_LABELS = {
    "accept": ActivityResult.ACCEPTED,
    "keep_current": ActivityResult.KEPT_CURRENT,
    "reject": ActivityResult.REJECTED,
    "completed": ActivityResult.COVER_ADVICE_COMPLETED,
    "unavailable": ActivityResult.COVER_ADVICE_UNAVAILABLE,
    "failed": ActivityResult.COVER_ADVICE_FAILED_SAFELY,
    "selected": ActivityResult.OWNER_COVER_CHOICE_RECORDED,
    "retry": ActivityResult.MANUAL_COVER_RETRY,
    "no_change_required": ActivityResult.NO_CHANGE_REQUIRED,
    "applying": ActivityResult.APPLYING,
    "claimed": ActivityResult.PREPARING,
    "planned": ActivityResult.PLANNED,
    "reconciling": ActivityResult.CHECKING,
    "retryable": ActivityResult.WAITING_SAFELY,
    "verified": ActivityResult.VERIFIED,
    "stale": ActivityResult.STALE,
    "superseded": ActivityResult.SUPERSEDED,
    "abandoned": ActivityResult.STOPPED_SAFELY,
}


@dataclass(frozen=True, slots=True)
class ReadableActivityEvent:
    """Serialized output of canonical event/report composition, without join data."""

    document: bytes

    @classmethod
    def compose(
        cls, event: dict[str, object], report: MetadataReport | None
    ) -> ReadableActivityEvent:
        public = dict(event)
        project_readable_activity_event(public, report)
        return cls(
            json.dumps(public, ensure_ascii=False, separators=(",", ":")).encode()
        )

    def to_data(self) -> dict[str, object]:
        return cast("dict[str, object]", json.loads(self.document))


class ActivityProjectionError(ValueError):
    """An event's declared report identity or target does not agree."""


def project_readable_activity_event(
    event: dict[str, object], report: MetadataReport | None
) -> None:
    """Compose an event from its exact report; retain no report or join values."""
    connection = event.pop("connection", None)
    report_id = event.pop("report_id", None)
    event["work_label"] = event.get("work_label", "Recorded work")
    event["author_label"] = "Author details are not recorded"
    if connection is None and report_id is None:
        readable_activity_event(event, None)
        return
    if (
        type(connection) is not str
        or type(report_id) is not str
        or report is None
        or report.report_id != report_id
        or report.target.connection != connection
        or report.target.library != event.get("library")
        or report.target.item != event.get("work_id")
    ):
        raise ActivityProjectionError("activity")
    event["work_label"] = report.target.current_title or "Untitled"
    authors = next(
        (
            field.value
            for field in report.target.fields
            if field.destination is TargetDestination.AUTHORS
        ),
        (),
    )
    if type(authors) is tuple and all(type(author) is str for author in authors):
        joined = ", ".join(cast("tuple[str, ...]", authors))
        event["author_label"] = (
            joined if len(joined) <= 512 else joined[:511].rstrip() + "…"
        ) or "Author details are not recorded"
    readable_activity_event(event, report)


def activity_result_label(state: str) -> ActivityResult:
    """Return the closed owner-visible result word for one durable state."""
    return _RESULT_LABELS.get(state, ActivityResult.RECORDED)


def _reported_value(report: MetadataReport | None, destination: str) -> object:
    """Return the value the pinned report recorded for one destination.

    This is the report the event's own decision was made against, identified
    by the fingerprint the caller has already matched -- not a current lookup.
    """
    if report is None:
        return None
    try:
        target = TargetDestination(destination)
    except ValueError:
        return None
    field = canonical_target_fields(report.target.fields)[target]
    if field.state is not TargetFieldState.VALUE:
        return None
    value = field.value
    if type(value) is tuple:
        entries = cast("tuple[object, ...]", value)
        if entries and all(type(item) is TargetSeries for item in entries):
            return [
                {"name": item.name, "sequence": item.sequence}
                for item in cast("tuple[TargetSeries, ...]", entries)
            ]
        return list(entries)
    return value


def _pinned_report(
    record: dict[str, object], report: MetadataReport | None
) -> MetadataReport | None:
    """Accept the report only when this record's own fingerprint names it."""
    fingerprint = record.get("target_fingerprint")
    if report is None or type(fingerprint) is not str:
        return None
    return report if report.target.fingerprint == fingerprint else None


def _write_facts(
    record: dict[str, object], destination: str, origin: str, state: str, failure: str
) -> tuple[ReadableChange, list[dict[str, str]]]:
    previous, desired = record.get("pre_value"), record.get("desired_value")
    companion = record.get("subtitle_companion")
    if type(companion) is dict:
        confirmed = state == "verified" and not failure
        summary = (
            "Split title and subtitle"
            if confirmed
            else "Paired title and subtitle change unconfirmed"
            if state == "reconciling"
            else "Paired title and subtitle change pending"
            if not failure
            else "Paired title and subtitle change stopped"
        )
        return ReadableChange(summary, summary), [
            {"label": "Previous title", "value": value_text("title", previous)},
            {"label": "Selected title", "value": value_text("title", desired)},
            {
                "label": "Previous subtitle",
                "value": value_text("subtitle", companion["pre_value"]),
            },
            {
                "label": "Selected subtitle",
                "value": value_text("subtitle", companion["desired_value"]),
            },
            {"label": "Chosen by", "value": chooser_label(origin)},
            {
                "label": "Confirmed in Audiobookshelf",
                "value": "Both title and subtitle confirmed"
                if confirmed
                else "Both fields have not been confirmed",
            },
        ]
    change = set_change(destination, previous, desired)
    chosen = chooser_label(origin)
    if state == "reconciling":
        return ReadableChange("Change not confirmed yet", "Change not confirmed yet"), [
            {"label": "Selected value", "value": value_text(destination, desired)},
            {"label": "Chosen by", "value": chosen},
            {"label": "Confirmed in Audiobookshelf", "value": "Not confirmed yet"},
            {
                "label": "What happens next",
                "value": "Mechanical Librarian checks the current value again",
            },
        ]
    if failure:
        # A closed preparation may follow a write whose outcome was not confirmed.
        facts = [
            {"label": "Prepared value", "value": value_text(destination, desired)},
            {"label": "Why it stopped", "value": failure_reason(failure)},
            {"label": "Chosen by", "value": chosen},
        ]
        if state == "retryable":
            facts.append(
                {
                    "label": "What happens next",
                    "value": "Mechanical Librarian checks the current value again",
                }
            )
            return ReadableChange(
                "Prepared change paused", "Prepared change paused"
            ), facts
        facts.append(
            {
                "label": "Confirmed in Audiobookshelf",
                "value": "The selected change was not confirmed",
            }
        )
        return ReadableChange("Prepared change closed", "Prepared change closed"), facts
    facts = [
        {"label": "Previous value", "value": value_text(destination, previous)},
        {"label": "New value", "value": value_text(destination, desired)},
        {"label": "Chosen by", "value": chosen},
    ]
    if state == "verified":
        facts.append(
            {"label": "Confirmed in Audiobookshelf", "value": "Matches the new value"}
        )
    elif state in _UNSETTLED:
        facts.append(
            {"label": "Confirmed in Audiobookshelf", "value": "Not confirmed yet"}
        )
    return change, facts


def _decision_facts(
    record: dict[str, object],
    destination: str,
    state: str,
    report: MetadataReport | None,
) -> tuple[ReadableChange, list[dict[str, str]]]:
    current = _reported_value(_pinned_report(record, report), destination)
    selected = record.get("selected_value")
    chosen = {"label": "Chosen by", "value": "Library owner"}
    if state == "keep_current":
        return keep_change(destination, current), [
            {"label": "Current value", "value": value_text(destination, current)},
            {"label": "Selected value", "value": value_text(destination, current)},
            chosen,
        ]
    if state == "reject" or type(selected) is not dict:
        return (
            ReadableChange(
                "Left unchanged", f"Left {destination_label(destination)} unchanged"
            ),
            [
                {"label": "Current value", "value": value_text(destination, current)},
                chosen,
                {"label": "Outcome", "value": "No change was prepared"},
            ],
        )
    chosen_value = cast("dict[str, object]", selected).get("value")
    return set_change(destination, current, chosen_value), [
        {"label": "Previous value", "value": value_text(destination, current)},
        {"label": "New value", "value": value_text(destination, chosen_value)},
        chosen,
    ]


def _automatic_facts(
    record: dict[str, object], destination: str, report: MetadataReport | None
) -> tuple[ReadableChange, list[dict[str, str]], bool]:
    """Return the automatic row's change, its facts, and whether it was a check.

    A check is an automatic outcome that left the pinned value as it was: a
    keep, an ignore, or a candidate exactly equal to the reported value.
    """
    current = _reported_value(_pinned_report(record, report), destination)
    outcome = record.get("outcome")
    selected_value = record.get("selected_value")
    chosen = type(selected_value) is dict
    selected = (
        current
        if outcome in {"keep_current", "ignore"}
        else cast("dict[str, object]", selected_value).get("value")
        if chosen
        else None
    )
    field = destination_label(destination)
    category = cast(str, record.get("policy_category", ""))
    option = cast(str, record.get("policy_option", "")).partition(":")[0]
    generation = record.get("policy_generation")
    facts = [
        {"label": "Previous value", "value": value_text(destination, current)},
        {"label": "New value", "value": value_text(destination, selected)},
        {"label": "Chosen by", "value": "Automatic policy"},
        {
            "label": "Policy setting",
            "value": f"{policy_setting_label(category)}: {policy_option_label(option, category)}"
            if category and option
            else "Not recorded",
        },
    ]
    if type(generation) is int:
        facts.append({"label": "Policy generation", "value": str(generation)})
    if outcome == "keep_current":
        change = keep_change(destination, current)
    elif outcome == "ignore":
        change = ReadableChange(
            f"Left {field_phrase(field)} unchanged", f"Left {field} unchanged"
        )
    elif outcome == "candidate" and chosen:
        change = set_change(destination, current, selected, named=True)
    else:
        article = "an" if field[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
        change = ReadableChange(
            f"Chose {article} {field_phrase(field)} value automatically",
            f"Chose {article} {field} value automatically",
        )
    checked = outcome in {"keep_current", "ignore"} or (
        outcome == "candidate" and selected is not None and selected == current
    )
    return change, facts, checked


def _cover_facts(state: str) -> tuple[ReadableChange, list[dict[str, str]]]:
    described = {
        "completed": ("Cover advice ready", "Mechanical Librarian", "Ready to review"),
        "unavailable": (
            "Cover advice unavailable",
            "Mechanical Librarian",
            "No cover advice could be produced",
        ),
        "failed": (
            "Cover advice stopped safely",
            "Mechanical Librarian",
            "No cover was changed",
        ),
        "selected": ("Chose a different cover", "Library owner", "Recorded"),
        "kept_incumbent": ("Kept the current cover", "Library owner", "Recorded"),
        "no_action": ("Left the cover unchanged", "Library owner", "Recorded"),
        "stale": (
            "Prepared cover closed",
            "Not recorded",
            "The current cover changed after this was prepared",
        ),
        "superseded": (
            "Prepared cover replaced",
            "Not recorded",
            "A newer cover preparation replaced this one",
        ),
    }.get(state, ("Recorded", "Not recorded", "Recorded"))
    summary, chosen, outcome = described
    return ReadableChange(summary, summary), [
        {"label": "Chosen by", "value": chosen},
        {"label": "What happened", "value": outcome},
        {"label": "Your action", "value": "None"},
    ]


def readable_activity_event(
    event: dict[str, object], report: MetadataReport | None
) -> None:
    """Replace one event's private durable record with owner-readable fields."""
    record = event.pop("record", None)
    if type(record) is not dict:
        record = {"source_kind": "unknown"}
    record = cast("dict[str, object]", record)
    source_kind = cast(str, record.get("source_kind", "unknown"))
    destination = cast(str, event.get("destination", ""))
    origin = cast(str, event.get("origin", "unknown"))
    state = cast(str, event.get("state", ""))
    failure = cast(str, record.get("failure") or "")
    # An automatic check that changed nothing is gathered with its
    # preparation's other checks under that preparation's own report; a
    # change, and anything an owner chose, never is.
    checked = source_kind == "isbn_refusal" or (
        source_kind == "field_effect_no_change" and origin == "automatic_policy"
    )
    if source_kind == "field_effect":
        change, facts = _write_facts(record, destination, origin, state, failure)
    elif source_kind == "human_decision":
        change, facts = _decision_facts(record, destination, state, report)
    elif source_kind == "description_preparation":
        description = {
            "requested": "Description advice requested",
            "running": "Preparing description advice",
            "succeeded": "Description advice prepared for review",
            "failed": "Description advice could not be prepared",
            "interrupted": "Description advice preparation interrupted",
        }.get(state, "Description advice recorded")
        change = ReadableChange(description, description)
        facts = [
            {
                "label": "Application",
                "value": "Preparation grants no write authority. Review current Description choices in Library.",
            }
        ]
        if state in {"failed", "interrupted"}:
            facts.append(
                {
                    "label": "Retry",
                    "value": "Request another attempt from Description in Library; no automatic retry.",
                }
            )
    elif source_kind == "description_decision":
        change = ReadableChange(
            "Advised description selected",
            "Advised description queued under existing write requirements",
        )
        selected = record.get("selected_value")
        facts = [
            {
                "label": "Selected description",
                "value": value_text(
                    "description",
                    selected.get("value") if type(selected) is dict else None,
                ),
            },
            {"label": "Chosen by", "value": chooser_label(origin)},
            {
                "label": "Application",
                "value": "Queued under existing write requirements; no write is implied by this selection",
            },
        ]
    elif source_kind == "automatic_decision":
        change, facts, checked = _automatic_facts(record, destination, report)
    elif source_kind == "isbn_refusal":
        change = ReadableChange("Left ISBN unchanged", "Left ISBN unchanged")
        facts = [
            {
                "label": "Why it stayed unchanged",
                "value": ISBN_REFUSAL_REASONS[cast(str, record["reason_code"])],
            },
            {
                "label": "Outcome",
                "value": "No ISBN change was prepared at this evaluation",
            },
        ]
    elif source_kind == "field_effect_no_change":
        current = _reported_value(_pinned_report(record, report), destination)
        change = keep_change(destination, current)
        facts = [
            {"label": "Current value", "value": value_text(destination, current)},
            {"label": "Chosen by", "value": chooser_label(origin)},
            {
                "label": "Confirmed in Audiobookshelf",
                "value": "No change was needed",
            },
        ]
    elif source_kind == "cover_safeguard":
        change, facts = _cover_facts(state)
    elif source_kind == "policy_transition":
        changes = cast("list[dict[str, str]]", record.get("policy_changes") or [])
        counted = f"{len(changes)} {'change' if len(changes) == 1 else 'changes'}"
        activated = state in {"completed", "completed_with_failures"}
        summary = f"{counted} {'activated' if activated else 'being applied'}"
        change = ReadableChange(summary, summary)
        facts = [
            {"label": "Changed by", "value": "Library owner"},
            {
                "label": "Policy generation",
                "value": str(record.get("generation", ABSENT)),
            },
            {
                "label": "Active policy",
                "value": f"Updated with all {counted}"
                if activated
                else "Being updated",
            },
        ]
        event["policy_changes"] = changes
    elif source_kind == "degraded":
        degraded_reason = cast(str, record.get("degraded_reason", ""))
        unavailable = {
            AUTOMATIC_AUTHORITY_UNAVAILABLE: "The automatic authority is no longer available",
        }.get(degraded_reason, "Details cannot be read")
        change = ReadableChange(
            "Details unavailable", "Recorded details are unavailable"
        )
        facts = [
            {
                "label": "Record status",
                "value": "This retained event cannot be fully projected",
            },
            {"label": "Disclosure", "value": unavailable},
        ]
        event["diagnostic"] = {
            "event_class": "automatic_decision",
            "reason": degraded_reason,
        }
    else:
        change = ReadableChange("Recorded", "Recorded")
        facts = [{"label": "Chosen by", "value": chooser_label(origin)}]
    if record.get("advised") is True:
        facts.extend(
            [
                {"label": "Provenance", "value": "Advised · Authored content"},
                {
                    "label": "Adviser",
                    "value": f"{record.get('engine')} · {record.get('model')}",
                },
            ]
        )
    event["category"] = _CATEGORIES.get(
        cast(str, event.get("event_type", "unknown")), "recovery"
    )
    event["field_label"] = (
        "Title and subtitle"
        if type(record.get("subtitle_companion")) is dict
        else destination_label(destination)
    )
    event["change"] = change.compact
    event["change_detail"] = change.sentence
    event["evidence"] = facts
    if checked and report is not None:
        event["check_group"] = report.report_id


def activity_search_text(
    event: dict[str, object], report: MetadataReport | None
) -> str:
    """Return exactly the owner-readable Activity words that may be searched."""
    projected = dict(event)
    readable_activity_event(projected, report)
    values: list[str] = []
    for key in (
        "work_id",
        "library",
        "destination",
        "work_label",
        "detail",
        "field_label",
        "change",
        "change_detail",
    ):
        value = projected.get(key)
        if type(value) is str:
            values.append(value)
    state = projected.get("state")
    if type(state) is str:
        values.append(activity_result_label(state).value)
    if report is not None:
        values.append(report.target.current_title or "Untitled")
    evidence = projected.get("evidence")
    for fact in evidence if type(evidence) is list else ():
        if type(fact) is dict:
            for key in ("label", "value"):
                value = fact.get(key)
                if type(value) is str:
                    values.append(value)
    policy_changes = projected.get("policy_changes")
    for change in policy_changes if type(policy_changes) is list else ():
        if type(change) is dict:
            for key in ("field", "setting", "previous", "next"):
                value = change.get(key)
                if type(value) is str:
                    values.append(value)
    return "\n".join(values).casefold()
