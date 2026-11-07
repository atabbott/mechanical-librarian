"""A value only one uncorroborated source proposes stays choosable, unqueued (#1310)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.schema import _SINGLE_SOURCE_CHOICE_SQL
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
    _needs_attention,
    _single_source_choice,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderClaim,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    publish_audiobook_preview,
)
from tests.support.genre_reports import genre_report, publish_genre_case
from tests.support.hardcover_work_route import audio_edition, hardcover_work_report
from tests.support.review_cases import _report

_SINGLE_SOURCE = (
    "Only one source suggests this, and nothing else confirms it, so it isn't "
    "used and no review is needed. You can still choose it."
)


def _with_publisher(
    report: MetadataReport, current: str, proposed: tuple[str, ...]
) -> MetadataReport:
    """State `proposed` publishers on every candidate and `current` on the item."""
    providers = tuple(
        replace(
            provider,
            candidates=tuple(
                replace(
                    candidate,
                    claims=candidate.claims
                    + tuple(
                        ProviderClaim.create(
                            provider=provider.provider,
                            candidate=candidate.identity,
                            kind=ClaimKind.PUBLISHER,
                            value=value,
                            state=FieldState.VALID,
                            scope=ClaimScope.EDITION,
                            applicability="not_applicable",
                            raw_order=60 + index,
                        )
                        for index, value in enumerate(proposed)
                    ),
                )
                for candidate in provider.candidates
            ),
        )
        for provider in report.providers
    )
    fields = tuple(
        TargetField(TargetDestination.PUBLISHER, TargetFieldState.VALUE, current)
        if field.destination is TargetDestination.PUBLISHER
        else field
        for field in report.target.fields
    )
    return MetadataReport.create(
        target=TargetFacts(
            report.target.connection,
            report.target.library,
            report.target.item,
            fields,
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=providers,
        audio=report.audio,
    )


@pytest.mark.parametrize("medium", ("audiobook", "ebook"))
def test_a_single_source_replacement_is_choosable_and_not_queued(
    tmp_path: Path, medium: str
) -> None:
    """The case page, the queue and the persisted facts agree on every medium."""
    database = tmp_path / "application.sqlite3"
    with closing(open_repository(database)) as repository:
        if medium == "audiobook":
            report = _with_publisher(
                admitted_audiobook_report(), "Old Mill Books", ("Harbor Light Audio",)
            )
            case_id, _ = publish_audiobook_preview(repository, report, ("isbn_form",))
        else:
            report = _with_publisher(
                genre_report(current=("Mystery",), genres=("Fantasy",)),
                "Old Mill Books",
                ("Harbor Light Audio",),
            )
            case_id, _ = publish_genre_case(repository, report, "automatic")
    publisher = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.PUBLISHER
    )
    assert publisher.reasons == ("insufficient_independent_corroboration",)
    with closing(open_repository(database, create=False)) as repository:
        detail = InspectCurrentReviewCase(repository).run(case_id)
        field = next(f for f in detail["fields"] if f["destination"] == "publisher")
        assert field["deterministic_disposition"] == "unresolved_choice"
        assert field["classification"] == "owner_choosable"
        assert field["classification_reason"] == _SINGLE_SOURCE
        assert field["needs_attention"] is False
        assert field["decision_actions"]
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        assert "publisher" not in {item["destination"] for item in attention["items"]}
        assert attention["total"] == repository.count_current_attention_fields(None)
        assert (
            repository._connection.execute(
                "SELECT unresolved_choice FROM review_case_field_facts "
                "WHERE review_case_id=? AND destination='publisher'",
                (case_id,),
            ).fetchone()[0]
            == 0
        )


def _read_field(database: Path, case_id: str, destination: str) -> dict[str, object]:
    """Read one field through the case page, the queue and the persisted facts.

    The three must agree on whether the field asks, and the field stays the
    owner's to choose either way.
    """
    with closing(open_repository(database, create=False)) as repository:
        detail = InspectCurrentReviewCase(repository).run(case_id)
        field = next(f for f in detail["fields"] if f["destination"] == destination)
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        queued = destination in {item["destination"] for item in attention["items"]}
        assert attention["total"] == repository.count_current_attention_fields(None)
        fact = repository._connection.execute(
            "SELECT unresolved_choice FROM review_case_field_facts "
            "WHERE review_case_id=? AND destination=?",
            (case_id, destination),
        ).fetchone()[0]
    assert field["deterministic_disposition"] == "unresolved_choice"
    assert field["decision_actions"]
    assert field["needs_attention"] is queued
    return {"field": field, "queued": queued, "fact": fact}


@pytest.mark.parametrize("contributor_names", ("automatic", "ask"))
def test_a_single_source_narrator_fill_is_choosable_and_asks_only_under_ask(
    tmp_path: Path, contributor_names: str
) -> None:
    """A cast one unconfirmed audio edition states is held by the narrator guard.

    It reaches the single-source shape through `_narrator_corroboration_guard`
    rather than `_evaluate`, leaves the queue under Automatic and still asks
    under an explicit Ask.
    """
    report = hardcover_work_report(editions=(audio_edition(3),))
    narrators = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.NARRATORS
    )
    assert narrators.reasons == ("insufficient_independent_corroboration",)
    assert narrators.alternatives[0].assessment.value == "fills_empty"
    database = tmp_path / "application.sqlite3"
    with closing(open_repository(database)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("isbn_form",),
            selected_destinations={"contributor_names": contributor_names},
        )
    read = _read_field(database, case_id, "narrators")
    if contributor_names == "ask":
        assert read["queued"] is True
        assert read["field"]["classification"] == "needs_attention"
        assert read["field"]["classification_reason"] != _SINGLE_SOURCE
    else:
        assert read["queued"] is False
        assert read["field"]["classification"] == "owner_choosable"
        assert read["fact"] == 0
        assert read["field"]["classification_reason"] == _SINGLE_SOURCE


@pytest.mark.parametrize("title_layout", ("automatic", "ask"))
def test_an_ebooks_single_source_title_is_choosable_and_asks_only_under_ask(
    tmp_path: Path, title_layout: str
) -> None:
    """An EPUB's opening matter alone proposing a title is single-source.

    It never reads "nothing to decide": it is choosable with the single-source
    wording, and an explicit Ask for titles still asks.
    """
    report = _report(competing=False)
    title = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.TITLE
    )
    assert title.reasons == ("insufficient_independent_corroboration",)
    database = tmp_path / "application.sqlite3"
    with closing(open_repository(database)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("isbn_form",),
            selected_destinations={"title_layout": title_layout},
        )
    read = _read_field(database, case_id, "title")
    assert "nothing to decide" not in read["field"]["classification_reason"].casefold()
    assert read["queued"] is (title_layout == "ask")
    if title_layout == "automatic":
        assert read["field"]["classification"] == "owner_choosable"
        assert read["fact"] == 0
        assert read["field"]["classification_reason"] == _SINGLE_SOURCE


def test_two_sources_proposing_different_titles_still_ask(tmp_path: Path) -> None:
    """A choice between two sources' values is not single-source and asks."""
    report = _report()
    title = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.TITLE
    )
    assert title.reasons == ("competing_evidence",)
    database = tmp_path / "application.sqlite3"
    with closing(open_repository(database)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("isbn_form",),
            selected_destinations={"title_layout": "automatic"},
        )
    read = _read_field(database, case_id, "title")
    assert read["queued"] is True
    assert read["fact"] == 1
    assert read["field"]["classification_reason"] != _SINGLE_SOURCE


