"""Saving Book matching re-checks current cover comparisons (#1355, S4 A).

The re-check is #1573's re-projection of a complete current case work: the
case is not retired and keeps its id, and each re-projection prepares the
cover comparison again. The settings view counts how far the check has come,
and a work whose check gave up is taken again by the next save.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.field_effect_runtime import (
    PersistedEffectDependencies,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
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
from mechanical_librarian.application.review_application import (
    prepare_cover_consideration,
)
from mechanical_librarian.domain.cover_evidence import CoverArtifact
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.integration.test_leaving_automatic_posture import _Library, _prepared
from tests.unit.test_cover_packets import _preview, _source

_PAYLOAD = _preview(2)
_LISTED_PAYLOAD = _preview(7)
_PREVIEWS = {
    hashlib.sha256(payload).hexdigest(): payload
    for payload in (_PAYLOAD, _LISTED_PAYLOAD)
}
_LISTED_ARTIFACT = CoverArtifact.create(
    b"listed-7", _LISTED_PAYLOAD, "image/png", 400, 600
)


def _read_preview(artifact_id: str, _variant: str) -> bytes:
    return _LISTED_PAYLOAD if artifact_id == _LISTED_ARTIFACT.artifact_id else _PAYLOAD


class _Level:
    """The saved Book matching level, as the runtime generation carries it."""

    def __init__(self) -> None:
        self.value = WorkIdentityLevel.IDENTIFIER
        self.now: list[int] = [0]


def _service(
    repository: SQLiteMetadataReportRepository, level: WorkIdentityLevel, now: int
) -> CoverConsiderationService:
    service = CoverConsiderationService(
        repository,
        read_preview=_read_preview,
        read_preview_digest=lambda digest: _PREVIEWS.get(digest, _PAYLOAD),
        require_artifact=lambda _artifact: None,
        clock=lambda: now,
        work_identity_level=level,
        bind_policy=repository.bind_current_cover_authority,
    )
    # As composition does: the live authority names the configured level.
    service.update_policy(CoverPolicy())
    repository.bind_owner_cover_validation(service.require_owner_cover)
    return service


def _cover_stage(level: _Level) -> Any:
    """The projection's cover stage, as `ReviewPipelineStages.prepare_cover`."""

    def prepare(repository: object, action: Any) -> None:
        concrete = cast(SQLiteMetadataReportRepository, repository)

        def snapshot() -> tuple[str, str, bool] | None:
            return concrete.read_staged_cover_preparation(action)

        if action.review_case_id is None or snapshot() is None:
            return
        prepare_cover_consideration(
            None,
            _service(concrete, level.value, level.now[0]),
            action.review_case_id,
            serialized=lambda call: call(),
            snapshot=snapshot,
            finalize=cast(Any, concrete.atomic_cover_preparation),
        )

    return prepare


@pytest.fixture
def level() -> _Level:
    return _Level()


@pytest.fixture
def library(tmp_path: Path, level: _Level) -> Iterator[_Library]:
    with _prepared(
        tmp_path,
        automatic=False,
        covers=(_source(2, _PAYLOAD, embedded=True),),
        before_projection=_cover_stage(level),
    ) as value:
        level.now = value.now
        yield value


def _case(library: _Library) -> tuple[str, str]:
    return library.root._connection.execute(
        "SELECT review_case_id,proposal_set_id FROM current_review_case_lineages"
    ).fetchone()


def _compare(library: _Library, level: WorkIdentityLevel) -> None:
    """Prepare the cover comparison as the owner's Prepare action does."""
    case_id, _proposal_set_id = _case(library)
    service = _service(library.root, level, library.now[0])
    case = ReviewCase.create_from_data(
        json.loads(library.root.get_review_case(case_id))["case"]
    )
    proposal = next(item for item in case.proposals if item["destination"] == "cover")
    service.ensure(case_id, library.report.to_json(), proposal)


def _save(library: _Library, level: str, *, at: int) -> None:
    connection = library.root._connection
    connection.execute("BEGIN IMMEDIATE")
    request_matching_check(connection, level, updated_at=at)
    connection.execute("COMMIT")


