"""Public identity of the separately qualified automatic cover policy."""

from typing import Final

from mechanical_librarian.domain.audiobook_decision_qualification import (
    qualified_policy_bases,
)
from mechanical_librarian.domain.automatic_decision import QUALIFIED_POLICY_OPTIONS

COVER_ELECTION_QUALIFICATION_MANIFEST_SHA256: Final = (
    "b91ae72a8c4e55c9bfe1d514c28210e22427b57a03082d2b5d1ae0b40df7c457"
)
COVER_QUALIFIED_POLICY_OPTION: Final = "cover_order:fixed_source_order"
# The shadow identity of a measured-rule replacement of a confirmed current
# cover (ADR-0093, #1363 S6). No shipped record names it, so until #1418
# records a qualification every rule-made switch is a suggestion.
MEASURED_REPLACEMENT_IDENTITY: Final = (
    "cover_order:fixed_source_order:measured_replacement"
)
MEASURED_REPLACEMENT_QUALIFICATION_SHA256: str | None = None
# The shadow identity of a replacement of a current cover that is not
# confirmed: readable but unconfirmed, or unreadable (ADR-0094, #1362 S9). It
# has its own record, so #1418 can activate it apart from the measured one.
UNCONFIRMED_REPLACEMENT_IDENTITY: Final = (
    "cover_order:fixed_source_order:unconfirmed_replacement"
)
UNCONFIRMED_REPLACEMENT_QUALIFICATION_SHA256: str | None = None
# The actual target state each replacement identity may change.
REPLACEMENT_TARGET_STATES: Final = {
    MEASURED_REPLACEMENT_IDENTITY: "confirmed",
    UNCONFIRMED_REPLACEMENT_IDENTITY: "unknown",
}
COVER_AWARE_QUALIFIED_POLICY_OPTIONS: Final = QUALIFIED_POLICY_OPTIONS | {
    COVER_QUALIFIED_POLICY_OPTION
}


def measured_replacement_record() -> str | None:
    """Return the digest of the record qualifying measured replacement, if any.

    It covers a confirmed current cover only. Read on each ask, like the
    audiobook envelope, so a record activates it without another edit.
    """
    return MEASURED_REPLACEMENT_QUALIFICATION_SHA256


def unconfirmed_replacement_record() -> str | None:
    """Return the digest of the record qualifying unconfirmed replacement, if any.

    Read on each ask, like ``measured_replacement_record``.
    """
    return UNCONFIRMED_REPLACEMENT_QUALIFICATION_SHA256


def replacement_record(identity: str | None) -> str | None:
    """Return the record of either replacement identity; ``None`` for any other."""
    if identity == MEASURED_REPLACEMENT_IDENTITY:
        return measured_replacement_record()
    if identity == UNCONFIRMED_REPLACEMENT_IDENTITY:
        return unconfirmed_replacement_record()
    return None


def cover_aware_qualified_policy_options() -> frozenset[str]:
    """Return every `category:option` the owner may route to Automatic now.

    An audiobook record widens this set without any other edit, so the answer
    is composed on each ask rather than frozen at import (#967).
    """
    return frozenset(COVER_AWARE_QUALIFIED_POLICY_OPTIONS) | qualified_policy_bases()
