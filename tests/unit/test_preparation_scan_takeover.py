"""A library's newer scan takes over the work an older scan left (#2023).

The owner's case: the first-boot scan A closes with a book that could not
finish, the next untriggered request creates the regular scan B, and work
queued again afterwards must belong to a scan that can claim it. Each test
reopens its state with ``open_repository(..., create=False)``, because a
work that changes scan must leave the presentation valid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.description_advice import (
    request_description_preparation,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    request_matching_check,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_policy import PolicyNotebook
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    pipeline_scan_id,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.preparation_pipeline import PRODUCER, entry
from tests.support.review_cases import _report

SCAN_A = pipeline_scan_id("primary", "ebooks", first_boot=True)
SCAN_B = pipeline_scan_id("primary", "ebooks")
LIBRARY = (("primary", "ebooks"),)
PRODUCERS = (("primary", "ebooks", "lib-1", PRODUCER),)


@dataclass
class _Library:
    path: Path
    repository: SQLiteMetadataReportRepository
    report: MetadataReport
    case_id: str

    def reopen(self) -> None:
        self.repository.close()
        self.repository = open_repository(self.path, create=False)


def _ebook(item_id: str, file_identity: str) -> CatalogueEntry:
    return replace(entry(item_id, file_identity), library="ebooks")


def _entries(report: MetadataReport) -> tuple[CatalogueEntry, ...]:
    """The two unchanged items every discovery of the library lists."""
    return (
        _ebook(report.target.item, report.file.identity),
        _ebook("item-2", "2" * 64),
    )


def _claim(library: _Library, at: int) -> PipelineAction:
    action = library.repository.claim_review_pipeline_action(at)
    assert action is not None
    return action


def _closed_first_boot_library(path: Path, item_2_code: str = "failed") -> _Library:
    """First-boot scan A prepares item-1 and gives up on item-2.

    item-2 fails at extraction with ``item_2_code``, a permanent failure, so
    A closes ``completed_with_failures`` and its INITIAL_SETUP run finishes.
    A ``stale`` failure first rediscovers the library up to three times.
    """
    repository = open_repository(path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    library = _Library(path, repository, report, case_id)
    repository.request_review_scan(
        "primary", "ebooks", "lib-1", PRODUCER, 10, first_boot=True
    )
    discovery = _claim(library, 11)
    assert discovery.scan_id == SCAN_A
    repository.complete_review_discovery(discovery, 1, "a" * 64, _entries(report), 12)
    for at, output in (
        (13, report.report_id),
        (15, None),
        (17, proposal.proposal_set_id),
        (19, case_id),
        (21, None),
    ):
        action = _claim(library, at)
        assert action.item_id == "item-1"
        repository.advance_review_pipeline_action(action, output, at + 1)
    for at in range(23, 43):
        claimed = repository.claim_review_pipeline_action(at)
        if claimed is None:
            if _scan_states(library)[SCAN_A] != "queued":
                break
        elif claimed.stage == "discovery":
            repository.complete_review_discovery(
                claimed, 1, "a" * 64, _entries(report), at
            )
        else:
            assert (claimed.item_id, claimed.stage) == ("item-2", "extraction")
            repository.fail_review_pipeline_action(claimed, item_2_code, False, at)
    assert _scan_states(library) == {SCAN_A: "completed_with_failures"}
    return library


def _scan_states(library: _Library) -> dict[str, str]:
    return dict(
        library.repository._connection.execute(
            "SELECT scan_id,state FROM review_preparation_scans"
        ).fetchall()
    )


def _works(library: _Library) -> dict[str, tuple[str, str, str]]:
    return {
        item: (scan_id, stage, state)
        for item, scan_id, stage, state in library.repository._connection.execute(
            "SELECT item_id,scan_id,stage,state FROM review_preparation_work"
        ).fetchall()
    }


def _library_state(library: _Library) -> str | None:
    """The state Activity reads: the library's latest scan."""
    (progress,), _paused = library.repository.review_pipeline_status(LIBRARY)
    return None if progress is None else progress.state


def _discover_b(library: _Library, at: int) -> None:
    """Run B's discovery of the same unchanged items."""
    discovery = _claim(library, at)
    assert (discovery.stage, discovery.scan_id) == ("discovery", SCAN_B)
    library.repository.complete_review_discovery(
        discovery, 2, "b" * 64, _entries(library.report), at + 1
    )


