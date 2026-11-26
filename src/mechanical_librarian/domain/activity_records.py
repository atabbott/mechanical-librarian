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

from mechanical_librarian.domain.evidence_bounds import cut_formatted_description
from mechanical_librarian.domain.field_effect import CONNECTION_REPLAN_LIMIT
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.owner_settings import provider_label
from mechanical_librarian.domain.proposal_set import canonical_target_fields
from mechanical_librarian.domain.readable_activity import (
    ABSENT,
    AUTOMATIC_AUTHORITY_UNAVAILABLE,
    EVIDENCE_LIMIT,
    ISBN_REFUSAL_REASONS,
    ReadableChange,
    chooser_label,
    destination_label,
    failure_reason,
    field_phrase,
    keep_change,
    plain_text,
    policy_option_label,
    policy_setting_label,
    set_change,
    value_text,
    went_back_change,
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
    WENT_BACK = "Went back"
    REJECTED = "Rejected"
    OWNER_ENTERED = "Entered by you"
    WITHDRAWN = "Withdrawn"
    PINNED = "Pinned"
    UNPINNED = "Unpinned"
    COVER_ADVICE_COMPLETED = "Cover advice completed"
    COVER_ADVICE_UNAVAILABLE = "Cover advice unavailable"
    COVER_ADVICE_FAILED = "Cover advice failed"
    OWNER_COVER_CHOICE_RECORDED = "Your cover choice recorded"
    MANUAL_COVER_RETRY = "Cover retried by you"
    NO_CHANGE_NEEDED = "No change needed"
    APPLYING = "Applying"
    PREPARING = "Preparing"
    PLANNED = "Planned"
    CHECKING = "Checking"
    WAITING_TO_RETRY = "Waiting to retry"
    VERIFIED = "Verified"
    CLOSED = "Closed"
    ITEM_CHANGED = "Closed because the item changed"
    MATCHING_CHANGED = "Closed because Book matching changed"
    REPLACED = "Replaced"
    STOPPED = "Stopped"
    RECORDED = "Recorded"
    LINKED = "Linked"
    LINK_CHANGED = "Link changed"
    LINK_WITHDRAWN = "Link withdrawn"
    IMPORTED = "Imported"


# The write lifecycle states that are still moving or already closed without
# an applied value. Neither is a completed change, so neither claims one.
_UNSETTLED = frozenset({"planned", "claimed", "applying", "reconciling"})

# A prepared change that ended says so in its result column's own word.
_CLOSED_VERBS = {
    "stale": "closed",
    "abandoned": "stopped",
    "failed": "failed",
    "superseded": "replaced",
}

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
    "went_back": ActivityResult.WENT_BACK,
    "reject": ActivityResult.REJECTED,
    "owner_entered": ActivityResult.OWNER_ENTERED,
    "withdraw": ActivityResult.WITHDRAWN,
    "pin": ActivityResult.PINNED,
    "unpin": ActivityResult.UNPINNED,
    "completed": ActivityResult.COVER_ADVICE_COMPLETED,
    "unavailable": ActivityResult.COVER_ADVICE_UNAVAILABLE,
    "failed": ActivityResult.COVER_ADVICE_FAILED,
    "selected": ActivityResult.OWNER_COVER_CHOICE_RECORDED,
    "retry": ActivityResult.MANUAL_COVER_RETRY,
    "no_change_required": ActivityResult.NO_CHANGE_NEEDED,
    "applying": ActivityResult.APPLYING,
    "claimed": ActivityResult.PREPARING,
    "planned": ActivityResult.PLANNED,
    "reconciling": ActivityResult.CHECKING,
    "retryable": ActivityResult.WAITING_TO_RETRY,
    "verified": ActivityResult.VERIFIED,
    "stale": ActivityResult.CLOSED,
    "superseded": ActivityResult.REPLACED,
    "abandoned": ActivityResult.STOPPED,
    "linked": ActivityResult.LINKED,
    "link_changed": ActivityResult.LINK_CHANGED,
    "link_withdrawn": ActivityResult.LINK_WITHDRAWN,
    "imported": ActivityResult.IMPORTED,
    "recorded": ActivityResult.RECORDED,
}

