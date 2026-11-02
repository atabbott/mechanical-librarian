"""Choices a library's options settle stay the owner's on every medium (#1310)."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.genre_reports import genre_report, publish_genre_case
from tests.support.proposal_evidence import _claim

_TAGS_OFF = (
    "Tags are off in your settings, so your tags are left as they are and no "
    "review is needed. You can still choose proposed tags."
)


def _with_tags(
    report: MetadataReport, current: tuple[str, ...], tags: tuple[str, ...]
) -> MetadataReport:
    providers = tuple(
        replace(
            provider,
            candidates=tuple(
                replace(
                    candidate,
                    claims=candidate.claims
                    + tuple(
                        _claim(
                            "catalogue",
                            "edition-1",
                            ClaimKind.TAG,
                            value,
                            scope=ClaimScope.WORK,
                            order=40 + index,
                        )
                        for index, value in enumerate(tags)
                    ),
                )
                for candidate in provider.candidates
            ),
        )
        for provider in report.providers
    )
    fields = tuple(
        TargetField(TargetDestination.TAGS, TargetFieldState.VALUE, current)
        if field.destination is TargetDestination.TAGS
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
    )


@pytest.mark.parametrize("genres_destination", ("automatic", "ask"))
def test_an_ebooks_tags_under_off_are_kept_and_still_choosable(
    tmp_path: Path, genres_destination: str
) -> None:
    """With tags off, an ebook's tags choice is optional, never 'nothing to decide'.

    The case page, the queue and the persisted facts agree, and an explicit Ask
    for genres still asks about genres.
    """
    report = _with_tags(
        genre_report(current=("Mystery",), genres=("Fantasy",)),
        ("old",),
        ("Espionage", "Moody"),
    )
    assert report.file.format == "epub"
    database = tmp_path / "application.sqlite3"
    with closing(open_repository(database)) as repository:
        case_id, proposal = publish_genre_case(repository, report, genres_destination)
    tags = next(r for r in proposal.results if r.destination is TargetDestination.TAGS)
    assert tags.outcome.value == "unresolved_choice"
    with closing(open_repository(database, create=False)) as repository:
        detail = InspectCurrentReviewCase(repository).run(case_id)
        field = next(f for f in detail["fields"] if f["destination"] == "tags")
        assert field["classification"] == "owner_choosable"
        assert field["needs_attention"] is False
        assert field["decision_actions"]
        assert field["classification_reason"] == _TAGS_OFF
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        queued = {item["destination"] for item in attention["items"]}
        assert "tags" not in queued
        assert ("genres" in queued) is (genres_destination == "ask")
        assert attention["total"] == repository.count_current_attention_fields(None)
        assert (
            repository._connection.execute(
                "SELECT unresolved_choice FROM review_case_field_facts "
                "WHERE review_case_id=? AND destination='tags'",
                (case_id,),
            ).fetchone()[0]
            == 0
        )


def _tags_fact(repository: object, case_id: str) -> int:
    row = repository._connection.execute(  # type: ignore[attr-defined]
        "SELECT unresolved_choice FROM review_case_field_facts "
        "WHERE review_case_id=? AND destination='tags'",
        (case_id,),
    ).fetchone()
    return int(row[0])


def test_storing_a_cases_policy_later_re_derives_its_settled_facts(
    tmp_path: Path,
) -> None:
    """A case admitted before its resolved policy was stored keeps opening.

    Its facts are derived again when that policy's first row is stored, so the
    persisted facts equal what a fresh open derives.
    """
    report = _with_tags(
        genre_report(current=("Mystery",), genres=("Fantasy",)),
        ("old",),
        ("Espionage", "Moody"),
    )
    resolved = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    assert dict(resolved.options)["tags"] == "off"
    database = tmp_path / "application.sqlite3"
    with closing(open_repository(database)) as repository:
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        # No row of the policy is stored yet, so nothing settles the choice.
        assert _tags_fact(repository, case_id) == 1
        assert resolved.resolved_id == (
            repository.bind_initial_metadata_policy_resolution(resolved.canonical_json)
        )
        assert _tags_fact(repository, case_id) == 0
    with closing(open_repository(database, create=False)) as repository:
        assert _tags_fact(repository, case_id) == 0
        assert (
            repository.count_current_attention_fields(None)
            == BrowseReviewLibrary(repository).attention(
                search=None, limit=50, cursor=None
            )["total"]
        )
