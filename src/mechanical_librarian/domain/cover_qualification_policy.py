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
# The shadow identity of a measured-rule replacement of a populated cover
# (ADR-0093, #1363 S6). No shipped record names it, so until #1418 records a
# qualification every rule-made switch is a suggestion.
MEASURED_REPLACEMENT_IDENTITY: Final = (
    "cover_order:fixed_source_order:measured_replacement"
)
MEASURED_REPLACEMENT_QUALIFICATION_SHA256: str | None = None
COVER_AWARE_QUALIFIED_POLICY_OPTIONS: Final = QUALIFIED_POLICY_OPTIONS | {
    COVER_QUALIFIED_POLICY_OPTION
}


def measured_replacement_record() -> str | None:
    """Return the digest of the record qualifying measured replacement, if any.

    Read on each ask, like the audiobook envelope, so a record activates it
    without another edit.
    """
    return MEASURED_REPLACEMENT_QUALIFICATION_SHA256


def cover_aware_qualified_policy_options() -> frozenset[str]:
    """Return every `category:option` the owner may route to Automatic now.

    An audiobook record widens this set without any other edit, so the answer
    is composed on each ask rather than frozen at import (#967).
    """
    return frozenset(COVER_AWARE_QUALIFIED_POLICY_OPTIONS) | qualified_policy_bases()
