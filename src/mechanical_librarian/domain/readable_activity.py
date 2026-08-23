"""Readable owner language for one immutable Activity record.

Every function here is pure and takes only values that already arrived from a
single event's own decision, plan or verification document. Nothing in this
module reads a catalogue, a lineage or a latest-state row, which is what makes
a historical Activity row stable: replay the same event document a year later,
against a library whose current metadata has moved on, and the same words come
back.

The vocabulary is deliberately small. A row says which work or setting changed,
the exact change, who or what chose it, and what came of it -- in that order --
and an entry that was not a change (a recovery, a paused write, a closed
preparation) says what happened instead rather than being forced into empty
previous/new fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

__all__ = [
    "ABSENT",
    "AUTOMATIC_AUTHORITY_UNAVAILABLE",
    "CHANGE_LIMIT",
    "EVIDENCE_LIMIT",
    "ReadableChange",
    "chooser_label",
    "destination_label",
    "failure_reason",
    "keep_change",
    "policy_option_label",
    "policy_setting_label",
    "set_change",
    "value_text",
]

# The row's change column is one line at desktop and one wrapped line on a
# compact card, so a 32KB description is bounded here rather than left to CSS:
# `text-overflow` hides an overrun at desktop but a compact card sets
# `white-space:normal`, where the same string would push the three-row card to
# whatever height the description happens to want.
CHANGE_LIMIT = 120
# Expanded evidence has room to be read, and the transport bounds a fact value
# at 512 characters.
EVIDENCE_LIMIT = 512
ABSENT = "Not set"
AUTOMATIC_AUTHORITY_UNAVAILABLE = "automatic_authority_unavailable"
_ELLIPSIS = "…"
_ARROW = "→"
# An opaque durable cover identity. It names an exact image and means nothing
# to a reader, so it is never printed; the change is described instead.
_COVER_TOKEN = re.compile(r"^cover_[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

_DESTINATION_LABELS = {
    "asin": "ASIN",
    "authors": "Authors",
    "cover": "Cover",
    "description": "Description",
    "genres": "Genres",
    "isbn": "ISBN",
    "language": "Language",
    "policy": "Policy",
    "publishedDate": "Published date",
    "publishedYear": "Published year",
    "publisher": "Publisher",
    "series": "Series",
    "subtitle": "Subtitle",
    "tags": "Tags",
    "title": "Title",
}
_CHOOSER_LABELS = {
    "automatic_pipeline": "Automatic preparation",
    "automatic_policy": "Automatic policy",
    "human": "Library owner",
    "owner_retry": "Library owner",
    "policy": "Library owner",
    "unknown": "Not recorded",
}
# Owner-readable causes for the closed durable failure vocabulary. A code the
# owner cannot act on is still named as something that happened to their
# library rather than as an internal token.
_FAILURE_REASONS = {
    "ambiguous": "The current value could not be read back unambiguously",
    "authority": "The authority behind this change was replaced",
    "authority_superseded": "The authority behind this change was replaced",
    "backup": "A safety copy of the current value was unavailable",
    "capability": "Audiobookshelf did not offer this field",
    "circuit_open": "Writing paused after repeated failures",
    "consent": "Write permission was not in place",
    "consent_revoked": "Write permission was withdrawn",
    "cover_recovery": "Cover recovery took this change over",
    "dependency": "Something this change depended on moved",
    "not_applied": "Audiobookshelf did not apply the change",
    "owner_abandoned": "You stopped this change",
    "readback": "The change could not be confirmed after writing",
    "restart_readback": "The change could not be confirmed after a restart",
    "retry_exhausted": "The change was retried and then stopped safely",
    "target_changed": "The current value changed after this was prepared",
    "target_failure": "Audiobookshelf refused the change",
    "third_value": "The current value became a third, unexpected value",
}


@dataclass(frozen=True, slots=True)
class ReadableChange:
    """One change in two registers that never disagree.

    ``compact`` is what the row column shows and ``sentence`` is the whole
    statement a screen reader announces. They are produced together from the
    same values so the short form can use ``→`` -- which most screen
    readers either skip or read as "right arrow" -- without the announced row
    losing the word the arrow stands for.
    """

    compact: str
    sentence: str


def _tidy(value: str, limit: int) -> str:
    text = _WHITESPACE.sub(" ", _CONTROL.sub(" ", value)).strip()
    if not text:
        return ABSENT
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + _ELLIPSIS


def destination_label(destination: str) -> str:
    """Name a durable destination the way the owner reads it."""
    named = _DESTINATION_LABELS.get(destination)
    if named is not None:
        return named
    # Sentence case, the one convention every server-named field reads in:
    # `publishedYear` and `published_year` both become `Published year`, so an
    # unmapped destination never arrives looking like a proper noun.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", destination.replace("_", " "))
    tidied = _tidy(spaced.lower(), 64)
    return tidied[:1].upper() + tidied[1:]


def chooser_label(origin: str) -> str:
    """Name who or what chose, from the event's own recorded origin."""
    return _CHOOSER_LABELS.get(origin, "Not recorded")


