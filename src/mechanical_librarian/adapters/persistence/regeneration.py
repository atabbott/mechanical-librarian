"""The store side of `regenerate-derived` (ADR-0108, "The offline command").

Admission opens the database through the server's binding, so it refuses
while a server holds the volume, and passes a ledger-only validator to all
three validation points: crash recovery's copy, the read-only check after
binding and the bound connection. The derived partition may be another
build's, so nothing here reads a derived table. Canonical ledger content is
proved on a private scratch copy that holds the ledger beside this build's
empty derived partition, the state the next startup validates.

The gate, the report and the cover pointer plan read ledger rows only. The
retained backup is a verified `VACUUM INTO` copy renamed into
`regeneration-backup`. On a short volume, a held copy of the same epoch whose
ledger matches the database's, as an interrupted run leaves it, counts as
free space and is removed just before the new copy is taken. The
regeneration itself is one transaction.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import NamedTuple, NoReturn
from urllib.parse import quote

from mechanical_librarian._version import __version__
from mechanical_librarian.adapters.persistence.binding import (
    DatabaseInUse,
    _connect_bound,
    _LinuxDatabaseBinding,
    _validate_connection_binding,
)
from mechanical_librarian.adapters.persistence.crash_recovery import (
    _cleanup_scratch,
    recover_private_bindings,
)
from mechanical_librarian.adapters.persistence.field_effect_activity import (
    _OWNER_ACTION_FAILURES,
)
from mechanical_librarian.adapters.persistence.field_effects import (
    SQLiteFieldEffectRepository,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_existing_database,
)
from mechanical_librarian.adapters.persistence.owner_field_answers import (
    _UNCLOSED,
    answer_written_sql,
    close_resume_over_answer,
    retire_record_answer,
)
from mechanical_librarian.adapters.persistence.regeneration_backup import (
    BACKUP_DIRECTORY,
    backup_held,
)
from mechanical_librarian.adapters.persistence.schema import (
    DERIVED_VERSION,
    LEDGER_FORMAT_VERSION,
    BuildPartition,
    _check_constraint_violation,
    _create_schema,
    _expected_schema_objects,
    _register_casefold,
    _validate_ledger_meta,
    _validate_schema,
    _validate_schema_contents,
    browse_fact_identity_is_current,
    build_partition,
)
from mechanical_librarian.application.metadata_report import (
    DerivedRegenerationRequired,
    MetadataReportError,
    PersistedStateRefusal,
)
from mechanical_librarian.application.regenerate_derived import (
    BlockingItem,
    Cause,
    CoverPointer,
    Inspection,
    InsufficientSpace,
    Libraries,
    LibraryReport,
    Reason,
    Refusal,
    RegenerationRefusal,
    WorkField,
)
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument

_INCOMPATIBLE = "database is unavailable or incompatible"
_STOPPED = "regeneration could not continue"
_SCRATCH_COPY = "database.sqlite"
_BACKUP_COPY = re.compile(r"epoch-(0|[1-9][0-9]*)\.sqlite3")
# The retained copy, then crash recovery's copies of the file and its
# journal and the rollback journal itself (ADR-0108 step 1). Admission's
# scratch copy of the ledger is deleted before the retained copy is made, so
# its own file size fits inside the same reserve, which is checked before it.
_RESERVE_FILE_SIZES = 4
# `--retire-pending`'s retirements (ADR-0108 step 4); zero without the flag.
_RETIREMENT_KEYS = (
    "answers_retired",
    "plans_released",
    "plans_retired",
    "policy_transitions_settled",
    "resume_requests_closed",
)
_COUNT_KEYS = (
    "answers_unwritten",
    "answers_written",
    "authored_unwritten",
    "authorities_revoked",
    "cover_pointers_deleted",
    "cover_pointers_repointed",
    "derived_objects_dropped",
    "derived_objects_recreated",
    "stopped_for_owner",
    *_RETIREMENT_KEYS,
)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _full(error: BaseException | None) -> bool:
    """A write met a full volume: the system's ENOSPC or EDQUOT, or SQLite's
    SQLITE_FULL, raised or behind the error that carries it."""
    while error is not None:
        if isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EDQUOT}:
            return True
        if isinstance(error, sqlite3.Error) and hasattr(error, "sqlite_errorcode"):
            return error.sqlite_errorcode & 0xFF == sqlite3.SQLITE_FULL
        error = error.__cause__ or error.__context__
    return False


def _space(parent: int, database: int) -> tuple[int, int]:
    """Bytes free on the volume and the reserve the run needs."""
    volume = os.fstatvfs(parent)
    size = os.fstat(database).st_size
    return volume.f_bavail * volume.f_frsize, _RESERVE_FILE_SIZES * size


def _no_space(parent: int, database: int) -> InsufficientSpace:
    return InsufficientSpace(*_space(parent, database), backup_held(parent))


@contextmanager
def _named_failures(parent: int, database: int) -> Iterator[None]:
    """A system or SQLite error the run meets is the free-space refusal,
    measured now, when the volume is full, and otherwise a failure with
    nothing changed; neither reaches the owner as internal text."""
    try:
        yield
    except (RegenerationRefusal, InsufficientSpace, PersistedStateRefusal):
        raise
    except (OSError, sqlite3.Error, MetadataReportError) as error:
        if _full(error):
            raise _no_space(parent, database) from error
        if isinstance(error, MetadataReportError):
            raise
        raise MetadataReportError(_STOPPED) from error


def _prepare(connection: sqlite3.Connection) -> None:
    _register_casefold(connection)
    connection.create_function(
        "ml_cover_consideration_current", 2, lambda _document, _replay: 0
    )


# --- Admission -----------------------------------------------------------


def _validate_ledger_structure(connection: sqlite3.Connection) -> None:
    """The ledger record, every ledger object exactly, the whole file's
    integrity, each ledger table's foreign keys and CHECKs.

    Startup's own precedence applies; another build's derived partition is
    what this command is for, so only that refusal is let through.
    """
    try:
        _validate_ledger_meta(connection)
    except DerivedRegenerationRequired:
        pass
    except PersistedStateRefusal:
        raise
    except MetadataReportError as error:
        raise RegenerationRefusal(Refusal.LEDGER_ALTERED) from error
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise RegenerationRefusal(Refusal.INTEGRITY)
    ledger = [
        (kind, name, sql) for kind, name, _, sql in build_partition().ledger_objects
    ]
    for kind, name, _ in ledger:
        if (
            kind == "table"
            and connection.execute(
                f"PRAGMA foreign_key_check({_quoted(name)})"
            ).fetchone()
            is not None
        ):
            raise MetadataReportError(_INCOMPATIBLE)
    if table := _check_constraint_violation(connection, ledger):
        raise MetadataReportError(
            f"{_INCOMPATIBLE}: CHECK constraint failed in {table}"
        )


def _validate_ledger_read_only(path: Path, descriptor: int) -> None:
    """Validation points 1 and 2: the ledger's structure on a `mode=ro`
    connection, in one read transaction."""
    uri = f"file:{quote(str(path.absolute()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _validate_connection_binding(connection, path, descriptor)
        _prepare(connection)
        connection.execute("BEGIN")
        _validate_ledger_structure(connection)
    except sqlite3.DatabaseError as error:
        # A file SQLite cannot read as a database fails the same check.
        raise RegenerationRefusal(Refusal.INTEGRITY) from error
    finally:
        try:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        finally:
            connection.close()


@contextmanager
def _scratch(binding: _LinuxDatabaseBinding) -> Iterator[tuple[int, Path]]:
    """A 0700 directory in crash recovery's scratch namespace beside the
    database, holding one empty 0600 file; a kill leaves only what recovery
    already cleans. Yields the directory's descriptor and the file's path."""
    parent = binding.parent_descriptor
    name = f".metadata-report-recovery-{secrets.token_hex(16)}"
    os.mkdir(name, 0o700, dir_fd=parent)
    directory = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
    )
    identity = os.fstat(directory)
    try:
        os.fchmod(directory, 0o700)
        created = os.open(
            _SCRATCH_COPY,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            os.fchmod(created, 0o600)
        finally:
            os.close(created)
        yield directory, Path(f"/proc/self/fd/{directory}/{_SCRATCH_COPY}")
    finally:
        os.close(directory)
        primary = sys.exception()
        try:
            _cleanup_scratch(
                parent,
                name,
                binding.require_current,
                (identity.st_dev, identity.st_ino),
            )
        except BaseException:
            if primary is None:
                raise


def _ledger_select(name: str, sql: str | None) -> str:
    rowid = sql is not None and not sql.rstrip().upper().endswith("WITHOUT ROWID")
    return f"SELECT * FROM {_quoted(name)}" + (" ORDER BY rowid" if rowid else "")


def _validate_ledger_content(
    connection: sqlite3.Connection, binding: _LinuxDatabaseBinding
) -> None:
    """Validation point 3: copy every ledger row into a fresh schema of this
    build, whose derived partition holds only its seed rows, and run the
    open's content validation there, offline. Triggers are set aside for the
    copy, as a regeneration leaves no derived row behind them."""
    tables = [
        (name, sql)
        for kind, name, _, sql in build_partition().ledger_objects
        if kind == "table"
    ]
    with _scratch(binding) as (_, path):
        scratch = sqlite3.connect(path, isolation_level=None)
        try:
            _prepare(scratch)
            _create_schema(scratch)
            triggers = scratch.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY rowid"
            ).fetchall()
            scratch.execute("BEGIN")
            for name, _ in triggers:
                scratch.execute(f"DROP TRIGGER {_quoted(name)}")
            for name, sql in tables:
                scratch.execute(f"DELETE FROM {_quoted(name)}")
                rows = connection.execute(_ledger_select(name, sql))
                width = len(rows.description)
                scratch.executemany(
                    f"INSERT INTO {_quoted(name)} VALUES({','.join('?' * width)})",
                    rows,
                )
            for _, sql in triggers:
                scratch.execute(sql)
            scratch.execute("COMMIT")
            scratch.execute("BEGIN")
            _validate_schema_contents(scratch, offline=True)
            scratch.execute("ROLLBACK")
        finally:
            scratch.close()


class SQLiteRegenerationStore:
    """Admit the application database for one regeneration."""

    def __init__(self, database: Path, clock: Callable[[], int]) -> None:
        self._database = database
        self._clock = clock

    def admit(self) -> SQLiteRegenerationSession:
        path = self._database
        if not os.path.lexists(path):
            raise RegenerationRefusal(Refusal.NO_DATABASE)
        if not sys.platform.startswith("linux"):
            raise MetadataReportError("database private binding is unavailable")
        started_at = self._clock()
        descriptor, _ = open_existing_database(path)
        try:
            binding = _LinuxDatabaseBinding.create(
                path,
                descriptor,
                recover_stale=lambda parent, database, current: (
                    recover_private_bindings(
                        parent, database, path.name, _validate_ledger_read_only, current
                    )
                ),
            )
        except DatabaseInUse as error:
            os.close(descriptor)
            raise RegenerationRefusal(Refusal.IN_USE) from error
        except BaseException as error:
            # Crash recovery restores and copies here, so it can meet a full
            # volume; the binding's folder is not held yet.
            try:
                if _full(error):
                    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        raise _no_space(parent, descriptor) from error
                    finally:
                        os.close(parent)
                if isinstance(error, sqlite3.Error):
                    raise MetadataReportError(_STOPPED) from error
                raise
            finally:
                os.close(descriptor)
        connection: sqlite3.Connection | None = None
        try:
            with _named_failures(binding.parent_descriptor, binding.descriptor):
                connection, held = self._admitted(binding)
            # Admission is never lent to a dependent: no schema attestation.
            session = SQLiteRegenerationSession(
                connection, binding, self._clock, started_at, held
            )
        except BaseException:
            if connection is not None:
                connection.close()
            binding.close()
            raise
        return session

    @staticmethod
    def _admitted(
        binding: _LinuxDatabaseBinding,
    ) -> tuple[sqlite3.Connection, _HeldCopy | None]:
        """Validation points 2 and 3 over the bound database; the connection
        they admit, ready for the run, and the held restore point the
        free-space gate counted as free, if it needed to."""
        _validate_ledger_read_only(binding.alias_path, binding.descriptor)
        binding.require_current()
        connection = _connect_bound(
            binding.alias_path, binding.descriptor, binding.require_current
        )
        try:
            _prepare(connection)
            binding.require_current()
            for pragma in (
                "PRAGMA foreign_keys=ON",
                "PRAGMA journal_mode=DELETE",
                "PRAGMA synchronous=FULL",
                "PRAGMA busy_timeout=5000",
            ):
                connection.execute(pragma)
            held = None
            # A database of this build has nothing to regenerate: the run
            # writes nothing, so it needs neither the reserve nor the copy.
            if not _derived_is_current(connection):
                # The scratch copy is the run's first write: the free-space
                # refusal comes before it, never a full volume met while copying.
                free, needed = _space(binding.parent_descriptor, binding.descriptor)
                if free < needed:
                    # Only a short volume pays for reading the held copy.
                    held = _replaced_copy(connection, binding.parent_descriptor)
                    if held is None or free + held.size < needed:
                        raise InsufficientSpace(
                            free, needed, backup_held(binding.parent_descriptor)
                        )
                _validate_ledger_content(connection, binding)
        except BaseException:
            connection.close()
            raise
        return connection, held


# --- A held restore point the run replaces ---------------------------------


class _HeldCopy(NamedTuple):
    """The held restore point as admission checked it: its name and its
    device, inode, size and modification time."""

    name: str
    identity: tuple[int, int, int, int]

    @property
    def size(self) -> int:
        return self.identity[2]


def _identity(information: os.stat_result) -> tuple[int, int, int, int]:
    return (
        information.st_dev,
        information.st_ino,
        information.st_size,
        information.st_mtime_ns,
    )


def _retained_name(connection: sqlite3.Connection) -> str:
    """The name `back_up` gives the copy of the database as it stands."""
    (epoch,) = connection.execute(
        "SELECT derived_epoch FROM ledger_meta WHERE singleton=1"
    ).fetchone()
    return f"epoch-{epoch}.sqlite3"


def _open_held(directory: int, name: str) -> int | None:
    """`name` in the restore point folder, opened read-only without following
    a link or waiting on a pipe, when it is a regular file of ours."""
    try:
        held = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
    except OSError:
        return None
    try:
        information = os.fstat(held)
    except OSError:
        information = None
    if (
        information is None
        or not stat.S_ISREG(information.st_mode)
        or information.st_uid != os.geteuid()
    ):
        os.close(held)
        return None
    return held


def _open_held_in_folder(parent: int, name: str) -> int | None:
    """`_open_held` in `regeneration-backup`, read without creating it, when
    the folder is one `_backup_directory` keeps: `0700` and ours."""
    try:
        directory = os.open(
            BACKUP_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent,
        )
    except OSError:
        return None
    try:
        folder = os.fstat(directory)
        if folder.st_uid != os.geteuid() or stat.S_IMODE(folder.st_mode) != 0o700:
            return None
        return _open_held(directory, name)
    finally:
        os.close(directory)


def _ledger_digests(connection: sqlite3.Connection) -> list[str]:
    return [
        _table_digest(connection, name, sql)
        for kind, name, _, sql in build_partition().ledger_objects
        if kind == "table"
    ]


def _replaced_copy(connection: sqlite3.Connection, parent: int) -> _HeldCopy | None:
    """The held restore point this run replaces, which the free-space gate
    counts as free: the copy `back_up` would write over, of the database's
    current epoch, whose every ledger table holds exactly the database's
    rows, so the run that took it changed nothing. A copy that cannot be
    read, or lacks a ledger table, is not one. Reads the whole copy."""
    name = _retained_name(connection)
    held = _open_held_in_folder(parent, name)
    if held is None:
        return None
    try:
        identity = _identity(os.fstat(held))
        uri = f"file:/proc/self/fd/{held}?mode=ro"
        copy = sqlite3.connect(uri, uri=True, isolation_level=None)
        try:
            _prepare(copy)
            copy.execute("BEGIN")
            same = _ledger_digests(copy) == _ledger_digests(connection)
        finally:
            copy.close()
        if not same or _identity(os.fstat(held)) != identity:
            return None
    except (OSError, sqlite3.Error):
        return None
    finally:
        os.close(held)
    return _HeldCopy(name, identity)


def _derived_is_current(connection: sqlite3.Connection) -> bool:
    """The ledger names this build's derived partition, the file holds
    exactly this build's objects, and its browse facts were folded under this
    build's Unicode version and rule list."""
    row = connection.execute(
        "SELECT derived_version,derived_schema_digest FROM ledger_meta"
    ).fetchone()
    objects = connection.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    return (
        row == (DERIVED_VERSION, build_partition().derived_schema_digest)
        and objects == _expected_schema_objects()
        and browse_fact_identity_is_current(connection) is True
    )


