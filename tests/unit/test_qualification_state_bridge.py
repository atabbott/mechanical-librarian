from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

import mechanical_librarian.adapters.persistence.qualification_state_bridge as bridge
from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.qualification_state_bridge import (
    RETAINED_LIBRARY_V26_TO_V32,
    QualificationStateBridgeRecoveryRequired,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderCandidate,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    canonical_json,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
)
from mechanical_librarian.interfaces.cli import main
from tests.support.metadata_report_schemas import synthetic_report


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _v26_document(
    *,
    missing_work_lineage: bool = False,
    artifact_bearing_cover: bool = False,
    nonartifact_cover: bool = False,
) -> str:
    data = synthetic_report().to_data()
    data.pop("audio")
    data["schema_version"] = "metadata-report-v26"
    data["comparison_version"] = "closed-title-creator-publication-v4"
    data["field_results"] = [
        {
            **value,
            "sources": [
                source
                for source in value["sources"]
                if source["source"] in {"epub", "target_metadata"}
            ],
        }
        for value in data["field_results"]
        if value["kind"] not in {"duration", "performance_type"}
    ]
    providers = data["providers"]
    if missing_work_lineage:
        provider = ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            candidates=(ProviderCandidate("edition-1", "work-1", ()),),
            query_kind=ProviderQueryKind.WORK,
            authority="open_library",
            accessed_at="2026-01-01T00:00:00.000000Z",
            dispatch_lineage=WorkDispatchLineage(WorkDispatchRung.NORMALIZED),
        ).to_data()
        provider.pop("dispatch_lineage")
        provider.pop("transformation_lineage")
        providers = [provider]
    else:
        for provider in providers:
            provider.pop("dispatch_lineage")
            provider.pop("transformation_lineage")
            for lane in provider["lanes"]:
                lane.pop("diagnostics")
                lane.pop("dispatch_lineage")
                lane.pop("transformation_lineage")
    data["providers"] = providers
    if artifact_bearing_cover:
        artifact = CoverArtifact.create(
            b"synthetic original", b"synthetic preview", "image/png", 2, 3
        )
        data["covers"] = [
            CoverSource(
                cover_source_id(CoverSourceKind.EPUB, "OPS/cover.png"),
                CoverSourceKind.EPUB,
                CoverSourceState.RETRIEVED,
                artifact,
            ).to_data()
        ]
    elif nonartifact_cover:
        data["covers"] = [
            CoverSource(
                cover_source_id(CoverSourceKind.EPUB, "OPS/missing-cover.png"),
                CoverSourceKind.EPUB,
                CoverSourceState.ABSENT,
            ).to_data()
        ]
    identity = dict(data)
    identity.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    )
    return canonical_json(data)


def _create_source(
    path: Path, *, missing_work_lineage: bool = False, document: str | None = None
) -> str:
    path.parent.chmod(0o700)
    report_document = (
        _v26_document(missing_work_lineage=missing_work_lineage)
        if document is None
        else document
    )
    data = json.loads(report_document)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE metadata_reports ("
        "report_id TEXT PRIMARY KEY NOT NULL, "
        "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, "
        "item_id TEXT NOT NULL, file_identity TEXT NOT NULL, "
        "content_digest TEXT NOT NULL, schema_version TEXT NOT NULL, "
        "document_digest TEXT NOT NULL, document TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE current_metadata_reports ("
        "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, "
        "item_id TEXT NOT NULL, file_identity TEXT NOT NULL, "
        "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
        "PRIMARY KEY(connection_name,library_name,item_id,file_identity))"
    )
    connection.execute(
        "CREATE TABLE current_metadata_report_lineages ("
        "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, "
        "item_id TEXT NOT NULL, "
        "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
        "PRIMARY KEY(connection_name,library_name,item_id))"
    )
    connection.execute(
        "INSERT INTO metadata_reports VALUES(?,?,?,?,?,?,?,?,?)",
        (
            data["report_id"],
            data["target"]["connection"],
            data["target"]["library"],
            data["target"]["item"],
            data["file"]["identity"],
            data["file"]["content_digest"],
            data["schema_version"],
            hashlib.sha256(report_document.encode("utf-8")).hexdigest(),
            report_document,
        ),
    )
    connection.execute(
        "INSERT INTO current_metadata_reports VALUES(?,?,?,?,?)",
        (
            data["target"]["connection"],
            data["target"]["library"],
            data["target"]["item"],
            data["file"]["identity"],
            data["report_id"],
        ),
    )
    connection.execute(
        "INSERT INTO current_metadata_report_lineages VALUES(?,?,?,?)",
        (
            data["target"]["connection"],
            data["target"]["library"],
            data["target"]["item"],
            data["report_id"],
        ),
    )
    connection.commit()
    connection.close()
    path.chmod(0o600)
    return data["report_id"]


