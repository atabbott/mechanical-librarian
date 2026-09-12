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
COVER_AWARE_QUALIFIED_POLICY_OPTIONS: Final = QUALIFIED_POLICY_OPTIONS | {
    COVER_QUALIFIED_POLICY_OPTION
}


def cover_aware_qualified_policy_options() -> frozenset[str]:
    """Return every `category:option` the owner may route to Automatic now.

    An audiobook record widens this set without any other edit, so the answer
    is composed on each ask rather than frozen at import (#967).
    """
    return frozenset(COVER_AWARE_QUALIFIED_POLICY_OPTIONS) | qualified_policy_bases()