# --- The gate, the report and the cover pointer plan (ledger rows only) ----

# A plan `--retire-pending` retires: waiting to be sent and never sent.
_RETIRABLE_PLAN = (
    "(plan.state IN ('planned','claimed') OR (plan.state='retryable' AND NOT EXISTS "
    "(SELECT 1 FROM field_effect_events applying WHERE applying.plan_id=plan.plan_id "
    "AND applying.kind='applying')) OR (plan.state='stale' "
    "AND json_extract(plan.document,'$.failure')='connection'))"
)
_RETIRABLE_SQL = (
    "SELECT plan.document FROM field_effects plan WHERE "
    + _RETIRABLE_PLAN
    + " ORDER BY plan.rowid"
)
_IN_FLIGHT_SQL = (
    "SELECT plan.connection_name,plan.library_name,plan.work_id,plan.destination,"
    "plan.state,EXISTS (SELECT 1 FROM field_effect_events applying "
    "WHERE applying.plan_id=plan.plan_id AND applying.kind='applying') "
    "FROM field_effects plan WHERE plan.state IN "
    "('planned','claimed','applying','reconciling','retryable') "
    "OR (plan.state='stale' AND json_extract(plan.document,'$.failure')='connection') "
    "ORDER BY plan.rowid"
)
# An open Resume request is one with no completion or supersession whose
# latest event is not its failure: a failed request is not open (ADR-0108).
_OPEN_RESUME_SQL = (
    "SELECT request.connection_name,request.library_name,request.item_id,"
    "request.destination FROM owner_field_events request "
    "WHERE request.kind='resume_requested' AND "
    + _UNCLOSED.format("request")
    + " AND COALESCE((SELECT latest.kind FROM owner_field_events latest "
    "WHERE latest.request_id=request.event_id ORDER BY latest.rowid DESC LIMIT 1),"
    "'')<>'resume_failed' ORDER BY request.rowid"
)
_POLICY_PROCESSING_SQL = (
    "SELECT EXISTS (SELECT 1 FROM metadata_policy_state active "
    "JOIN metadata_policy_transitions transition "
    "ON transition.generation=active.generation "
    "WHERE transition.state='processing')"
)
# The record's answers with something to write, by whether each is written
# and whether `--retire-pending` would retire its waiting plan.
_RECORD_SQL = (
    "SELECT answer.connection_name,answer.library_name,answer.item_id,"
    "answer.destination,answer.source,"
    + answer_written_sql("answer.answer_id")
    + ",EXISTS (SELECT 1 FROM field_effects plan "
    "WHERE plan.decision_id=answer.answer_id AND "
    + _RETIRABLE_PLAN
    + ") FROM owner_field_answers answer WHERE answer.destination<>'cover' "
    "AND answer.outcome IN ('accept','owner_entered','authored') "
    "ORDER BY answer.connection_name,answer.library_name,answer.item_id,"
    "answer.destination"
)
# Plans stopped for the owner that still count in Activity: their authority
# is current. The regeneration revokes it, so they leave the count.
_STOPPED_SQL = (
    "SELECT plan.connection_name,plan.library_name,plan.work_id,plan.destination "
    "FROM field_effects plan JOIN field_effect_authorities authority "
    "ON authority.decision_id=plan.decision_id AND authority.current=1 "
    "WHERE plan.state='stale' AND json_extract(plan.document,'$.failure') IN ("
    + ",".join(f"'{failure}'" for failure in _OWNER_ACTION_FAILURES)
    + ") ORDER BY plan.connection_name,plan.library_name,plan.work_id,"
    "plan.destination"
)
# Each owner pointer that names a choice neither written nor a keep or a
# rejection, with the work's latest written owner choice to name instead.
_POINTER_PLAN_SQL = (
    "SELECT pointer.connection_name,pointer.library_name,pointer.item_id,"
    "(SELECT latest.selection_id FROM owner_cover_selections latest "
    "JOIN human_decisions chosen ON chosen.decision_id=latest.decision_id "
    "WHERE latest.connection_name=pointer.connection_name "
    "AND latest.library_name=pointer.library_name "
    "AND latest.item_id=pointer.item_id AND "
    + answer_written_sql("latest.decision_id")
    + " ORDER BY chosen.ordinal DESC LIMIT 1) "
    "FROM owner_cover_pointers pointer JOIN owner_cover_selections selection "
    "ON selection.selection_id=pointer.selection_id "
    "JOIN human_decisions decision ON decision.decision_id=selection.decision_id "
    "WHERE decision.outcome NOT IN ('keep_current','reject') AND NOT "
    + answer_written_sql("selection.decision_id")
    + " ORDER BY pointer.connection_name,pointer.library_name,pointer.item_id"
)