# How a book link names what it linked (#1477): the fact, and the clause.
_LINKED_AS = {
    "work": ("This book", "as this book"),
    "edition": ("This edition", "as this edition"),
    "recording": ("This recording", "as this recording"),
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
    # An owner row whose report is gone reads its copy's author label
    # (ADR-0108); a report, when there, names its own.
    event["author_label"] = event.get("author_label", "Author details are not recorded")
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
    event["work_label"], event["author_label"] = report_work_labels(report)
    readable_activity_event(event, report)


def report_work_labels(report: MetadataReport) -> tuple[str, str]:
    """Name a work and its authors as its exact report records them."""
    authors = next(
        (
            field.value
            for field in report.target.fields
            if field.destination is TargetDestination.AUTHORS
        ),
        (),
    )
    return report.target.current_title or "Untitled", author_label(authors)


def author_label(authors: object) -> str:
    """Name a work's authors from its report's authors value, or from a ledger
    row's copy of it (ADR-0108)."""
    if isinstance(authors, list):
        authors = tuple(authors)
    if type(authors) is tuple and all(type(author) is str for author in authors):
        joined = ", ".join(cast("tuple[str, ...]", authors))
        return (
            joined if len(joined) <= 512 else joined[:511].rstrip() + "…"
        ) or "Author details are not recorded"
    return "Author details are not recorded"


def activity_result_label(state: str, failure: object = None) -> ActivityResult:
    """Return the closed owner-visible result word for one durable state.

    A change stopped only by a lost connection is still waiting to be prepared
    again, so it reads as waiting rather than as a stopped stale (#1402). One
    that closed because the item changed says so, without claiming nothing was
    written: an earlier attempt may have landed (#1542).
    """
    if (state, failure) == ("stale", "connection"):
        return ActivityResult.WAITING_TO_RETRY
    if (state, failure) == ("stale", "target_changed"):
        return ActivityResult.ITEM_CHANGED
    if (state, failure) == ("stale", "matching_changed"):
        return ActivityResult.MATCHING_CHANGED
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


def _reported_form(report: MetadataReport | None, destination: str) -> object:
    """The formatted copy of the description the pinned report recorded."""
    if report is None or destination != "description":
        return None
    return canonical_target_fields(report.target.fields)[
        TargetDestination.DESCRIPTION
    ].formatted


def _value_fact(
    label: str, destination: str, value: object, form: object = None
) -> dict[str, str]:
    """One value fact, with its description's own formatted copy (#2003).

    The copy is shown in place of the text and cut, still well nested, to
    the bound a fact's text has; the text stays what is compared and
    searched.
    """
    fact = {"label": label, "value": value_text(destination, value)}
    if destination == "description" and type(value) is str and type(form) is str:
        fact["formatted"] = cut_formatted_description(form, EVIDENCE_LIMIT)
    return fact


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
            else "Title and subtitle change not confirmed yet"
            if state == "reconciling"
            else "Title and subtitle change pending"
            if not failure
            else "Title and subtitle change waiting"
            if failure == "connection" or state == "retryable"
            else f"Title and subtitle change {_CLOSED_VERBS.get(state, 'closed')}"
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
                else "Title and subtitle not confirmed",
            },
        ]
    change = set_change(destination, previous, desired)
    chosen = chooser_label(origin)
    desired_form = record.get("desired_form")
    if state == "reconciling":
        return ReadableChange("Change not confirmed yet", "Change not confirmed yet"), [
            _value_fact("Selected value", destination, desired, desired_form),
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
            _value_fact("Prepared value", destination, desired, desired_form),
            {
                # A waiting change and an ended one both say what happened;
                # the summary carries the result word (#1402, #1765).
                "label": "What happened",
                "value": failure_reason(failure),
            },
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
                "Prepared change waiting", "Prepared change waiting"
            ), facts
        if failure == "connection":
            # A lost connection closes this attempt, not the decision: say what
            # follows and how many bounded preparations it has used (#1402).
            prepared = cast(int, record.get("connection_stales", 1)) - 1
            facts.append(
                {
                    "label": "What happens next",
                    "value": "Mechanical Librarian prepares it again once "
                    "Audiobookshelf can be reached. "
                    + (
                        f"Prepared again {prepared} of {CONNECTION_REPLAN_LIMIT} times."
                        if prepared
                        else "Not prepared again yet (up to "
                        f"{CONNECTION_REPLAN_LIMIT} times)."
                    ),
                }
            )
            return ReadableChange(
                "Prepared change waiting", "Prepared change waiting"
            ), facts
        facts.append(
            {
                "label": "Confirmed in Audiobookshelf",
                # A withdrawn change whose request never left is not an
                # unconfirmed one (#1402).
                "value": "Nothing was sent to Audiobookshelf"
                if record.get("unsent") is True
                else "The selected change was not confirmed",
            }
        )
        closed = f"Prepared change {_CLOSED_VERBS.get(state, 'closed')}"
        return ReadableChange(closed, closed), facts
    facts = [
        _value_fact("Previous value", destination, previous, record.get("pre_form")),
        _value_fact("New value", destination, desired, desired_form),
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
    # Once planned, a decision that chose a value names what its own write
    # replaced, which an earlier write on the same case can have made differ
    # from the pinned report (#1286); otherwise, the report it was made
    # against. Only accept and owner-entered rows carry ``pre_value``.
    pinned = _pinned_report(record, report)
    current, current_form = (
        (record["pre_value"], record.get("pre_form"))
        if "pre_value" in record
        else (_reported_value(pinned, destination), _reported_form(pinned, destination))
    )
    selected = record.get("selected_value")
    chosen = {"label": "Chosen by", "value": "You"}
    went_back = record.get("went_back")
    if state == "keep_current" and type(went_back) is dict:
        # A keep that went back on the case's own write changed the library:
        # from the written value to the value before it (#1523).
        return went_back_change(
            destination, went_back.get("written"), went_back.get("before")
        ), [
            _value_fact(
                "Written value",
                destination,
                went_back.get("written"),
                went_back.get("written_form"),
            ),
            _value_fact(
                "Value before your change",
                destination,
                went_back.get("before"),
                went_back.get("before_form"),
            ),
            chosen,
        ] + (
            # A write-back that did not land says why, as its write's row does.
            [{"label": "What happened", "value": failure_reason(failure)}]
            if type(failure := went_back.get("failure")) is str and failure
            else []
        )
    if state == "keep_current":
        return keep_change(destination, current), [
            _value_fact("Current value", destination, current, current_form),
            _value_fact("Selected value", destination, current, current_form),
            chosen,
        ]
    if state == "withdraw":
        # A withdrawal records that no owner decision stands, so nothing of
        # this field was chosen or left unchanged by it (ADR-0085).
        return ReadableChange(
            "Withdrawn", f"Withdrew the {destination_label(destination)} decision"
        ), [
            _value_fact("Current value", destination, current, current_form),
            chosen,
            {"label": "Outcome", "value": "The earlier decision no longer applies"},
        ]
    if state == "pin":
        return ReadableChange(
            "Pinned", f"Pinned the {destination_label(destination)} value"
        ), [chosen]
    if state == "unpin":
        return ReadableChange(
            "Unpinned", f"Unpinned the {destination_label(destination)} field"
        ), [
            _value_fact("Current value", destination, current, current_form),
            chosen,
            {
                "label": "Outcome",
                "value": "Mechanical Librarian can ask about this field again",
            },
        ]
    if state == "reject" or type(selected) is not dict:
        return (
            ReadableChange(
                "Left unchanged", f"Left {destination_label(destination)} unchanged"
            ),
            [
                _value_fact("Current value", destination, current, current_form),
                chosen,
            ],
        )
    chosen_value = cast("dict[str, object]", selected).get("value")
    return set_change(destination, current, chosen_value), [
        _value_fact("Previous value", destination, current, current_form),
        _value_fact(
            "New value", destination, chosen_value, record.get("selected_form")
        ),
        chosen,
    ]


def _automatic_facts(
    record: dict[str, object], destination: str, report: MetadataReport | None
) -> tuple[ReadableChange, list[dict[str, str]], bool]:
    """Return the automatic row's change, its facts, and whether it was a check.

    A check is an automatic outcome that left the pinned value as it was: a
    keep, an ignore, or a candidate exactly equal to the reported value.
    """
    pinned = _pinned_report(record, report)
    current = _reported_value(pinned, destination)
    current_form = _reported_form(pinned, destination)
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
        _value_fact("Previous value", destination, current, current_form),
        _value_fact(
            "New value",
            destination,
            selected,
            current_form
            if outcome in {"keep_current", "ignore"}
            else record.get("selected_form"),
        ),
        {"label": "Chosen by", "value": "Automatic policy"},
        {
            "label": "Policy option",
            "value": f"{policy_setting_label(category)}: {policy_option_label(option, category)}"
            if category and option
            else "Not recorded",
        },
    ]
    if type(generation) is int:
        facts.append({"label": "Policy generation", "value": str(generation)})
    taste_version = record.get("taste_version")
    if type(taste_version) is int:
        # Which taste profile version chose a switched cover (#1372 PR 5).
        facts.append({"label": "Taste profile", "value": f"Version {taste_version}"})
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
            "Cover advice failed",
            "Mechanical Librarian",
            "No cover was changed",
        ),
        "selected": ("Chose a different cover", "You", "Recorded"),
        "kept_incumbent": ("Kept the current cover", "You", "Recorded"),
        "no_action": ("Left the cover unchanged", "You", "Recorded"),
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
    ]


