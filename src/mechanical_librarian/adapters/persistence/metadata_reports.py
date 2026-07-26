"""Clean-state SQLite repository for immutable reports and ProposalSets."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    REPORT_SCHEMA,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError
from mechanical_librarian.domain.review_case import ReviewAdmissionResult, ReviewCase, ReviewCaseValueError, is_actionable

if sys.platform.startswith("linux"):
    import fcntl
else:
    fcntl = None


_REPORTS_SQL = (
    "CREATE TABLE metadata_reports ("
    "report_id TEXT PRIMARY KEY NOT NULL CHECK (report_id GLOB 'report_[0-9a-f]*' AND length(report_id) = 71), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "file_identity TEXT NOT NULL CHECK (length(file_identity) = 64 AND file_identity NOT GLOB '*[^0-9a-f]*'), "
    "content_digest TEXT NOT NULL CHECK (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*'), "
    "schema_version TEXT NOT NULL CHECK (schema_version = 'metadata-report-v8'), "
    "document_digest TEXT NOT NULL CHECK (length(document_digest) = 64 AND document_digest NOT GLOB '*[^0-9a-f]*'), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152), "
    "UNIQUE (connection_name, library_name, item_id, file_identity, document_digest))"
)
_CURRENT_SQL = (
    "CREATE TABLE current_metadata_reports ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, file_identity TEXT NOT NULL, "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "PRIMARY KEY (connection_name, library_name, item_id, file_identity))"
)
_CURRENT_LINEAGE_SQL = (
    "CREATE TABLE current_metadata_report_lineages ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "PRIMARY KEY (connection_name, library_name, item_id))"
)
_COVER_REFERENCES_SQL = (
    "CREATE TABLE metadata_report_cover_references ("
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "source_id TEXT NOT NULL, artifact_id TEXT, preview_digest TEXT, preview_variant TEXT, "
    "PRIMARY KEY (report_id, source_id), "
    "CHECK ((artifact_id IS NULL AND preview_digest IS NULL AND preview_variant IS NULL) OR "
    "(length(artifact_id) = 70 AND artifact_id GLOB 'cover_[0-9a-f]*' "
    "AND length(preview_digest) = 64 AND preview_digest NOT GLOB '*[^0-9a-f]*' AND preview_variant = 'preview-320x480-rgba-png-v1')))"
)
_COVER_ARTIFACT_INDEX_SQL = (
    "CREATE INDEX metadata_report_cover_references_artifact_id "
    "ON metadata_report_cover_references(artifact_id) WHERE artifact_id IS NOT NULL"
)
_COVER_PREVIEW_INDEX_SQL = (
    "CREATE INDEX metadata_report_cover_references_preview_digest "
    "ON metadata_report_cover_references(preview_digest) WHERE preview_digest IS NOT NULL"
)
_PROPOSAL_SETS_SQL = (
    "CREATE TABLE proposal_sets ("
    "proposal_set_id TEXT PRIMARY KEY NOT NULL CHECK (proposal_set_id GLOB 'proposal_set_[0-9a-f]*' AND length(proposal_set_id) = 77), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152))"
)
_REVIEW_CASES_SQL = (
    "CREATE TABLE review_cases ("
    "review_case_id TEXT PRIMARY KEY NOT NULL CHECK (review_case_id GLOB 'review_case_[0-9a-f]*' AND length(review_case_id)=76), "
    "proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "document TEXT NOT NULL CHECK (typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152))"
)
_REVIEW_POINTERS_SQL = (
    "CREATE TABLE current_review_case_lineages ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "transition_id TEXT NOT NULL REFERENCES review_case_transitions(transition_id), checkpoint INTEGER NOT NULL CHECK(checkpoint > 0), outcome TEXT NOT NULL CHECK(outcome IN ('admitted','superseded','no_case','retired')), "
    "PRIMARY KEY (connection_name,library_name,item_id))"
)
_REVIEW_TRANSITIONS_SQL = (
    "CREATE TABLE review_case_transitions ("
    "transition_id TEXT PRIMARY KEY NOT NULL CHECK(transition_id GLOB 'review_transition_[0-9a-f]*' AND length(transition_id)=82), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, checkpoint INTEGER NOT NULL CHECK(checkpoint > 0), actor TEXT NOT NULL CHECK(actor='review-admission-v1'), "
    "from_review_case_id TEXT REFERENCES review_cases(review_case_id), to_review_case_id TEXT REFERENCES review_cases(review_case_id), proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), kind TEXT NOT NULL CHECK(kind IN ('admitted','superseded','no_case','retired')), reason TEXT NOT NULL CHECK(reason IN ('actionable','nonactionable')), UNIQUE(connection_name,library_name,item_id,checkpoint) )"
)
_NO_UPDATE_SQL = (
    "CREATE TRIGGER metadata_reports_no_update BEFORE UPDATE ON metadata_reports "
    "BEGIN SELECT RAISE(ABORT, 'metadata reports are append-only'); END"
)
_NO_DELETE_SQL = (
    "CREATE TRIGGER metadata_reports_no_delete BEFORE DELETE ON metadata_reports "
    "BEGIN SELECT RAISE(ABORT, 'metadata reports are append-only'); END"
)
_COVER_NO_UPDATE_SQL = (
    "CREATE TRIGGER metadata_report_cover_references_no_update BEFORE UPDATE ON metadata_report_cover_references "
    "BEGIN SELECT RAISE(ABORT, 'metadata report cover references are append-only'); END"
)
_COVER_NO_DELETE_SQL = (
    "CREATE TRIGGER metadata_report_cover_references_no_delete BEFORE DELETE ON metadata_report_cover_references "
    "BEGIN SELECT RAISE(ABORT, 'metadata report cover references are append-only'); END"
)
_PROPOSAL_NO_UPDATE_SQL = (
    "CREATE TRIGGER proposal_sets_no_update BEFORE UPDATE ON proposal_sets "
    "BEGIN SELECT RAISE(ABORT, 'proposal sets are append-only'); END"
)
_PROPOSAL_NO_DELETE_SQL = (
    "CREATE TRIGGER proposal_sets_no_delete BEFORE DELETE ON proposal_sets "
    "BEGIN SELECT RAISE(ABORT, 'proposal sets are append-only'); END"
)
_REVIEW_NO_UPDATE_SQL = "CREATE TRIGGER review_cases_no_update BEFORE UPDATE ON review_cases BEGIN SELECT RAISE(ABORT, 'review cases are append-only'); END"
_REVIEW_NO_DELETE_SQL = "CREATE TRIGGER review_cases_no_delete BEFORE DELETE ON review_cases BEGIN SELECT RAISE(ABORT, 'review cases are append-only'); END"
_REVIEW_TRANSITION_NO_UPDATE_SQL = "CREATE TRIGGER review_case_transitions_no_update BEFORE UPDATE ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'review transitions are append-only'); END"
_REVIEW_TRANSITION_NO_DELETE_SQL = "CREATE TRIGGER review_case_transitions_no_delete BEFORE DELETE ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'review transitions are append-only'); END"


class _LinuxDirectoryChain:
    def __init__(
        self,
        names: tuple[str, ...],
        descriptors: tuple[int, ...],
        identities: tuple[tuple[int, int], ...],
    ) -> None:
        self.names = names
        self.descriptors = descriptors
        self.identities = identities
        self.closed = False

    @classmethod
    def open(cls, path: Path) -> "_LinuxDirectoryChain":
        absolute = path.absolute()
        if not absolute.is_absolute():
            raise MetadataReportError("database parent path is invalid")
        names = tuple(absolute.parts[1:])
        if any(name in ("", ".", "..") for name in names):
            raise MetadataReportError("database parent path is invalid")
        descriptors: list[int] = []
        identities: list[tuple[int, int]] = []
        try:
            descriptor = os.open(
                "/",
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            descriptors.append(descriptor)
            information = os.fstat(descriptor)
            identities.append((information.st_dev, information.st_ino))
            for name in names:
                descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptors[-1],
                )
                descriptors.append(descriptor)
                information = os.fstat(descriptor)
                if not stat.S_ISDIR(information.st_mode):
                    raise MetadataReportError(
                        "database parent path is invalid"
                    )
                _require_protected_directory_entry(
                    os.fstat(descriptors[-2]), information
                )
                identities.append((information.st_dev, information.st_ino))
            return cls(names, tuple(descriptors), tuple(identities))
        except MetadataReportError:
            raise
        except (OSError, ValueError) as error:
            raise MetadataReportError(
                "database parent path is unavailable"
            ) from error
        finally:
            if len(identities) != len(descriptors):
                for descriptor in reversed(descriptors):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    @property
    def parent_descriptor(self) -> int:
        return self.descriptors[-1]

    def open_current_parent(self) -> int:
        if self.closed:
            raise MetadataReportError(
                "database parent path is unavailable"
            )
        current = -1
        try:
            current = os.open(
                "/",
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            root = os.fstat(current)
            if (root.st_dev, root.st_ino) != self.identities[0]:
                raise MetadataReportError("database parent path changed")
            for index, name in enumerate(self.names):
                descriptor = -1
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current,
                    )
                    information = os.fstat(descriptor)
                    if (
                        not stat.S_ISDIR(information.st_mode)
                        or (information.st_dev, information.st_ino)
                        != self.identities[index + 1]
                    ):
                        raise MetadataReportError(
                            "database parent path changed"
                        )
                    _require_protected_directory_entry(
                        os.fstat(current), information
                    )
                    os.close(current)
                    current = descriptor
                    descriptor = -1
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
            result = current
            current = -1
            return result
        except MetadataReportError:
            raise
        except OSError as error:
            raise MetadataReportError(
                "database parent path changed"
            ) from error
        finally:
            if current >= 0:
                os.close(current)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: OSError | None = None
        for descriptor in reversed(self.descriptors):
            try:
                os.close(descriptor)
            except OSError as error:
                failure = failure or error
        if failure is not None:
            raise MetadataReportError(
                "database parent binding cleanup failed"
            ) from failure


class _LinuxDatabaseBinding:
    def __init__(
        self,
        configured_name: str,
        descriptor: int,
        directory_chain: _LinuxDirectoryChain,
        alias_name: str,
        expected_identity: tuple[int, int],
    ) -> None:
        self.configured_name = configured_name
        self.descriptor = descriptor
        self.directory_chain = directory_chain
        self.parent_descriptor = directory_chain.parent_descriptor
        self.alias_name = alias_name
        self.expected_identity = expected_identity
        self.alias_path = Path(
            f"/proc/self/fd/{self.parent_descriptor}/{alias_name}"
        )
        self.closed = False

    @classmethod
    def create(cls, path: Path, descriptor: int) -> "_LinuxDatabaseBinding":
        if fcntl is None:
            raise MetadataReportError("database private binding is unavailable")
        absolute = path.absolute()
        if not absolute.name:
            raise MetadataReportError("database path is invalid")
        directory_chain: _LinuxDirectoryChain | None = None
        alias_name: str | None = None
        locked = False
        succeeded = False
        try:
            directory_chain = _LinuxDirectoryChain.open(absolute.parent)
            parent_descriptor = directory_chain.parent_descriptor
            _require_owner_only_database_parent(
                os.fstat(parent_descriptor)
            )
            fcntl.flock(parent_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            expected = os.fstat(descriptor)
            expected_identity = (expected.st_dev, expected.st_ino)
            _require_named_identity(
                parent_descriptor, absolute.name, expected_identity
            )
            _reject_database_sidecars_in_directory(
                parent_descriptor, absolute.name
            )
            for _ in range(8):
                candidate = (
                    f".metadata-report-binding-{secrets.token_hex(16)}.sqlite"
                )
                try:
                    os.link(
                        absolute.name,
                        candidate,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                alias_name = candidate
                break
            if alias_name is None:
                raise MetadataReportError(
                    "database private binding is unavailable"
                )
            _require_named_identity(
                parent_descriptor, alias_name, expected_identity
            )
            binding = cls(
                absolute.name,
                descriptor,
                directory_chain,
                alias_name,
                expected_identity,
            )
            succeeded = True
            return binding
        except MetadataReportError:
            raise
        except (OSError, ValueError) as error:
            raise MetadataReportError(
                "database private binding is unavailable"
            ) from error
        finally:
            if (
                alias_name is not None
                and directory_chain is not None
                and not succeeded
            ):
                try:
                    os.unlink(
                        alias_name,
                        dir_fd=directory_chain.parent_descriptor,
                    )
                except OSError:
                    pass
            if not succeeded:
                if locked and directory_chain is not None:
                    try:
                        fcntl.flock(
                            directory_chain.parent_descriptor,
                            fcntl.LOCK_UN,
                        )
                    except OSError:
                        pass
                if directory_chain is not None:
                    directory_chain.close()

    def require_current(self) -> None:
        current_parent = self.directory_chain.open_current_parent()
        try:
            _require_owner_only_database_parent(
                os.fstat(current_parent)
            )
            _require_named_identity(
                current_parent,
                self.configured_name,
                self.expected_identity,
            )
            _reject_database_sidecars_in_directory(
                current_parent, self.configured_name
            )
        finally:
            os.close(current_parent)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: OSError | None = None
        for suffix in ("-journal", "-wal", "-shm"):
            name = f"{self.alias_name}{suffix}"
            try:
                information = os.stat(
                    name,
                    dir_fd=self.parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except OSError as error:
                failure = failure or error
                continue
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
            ):
                failure = failure or OSError("unsafe private database artifact")
                continue
            try:
                os.unlink(name, dir_fd=self.parent_descriptor)
            except OSError as error:
                failure = failure or error
        try:
            _require_named_identity(
                self.parent_descriptor,
                self.alias_name,
                self.expected_identity,
            )
            os.unlink(self.alias_name, dir_fd=self.parent_descriptor)
        except (OSError, MetadataReportError) as error:
            failure = failure or OSError(str(error))
        try:
            os.close(self.descriptor)
        except OSError as error:
            failure = failure or error
        try:
            fcntl.flock(self.parent_descriptor, fcntl.LOCK_UN)
        except OSError as error:
            failure = failure or error
        try:
            self.directory_chain.close()
        except MetadataReportError as error:
            failure = failure or OSError(str(error))
        if failure is not None:
            raise MetadataReportError(
                "database private binding cleanup failed"
            ) from failure


class SQLiteMetadataReportRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        identity: tuple[int, int] | None = None,
    ) -> None:
        self._connection = connection
        self._binding = binding
        self.identity = identity

    def publish(self, report: MetadataReport, currentness: Callable[[], bool], expected_current: str | None = None) -> str:
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
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            if currentness() is not True:
                raise MetadataReportError("target or media changed")
            current = self._connection.execute(
                "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,r.file_identity,r.content_digest,r.schema_version,r.document_digest,r.document FROM current_metadata_reports c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.connection_name=? AND c.library_name=? AND c.item_id=? AND c.file_identity=?",
                values[1:5],
            ).fetchone()
            if current is not None:
                document = _validate_report_row(current)
                persisted = MetadataReport.from_json(document)
                if (persisted.target.connection, persisted.target.library, persisted.target.item, persisted.file.identity) != values[1:5]:
                    raise MetadataReportError("persisted current report scope disagrees")
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
                            None if source.artifact is None else source.artifact.artifact_id,
                            None if source.artifact is None else source.artifact.preview_digest,
                            None if source.artifact is None else source.artifact.preview_variant,
                        ),
                    )
            else:
                if existing != values[1:]:
                    raise MetadataReportError("persisted report identity conflicts")
                expected_references = tuple(
                    (source.source_id, None if source.artifact is None else source.artifact.artifact_id, None if source.artifact is None else source.artifact.preview_digest, None if source.artifact is None else source.artifact.preview_variant)
                    for source in report.cover_sources
                )
                actual_references = tuple(self._connection.execute(
                    "SELECT source_id,artifact_id,preview_digest,preview_variant FROM metadata_report_cover_references WHERE report_id=? ORDER BY source_id",
                    (report.report_id,),
                ))
                if tuple(sorted(expected_references)) != actual_references:
                    raise MetadataReportError("persisted report cover references conflict")
            self._connection.execute(
                "INSERT INTO current_metadata_reports(connection_name,library_name,item_id,file_identity,report_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id,file_identity) DO UPDATE SET report_id=excluded.report_id",
                (*values[1:5], report.report_id),
            )
            self._connection.execute(
                "INSERT INTO current_metadata_report_lineages(connection_name,library_name,item_id,report_id) VALUES(?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET report_id=excluded.report_id",
                (report.target.connection, report.target.library, report.target.item, report.report_id),
            )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return self.get(report.report_id)
        except MetadataReportError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("report publication failed") from error
        except Exception as error:
            self._connection.rollback()
            raise MetadataReportError("report publication precondition failed") from error

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

    def publish_proposal(self, proposal_set: ProposalSet) -> str:
        """Insert one exact ProposalSet after transaction-local report recheck."""
        if type(proposal_set) is not ProposalSet:
            raise MetadataReportError("proposal publication failed")
        document = proposal_set.to_json()
        values = (
            proposal_set.proposal_set_id,
            proposal_set.report_id,
            document,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            existing = self._connection.execute(
                "SELECT report_id,document "
                "FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set.proposal_set_id,),
            ).fetchone()
            expected = values[1:]
            if existing is not None and existing != expected:
                raise MetadataReportError("persisted proposal identity conflicts")
            row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document "
                "FROM metadata_reports WHERE report_id=?",
                (proposal_set.report_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("proposal report is unavailable")
            report_document = _validate_report_row(row)
            report = MetadataReport.from_json(report_document)
            current = self._connection.execute(
                "SELECT report_id FROM current_metadata_reports "
                "WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
                (
                    report.target.connection,
                    report.target.library,
                    report.target.item,
                    report.file.identity,
                ),
            ).fetchone()
            if (
                report.report_id != proposal_set.report_id
                or report.target.fingerprint != proposal_set.target_fingerprint
                or current != (proposal_set.report_id,)
            ):
                raise MetadataReportError("proposal report changed")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO proposal_sets(proposal_set_id,report_id,document) VALUES(?,?,?)",
                    values,
                )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return self.get_proposal(proposal_set.proposal_set_id)
        except MetadataReportError:
            self._connection.rollback()
            raise
        except (sqlite3.Error, ProposalValueError, ReportValueError) as error:
            self._connection.rollback()
            raise MetadataReportError("proposal publication failed") from error
        except Exception as error:
            self._connection.rollback()
            raise MetadataReportError(
                "proposal publication precondition failed"
            ) from error

    def get_proposal(self, proposal_set_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT proposal_set_id,report_id,document "
                "FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("persisted proposal is unavailable")
            return _validate_proposal_row(row)
        except MetadataReportError:
            raise
        except (sqlite3.Error, ProposalValueError, UnicodeError) as error:
            raise MetadataReportError("persisted proposal is invalid") from error

    def admit_review_case(self, proposal_set: ProposalSet) -> str:
        """Atomically bind one exact current ProposalSet to its target lineage."""
        if type(proposal_set) is not ProposalSet:
            raise MetadataReportError("review admission failed")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            proposal_row = self._connection.execute("SELECT proposal_set_id,report_id,document FROM proposal_sets WHERE proposal_set_id=?", (proposal_set.proposal_set_id,)).fetchone()
            if proposal_row is None or _validate_proposal_row(proposal_row) != proposal_set.to_json():
                raise MetadataReportError("review proposal is unavailable")
            report_row = self._connection.execute("SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?", (proposal_set.report_id,)).fetchone()
            if report_row is None:
                raise MetadataReportError("review report is unavailable")
            report = MetadataReport.from_json(_validate_report_row(report_row))
            lineage = (report.target.connection, report.target.library, report.target.item)
            if self._connection.execute("SELECT report_id FROM current_metadata_report_lineages WHERE connection_name=? AND library_name=? AND item_id=?", lineage).fetchone() != (report.report_id,):
                raise MetadataReportError("review proposal is stale")
            if self._connection.execute("SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?", (*lineage, report.file.identity)).fetchone() != (report.report_id,):
                raise MetadataReportError("review proposal is stale")
            prior = self._connection.execute("SELECT proposal_set_id,report_id,review_case_id FROM current_review_case_lineages WHERE connection_name=? AND library_name=? AND item_id=?", lineage).fetchone()
            if prior is not None and prior[0] == proposal_set.proposal_set_id:
                transition = self._connection.execute("SELECT kind FROM review_case_transitions WHERE connection_name=? AND library_name=? AND item_id=? AND proposal_set_id=? ORDER BY transition_id DESC LIMIT 1", (*lineage, proposal_set.proposal_set_id)).fetchone()
                if transition is None:
                    raise MetadataReportError("review transition is unavailable")
                if self._binding is not None:
                    self._binding.require_current()
                self._connection.commit()
                return _admission_json(transition[0], prior[2])
            if not is_actionable(proposal_set):
                self._connection.execute("INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id) VALUES(?,?,?,?,?,NULL) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=NULL", (*lineage, proposal_set.proposal_set_id, report.report_id))
                self._connection.execute("INSERT INTO review_case_transitions(connection_name,library_name,item_id,from_review_case_id,to_review_case_id,proposal_set_id,kind) VALUES(?,?,?,?,?,?,?)", (*lineage, prior[2] if prior else None, None, proposal_set.proposal_set_id, "retired" if prior is not None else "no_case"))
                if self._binding is not None:
                    self._binding.require_current()
                self._connection.commit()
                return _admission_json("retired" if prior is not None else "no_case", None)
            case = ReviewCase.create(proposal_set, report)
            existing = self._connection.execute("SELECT document FROM review_cases WHERE review_case_id=?", (case.review_case_id,)).fetchone()
            if existing is not None and existing != (case.to_json(),):
                raise MetadataReportError("review case identity conflicts")
            if existing is None:
                self._connection.execute("INSERT INTO review_cases(review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)", (case.review_case_id, case.proposal_set_id, case.report_id, case.to_json()))
            self._connection.execute("INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id) VALUES(?,?,?,?,?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id", (*lineage, proposal_set.proposal_set_id, report.report_id, case.review_case_id))
            self._connection.execute("INSERT INTO review_case_transitions(connection_name,library_name,item_id,from_review_case_id,to_review_case_id,proposal_set_id,kind) VALUES(?,?,?,?,?,?,?)", (*lineage, prior[2] if prior else None, case.review_case_id, proposal_set.proposal_set_id, "superseded" if prior else "admitted"))
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return _admission_json("superseded" if prior is not None else "admitted", case.review_case_id)
        except MetadataReportError:
            self._connection.rollback(); raise
        except (sqlite3.Error, ProposalValueError, ReportValueError, ReviewCaseValueError) as error:
            self._connection.rollback(); raise MetadataReportError("review admission failed") from error

    def get_review_case(self, review_case_id: str) -> str:
        try:
            row = self._connection.execute("SELECT review_case_id,proposal_set_id,report_id,document FROM review_cases WHERE review_case_id=?", (review_case_id,)).fetchone()
            if row is None or len(row) != 4:
                raise MetadataReportError("review case is unavailable")
            case = ReviewCase.from_json(row[3])
            if (case.review_case_id, case.proposal_set_id, case.report_id) != row[:3]:
                raise MetadataReportError("review case is invalid")
            proposal_row = self._connection.execute(
                "SELECT proposal_set_id,report_id,document FROM proposal_sets WHERE proposal_set_id=?",
                (case.proposal_set_id,),
            ).fetchone()
            report_row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (case.report_id,),
            ).fetchone()
            if proposal_row is None or report_row is None:
                raise MetadataReportError("review case source is unavailable")
            proposal = ProposalSet.from_json(_validate_proposal_row(proposal_row))
            report = MetadataReport.from_json(_validate_report_row(report_row))
            if (
                proposal.proposal_set_id != case.proposal_set_id
                or proposal.report_id != case.report_id
                or proposal.target_fingerprint != case.target_fingerprint
                or report.target.to_data() != case.target
                or report.target.fingerprint != case.target_fingerprint
                or report.file.identity != case.file_identity
                or tuple(value.to_data() for value in proposal.results) != case.results
            ):
                raise MetadataReportError("review case source disagrees")
            current = self._connection.execute("SELECT review_case_id FROM current_review_case_lineages WHERE connection_name=? AND library_name=? AND item_id=?", (case.target["connection"], case.target["library"], case.target["item"])).fetchone()
            if current is not None and current[0] is not None and (
                type(current[0]) is not str
                or not current[0].startswith("review_case_")
            ):
                raise MetadataReportError("review case pointer is invalid")
            source_current = self._connection.execute("SELECT report_id FROM current_metadata_report_lineages WHERE connection_name=? AND library_name=? AND item_id=?", (case.target["connection"], case.target["library"], case.target["item"])).fetchone()
            file_current = self._connection.execute("SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?", (case.target["connection"], case.target["library"], case.target["item"], case.file_identity)).fetchone()
            return _canonical_json({"case": case.to_data(), "current": current == (case.review_case_id,) and source_current == (case.report_id,) and file_current == (case.report_id,), "schema_version": "review-case-get-v1"})
        except (sqlite3.Error, ReviewCaseValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    def current(self, connection: str, library: str, item: str, file_identity: str) -> str:
        document = self.current_optional(connection, library, item, file_identity)
        if document is None:
            raise MetadataReportError("persisted current report is unavailable")
        return document

    def current_optional(self, connection: str, library: str, item: str, file_identity: str) -> str | None:
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

    def preview_digest(self, report_id: str, artifact_id: str, preview_variant: str) -> str:
        """Return only a preview explicitly referenced by one exact report."""
        if type(report_id) is not str or type(artifact_id) is not str or preview_variant != "preview-320x480-rgba-png-v1":
            raise MetadataReportError("persisted cover is unavailable")
        try:
            row = self._connection.execute(
                "SELECT c.preview_digest,r.document FROM metadata_report_cover_references c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.report_id=? AND c.artifact_id=? AND c.preview_variant=?",
                (report_id, artifact_id, preview_variant),
            ).fetchone()
            if row is None or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str:
                raise MetadataReportError("persisted cover is unavailable")
            report = MetadataReport.from_json(self.get(report_id))
            if not any(source.artifact is not None and source.artifact.artifact_id == artifact_id and source.artifact.preview_variant == preview_variant and source.artifact.preview_digest == row[0] for source in report.cover_sources):
                raise MetadataReportError("persisted cover is unavailable")
            return row[0]
        except MetadataReportError:
            raise
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover is unavailable") from error

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
            documents = tuple(self._connection.execute(
                "SELECT report_id,document FROM metadata_reports WHERE document LIKE ? ORDER BY report_id",
                ("%" + digest + "%",),
            ))
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover references are invalid") from error
        for report_id, document in documents:
            if type(report_id) is not str or type(document) is not str:
                raise MetadataReportError("persisted cover references are invalid")
            try:
                report = MetadataReport.from_json(document)
            except (ReportValueError, TypeError) as error:
                raise MetadataReportError("persisted cover references are invalid") from error
            if report.report_id != report_id:
                raise MetadataReportError("persisted cover references are invalid")
            if any(
                source.artifact is not None
                and digest in {
                    source.artifact.original_digest,
                    source.artifact.preview_digest,
                }
                for source in report.cover_sources
            ):
                raise MetadataReportError("persisted cover references are invalid")
        return False

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            if self._binding is not None:
                self._binding.close()


def open_repository(path: Path, *, create: bool = True, expected_identity: tuple[int, int] | None = None) -> SQLiteMetadataReportRepository:
    created = False
    descriptor = -1
    created_identity: tuple[int, int] | None = None
    connection: sqlite3.Connection | None = None
    binding: _LinuxDatabaseBinding | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise MetadataReportError("database no-follow access is unavailable")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
        if os.path.lexists(path):
            descriptor = os.open(path, flags)
            information = os.fstat(descriptor)
            if expected_identity is not None and (information.st_dev, information.st_ino) != expected_identity:
                raise MetadataReportError("database authority changed")
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            _validate_delete_mode_header(descriptor)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor)
                descriptor = -1
                _validate_existing_read_only(
                    binding.alias_path, binding.descriptor
                )
                binding.require_current()
            else:
                _reject_database_sidecars(path)
                _validate_existing_read_only(path, descriptor)
                _reject_database_sidecars(path)
        elif expected_identity is not None:
            raise MetadataReportError("database authority changed")
        elif create:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            created = True
            created_identity = (information.st_dev, information.st_ino)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor)
                descriptor = -1
        else:
            raise MetadataReportError("database is unavailable")
        database_path = binding.alias_path if binding is not None else path
        database_descriptor = (
            binding.descriptor if binding is not None else descriptor
        )
        connection = _connect_bound(
            database_path,
            database_descriptor,
            binding.require_current if binding is not None else None,
        )
        if binding is not None:
            binding.require_current()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        if created:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_REPORTS_SQL)
            connection.execute(_CURRENT_SQL)
            connection.execute(_CURRENT_LINEAGE_SQL)
            connection.execute(_COVER_REFERENCES_SQL)
            connection.execute(_COVER_ARTIFACT_INDEX_SQL)
            connection.execute(_COVER_PREVIEW_INDEX_SQL)
            connection.execute(_PROPOSAL_SETS_SQL)
            connection.execute(_REVIEW_CASES_SQL)
            connection.execute(_REVIEW_POINTERS_SQL)
            connection.execute(_REVIEW_TRANSITIONS_SQL)
            connection.execute(_NO_UPDATE_SQL)
            connection.execute(_COVER_NO_UPDATE_SQL)
            connection.execute(_COVER_NO_DELETE_SQL)
            connection.execute(_PROPOSAL_NO_UPDATE_SQL)
            connection.execute(_PROPOSAL_NO_DELETE_SQL)
            connection.execute(_REVIEW_NO_UPDATE_SQL)
            connection.execute(_REVIEW_NO_DELETE_SQL)
            connection.execute(_REVIEW_TRANSITION_NO_UPDATE_SQL)
            connection.execute(_REVIEW_TRANSITION_NO_DELETE_SQL)
            connection.execute(_NO_DELETE_SQL)
            connection.commit()
        _validate_schema(connection)
        if binding is None:
            os.close(descriptor)
            descriptor = -1
        repository = SQLiteMetadataReportRepository(connection, binding, (information.st_dev, information.st_ino))
        connection = None
        binding = None
        return repository
    except MetadataReportError:
        if connection is not None:
            connection.close()
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError:
                pass
        if created and created_identity is not None:
            _unlink_created(path, created_identity)
        raise
    except (OSError, sqlite3.Error) as error:
        if connection is not None:
            connection.close()
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError:
                pass
        if created and created_identity is not None:
            _unlink_created(path, created_identity)
        raise MetadataReportError("database is unavailable or incompatible") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_existing_read_only(path: Path, descriptor: int) -> None:
    absolute = str(path.absolute())
    uri = f"file:{quote(absolute, safe='/')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        before = _regular_descriptors(descriptor)
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor, before)
        connection.execute("PRAGMA schema_version").fetchone()
        _validate_schema(connection)
    finally:
        if connection is not None:
            connection.close()


def _connect_bound(
    path: Path,
    descriptor: int,
    preconnect: Callable[[], None] | None = None,
) -> sqlite3.Connection:
    absolute = str(path.absolute())
    uri = f"file:{quote(absolute, safe='/')}?mode=rw"
    before = _regular_descriptors(descriptor)
    _reject_database_sidecars(path)
    if preconnect is not None:
        preconnect()
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _validate_connection_binding(connection, path, descriptor, before)
        return connection
    except Exception:
        connection.close()
        raise


def _reject_database_sidecars(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MetadataReportError(
                "database recovery state is unavailable"
            ) from error
        raise MetadataReportError("database recovery state is unsupported")


def _reject_database_sidecars_in_directory(
    parent_descriptor: int, basename: str
) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            os.stat(
                f"{basename}{suffix}",
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MetadataReportError(
                "database recovery state is unavailable"
            ) from error
        raise MetadataReportError("database recovery state is unsupported")


def _require_named_identity(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        information = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise MetadataReportError(
            "database pathname changed while opening"
        ) from error
    if not stat.S_ISREG(information.st_mode) or (
        information.st_dev,
        information.st_ino,
    ) != expected_identity:
        raise MetadataReportError("database pathname changed while opening")


def _require_protected_directory_entry(
    parent: os.stat_result, entry: os.stat_result
) -> None:
    effective_user = os.geteuid()
    if parent.st_uid not in (0, effective_user):
        raise MetadataReportError(
            "database parent path is not protected"
        )
    writable_by_others = parent.st_mode & 0o022
    sticky = parent.st_mode & stat.S_ISVTX
    if writable_by_others and (
        not sticky or entry.st_uid != effective_user
    ):
        raise MetadataReportError(
            "database parent path is not protected"
        )


def _require_owner_only_database_parent(
    information: os.stat_result,
) -> None:
    if (
        not stat.S_ISDIR(information.st_mode)
        or information.st_uid != os.geteuid()
        or information.st_mode & 0o077
    ):
        raise MetadataReportError(
            "database parent directory is not owner-only"
        )


def _validate_delete_mode_header(descriptor: int) -> None:
    try:
        header = os.pread(descriptor, 20, 0)
    except OSError as error:
        raise MetadataReportError("database header is unavailable") from error
    if (
        len(header) != 20
        or header[:16] != b"SQLite format 3\x00"
        or header[18:20] != b"\x01\x01"
    ):
        raise MetadataReportError("database recovery mode is unsupported")


def _validate_connection_binding(
    connection: sqlite3.Connection,
    path: Path,
    descriptor: int,
    before: dict[int, tuple[int, int]],
) -> None:
    expected = os.fstat(descriptor)
    expected_identity = (expected.st_dev, expected.st_ino)
    if sys.platform.startswith("linux"):
        after = _regular_descriptors(descriptor)
        newly_opened = set(after) - set(before)
        if not any(after[value] == expected_identity for value in newly_opened):
            raise MetadataReportError("database connection inode is unavailable")
    rows = connection.execute("PRAGMA database_list").fetchall()
    if (
        len(rows) != 1
        or len(rows[0]) != 3
        or rows[0][0] != 0
        or rows[0][1] != "main"
        or type(rows[0][2]) is not str
        or not rows[0][2]
        or not os.path.isabs(rows[0][2])
    ):
        raise MetadataReportError("database connection identity is unavailable")
    verification = -1
    try:
        verification = os.open(
            rows[0][2],
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        connected = os.fstat(verification)
        if not stat.S_ISREG(connected.st_mode) or (
            connected.st_dev,
            connected.st_ino,
        ) != expected_identity:
            raise MetadataReportError("database pathname changed while opening")
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    finally:
        if verification >= 0:
            os.close(verification)
    try:
        requested = path.lstat()
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    if not stat.S_ISREG(requested.st_mode) or (
        requested.st_dev,
        requested.st_ino,
    ) != expected_identity:
        raise MetadataReportError("database pathname changed while opening")


def _regular_descriptors(excluded: int) -> dict[int, tuple[int, int]]:
    if not sys.platform.startswith("linux"):
        return {}
    try:
        names = os.listdir("/proc/self/fd")
    except OSError as error:
        raise MetadataReportError("database descriptor binding is unavailable") from error
    result: dict[int, tuple[int, int]] = {}
    for name in names:
        if not name.isascii() or not name.isdigit():
            continue
        descriptor = int(name)
        if descriptor == excluded:
            continue
        try:
            information = os.fstat(descriptor)
        except OSError:
            continue
        if stat.S_ISREG(information.st_mode):
            result[descriptor] = (information.st_dev, information.st_ino)
    return result


def _unlink_created(path: Path, expected: tuple[int, int]) -> None:
    try:
        information = path.lstat()
        if stat.S_ISREG(information.st_mode) and (
            information.st_dev,
            information.st_ino,
        ) == expected:
            path.unlink()
    except OSError:
        pass


def _validate_schema(connection: sqlite3.Connection) -> None:
    objects = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    expected_objects = sorted((
        ("index", "sqlite_autoindex_current_metadata_reports_1", None),
        ("index", "sqlite_autoindex_current_metadata_report_lineages_1", None),
        ("index", "sqlite_autoindex_current_review_case_lineages_1", None),
        ("index", "sqlite_autoindex_metadata_report_cover_references_1", None),
        ("index", "metadata_report_cover_references_artifact_id", _COVER_ARTIFACT_INDEX_SQL),
        ("index", "metadata_report_cover_references_preview_digest", _COVER_PREVIEW_INDEX_SQL),
        ("index", "sqlite_autoindex_metadata_reports_1", None),
        ("index", "sqlite_autoindex_metadata_reports_2", None),
        ("index", "sqlite_autoindex_proposal_sets_1", None),
        ("index", "sqlite_autoindex_review_cases_1", None),
        ("index", "sqlite_autoindex_review_case_transitions_1", None),
        ("index", "sqlite_autoindex_review_case_transitions_2", None),
        ("table", "current_metadata_reports", _CURRENT_SQL),
        ("table", "current_metadata_report_lineages", _CURRENT_LINEAGE_SQL),
        ("table", "current_review_case_lineages", _REVIEW_POINTERS_SQL),
        ("table", "metadata_report_cover_references", _COVER_REFERENCES_SQL),
        ("table", "metadata_reports", _REPORTS_SQL),
        ("table", "proposal_sets", _PROPOSAL_SETS_SQL),
        ("table", "review_case_transitions", _REVIEW_TRANSITIONS_SQL),
        ("table", "review_cases", _REVIEW_CASES_SQL),
        ("trigger", "metadata_report_cover_references_no_delete", _COVER_NO_DELETE_SQL),
        ("trigger", "metadata_report_cover_references_no_update", _COVER_NO_UPDATE_SQL),
        ("trigger", "metadata_reports_no_delete", _NO_DELETE_SQL),
        ("trigger", "metadata_reports_no_update", _NO_UPDATE_SQL),
        ("trigger", "proposal_sets_no_delete", _PROPOSAL_NO_DELETE_SQL),
        ("trigger", "proposal_sets_no_update", _PROPOSAL_NO_UPDATE_SQL),
        ("trigger", "review_case_transitions_no_delete", _REVIEW_TRANSITION_NO_DELETE_SQL),
        ("trigger", "review_case_transitions_no_update", _REVIEW_TRANSITION_NO_UPDATE_SQL),
        ("trigger", "review_cases_no_delete", _REVIEW_NO_DELETE_SQL),
        ("trigger", "review_cases_no_update", _REVIEW_NO_UPDATE_SQL),
    ))
    if objects != expected_objects or connection.execute("PRAGMA foreign_key_check").fetchall() or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise MetadataReportError("database is unavailable or incompatible")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _admission_json(status: str, review_case_id: str | None) -> str:
    return ReviewAdmissionResult(status, review_case_id).to_json()


def _validate_report_row(row: tuple[object, ...]) -> str:
    if len(row) != 9:
        raise MetadataReportError("persisted report is invalid")
    report_id, connection, library, item, file_identity, content_digest, schema, digest, document = row
    if type(report_id) is not str or type(document) is not str or schema != REPORT_SCHEMA or type(digest) is not str or hashlib.sha256(document.encode("utf-8")).hexdigest() != digest:
        raise MetadataReportError("persisted report is invalid")
    try:
        report = MetadataReport.from_json(document)
    except ReportValueError as error:
        raise MetadataReportError("persisted report is invalid") from error
    if (report.report_id, report.target.connection, report.target.library, report.target.item, report.file.identity, report.file.content_digest) != (report_id, connection, library, item, file_identity, content_digest):
        raise MetadataReportError("persisted report envelope disagrees")
    return document


def _validate_proposal_row(row: tuple[object, ...]) -> str:
    if len(row) != 3:
        raise MetadataReportError("persisted proposal is invalid")
    proposal_set_id, report_id, document = row
    if (
        type(proposal_set_id) is not str
        or type(document) is not str
    ):
        raise MetadataReportError("persisted proposal is invalid")
    try:
        proposal_set = ProposalSet.from_json(document)
    except ProposalValueError as error:
        raise MetadataReportError("persisted proposal is invalid") from error
    if (
        proposal_set.proposal_set_id,
        proposal_set.report_id,
    ) != (
        proposal_set_id,
        report_id,
    ):
        raise MetadataReportError("persisted proposal envelope disagrees")
    return document

__all__ = ["SQLiteMetadataReportRepository", "open_repository"]