def _pointer_plan(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str, str | None]]:
    """The pointers the regeneration repoints (to the selection named) or
    deletes (None); every other pointer is kept. Report list 4 and the
    transaction both read this."""
    return connection.execute(_POINTER_PLAN_SQL).fetchall()


class SQLiteRegenerationSession:
    """The admitted database, bound exclusively until `close`."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding,
        clock: Callable[[], int],
        started_at: int,
        held: _HeldCopy | None,
    ) -> None:
        self._connection = connection
        self._binding = binding
        self._clock = clock
        self._started_at = started_at
        self._held = held
        self._credited = False
        self._backup_sha256: str | None = None

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            self._binding.close()

    def _named_failures(self) -> AbstractContextManager[None]:
        return _named_failures(
            self._binding.parent_descriptor, self._binding.descriptor
        )

    def derived_is_current(self) -> bool:
        """Nothing to do: the ledger names this build's derived partition and
        the file holds exactly this build's objects."""
        with self._named_failures():
            return _derived_is_current(self._connection)

    def free_space(self) -> tuple[int, int]:
        """Free space counts the held restore point admission found the run
        replaces, while it is unchanged and the volume is short without it;
        `back_up` then removes it just before taking the new copy."""
        with self._named_failures():
            free, needed = _space(
                self._binding.parent_descriptor, self._binding.descriptor
            )
            held = self._held
            self._credited = (
                free < needed
                and held is not None
                and free + held.size >= needed
                and self._held_unchanged(held)
            )
            if self._credited and held is not None:
                return free + held.size, needed
            return free, needed

    def _held_unchanged(self, held: _HeldCopy) -> bool:
        descriptor = _open_held_in_folder(self._binding.parent_descriptor, held.name)
        if descriptor is None:
            return False
        try:
            return _identity(os.fstat(descriptor)) == held.identity
        finally:
            os.close(descriptor)

    def store_libraries(self) -> Libraries | None:
        """The stored settings' libraries, decoded without their secrets;
        None while setup has not finished or the document does not decode."""
        with self._named_failures():
            row = self._connection.execute(
                "SELECT document FROM owner_settings WHERE singleton=1"
            ).fetchone()
        if row is None:
            return None
        try:
            document = OwnerSettingsDocument.model_validate_json(row[0])
        except ValueError:
            return None
        return frozenset(
            (library.connection, library.name) for library in document.libraries
        )

    def _causes(
        self, configured: Libraries | None
    ) -> Callable[[str | None, str | None, str | None], tuple[Cause, ...]]:
        connection = self._connection
        writes_paused = connection.execute(
            "SELECT paused FROM field_effect_control WHERE singleton=1"
        ).fetchone() == (1,)
        preparation_paused = connection.execute(
            "SELECT paused FROM review_preparation_owner_control WHERE singleton=1"
        ).fetchone() == (1,)
        suspended = set(
            connection.execute(
                "SELECT scope,connection_name,destination FROM field_effect_suspensions"
            ).fetchall()
        )

        def causes(
            connection_name: str | None, library: str | None, destination: str | None
        ) -> tuple[Cause, ...]:
            found = [Cause.WRITES_PAUSED] if writes_paused else []
            if ("destination", connection_name, destination) in suspended:
                found.append(Cause.FIELD_SUSPENDED)
            if ("connection", connection_name, "") in suspended:
                found.append(Cause.CONNECTION_SUSPENDED)
            if preparation_paused:
                found.append(Cause.PREPARATION_PAUSED)
            if (
                configured is not None
                and connection_name is not None
                and (connection_name, library) not in configured
            ):
                found.append(Cause.LIBRARY_NOT_CONFIGURED)
            return tuple(found)

        return causes

    def inspect(self, configured: Libraries | None) -> Inspection:
        with self._named_failures():
            return self._inspect(configured)

    def _inspect(self, configured: Libraries | None) -> Inspection:
        connection = self._connection
        causes = self._causes(configured)
        blocking: list[BlockingItem] = []
        retirable: list[BlockingItem] = []
        for name, library, item, destination, state, applied in connection.execute(
            _IN_FLIGHT_SQL
        ).fetchall():
            sent = state in {"applying", "reconciling"} or (
                state == "retryable" and applied
            )
            (blocking if sent else retirable).append(
                BlockingItem(
                    Reason.MAY_HAVE_RECEIVED if sent else Reason.WAITING,
                    causes(name, library, destination),
                    name,
                    library,
                    item,
                    destination,
                )
            )
        for name, library, item, destination in connection.execute(
            _OPEN_RESUME_SQL
        ).fetchall():
            retirable.append(
                BlockingItem(
                    Reason.RESUME,
                    causes(name, library, destination),
                    name,
                    library,
                    item,
                    destination,
                )
            )
        if connection.execute(_POLICY_PROCESSING_SQL).fetchone() == (1,):
            policy_causes = tuple(
                cause
                for cause in causes(None, None, None)
                if cause is Cause.PREPARATION_PAUSED
            )
            retirable.append(BlockingItem(Reason.POLICY, policy_causes))
        return Inspection(
            tuple(blocking),
            tuple(retirable),
            self._reports(),
            backup_held(self._binding.parent_descriptor),
        )

    def _reports(self) -> tuple[LibraryReport, ...]:
        connection = self._connection
        unwritten: dict[tuple[str, str], list[WorkField]] = {}
        retired: dict[tuple[str, str], list[WorkField]] = {}
        written: dict[tuple[str, str], int] = {}
        authored: dict[tuple[str, str], list[WorkField]] = {}
        covers: dict[tuple[str, str], list[CoverPointer]] = {}
        stopped: dict[tuple[str, str], list[WorkField]] = {}
        for (
            name,
            library,
            item,
            destination,
            source,
            done,
            waiting,
        ) in connection.execute(_RECORD_SQL).fetchall():
            entry = WorkField(item, destination)
            if done:
                written[name, library] = written.get((name, library), 0) + 1
            elif source == "authored_description":
                authored.setdefault((name, library), []).append(entry)
            else:
                unwritten.setdefault((name, library), []).append(entry)
                if waiting:
                    retired.setdefault((name, library), []).append(entry)
        for name, library, item, replacement in _pointer_plan(connection):
            covers.setdefault((name, library), []).append(
                CoverPointer(item, replacement is not None)
            )
        for name, library, item, destination in connection.execute(
            _STOPPED_SQL
        ).fetchall():
            stopped.setdefault((name, library), []).append(WorkField(item, destination))
        keys = sorted({*unwritten, *written, *authored, *covers, *stopped})
        return tuple(
            LibraryReport(
                name,
                library,
                unwritten=tuple(unwritten.get((name, library), ())),
                retired=tuple(retired.get((name, library), ())),
                written=written.get((name, library), 0),
                authored=tuple(authored.get((name, library), ())),
                covers=tuple(covers.get((name, library), ())),
                stopped=tuple(stopped.get((name, library), ())),
            )
            for name, library in keys
        )

    # --- The retained backup ----------------------------------------------

    def _backup_directory(self) -> int:
        """Open `regeneration-backup`, making it 0700 when absent; any other
        entry under the name, or anything in it but earlier copies, refuses."""
        parent = self._binding.parent_descriptor
        try:
            os.mkdir(BACKUP_DIRECTORY, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        try:
            directory = os.open(
                BACKUP_DIRECTORY,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
        except OSError as error:
            raise RegenerationRefusal(Refusal.BACKUP_UNSAFE) from error
        try:
            information = os.fstat(directory)
            if (
                information.st_uid != os.geteuid()
                or stat.S_IMODE(information.st_mode) != 0o700
            ):
                raise RegenerationRefusal(Refusal.BACKUP_UNSAFE)
            for name in os.listdir(directory):
                entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (
                    _BACKUP_COPY.fullmatch(name) is None
                    or not stat.S_ISREG(entry.st_mode)
                    or entry.st_uid != os.geteuid()
                ):
                    raise RegenerationRefusal(Refusal.BACKUP_UNSAFE)
        except BaseException:
            os.close(directory)
            raise
        return directory

    def back_up(self) -> None:
        """Fill a scratch copy with `VACUUM INTO`, sync and verify it, and
        rename it to `regeneration-backup/epoch-<n>.sqlite3`, replacing any
        earlier copy (ADR-0108 step 5). A full volume is the free-space
        refusal; any other error stops the run with nothing changed."""
        with self._named_failures():
            self._back_up()

    def _back_up(self) -> None:
        retained = _retained_name(self._connection)
        directory = self._backup_directory()
        try:
            with _scratch(self._binding) as (scratch, path):
                if self._credited:
                    self._remove_held(directory, retained)
                self._connection.execute("VACUUM INTO ?", (str(path),))
                copy = os.open(
                    _SCRATCH_COPY, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=scratch
                )
                try:
                    os.fsync(copy)
                    digest = hashlib.sha256()
                    while chunk := os.read(copy, 1024 * 1024):
                        digest.update(chunk)
                finally:
                    os.close(copy)
                _verify_copy(self._connection, path)
                os.rename(
                    _SCRATCH_COPY, retained, src_dir_fd=scratch, dst_dir_fd=directory
                )
            os.fsync(directory)
            for name in os.listdir(directory):
                if name != retained:
                    os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            os.fsync(self._binding.parent_descriptor)
        finally:
            os.close(directory)
        self._backup_sha256 = digest.hexdigest()

    def _remove_held(self, directory: int, retained: str) -> None:
        """Remove the held copy the free-space gate counted as free, only if
        it is still the file admission checked; otherwise refuse for space,
        as without it, and leave it."""
        held = self._held
        if held is not None and held.name == retained:
            descriptor = _open_held(directory, retained)
            if descriptor is not None:
                try:
                    same = _identity(os.fstat(descriptor)) == held.identity
                finally:
                    os.close(descriptor)
                if same:
                    os.unlink(retained, dir_fd=directory)
                    return
        raise _no_space(self._binding.parent_descriptor, self._binding.descriptor)

    # --- The one transaction ------------------------------------------------

    def regenerate(self, *, retire_pending: bool, inspection: Inspection) -> None:
        """Run step 6 in one transaction; any failure rolls it back."""
        if self._backup_sha256 is None:
            raise MetadataReportError("regeneration has no verified backup")
        connection = self._connection
        with self._named_failures():
            try:
                connection.execute("PRAGMA secure_delete=FAST")
                connection.execute("BEGIN EXCLUSIVE")
                self._transaction(retire_pending, inspection)
                connection.execute("COMMIT")
            except Exception as error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                if _full(error):
                    # Rolled back; the retained copy is kept (step 5).
                    raise _no_space(
                        self._binding.parent_descriptor, self._binding.descriptor
                    ) from error
                raise RegenerationRefusal(Refusal.ROLLED_BACK) from error
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _transaction(self, retire_pending: bool, inspection: Inspection) -> None:
        """Step 6 inside `BEGIN EXCLUSIVE`, in ADR-0108's order:

        1. drop every object not in this build's ledger list;
        2. ledger writes from ledger rows only. A pending ledger migration
           runs first, here, once C6 builds one (admission refuses any other
           ledger format until then); then, with the flag, its retirements
           (`_retire_pending`); then every current authority is revoked through the existing
           revocation; then the cover pointer plan; then `ledger_meta` takes
           the next epoch and this build's derived version and digest;
        3. recreate every derived object from this build and its seed rows;
           the empty scan table makes the next start ask every configured
           library to scan;
        4. the audit row, then the open's whole validation over the result,
           which includes `foreign_key_check` and `integrity_check`, so what
           commits is what the next start admits.
        """
        connection = self._connection
        build = build_partition()
        before = connection.execute(
            "SELECT ledger_format_version,derived_epoch,derived_version,"
            "derived_schema_digest FROM ledger_meta WHERE singleton=1"
        ).fetchone()
        counts = dict.fromkeys(_COUNT_KEYS, 0)
        counts["derived_objects_dropped"] = _drop_derived(connection, build)
        at = self._clock()
        effects = _field_effects(connection, self._binding)
        if retire_pending:
            counts.update(_retire_pending(connection, effects, at))
        counts["authorities_revoked"] = _revoke_authorities(connection, effects, at)
        for name, library, item, replacement in _pointer_plan(connection):
            if replacement is None:
                connection.execute(
                    "DELETE FROM owner_cover_pointers WHERE connection_name=? "
                    "AND library_name=? AND item_id=?",
                    (name, library, item),
                )
                counts["cover_pointers_deleted"] += 1
            else:
                connection.execute(
                    "UPDATE owner_cover_pointers SET selection_id=? "
                    "WHERE connection_name=? AND library_name=? AND item_id=?",
                    (replacement, name, library, item),
                )
                counts["cover_pointers_repointed"] += 1
        connection.execute(
            "UPDATE ledger_meta SET derived_epoch=derived_epoch+1,derived_version=?,"
            "derived_schema_digest=? WHERE singleton=1",
            (DERIVED_VERSION, build.derived_schema_digest),
        )
        counts["derived_objects_recreated"] = _recreate_derived(connection, build)
        for report in inspection.reports:
            counts["answers_unwritten"] += len(report.unwritten)
            counts["answers_written"] += report.written
            counts["authored_unwritten"] += len(report.authored)
            counts["stopped_for_owner"] += len(report.stopped)
        (hwm,) = connection.execute(
            "SELECT ordinal FROM owner_answer_ordinal WHERE singleton=1"
        ).fetchone()
        connection.execute(
            "INSERT INTO derived_regenerations(derived_epoch_before,"
            "derived_epoch_after,derived_version_before,derived_version_after,"
            "derived_schema_digest_before,derived_schema_digest_after,"
            "ledger_format_version_before,ledger_format_version_after,"
            "build_version,started_at,finished_at,backup_sha256,"
            "decision_ordinal_hwm,retire_pending,counts_document) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                before[1],
                before[1] + 1,
                before[2],
                DERIVED_VERSION,
                before[3],
                build.derived_schema_digest,
                before[0],
                LEDGER_FORMAT_VERSION,
                __version__,
                self._started_at,
                max(self._started_at, self._clock()),
                self._backup_sha256,
                hwm,
                int(retire_pending),
                json.dumps(counts, sort_keys=True, separators=(",", ":")),
            ),
        )
        _validate_schema(connection)


def _table_digest(connection: sqlite3.Connection, name: str, sql: str | None) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(_ledger_select(name, sql)):
        digest.update(repr(row).encode())
    return digest.hexdigest()


def _verify_copy(source: sqlite3.Connection, path: Path) -> None:
    """The copy passes `integrity_check` and holds every table's rows, in
    order, exactly as the source does; otherwise nothing more happens."""
    tables = source.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    copy = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _prepare(copy)
        if (
            copy.execute("PRAGMA integrity_check").fetchone() != ("ok",)
            or copy.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            != tables
        ):
            raise RegenerationRefusal(Refusal.BACKUP_MISMATCH)
        for name, sql in tables:
            if _table_digest(copy, name, sql) != _table_digest(source, name, sql):
                raise RegenerationRefusal(Refusal.BACKUP_MISMATCH)
    except sqlite3.Error as error:
        raise RegenerationRefusal(Refusal.BACKUP_MISMATCH) from error
    finally:
        copy.close()


def _drop_derived(connection: sqlite3.Connection, build: BuildPartition) -> int:
    """Drop every object outside this build's ledger list: triggers and views,
    then indexes on tables that stay, then tables, children first."""
    ledger = {(kind, name, sql) for kind, name, _, sql in build.ledger_objects}
    dropped = [
        (kind, name, table)
        for kind, name, table, sql in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY rowid"
        ).fetchall()
        if (kind, name, sql) not in ledger
    ]
    tables = {name for kind, name, _ in dropped if kind == "table"}
    for kind in ("trigger", "view"):
        for found, name, _ in dropped:
            if found == kind:
                connection.execute(f"DROP {kind.upper()} {_quoted(name)}")
    for kind, name, table in dropped:
        if kind == "index" and table not in tables:
            connection.execute(f"DROP INDEX {_quoted(name)}")
    for name in _children_first(connection, tables):
        connection.execute(f"DROP TABLE {_quoted(name)}")
    return len(dropped)