def failure_reason(failure: str) -> str:
    """State an owner-readable cause for a durable failure code."""
    return _FAILURE_REASONS.get(failure, "The change stopped safely")


def policy_setting_label(category: str) -> str:
    """Name a policy option category the way the Policy surface names it."""
    return destination_label(category)


def policy_option_label(option: str) -> str:
    """Name a policy option value in owner language."""
    named = {
        "all_qualified": "All qualified memberships",
        "ask": "Ask me",
        "automatic": "Automatic",
        "canonical": "Canonical form",
        "fill_empty": "Fill when empty",
        "ignore": "Skip it",
        "preserve": "Leave as it is",
        "primary_narrative": "Primary narrative",
        "safe_plain_text": "Safe plain text",
        "unsupported": "Not supported",
    }.get(option)
    if named is not None:
        return named
    spaced = _tidy(option.replace("_", " "), 64)
    return spaced[:1].upper() + spaced[1:]


def value_text(destination: str, value: object, *, limit: int = EVIDENCE_LIMIT) -> str:
    """Render one exact durable field value as safe, readable owner text.

    The closed durable vocabulary is a string, a list of strings, a list of
    ``{name, sequence}`` series memberships, an opaque cover identity, or
    absence. Anything outside it renders as absence rather than as a shape the
    reader has to decode.
    """
    if value is None:
        return ABSENT
    if type(value) is str:
        if destination == "cover":
            return (
                "A recorded cover image"
                if _COVER_TOKEN.match(value)
                else _tidy(value, limit)
            )
        return _tidy(value, limit)
    if type(value) is bool:
        return "Yes" if value else "No"
    if type(value) is list:
        if not value:
            return ABSENT
        if all(type(item) is str for item in value):
            return _tidy(", ".join(str(item) for item in value), limit)
        if all(
            type(item) is dict and set(item) == {"name", "sequence"} for item in value
        ):
            parts = []
            for item in value:
                entry = dict(cast("dict[str, object]", item))
                name, sequence = entry["name"], entry["sequence"]
                if type(name) is not str:
                    return ABSENT
                parts.append(
                    f"{name} ({sequence})"
                    if type(sequence) is str and sequence
                    else name
                )
            return _tidy(", ".join(parts), limit)
    return ABSENT


def set_change(destination: str, previous: object, new: object) -> ReadableChange:
    """State a change from one exact recorded value to another."""
    field = destination_label(destination)
    if destination == "cover":
        return ReadableChange(
            f"Current cover {_ARROW} selected cover",
            f"Set {field} from the current image to the selected image",
        )
    before = value_text(destination, previous, limit=CHANGE_LIMIT)
    after = value_text(destination, new, limit=CHANGE_LIMIT)
    if after == ABSENT:
        return ReadableChange(f"Removed {field.lower()}", f"Removed {field}")
    if before == after:
        return keep_change(destination, new)
    return ReadableChange(
        f"{before} {_ARROW} {after}", f"Set {field} from {before} to {after}"
    )


def keep_change(destination: str, value: object) -> ReadableChange:
    """State that an exact recorded value was deliberately retained."""
    field = destination_label(destination)
    if destination == "cover":
        return ReadableChange(
            "Kept the current cover", f"Kept {field} as the current image"
        )
    kept = value_text(destination, value, limit=CHANGE_LIMIT)
    if kept == ABSENT:
        return ReadableChange(f"Left {field.lower()} unset", f"Left {field} unset")
    return ReadableChange(f"Kept {kept}", f"Kept {field} as {kept}")