def _fail_item_2_again(library: _Library, at: int) -> None:
    action = _claim(library, at)
    assert (action.item_id, action.stage, action.scan_id) == (
        "item-2",
        "extraction",
        SCAN_B,
    )
    library.repository.fail_review_pipeline_action(action, "failed", False, at + 1)


# --- T1: the issue's sequence ------------------------------------------------


def _choose_description(library: _Library, at: int) -> None:
    connection = library.repository._connection
    connection.execute("BEGIN IMMEDIATE")
    request_description_preparation(connection, at, review_case_id=library.case_id)
    connection.execute("COMMIT")


def _change_matching(library: _Library, at: int) -> None:
    connection = library.repository._connection
    connection.execute("BEGIN IMMEDIATE")
    request_matching_check(connection, "title_and_creator", updated_at=at)
    connection.execute("COMMIT")


def _activate_policy(library: _Library, at: int) -> None:
    """Change the policy as the owner does, then prepare item-1 under it."""
    repository = library.repository
    repository.enter_shadow_preview(at, ())
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["title_layout"] = "automatic"
    draft = MetadataPolicyProfile.create(
        "Qualified automatic policy", dict(DEFAULT_OPTIONS), destinations
    )
    repository.create_metadata_policy_draft(draft.canonical_json)
    PolicyNotebook(repository, ("ebooks",), {}).activate(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        at + 1,
        expected_active_generation=1,
    )


@pytest.mark.parametrize(
    "cause",
    ("description", "matching", "policy"),
    ids=("description", "matching", "policy"),
)
def test_requeued_work_is_claimed_under_the_newer_scan(
    tmp_path: Path, cause: str
) -> None:
    """Work queued again after B exists runs under B, and B stays open."""
    library = _closed_first_boot_library(tmp_path / "state.sqlite3")
    if cause == "description":
        # Choosing an adviser requests the scan that B's discovery runs.
        _choose_description(library, 30)
        _discover_b(library, 31)
        expected_stage = "projection"
    else:
        library.repository.request_review_scan(
            "primary", "ebooks", "lib-1", PRODUCER, 30
        )
        _discover_b(library, 31)
        if cause == "matching":
            _change_matching(library, 33)
            expected_stage = "projection"
        else:
            _activate_policy(library, 33)
            expected_stage = "proposal"
    library.reopen()
    assert _library_state(library) == "processing"
    action = _claim(library, 40)
    assert (action.item_id, action.stage, action.scan_id) == (
        "item-1",
        expected_stage,
        SCAN_B,
    )
    if cause == "policy":
        current = library.repository.current_resolved_metadata_policy("ebooks")
        assert current is not None
        successor = evaluate_report(
            library.report, current.resolved_id, dict(current.destinations)
        )
        library.repository.publish_proposal(successor)
        library.repository.advance_review_pipeline_action(
            action, successor.proposal_set_id, 41
        )
        action = _claim(library, 42)
        admitted = json.loads(library.repository.admit_review_case(successor))
        library.repository.advance_review_pipeline_action(
            action, admitted["review_case_id"], 43
        )
        action = _claim(library, 44)
    assert action.stage == "projection"
    library.repository.advance_review_pipeline_action(action, None, 45)
    assert _library_state(library) == "processing"
    _fail_item_2_again(library, 46)
    assert library.repository.claim_review_pipeline_action(48) is None
    assert _works(library)["item-1"] == (SCAN_B, "complete", "complete")
    assert _scan_states(library)[SCAN_B] == "completed_with_failures"
    library.reopen()
    library.repository.close()


# --- T3: the owner's stranded database -------------------------------------