def _children_first(connection: sqlite3.Connection, tables: set[str]) -> list[str]:
    parents = {
        table: {
            row[2]
            for row in connection.execute(f"PRAGMA foreign_key_list({_quoted(table)})")
        }
        - {table}
        for table in tables
    }
    order: list[str] = []
    remaining = set(tables)
    while remaining:
        referenced = set().union(*(parents[table] for table in remaining))
        leaves = sorted(remaining - referenced)
        if not leaves:
            raise MetadataReportError("derived tables reference each other in a cycle")
        order.extend(leaves)
        remaining -= set(leaves)
    return order


def _recreate_derived(connection: sqlite3.Connection, build: BuildPartition) -> int:
    """Replay this build's derived objects in creation order, each table's
    seed rows straight after the table, before any trigger can fire."""
    seeds = dict(build.derived_seed_rows)
    for kind, name, _, sql in build.derived_objects:
        if sql is None:
            continue
        connection.execute(sql)
        for row in seeds.get(name, ()) if kind == "table" else ():
            connection.execute(
                f"INSERT INTO {_quoted(name)} VALUES({','.join('?' * len(row))})", row
            )
    return len(build.derived_objects)


def _unreachable(*_arguments: object) -> NoReturn:
    raise MetadataReportError(_INCOMPATIBLE)