def _owner_link_facts(
    record: dict[str, object], state: str, work: str
) -> tuple[ReadableChange, list[dict[str, str]]]:
    """A book link made, changed or withdrawn, in the owner's words (S31).

    A link from an identifiers file (#1478) names its ISBN or ASIN, since it
    keeps no title and names no source.
    """
    if record.get("source") == "file":
        kind = "ISBN" if record.get("scope") == "edition" else "ASIN"
        title = f"{kind} {plain_text(str(record.get('identity', ''))) or ABSENT}"
        source = "Identifiers file"
        on_source = "from an identifiers file"
    else:
        title = plain_text(str(record.get("title", ""))) or ABSENT
        source = provider_label(cast("str | None", record.get("provider")))
        on_source = f"on {source}"
    if state == "link_withdrawn":
        return ReadableChange(
            f"Withdrew the link to {title}",
            f"Withdrew the link from {work} to {title}, {on_source}."
            if record.get("source") == "file"
            else f"Withdrew the link from {work} to {title} {on_source}.",
        ), [
            {"label": "Source", "value": source},
            {
                "label": "What happens next",
                "value": "Withdrawing writes nothing to Audiobookshelf. "
                "This work is prepared again without the link.",
            },
        ]
    linked_as, clause = _LINKED_AS.get(
        cast(str, record.get("scope")), ("Not recorded", "as recorded")
    )
    return ReadableChange(
        f"Linked to {title} {on_source}",
        f"Linked {work} to {title} {on_source}, {clause}.",
    ), [
        {"label": "Source", "value": source},
        {"label": "Linked as", "value": linked_as},
        {
            "label": "What happens next",
            "value": "Linking writes nothing to Audiobookshelf. "
            "This work is prepared again with the link.",
        },
    ]


