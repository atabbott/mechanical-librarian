"""The offline `regenerate-derived` command's decisions (ADR-0108).

The store side lives in `adapters/persistence/regeneration.py` and the words
in `interfaces/cli/regenerate.py`. This module holds the order of the steps
and the decision rule: admission, nothing to do, the free-space reserve, the
gate, the report, the rule that only `--retire-pending` lets lists 3, 4 and 5
go ahead, the retained backup and the one transaction. It returns what
happened; the caller prints it and turns it into the exit status.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
)
from mechanical_librarian.application.metadata_report import MetadataReportError

Libraries = frozenset[tuple[str, str]]


class Refusal(StrEnum):
    """Why the command changed nothing, before or inside its run."""

    IN_USE = "in_use"
    NO_DATABASE = "no_database"
    INTEGRITY = "integrity"
    BACKUP_UNSAFE = "backup_unsafe"
    BACKUP_MISMATCH = "backup_mismatch"
    ROLLED_BACK = "rolled_back"


class RegenerationRefusal(MetadataReportError):
    """A refusal the command names; nothing was changed."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


class Reason(StrEnum):
    """What a blocking item is waiting for."""

    MAY_HAVE_RECEIVED = "may_have_received"
    WAITING = "waiting"
    RESUME = "resume"
    POLICY = "policy"


class Cause(StrEnum):
    """What the command can see offline that keeps an item waiting."""

    WRITES_PAUSED = "writes_paused"
    FIELD_SUSPENDED = "field_suspended"
    CONNECTION_SUSPENDED = "connection_suspended"
    PREPARATION_PAUSED = "preparation_paused"
    LIBRARY_NOT_CONFIGURED = "library_not_configured"


@dataclass(frozen=True, slots=True)
class BlockingItem:
    """One item the gate names. A policy check names no work or field."""

    reason: Reason
    causes: tuple[Cause, ...]
    connection: str | None = None
    library: str | None = None
    item_id: str | None = None
    destination: str | None = None


@dataclass(frozen=True, slots=True)
class WorkField:
    item_id: str
    destination: str


@dataclass(frozen=True, slots=True)
class CoverPointer:
    """A work whose owner pointer names an unwritten choice: it will name the
    work's last written owner choice, or be deleted."""

    item_id: str
    keeps_written: bool


@dataclass(frozen=True, slots=True)
class LibraryReport:
    """Report lists 1 to 5 for one library, by item key and destination.

    `retired` is the part of list 1 whose waiting write `--retire-pending`
    retires: those answers are asked again and Automatic stays off.
    """

    connection: str
    library: str
    unwritten: tuple[WorkField, ...] = ()
    retired: tuple[WorkField, ...] = ()
    written: int = 0
    authored: tuple[WorkField, ...] = ()
    covers: tuple[CoverPointer, ...] = ()
    stopped: tuple[WorkField, ...] = ()

    @property
    def stops_without_flag(self) -> bool:
        """Lists 3, 4 and 5 need the owner's confirmation (ADR-0108 step 4)."""
        return bool(self.authored or self.covers or self.stopped)

    @property
    def empty(self) -> bool:
        return not (self.unwritten or self.written or self.stops_without_flag)


@dataclass(frozen=True, slots=True)
class Inspection:
    """What admission found, from ledger rows only.

    `blocking` refuses even with the flag; `retirable` refuses without it and
    is what the flag would retire.
    """

    blocking: tuple[BlockingItem, ...]
    retirable: tuple[BlockingItem, ...]
    reports: tuple[LibraryReport, ...]
    backup_held: bool


class RegenerationSession(Protocol):
    """The admitted database, held exclusively until `close`."""

    def derived_is_current(self) -> bool: ...

    def free_space(self) -> tuple[int, int]:
        """Bytes free on the volume and bytes the run needs."""
        ...

    def store_libraries(self) -> Libraries | None:
        """The libraries the stored settings name, or None when unknown."""
        ...

    def inspect(self, configured: Libraries | None) -> Inspection: ...

    def back_up(self) -> None: ...

    def regenerate(self, *, retire_pending: bool, inspection: Inspection) -> None: ...

    def close(self) -> None: ...


class RegenerationStore(Protocol):
    def admit(self) -> RegenerationSession: ...


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the run did. `kind` decides the words and the exit status."""

    kind: Literal[
        "refused",
        "failed",
        "nothing_to_do",
        "no_space",
        "gate",
        "report",
        "regenerated",
    ]
    refusal: Refusal | None = None
    message: str | None = None
    free: int = 0
    needed: int = 0
    backup_held: bool = False
    items: tuple[BlockingItem, ...] = ()
    reports: tuple[LibraryReport, ...] = field(default=())
    retire_pending: bool = False


def configured_libraries(
    configuration: Callable[[], ApplicationConfiguration],
    session: RegenerationSession,
) -> Libraries | None:
    """The configured libraries: the file's in file mode, the stored
    settings' in store mode, or None when neither can be read."""
    try:
        loaded = configuration()
    except FileNotFoundError:
        return session.store_libraries()
    except ConfigurationError:
        return None
    return frozenset((library.connection, library.name) for library in loaded.libraries)


def regenerate_derived(
    store: RegenerationStore,
    *,
    retire_pending: bool,
    configuration: Callable[[], ApplicationConfiguration],
) -> Outcome:
    """Run the command once. Nothing is written unless it returns
    `regenerated`."""
    try:
        session = store.admit()
    except RegenerationRefusal as refused:
        return Outcome("refused", refusal=refused.refusal)
    except MetadataReportError as error:
        return Outcome("failed", message=str(error))
    try:
        return _run(session, retire_pending, configuration)
    except RegenerationRefusal as refused:
        return Outcome("refused", refusal=refused.refusal)
    except MetadataReportError as error:
        return Outcome("failed", message=str(error))
    finally:
        session.close()


def _run(
    session: RegenerationSession,
    retire_pending: bool,
    configuration: Callable[[], ApplicationConfiguration],
) -> Outcome:
    if session.derived_is_current():
        return Outcome("nothing_to_do")
    inspection = session.inspect(configured_libraries(configuration, session))
    free, needed = session.free_space()
    if free < needed:
        return Outcome(
            "no_space", free=free, needed=needed, backup_held=inspection.backup_held
        )
    blocking = inspection.blocking + (() if retire_pending else inspection.retirable)
    if blocking:
        return Outcome("gate", items=blocking)
    stops = any(report.stops_without_flag for report in inspection.reports)
    if stops and not retire_pending:
        return Outcome(
            "report",
            reports=inspection.reports,
            backup_held=inspection.backup_held,
        )
    session.back_up()
    session.regenerate(retire_pending=retire_pending, inspection=inspection)
    return Outcome(
        "regenerated",
        reports=inspection.reports,
        backup_held=inspection.backup_held,
        retire_pending=retire_pending,
    )


__all__ = [
    "BlockingItem",
    "Cause",
    "CoverPointer",
    "Inspection",
    "LibraryReport",
    "Outcome",
    "Reason",
    "Refusal",
    "RegenerationRefusal",
    "RegenerationSession",
    "RegenerationStore",
    "WorkField",
    "configured_libraries",
    "regenerate_derived",
]
