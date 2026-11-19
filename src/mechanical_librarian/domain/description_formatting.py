"""Which formatted copy a description alternative carries (#1995, ADR-0107).

An alternative is a visible text several provider claims may state. Each
claim that rendered markup keeps its own formatted copy; the alternative
carries one only when every copy among its claims is the same. Two different
copies behind the same words, or none, leave the alternative plain.
"""

from __future__ import annotations

from collections.abc import Iterable

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    MetadataReport,
    ProviderClaim,
)


def chosen_formatted(copies: Iterable[str | None]) -> str | None:
    """The one distinct copy among these, or `None` for none or several."""
    distinct = {copy for copy in copies if copy is not None}
    return next(iter(distinct)) if len(distinct) == 1 else None


def alternative_formatted(
    report: MetadataReport, references: Iterable[str], value: object
) -> str | None:
    """The copy of one description alternative citing these claims.

    Only description claims stating exactly the alternative's visible text
    count, live or kept from a refused answer (#1355).
    """
    if type(value) is not str:
        return None
    cited = set(references)
    return chosen_formatted(
        claim.formatted
        for provider in report.providers
        for candidate in (*provider.candidates, *provider.identity_refused_candidates)
        for claim in candidate.claims
        if _cites(claim, cited, value)
    )


def _cites(claim: ProviderClaim, cited: set[str], value: str) -> bool:
    return (
        claim.reference in cited
        and claim.kind is ClaimKind.DESCRIPTION
        and claim.value == value
    )


__all__ = ["alternative_formatted", "chosen_formatted"]
