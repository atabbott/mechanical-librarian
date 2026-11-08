"""Put back: the original an automatic switch replaced, offered and written back (#1363).

These run the guarded write, the retention store, review preparation and the
cover observer over one real SQLite store, so each proof crosses the seams a
Put back depends on: the switch verifies, its work is prepared afresh, the
next report carries the original, and a verified Put back releases the copy.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.cover_evidence_observer import (
    CoverEvidenceObserver,
)
from mechanical_librarian.adapters.field_effect_fakes import PrivateCoverRecoveryStore
from mechanical_librarian.adapters.field_effect_runtime import CoalescedScanRequest
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.adapters.providers.remote_cover import CoverDeadline
from mechanical_librarian.adapters.retained_covers import (
    ContentAddressedOriginals,
    RetainedCoverStore,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.cover_retention import CoverRetention
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    TargetCoverReader,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineProgress,
    ReviewPipelineError,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceKind
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from tests.support.cover_images import synthetic_image
from tests.support.field_effects import (
    CAPABILITY,
    DEPENDENCY,
    _authorize,
    _EffectEnvironment,
    _plan,
    envelope,
)
from tests.support.field_effects import (
    effects as effects,
)
from tests.unit.test_cover_retention import _automatic_cover
from tests.unit.test_not_matched_lens import (
    _audiobook,
    _completes_without_identity,
    _for,
)

# A real image, so the original decodes like any cover.
ORIGINAL = synthetic_image("PNG", (300, 450))
ORIGINAL_DIGEST = hashlib.sha256(ORIGINAL).hexdigest()
SWITCHED = "cover_" + "9" * 64
ENTRY = CatalogueEntry(
    "primary",
    "books",
    "lib-1",
    "book-1",
    1,
    "Recorded book",
    ("Example Author",),
    False,
    False,
    "singular-supported",
    "e" * 64,
    "/book-1.m4b",
    "m4b",
    "audiobooks",
)


def _root(effects: _EffectEnvironment) -> Path:
    return Path(effects.temporary.name) / "retained-covers"


def _application(
    effects: _EffectEnvironment, rescans: list[tuple[str, str]]
) -> GuardedFieldApplication:
    """The guarded writer as composition builds it, reading the real original."""
    recovery = PrivateCoverRecoveryStore(
        Path(effects.temporary.name) / "recovery-originals", lambda _: ORIGINAL
    )
    return GuardedFieldApplication(
        effects.repository,
        effects.target,
        effects.backup,
        effects.capabilities,
        effects.authority,
        effects.dependencies,
        effects.consent,
        recovery,
        effects.reevaluation,
        effects.clock,
        effects.preclaim,
        effects.claim_hook,
        effects.recovery_hook,
        cover_retention=CoverRetention(
            RetainedCoverStore(_root(effects)),
            effects.repository,
            recovery.read,
            effects.clock,
        ),
        cover_rescan=lambda connection, library: rescans.append((connection, library)),
    )


def _switch(
    effects: _EffectEnvironment, application: GuardedFieldApplication
) -> FieldEffectOperation:
    automatic = _automatic_cover()
    effects.authority.automatic_values[automatic.decision_id] = envelope(
        "cover", SWITCHED
    )
    _authorize(effects, automatic)
    planned = application.plan(
        automatic,
        connection="primary",
        library="books",
        work_id="book-1",
        destination="cover",
        pre_value="cover_" + ORIGINAL_DIGEST,
        desired_value=envelope("cover", SWITCHED),
        target_fingerprint=automatic.target_fingerprint,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert planned is not None
    effects.target.seed(planned.plan)
    return planned


def _drain(repository: SQLiteMetadataReportRepository, tick: int) -> list[str]:
    """Run every preparation action, publishing a report for each extraction."""
    stages = []
    while action := repository.claim_review_pipeline_action(tick):
        stages.append(action.stage)
        if action.stage == "discovery":
            repository.complete_review_discovery(
                action, 1, "a" * 64, (ENTRY,), tick + 1
            )
        elif action.stage == "extraction":
            repository.publish(
                _for(_audiobook("x"), "books", "book-1"),
                lambda: True,
                None,
                _completes_without_identity(action, tick + 1),
            )
        else:
            raise AssertionError(f"unexpected preparation stage {action.stage}")
        tick += 2
    return stages


@pytest.mark.parametrize("verified", (True, False), ids=("verified", "abandoned"))
def test_a_verified_switch_prepares_its_work_afresh_on_the_next_scan(
    effects: _EffectEnvironment, verified: bool
) -> None:
    """Audiobookshelf writes an uploaded cover to the same path, so nothing in
    the catalogue changes. Only the mark makes the next scan extract again
    (#1363, Q17); without it the work carries forward its earlier report."""
    repository = effects.repository
    repository.request_review_scan("primary", "books", "lib-1", "f" * 64, 10)
    assert _drain(repository, 11) == ["discovery", "extraction"]
    rescans: list[tuple[str, str]] = []
    application = _application(effects, rescans)
    planned = _switch(effects, application)

    if verified:
        assert application.apply(planned.plan.plan_id).state == "verified"
    else:
        assert application.abandon(planned.plan.plan_id).state == "abandoned"

    assert rescans == ([("primary", "books")] if verified else [])
    repository.request_review_scan("primary", "books", "lib-1", "f" * 64, 100)
    assert _drain(repository, 101) == (
        ["discovery", "extraction"] if verified else ["discovery"]
    )


def test_the_original_reaches_a_report_only_once_its_switch_verifies(
    effects: _EffectEnvironment,
) -> None:
    artifacts = CoverArtifactStore(Path(effects.temporary.name) / "artifacts")
    observer = CoverEvidenceObserver(
        # The target is never read: only the retained source is observed.
        cast("TargetCoverReader", None),
        artifacts,
        (),
        _root(effects),
    )
    application = _application(effects, [])
    planned = _switch(effects, application)
    deadline = CoverDeadline.start()

    # Negative control: a report prepared before verification has no binding
    # to read, so it carries no retained source.
    before = effects.repository.retained_cover_original("primary", "books", "book-1")
    assert before is None
    assert observer._retained_source(before, deadline) == ()

    assert application.apply(planned.plan.plan_id).state == "verified"
    binding = effects.repository.retained_cover_original("primary", "books", "book-1")
    (source,) = observer._retained_source(binding, deadline)

    assert source.kind is CoverSourceKind.RETAINED
    assert source.provenance is None
    assert source.artifact is not None
    assert source.artifact.original_digest == ORIGINAL_DIGEST
    # Only its preview is staged: the original is in the retained store alone,
    # so a release leaves no copy of it in the artifact store.
    assert artifacts.preview(source.artifact)
    with pytest.raises(MetadataReportError):
        artifacts.read_original_digest(ORIGINAL_DIGEST)
    # The guarded writer reads it back from the retained store by digest.
    assert (
        ContentAddressedOriginals(artifacts, _root(effects)).read_original_digest(
            ORIGINAL_DIGEST
        )
        == ORIGINAL
    )


def _owner_put_back(
    effects: _EffectEnvironment, decision_id: str, switch_plan: str
) -> None:
    """Record the owner's v3 selection naming the switch it undoes.

    The consideration that would bind it is outside this proof, so its foreign
    key is not enforced on this one statement.
    """
    document = json.dumps(
        {
            "candidate_kind": "retained",
            "retained_plan_id": switch_plan,
            "schema_version": "cover-selection-authority-v3",
        }
    )
    connection = sqlite3.connect(effects.database_path)
    try:
        connection.execute(
            "INSERT INTO cover_selection_authorities(selection_id,connection_name,"
            "library_name,item_id,kind,decision_id,consideration_id,"
            "supersedes_selection_id,occurred_at,document) "
            "VALUES(?,?,?,?,'owner',?,?,NULL,1,?)",
            (
                "cover_selection_" + "5" * 64,
                "primary",
                "books",
                "book-1",
                decision_id,
                "cover_consideration_" + "6" * 64,
                document,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _removals(effects: _EffectEnvironment) -> list[tuple[str, str]]:
    connection = sqlite3.connect(effects.database_path)
    try:
        return [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT plan_id,reason FROM retained_cover_removals"
            )
        ]
    finally:
        connection.close()


@pytest.mark.parametrize("changed", (False, True), ids=("verified", "target_changed"))
def test_a_put_back_releases_its_copy_only_when_its_write_verifies(
    effects: _EffectEnvironment, changed: bool
) -> None:
    rescans: list[tuple[str, str]] = []
    application = _application(effects, rescans)
    switch = _switch(effects, application)
    assert application.apply(switch.plan.plan_id).state == "verified"
    put_back = _plan(
        effects,
        "cover",
        pre=SWITCHED,
        desired="cover_" + ORIGINAL_DIGEST,
        suffix="7",
    )
    _owner_put_back(effects, put_back.plan.authority.decision_id, switch.plan.plan_id)
    if changed:
        # Someone sets another cover in Audiobookshelf before the write.
        effects.target.before_apply = lambda plan: effects.target._write(
            plan, "cover_" + "8" * 64
        )

    result = application.apply(put_back.plan.plan_id)

    if changed:
        assert (result.state, result.failure) == ("stale", "target_changed")
        # The copy stays, so the owner can still put it back or keep the cover.
        assert _removals(effects) == []
        assert effects.repository.retained_cover_original(
            "primary", "books", "book-1"
        ) == (ORIGINAL_DIGEST, switch.plan.plan_id, switch.plan.authority.decision_id)
    else:
        assert result.state == "verified"
        assert _removals(effects) == [(switch.plan.plan_id, "released")]
        assert (
            effects.repository.retained_cover_original("primary", "books", "book-1")
            is None
        )


def _progress(state: str, rescan_requested: bool) -> PipelineProgress:
    return PipelineProgress(
        "scan", "primary", "books", state, 0, 0, 0, 0, 0, 0, rescan_requested
    )


def test_cover_writes_ask_for_at_most_one_scan_per_library_until_it_starts() -> None:
    """Many switches verified in one run ask the scheduler once (#1363, Q17)."""
    scans: dict[tuple[str, str], PipelineProgress] = {}
    asked: list[tuple[str, str]] = []

    def request(connection: str, library: str) -> None:
        asked.append((connection, library))
        current = scans.get((connection, library))
        # The scheduler queues an idle library and marks a running one to
        # scan again, as `request_review_scan` does.
        scans[(connection, library)] = (
            _progress("processing", True)
            if current is not None and current.state == "processing"
            else _progress("queued", False)
        )

    rescan = CoalescedScanRequest(
        lambda connection, library: scans.get((connection, library)), request
    )
    for _ in range(5):
        rescan("primary", "books")
    rescan("primary", "audio")
    assert asked == [("primary", "books"), ("primary", "audio")]

    # Once the scan runs, one more write asks it to scan again, once.
    scans[("primary", "books")] = _progress("processing", False)
    for _ in range(3):
        rescan("primary", "books")
    assert asked[2:] == [("primary", "books")]


@pytest.mark.parametrize(
    "error",
    (ReviewPipelineError("preparation_restart_required"), MetadataReportError("x")),
)
def test_a_refused_scan_request_never_fails_the_committed_write(
    error: Exception,
) -> None:
    def refuse(_connection: str, _library: str) -> None:
        raise error

    CoalescedScanRequest(lambda _connection, _library: None, refuse)("primary", "books")


def test_the_scan_request_runs_after_the_write_commits(
    effects: _EffectEnvironment,
) -> None:
    observed: list[str] = []

    def record(connection: str, library: str) -> None:
        del connection, library
        observed.append(effects.repository.get_field_effect(switch.plan.plan_id).state)

    application = _application(effects, [])
    application._cover_rescan = record
    switch = _switch(effects, application)
    application.apply(switch.plan.plan_id)

    assert observed == ["verified"]