def _work(library: _Library) -> tuple[str, str]:
    return library.root._connection.execute(
        "SELECT stage,state FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone()


def _current_level(library: _Library) -> str:
    (value,) = library.root._connection.execute(
        "SELECT json_extract(c.document,'$.work_identity_level') "
        "FROM current_cover_considerations current "
        "JOIN cover_considerations c ON c.consideration_id=current.consideration_id"
    ).fetchone()
    return cast(str, value)


def test_the_re_check_finishes_on_its_own_through_preparation(
    library: _Library, level: _Level
) -> None:
    """Saving a changed level re-projects the work; the projection's own cover
    stage prepares the comparison under the new level, with no manual step,
    and the case keeps its id (reviewer finding 3)."""
    connection = library.root._connection
    case_id, _ = _case(library)
    # Preparation itself made the first comparison, at `identifier`.
    assert _current_level(library) == "identifier"
    assert matching_check_counts(connection, "identifier") == (1, 0, 0)

    level.value = WorkIdentityLevel.TITLE_AND_CREATOR
    _save(library, "title_and_creator", at=40)

    assert _work(library) == ("projection", "queued")
    assert matching_check_counts(connection, "title_and_creator") == (1, 1, 0)
    trigger = connection.execute(
        "SELECT accepted_count FROM review_preparation_presentation "
        "WHERE row_id='trigger:matching_changed'"
    ).fetchone()
    assert trigger == (1,)

    library.now[0] = 41
    library.drain()
    assert _case(library)[0] == case_id
    assert len(library.admissions) == 1
    assert matching_check_counts(connection, "title_and_creator") == (1, 0, 0)
    assert _current_level(library) == "title_and_creator"


def test_a_check_that_gave_up_is_counted_and_retried_by_saving_again(
    library: _Library,
) -> None:
    connection = library.root._connection
    _save(library, "title_and_creator_exact", at=40)
    connection.execute(
        "UPDATE review_preparation_work SET state='failed',failure_code='failed' "
        "WHERE item_id='item-1'"
    )
    assert matching_check_counts(connection, "title_and_creator_exact") == (1, 0, 1)

    # Saving the same value again takes the work that gave up.
    _save(library, "title_and_creator_exact", at=41)
    assert _work(library) == ("projection", "queued")
    assert matching_check_counts(connection, "title_and_creator_exact") == (1, 1, 0)


def test_a_save_that_changes_nothing_requeues_nothing(library: _Library) -> None:
    connection = library.root._connection
    before = _work(library)
    _save(library, "identifier", at=40)
    assert _work(library) == before
    assert matching_check_counts(connection, "identifier") == (1, 0, 0)
    assert connection.execute(
        "SELECT count(*) FROM review_preparation_presentation "
        "WHERE row_id='trigger:matching_changed'"
    ).fetchone() == (0,)
    assert json.loads(library.report.to_json())["covers"]


def test_a_comparison_without_previews_is_counted_and_retried(
    tmp_path: Path, level: _Level
) -> None:
    """A comparison with no classified preview still names its level, so a
    re-check that gave up is counted and saving again retries it (Codex r5, 3)."""
    from mechanical_librarian.domain.cover_evidence import (
        CoverSource,
        CoverSourceKind,
        CoverSourceState,
        cover_source_id,
    )

    unavailable = CoverSource(
        cover_source_id(CoverSourceKind.EPUB, "source-unavailable"),
        CoverSourceKind.EPUB,
        CoverSourceState.DECODE_FAILED,
        None,
    )
    with _prepared(
        tmp_path,
        automatic=False,
        covers=(unavailable,),
        before_projection=_cover_stage(level),
    ) as library:
        level.now = library.now
        connection = library.root._connection
        consideration = json.loads(
            cast(
                str,
                library.root.current_cover_consideration_document(_case(library)[0]),
            )
        )
        assert consideration["identity_document"] is None
        assert _current_level(library) == "identifier"
        # A cover effect chosen from it also closes as matching_changed once
        # another level is live.
        (digest,) = connection.execute(
            "SELECT document_digest FROM cover_considerations WHERE consideration_id=?",
            (consideration["consideration_id"],),
        ).fetchone()
        dependencies = PersistedEffectDependencies(library.root)
        assert dependencies.matching_changed((digest,)) is False
        _service(library.root, WorkIdentityLevel.TITLE_AND_CREATOR, 39)
        assert dependencies.matching_changed((digest,)) is True
        _save(library, "title_and_creator", at=40)
        assert _work(library) == ("projection", "queued")
        connection.execute(
            "UPDATE review_preparation_work SET state='failed',failure_code='failed' "
            "WHERE item_id='item-1'"
        )
        assert matching_check_counts(connection, "title_and_creator") == (1, 0, 1)
        _save(library, "title_and_creator", at=41)
        assert _work(library) == ("projection", "queued")
        level.value = WorkIdentityLevel.TITLE_AND_CREATOR
        library.now[0] = 42
        library.drain()
        assert matching_check_counts(connection, "title_and_creator") == (1, 0, 0)


def test_a_work_that_failed_before_projection_is_retried_by_saving_again(
    library: _Library, level: _Level
) -> None:
    """A rescan of a current case that failed early leaves the case current
    with its work at an earlier stage. Saving again retries that work, so the
    remedy the Policy line names is true (experience review)."""
    connection = library.root._connection
    connection.execute(
        "UPDATE review_preparation_work SET stage='provider',state='failed',"
        "failure_code='provider',outcome=NULL,review_case_id=NULL,"
        "proposal_set_id=NULL WHERE item_id='item-1'"
    )
    level.value = WorkIdentityLevel.TITLE_AND_CREATOR
    _save(library, "title_and_creator", at=40)
    assert matching_check_counts(connection, "title_and_creator") == (1, 1, 0)
    assert _work(library) == ("provider", "queued")
    library.now[0] = 41
    library.drain()
    assert library.external == []
    assert matching_check_counts(connection, "title_and_creator") == (1, 0, 0)
    assert _current_level(library) == "title_and_creator"


@pytest.mark.parametrize(
    "work",
    (
        # A completed work whose row names no case, beside a current one.
        "stage='complete',state='complete',outcome='no_case',review_case_id=NULL",
        # A rescan whose proposal went stale under it.
        "stage='proposal',state='stale',failure_code='stale',outcome=NULL,"
        "review_case_id=NULL,proposal_set_id=NULL",
        # A rescan whose extraction failed.
        "stage='extraction',state='failed',failure_code='failed',outcome=NULL,"
        "review_case_id=NULL,proposal_set_id=NULL,report_id=NULL",
    ),
    ids=("names_another_case", "stale_before_projection", "extraction_failed"),
)
def test_reconciliation_makes_no_external_request(
    library: _Library, level: _Level, work: str
) -> None:
    """A current case whose item's work cannot simply be projected again or
    retried at a stored-data stage is bound back to its work and projected
    again. Nothing reads the file, Audiobookshelf or a provider (the harness
    records any extraction; discovery raises), the case keeps its id, and
    saving again stays a true remedy (re-review 1)."""
    connection = library.root._connection
    case_id = library.case_id()
    connection.execute(
        "UPDATE review_preparation_work SET " + work + " WHERE item_id='item-1'"
    )
    level.value = WorkIdentityLevel.TITLE_AND_CREATOR
    _save(library, "title_and_creator", at=40)
    assert matching_check_counts(connection, "title_and_creator") == (1, 1, 0)
    assert _work(library) == ("projection", "queued")
    library.now[0] = 41
    library.drain()
    assert library.external == []
    assert library.case_id() == case_id
    assert matching_check_counts(connection, "title_and_creator") == (1, 0, 0)
    assert _current_level(library) == "title_and_creator"
    assert _work(library) == ("complete", "complete")


def test_a_case_whose_report_is_no_longer_current_waits_for_the_next_scan(
    library: _Library,
) -> None:
    """A case whose item's current report is newer awaits the next scan's
    replacement: a save neither counts it nor touches its work."""
    connection = library.root._connection
    connection.execute(
        "UPDATE review_preparation_work SET stage='extraction',state='failed',"
        "failure_code='failed',outcome=NULL,review_case_id=NULL,"
        "proposal_set_id=NULL,report_id=NULL WHERE item_id='item-1'"
    )
    # Stand in for a newer report: the lineage no longer names the case's.
    for (trigger,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='current_metadata_report_lineages'"
    ).fetchall():
        connection.execute(f'DROP TRIGGER "{trigger}"')
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "UPDATE current_metadata_report_lineages SET report_id='report_' || "
        "printf('%064d', 0) WHERE item_id='item-1'"
    )
    _save(library, "title_and_creator", at=40)
    assert matching_check_counts(connection, "title_and_creator") == (1, 0, 0)
    assert _work(library) == ("extraction", "failed")