def _strand_on_a(
    library: _Library, b_state: str, at: int, item_1: str = "projection"
) -> None:
    """Reproduce the owner's database with raw SQL (#2023).

    B ran, but every work row stayed on A: item-1 queued at projection, as
    a chosen description adviser leaves it (or, with ``item_1``, still
    complete or failed at the provider stage), and item-2 failed. The
    finished run was dismissed, so no presentation row names either work.
    B is left in ``b_state`` with no work of its own.
    """
    connection = library.repository._connection
    connection.execute(
        "UPDATE review_preparation_work SET scan_id=?,updated_at=? ", (SCAN_A, at)
    )
    connection.execute(
        {
            "projection": "UPDATE review_preparation_work SET stage='projection',"
            "state='queued',outcome=NULL WHERE item_id='item-1'",
            "complete": "UPDATE review_preparation_work SET stage='complete',"
            "state='complete',outcome='case' WHERE item_id='item-1'",
            "provider": "UPDATE review_preparation_work SET stage='provider',"
            "state='failed',failure_code='provider',proposal_set_id=NULL,"
            "review_case_id=NULL,outcome=NULL WHERE item_id='item-1'",
        }[item_1]
    )
    connection.execute(
        "UPDATE review_preparation_work SET stage='extraction',state='failed',"
        "failure_code='failed',report_id=NULL,proposal_set_id=NULL,"
        "review_case_id=NULL,outcome=NULL WHERE item_id='item-2'"
    )
    connection.execute(
        "UPDATE review_preparation_scans SET state=?,updated_at=? WHERE scan_id=?",
        (b_state, at, SCAN_B),
    )
    connection.commit()


def _stranded_library(path: Path, b_state: str, item_1: str = "projection") -> _Library:
    library = _closed_first_boot_library(path)
    presentation = library.repository.preparation_presentation(26)
    assert presentation is not None
    library.repository.dismiss_preparation_presentation(presentation.run_id, 26)
    library.repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 30)
    _discover_b(library, 31)
    _strand_on_a(library, b_state, 33, item_1)
    # The constructed state is one the product opens.
    library.reopen()
    return library


@pytest.mark.parametrize(
    "b_state", ("completed", "processing"), ids=("closed", "processing")
)
def test_next_claim_takes_over_work_stranded_under_a_closed_scan(
    tmp_path: Path, b_state: str
) -> None:
    library = _stranded_library(tmp_path / "state.sqlite3", b_state)
    assert _works(library)["item-1"] == (SCAN_A, "projection", "queued")

    action = _claim(library, 40)

    assert (action.item_id, action.stage, action.scan_id) == (
        "item-1",
        "projection",
        SCAN_B,
    )
    assert _works(library)["item-1"] == (SCAN_B, "projection", "running")
    assert _scan_states(library)[SCAN_B] == "processing"
    assert _library_state(library) == "processing"
    library.reopen()
    library.repository.close()


@pytest.mark.parametrize(
    ("b_state", "discovery_failure"),
    (("queued", None), ("completed_with_failures", "library_unavailable")),
    ids=("latest_queued", "latest_discovery_failed"),
)
def test_claim_leaves_stranded_work_to_discovery_or_a_failed_scan(
    tmp_path: Path, b_state: str, discovery_failure: str | None
) -> None:
    """A queued B takes the work over in its discovery; a B whose discovery
    failed keeps its library's work unclaimable (ADR-0031)."""
    library = _stranded_library(tmp_path / "state.sqlite3", b_state)
    connection = library.repository._connection
    connection.execute(
        "UPDATE review_preparation_scans SET discovery_failure=? WHERE scan_id=?",
        (discovery_failure, SCAN_B),
    )
    connection.commit()
    library.reopen()

    action = library.repository.claim_review_pipeline_action(40)

    assert _works(library)["item-1"] == (SCAN_A, "projection", "queued")
    if discovery_failure is None:
        assert action is not None
        assert (action.stage, action.scan_id) == ("discovery", SCAN_B)
    else:
        assert action is None
        assert _scan_states(library)[SCAN_B] == "completed_with_failures"
    library.reopen()
    library.repository.close()


# --- T4: startup ----------------------------------------------------------


def test_startup_recovery_takes_over_stranded_work_before_any_claim(
    tmp_path: Path,
) -> None:
    library = _stranded_library(tmp_path / "state.sqlite3", "completed")

    library.repository.recover_review_pipeline(PRODUCERS)

    assert _works(library)["item-1"] == (SCAN_B, "projection", "queued")
    assert _scan_states(library)[SCAN_B] == "processing"
    assert _library_state(library) == "processing"
    library.reopen()
    action = _claim(library, 40)
    assert (action.item_id, action.scan_id) == ("item-1", SCAN_B)
    library.repository.close()


def test_startup_recovery_retires_stranded_work_of_a_removed_library(
    tmp_path: Path,
) -> None:
    library = _stranded_library(tmp_path / "state.sqlite3", "completed")

    library.repository.recover_review_pipeline(
        (("primary", "fiction", "lib-1", PRODUCER),)
    )

    assert _works(library)["item-1"] == (SCAN_B, "projection", "stale")
    assert _scan_states(library)[SCAN_B] == "completed_with_failures"
    library.reopen()
    library.repository.close()


