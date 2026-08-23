"""The metadata-report aggregate.

Owns the append-only `metadata_reports` table, the `current_metadata_reports`
and `current_metadata_report_lineages` pointers that name which report is the
current one for a target, and `metadata_report_cover_references`: the exact
cover sources one report referenced.

`publish` is the first extracted method whose transaction spans aggregates. It
writes the report tables and, when no continuation is supplied, the review
package pointer too. That one review write stays here until the review
aggregate is extracted; the transaction is this method's, so the write is
atomic with the report either way.

The review reads it needs are injected as one narrow typed callable, so this
module depends on a signature rather than importing a sibling aggregate.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.schema import _validate_report_row
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    ReportNotFoundError,
    ReportRepository,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)

_ReviewPackage = tuple[str | None, str, str | None, int | None, str]


class SQLiteReportRepository(ConnectionScoped):
    """Persist and read exact immutable metadata reports."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        current_review_package: Callable[[str, str, str], _ReviewPackage | None],
    ) -> None:
        super().__init__(connection, binding)
        self.current_review_package = current_review_package

    def publish(
        self,
        report: MetadataReport,
        currentness: Callable[[], bool],
        expected_current: str | None = None,
        after_publication: Callable[[ReportRepository, MetadataReport], None]
        | None = None,
        *,
        expected_package: tuple[str | None, str, str | None, int | None, str]
        | None = None,
        # The repository handed to `after_publication`. That callback receives
        # the bound repository and calls review-pipeline methods on it, so the
        # composing facade supplies itself rather than this aggregate.
        bound: ReportRepository,
    ) -> str:
        if type(report) is not MetadataReport or not callable(currentness):
            raise MetadataReportError("report publication failed")
        document = report.to_json()
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        values = (
            report.report_id,
            report.target.connection,
            report.target.library,
            report.target.item,
            report.file.identity,
            report.file.content_digest,
            report.schema_version,
            digest,
            document,
        )
        transaction = self._transaction("publish")
        try:
            if self._binding is not None:
                self._binding.require_current()
            if currentness() is not True:
                raise MetadataReportError("target or media changed")
            transaction.begin()
            if self._binding is not None:
                self._binding.require_current()
            if (
                expected_package is not None
                and self.current_review_package(
                    report.target.connection, report.target.library, report.target.item
                )
                != expected_package
            ):
                raise MetadataReportError("current review package changed")
            current = self._connection.execute(
                "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,r.file_identity,r.content_digest,r.schema_version,r.document_digest,r.document FROM current_metadata_reports c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.connection_name=? AND c.library_name=? AND c.item_id=? AND c.file_identity=?",
                values[1:5],
            ).fetchone()
            if current is not None:
                document = _validate_report_row(current)
                persisted = MetadataReport.from_json(document)
                if (
                    persisted.target.connection,
                    persisted.target.library,
                    persisted.target.item,
                    persisted.file.identity,
                ) != values[1:5]:
                    raise MetadataReportError(
                        "persisted current report scope disagrees"
                    )
                if (
                    persisted.report_id != report.report_id
                    and persisted.report_id != expected_current
                ):
                    raise MetadataReportError("current report changed")
            elif expected_current is not None:
                raise MetadataReportError("current report changed")
            existing = self._connection.execute(
                "SELECT connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report.report_id,),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document) VALUES(?,?,?,?,?,?,?,?,?)",
                    values,
                )
                for source in report.cover_sources:
                    self._connection.execute(
                        "INSERT INTO metadata_report_cover_references(report_id,source_id,artifact_id,preview_digest,preview_variant) VALUES(?,?,?,?,?)",
                        (
                            report.report_id,
                            source.source_id,
                            None
                            if source.artifact is None
                            else source.artifact.artifact_id,
                            None
                            if source.artifact is None
                            else source.artifact.preview_digest,
                            None
                            if source.artifact is None
                            else source.artifact.preview_variant,
                        ),
                    )
            else:
                if existing != values[1:]:
                    raise MetadataReportError("persisted report identity conflicts")
                expected_references = tuple(
                    (
                        source.source_id,
                        None
                        if source.artifact is None
                        else source.artifact.artifact_id,
                        None
                        if source.artifact is None
                        else source.artifact.preview_digest,
                        None
                        if source.artifact is None
                        else source.artifact.preview_variant,
                    )
                    for source in report.cover_sources
                )
                actual_references = tuple(
                    self._connection.execute(
                        "SELECT source_id,artifact_id,preview_digest,preview_variant FROM metadata_report_cover_references WHERE report_id=? ORDER BY source_id",
                        (report.report_id,),
                    )
                )
                if tuple(sorted(expected_references)) != actual_references:
                    raise MetadataReportError(
                        "persisted report cover references conflict"
                    )
            self._connection.execute(
                "INSERT INTO current_metadata_reports(connection_name,library_name,item_id,file_identity,report_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id,file_identity) DO UPDATE SET report_id=excluded.report_id",
                (*values[1:5], report.report_id),
            )
            self._connection.execute(
                "INSERT INTO current_metadata_report_lineages(connection_name,library_name,item_id,report_id) VALUES(?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET report_id=excluded.report_id",
                (
                    report.target.connection,
                    report.target.library,
                    report.target.item,
                    report.report_id,
                ),
            )
            if after_publication is None:
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,NULL,?,NULL,NULL,'report_only') "
                    "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=NULL,report_id=excluded.report_id,review_case_id=NULL,checkpoint=NULL,outcome='report_only'",
                    (
                        report.target.connection,
                        report.target.library,
                        report.target.item,
                        report.report_id,
                    ),
                )
            if after_publication is not None:
                after_publication(bound, report)
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return self.get(report.report_id)
        except TargetConnectionError:
            transaction.rollback()
            raise
        except MetadataReportError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("report publication failed") from error
        except Exception as error:
            transaction.rollback()
            raise MetadataReportError(
                "report publication precondition failed"
            ) from error

    def get(self, report_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("persisted report is unavailable")
            document = _validate_report_row(row)
            return document
        except (sqlite3.Error, ReportValueError, UnicodeError) as error:
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("persisted report is invalid") from error

    def get_proposal_input(self, report_id: str) -> str:
        """Read proposal input without collapsing absence into a store fault."""
        try:
            row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted report is unavailable") from error
        if row is None:
            raise ReportNotFoundError("persisted report is unavailable")
        try:
            return _validate_report_row(row)
        except MetadataReportError as error:
            raise ReportValueError("persisted report is invalid") from error

    def current_optional(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT c.report_id,r.schema_version,r.connection_name,r.library_name,r.item_id,r.file_identity FROM current_metadata_reports c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.connection_name=? AND c.library_name=? AND c.item_id=? AND c.file_identity=?",
                (connection, library, item, file_identity),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted current report is invalid") from error
        if row is None:
            return None
        if type(row[0]) is not str:
            raise MetadataReportError("persisted current report is invalid")
        document = self.get(row[0])
        try:
            report = MetadataReport.from_json(document)
        except ReportValueError as error:
            raise MetadataReportError("persisted current report is invalid") from error
        if (
            report.target.connection != connection
            or report.target.library != library
            or report.target.item != item
            or report.file.identity != file_identity
        ):
            raise MetadataReportError("persisted current report scope disagrees")
        return document

    def preview_digest(
        self, report_id: str, artifact_id: str, preview_variant: str
    ) -> str:
        """Return only a preview explicitly referenced by one exact report."""
        if (
            type(report_id) is not str
            or type(artifact_id) is not str
            or preview_variant != "preview-320x480-rgba-png-v1"
        ):
            raise MetadataReportError("persisted cover is unavailable")
        try:
            row = self._connection.execute(
                "SELECT c.preview_digest,r.document FROM metadata_report_cover_references c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.report_id=? AND c.artifact_id=? AND c.preview_variant=?",
                (report_id, artifact_id, preview_variant),
            ).fetchone()
            if (
                row is None
                or len(row) != 2
                or type(row[0]) is not str
                or type(row[1]) is not str
            ):
                raise MetadataReportError("persisted cover is unavailable")
            report = MetadataReport.from_json(self.get(report_id))
            if not any(
                source.artifact is not None
                and source.artifact.artifact_id == artifact_id
                and source.artifact.preview_variant == preview_variant
                and source.artifact.preview_digest == row[0]
                for source in report.cover_sources
            ):
                raise MetadataReportError("persisted cover is unavailable")
            return row[0]
        except MetadataReportError:
            raise
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover is unavailable") from error

    def preview_digest_for_artifact(
        self, artifact_id: str, preview_variant: str
    ) -> str:
        """Resolve the one immutable preview identity shared by report references."""
        try:
            rows = self._connection.execute(
                "SELECT DISTINCT preview_digest FROM metadata_report_cover_references WHERE artifact_id=? AND preview_variant=?",
                (artifact_id, preview_variant),
            ).fetchall()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover is unavailable") from error
        if len(rows) != 1 or type(rows[0][0]) is not str:
            raise MetadataReportError("persisted cover is unavailable")
        return rows[0][0]

    def artifact_is_referenced(self, digest: str) -> bool:
        """Check one cleanup candidate without semantically auditing history."""
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
        ):
            raise MetadataReportError("persisted cover references are invalid")
        try:
            row = self._connection.execute(
                "SELECT 1 FROM metadata_report_cover_references WHERE artifact_id=? OR preview_digest=? LIMIT 1",
                ("cover_" + digest, digest),
            ).fetchone()
            if row is not None:
                return True
            documents = tuple(
                self._connection.execute(
                    "SELECT report_id,document FROM metadata_reports WHERE document LIKE ? ORDER BY report_id",
                    ("%" + digest + "%",),
                )
            )
        except sqlite3.Error as error:
            raise MetadataReportError(
                "persisted cover references are invalid"
            ) from error
        for report_id, document in documents:
            if type(report_id) is not str or type(document) is not str:
                raise MetadataReportError("persisted cover references are invalid")
            try:
                report = MetadataReport.from_json(document)
            except (ReportValueError, TypeError) as error:
                raise MetadataReportError(
                    "persisted cover references are invalid"
                ) from error
            if report.report_id != report_id:
                raise MetadataReportError("persisted cover references are invalid")
            if any(
                source.artifact is not None
                and digest
                in {
                    source.artifact.original_digest,
                    source.artifact.preview_digest,
                }
                for source in report.cover_sources
            ):
                raise MetadataReportError("persisted cover references are invalid")
        return False


__all__ = ["SQLiteReportRepository"]