# --- 9. The unverified-match gates -------------------------------------------


def _listed_case(
    tmp_path: Path,
    *,
    automatic: bool = False,
    cover_order: str | None = None,
    verified: bool = False,
) -> AbstractContextManager[_Library]:
    """One work whose only cover is a listed-edition record of the same title
    and author; with `verified` the file's embedded cover joins it."""
    from mechanical_librarian.domain.cover_evidence import (
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
        _LISTED_ARTIFACT,
        provenance=CoverProvenance(
            "hardcover",
            CoverDesignation.LISTED_EDITION,
            "hardcover-work",
            "hardcover-edition-listed",
            (CoverContext(CoverContextField.LANGUAGE, "en"),),
        ),
    )
    return _prepared(
        tmp_path,
        automatic=automatic,
        covers=(listed, _source(2, _PAYLOAD, embedded=True)) if verified else (listed,),
        providers=(book,),
        cover_order=cover_order,
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


_COVER_ORDERS = ("fixed_source_order", "replace_if_strictly_better")


def _automatic_covers(library: _Library) -> list[tuple[str, str]]:
    return library.root._connection.execute(
        "SELECT kind,json_extract(document,'$.candidate_artifact_id') "
        "FROM cover_selection_authorities"
    ).fetchall()


@pytest.mark.parametrize("cover_order", _COVER_ORDERS)
def test_the_automatic_cover_path_is_live_for_a_verified_cover(
    tmp_path: Path, cover_order: str
) -> None:
    """Positive control for the guard below: in the same harness, with covers
    automatic, the file's own embedded cover is selected automatically at a
    title-and-author level while the listed record beside it is not."""
    with _listed_case(
        tmp_path, automatic=True, cover_order=cover_order, verified=True
    ) as library:
        case_id, _ = _case(library)
        _compare(library, WorkIdentityLevel.TITLE_AND_CREATOR)
        consideration = json.loads(
            cast(str, library.root.current_cover_consideration_document(case_id))
        )
        outcomes = {
            row["artifact_id"]: row["identity"]["outcome"]
            for row in consideration["candidates"]
        }
        embedded = _source(2, _PAYLOAD, embedded=True).artifact
        assert embedded is not None
        assert outcomes.pop(embedded.artifact_id) == "verified_manifestation_match"
        assert list(outcomes.values()) == ["title_creator_work_match"]
        assert _automatic_covers(library) == [("automatic", embedded.artifact_id)]


@pytest.mark.parametrize("cover_order", _COVER_ORDERS)
@pytest.mark.parametrize("level", list(WorkIdentityLevel), ids=lambda v: v.value)
def test_a_level_match_never_becomes_an_automatic_cover(
    tmp_path: Path, level: WorkIdentityLevel, cover_order: str
) -> None:
    """Under qualified Automatic with covers automatic, under each automatic
    cover order and at every level, a cover that only a level's predicate
    matches is never recommended, accepted or selected (ADR-0093's switches
    and fills). The positive control above shows the path is live."""
    with _listed_case(tmp_path, automatic=True, cover_order=cover_order) as library:
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


# --- 11. A cover effect bound to another level closes as matching_changed ---


def _guarded(library: _Library, dependencies: object) -> Any:
    """The guarded write over the real store, with the store's own
    dependency check and doubles for the library and its other seams."""
    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeAuthorityCurrentness,
        FakeBackupReadiness,
        FakeCapabilityObserver,
        FakeConsentCurrentness,
        FakeForwardReevaluation,
        FakePreclaimHook,
        FakeTargetService,
        PrivateCoverRecoveryStore,
    )
    from mechanical_librarian.application.field_effects import (
        GuardedFieldApplication,
    )
    from tests.support.field_effects import CAPABILITY, Clock

    root = library.root
    target = FakeTargetService()
    authority = FakeAuthorityCurrentness()
    consent = FakeConsentCurrentness(root.field_effect_consent_envelope_identity)
    recovery = PrivateCoverRecoveryStore(
        Path(
            cast(str, root._connection.execute("PRAGMA database_list").fetchone()[2])
        ).parent.joinpath("recovery"),
        lambda _: b"old-cover",
    )
    service = GuardedFieldApplication(
        root,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver(CAPABILITY),
        authority,
        cast(Any, dependencies),
        consent,
        recovery,
        FakeForwardReevaluation(),
        Clock(),
        FakePreclaimHook(),
        FakePreclaimHook(),
        FakePreclaimHook(),
    )
    return service, target, authority, consent


