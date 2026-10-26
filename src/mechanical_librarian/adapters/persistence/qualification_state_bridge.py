"""Closed offline reconstruction of retained maintainer qualification state.

The descriptor binding, verified backup, path recheck, failure classification,
and cleanup controls recover the implementation immediately before deletion
commit dd63d193df5bc9fcf5f372ff4c3c9fb1ac613a29. The one source-to-fresh-output
reconstruction below is the retained-v26 adaptation; the retired adoption plans
are deliberately not restored.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.binding import (
    _LinuxDatabaseBinding,
    _reject_database_sidecars,
    _require_named_identity,
    _require_owner_only_database_parent,
    _validate_connection_binding,
    _validate_delete_mode_header,
)
from mechanical_librarian.adapters.persistence.offline_backup import (
    _copy_verified_backup,
    _descriptor_digest,
    _expected_schema_objects_digest,
    _require_backup_current,
    _require_source_current,
    _schema_objects_digest,
)
from mechanical_librarian.adapters.persistence.schema import (
    _check_constraint_violation,
    _create_schema,
    _register_casefold,
    _validate_schema,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.qualification_state_bridge import (
    QUALIFICATION_STATE_BRIDGE_PLANS,
    RETAINED_LIBRARY_V26_TO_V32,
    QualificationStateBridgeRecoveryRequired,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    _cover_source_from_data,
    _identifier_evidence_from_data,
    _local_claim_from_data,
    _provider_from_data,
    _reject_number,
    _target_field_from_data,
    _unique_object,
    canonical_json,
    derive_field_results,
    derive_identifier_evidence,
)

_SUBJECT = "qualification-state"
_SOURCE_SCHEMA_OBJECTS_SHA256 = (
    "e6323cdec1412ab7158ffcde612a927d2ca5e859e7de412041a19af0d6ab1fd1"
)
_TARGET_SCHEMA_OBJECTS_SHA256 = (
    "5dc77f44c3450e9251af772207c06b4845ee2cca9566ec9341367bd26f4292c8"
)
_SOURCE_REPORT_SCHEMA = "metadata-report-v26"
_SOURCE_COMPARISON_VERSION = "closed-title-creator-publication-v4"
_SOURCE_FIELD_KINDS = (
    "title",
    "subtitle",
    "creator",
    "contributor",
    "series",
    "series_sequence",
    "isbn",
    "asin",
    "publication_date",
    "date_event",
    "contextual_credit",
    "publisher",
    "description",
    "language",
    "subject",
    "imprint",
    "genre",
    "tag",
)
_SOURCE_FIELD_SOURCES = {"epub", "target_metadata"}
_WORK_RESULT_STATUSES = {"candidates", "candidate_limit_exceeded"}
_BRIDGE_POPULATED_TABLES = {
    "current_metadata_report_lineages",
    "current_metadata_reports",
    "current_review_packages",
    "metadata_report_browse_authors",
    "metadata_report_browse_facts",
    "metadata_report_cover_references",
    "metadata_reports",
}


@dataclass(frozen=True, slots=True)
class _ReconstructedReport:
    source_report_id: str
    report: MetadataReport
    selectively_invalidated: bool


@dataclass(frozen=True, slots=True)
class _BridgeData:
    reports: tuple[MetadataReport, ...]
    current_files: tuple[tuple[str, str, str, str, str], ...]
    current_lineages: tuple[tuple[str, str, str, str], ...]
    selectively_invalidated: int


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise MetadataReportError("qualification-state source is incompatible")
    return value


def _list(value: object) -> list[object]:
    if type(value) is not list:
        raise MetadataReportError("qualification-state source is incompatible")
    return value


def _strings(value: object) -> tuple[str, ...]:
    values = _list(value)
    if any(type(item) is not str for item in values):
        raise MetadataReportError("qualification-state source is incompatible")
    return cast("tuple[str, ...]", tuple(values))


def _connect_read_only(
    path: Path,
    descriptor: int,
    binding: _LinuxDatabaseBinding | None,
) -> sqlite3.Connection:
    sqlite_path = binding.alias_path if binding is not None else path
    uri = f"file:{quote(str(sqlite_path.absolute()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _validate_connection_binding(connection, sqlite_path, descriptor)
        _register_casefold(connection)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=0")
        return connection
    except Exception:
        connection.close()
        raise


def _provider_requires_work_lineage(provider: dict[str, object]) -> bool:
    if (
        provider.get("query_kind") == "work"
        and provider.get("status") in _WORK_RESULT_STATUSES
    ):
        return True
    lanes = _list(provider.get("lanes"))
    return any(
        type(lane) is dict
        and lane.get("kind") == "work"
        and lane.get("status") in _WORK_RESULT_STATUSES
        for lane in lanes
    )


def _augment_provider(provider: dict[str, object]) -> None:
    provider["recording_lookup_candidates"] = []
    provider["dispatch_lineage"] = None
    provider["transformation_lineage"] = None
    for value in _list(provider["lanes"]):
        lane = _object(
            value,
            {
                "candidates",
                "identity",
                "input_references",
                "kind",
                "matched_family",
                "status",
            },
        )
        lane["diagnostics"] = []
        lane["dispatch_lineage"] = None
        lane["transformation_lineage"] = None
        lane["exhausted_dispatch"] = []
        lane["recording_lookup_hints"] = []
        lane["dispatch"] = None


def _project_source_field_results(
    claims: tuple[LocalClaim, ...],
) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for result in derive_field_results(claims):
        if result.kind.value not in _SOURCE_FIELD_KINDS:
            continue
        item = result.to_data()
        sources = item["sources"]
        if type(sources) is not list:
            raise MetadataReportError("qualification-state source is incompatible")
        item["sources"] = [
            value
            for value in sources
            if type(value) is dict and value.get("source") in _SOURCE_FIELD_SOURCES
        ]
        projected.append(item)
    return projected


def _reconstruct_report(document: str) -> _ReconstructedReport:
    try:
        source = json.loads(
            document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
        )
        root = _object(
            source,
            {
                "comparison_version",
                "covers",
                "field_results",
                "file",
                "identifier_evidence",
                "local",
                "providers",
                "report_id",
                "schema_version",
                "target",
            },
        )
        if canonical_json(root) != document:
            raise MetadataReportError("qualification-state source is incompatible")
        if (
            root["schema_version"] != _SOURCE_REPORT_SCHEMA
            or root["comparison_version"] != _SOURCE_COMPARISON_VERSION
            or type(root["report_id"]) is not str
        ):
            raise MetadataReportError("qualification-state source is incompatible")
        identity_data = dict(root)
        source_report_id = identity_data.pop("report_id")
        expected_source_report_id = (
            "report_"
            + hashlib.sha256(canonical_json(identity_data).encode("utf-8")).hexdigest()
        )
        if source_report_id != expected_source_report_id:
            raise MetadataReportError("qualification-state source is incompatible")
        target_data = _object(
            root["target"], {"connection", "fields", "item", "library"}
        )
        file_data = _object(
            root["file"],
            {"content_digest", "format", "identity", "selected_index"},
        )
        local = _object(root["local"], {"claims", "reasons", "status"})
        claims = tuple(_local_claim_from_data(item) for item in _list(local["claims"]))
        expected_local_status = (
            "candidate"
            if any(claim.basis == "opening_matter" for claim in claims)
            else "no_candidate"
        )
        if local["status"] != expected_local_status:
            raise MetadataReportError("qualification-state source is incompatible")
        if _project_source_field_results(claims) != root["field_results"]:
            raise MetadataReportError("qualification-state source is incompatible")
        provider_values = _list(root["providers"])
        invalidated_providers: set[str] = set()
        retained_provider_values: list[dict[str, object]] = []
        all_providers = []
        for value in provider_values:
            provider = _object(
                value,
                {
                    "accessed_at",
                    "authority",
                    "candidates",
                    "convergence",
                    "diagnostics",
                    "edition_enrichment_overflow",
                    "input_references",
                    "lanes",
                    "provider",
                    "query_kind",
                    "request_accounting",
                    "status",
                },
            )
            requires_lineage = _provider_requires_work_lineage(provider)
            _augment_provider(provider)
            parsed = _provider_from_data(provider)
            all_providers.append(parsed)
            if requires_lineage:
                invalidated_providers.add(parsed.provider)
            else:
                retained_provider_values.append(provider)
        if [
            value.to_data()
            for value in derive_identifier_evidence(claims, tuple(all_providers))
        ] != root["identifier_evidence"]:
            raise MetadataReportError("qualification-state source is incompatible")
        # Parsing the unchanged identifier envelope is deliberate even though
        # current derivation replaces it. The v26 field-result envelope has a
        # smaller source vocabulary, so its exact validation is the equality
        # against the current derivation projected above.
        for value in _list(root["identifier_evidence"]):
            _identifier_evidence_from_data(value)
        covers = []
        invalidated_artifact = False
        for value in _list(root["covers"]):
            cover_data = _object(
                value,
                {
                    "artifact",
                    "kind",
                    "provenance",
                    "source_id",
                    "state",
                    "timeout_scope",
                },
            )
            cover = _cover_source_from_data(cover_data)
            if (
                cover.provenance is not None
                and cover.provenance.provider in invalidated_providers
            ):
                continue
            if cover.artifact is not None:
                invalidated_artifact = True
                continue
            covers.append(cover)
        target = TargetFacts(
            target_data["connection"],  # type: ignore[arg-type]
            target_data["library"],  # type: ignore[arg-type]
            target_data["item"],  # type: ignore[arg-type]
            tuple(
                _target_field_from_data(item) for item in _list(target_data["fields"])
            ),
        )
        file = FileFacts(
            file_data["selected_index"],  # type: ignore[arg-type]
            file_data["identity"],  # type: ignore[arg-type]
            file_data["content_digest"],  # type: ignore[arg-type]
            file_data["format"],  # type: ignore[arg-type]
        )
        report = MetadataReport.create(
            target=target,
            file=file,
            local_claims=claims,
            local_reasons=_strings(local["reasons"]),
            providers=tuple(
                _provider_from_data(value) for value in retained_provider_values
            ),
            cover_sources=tuple(covers),
        )
        MetadataReport.from_json(report.to_json())
        return _ReconstructedReport(
            source_report_id,
            report,
            bool(invalidated_providers) or invalidated_artifact,
        )
    except MetadataReportError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
    ) as error:
        raise MetadataReportError(
            "qualification-state source is incompatible"
        ) from error


def _load_bridge_data(connection: sqlite3.Connection) -> _BridgeData:
    if _schema_objects_digest(connection) != _SOURCE_SCHEMA_OBJECTS_SHA256:
        raise MetadataReportError("qualification-state source is incompatible")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MetadataReportError("qualification-state source is incompatible")
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise MetadataReportError("qualification-state source is incompatible")
    # The source predates every schema this build defines, so its expressions
    # come from the objects the digest above just bound to the exact historical
    # text, read inside the same read transaction (#1401).
    if table := _check_constraint_violation(
        connection,
        connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall(),
    ):
        raise MetadataReportError(
            f"qualification-state source is incompatible: CHECK constraint failed in {table}"
        )
    reconstructed = []
    source_reports: dict[str, MetadataReport] = {}
    unique_reports: dict[str, MetadataReport] = {}
    for row in connection.execute(
        "SELECT report_id,connection_name,library_name,item_id,file_identity,"
        "content_digest,schema_version,document_digest,document "
        "FROM metadata_reports ORDER BY report_id"
    ):
        if len(row) != 9 or type(row[8]) is not str:
            raise MetadataReportError("qualification-state source is incompatible")
        item = _reconstruct_report(row[8])
        if (
            row[0] != item.source_report_id
            or row[1] != item.report.target.connection
            or row[2] != item.report.target.library
            or row[3] != item.report.target.item
            or row[4] != item.report.file.identity
            or row[5] != item.report.file.content_digest
            or row[6] != _SOURCE_REPORT_SCHEMA
            or row[7] != hashlib.sha256(row[8].encode("utf-8")).hexdigest()
        ):
            raise MetadataReportError("qualification-state source is incompatible")
        previous = unique_reports.get(item.report.report_id)
        if previous is not None and previous.to_json() != item.report.to_json():
            raise MetadataReportError("qualification-state reconstruction conflicts")
        unique_reports[item.report.report_id] = item.report
        source_reports[item.source_report_id] = item.report
        reconstructed.append(item)
    if not reconstructed:
        raise MetadataReportError("qualification-state source is incompatible")

    def translated_report(value: object) -> MetadataReport:
        if type(value) is not str or value not in source_reports:
            raise MetadataReportError("qualification-state source is incompatible")
        return source_reports[value]

    current_files = []
    for row in connection.execute(
        "SELECT connection_name,library_name,item_id,file_identity,report_id "
        "FROM current_metadata_reports "
        "ORDER BY connection_name,library_name,item_id,file_identity"
    ):
        if len(row) != 5 or any(type(value) is not str for value in row):
            raise MetadataReportError("qualification-state source is incompatible")
        report = translated_report(row[4])
        if tuple(row[:4]) != (
            report.target.connection,
            report.target.library,
            report.target.item,
            report.file.identity,
        ):
            raise MetadataReportError("qualification-state source is incompatible")
        current_files.append((*row[:4], report.report_id))
    current_lineages = []
    for row in connection.execute(
        "SELECT connection_name,library_name,item_id,report_id "
        "FROM current_metadata_report_lineages "
        "ORDER BY connection_name,library_name,item_id"
    ):
        if len(row) != 4 or any(type(value) is not str for value in row):
            raise MetadataReportError("qualification-state source is incompatible")
        report = translated_report(row[3])
        if tuple(row[:3]) != (
            report.target.connection,
            report.target.library,
            report.target.item,
        ):
            raise MetadataReportError("qualification-state source is incompatible")
        current_lineages.append((*row[:3], report.report_id))
    return _BridgeData(
        tuple(unique_reports[key] for key in sorted(unique_reports)),
        tuple(current_files),
        tuple(current_lineages),
        sum(item.selectively_invalidated for item in reconstructed),
    )


def _report_rows(
    reports: tuple[MetadataReport, ...],
) -> tuple[tuple[object, ...], ...]:
    rows = []
    for report in reports:
        document = report.to_json()
        rows.append(
            (
                report.report_id,
                report.target.connection,
                report.target.library,
                report.target.item,
                report.file.identity,
                report.file.content_digest,
                report.schema_version,
                hashlib.sha256(document.encode("utf-8")).hexdigest(),
                document,
            )
        )
    return tuple(rows)


def _cover_reference_rows(
    reports: tuple[MetadataReport, ...],
) -> tuple[tuple[str, str, str | None, str | None, str | None], ...]:
    rows = [
        (
            report.report_id,
            source.source_id,
            None if source.artifact is None else source.artifact.artifact_id,
            None if source.artifact is None else source.artifact.preview_digest,
            None if source.artifact is None else source.artifact.preview_variant,
        )
        for report in reports
        for source in report.cover_sources
    ]
    return tuple(sorted(rows, key=lambda row: (row[0], row[1])))


def _review_package_rows(
    current_lineages: tuple[tuple[str, str, str, str], ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (connection, library, item, None, report_id, None, None, "report_only")
        for connection, library, item, report_id in current_lineages
    )


def _insert_bridge_data(
    connection: sqlite3.Connection, bridge_data: _BridgeData
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.executemany(
            "INSERT INTO metadata_reports("
            "report_id,connection_name,library_name,item_id,file_identity,"
            "content_digest,schema_version,document_digest,document"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            _report_rows(bridge_data.reports),
        )
        connection.executemany(
            "INSERT INTO metadata_report_cover_references("
            "report_id,source_id,artifact_id,preview_digest,preview_variant"
            ") VALUES(?,?,?,?,?)",
            _cover_reference_rows(bridge_data.reports),
        )
        connection.executemany(
            "INSERT INTO current_metadata_reports("
            "connection_name,library_name,item_id,file_identity,report_id"
            ") VALUES(?,?,?,?,?)",
            bridge_data.current_files,
        )
        connection.executemany(
            "INSERT INTO current_metadata_report_lineages("
            "connection_name,library_name,item_id,report_id"
            ") VALUES(?,?,?,?)",
            bridge_data.current_lineages,
        )
        connection.executemany(
            "INSERT INTO current_review_packages("
            "connection_name,library_name,item_id,proposal_set_id,report_id,"
            "review_case_id,checkpoint,outcome"
            ") VALUES(?,?,?,?,?,?,?,?)",
            _review_package_rows(bridge_data.current_lineages),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _validate_output(path: Path, bridge_data: _BridgeData) -> tuple[int, int]:
    descriptor = -1
    connection: sqlite3.Connection | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise MetadataReportError("qualification-state output is incompatible")
        _validate_delete_mode_header(descriptor)
        _reject_database_sidecars(path)
        uri = f"file:{quote(str(path.absolute()), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor)
        _register_casefold(connection)
        _validate_schema(connection, read_only=True)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MetadataReportError("qualification-state output is incompatible")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise MetadataReportError("qualification-state output is incompatible")
        report_rows = tuple(
            row
            for row in connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,"
                "content_digest,schema_version,document_digest,document "
                "FROM metadata_reports ORDER BY report_id"
            )
        )
        if report_rows != _report_rows(bridge_data.reports):
            raise MetadataReportError("qualification-state output is incompatible")
        for row in report_rows:
            MetadataReport.from_json(row[8])
        cover_references = tuple(
            connection.execute(
                "SELECT report_id,source_id,artifact_id,preview_digest,preview_variant "
                "FROM metadata_report_cover_references ORDER BY report_id,source_id"
            )
        )
        current_files = tuple(
            connection.execute(
                "SELECT connection_name,library_name,item_id,file_identity,report_id "
                "FROM current_metadata_reports "
                "ORDER BY connection_name,library_name,item_id,file_identity"
            )
        )
        current_lineages = tuple(
            connection.execute(
                "SELECT connection_name,library_name,item_id,report_id "
                "FROM current_metadata_report_lineages "
                "ORDER BY connection_name,library_name,item_id"
            )
        )
        review_packages = tuple(
            connection.execute(
                "SELECT connection_name,library_name,item_id,proposal_set_id,report_id,"
                "review_case_id,checkpoint,outcome FROM current_review_packages "
                "ORDER BY connection_name,library_name,item_id"
            )
        )
        if (
            cover_references != _cover_reference_rows(bridge_data.reports)
            or current_files != bridge_data.current_files
            or current_lineages != bridge_data.current_lineages
            or review_packages != _review_package_rows(bridge_data.current_lineages)
        ):
            raise MetadataReportError("qualification-state output is incompatible")
        baseline = sqlite3.connect(":memory:", isolation_level=None)
        try:
            _create_schema(baseline)
            tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            )
            for table in tables:
                if table in _BRIDGE_POPULATED_TABLES:
                    continue
                observed = sorted(
                    connection.execute(f'SELECT * FROM "{table}"').fetchall(),
                    key=repr,
                )
                expected = sorted(
                    baseline.execute(f'SELECT * FROM "{table}"').fetchall(),
                    key=repr,
                )
                if observed != expected:
                    raise MetadataReportError(
                        "qualification-state output is incompatible"
                    )
        finally:
            baseline.close()
        return information.st_dev, information.st_ino
    except MetadataReportError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise MetadataReportError(
            "qualification-state output is incompatible"
        ) from error
    finally:
        if connection is not None:
            connection.close()
        if descriptor >= 0:
            os.close(descriptor)


def _publish_temp(
    parent_descriptor: int,
    temp_name: str,
    output_name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        os.link(
            temp_name,
            output_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        os.fsync(parent_descriptor)
        _require_named_identity(parent_descriptor, output_name, expected_identity)
        os.unlink(temp_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except BaseException as error:
        raise QualificationStateBridgeRecoveryRequired from error


def bridge_qualification_state(
    database_path: Path,
    backup_path: Path,
    output_path: Path,
    plan: str,
) -> dict[str, str | int]:
    """Build one exact-current qualification database from a verified clone."""
    if plan not in QUALIFICATION_STATE_BRIDGE_PLANS:
        raise MetadataReportError("qualification-state bridge plan is unsupported")
    if plan != RETAINED_LIBRARY_V26_TO_V32:
        raise MetadataReportError("qualification-state bridge plan is unsupported")
    if _expected_schema_objects_digest() != _TARGET_SCHEMA_OBJECTS_SHA256:
        raise MetadataReportError(
            "qualification-state bridge is unavailable for this build"
        )
    database = database_path.absolute()
    backup = backup_path.absolute()
    output = output_path.absolute()
    if (
        len({database, backup, output}) != 3
        or database.parent != backup.parent
        or database.parent != output.parent
    ):
        raise MetadataReportError("qualification-state bridge paths are invalid")
    temp = output.with_name(f".{output.name}.qualification-state-bridge.tmp")
    if temp in {database, backup, output}:
        raise MetadataReportError("qualification-state bridge paths are invalid")

    source_descriptor = -1
    parent_descriptor = -1
    output_descriptor = -1
    binding: _LinuxDatabaseBinding | None = None
    source_connection: sqlite3.Connection | None = None
    output_connection: sqlite3.Connection | None = None
    published = False
    temp_created = False
    backup_identity: tuple[int, int] | None = None
    try:
        source_descriptor = os.open(
            database,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        information = os.fstat(source_descriptor)
        source_identity = (information.st_dev, information.st_ino)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise MetadataReportError("qualification-state source is not owner-only")
        _validate_delete_mode_header(source_descriptor)
        if sys.platform.startswith("linux"):
            binding = _LinuxDatabaseBinding.create(database, source_descriptor)
            source_descriptor = -1
            bound_descriptor = binding.descriptor
            parent_descriptor = binding.directory_chain.open_current_parent()
        else:
            bound_descriptor = source_descriptor
            parent_descriptor = os.open(
                database.parent,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            _require_owner_only_database_parent(os.fstat(parent_descriptor))
            _reject_database_sidecars(database)
        source_connection = _connect_read_only(database, bound_descriptor, binding)
        source_connection.execute("BEGIN")
        bridge_data = _load_bridge_data(source_connection)
        source_digest = _descriptor_digest(bound_descriptor)
        _require_source_current(
            database, source_identity, parent_descriptor, binding, subject=_SUBJECT
        )

        backup_exists = os.path.lexists(backup)
        if backup_exists:
            backup_identity = _require_backup_current(
                parent_descriptor, backup.name, source_digest, subject=_SUBJECT
            )
        if os.path.lexists(output):
            if not backup_exists:
                raise QualificationStateBridgeRecoveryRequired
            output_identity = _validate_output(output, bridge_data)
            if os.path.lexists(temp):
                output_information = os.stat(output, follow_symlinks=False)
                temp_information = os.stat(temp, follow_symlinks=False)
                if (
                    output_information.st_dev,
                    output_information.st_ino,
                ) != (temp_information.st_dev, temp_information.st_ino):
                    raise QualificationStateBridgeRecoveryRequired
                published = True
                os.unlink(temp)
                os.fsync(parent_descriptor)
            if backup_identity is None:
                raise QualificationStateBridgeRecoveryRequired
            _require_backup_current(
                parent_descriptor,
                backup.name,
                source_digest,
                backup_identity,
                subject=_SUBJECT,
            )
            _require_source_current(
                database, source_identity, parent_descriptor, binding, subject=_SUBJECT
            )
            _require_named_identity(parent_descriptor, output.name, output_identity)
            return {
                "plan": plan,
                "status": "already-current",
                "reports": len(bridge_data.reports),
                "selectively_invalidated": bridge_data.selectively_invalidated,
            }
        if os.path.lexists(temp):
            if not backup_exists:
                raise QualificationStateBridgeRecoveryRequired
            temp_identity = _validate_output(temp, bridge_data)
            published = True
            _publish_temp(parent_descriptor, temp.name, output.name, temp_identity)
            output_identity = _validate_output(output, bridge_data)
            if output_identity != temp_identity:
                raise QualificationStateBridgeRecoveryRequired
            if backup_identity is None:
                raise QualificationStateBridgeRecoveryRequired
            _require_backup_current(
                parent_descriptor,
                backup.name,
                source_digest,
                backup_identity,
                subject=_SUBJECT,
            )
            _require_source_current(
                database, source_identity, parent_descriptor, binding, subject=_SUBJECT
            )
            _require_named_identity(parent_descriptor, output.name, temp_identity)
            return {
                "plan": plan,
                "status": "bridged",
                "reports": len(bridge_data.reports),
                "selectively_invalidated": bridge_data.selectively_invalidated,
            }
        if not backup_exists:
            backup_identity, _ = _copy_verified_backup(
                bound_descriptor,
                parent_descriptor,
                backup.name,
                subject=_SUBJECT,
                recovery=QualificationStateBridgeRecoveryRequired,
            )
        if backup_identity is None:
            raise MetadataReportError("qualification-state backup identity changed")
        _require_backup_current(
            parent_descriptor,
            backup.name,
            source_digest,
            backup_identity,
            subject=_SUBJECT,
        )
        _require_source_current(
            database, source_identity, parent_descriptor, binding, subject=_SUBJECT
        )

        output_descriptor = os.open(
            temp.name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        temp_created = True
        output_information = os.fstat(output_descriptor)
        output_identity = (
            output_information.st_dev,
            output_information.st_ino,
        )
        uri = f"file:{quote(str(temp.absolute()), safe='/')}?mode=rw"
        output_connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(output_connection, temp, output_descriptor)
        _register_casefold(output_connection)
        output_connection.execute("PRAGMA synchronous=FULL")
        output_connection.execute("PRAGMA busy_timeout=0")
        _create_schema(output_connection)
        _insert_bridge_data(output_connection, bridge_data)
        output_connection.close()
        output_connection = None
        os.fsync(output_descriptor)
        os.fsync(parent_descriptor)
        _validate_output(temp, bridge_data)
        _require_backup_current(
            parent_descriptor,
            backup.name,
            source_digest,
            backup_identity,
            subject=_SUBJECT,
        )
        _require_source_current(
            database, source_identity, parent_descriptor, binding, subject=_SUBJECT
        )
        published = True
        _publish_temp(parent_descriptor, temp.name, output.name, output_identity)
        temp_created = False
        validated_output_identity = _validate_output(output, bridge_data)
        if validated_output_identity != output_identity:
            raise QualificationStateBridgeRecoveryRequired
        _require_backup_current(
            parent_descriptor,
            backup.name,
            source_digest,
            backup_identity,
            subject=_SUBJECT,
        )
        _require_source_current(
            database, source_identity, parent_descriptor, binding, subject=_SUBJECT
        )
        _require_named_identity(parent_descriptor, output.name, output_identity)
        return {
            "plan": plan,
            "status": "bridged",
            "reports": len(bridge_data.reports),
            "selectively_invalidated": bridge_data.selectively_invalidated,
        }
    except QualificationStateBridgeRecoveryRequired:
        raise
    except BaseException as error:
        if published:
            raise QualificationStateBridgeRecoveryRequired from error
        if isinstance(error, MetadataReportError):
            raise
        if not isinstance(error, Exception):
            raise
        raise MetadataReportError("qualification-state bridge failed safely") from error
    finally:
        cleanup_failure: Exception | None = None
        if output_connection is not None:
            try:
                output_connection.close()
            except sqlite3.Error as error:
                cleanup_failure = error
        if source_connection is not None:
            try:
                source_connection.close()
            except sqlite3.Error as error:
                cleanup_failure = cleanup_failure or error
        if output_descriptor >= 0:
            try:
                os.close(output_descriptor)
            except OSError as error:
                cleanup_failure = cleanup_failure or error
        if temp_created and not published and parent_descriptor >= 0:
            try:
                os.unlink(temp.name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError as error:
                cleanup_failure = cleanup_failure or error
        if parent_descriptor >= 0:
            try:
                os.close(parent_descriptor)
            except OSError as error:
                cleanup_failure = cleanup_failure or error
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError as error:
                cleanup_failure = cleanup_failure or error
        elif source_descriptor >= 0:
            try:
                os.close(source_descriptor)
            except OSError as error:
                cleanup_failure = cleanup_failure or error
        if cleanup_failure is not None:
            if published:
                raise QualificationStateBridgeRecoveryRequired from cleanup_failure
            raise MetadataReportError(
                "qualification-state bridge cleanup failed"
            ) from cleanup_failure
