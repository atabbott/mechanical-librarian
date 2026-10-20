"""The cold-start report reconciles with the surfaces it summarises.

The workbench script composes the Attention surface's own SQL fragments, so
the first test proves the numbers agree end-to-end through a real repository;
the pure-assembly tests pin the conditional determinism comparison and the
ledger-derived timing; the entry-point test bounds the read-only invocation.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
import scripts.cold_start_report as cold_start
import scripts.persistence_census as persistence_census

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.review_cases import _report as _plain_report
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)
from tests.unit.test_shadow_outcome_persistence import _qualified_cover_report


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        connections=(
            TargetConnectionConfiguration(
                "primary", "https://primary.invalid", "/synthetic/primary.secret"
            ),
        ),
        libraries=(
            LibraryConfiguration(
                "ebooks", "primary", "library-a", "ebook", "books", ("hardcover-main",)
            ),
        ),
        mounts=(MediaMountConfiguration("books", "/media/books", "/library", True),),
        providers=(
            ProviderConnectionConfiguration("hardcover-main", False, False, None),
        ),
    )


def _for_item(
    source: MetadataReport,
    item: str,
    identity: str,
    *,
    connection: str | None = None,
    library: str | None = None,
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            connection or source.target.connection,
            library or source.target.library,
            item,
            source.target.fields,
        ),
        file=FileFacts(0, identity * 64, source.file.content_digest),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )


def _empty_snapshot() -> dict[str, object]:
    return {
        "libraries": [],
        "attention_fields": [],
        "current_cover_pointer_libraries": [],
        "provider_lanes": [],
        "unmatched_works": [],
        "scans": [],
        "work_states": [],
        "absences": [],
        "duplicates": [],
        "agreement": [],
        "audiobooks": [],
        "dispatches": [],
    }


def _publish_current_cover_consideration(
    repository: SQLiteMetadataReportRepository,
    *,
    root: Path,
    connection: str,
    library: str,
    item: str,
    identity: str,
) -> str:
    qualified, _proposal = _qualified_cover_report(root)
    report = _for_item(
        qualified,
        item,
        identity,
        connection=connection,
        library=library,
    )
    proposal = evaluate_report(report)
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    admission = json.loads(repository.admit_review_case(proposal))
    review_case_id = cast("str", admission["review_case_id"])
    detail = InspectCurrentReviewCase(repository).run(review_case_id)
    cover_field = next(
        field
        for field in cast("list[dict[str, object]]", detail["fields"])
        if field["destination"] == "cover"
    )
    cover_result = next(
        dict(result)
        for result in cast("list[dict[str, object]]", proposal.to_data()["results"])
        if result["destination"] == "cover"
    )
    cover_result["proposal_id"] = cover_field["proposal_id"]
    artifacts = CoverArtifactStore(root / "artifacts", create=False)
    preview_digests = {
        source.artifact.artifact_id: source.artifact.preview_digest
        for source in report.cover_sources
        if source.artifact is not None
    }
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact, _variant: artifacts.read_preview_digest(
            preview_digests[artifact]
        ),
        read_preview_digest=artifacts.read_preview_digest,
        clock=lambda: 1,
    )
    repository.bind_cover_consideration_currentness(lambda *_identity: False)
    service.update_policy(service._policy)
    consideration = service.ensure(review_case_id, report.to_json(), cover_result)
    assert consideration["consideration_id"]
    return review_case_id


def test_snapshot_reconciles_with_the_attention_surface_and_redacts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        repository = open_repository(database)
        attention_work = _bibliographic_report(
            current_title="Mistborn Secret History",
            providers=(
                _bibliographic_source(
                    "catalogue", "edition-1", titles=(), series="Mistborn"
                ),
            ),
        )
        repository.publish(attention_work, lambda: True)
        proposal = evaluate_report(attention_work)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        duplicate = _plain_report("Duplicate copy", "9", "1", actionable=False)
        for item, identity in (("copy-one", "2"), ("copy-two", "3")):
            copy = _for_item(duplicate, item, identity)
            repository.publish(copy, lambda: True)
            copy_proposal = evaluate_report(copy)
            repository.publish_proposal(copy_proposal)
            status = json.loads(repository.admit_review_case(copy_proposal))["status"]
            assert status == "no_case"
        pending = _for_item(
            _plain_report("Pending work", "7", "8", actionable=False),
            "pending-item",
            "7",
        )
        repository.publish(pending, lambda: True)
        surface_count = repository.count_current_attention_fields(None)
        repository.close()

        connection = cold_start._open_read_only(database)
        try:
            snapshot = cold_start.cold_start_snapshot(connection)
        finally:
            connection.close()

        assert surface_count == 1
        assert len(cast("list[object]", snapshot["attention_fields"])) == surface_count
        assert cast("list[object]", snapshot["libraries"]) == [
            ("primary", "ebooks", 3, 1)
        ]
        assert cast("list[object]", snapshot["unmatched_works"]) == [
            ("primary", "ebooks", 2)
        ]

        report = cold_start.assemble_cold_start_report(
            _configuration(),
            snapshot,
            (
                7,
                (
                    ("primary", "ebooks", "singular-supported", "epub", 3),
                    ("primary", "ebooks", "needs-attention", "pdf", 1),
                ),
            ),
            {"books": {"epub": 3}},
            1000,
        )
        again = cold_start.assemble_cold_start_report(
            _configuration(),
            snapshot,
            (
                7,
                (
                    ("primary", "ebooks", "singular-supported", "epub", 3),
                    ("primary", "ebooks", "needs-attention", "pdf", 1),
                ),
            ),
            {"books": {"epub": 3}},
            1000,
        )
        assert report == again

        assert report["schema_version"] == cold_start.COLD_START_REPORT_SCHEMA
        assert report["catalogue_generation"] == 7
        assert report["totals"] == {
            "works": 3,
            "attention_works": 1,
            "quiet_works": 2,
            "quiet_fraction": 0.6667,
            "attention_unavailable_reason": None,
        }
        assert report["determinism"] == {
            "duplicate_groups_compared": 1,
            "divergent_groups": 0,
        }
        assert report["zero_lane_providers"] == ["hardcover-main"]
        library = cast("list[dict[str, object]]", report["libraries"])[0]
        assert library["connection"] == "primary"
        assert library["library"] == "ebooks"
        assert library["works"] == {
            "current_total": 3,
            "attention": 1,
            "quiet": 2,
            "ledger_total": 0,
            "ready": 0,
            "blocked": 0,
            "absences": {},
        }
        attention = cast("dict[str, object]", library["attention_fields"])
        assert attention["total"] == 1
        assert attention["by_destination"] == {"series": 1}
        assert sum(cast("dict[str, int]", attention["by_class"]).values()) == 1
        assert library["providers"] == [
            {"provider": "hardcover-main", "lanes": {}, "zero_lane": True},
            {"provider": "catalogue", "lanes": {"candidates": 1}, "zero_lane": False},
        ]
        assert library["works_without_provider_candidates"] == 2
        fidelity = cast("dict[str, object]", library["scan_fidelity"])
        assert fidelity["mount"] == "books"
        assert fidelity["mount_files_by_extension"] == {"epub": 3}
        assert fidelity["target_items_by_classification"] == {
            "needs-attention": 1,
            "singular-supported": 3,
        }
        assert fidelity["target_items_by_format"] == {"epub": 3, "pdf": 1}

        serialized = json.dumps(report, ensure_ascii=False)
        for leaked in (
            "Mistborn",
            "Duplicate copy",
            "copy-one",
            "copy-two",
            "Pending work",
            "item-1",
            "edition-1",
        ):
            assert leaked not in serialized


def test_current_cover_pointer_marks_attention_counts_unavailable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    attention_work = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _bibliographic_source(
                "catalogue", "edition-1", titles=(), series="Mistborn"
            ),
        ),
    )
    repository.publish(attention_work, lambda: True)
    attention_proposal = evaluate_report(attention_work)
    repository.publish_proposal(attention_proposal)
    repository.admit_review_case(attention_proposal)
    cover_case_id = _publish_current_cover_consideration(
        repository,
        root=tmp_path / "current-cover",
        connection="primary",
        library="ebooks",
        item="private-cover-item",
        identity="c",
    )
    current_consideration = json.loads(
        cast(
            "str",
            repository.current_cover_consideration_document(cover_case_id),
        )
    )
    repository.bind_current_cover_authority(
        cast("str", current_consideration["policy_reference"]),
        cast("str", current_consideration["replay_reference"]),
    )
    assert repository.count_current_attention_fields(None) == 2
    repository.bind_current_cover_authority(
        "cover_policy_" + "d" * 64,
        "cover_replay_" + "e" * 64,
    )
    assert repository.count_current_attention_fields(None) == 1
    repository.close()
    before = database.read_bytes()

    connection = cold_start._open_read_only(database)
    try:
        snapshot = cold_start.cold_start_snapshot(connection)
    finally:
        connection.close()
    census_connection = persistence_census._open_read_only(database)
    try:
        plans = persistence_census._plans(census_connection)
    finally:
        census_connection.close()

    assert database.read_bytes() == before
    assert snapshot["current_cover_pointer_libraries"] == [("primary", "ebooks")]
    assert "steps" in plans["library_work_counts"]
    assert "steps" in plans["attention_field_rows"]
    assert "steps" in plans["attention_field_count"]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 1000
    )
    assert report["totals"] == {
        "works": 2,
        "attention_works": None,
        "quiet_works": None,
        "quiet_fraction": None,
        "attention_unavailable_reason": "current_cover_authority_unavailable",
    }
    library = cast("list[dict[str, object]]", report["libraries"])[0]
    assert library["works"] == {
        "current_total": 2,
        "attention": None,
        "quiet": None,
        "ledger_total": 0,
        "ready": 0,
        "blocked": 0,
        "absences": {},
    }
    assert library["attention_fields"] is None
    assert (
        library["attention_unavailable_reason"] == "current_cover_authority_unavailable"
    )
    assert library["works_without_provider_candidates"] == 0
    serialized = json.dumps(report)
    assert "private-cover-item" not in serialized
    assert "edition-3" not in serialized
    assert "cover_policy_" not in serialized
    assert "cover_replay_" not in serialized


def test_historical_and_unconfigured_cover_pointers_do_not_hide_totals(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    attention_work = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _bibliographic_source(
                "catalogue", "edition-1", titles=(), series="Mistborn"
            ),
        ),
    )
    repository.publish(attention_work, lambda: True)
    attention_proposal = evaluate_report(attention_work)
    repository.publish_proposal(attention_proposal)
    repository.admit_review_case(attention_proposal)
    _publish_current_cover_consideration(
        repository,
        root=tmp_path / "historical-cover",
        connection="primary",
        library="ebooks",
        item="historical-cover-item",
        identity="c",
    )
    replacement = _for_item(
        _plain_report("Current quiet replacement", "8", "9", actionable=False),
        "historical-cover-item",
        "d",
    )
    repository.publish(replacement, lambda: True)
    replacement_proposal = evaluate_report(replacement)
    repository.publish_proposal(replacement_proposal)
    assert json.loads(repository.admit_review_case(replacement_proposal))["status"] == (
        "retired"
    )
    _publish_current_cover_consideration(
        repository,
        root=tmp_path / "unconfigured-cover",
        connection="retired",
        library="archive",
        item="unconfigured-cover-item",
        identity="e",
    )
    repository.close()
    before = database.read_bytes()

    connection = cold_start._open_read_only(database)
    try:
        assert connection.execute(
            "SELECT count(*) FROM current_cover_considerations"
        ).fetchone() == (2,)
        snapshot = cold_start.cold_start_snapshot(connection)
    finally:
        connection.close()

    assert database.read_bytes() == before
    assert snapshot["current_cover_pointer_libraries"] == [("retired", "archive")]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 1000
    )
    assert report["totals"] == {
        "works": 2,
        "attention_works": 1,
        "quiet_works": 1,
        "quiet_fraction": 0.5,
        "attention_unavailable_reason": None,
    }
    library = cast("list[dict[str, object]]", report["libraries"])[0]
    assert library["attention_unavailable_reason"] is None
    assert cast("dict[str, object]", library["attention_fields"])["total"] == 1


def test_duplicate_determinism_is_conditioned_on_target_context() -> None:
    snapshot = _empty_snapshot()
    snapshot["duplicates"] = [
        # Same bytes, same target context, different results: nondeterminism.
        ("primary", "ebooks", "digest-1", "context-a", "results-1"),
        ("primary", "ebooks", "digest-1", "context-a", "results-2"),
        # Same bytes but different target context: a different input, so the
        # differing results are expected and the group is not even compared.
        ("primary", "ebooks", "digest-2", "context-a", "results-1"),
        ("primary", "ebooks", "digest-2", "context-b", "results-2"),
        # Same bytes, same context, same results: compared and clean.
        ("primary", "ebooks", "digest-3", "context-a", "results-1"),
        ("primary", "ebooks", "digest-3", "context-a", "results-1"),
    ]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 0
    )
    assert report["determinism"] == {
        "duplicate_groups_compared": 2,
        "divergent_groups": 1,
    }


def test_duplicate_determinism_is_scoped_to_one_configured_library() -> None:
    snapshot = _empty_snapshot()
    snapshot["duplicates"] = [
        ("primary", "ebooks", "digest", "context", "results-1"),
        ("secondary", "ebooks", "digest", "context", "results-2"),
    ]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 0
    )
    assert report["determinism"] == {
        "duplicate_groups_compared": 0,
        "divergent_groups": 0,
    }


def test_duplicate_groups_compare_quiet_proposal_results() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE current_review_packages ("
        "connection_name TEXT,library_name TEXT,report_id TEXT,"
        "proposal_set_id TEXT,outcome TEXT);"
        "CREATE TABLE metadata_reports ("
        "report_id TEXT,content_digest TEXT,document TEXT);"
        "CREATE TABLE proposal_sets (proposal_set_id TEXT,document TEXT);"
    )
    report = json.dumps({"target": {"fields": {"title": "same"}}})
    for number, outcome in enumerate(("defer", "keep"), start=1):
        report_id = f"report-{number}"
        proposal_id = f"proposal-{number}"
        connection.execute(
            "INSERT INTO metadata_reports VALUES(?,?,?)",
            (report_id, "digest", report),
        )
        connection.execute(
            "INSERT INTO proposal_sets VALUES(?,?)",
            (proposal_id, json.dumps({"results": [{"outcome": outcome}]})),
        )
        connection.execute(
            "INSERT INTO current_review_packages VALUES(?,?,?,?,?)",
            ("primary", "ebooks", report_id, proposal_id, "no_case"),
        )

    groups = cold_start._duplicate_groups(connection)
    connection.close()

    assert len(groups) == 2
    assert all(group[4] != "null" for group in groups)
    assert groups[0][4] != groups[1][4]


def test_headline_totals_include_only_configured_libraries() -> None:
    snapshot = _empty_snapshot()
    snapshot["libraries"] = [
        ("primary", "ebooks", 2, 1),
        ("retired", "old-library", 50, 20),
    ]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 0
    )
    assert report["totals"] == {
        "works": 2,
        "attention_works": 1,
        "quiet_works": 1,
        "quiet_fraction": 0.5,
        "attention_unavailable_reason": None,
    }


def test_reused_scan_ledger_is_hidden_until_discovery_refreshes_it() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE review_preparation_scans ("
        "scan_id TEXT,connection_name TEXT,library_name TEXT,"
        "catalogue_generation INTEGER);"
        "CREATE TABLE review_preparation_work ("
        "scan_id TEXT,connection_name TEXT,library_name TEXT,"
        "state TEXT,updated_at INTEGER);"
        "CREATE TABLE review_preparation_absences (scan_id TEXT,kind TEXT);"
        "INSERT INTO review_preparation_scans VALUES("
        "'scan','primary','ebooks',NULL);"
        "INSERT INTO review_preparation_work VALUES("
        "'scan','primary','ebooks','complete',100);"
        "INSERT INTO review_preparation_absences VALUES('scan','retired');"
    )
    assert cold_start._rows(connection, cold_start._WORK_STATE_COUNTS_SQL) == []
    assert cold_start._rows(connection, cold_start._ABSENCE_COUNTS_SQL) == []

    connection.execute("UPDATE review_preparation_scans SET catalogue_generation=7")
    assert cold_start._rows(connection, cold_start._WORK_STATE_COUNTS_SQL) == [
        ("primary", "ebooks", 1, 0, 1, 100, 100)
    ]
    assert cold_start._rows(connection, cold_start._ABSENCE_COUNTS_SQL) == [
        ("primary", "ebooks", "retired", 1)
    ]
    connection.close()


def test_timing_derives_from_the_scan_request_and_the_work_ledger() -> None:
    snapshot = _empty_snapshot()
    snapshot["scans"] = [("scan-1", "primary", "ebooks", "running", 10, 1000, 4600)]
    snapshot["work_states"] = [("primary", "ebooks", 4, 1, 6, 1600, 4600)]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 0
    )
    library = cast("list[dict[str, object]]", report["libraries"])[0]
    assert library["timing"] == {
        "requested_at": 1000,
        "first_ready_seconds": 600,
        "elapsed_seconds": 3600,
        "works_per_hour": 4.0,
        "projected_remaining_seconds": 5400,
    }
    assert library["scan"] == {
        "state": "running",
        "discovered_total": 10,
        "requested_at": 1000,
        "updated_at": 4600,
    }
    works = cast("dict[str, object]", library["works"])
    assert works["ready"] == 4
    assert works["blocked"] == 1
    assert works["ledger_total"] == 6


def test_main_reads_existing_state_and_remaps_mounts() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "application.sqlite3"
        repository = open_repository(database)
        work = _plain_report("Runner work", "5", "6", actionable=False)
        repository.publish(work, lambda: True)
        proposal = evaluate_report(work)
        repository.publish_proposal(proposal)
        status = json.loads(repository.admit_review_case(proposal))["status"]
        assert status == "no_case"
        repository.close()
        before = database.read_bytes()
        from mechanical_librarian.adapters.persistence.provider_recovery import (
            SQLiteProviderRecoveryStore,
        )

        recovery = root / "provider-recovery.sqlite"
        SQLiteProviderRecoveryStore.open(recovery).close()
        recovery_before = recovery.read_bytes()
        mount_root = root / "books"
        mount_root.mkdir()
        (mount_root / "one.epub").write_bytes(b"a")
        (mount_root / "two.EPUB").write_bytes(b"b")
        (mount_root / "notes.txt").write_bytes(b"c")
        (mount_root / "alias.epub").symlink_to("one.epub")
        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": "config-v9",
                    "provider_recovery_generation": 1,
                    "connections": [
                        {
                            "name": "primary",
                            "adapter": "audiobookshelf",
                            "endpoint": "https://primary.invalid",
                            "secret_file": str(root / "primary.secret"),
                        }
                    ],
                    "libraries": [
                        {
                            "name": "ebooks",
                            "connection": "primary",
                            "library_id": "library-a",
                            "mount": "books",
                            "provider_connections": ["hardcover-main"],
                        }
                    ],
                    "mounts": [
                        {
                            "name": "books",
                            "container_path": "/media/books",
                            "service_path": "/library/books",
                            "read_only": True,
                        }
                    ],
                    "providers": [
                        {
                            "name": "hardcover-main",
                            "adapter": "hardcover",
                            "enabled": False,
                            "cover_enabled": False,
                            "secret_file": None,
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        output = io.StringIO()
        code = cold_start.main(
            [
                "--config",
                str(config),
                "--database",
                str(database),
                "--catalogue",
                str(root / "missing-catalogue"),
                "--mount-root",
                f"books={mount_root}",
                "--recovery-database",
                str(recovery),
            ],
            stdout=output,
        )

        assert code == 0
        assert before == database.read_bytes()
        assert recovery_before == recovery.read_bytes()
        report = json.loads(output.getvalue())
        assert report["schema_version"] == cold_start.COLD_START_REPORT_SCHEMA
        assert report["catalogue_generation"] is None
        assert report["totals"]["works"] == 1
        library = report["libraries"][0]
        fidelity = library["scan_fidelity"]
        assert fidelity["mount_files_by_extension"] == {"epub": 2, "txt": 1}
        assert fidelity["target_items_by_classification"] is None
        assert {"jobs": 0, "groups": []} == report["recovery_jobs"]
        assert 1 == library["dispatches"]["items"]
        assert "Runner work" not in output.getvalue()


@pytest.mark.parametrize(
    "opener",
    (cold_start._open_read_only, persistence_census._open_read_only),
)
def test_read_only_database_uris_encode_filename_syntax(
    tmp_path: Path,
    opener: Callable[[Path], sqlite3.Connection],
) -> None:
    database = tmp_path / "application#copy.sqlite3"
    writable = sqlite3.connect(database)
    writable.execute("CREATE TABLE marker (value INTEGER)")
    writable.execute("INSERT INTO marker VALUES(7)")
    writable.commit()
    writable.close()

    connection = opener(database)
    assert connection.execute("SELECT value FROM marker").fetchone() == (7,)
    connection.close()
    assert not (tmp_path / "application").exists()


def test_mount_census_is_unavailable_when_the_file_cap_is_exceeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "one.epub").write_bytes(b"1")
    (tmp_path / "two.epub").write_bytes(b"2")
    monkeypatch.setattr(cold_start, "_MAX_MOUNT_WALK_FILES", 1)
    assert cold_start._count_mount_files(tmp_path) is None


def test_mount_census_is_unavailable_when_a_directory_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable_walk(
        _root: Path,
        *,
        onerror: Callable[[OSError], None],
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        onerror(PermissionError())
        return iter(())

    monkeypatch.setattr(os, "walk", unreadable_walk)
    assert cold_start._count_mount_files(tmp_path) is None


def test_main_rejects_a_malformed_mount_root() -> None:
    with pytest.raises(SystemExit):
        cold_start.main(
            [
                "--config",
                "/synthetic/config.json",
                "--database",
                "/synthetic/application.sqlite3",
                "--catalogue",
                "/synthetic/catalogue.sqlite3",
                "--mount-root",
                "books",
            ],
            stdout=io.StringIO(),
        )


def test_the_schema_is_pinned_at_its_third_version() -> None:
    assert "cold-start-report-v3" == cold_start.COLD_START_REPORT_SCHEMA


_EMPTY_DISPATCH = {
    "reports": 0,
    "lanes": [],
    "providers": [],
    "audible_rungs": [],
    "band": [],
    "near_miss": {"items": 0, "buckets": []},
}


def _dispatch_configuration() -> ApplicationConfiguration:
    configuration = _configuration()
    return ApplicationConfiguration(
        connections=configuration.connections,
        libraries=(
            LibraryConfiguration(
                "audiobooks", "primary", "library-b", "audiobook", "books", ()
            ),
            *configuration.libraries,
        ),
        mounts=configuration.mounts,
        providers=configuration.providers,
    )


def _census_reports() -> tuple[MetadataReport, ...]:
    """Three audiobook reports whose lanes reach every dispatch outcome."""
    from dataclasses import replace

    from mechanical_librarian.application.metadata_report import (
        MetadataDiscoveryLane,
        MetadataDiscoveryPlan,
        ProviderLaneResult,
        ProviderResult,
        WorkLadderFacts,
        provider_attempt_diagnostics,
        provider_observation,
        with_lane_dispatch,
    )
    from mechanical_librarian.application.provider_evidence import (
        ProviderIncompleteCause,
        ProviderIncompleteCauseRecord,
        ProviderOperationalEvidence,
    )
    from mechanical_librarian.domain.metadata_report import (
        DispatchOutcome,
        ProviderLaneDispatch,
        ProviderLaneObservation,
        ProviderObservation,
        ProviderQueryKind,
        ProviderStatus,
        TargetField,
        WorkDispatchLineage,
        work_ladder_diagnostics,
    )
    from mechanical_librarian.domain.work_identity import work_query_ladder
    from tests.support.audiobook_providers import (
        alternative_work_plan,
        asin_plan,
        audio_target,
        work_plan,
    )
    from tests.support.audiobook_reports import (
        audio_local_claims,
        audiobook_candidate,
    )
    from tests.support.metadata_report_doubles import single_track_audio_facts

    def ladder(lane: MetadataDiscoveryLane) -> tuple[WorkDispatchLineage, ...]:
        return tuple(
            WorkDispatchLineage.from_query(query)
            for query in work_query_ladder(
                lane.title or "", lane.authors, audiobook=True
            )
        )

    def observed(
        name: str,
        authority: str,
        results: tuple[ProviderLaneResult, ...],
        *,
        deadline: bool = False,
        ladders: tuple[WorkLadderFacts, ...] = (),
    ) -> ProviderObservation:
        causes = (
            (ProviderIncompleteCauseRecord(ProviderIncompleteCause.DEADLINE),)
            if deadline
            else ()
        )
        observation = provider_observation(
            name,
            results,
            authority=authority,
            diagnostics=(
                provider_attempt_diagnostics(
                    (), status="incomplete", incomplete_causes=causes
                )
                if deadline
                else ()
            ),
        )
        return with_lane_dispatch(
            observation,
            ProviderResult(
                observation,
                evidence=ProviderOperationalEvidence(incomplete_causes=causes),
                work_ladders=ladders,
            ),
            audio=True,
        )

    def report(
        item: str,
        fields: tuple[TargetField, ...],
        plan: MetadataDiscoveryPlan,
        *providers: ProviderObservation,
    ) -> MetadataReport:
        return MetadataReport.create(
            target=TargetFacts("primary", "audiobooks", item, fields),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=plan.local_claims,
            local_reasons=("embedded_tag_duration",),
            providers=tuple(sorted(providers, key=lambda value: value.provider)),
            audio=plan.audio,
        )

    work = replace(work_plan(*audio_local_claims()), audio=single_track_audio_facts())
    (lane,) = work.lanes
    walked = ladder(lane)
    candidate = audiobook_candidate(work.local_claims, provider="audible-admitted")
    disabled = ProviderObservation(
        "second",
        ProviderStatus.DISABLED,
        query_kind=ProviderQueryKind.WORK,
        authority="audible_catalog",
        lanes=(
            ProviderLaneObservation(
                lane.identity,
                lane.kind,
                ProviderStatus.DISABLED,
                lane.input_references,
                dispatch=ProviderLaneDispatch(DispatchOutcome.NOT_DISPATCHED),
            ),
        ),
    )
    first = report(
        "item-1",
        audio_target().fields,
        work,
        observed(
            "audible",
            "audible_catalog",
            (
                ProviderLaneResult(
                    lane,
                    ProviderStatus.NO_MATCH,
                    diagnostics=work_ladder_diagnostics(0, len(walked)),
                    exhausted_dispatch=walked,
                ),
            ),
            ladders=(WorkLadderFacts(lane.identity, len(walked), -1_200),),
        ),
        observed(
            "audible-admitted",
            "audible_catalog",
            (
                ProviderLaneResult(
                    lane,
                    ProviderStatus.CANDIDATES,
                    (candidate,),
                    dispatch_lineage=walked[0],
                ),
            ),
            ladders=(WorkLadderFacts(lane.identity, 1, 4_000),),
        ),
        observed(
            "audible-empty",
            "audible_catalog",
            (
                ProviderLaneResult(
                    lane,
                    ProviderStatus.NO_MATCH,
                    diagnostics=work_ladder_diagnostics(len(walked), 0),
                    exhausted_dispatch=walked,
                ),
            ),
            ladders=(WorkLadderFacts(lane.identity, len(walked)),),
        ),
        observed(
            "audnexus",
            "audnexus",
            (ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE),),
        ),
        observed(
            "hardcover",
            "hardcover",
            (ProviderLaneResult(lane, ProviderStatus.NO_MATCH),),
        ),
        observed(
            "open_library",
            "open_library",
            (ProviderLaneResult(lane, ProviderStatus.INCOMPLETE),),
            deadline=True,
        ),
        disabled,
    )
    alternative = alternative_work_plan()
    asserted, other = alternative.lanes
    second = report(
        "item-2",
        audio_target("Asserted Title", ("Ada Author",)).fields,
        alternative,
        observed(
            "audible",
            "audible_catalog",
            (
                ProviderLaneResult(
                    asserted,
                    ProviderStatus.NO_MATCH,
                    diagnostics=work_ladder_diagnostics(len(ladder(asserted)), 0),
                    exhausted_dispatch=ladder(asserted),
                ),
                ProviderLaneResult(other, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED),
            ),
            ladders=(
                WorkLadderFacts(asserted.identity, len(ladder(asserted))),
                WorkLadderFacts(other.identity, 1),
            ),
        ),
    )
    asin = asin_plan()
    (asin_lane,) = asin.lanes
    third = report(
        "item-3",
        audio_target().fields,
        replace(asin, audio=single_track_audio_facts()),
        observed(
            "audible",
            "audible_catalog",
            (
                ProviderLaneResult(
                    asin_lane,
                    ProviderStatus.CANDIDATES,
                    (audiobook_candidate(asin.local_claims, provider="audible"),),
                ),
            ),
        ),
    )
    return first, second, third


def test_the_audiobook_dispatch_census_counts_every_outcome_exactly() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "application.sqlite3"
        repository = open_repository(database)
        first, second, third = _census_reports()
        for value in (first, second, third):
            repository.publish(value, lambda: True)
        # The third stays a report-only package, which the census still counts.
        for value in (first, second):
            proposal = evaluate_report(value)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
        repository.close()
        connection = cold_start._open_read_only(database)
        try:
            outcomes = connection.execute(
                "SELECT outcome FROM current_review_packages ORDER BY item_id"
            ).fetchall()
            snapshot = cold_start.cold_start_snapshot(connection)
        finally:
            connection.close()

    report = cold_start.assemble_cold_start_report(
        _dispatch_configuration(), snapshot, None, {}, 1
    )
    blocks = {
        library["library"]: library["audiobook_dispatch"]
        for library in cast("list[dict[str, object]]", report["libraries"])
    }

    assert ("report_only",) == outcomes[-1]
    assert _EMPTY_DISPATCH == blocks["ebooks"]
    lanes = [
        ("audible", "asin", "admitted", 1),
        ("audible", "work", "empty", 1),
        ("audible", "work", "refused", 1),
        ("audible", "work_alternative", "overflow", 1),
        ("audible-admitted", "work", "admitted", 1),
        ("audible-empty", "work", "empty", 1),
        ("audnexus", "work", "unavailable", 1),
        ("hardcover", "work", "empty_or_refused", 1),
        ("open_library", "work", "starved", 1),
        ("second", "work", "not_dispatched", 1),
    ]
    assert {
        "reports": 3,
        "lanes": [
            {"provider": provider, "lane": lane, "outcome": outcome, "count": count}
            for provider, lane, outcome, count in lanes
        ],
        "providers": [
            {"provider": provider, "class": value, "items": items}
            for provider, value, items in (
                ("audible", "admitted", 1),
                ("audible", "overflow", 1),
                ("audible", "refused", 1),
                ("audible-admitted", "admitted", 1),
                ("audible-empty", "empty", 1),
                ("audnexus", "unavailable", 1),
                ("hardcover", "empty_or_refused", 1),
                ("open_library", "starved", 1),
                ("second", "not_dispatched", 1),
            )
        ],
        "audible_rungs": [
            {"outcome": "admitted", "rungs": 1, "count": 1},
            {"outcome": "empty", "rungs": 1, "count": 2},
            {"outcome": "overflow", "rungs": 1, "count": 1},
            {"outcome": "refused", "rungs": 1, "count": 1},
        ],
        "band": [
            {"provider": provider, "band": band, "count": count}
            for provider, band, count in (
                ("audible", "none", 3),
                ("audible", "tight", 1),
                ("audible-admitted", "tight", 1),
                ("audible-empty", "none", 1),
                ("audnexus", "none", 1),
                ("hardcover", "none", 1),
                ("open_library", "none", 1),
                ("second", "none", 1),
            )
        ],
        "near_miss": {
            "items": 2,
            "buckets": [
                {"sign": "+", "range": "3600+", "count": 1},
                {"sign": "-", "range": "900-1800", "count": 1},
            ],
        },
    } == blocks["audiobooks"]
    serialized = json.dumps(blocks["audiobooks"])
    assert "Project Hail Mary" not in serialized and "B08G9PRS1K" not in serialized


@pytest.mark.parametrize(
    ("value", "bucket"),
    (
        (601, ("+", "600-900")),
        (-900, ("-", "600-900")),
        (901, ("+", "900-1800")),
        (-1_800, ("-", "900-1800")),
        (3_600, ("+", "1800-3600")),
        (-3_601, ("-", "3600+")),
    ),
)
def test_near_miss_buckets_close_on_their_upper_bound(
    value: int, bucket: tuple[str, str]
) -> None:
    assert bucket == cold_start._near_miss_bucket(value)


def _timing_file(directory: Path) -> Path:
    from mechanical_librarian.adapters.operational_evidence import LocalTimingStore
    from mechanical_librarian.application.operations import StageTiming

    path = directory / "private" / "stage-timings.json"
    store = LocalTimingStore(path, enabled=True)
    for recorded_at, duration in enumerate((5, 1, 4, 2, 3, 10, 9, 8, 7, 6), 100):
        store.append(StageTiming("provider_search", duration, "provider", recorded_at))
    store.append(StageTiming("cover", 7, "target", 90))
    return path


def test_the_stage_timing_split_reports_nearest_rank_percentiles() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        timings = cold_start._read_stage_timings(_timing_file(Path(temporary)))

    report = cold_start.assemble_cold_start_report(
        _configuration(), _empty_snapshot(), None, {}, 1, timings
    )

    assert {
        "basis": "retained_tail",
        "records": 11,
        "first_recorded_at": 90,
        "last_recorded_at": 109,
        "stages": [
            {
                "stage": "cover",
                "wait_class": "target",
                "count": 1,
                "p50": 7,
                "p95": 7,
                "max": 7,
            },
            {
                "stage": "provider_search",
                "wait_class": "provider",
                "count": 10,
                "p50": 5,
                "p95": 10,
                "max": 10,
            },
        ],
    } == report["stage_timings"]
    assert (
        cold_start.assemble_cold_start_report(
            _configuration(), _empty_snapshot(), None, {}, 1
        )["stage_timings"]
        is None
    )


def test_a_missing_or_unreadable_stage_timing_file_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit, match="names no stage-timing file"):
        cold_start._read_stage_timings(tmp_path / "private" / "absent.json")
    path = _timing_file(tmp_path)
    path.chmod(0o644)
    with pytest.raises(SystemExit, match="file is unreadable"):
        cold_start._read_stage_timings(path)


def _outcome_reports() -> tuple[MetadataReport, ...]:
    """Three more audiobooks: one runtime reason each, and one work-only case."""
    from dataclasses import replace

    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        ProviderResult,
        provider_observation,
        with_lane_dispatch,
    )
    from mechanical_librarian.domain.cover_evidence import (
        CoverSource,
        CoverSourceKind,
        CoverSourceState,
        cover_source_id,
    )
    from mechanical_librarian.domain.metadata_report import (
        ProviderRequestAccounting,
        ProviderStatus,
        WorkDispatchLineage,
    )
    from mechanical_librarian.domain.work_identity import work_query_ladder
    from tests.support.audiobook_providers import audio_target, work_plan
    from tests.support.audiobook_reports import (
        audio_local_claims,
        audiobook_candidate,
    )
    from tests.support.metadata_report_doubles import single_track_audio_facts

    plan = replace(work_plan(*audio_local_claims()), audio=single_track_audio_facts())
    (lane,) = plan.lanes
    first_rung = WorkDispatchLineage.from_query(
        work_query_ladder(lane.title or "", lane.authors, audiobook=True)[0]
    )

    def report(
        item: str,
        duration: str,
        cover: CoverSource,
        dispatched: tuple[int, int],
        series: tuple[str, str] | None = None,
    ) -> MetadataReport:
        observation = provider_observation(
            "audible",
            (
                ProviderLaneResult(
                    lane,
                    ProviderStatus.CANDIDATES,
                    (
                        audiobook_candidate(
                            plan.local_claims,
                            provider="audible",
                            duration=duration,
                            series=series,
                        ),
                    ),
                    dispatch_lineage=first_rung,
                ),
            ),
            authority="audible_catalog",
        )
        initial, retry = dispatched
        observation = with_lane_dispatch(
            replace(
                observation,
                request_accounting=ProviderRequestAccounting(initial, initial, retry),
            ),
            ProviderResult(observation),
            audio=True,
        )
        return MetadataReport.create(
            target=TargetFacts("primary", "audiobooks", item, audio_target().fields),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=plan.local_claims,
            local_reasons=("embedded_tag_duration",),
            providers=(observation,),
            cover_sources=(cover,),
            audio=plan.audio,
        )

    def abs_cover(state: CoverSourceState, scope: str | None = None) -> CoverSource:
        return CoverSource(
            cover_source_id(CoverSourceKind.ABS, "absent"),
            CoverSourceKind.ABS,
            state,
            timeout_scope=scope,
        )

    # 58,244 s locally: 50,000 is outside the loose band and 58,700 inside it.
    return (
        report(
            "item-4",
            "50000.000",
            abs_cover(CoverSourceState.TIMEOUT, "attempted"),
            (3, 1),
        ),
        report(
            "item-5",
            "58700.000",
            abs_cover(
                CoverSourceState.TIMEOUT, "not_started_due_to_aggregate_deadline"
            ),
            (1, 0),
        ),
        report(
            "item-6",
            "50000.000",
            abs_cover(CoverSourceState.ABSENT),
            (0, 0),
            series=("The Series", "1"),
        ),
    )


def test_audiobook_outcomes_cover_reads_and_dispatches_count_exactly() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "application.sqlite3"
        repository = open_repository(database)
        for value in (*_census_reports(), *_outcome_reports()):
            repository.publish(value, lambda: True)
        repository.close()
        connection = cold_start._open_read_only(database)
        try:
            snapshot = cold_start.cold_start_snapshot(connection)
        finally:
            connection.close()

    report = cold_start.assemble_cold_start_report(
        _dispatch_configuration(), snapshot, None, {}, 1
    )
    blocks = {
        library["library"]: library
        for library in cast("list[dict[str, object]]", report["libraries"])
    }
    audiobooks = blocks["audiobooks"]

    assert {
        "reports": 6,
        "recording_admitted": 2,
        "work_only": 1,
        "evidence_only": {
            "total": 3,
            "no_retained_recording_candidate": 1,
            "runtime_contradiction": 1,
            "runtime_near_miss_only_or_mixed": 1,
            "tight_band_candidate_but_not_admitted": 0,
        },
    } == audiobooks["audiobook_outcomes"]
    assert {
        "reports": 6,
        "without_read": 3,
        "states": [
            {"state": "absent", "timeout_scope": None, "count": 1},
            {"state": "timeout", "timeout_scope": "attempted", "count": 1},
            {
                "state": "timeout",
                "timeout_scope": "not_started_due_to_aggregate_deadline",
                "count": 1,
            },
        ],
    } == audiobooks["audiobook_cover_reads"]
    providers = [
        "audible",
        "audible-admitted",
        "audible-empty",
        "audnexus",
        "hardcover",
        "open_library",
        "second",
    ]
    assert {
        "items": 6,
        "initial": 4,
        "retry": 1,
        "mean_per_item": 0.83,
        "per_item": [
            {"range": name, "items": items}
            for name, items in (
                ("0", 4),
                ("1", 1),
                ("2", 0),
                ("3-5", 1),
                ("6-10", 0),
                ("11-20", 0),
                ("21+", 0),
            )
        ],
        "providers": [
            {
                "provider": name,
                "initial": 4 if name == "audible" else 0,
                "retry": 1 if name == "audible" else 0,
            }
            for name in providers
        ],
    } == audiobooks["dispatches"]
    ebooks = blocks["ebooks"]
    assert 0 == cast("dict[str, object]", ebooks["audiobook_outcomes"])["reports"]
    assert None is cast("dict[str, object]", ebooks["dispatches"])["mean_per_item"]
    assert None is report["recovery_jobs"]


@pytest.mark.parametrize(
    ("bands", "reason"),
    (
        (frozenset(), "no_retained_recording_candidate"),
        (frozenset({"outside"}), "runtime_contradiction"),
        (frozenset({"loose"}), "runtime_near_miss_only_or_mixed"),
        (frozenset({"loose", "outside"}), "runtime_near_miss_only_or_mixed"),
        (frozenset({"unstated", "outside"}), "runtime_near_miss_only_or_mixed"),
        (frozenset({"tight", "outside"}), "tight_band_candidate_but_not_admitted"),
    ),
)
def test_an_evidence_only_audiobook_is_named_by_its_best_bands(
    bands: frozenset[str], reason: str
) -> None:
    assert reason == cold_start._evidence_only_reason(bands)


def test_recovery_jobs_count_by_provider_library_and_state(tmp_path: Path) -> None:
    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )

    path = tmp_path / "provider-recovery.sqlite"
    store = SQLiteProviderRecoveryStore.open(path)
    store.close()
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "INSERT INTO provider_recovery_members VALUES('catalogue','hardcover','search')"
        )
        for member, library, state in (
            ("member-1", "ebooks", "queued"),
            ("member-2", "ebooks", "queued"),
            ("member-3", "ebooks", "complete"),
            ("member-4", "audiobooks", "failed"),
        ):
            connection.execute(
                "INSERT INTO provider_recovery_jobs(connection,adapter,scope,"
                "generation,member_id,target_connection,library,item,"
                "file_identity,source_report_id,package_report_id,"
                "package_outcome,lane_ids,state,attempt,dispatch_reason,"
                "dispatch_rank,enrollment_ordinal) VALUES('catalogue','hardcover',"
                "'search',1,?,'primary',?,'item','file','report','report',"
                "'report_only','[]',?,0,'blanket_scan',3,1)",
                (member, library, state),
            )
    connection.close()

    rows = cold_start._read_recovery_jobs(path)
    report = cold_start.assemble_cold_start_report(
        _configuration(), _empty_snapshot(), None, {}, 1, recovery_jobs=rows
    )

    group = {"provider": "catalogue", "adapter": "hardcover", "connection": "primary"}
    assert {
        "jobs": 4,
        "groups": [
            {**group, "library": "audiobooks", "state": "failed", "count": 1},
            {**group, "library": "ebooks", "state": "complete", "count": 1},
            {**group, "library": "ebooks", "state": "queued", "count": 2},
        ],
    } == report["recovery_jobs"]
    with pytest.raises(SystemExit, match="names no recovery database"):
        cold_start._read_recovery_jobs(tmp_path / "absent.sqlite")
    (tmp_path / "other.sqlite").write_bytes(b"")
    with pytest.raises(SystemExit, match="is unreadable"):
        cold_start._read_recovery_jobs(tmp_path / "other.sqlite")
