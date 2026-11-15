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
from mechanical_librarian.adapters.persistence.review_preparation import (
    preparation_work_id,
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
    PipelineAction,
    PipelineProgress,
    ReviewPipelineError,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceKind
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import COVER_SELECTION_AUTHORITY_SCHEMA
from mechanical_librarian.domain.metadata_report import MetadataReport
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
from tests.unit.test_cover_retention import (
    _measured_record as _measured_record,  # autouse: a switch is the measured replacement
)
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
    effects: _EffectEnvironment,
    rescans: list[tuple[str, str]],
    original: bytes = ORIGINAL,
) -> GuardedFieldApplication:
    """The guarded writer as composition builds it, reading the real original."""
    recovery = PrivateCoverRecoveryStore(
        Path(effects.temporary.name) / "recovery-originals", lambda _: original
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
    effects: _EffectEnvironment,
    application: GuardedFieldApplication,
    original_digest: str = ORIGINAL_DIGEST,
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
        pre_value="cover_" + original_digest,
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


def _refresh_requested(effects: _EffectEnvironment) -> int:
    connection = sqlite3.connect(effects.database_path)
    try:
        (value,) = connection.execute(
            "SELECT refresh_requested FROM review_preparation_work"
        ).fetchone()
        return int(value)
    finally:
        connection.close()


def test_a_switch_that_verifies_during_a_run_prepares_its_work_afresh_after_it(
    effects: _EffectEnvironment,
) -> None:
    """The run under way observed the cover before the write and completes
    with that report. The next scan, finding the catalogue unchanged, still
    extracts again rather than carry that report forward, and the request is
    spent once that extraction is claimed (#1363, Q17)."""
    repository = effects.repository
    repository.request_review_scan("primary", "books", "lib-1", "f" * 64, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None and discovery.stage == "discovery"
    repository.complete_review_discovery(discovery, 1, "a" * 64, (ENTRY,), 12)
    running = repository.claim_review_pipeline_action(13)
    assert running is not None and running.stage == "extraction"
    application = _application(effects, [])
    planned = _switch(effects, application)

    assert application.apply(planned.plan.plan_id).state == "verified"
    repository.publish(
        _for(_audiobook("x"), "books", "book-1"),
        lambda: True,
        None,
        _completes_without_identity(running, 14),
    )
    assert _drain(repository, 15) == []
    assert _refresh_requested(effects) == 1

    repository.request_review_scan("primary", "books", "lib-1", "f" * 64, 100)
    assert _drain(repository, 101) == ["discovery", "extraction"]
    assert _refresh_requested(effects) == 0
    # Spent: the scan after that carries the fresh report forward.
    repository.request_review_scan("primary", "books", "lib-1", "f" * 64, 200)
    assert _drain(repository, 201) == ["discovery"]


def _request_refresh(effects: _EffectEnvironment) -> None:
    connection = sqlite3.connect(effects.database_path)
    try:
        connection.execute("UPDATE review_preparation_work SET refresh_requested=1")
        connection.commit()
    finally:
        connection.close()


def _claim(
    repository: SQLiteMetadataReportRepository, by_work: bool, tick: int
) -> PipelineAction | None:
    """Claim through the scheduler's path or the named-successor path."""
    if by_work:
        return repository.claim_review_pipeline_work(
            preparation_work_id("primary", "books", "book-1"), tick
        )
    return repository.claim_review_pipeline_action(tick)


def _discovered(repository: SQLiteMetadataReportRepository) -> None:
    """One scan's discovery, leaving the work queued at extraction."""
    repository.request_review_scan("primary", "books", "lib-1", "f" * 64, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None and discovery.stage == "discovery"
    repository.complete_review_discovery(discovery, 1, "a" * 64, (ENTRY,), 12)


@pytest.mark.parametrize("by_work", (False, True), ids=("next", "named"))
def test_claiming_an_extraction_spends_a_refresh_request(
    effects: _EffectEnvironment, by_work: bool
) -> None:
    """An extraction reads the cover again, so claiming it spends the
    request (#1363, Q17)."""
    _discovered(effects.repository)
    _request_refresh(effects)

    extraction = _claim(effects.repository, by_work, 13)

    assert extraction is not None and extraction.stage == "extraction"
    assert _refresh_requested(effects) == 0


@pytest.mark.parametrize("by_work", (False, True), ids=("next", "named"))
def test_claiming_a_later_stage_keeps_a_refresh_request(
    effects: _EffectEnvironment, by_work: bool
) -> None:
    """A later stage builds on the report it already has and never reads the
    cover, so claiming it keeps the request for the next discovery (#1363)."""
    repository = effects.repository
    _discovered(repository)
    running = repository.claim_review_pipeline_action(13)
    assert running is not None and running.stage == "extraction"
    _request_refresh(effects)

    def advance(value: object, published: object) -> None:
        # The report goes on to proposal rather than completing.
        cast(
            SQLiteMetadataReportRepository, value
        ).complete_review_extraction_publication(
            running, cast("MetadataReport", published).report_id, 14
        )

    repository.publish(
        _for(_audiobook("x"), "books", "book-1"), lambda: True, None, advance
    )

    later = _claim(repository, by_work, 15)

    assert later is not None and later.stage == "proposal"
    assert _refresh_requested(effects) == 1


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
            "schema_version": COVER_SELECTION_AUTHORITY_SCHEMA,
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


# Signature-valid bytes the constrained decoder refuses.
UNDECODABLE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
UNDECODABLE_DIGEST = hashlib.sha256(UNDECODABLE).hexdigest()


def test_an_original_that_does_not_decode_is_kept_offered_and_put_back(
    effects: _EffectEnvironment,
) -> None:
    """The owner gets back exactly the bytes a switch replaced, whether or not
    they decode (#1362, A5 and S11).

    The switch is seeded by planning it directly under a synthetic automatic
    authority, as every Put back proof here does: until #1362's stage change,
    the stage itself never switches a cover it could not read.
    """
    artifacts = CoverArtifactStore(Path(effects.temporary.name) / "artifacts")
    observer = CoverEvidenceObserver(
        cast("TargetCoverReader", None), artifacts, (), _root(effects)
    )
    application = _application(effects, [], UNDECODABLE)
    switch = _switch(effects, application, UNDECODABLE_DIGEST)
    assert application.apply(switch.plan.plan_id).state == "verified"
    assert RetainedCoverStore(_root(effects)).read(UNDECODABLE_DIGEST) == UNDECODABLE

    # The next report offers it by its verified digest, with no artifact and
    # nothing staged, so no preview of it exists anywhere.
    binding = effects.repository.retained_cover_original("primary", "books", "book-1")
    assert binding == (
        UNDECODABLE_DIGEST,
        switch.plan.plan_id,
        switch.plan.authority.decision_id,
    )
    (source,) = observer._retained_source(binding, CoverDeadline.start())
    assert (source.kind, source.artifact, source.original_digest) == (
        CoverSourceKind.RETAINED,
        None,
        UNDECODABLE_DIGEST,
    )
    stored = Path(effects.temporary.name) / "artifacts"
    assert not any(path.is_file() for path in stored.rglob("*"))

    # Put back: the writer reads the original from the retained store, as
    # composition's Audiobookshelf writer does, and uploads exactly those bytes.
    uploaded: list[bytes] = []
    effects.target.after_apply = lambda plan: uploaded.append(
        ContentAddressedOriginals(artifacts, _root(effects)).read_original_digest(
            cast(str, plan.desired_value).removeprefix("cover_")
        )
    )
    put_back = _plan(
        effects,
        "cover",
        pre=SWITCHED,
        desired="cover_" + UNDECODABLE_DIGEST,
        suffix="7",
    )
    _owner_put_back(effects, put_back.plan.authority.decision_id, switch.plan.plan_id)

    assert application.apply(put_back.plan.plan_id).state == "verified"
    assert uploaded == [UNDECODABLE]
    assert effects.target.read(put_back.plan).value == "cover_" + UNDECODABLE_DIGEST
    assert _removals(effects) == [(switch.plan.plan_id, "released")]


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


def test_a_retained_put_back_leaks_nothing_to_events_or_the_bundle(
    effects: _EffectEnvironment,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The retained path and the original's bytes stay private (#1363, A15)."""
    from mechanical_librarian.adapters.operational_evidence import SupportBundle
    from mechanical_librarian.application.operations import (
        support_preparation_incidents,
    )

    application = _application(effects, [])
    switch = _switch(effects, application)
    assert application.apply(switch.plan.plan_id).state == "verified"
    put_back = _plan(
        effects, "cover", pre=SWITCHED, desired="cover_" + ORIGINAL_DIGEST, suffix="7"
    )
    _owner_put_back(effects, put_back.plan.authority.decision_id, switch.plan.plan_id)
    assert application.apply(put_back.plan.plan_id).state == "verified"
    repository = effects.repository
    bundle = (
        SupportBundle(
            (
                (
                    "operational_incidents",
                    lambda: support_preparation_incidents(
                        repository.operational_snapshot()
                    ),
                ),
            )
        )
        .export()
        .decode()
    )
    activity = json.dumps(
        repository.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )
    )
    written = capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    surfaces = "\n".join((written.out, written.err, logged, bundle, activity))
    # Control: the original was kept under the retained root.
    assert any(_root(effects).iterdir())
    for leaked in (
        str(_root(effects)),
        "retained-covers",
        ORIGINAL[:16].hex(),
        ORIGINAL[:16].decode("latin-1"),
    ):
        assert leaked not in surfaces
