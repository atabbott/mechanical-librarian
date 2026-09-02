"""Public, aggregate-safe F38 contributor-display qualification envelope."""

from __future__ import annotations

from typing import Final

F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY: Final = "contributor_names"
F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION: Final = "canonical:provider_consensus_display"
F38_QUALIFICATION_MANIFEST_SHA256: Final = (
    "165b9011db96374d25e4ab0c9cc4fef7606805c90488afd38b59dc12e24fbead"
)

__all__ = [
    "F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY",
    "F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION",
    "F38_QUALIFICATION_MANIFEST_SHA256",
]