def _accept_synthetic_source(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        digest = bridge._schema_objects_digest(connection)
    finally:
        connection.close()
    monkeypatch.setattr(bridge, "_SOURCE_SCHEMA_OBJECTS_SHA256", digest)


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "source.sqlite3",
        tmp_path / "backup.sqlite3",
        tmp_path / "current.sqlite3",
    )


def test_exact_predecessor_builds_fresh_current_state_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    source_before = _digest(source)

    result = bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )

    assert result == {
        "plan": RETAINED_LIBRARY_V26_TO_V32,
        "status": "bridged",
        "reports": 1,
        "selectively_invalidated": 0,
    }
    assert _digest(source) == source_before == _digest(backup)
    assert stat_mode(source) == stat_mode(backup) == stat_mode(output) == 0o600
    with pytest.raises(MetadataReportError):
        open_repository(source, create=False)
    repository = open_repository(output, create=False)
    repository.close()
    connection = sqlite3.connect(f"file:{output}?mode=ro", uri=True)
    document = connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
    report = MetadataReport.from_json(document)
    assert report.schema_version == "metadata-report-v38"
    assert connection.execute("SELECT count(*) FROM proposal_sets").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM review_cases").fetchone() == (0,)
    assert connection.execute(
        "SELECT outcome FROM current_review_packages"
    ).fetchall() == [("report_only",)]
    connection.close()


def test_target_schema_binding_is_the_exact_installed_contract() -> None:
    assert bridge._TARGET_SCHEMA_OBJECTS_SHA256 == (
        "1faba21f99d464fbdd0b5948a64fc8b2abd51137a7f09f968b443f58f013d36e"
    )
    assert (
        bridge._expected_schema_objects_digest() == bridge._TARGET_SCHEMA_OBJECTS_SHA256
    )


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_missing_work_dispatch_lineage_is_selectively_invalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source, missing_work_lineage=True)
    _accept_synthetic_source(monkeypatch, source)

    result = bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )

    assert result["selectively_invalidated"] == 1
    connection = sqlite3.connect(f"file:{output}?mode=ro", uri=True)
    report = MetadataReport.from_json(
        connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
    )
    connection.close()
    assert report.providers == ()
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        source_document = json.loads(
            connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
        )
    assert source_document["providers"][0]["status"] == "candidates"


def test_malformed_cover_dependent_on_invalidated_provider_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(_v26_document(missing_work_lineage=True))
    data["covers"] = [
        {
            "artifact": None,
            "kind": "invalid",
            "provenance": {"provider": "catalogue"},
            "source_id": "invalid_source",
            "state": "absent",
            "timeout_scope": None,
        }
    ]
    identity = dict(data)
    identity.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    )
    source, backup, output = _paths(tmp_path)
    _create_source(source, document=canonical_json(data))
    _accept_synthetic_source(monkeypatch, source)

    with pytest.raises(
        MetadataReportError, match="qualification-state source is incompatible"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


def test_artifact_bearing_cover_is_invalidated_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source, document=_v26_document(artifact_bearing_cover=True))
    _accept_synthetic_source(monkeypatch, source)
    source_before = _digest(source)

    result = bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )

    assert result["selectively_invalidated"] == 1
    with sqlite3.connect(f"file:{output}?mode=ro", uri=True) as connection:
        report = MetadataReport.from_json(
            connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
        )
    assert report.cover_sources == ()
    assert _digest(source) == source_before == _digest(backup)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        source_document = json.loads(
            connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
        )
    assert source_document["covers"][0]["artifact"] is not None


def test_exact_output_and_backup_make_rerun_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    before = (_digest(source), _digest(backup), _digest(output))

    result = bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )

    assert result["status"] == "already-current"
    assert (_digest(source), _digest(backup), _digest(output)) == before