def _counted(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _identifier_import_facts(
    record: dict[str, object],
) -> tuple[ReadableChange, list[dict[str, str]]]:
    """An identifiers file import or its withdrawal, by its counts (#1478).

    Only immutable counts: how many links are still current is read live
    when the row's details open.
    """
    rows, linked, affected = (
        cast(int, record.get(name, 0)) for name in ("rows", "linked", "affected")
    )
    if record.get("kind") == "withdrawn":
        withdrew = _counted(affected, "link", "links")
        facts = [{"label": "Linked", "value": str(linked)}]
        if linked > affected:
            facts.append(
                {"label": "Changed since the import", "value": str(linked - affected)}
            )
        return ReadableChange(
            f"Withdrew {withdrew}",
            f"Withdrew {withdrew} with their identifiers file import.",
        ), [
            *facts,
            {"label": "Chosen by", "value": "You"},
            {
                "label": "What happens next",
                "value": "Withdrawing writes nothing to Audiobookshelf. "
                "Each work is prepared again without its link.",
            },
        ]
    works = _counted(linked, "work", "works")
    return ReadableChange(
        f"Linked {works}", f"Linked {works} from an identifiers file."
    ), [
        {"label": "Rows in the file", "value": str(rows)},
        {"label": "Linked", "value": str(linked)},
        {"label": "Not linked", "value": str(rows - linked)},
        {"label": "Chosen by", "value": "You"},
        {
            "label": "What happens next",
            "value": "Linking writes nothing to Audiobookshelf. "
            "Each work is prepared again with its link.",
        },
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
        went_back = record.get("went_back")
        if state == "keep_current" and type(went_back) is dict:
            # Its result says it went back, as History does, once its write
            # back has verified. Until then it reads as the change it is
            # making, in its write's own word, as that write's row does: a
            # keep not yet planned is recorded, one refused or failed closed
            # (#1523). Never Kept current.
            written = went_back.get("state")
            if written == "verified":
                event["state"] = "went_back"
            elif type(written) is str:
                event["state"] = written
                event["failure"] = went_back.get("failure")
            else:
                event["state"] = "recorded"
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
                "label": "What happens next",
                "value": "Nothing is written until you choose a description in Library.",
            }
        ]
        if state in {"failed", "interrupted"}:
            facts.append(
                {
                    "label": "Retry",
                    "value": "Request it again from Description in Library. It is not retried on its own.",
                }
            )
    elif source_kind == "description_decision":
        change = ReadableChange(
            "Advised description selected",
            "Selected the advised description",
        )
        selected = record.get("selected_value")
        facts = [
            _value_fact(
                "Selected description",
                "description",
                selected.get("value") if type(selected) is dict else None,
                record.get("selected_form"),
            ),
            {"label": "Chosen by", "value": chooser_label(origin)},
            {
                "label": "What happens next",
                "value": "It is written only with your write permission",
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
        ]
    elif source_kind == "field_effect_no_change":
        # An owner's accept with nothing to write keeps the value the case's
        # latest write holds, not the value the case read.
        pinned = _pinned_report(record, report)
        current, current_form = (
            (record["kept_value"], record.get("kept_form"))
            if "kept_value" in record
            else (
                _reported_value(pinned, destination),
                _reported_form(pinned, destination),
            )
        )
        change = keep_change(destination, current)
        facts = [
            _value_fact("Current value", destination, current, current_form),
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
            {"label": "Changed by", "value": "You"},
            {
                "label": "Policy generation",
                "value": str(record.get("generation", ABSENT)),
            },
        ]
        event["policy_changes"] = changes
    elif source_kind == "owner_link":
        change, facts = _owner_link_facts(
            record, state, str(event.get("work_label", "Recorded work"))
        )
    elif source_kind == "identifier_import":
        change, facts = _identifier_import_facts(record)
        if record.get("kind") == "imported":
            # Its Details read the import live and offer Withdraw this import.
            event["identifier_import_id"] = record.get("import_id")
    elif source_kind == "degraded":
        degraded_reason = cast(str, record.get("degraded_reason", ""))
        unavailable = {
            AUTOMATIC_AUTHORITY_UNAVAILABLE: "The automatic decision it records can no longer be read",
        }.get(degraded_reason, "Details cannot be read")
        change = ReadableChange(
            "Details unavailable", "Recorded details are unavailable"
        )
        facts = [
            {"label": "Reason", "value": unavailable},
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
                {"label": "Written by", "value": "Description adviser"},
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
        values.append(activity_result_label(state, projected.get("failure")).value)
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
