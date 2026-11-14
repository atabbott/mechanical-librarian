"""Saving Book matching re-checks current cover comparisons (#1355, S4 A).

The re-check is #1573's re-projection of a complete current case work: the
case is not retired and keeps its id, and each re-projection prepares the
cover comparison again. The settings view counts how far the check has come,
and a work whose check gave up is taken again by the next save.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.review_preparation import (
    matching_check_counts,
    request_matching_check,
)
from mechanical_librarian.adapters.persistence.schema import (
    _validate_cover_selection_authorities,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.integration.test_leaving_automatic_posture import _Library, _prepared
from tests.unit.test_cover_packets import _preview, _source

_PAYLOAD = _preview(2)


@pytest.fixture
def library(tmp_path: Path) -> Iterator[_Library]:
    with _prepared(
        tmp_path, automatic=False, covers=(_source(2, _PAYLOAD, embedded=True),)
    ) as value:
        yield value


def _case(library: _Library) -> tuple[str, str]:
    return library.root._connection.execute(
        "SELECT review_case_id,proposal_set_id FROM current_review_case_lineages"
    ).fetchone()


def _compare(library: _Library, level: WorkIdentityLevel) -> None:
    """Prepare the cover comparison as the projection's cover stage does."""
    case_id, _proposal_set_id = _case(library)
    service = CoverConsiderationService(
        library.root,
        read_preview=lambda *_: _PAYLOAD,
        read_preview_digest=lambda _: _PAYLOAD,
        require_artifact=lambda _artifact: None,
        clock=lambda: library.now[0],
        work_identity_level=level,
        bind_policy=library.root.bind_current_cover_authority,
    )
    # As composition does: the live authority names the configured level.
    service.update_policy(CoverPolicy())
    library.root.bind_owner_cover_validation(service.require_owner_cover)
    case = ReviewCase.create_from_data(
        json.loads(library.root.get_review_case(case_id))["case"]
    )
    proposal = next(item for item in case.proposals if item["destination"] == "cover")
    service.ensure(case_id, library.report.to_json(), proposal)


def _save(library: _Library, level: str, *, changed: bool, at: int) -> None:
    connection = library.root._connection
    connection.execute("BEGIN IMMEDIATE")
    request_matching_check(connection, level, level_changed=changed, updated_at=at)
    connection.execute("COMMIT")


def _work(library: _Library) -> tuple[str, str]:
    return library.root._connection.execute(
        "SELECT stage,state FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone()


def test_saving_a_changed_level_rechecks_the_case_without_retiring_it(
    library: _Library,
) -> None:
    connection = library.root._connection
    case_id, _ = _case(library)
    _compare(library, WorkIdentityLevel.IDENTIFIER)
    assert matching_check_counts(connection, "identifier") == (1, 0, 0)

    _save(library, "title_and_creator", changed=True, at=40)

    assert _work(library) == ("projection", "queued")
    assert matching_check_counts(connection, "title_and_creator") == (1, 1, 0)
    trigger = connection.execute(
        "SELECT accepted_count FROM review_preparation_presentation "
        "WHERE row_id='trigger:matching_changed'"
    ).fetchone()
    assert trigger == (1,)

    library.now[0] = 41
    library.drain()
    # The case is the same case: re-projection never retires it.
    assert _case(library)[0] == case_id
    assert len(library.admissions) == 1
    _compare(library, WorkIdentityLevel.TITLE_AND_CREATOR)
    assert matching_check_counts(connection, "title_and_creator") == (1, 0, 0)
    level = connection.execute(
        "SELECT json_extract(json_extract(c.document,'$.identity_document'),"
        "'$.work_identity_level') FROM current_cover_considerations current "
        "JOIN cover_considerations c ON c.consideration_id=current.consideration_id"
    ).fetchone()
    assert level == ("title_and_creator",)


def test_a_check_that_gave_up_is_counted_and_retried_by_saving_again(
    library: _Library,
) -> None:
    connection = library.root._connection
    _compare(library, WorkIdentityLevel.IDENTIFIER)
    _save(library, "title_and_creator_exact", changed=True, at=40)
    connection.execute(
        "UPDATE review_preparation_work SET state='failed',failure_code='failed' "
        "WHERE item_id='item-1'"
    )
    assert matching_check_counts(connection, "title_and_creator_exact") == (1, 0, 1)

    # Saving the same value again takes the work that gave up.
    _save(library, "title_and_creator_exact", changed=False, at=41)
    assert _work(library) == ("projection", "queued")
    assert matching_check_counts(connection, "title_and_creator_exact") == (1, 1, 0)


def test_a_save_that_changes_nothing_requeues_nothing(library: _Library) -> None:
    connection = library.root._connection
    _compare(library, WorkIdentityLevel.IDENTIFIER)
    before = _work(library)
    _save(library, "identifier", changed=False, at=40)
    assert _work(library) == before
    assert matching_check_counts(connection, "identifier") == (1, 0, 0)
    assert connection.execute(
        "SELECT count(*) FROM review_preparation_presentation "
        "WHERE row_id='trigger:matching_changed'"
    ).fetchone() == (0,)
    assert json.loads(library.report.to_json())["covers"]


# --- 9. The unverified-match gates -------------------------------------------


def _listed_case(
    tmp_path: Path, *, automatic: bool = False
) -> AbstractContextManager[_Library]:
    from mechanical_librarian.domain.cover_evidence import (
        CoverArtifact,
        CoverContext,
        CoverContextField,
        CoverDesignation,
        CoverProvenance,
        CoverSource,
        CoverSourceKind,
        CoverSourceState,
        cover_source_id,
    )
    from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope
    from tests.support.proposal_evidence import _claim, _provider

    edition = "hardcover-edition"
    book = _provider(
        "hardcover",
        edition,
        (
            _claim(
                "hardcover",
                edition,
                ClaimKind.TITLE,
                "Current title",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "hardcover",
                edition,
                ClaimKind.CREATOR,
                "Current Writer",
                scope=ClaimScope.WORK,
                role="author",
                order=1,
            ),
        ),
    )
    listed = CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, "hardcover:listed:7"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(b"listed-7", _PAYLOAD, "image/png", 400, 600),
        provenance=CoverProvenance(
            "hardcover",
            CoverDesignation.LISTED_EDITION,
            "hardcover-work",
            "hardcover-edition-listed",
            (CoverContext(CoverContextField.LANGUAGE, "en"),),
        ),
    )
    return _prepared(
        tmp_path, automatic=automatic, covers=(listed,), providers=(book,)
    )