def test_prior_v22_output_is_rejected_without_mutating_any_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    with sqlite3.connect(output) as connection:
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=replace(sql, ?, ?) WHERE name='proposal_sets'",
            ("fixed-conservative-v23", "fixed-conservative-v22"),
        )
        connection.commit()
        connection.execute("PRAGMA writable_schema=OFF")
    connection.close()
    with sqlite3.connect(f"file:{output}?mode=ro", uri=True) as connection:
        assert bridge._schema_objects_digest(connection) == (
            "6ab7440f7c4dd8f4e17c7909305fa6296fdb597b42497b6f326f32cc9216fb0f"
        )
    connection.close()
    before = (_digest(source), _digest(backup), _digest(output))

    with pytest.raises(MetadataReportError):
        open_repository(output, create=False)
    assert (_digest(source), _digest(backup), _digest(output)) == before
    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )
    assert (_digest(source), _digest(backup), _digest(output)) == before


def test_durable_temp_is_reconciled_after_ambiguous_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    temp = output.with_name(f".{output.name}.qualification-state-bridge.tmp")
    os.link(output, temp)
    output.unlink()

    result = bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )

    assert result["status"] == "bridged"
    assert output.exists()
    assert not temp.exists()


def test_existing_output_temp_cleanup_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    temp = output.with_name(f".{output.name}.qualification-state-bridge.tmp")
    os.link(output, temp)
    real_unlink = bridge.os.unlink

    def fail_temp_unlink(
        path: str | os.PathLike[str],
        *,
        dir_fd: int | None = None,
    ) -> None:
        if Path(path) == temp:
            raise OSError("synthetic recovery cleanup failure")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(bridge.os, "unlink", fail_temp_unlink)
    with pytest.raises(QualificationStateBridgeRecoveryRequired):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.exists()
    assert temp.exists()


