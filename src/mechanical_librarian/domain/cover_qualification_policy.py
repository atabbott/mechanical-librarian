"""Public identity of the separately qualified automatic cover policy."""

from typing import Final

from mechanical_librarian.domain.automatic_decision import QUALIFIED_POLICY_OPTIONS

COVER_ELECTION_QUALIFICATION_MANIFEST_SHA256: Final = (
    "b91ae72a8c4e55c9bfe1d514c28210e22427b57a03082d2b5d1ae0b40df7c457"
)
COVER_QUALIFIED_POLICY_OPTION: Final = "cover_order:fixed_source_order"
COVER_AWARE_QUALIFIED_POLICY_OPTIONS: Final = QUALIFIED_POLICY_OPTIONS | {
    COVER_QUALIFIED_POLICY_OPTION
}