@pytest.mark.parametrize("confirmed", (False, True))
def test_a_title_and_author_pick_needs_the_unverified_match_flag(
    tmp_path: Path, confirmed: bool
) -> None:
    from mechanical_librarian.domain.human_decision import (
        HumanDecision,
        HumanDecisionError,
    )

    with _listed_case(tmp_path) as library:
        case_id, _ = _case(library)
        _compare(library, WorkIdentityLevel.TITLE_AND_CREATOR_EXACT)
        root = library.root
        consideration = json.loads(
            cast(str, root.current_cover_consideration_document(case_id))
        )
        (candidate,) = consideration["candidates"]
        case = ReviewCase.create_from_data(
            json.loads(root.get_review_case(case_id))["case"]
        )
        proposal = next(
            item for item in case.proposals if item["destination"] == "cover"
        )
        owner = HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="accept",
            selected_value={"kind": "cover", "value": candidate["artifact_id"]},
            idempotency_key="synthetic-level-match-pick-0001",
            occurred_at=50,
        )
        confirmation: dict[str, object] = {
            "expected_selection_id": None,
            "confirm_unverified_match": confirmed,
        }
        if not confirmed:
            with pytest.raises(HumanDecisionError, match="invalid_choice"):
                root.record_human_decision(
                    owner,
                    cover_consideration_id=consideration["consideration_id"],
                    cover_confirmation=confirmation,
                )
            assert root._connection.execute(
                "SELECT count(*) FROM cover_selection_authorities"
            ).fetchone() == (0,)
            return
        root.record_human_decision(
            owner,
            cover_consideration_id=consideration["consideration_id"],
            cover_confirmation=confirmation,
        )
        assert root._connection.execute(
            "SELECT kind FROM cover_selection_authorities"
        ).fetchall() == [("owner",)]
        # The readback reopening runs admits the confirmed pick, and refuses
        # the same pick stored without the flag.
        _validate_cover_selection_authorities(root._connection)
        for (trigger,) in root._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='cover_selection_authorities'"
        ).fetchall():
            root._connection.execute(f'DROP TRIGGER "{trigger}"')
        root._connection.execute(
            "UPDATE cover_selection_authorities SET document=replace(document,"
            "'\"confirm_unverified_match\":true','\"confirm_unverified_match\":false')"
        )
        with pytest.raises(MetadataReportError):
            _validate_cover_selection_authorities(root._connection)


# --- 10. Guard: no automatic cover from a level match ------------------------


@pytest.mark.parametrize("level", list(WorkIdentityLevel), ids=lambda v: v.value)
def test_a_level_match_never_becomes_an_automatic_cover(
    tmp_path: Path, level: WorkIdentityLevel
) -> None:
    """Under qualified Automatic, at every level, a cover that only a level's
    predicate matches is never recommended, accepted or selected (ADR-0093's
    switches and fills, ADR-0095's automatic mode)."""
    with _listed_case(tmp_path, automatic=True) as library:
        case_id, _ = _case(library)
        _compare(library, level)
        root = library.root
        consideration = json.loads(
            cast(str, root.current_cover_consideration_document(case_id))
        )
        policy = json.loads(consideration["policy_report_document"])
        assert policy["recommended_candidate_reference"] is None
        assert policy["acceptable_candidate_references"] == []
        (candidate,) = consideration["candidates"]
        assert candidate["selectable"] is False
        assert candidate["identity"]["outcome"] == (
            "unknown"
            if level is WorkIdentityLevel.IDENTIFIER
            else "title_creator_work_match"
        )
        library.now[0] += 1
        library.drain()
        connection = root._connection
        assert connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='cover'"
        ).fetchone() == (0,)

