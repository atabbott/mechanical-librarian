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
    TargetSeries,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _target_work_candidate,
    _target_work_report,
)
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
    if scenario == "ordinary_title":
        from tests.support.evidence_led_titles import isbn_title_report

        return (
            isbn_title_report(current="Example Title"),
            "title_layout",
            "preserve",
            "title",
        )
    if scenario in {
        "series_source_order",
        "series_all_qualified",
        "series_primary_narrative",
    }:
        source = _source(
            "hardcover",
            "edition-1",
            titles=("New title",),
            series="Reading Order",
            sequence="2",
        )
        if scenario == "series_primary_narrative":
            source = _provider(
                "hardcover",
                "edition-1",
                (
                    *source.candidates[0].claims,
                    _claim(
                        "hardcover",
                        "edition-1",
                        ClaimKind.SERIES,
                        "Publication Order",
                        scope=ClaimScope.WORK,
                        order=3,
                    ),
                    _claim(
                        "hardcover",
                        "edition-1",
                        ClaimKind.SERIES_SEQUENCE,
                        "1",
                        scope=ClaimScope.WORK,
                        order=4,
                    ),
                ),
                authority="hardcover",
            )
        return (
            _proposal_report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.VALUE,
                        (TargetSeries("Reading Order", "2"),),
                    )
                ),
                providers=(source,),
            ),
            "series",
            scenario.removeprefix("series_"),
            "series",
        )
    rich: dict[str, tuple[str, str, str, str | tuple[str, ...]]] = {
        "genres_fill_empty": ("genres", "fill_empty", "genres", ("Existing",)),
        "genres_add": ("genres", "add", "genres", ("Mystery",)),
        "genres_replace": ("genres", "replace", "genres", ("Mystery",)),
        "tags_fill_empty": ("tags", "fill_empty", "tags", ("Existing",)),
        "tags_add": ("tags", "add", "tags", ("Espionage",)),
        "tags_replace": ("tags", "replace", "tags", ("Espionage",)),
        "description_preserved": (
            "description_fill",
            "safe_plain_text",
            "description",
            "Existing description.",
        ),
        "subtitle_preserved": (
            "subtitle",
            "fill_empty",
            "subtitle",
            "Existing subtitle",
        ),
    }
    if scenario in rich:
        category, option, destination, current = rich[scenario]
        base = _target_work_report(
            (
                (
                    "catalogue",
                    "hardcover",
                    (_target_work_candidate("catalogue", "edition-1", "work-1"),),
                    True,
                ),
            )
        )
        return (
            _proposal_report(
                fields=tuple(
                    TargetField(
                        value.destination,
                        TargetFieldState.VALUE,
                        current,
                        # A plain current description is stored as its text,
                        # so keeping it can write it back (#1424).
                        restorable=destination == "description",
                    )
                    if value.destination.value == destination
                    else value
                    for value in base.target.fields
                ),
                local_claims=base.local_claims,
                providers=base.providers,
            ),
            category,
            option,
            destination,
        )
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
    if scenario == "unproposed_isbn":
        # A filled ebook ISBN no source proposes, beside a filled genre list;
        # the proposed title is what makes the work a case.
        return (
            _proposal_report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN, TargetFieldState.VALUE, "9780140328721"
                    ),
                    genres=TargetField(
                        TargetDestination.GENRES, TargetFieldState.VALUE, ("Existing",)
                    ),
                ),
                providers=(_source("catalogue", "edition-1", titles=("Other title",)),),
            ),
            "isbn_form",
            "preserve_exact",
            "isbn",
        )
    if scenario in {"formatted_isbn", "formatted_isbn13", "unmatched_isbn"}:
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
                        "9780140328721"
                        if scenario == "unmatched_isbn"
                        else "0-306-40615-2",
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
            "prefer_isbn13" if scenario == "formatted_isbn13" else "preserve_exact",
            "isbn",
        )
    raise AssertionError(f"unknown automatic no-change scenario: {scenario}")