def _field_effects(
    connection: sqlite3.Connection, binding: _LinuxDatabaseBinding
) -> SQLiteFieldEffectRepository:
    """The existing field-effect writer over the bound connection. Every
    callback that would read derived state fails, so a step that reached one
    rolls the run back."""
    effects: SQLiteFieldEffectRepository = SQLiteFieldEffectRepository(
        connection,
        binding,
        record_field_effect_event=lambda operation, kind, occurred_at: (
            effects._append_field_effect_event(operation, kind, occurred_at)
        ),
        field_effect_consent_envelope_identity=_unreachable,
        field_effect_authority_source=_unreachable,
        retire_stale_cover_consideration=_unreachable,
        current_cover_authority=_unreachable,
    )
    return effects


def _retire_pending(
    connection: sqlite3.Connection, effects: SQLiteFieldEffectRepository, at: int
) -> dict[str, int]:
    """`--retire-pending`'s retirements, from ledger rows only (ADR-0108 step
    4): a claimed plan goes back to `retryable` (`not_sent`), then every plan
    waiting to be sent closes `superseded` (`authority_superseded`) through
    the existing revocation; each record whose answer such a plan would have
    written gets `retired` and is no longer carried; each open, unfailed
    Resume closes `resume_superseded` over its answer, as the question check's
    step 0 does (contract T5); and the active generation's processing policy
    change settles from its header, every unfinished work counted failed."""
    counts = dict.fromkeys(_RETIREMENT_KEYS, 0)
    retired: set[tuple[str, str, str, str, str]] = set()
    plans = [
        FieldEffectOperation.from_json(document)
        for (document,) in connection.execute(_RETIRABLE_SQL).fetchall()
    ]
    for operation in plans:
        if operation.state == "claimed":
            released = operation.transition("retryable", at=at, failure="not_sent")
            effects._update_field_effect(released, "claimed")
            effects._append_field_effect_event(
                released, "retryable", released.updated_at
            )
            counts["plans_released"] += 1
        effects._revoke_field_effect_authority_in_transaction(
            operation.plan.authority.decision_id, at, required=False
        )
        counts["plans_retired"] += 1
        # Only an automatic plan carries a subtitle companion (contract T1),
        # so the plan's own decision is the only answer it would have written.
        plan = operation.plan
        key = (plan.connection, plan.library, plan.work_id, plan.destination)
        answer = plan.authority.decision_id
        if (*key, answer) not in retired and connection.execute(
            "SELECT 1 FROM owner_field_answers WHERE connection_name=? "
            "AND library_name=? AND item_id=? AND destination=? AND answer_id=?",
            (*key, answer),
        ).fetchone():
            retire_record_answer(connection, key, answer, at)
            retired.add((*key, answer))
            counts["answers_retired"] += 1
    for key in connection.execute(_OPEN_RESUME_SQL).fetchall():
        if not close_resume_over_answer(connection, key, at):
            raise MetadataReportError(_INCOMPATIBLE)
        counts["resume_requests_closed"] += 1
    counts["policy_transitions_settled"] = connection.execute(
        "UPDATE metadata_policy_transitions SET state='completed_with_failures',"
        "failed=total-completed,updated_at=max(updated_at,?) WHERE state='processing' "
        "AND generation=(SELECT generation FROM metadata_policy_state WHERE singleton=1)",
        (at,),
    ).rowcount
    if (
        connection.execute(_RETIRABLE_SQL).fetchone() is not None
        or connection.execute(_OPEN_RESUME_SQL).fetchone() is not None
        or connection.execute(_POLICY_PROCESSING_SQL).fetchone() != (0,)
    ):
        raise MetadataReportError(_INCOMPATIBLE)
    return counts


def _revoke_authorities(
    connection: sqlite3.Connection, effects: SQLiteFieldEffectRepository, at: int
) -> int:
    """Revoke every current authority through the existing revocation, which
    writes the column and the document together. The gate and the flag's
    retirements leave no plan waiting on one, so no derived reader is
    reached; one that is fails."""
    current = connection.execute(
        "SELECT decision_id FROM field_effect_authorities WHERE current=1 ORDER BY rowid"
    ).fetchall()
    for (decision_id,) in current:
        effects._revoke_field_effect_authority_in_transaction(
            decision_id, at, required=True
        )
    return len(current)


__all__ = ["SQLiteRegenerationSession", "SQLiteRegenerationStore"]
