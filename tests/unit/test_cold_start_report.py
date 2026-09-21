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
    cover_currentness_from_facts,
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
    repository.bind_cover_consideration_currentness(
        lambda document, replay_reference: cover_currentness_from_facts(
            document, replay_reference, lambda *_identity: False
        )
    )
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
            ],
            stdout=output,
        )

        assert code == 0
        assert before == database.read_bytes()
        report = json.loads(output.getvalue())
        assert report["schema_version"] == cold_start.COLD_START_REPORT_SCHEMA
        assert report["catalogue_generation"] is None
        assert report["totals"]["works"] == 1
        library = report["libraries"][0]
        fidelity = library["scan_fidelity"]
        assert fidelity["mount_files_by_extension"] == {"epub": 2, "txt": 1}
        assert fidelity["target_items_by_classification"] is None
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