@pytest.mark.parametrize(
    ("chosen", "saved"),
    (
        (WorkIdentityLevel.IDENTIFIER, WorkIdentityLevel.TITLE_AND_CREATOR),
        (WorkIdentityLevel.TITLE_AND_CREATOR_EXACT, WorkIdentityLevel.IDENTIFIER),
    ),
    ids=("away_from_identifier", "back_to_identifier"),
)
def test_a_cover_effect_bound_to_another_level_closes_as_matching_changed(
    library: _Library, chosen: WorkIdentityLevel, saved: WorkIdentityLevel
) -> None:
    """Contract test 11 over the real store: the cover effect depends on the
    comparison it was chosen from, prepared under `chosen`; once the live
    authority names `saved`, applying it closes as `matching_changed`, and
    Activity says "Closed because Book matching changed"."""
    from mechanical_librarian.domain.activity_records import (
        ActivityResult,
        activity_result_label,
    )
    from tests.support.field_effects import CAPABILITY, decision, envelope

    _compare(library, chosen)
    root = library.root
    (digest,) = root._connection.execute(
        "SELECT c.document_digest FROM current_cover_considerations current "
        "JOIN cover_considerations c ON c.consideration_id=current.consideration_id"
    ).fetchone()
    assert _current_level(library) == chosen.value
    dependencies = PersistedEffectDependencies(root)
    service, target, authority, consent = _guarded(library, dependencies)

    def plan(suffix: str) -> Any:
        selected = envelope("cover", "cover_" + suffix * 64)
        source = decision("cover", selected, suffix)
        authority.bind(
            source,
            connection="primary",
            library="ebooks",
            work_id="item-1",
            target_version="version-1",
        )
        consent.allow(connection="primary", library="ebooks", destination="cover")
        operation = service.plan(
            source,
            connection="primary",
            library="ebooks",
            work_id="item-1",
            destination="cover",
            pre_value=None,
            desired_value=selected,
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(digest,),
            capability_digest=CAPABILITY,
        )
        assert operation is not None
        target.seed(operation.plan)
        return operation

    # Under the level it was chosen at, Book matching has not changed. This
    # harness's comparison awaits its cover review, so an effect applied now
    # closes, but as an unnamed dependency, not as matching_changed.
    assert dependencies.matching_changed((digest,)) is False
    baseline = service.apply(plan("0").plan.plan_id)
    assert (baseline.state, baseline.failure) == ("stale", "dependency")
    first = plan("1")
    # Saving another level rebinds the live authority to it.
    _service(root, saved, library.now[0])
    assert dependencies.matching_changed((digest,)) is True
    closed = service.apply(first.plan.plan_id)
    assert closed.state == "stale"
    assert closed.failure == "matching_changed"
    # The Activity ledger records the closure, and reads it to the owner as
    # "Closed because Book matching changed".
    latest = cast(
        list[dict[str, object]], root.field_effect_activity_data(50)["events"]
    )[0]
    assert (latest["destination"], latest["state"], latest["failure"]) == (
        "cover",
        "stale",
        "matching_changed",
    )
    label = activity_result_label(cast(str, latest["state"]), latest["failure"])
    assert label is ActivityResult.MATCHING_CHANGED
    assert str(label) == "Closed because Book matching changed"
    # Saving the chosen level again restores the dependency: nothing changed.
    _service(root, chosen, library.now[0])
    assert dependencies.matching_changed((digest,)) is False