# --- T2 and T5: every requeue writer reopens the latest scan ---------------


def test_retrying_a_failed_stage_runs_under_the_latest_scan(tmp_path: Path) -> None:
    """Saving Book matching retries a work that gave up at a stored-data
    stage; the retry runs under B, not the closed A that holds it."""
    library = _stranded_library(tmp_path / "state.sqlite3", "completed", "provider")

    _change_matching(library, 40)

    assert _works(library)["item-1"] == (SCAN_B, "provider", "queued")
    assert _scan_states(library) == {
        SCAN_A: "completed_with_failures",
        SCAN_B: "processing",
    }
    library.reopen()
    action = _claim(library, 41)
    assert (action.item_id, action.stage, action.scan_id) == (
        "item-1",
        "provider",
        SCAN_B,
    )
    library.repository.close()


def test_staging_a_provider_retry_runs_under_the_latest_scan(tmp_path: Path) -> None:
    library = _stranded_library(tmp_path / "state.sqlite3", "completed", "complete")
    repository = library.repository
    predecessor = library.report
    successor = _report(competing=False)
    assert successor.report_id != predecessor.report_id
    repository.publish(
        successor,
        lambda: True,
        predecessor.report_id,
        after_publication=lambda bound, report: bound.stage_provider_retry_successor(
            predecessor.report_id, report, 40
        ),
        expected_package=repository.current_review_package(
            "primary", "ebooks", "item-1"
        ),
    )

    assert _works(library)["item-1"] == (SCAN_B, "provider", "queued")
    assert _scan_states(library)[SCAN_B] == "processing"
    library.reopen()
    action = _claim(library, 41)
    assert (action.item_id, action.stage, action.scan_id) == (
        "item-1",
        "provider",
        SCAN_B,
    )
    library.repository.close()


def test_a_transition_reading_a_work_again_requeues_the_latest_scan(
    tmp_path: Path,
) -> None:
    """Re-queueing an older scan made it the latest, and the next request
    then tried to create B again (#2023).

    Executes the writer's statement directly: reaching it through an
    activation needs a verified write that the read-again rule refuses
    (ADR-0034), which this test does not exercise.
    """
    from mechanical_librarian.adapters.persistence.metadata_policy import (
        _REQUEUE_SCAN_FOR_DISCOVERY_SQL,
    )

    library = _stranded_library(tmp_path / "state.sqlite3", "completed")
    connection = library.repository._connection
    work_id = connection.execute(
        "SELECT work_id FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        _REQUEUE_SCAN_FOR_DISCOVERY_SQL, (40, 40, work_id, work_id, "no-transition")
    )
    connection.execute("COMMIT")
    assert _scan_states(library) == {
        SCAN_A: "completed_with_failures",
        SCAN_B: "queued",
    }
    library.reopen()

    _discover_b(library, 41)
    action = _claim(library, 43)
    assert (action.item_id, action.scan_id) == ("item-1", SCAN_B)
    library.repository.advance_review_pipeline_action(action, None, 44)
    _fail_item_2_again(library, 45)
    assert library.repository.claim_review_pipeline_action(47) is None
    progress = library.repository.request_review_scan(
        "primary", "ebooks", "lib-1", PRODUCER, 50
    )
    assert (progress.scan_id, progress.state) == (SCAN_B, "queued")
    library.reopen()
    library.repository.close()


def test_book_matching_saved_while_the_latest_scan_waits_joins_that_scan(
    tmp_path: Path,
) -> None:
    """Work queued again before B's discovery joins B, so the finished run
    and A stay consistent and B's discovery runs it."""
    library = _closed_first_boot_library(tmp_path / "state.sqlite3")
    library.repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 30)
    _change_matching(library, 31)

    assert _works(library)["item-1"] == (SCAN_B, "projection", "queued")
    assert _scan_states(library) == {
        SCAN_A: "completed_with_failures",
        SCAN_B: "queued",
    }
    library.reopen()
    _discover_b(library, 32)
    action = _claim(library, 34)
    assert (action.item_id, action.stage, action.scan_id) == (
        "item-1",
        "projection",
        SCAN_B,
    )
    library.repository.close()


