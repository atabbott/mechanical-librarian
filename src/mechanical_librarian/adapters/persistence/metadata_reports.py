"""Clean-state, two-table SQLite metadata report repository."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from pathlib import Path

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import MetadataReport, REPORT_SCHEMA, ReportValueError


_REPORTS_SQL = (
    "CREATE TABLE metadata_reports ("
    "report_id TEXT PRIMARY KEY NOT NULL CHECK (report_id GLOB 'report_[0-9a-f]*' AND length(report_id) = 71), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "file_identity TEXT NOT NULL CHECK (length(file_identity) = 64 AND file_identity NOT GLOB '*[^0-9a-f]*'), "
    "content_digest TEXT NOT NULL CHECK (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*'), "
    "schema_version TEXT NOT NULL CHECK (schema_version = 'metadata-report-v1'), "
    "document_digest TEXT NOT NULL CHECK (length(document_digest) = 64 AND document_digest NOT GLOB '*[^0-9a-f]*'), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 262144), "
    "UNIQUE (connection_name, library_name, item_id, file_identity, document_digest))"
)
_CURRENT_SQL = (
    "CREATE TABLE current_metadata_reports ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, file_identity TEXT NOT NULL, "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "PRIMARY KEY (connection_name, library_name, item_id, file_identity))"
)
_NO_UPDATE_SQL = (
    "CREATE TRIGGER metadata_reports_no_update BEFORE UPDATE ON metadata_reports "
    "BEGIN SELECT RAISE(ABORT, 'metadata reports are append-only'); END"
)
_NO_DELETE_SQL = (
    "CREATE TRIGGER metadata_reports_no_delete BEFORE DELETE ON metadata_reports "
    "BEGIN SELECT RAISE(ABORT, 'metadata reports are append-only'); END"
)


class SQLiteMetadataReportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def publish(self, report: MetadataReport) -> str:
        if type(report) is not MetadataReport:
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
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                "SELECT connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report.report_id,),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document) VALUES(?,?,?,?,?,?,?,?,?)",
                    values,
                )
            elif existing != values[1:]:
                raise MetadataReportError("persisted report identity conflicts")
            self._connection.execute(
                "INSERT INTO current_metadata_reports(connection_name,library_name,item_id,file_identity,report_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id,file_identity) DO UPDATE SET report_id=excluded.report_id",
                (*values[1:5], report.report_id),
            )
            self._connection.commit()
            return self.get(report.report_id)
        except MetadataReportError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("report publication failed") from error

    def get(self, report_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("persisted report is unavailable")
            connection, library, item, file_identity, content_digest, schema, digest, document = row
            if type(document) is not str or schema != REPORT_SCHEMA or hashlib.sha256(document.encode("utf-8")).hexdigest() != digest:
                raise MetadataReportError("persisted report is invalid")
            report = MetadataReport.from_json(document)
            if (
                report.report_id != report_id
                or report.target.connection != connection
                or report.target.library != library
                or report.target.item != item
                or report.file.identity != file_identity
                or report.file.content_digest != content_digest
            ):
                raise MetadataReportError("persisted report envelope disagrees")
            return document
        except (sqlite3.Error, ReportValueError, UnicodeError) as error:
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("persisted report is invalid") from error

    def current(self, connection: str, library: str, item: str, file_identity: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
                (connection, library, item, file_identity),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted current report is invalid") from error
        if row is None or type(row[0]) is not str:
            raise MetadataReportError("persisted current report is unavailable")
        return self.get(row[0])

    def close(self) -> None:
        self._connection.close()


def open_repository(path: Path) -> SQLiteMetadataReportRepository:
    created = False
    connection: sqlite3.Connection | None = None
    try:
        if os.path.lexists(path):
            information = path.lstat()
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
        else:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(descriptor)
            created = True
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        if created:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_REPORTS_SQL)
            connection.execute(_CURRENT_SQL)
            connection.execute(_NO_UPDATE_SQL)
            connection.execute(_NO_DELETE_SQL)
            connection.commit()
        _validate_schema(connection)
        return SQLiteMetadataReportRepository(connection)
    except MetadataReportError:
        if connection is not None:
            connection.close()
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    except (OSError, sqlite3.Error) as error:
        if connection is not None:
            connection.close()
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise MetadataReportError("database is unavailable or incompatible") from error


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    expected_tables = sorted((("current_metadata_reports", _CURRENT_SQL), ("metadata_reports", _REPORTS_SQL)))
    expected_triggers = sorted((("metadata_reports_no_delete", _NO_DELETE_SQL), ("metadata_reports_no_update", _NO_UPDATE_SQL)))
    if tables != expected_tables or triggers != expected_triggers or connection.execute("PRAGMA foreign_key_check").fetchall() or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise MetadataReportError("database is unavailable or incompatible")


__all__ = ["SQLiteMetadataReportRepository", "open_repository"]
