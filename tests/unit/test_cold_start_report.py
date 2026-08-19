"""The cold-start report reconciles with the surfaces it summarises.

The workbench script composes the Attention surface's own SQL fragments, so
the first test proves the numbers agree end-to-end through a real repository;
the pure-assembly tests pin the conditional determinism comparison and the
ledger-derived timing; the entry-point test bounds the read-only invocation.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from typing import cast

import pytest
import scripts.cold_start_report as cold_start

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
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


def _for_item(source: MetadataReport, item: str, identity: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            source.target.connection,
            source.target.library,
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
        "provider_lanes": [],
        "unmatched_works": [],
        "scans": [],
        "work_states": [],
        "absences": [],
        "duplicates": [],
        "agreement": [],
    }


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
            "item-1",
            "edition-1",
        ):
            assert leaked not in serialized


def test_duplicate_determinism_is_conditioned_on_target_context() -> None:
    snapshot = _empty_snapshot()
    snapshot["duplicates"] = [
        # Same bytes, same target context, different results: nondeterminism.
        ("digest-1", "context-a", "results-1"),
        ("digest-1", "context-a", "results-2"),
        # Same bytes but different target context: a different input, so the
        # differing results are expected and the group is not even compared.
        ("digest-2", "context-a", "results-1"),
        ("digest-2", "context-b", "results-2"),
        # Same bytes, same context, same results: compared and clean.
        ("digest-3", "context-a", "results-1"),
        ("digest-3", "context-a", "results-1"),
    ]
    report = cold_start.assemble_cold_start_report(
        _configuration(), snapshot, None, {}, 0
    )
    assert report["determinism"] == {
        "duplicate_groups_compared": 2,
        "divergent_groups": 1,
    }


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
        repository.close()
        before = database.read_bytes()
        mount_root = root / "books"
        mount_root.mkdir()
        (mount_root / "one.epub").write_bytes(b"a")
        (mount_root / "two.EPUB").write_bytes(b"b")
        (mount_root / "notes.txt").write_bytes(b"c")
        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": "config-v6",
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