def test_a_policy_change_during_the_latest_scans_discovery_lets_it_finish(
    tmp_path: Path,
) -> None:
    """The activation leaves a discovering scan to its discovery, which
    then runs the restarted work, rather than interrupting it."""
    library = _closed_first_boot_library(tmp_path / "state.sqlite3")
    library.repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 30)
    discovery = _claim(library, 31)
    assert (discovery.stage, discovery.scan_id) == ("discovery", SCAN_B)

    _activate_policy(library, 32)

    assert _scan_states(library)[SCAN_B] == "discovering"
    library.repository.complete_review_discovery(
        discovery, 2, "b" * 64, _entries(library.report), 35
    )
    library.reopen()
    action = _claim(library, 40)
    assert (action.item_id, action.stage, action.scan_id) == (
        "item-1",
        "proposal",
        SCAN_B,
    )
    library.repository.close()


# --- T6: the presentation follows a moved work ------------------------------


def test_a_finished_run_drops_the_rows_of_work_a_newer_scan_took_over(
    tmp_path: Path,
) -> None:
    library = _closed_first_boot_library(tmp_path / "state.sqlite3")
    before = library.repository.preparation_presentation(26)
    assert before is not None and len(before.recent_completions) == 2
    library.repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 30)
    _discover_b(library, 31)
    library.reopen()

    after = library.repository.preparation_presentation(33)
    assert after is not None
    assert (after.run_id, after.state) == (before.run_id, before.state)
    assert after.recent_completions == ()
    # The run keeps the passage and failures it saw; item-1 is no longer
    # counted ready, so a run that enrols it again counts it once.
    assert after.passage == replace(before.passage, ready=0)
    assert library.repository._connection.execute(
        "SELECT completion_count FROM review_preparation_presentation "
        "WHERE row_id='run'"
    ).fetchone() == (0,)
    library.repository.close()


@pytest.mark.parametrize(
    ("code", "kept"),
    (("failed", "failed"), ("stale", "failed")),
    ids=("failed", "stale"),
)
def test_a_finished_run_keeps_only_the_outcome_of_a_book_the_newer_scan_dropped(
    tmp_path: Path, code: str, kept: str
) -> None:
    """item-2 left the library before B listed it: B deletes A's work, and
    the finished run keeps the row only as the outcome it recorded."""
    library = _closed_first_boot_library(tmp_path / "state.sqlite3", code)
    assert _works(library)["item-2"] == (SCAN_A, "extraction", code)
    # A stale failure rediscovers A first, so B starts after A closed.
    at = library.repository._connection.execute(
        "SELECT MAX(updated_at) FROM review_preparation_scans"
    ).fetchone()[0]
    library.repository.request_review_scan(
        "primary", "ebooks", "lib-1", PRODUCER, at + 1
    )
    discovery = _claim(library, at + 2)
    assert discovery.scan_id == SCAN_B
    library.repository.complete_review_discovery(
        discovery, 2, "b" * 64, _entries(library.report)[:1], at + 3
    )
    library.reopen()

    assert set(_works(library)) == {"item-1"}
    assert library.repository._connection.execute(
        "SELECT p.current_work_state,p.work_outcome FROM review_preparation_presentation p "
        "WHERE p.row_kind='work' AND NOT EXISTS(SELECT 1 FROM review_preparation_work w "
        "WHERE w.work_id=p.work_id)"
    ).fetchall() == [(kept, "failed")]
    library.repository.close()


@pytest.mark.parametrize(
    "items",
    (("item-0", "item-2"), ("item-2", "item-9")),
    ids=("ordinal_shift", "no_shift"),
)
def test_rediscovery_that_shifts_a_finished_work_keeps_the_database_open(
    tmp_path: Path, items: tuple[str, str]
) -> None:
    """A finished run's row follows its work's new ordinal (F3)."""
    path = tmp_path / "state.sqlite3"
    repository = open_repository(path)
    repository.request_review_scan(
        "primary",
        "ebooks",
        "lib-1",
        PRODUCER,
        10,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_ebook("item-2", "2" * 64),), 12
    )
    work = repository.claim_review_pipeline_action(13)
    assert work is not None
    repository.fail_review_pipeline_action(work, "failed", False, 14)
    assert repository.claim_review_pipeline_action(15) is None
    repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 20)
    discovery = repository.claim_review_pipeline_action(21)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        2,
        "b" * 64,
        tuple(_ebook(item, item[-1] * 64) for item in items),
        22,
    )
    repository.close()

    reopened = open_repository(path, create=False)
    assert reopened._connection.execute(
        "SELECT p.ordinal=w.ordinal FROM review_preparation_presentation p "
        "JOIN review_preparation_work w USING(work_id) WHERE p.row_kind='work'"
    ).fetchall() == [(1,)]
    reopened.close()


