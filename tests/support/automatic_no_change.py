"""Reusable naturally-qualified equal automatic policy outcome fixtures."""

from __future__ import annotations

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.support.proposal_evidence import _claim, _fields, _provider
from tests.support.proposal_evidence import _report as _proposal_report


def _source(
    name: str,
    candidate: str,
    *,
    titles: tuple[str, ...],
    edition_title: str | None = None,
    subtitle: str | None = None,
    series: str | None = None,
    sequence: str | None = None,
):
    claims = [
        _claim(
            name, candidate, ClaimKind.TITLE, title, scope=ClaimScope.WORK, order=index
        )
        for index, title in enumerate(titles)
    ]
    for kind, value, scope, applicability in (
        (ClaimKind.TITLE, edition_title, ClaimScope.EDITION, None),
        (ClaimKind.SUBTITLE, subtitle, ClaimScope.EDITION, "ebook"),
        (ClaimKind.SERIES, series, ClaimScope.WORK, None),
        (ClaimKind.SERIES_SEQUENCE, sequence, ClaimScope.WORK, None),
    ):
        if value is not None:
            claims.append(
                _claim(
                    name,
                    candidate,
                    kind,
                    value,
                    scope=scope,
                    order=len(claims),
                    applicability=applicability or "not_applicable",
                )
            )
    return _provider(
        name,
        candidate,
        tuple(claims),
        authority=name
        if name in {"google_books", "hardcover", "open_library"}
        else None,
    )


def _automatic_no_change_inputs(
    scenario: str,
) -> tuple[MetadataReport, str, str, str]:
    """Build one naturally qualified, equal automatic policy outcome."""
    if scenario in {"exact_title", "canonical_title"}:
        current_title = (
            "Secret History" if scenario == "exact_title" else "secret history"
        )
        local_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Secret History",
            canonical="Secret History",
            excerpt="Secret History",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        return (
            _proposal_report(
                fields=_fields(
                    current_title,
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE, TargetFieldState.NULL
                    ),
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    ),
                ),
                local_claims=(local_title,),
                providers=(
                    _source(
                        "catalogue",
                        "edition-1",
                        titles=("Secret History",),
                        subtitle="A Mistborn Novella",
                    ),
                ),
            ),
            "title_layout",
            "title_and_subtitle",
            "title",
        )
    if scenario in {"exact_authors", "canonical_authors"}:
        authors = ("Mick Herron",) if scenario == "exact_authors" else ("mick herron",)
        local_creator = LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed="Mick Herron",
            canonical="Mick Herron",
            excerpt="Mick Herron",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role="author",
        )
        return (
            _proposal_report(
                fields=_fields(
                    authors=TargetField(
                        TargetDestination.AUTHORS, TargetFieldState.VALUE, authors
                    ),
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.VALUE,
                        "0-306-40615-2",
                    ),
                ),
                local_claims=(
                    local_creator,
                    LocalClaim.create(
                        kind=ClaimKind.ISBN,
                        observed="0-306-40615-2",
                        canonical="0306406152",
                        excerpt="0-306-40615-2",
                        applicability="ebook",
                        checksum="valid_isbn_10",
                        applicability_basis="ebook",
                        scope=ClaimScope.EDITION,
                    ),
                ),
                providers=(
                    _provider(
                        "catalogue",
                        "work-1",
                        (
                            _claim(
                                "catalogue",
                                "work-1",
                                ClaimKind.CREATOR,
                                "Mick Herron",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                            _claim(
                                "catalogue",
                                "work-1",
                                ClaimKind.ISBN,
                                "9780306406157",
                                scope=ClaimScope.EDITION,
                                applicability="ebook",
                                order=1,
                            ),
                        ),
                    ),
                ),
            ),
            "contributor_names",
            "preserve",
            "authors",
        )
    if scenario == "formatted_isbn":
        isbn10 = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0-306-40615-2",
            canonical="0306406152",
            excerpt="0-306-40615-2",
            applicability="ebook",
            checksum="valid_isbn_10",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        return (
            _proposal_report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.VALUE,
                        "0-306-40615-2",
                    )
                ),
                local_claims=(isbn10,),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.ISBN,
                                "9780306406157",
                                scope=ClaimScope.EDITION,
                                applicability="ebook",
                            ),
                        ),
                    ),
                ),
            ),
            "isbn_form",
            "preserve_exact",
            "isbn",
        )
    raise AssertionError(f"unknown automatic no-change scenario: {scenario}")