def test_unknown_source_contract_fails_before_backup_or_output(
    tmp_path: Path,
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    before = _digest(source)

    with pytest.raises(
        MetadataReportError, match="qualification-state source is incompatible"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert _digest(source) == before
    assert not backup.exists()
    assert not output.exists()


@pytest.mark.parametrize("suffix", ("-journal", "-wal", "-shm"))
def test_source_sidecars_are_rejected_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    source.with_name(source.name + suffix).write_bytes(b"sidecar")

    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


def test_closed_wal_source_is_rejected_before_backup_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    connection = sqlite3.connect(source)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    for suffix in ("-wal", "-shm"):
        source.with_name(source.name + suffix).unlink(missing_ok=True)
    source_before = _digest(source)

    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert _digest(source) == source_before
    assert not backup.exists()
    assert not output.exists()


def test_semantically_changed_source_document_fails_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(_v26_document())
    data["field_results"][0]["state"] = "unsupported"
    identity = dict(data)
    identity.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    )
    document = canonical_json(data)
    source, backup, output = _paths(tmp_path)
    _create_source(source, document=document)
    _accept_synthetic_source(monkeypatch, source)

    with pytest.raises(
        MetadataReportError, match="qualification-state source is incompatible"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


def test_inconsistent_retained_local_status_fails_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(_v26_document())
    data["local"]["status"] = (
        "no_candidate" if data["local"]["status"] == "candidate" else "candidate"
    )
    identity = dict(data)
    identity.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    )
    source, backup, output = _paths(tmp_path)
    _create_source(source, document=canonical_json(data))
    _accept_synthetic_source(monkeypatch, source)

    with pytest.raises(
        MetadataReportError, match="qualification-state source is incompatible"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


@pytest.mark.parametrize(
    ("table", "identity_columns"),
    (
        (
            "current_metadata_reports",
            "connection_name,library_name,item_id,file_identity",
        ),
        (
            "current_metadata_report_lineages",
            "connection_name,library_name,item_id",
        ),
    ),
)
def test_forged_current_pointer_is_rejected_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
    identity_columns: str,
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    with sqlite3.connect(source) as connection:
        values = list(
            connection.execute(f"SELECT {identity_columns} FROM {table}").fetchone()
        )
        values[2] = "forged-item"
        assignments = ",".join(f"{column}=?" for column in identity_columns.split(","))
        connection.execute(f"UPDATE {table} SET {assignments}", values)
    _accept_synthetic_source(monkeypatch, source)

    with pytest.raises(
        MetadataReportError, match="qualification-state source is incompatible"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


def test_unknown_existing_output_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    backup.write_bytes(source.read_bytes())
    backup.chmod(0o600)
    output.write_bytes(b"not a database")
    output.chmod(0o600)

    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.read_bytes() == b"not a database"


@pytest.mark.parametrize(
    "mutation",
    ("review-package", "cover-reference", "downstream-default"),
)
def test_changed_exact_schema_output_is_not_accepted_as_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source, document=_v26_document(nonartifact_cover=True))
    _accept_synthetic_source(monkeypatch, source)
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    with sqlite3.connect(output) as connection:
        if mutation == "review-package":
            connection.execute("DELETE FROM current_review_packages")
        elif mutation == "cover-reference":
            (trigger_sql,) = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='trigger' AND name='metadata_report_cover_references_no_delete'"
            ).fetchone()
            connection.execute(
                "DROP TRIGGER metadata_report_cover_references_no_delete"
            )
            connection.execute("DELETE FROM metadata_report_cover_references")
            connection.execute(trigger_sql)
        else:
            connection.execute("DELETE FROM automatic_safety_state")

    with pytest.raises(
        MetadataReportError, match="qualification-state output is incompatible"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.exists()


def test_frozen_target_contract_is_checked_before_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge, "_TARGET_SCHEMA_OBJECTS_SHA256", "0" * 64)
    source, backup, output = _paths(tmp_path)

    with pytest.raises(
        MetadataReportError,
        match="qualification-state bridge is unavailable for this build",
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not source.exists()
    assert not backup.exists()
    assert not output.exists()


@pytest.mark.parametrize(
    "plan",
    ["unknown", "", "review-owner-decision-actions-v1"],
)
def test_unknown_plan_is_rejected_without_filesystem_access(
    tmp_path: Path, plan: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge, "_TARGET_SCHEMA_OBJECTS_SHA256", "0" * 64)
    source, backup, output = _paths(tmp_path)

    with pytest.raises(MetadataReportError, match="plan is unsupported"):
        bridge.bridge_qualification_state(source, backup, output, plan)

    assert not source.exists()
    assert not backup.exists()
    assert not output.exists()


def test_symlink_and_non_owner_only_sources_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    symlink = tmp_path / "linked.sqlite3"
    symlink.symlink_to(source)

    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            symlink, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )
    source.chmod(0o644)
    with pytest.raises(
        MetadataReportError, match="qualification-state source is not owner-only"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


def test_mismatched_backup_collision_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    backup.write_bytes(b"collision")
    backup.chmod(0o600)
    before = _digest(source)

    with pytest.raises(
        MetadataReportError, match="qualification-state backup identity changed"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert _digest(source) == before
    assert not output.exists()


def test_byte_identical_backup_inode_replacement_is_rejected_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_validate = bridge._validate_output
    replaced = False

    def replace_backup_after_temp_validation(
        path: Path, bridge_data: bridge._BridgeData
    ) -> tuple[int, int]:
        nonlocal replaced
        result = real_validate(path, bridge_data)
        if path.name.startswith(".") and not replaced:
            replacement = backup.with_name("replacement-backup.sqlite3")
            replacement.write_bytes(backup.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(backup)
            replaced = True
        return result

    monkeypatch.setattr(
        bridge, "_validate_output", replace_backup_after_temp_validation
    )
    with pytest.raises(
        MetadataReportError, match="qualification-state backup identity changed"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert replaced
    assert _digest(source) == _digest(backup)
    assert not output.exists()


def test_backup_write_failure_removes_partial_backup_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    before = _digest(source)

    def fail_write(descriptor: int, value: object) -> int:
        raise OSError("no space")

    monkeypatch.setattr(bridge.os, "write", fail_write)
    with pytest.raises(
        MetadataReportError, match="qualification-state backup is unavailable"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert _digest(source) == before
    assert not backup.exists()
    assert not output.exists()


def test_backup_close_failure_still_unlinks_partial_and_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_close = bridge.os.close
    backup_descriptor = -1

    def fail_write(descriptor: int, value: object) -> int:
        nonlocal backup_descriptor
        backup_descriptor = descriptor
        raise OSError("synthetic full disk")

    def fail_backup_close(descriptor: int) -> None:
        if descriptor == backup_descriptor:
            raise OSError("synthetic close failure")
        real_close(descriptor)

    monkeypatch.setattr(bridge.os, "write", fail_write)
    monkeypatch.setattr(bridge.os, "close", fail_backup_close)
    try:
        with pytest.raises(QualificationStateBridgeRecoveryRequired):
            bridge.bridge_qualification_state(
                source, backup, output, RETAINED_LIBRARY_V26_TO_V32
            )
    finally:
        if backup_descriptor >= 0:
            real_close(backup_descriptor)

    assert not backup.exists()
    assert not output.exists()


def test_interrupted_reconstruction_removes_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    before = _digest(source)

    def interrupt(
        connection: sqlite3.Connection, bridge_data: bridge._BridgeData
    ) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(bridge, "_insert_bridge_data", interrupt)
    with pytest.raises(KeyboardInterrupt):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    temp = output.with_name(f".{output.name}.qualification-state-bridge.tmp")
    assert _digest(source) == before == _digest(backup)
    assert not output.exists()
    assert not temp.exists()


@pytest.mark.parametrize("interrupt", (False, True))
def test_postpublication_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: bool
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    validations = 0
    real_validate = bridge._validate_output

    def fail_final(path: Path, bridge_data: bridge._BridgeData) -> tuple[int, int]:
        nonlocal validations
        validations += 1
        if validations == 2:
            if interrupt:
                raise KeyboardInterrupt
            raise MetadataReportError("postpublication failure")
        return real_validate(path, bridge_data)

    monkeypatch.setattr(bridge, "_validate_output", fail_final)
    with pytest.raises(QualificationStateBridgeRecoveryRequired):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.exists()
    assert backup.exists()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux binding cleanup"
)
def test_postpublication_cleanup_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_close = _LinuxDatabaseBinding.close

    def fail_after_close(binding: _LinuxDatabaseBinding) -> None:
        real_close(binding)
        raise MetadataReportError("synthetic cleanup failure")

    monkeypatch.setattr(_LinuxDatabaseBinding, "close", fail_after_close)
    with pytest.raises(QualificationStateBridgeRecoveryRequired):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.exists()
    assert backup.exists()


def test_postlink_identity_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_require = bridge._require_named_identity

    def fail_output_identity(
        parent_descriptor: int, name: str, identity: tuple[int, int]
    ) -> None:
        if name == output.name:
            raise MetadataReportError("synthetic output identity failure")
        real_require(parent_descriptor, name, identity)

    monkeypatch.setattr(bridge, "_require_named_identity", fail_output_identity)
    with pytest.raises(QualificationStateBridgeRecoveryRequired):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.exists()
    assert backup.exists()


def test_interruption_immediately_after_publication_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_publish = bridge._publish_temp

    def interrupt_after_publication(
        parent_descriptor: int,
        temp_name: str,
        output_name: str,
        expected_identity: tuple[int, int],
    ) -> None:
        real_publish(parent_descriptor, temp_name, output_name, expected_identity)
        raise KeyboardInterrupt

    monkeypatch.setattr(bridge, "_publish_temp", interrupt_after_publication)
    with pytest.raises(QualificationStateBridgeRecoveryRequired):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert output.exists()
    assert backup.exists()


def test_postpublication_byte_identical_output_replacement_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_validate = bridge._validate_output
    replaced = False

    def replace_before_output_validation(
        path: Path, bridge_data: bridge._BridgeData
    ) -> tuple[int, int]:
        nonlocal replaced
        if path == output and not replaced:
            replacement = output.with_name("replacement-output.sqlite3")
            replacement.write_bytes(output.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(output)
            replaced = True
        return real_validate(path, bridge_data)

    monkeypatch.setattr(bridge, "_validate_output", replace_before_output_validation)
    with pytest.raises(QualificationStateBridgeRecoveryRequired):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert replaced
    assert output.exists()
    assert backup.exists()


def test_path_recheck_failure_never_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    checks = 0
    real_require = bridge._require_source_current

    def fail_after_backup(
        database: Path,
        identity: tuple[int, int],
        parent_descriptor: int,
        binding: object,
    ) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise MetadataReportError("qualification-state source pathname changed")
        real_require(database, identity, parent_descriptor, binding)  # type: ignore[arg-type]

    monkeypatch.setattr(bridge, "_require_source_current", fail_after_backup)
    with pytest.raises(
        MetadataReportError, match="qualification-state source pathname changed"
    ):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert backup.exists()
    assert not output.exists()


def test_existing_output_path_replacement_never_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    displaced = output.with_name("displaced-output.sqlite3")
    real_validate = bridge._validate_output
    replaced = False

    def replace_after_validation(
        path: Path, bridge_data: bridge._BridgeData
    ) -> tuple[int, int]:
        nonlocal replaced
        identity = real_validate(path, bridge_data)
        if path == output and not replaced:
            output.replace(displaced)
            output.write_bytes(b"replacement")
            output.chmod(0o600)
            replaced = True
        return identity

    monkeypatch.setattr(bridge, "_validate_output", replace_after_validation)
    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert replaced
    assert output.read_bytes() == b"replacement"


def test_backup_directory_entry_is_durable_before_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    real_fsync = bridge.os.fsync
    directory_synced_before_output = False

    def record_fsync(descriptor: int) -> None:
        nonlocal directory_synced_before_output
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not output.exists():
            directory_synced_before_output = True
        real_fsync(descriptor)

    monkeypatch.setattr(bridge.os, "fsync", record_fsync)

    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )

    assert directory_synced_before_output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux authority lock")
def test_live_source_parent_authority_is_rejected_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MetadataReportError):
            bridge.bridge_qualification_state(
                source, backup, output, RETAINED_LIBRARY_V26_TO_V32
            )
    finally:
        fcntl.flock(parent, fcntl.LOCK_UN)
        os.close(parent)

    assert not backup.exists()
    assert not output.exists()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux binding aliases"
)
def test_stale_private_binding_is_rejected_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, backup, output = _paths(tmp_path)
    _create_source(source)
    _accept_synthetic_source(monkeypatch, source)
    alias = (
        tmp_path / ".metadata-report-binding-00000000000000000000000000000000.sqlite"
    )
    os.link(source, alias)
    alias.with_name(alias.name + "-journal").write_bytes(b"retained hot journal")

    with pytest.raises(MetadataReportError):
        bridge.bridge_qualification_state(
            source, backup, output, RETAINED_LIBRARY_V26_TO_V32
        )

    assert not backup.exists()
    assert not output.exists()


def test_installed_command_reports_public_safe_result() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    result = main(
        [
            "qualification-state-bridge",
            RETAINED_LIBRARY_V26_TO_V32,
            "--database",
            "source.sqlite3",
            "--backup",
            "backup.sqlite3",
            "--output",
            "current.sqlite3",
        ],
        qualification_state_bridge_runner=lambda *_: {
            "plan": RETAINED_LIBRARY_V26_TO_V32,
            "status": "bridged",
            "reports": 1,
            "selectively_invalidated": 0,
        },
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert json.loads(stdout.getvalue())["status"] == "bridged"
    assert stderr.getvalue() == ""


def test_installed_command_distinguishes_recovery() -> None:
    stderr = io.StringIO()

    def require_recovery(*values: object) -> dict[str, str | int]:
        raise QualificationStateBridgeRecoveryRequired

    result = main(
        [
            "qualification-state-bridge",
            RETAINED_LIBRARY_V26_TO_V32,
            "--database",
            "source.sqlite3",
            "--backup",
            "backup.sqlite3",
            "--output",
            "current.sqlite3",
        ],
        qualification_state_bridge_runner=require_recovery,
        stderr=stderr,
    )

    assert result == 1
    assert stderr.getvalue() == "Qualification-state bridge requires recovery.\n"


def test_inventaire_nonartifact_cover_is_validated_by_historical_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.domain.cover_evidence import (
        CoverDesignation,
        CoverProvenance,
    )

    data = json.loads(_v26_document(nonartifact_cover=True))
    data["covers"] = [
        CoverSource(
            cover_source_id(CoverSourceKind.INVENTAIRE, "work-1"),
            CoverSourceKind.INVENTAIRE,
            CoverSourceState.UNAVAILABLE,
            provenance=CoverProvenance("inventaire", CoverDesignation.WORK, "work-1"),
        ).to_data()
    ]
    identity = {key: value for key, value in data.items() if key != "report_id"}
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    )
    source, backup, output = _paths(tmp_path)
    _create_source(source, document=canonical_json(data))
    _accept_synthetic_source(monkeypatch, source)
    before = source.read_bytes()
    bridge.bridge_qualification_state(
        source, backup, output, RETAINED_LIBRARY_V26_TO_V32
    )
    assert source.read_bytes() == before
    with sqlite3.connect(output) as connection:
        report = MetadataReport.from_json(
            connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
        )
    assert report.schema_version == "metadata-report-v38"