def test_a_scan_manually_restarted_over_taken_over_work_shows_it_in_its_run(
    tmp_path: Path,
) -> None:
    """Scan now on the newer scan starts a run that lists the work again."""
    library = _closed_first_boot_library(tmp_path / "state.sqlite3")
    library.repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 30)
    library.repository.request_review_scan(
        "primary",
        "ebooks",
        "lib-1",
        PRODUCER,
        31,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    _discover_b(library, 32)
    library.reopen()

    rows = library.repository._connection.execute(
        "SELECT p.scan_id,w.item_id,p.current_work_state "
        "FROM review_preparation_presentation p JOIN review_preparation_work w "
        "USING(work_id) WHERE p.row_kind='work' ORDER BY w.item_id"
    ).fetchall()
    assert rows == [(SCAN_B, "item-1", "complete"), (SCAN_B, "item-2", "queued")]
    library.repository.close()


# --- A library with a book that could not finish ----------------------------


def test_a_library_with_a_failed_book_ends_its_scan_needing_attention(
    tmp_path: Path,
) -> None:
    """B holds every work of its library, so its close-out counts item-2.

    Before #2023, B saw no work of its own and closed ``completed``, which
    the poll scans again, while item-2 stayed queued on the closed A.
    """
    library = _closed_first_boot_library(tmp_path / "state.sqlite3")
    library.repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 30)
    _discover_b(library, 31)
    _fail_item_2_again(library, 33)
    assert library.repository.claim_review_pipeline_action(35) is None

    assert _scan_states(library) == {
        SCAN_A: "completed_with_failures",
        SCAN_B: "completed_with_failures",
    }
    assert _works(library) == {
        "item-1": (SCAN_B, "complete", "complete"),
        "item-2": (SCAN_B, "extraction", "failed"),
    }
    # Only a library whose latest scan completed is polled again.
    assert _library_state(library) == "completed_with_failures"
    library.reopen()
    library.repository.close()


# --- T7: the claim pass stays bounded ---------------------------------------


def test_claim_pass_cost_does_not_grow_with_a_finished_library(
    tmp_path: Path,
) -> None:
    """Looking for stranded work reads the scans first, not the library."""

    def fail_every_work(repository: SQLiteMetadataReportRepository) -> None:
        repository._connection.execute(
            "UPDATE review_preparation_work SET state='failed',failure_code='failed'"
        )
        repository._connection.commit()

    def claim_steps(count: int) -> int:
        path = tmp_path / f"finished-{count}.sqlite3"
        entries = tuple(
            _ebook(f"item-{index:04d}", f"{index:064x}") for index in range(count)
        )
        repository = open_repository(path)
        repository.request_review_scan(
            "primary", "ebooks", "lib-1", PRODUCER, 1, first_boot=True
        )
        discovery = repository.claim_review_pipeline_action(2)
        assert discovery is not None
        repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 3)
        fail_every_work(repository)
        assert repository.claim_review_pipeline_action(4) is None
        presentation = repository.preparation_presentation(5)
        assert presentation is not None
        repository.dismiss_preparation_presentation(presentation.run_id, 5)
        repository.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 6)
        discovery = repository.claim_review_pipeline_action(7)
        assert discovery is not None and discovery.scan_id == SCAN_B
        repository.complete_review_discovery(discovery, 2, "b" * 64, entries, 8)
        fail_every_work(repository)
        assert repository.claim_review_pipeline_action(9) is None
        repository.close()
        repository = open_repository(path, create=False)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_work WHERE scan_id=?", (SCAN_B,)
        ).fetchone() == (count,)
        steps = 0

        def count_steps() -> int:
            nonlocal steps
            steps += 1
            return 0

        repository._connection.set_progress_handler(count_steps, 1)
        action = repository.claim_review_pipeline_action(10)
        repository._connection.set_progress_handler(None, 0)
        repository.close()
        assert action is None
        return steps

    ten_steps = claim_steps(10)
    thousand_steps = claim_steps(1_000)
    assert thousand_steps <= ten_steps + 80