def _alternative(assessment: str, correlations: int) -> dict[str, object]:
    return {
        "assessment": assessment,
        "correlations": [f"correlation-{index}" for index in range(correlations)],
    }


@pytest.mark.parametrize(
    ("outcome", "reasons", "alternatives", "single_source"),
    (
        (
            "unresolved_choice",
            ["insufficient_independent_corroboration"],
            [("replaces_target", 1)],
            True,
        ),
        # Only a choice can be a single-source choice.
        (
            "propose_change",
            ["insufficient_independent_corroboration"],
            [("replaces_target", 1)],
            False,
        ),
        (
            "unresolved_choice",
            ["insufficient_independent_corroboration"],
            [("fills_empty", 0)],
            True,
        ),
        (
            "unresolved_choice",
            ["insufficient_independent_corroboration"],
            [("preserves_target", 2), ("replaces_target", 1)],
            True,
        ),
        # Two independent sources are corroboration, not a single source.
        (
            "unresolved_choice",
            ["insufficient_independent_corroboration"],
            [("replaces_target", 2)],
            False,
        ),
        # Two alternatives are a choice between values, which still asks.
        (
            "unresolved_choice",
            ["insufficient_independent_corroboration"],
            [("replaces_target", 1), ("replaces_target", 1)],
            False,
        ),
        ("unresolved_choice", ["competing_evidence"], [("replaces_target", 1)], False),
        (
            "unresolved_choice",
            ["insufficient_independent_corroboration", "competing_evidence"],
            [("replaces_target", 1)],
            False,
        ),
    ),
)
def test_the_case_page_and_the_facts_read_one_single_source_shape(
    outcome: str,
    reasons: list[str],
    alternatives: list[tuple[str, int]],
    single_source: bool,
) -> None:
    """`_single_source_choice` and `_SINGLE_SOURCE_CHOICE_SQL` agree (#1310)."""
    result = {
        "destination": "publisher",
        "outcome": outcome,
        "reasons": reasons,
        "alternatives": [_alternative(*value) for value in alternatives],
    }
    proposal = {"destination": "publisher", "result": result, "manual_required": False}
    assert _single_source_choice(proposal) is single_source
    assert _needs_attention(proposal, settled_choice_destinations=()) is (
        outcome == "unresolved_choice" and not single_source
    )
    connection = sqlite3.connect(":memory:")
    assert connection.execute(
        "SELECT " + _SINGLE_SOURCE_CHOICE_SQL + " FROM (SELECT ? AS value) result",
        (json.dumps(result),),
    ).fetchone() == (int(single_source),)
