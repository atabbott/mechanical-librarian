"""Shared vocabulary for exact-typed, closed-shape document validation.

Every helper here is a predicate: it answers one question, returns a ``bool``,
and introduces no failure mode of its own. A caller therefore keeps its own
error identity, message, and short-circuit order, so consolidating longhand
into this vocabulary cannot change which inputs are rejected or how.

"Introduces no failure mode of its own" is the guarantee, not "never raises",
and the difference is deliberate. Every helper defined here does return
``False`` rather than raising, for any input at all. But ``closed_document``
accepts caller-supplied field checks and propagates whatever they propagate,
which is what lets a longhand ``value not in FROZEN_SET`` convert to passing
that set's own ``__contains__`` as a field check: an unhashable decoded value
still raises ``TypeError``, at the same point in the caller's sequence, rather
than being quietly rejected. Swallowing that would change behaviour instead of
preserving it, so the guarantee is worded to permit it.

The checks compare ``type(value) is ...`` rather than calling ``isinstance``,
so a subclass is rejected on purpose. ``bool`` is a subclass of ``int``, so a
persisted ``True`` would otherwise satisfy a field the schema declares as an
integer, and a ``str`` subclass carrying extra state would otherwise pass as
plain text. Fail-closed persistence accepts only the exact canonical type.

This module imports nothing from the project, so any layer may depend on it.
"""

from __future__ import annotations

from collections.abc import Callable
from re import Pattern
from typing import cast

FieldCheck = Callable[[object], bool]

OWNER_SESSION_LIFETIME_MINIMUM = 300
OWNER_SESSION_LIFETIME_DEFAULT = 2_592_000
OWNER_SESSION_LIFETIME_MAXIMUM = 2_592_000


def exact_type(expected: type) -> FieldCheck:
    """Accept only the exact type, rejecting every subclass of it."""
    return lambda value: type(value) is expected


boolean = exact_type(bool)
text = exact_type(str)


def bounded_utf8_text(maximum_bytes: int) -> FieldCheck:
    """Accept exact, nonempty text within one UTF-8 byte bound."""

    def check(value: object) -> bool:
        if type(value) is not str:
            return False
        try:
            return 1 <= len(value.encode("utf-8")) <= maximum_bytes
        except UnicodeEncodeError:
            return False

    return check


def prefixed_hex_identity(prefix: str) -> FieldCheck:
    """Accept one exact prefix followed by 64 lowercase hexadecimal characters."""
    return lambda value: (
        type(value) is str
        and len(value) == len(prefix) + 64
        and value.startswith(prefix)
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


def credential(value: object) -> bool:
    """Accept the exact canonical bearer-token payload used by every adapter."""
    return (
        type(value) is str
        and 1 <= len(value.encode("ascii", "ignore")) <= 2048
        and value.encode("ascii", "ignore").decode("ascii") == value
        and value.strip() == value
        and not value.lower().startswith("bearer")
        and all(0x21 <= ord(character) <= 0x7E for character in value)
    )


def matching(pattern: Pattern[str]) -> FieldCheck:
    """Accept exact text whose whole value matches one closed pattern."""
    return lambda value: text(value) and pattern.fullmatch(cast(str, value)) is not None


def literal(expected: object) -> FieldCheck:
    """Accept only one frozen constant, such as a pinned schema version.

    The exact-type check is what keeps this consistent with the rest of the
    vocabulary. Equality alone would accept ``True`` for a constant ``1`` and
    ``False`` for a constant ``0``, because ``bool`` is an ``int`` subclass and
    compares equal — the one hole the module exists to close.
    """
    return lambda value: type(value) is type(expected) and value == expected


def bounded_integer(minimum: int, maximum: int | None = None) -> FieldCheck:
    """Accept only an exact ``int`` inside the inclusive bounds."""

    def check(value: object) -> bool:
        if type(value) is not int:
            return False
        return minimum <= value and (maximum is None or value <= maximum)

    return check


non_negative_integer = bounded_integer(0)
owner_session_lifetime = bounded_integer(
    OWNER_SESSION_LIFETIME_MINIMUM, OWNER_SESSION_LIFETIME_MAXIMUM
)


def closed_document(*unconstrained: str, **schema: FieldCheck) -> FieldCheck:
    """Accept only a ``dict`` carrying exactly these fields, each one valid.

    ``unconstrained`` names fields that must be present but whose content this
    schema deliberately leaves alone, normally because the caller compares them
    against persisted columns instead. ``schema`` names the checked fields.

    The key set must match exactly, so an unknown or missing field is rejected
    before any field check runs. Checked fields are then examined in keyword
    order, and the first failure stops the rest, which lets a converted call
    site keep the order its longhand used and so keep exactly which check
    rejects a given value first — including which one raises. This adds no
    failure mode of its own, but a caller-supplied field check may have one,
    and it is propagated rather than swallowed.
    """
    keys = set(unconstrained) | set(schema)

    def check(value: object) -> bool:
        if type(value) is not dict:
            return False
        return set(value) == keys and all(
            field(value[name]) for name, field in schema.items()
        )

    return check
