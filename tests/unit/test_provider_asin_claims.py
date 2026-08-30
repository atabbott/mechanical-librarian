"""One ASIN grammar on the report path, and the strict write-path guards.

ADR-0061 clause 4 admits ISBN-10-shaped ASINs without a prefix filter, so the
retained report validates a provider ASIN claim against the same canonical form
the ASIN discovery lane is built from. The proposal and shadow-review guards
stay strict, which ADR-0064 records as deferred rather than decided.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    ProviderClaim,
    ReportValueError,
    TargetDestination,
    canonical_asin,
)
from mechanical_librarian.domain.proposal_set import _eligible_identifier
from mechanical_librarian.domain.shadow_review import _ASIN as _SHADOW_ASIN

_CANONICAL = ("B08G9PRS1K", "0743563123", "9780306406", "0000000000", "ZZZZZZZZZZ")
_REFUSED = (
    "B08G9PRS1",
    "B08G9PRS1KK",
    "B08G9PRS1K!",
    "B08G9PRS-1K",
    "B08G9PRS 1",
    "b08g9prs1k",
    "",
)


def _claim(value: str) -> ProviderClaim:
    return ProviderClaim.create(
        provider="audible",
        candidate="audible-product",
        kind=ClaimKind.ASIN,
        value=value,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )


@pytest.mark.parametrize("value", _CANONICAL)
def test_the_report_layer_accepts_every_canonical_identifier(value: str) -> None:
    assert value == _claim(value).value
    assert value == canonical_asin(value)


@pytest.mark.parametrize("value", _REFUSED)
def test_the_report_layer_still_refuses_a_noncanonical_identifier(value: str) -> None:
    assert canonical_asin(value) != value
    with pytest.raises(ReportValueError, match="invalid"):
        ProviderClaim(
            "provider_" + "0" * 64,
            ClaimKind.ASIN,
            value,
            FieldState.VALID,
            ClaimScope.EDITION,
            "not_applicable",
        )


def test_the_claim_factory_normalises_case_and_the_grammar_stays_shared() -> None:
    """`create` upper-cases before validating, so both spellings are one claim."""
    assert "B08G9PRS1K" == _claim("b08g9prs1k").value
    assert _claim("b08g9prs1k").reference == _claim("B08G9PRS1K").reference
    for value in (*_CANONICAL, *_REFUSED, "  B08G9PRS1K  "):
        report_admits = canonical_asin(value) == value
        try:
            ProviderClaim(
                "provider_" + "0" * 64,
                ClaimKind.ASIN,
                value,
                FieldState.VALID,
                ClaimScope.EDITION,
                "not_applicable",
            )
        except ReportValueError:
            assert not report_admits
        else:
            assert report_admits


def test_the_proposal_path_still_refuses_an_isbn_ten_shaped_identifier() -> None:
    """ADR-0064's deferred scope: the write destination stays `B0`-shaped."""
    assert _eligible_identifier(TargetDestination.ASIN, "B08G9PRS1K", "not_applicable")
    assert not _eligible_identifier(
        TargetDestination.ASIN, "0743563123", "not_applicable"
    )


def test_the_shadow_review_linkage_guard_stays_strict() -> None:
    assert _SHADOW_ASIN.fullmatch("B08G9PRS1K") is not None
    assert _SHADOW_ASIN.fullmatch("0743563123") is None
    assert canonical_asin("0743563123") == "0743563123"
