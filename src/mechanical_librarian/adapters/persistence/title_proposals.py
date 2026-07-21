"""Atomic SQLite history and current-pointer storage for F09a."""

from __future__ import annotations

import sqlite3
import re
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from mechanical_librarian.application.live_title_proposals import (
    LiveProviderEvidence,
    LiveTitleProviderReader,
    LiveSourceEvidence,
    LiveSourceState,
    LiveTitleSourceReader,
    mount_configuration_set_digest,
)
from mechanical_librarian.application.title_proposals import ProposalPersistence
from mechanical_librarian.domain.epub_opening_evidence import EpubEvidenceLocator
from mechanical_librarian.domain.title_proposals import (
    LiveTitleProposalSet,
    ProposalExecutionMode,
    TitleProposalSet,
    TitleProposalValueError,
)


class TitleProposalPersistenceError(RuntimeError):
    """Proposal history is unavailable, corrupt, or inconsistent."""


_MAX_SYNTHETIC_LINEAGE_REVISIONS = 2
_MAX_LIVE_LINEAGE_REVISIONS = 1_000


class SQLiteTitleProposalRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        after_history_insert: Callable[[], None] | None = None,
        after_pointer_advance: Callable[[], None] | None = None,
        before_readback: Callable[[], None] | None = None,
        source_reader: LiveTitleSourceReader | None = None,
        provider_reader: LiveTitleProviderReader | None = None,
        publication_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection = connection
        self._after_history_insert = after_history_insert
        self._after_pointer_advance = after_pointer_advance
        self._before_readback = before_readback
        self._source_reader = source_reader
        self._provider_reader = provider_reader
        self._publication_clock = publication_clock

    def current(self, lineage: str) -> TitleProposalSet | None:
        try:
            with _read_transaction(self._connection):
                _history, current = self._validate_lineage(lineage)
            if current is not None and type(current) is not TitleProposalSet:
                raise TitleProposalPersistenceError("synthetic proposal lineage has a live mode")
            return current
        except TitleProposalPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, RecursionError):
            raise TitleProposalPersistenceError("proposal state could not be read") from None

    def store(self, proposal_set: TitleProposalSet) -> ProposalPersistence:
        if type(proposal_set) is not TitleProposalSet:
            raise TitleProposalPersistenceError("proposal publication input is invalid")
        try:
            with _transaction(self._connection):
                return self._store(proposal_set)
        except TitleProposalPersistenceError:
            raise
        except (sqlite3.Error, TitleProposalValueError, TypeError, ValueError, RecursionError):
            raise TitleProposalPersistenceError("proposal publication failed safely") from None

    def current_live(self, lineage: str) -> LiveTitleProposalSet | None:
        try:
            with _read_transaction(self._connection):
                _history, current = self._validate_lineage(lineage)
            if current is not None and type(current) is not LiveTitleProposalSet:
                raise TitleProposalPersistenceError("live proposal lineage has a synthetic mode")
            return current
        except TitleProposalPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, RecursionError):
            raise TitleProposalPersistenceError("proposal state could not be read") from None

    def inspect_source(
        self, locator: EpubEvidenceLocator, mount_configuration_digests: tuple[str, ...]
    ) -> LiveSourceEvidence:
        try:
            if self._source_reader is None:
                raise TitleProposalPersistenceError("live proposal source is unavailable")
            mount_configuration_set_digest(mount_configuration_digests)
            with _read_transaction(self._connection):
                return self._inspect_source_selection(
                    locator, mount_configuration_digests
                )
        except TitleProposalPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, RecursionError):
            raise TitleProposalPersistenceError("live proposal source could not be read") from None

    def inspect_provider(
        self, work_key: str, observed_at
    ) -> LiveProviderEvidence:
        try:
            if self._provider_reader is None:
                raise TitleProposalPersistenceError(
                    "live provider evidence is unavailable"
                )
            with _read_transaction(self._connection):
                return self._provider_reader.inspect_title_provider(
                    work_key, observed_at
                )
        except TitleProposalPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, RecursionError):
            raise TitleProposalPersistenceError(
                "live provider evidence could not be read"
            ) from None

    def publish_live(
        self,
        proposal_set: LiveTitleProposalSet,
        source: LiveSourceEvidence,
        provider: LiveProviderEvidence,
        mount_configuration_digests: tuple[str, ...],
    ) -> ProposalPersistence:
        if (
            type(proposal_set) is not LiveTitleProposalSet
            or type(source) is not LiveSourceEvidence
            or type(provider) is not LiveProviderEvidence
        ):
            raise TitleProposalPersistenceError("live proposal publication input is invalid")
        try:
            with _transaction(self._connection):
                manifest = proposal_set.manifest
                mount_set_digest = mount_configuration_set_digest(
                    mount_configuration_digests
                )
                if (
                    manifest["target_reference"] != source.locator.target_reference
                    or manifest["snapshot_reference"] != source.locator.snapshot_reference
                    or manifest["file_index"] != source.locator.file_index
                    or manifest["inventory_fingerprint"] != source.inventory_fingerprint
                    or manifest["local_evidence_id"] != source.evidence_id
                    or manifest["mount_configuration_digest"]
                    != source.mount_configuration_digest
                    or manifest["mount_configuration_set_digest"]
                    != mount_set_digest
                    or manifest["source_fingerprint"] != source.source_fingerprint
                    or manifest["provider_evidence_id"] != provider.evidence_id
                    or manifest["provider_status"] != provider.state.value
                ):
                    raise TitleProposalPersistenceError(
                        "live proposal evidence projection changed"
                    )
                if (
                    self._source_reader is None
                    or self._provider_reader is None
                    or self._publication_clock is None
                ):
                    raise TitleProposalPersistenceError(
                        "live proposal evidence readers are unavailable"
                    )
                if self._inspect_source_selection(
                    source.locator, mount_configuration_digests
                ) != source:
                    raise TitleProposalPersistenceError("live proposal source changed")
                if provider.work_key is not None:
                    if self._provider_reader.inspect_title_provider(
                        provider.work_key, self._publication_clock()
                    ) != provider:
                        raise TitleProposalPersistenceError("live provider evidence changed")
                persistence = self._store(proposal_set)
                if (
                    not persistence.current
                    or persistence.current_set_id != proposal_set.set_id
                    or persistence.proposal_set != proposal_set
                ):
                    raise TitleProposalPersistenceError(
                        "live proposal is no longer current"
                    )
                return persistence
        except TitleProposalPersistenceError:
            raise
        except (sqlite3.Error, TitleProposalValueError, TypeError, ValueError, RecursionError):
            raise TitleProposalPersistenceError("live proposal publication failed safely") from None

    def _inspect_source_selection(
        self,
        locator: EpubEvidenceLocator,
        mount_configuration_digests: tuple[str, ...],
    ) -> LiveSourceEvidence:
        assert self._source_reader is not None
        sources = tuple(
            self._source_reader.inspect_title_source(locator, digest)
            for digest in mount_configuration_digests
        )
        ready = tuple(source for source in sources if source.state is LiveSourceState.READY)
        if len(ready) > 1:
            raise TitleProposalPersistenceError("live proposal source is ambiguous")
        return ready[0] if ready else sources[0]

    def _store(
        self, proposal_set: TitleProposalSet | LiveTitleProposalSet
    ) -> ProposalPersistence:
        canonical = proposal_set.to_json()
        history, current = self._validate_lineage(proposal_set.lineage)
        by_id = self._row_for_id(proposal_set.set_id)
        by_digest = self._connection.execute(
            "SELECT set_id, identity_digest, lineage, lineage_revision, previous_set_id, execution_mode, canonical_set "
            "FROM synthetic_title_proposal_sets WHERE identity_digest = ?",
            (proposal_set.identity_digest,),
        ).fetchone()
        by_revision = self._connection.execute(
            "SELECT set_id, identity_digest, lineage, lineage_revision, previous_set_id, execution_mode, canonical_set "
            "FROM synthetic_title_proposal_sets WHERE lineage = ? AND lineage_revision = ?",
            (proposal_set.lineage, proposal_set.lineage_revision),
        ).fetchone()
        rows = [row for row in (by_id, by_digest, by_revision) if row is not None]
        existing: TitleProposalSet | LiveTitleProposalSet | None = None
        for row in rows:
            hydrated = _hydrate(row)
            if existing is not None and hydrated != existing:
                raise TitleProposalPersistenceError("proposal identity rows disagree")
            existing = hydrated
        if existing is not None:
            if existing != proposal_set:
                raise TitleProposalPersistenceError("proposal identity collision detected")
            return ProposalPersistence(
                existing,
                replayed=True,
                current=current is not None and current.set_id == existing.set_id,
                current_set_id=existing.set_id if current is None else current.set_id,
                superseded_set_id=(
                    None if current is None or current.set_id == existing.set_id else existing.set_id
                ),
                history_count=len(history),
            )

        if current is None:
            if proposal_set.lineage_revision != 1 or proposal_set.previous_set_id is not None:
                raise TitleProposalPersistenceError("proposal predecessor is missing")
        else:
            if proposal_set.lineage_revision != current.lineage_revision + 1:
                raise TitleProposalPersistenceError("proposal revision is not the exact successor")
            if proposal_set.previous_set_id != current.set_id:
                raise TitleProposalPersistenceError("proposal predecessor does not match current state")
            if proposal_set.lineage != current.lineage or type(current) is not type(proposal_set):
                raise TitleProposalPersistenceError("proposal supersession crosses lineages")

        self._connection.execute(
            "INSERT INTO synthetic_title_proposal_sets("
            "set_id, identity_digest, lineage, lineage_revision, previous_set_id, "
            "mode, canonical_set, execution_mode) VALUES (?, ?, ?, ?, ?, 'synthetic_preview', ?, ?)",
            (
                proposal_set.set_id,
                proposal_set.identity_digest,
                proposal_set.lineage,
                proposal_set.lineage_revision,
                proposal_set.previous_set_id,
                canonical,
                proposal_set.mode.value,
            ),
        )
        if self._after_history_insert is not None:
            self._after_history_insert()
        if current is None:
            self._connection.execute(
                "INSERT INTO current_synthetic_title_proposals(lineage, set_id) VALUES (?, ?)",
                (proposal_set.lineage, proposal_set.set_id),
            )
        else:
            changed = self._connection.execute(
                "UPDATE current_synthetic_title_proposals SET set_id = ? "
                "WHERE lineage = ? AND set_id = ?",
                (proposal_set.set_id, proposal_set.lineage, current.set_id),
            ).rowcount
            if changed != 1:
                raise TitleProposalPersistenceError("proposal current pointer changed concurrently")
        if self._after_pointer_advance is not None:
            self._after_pointer_advance()
        if self._before_readback is not None:
            self._before_readback()
        readback_history, readback = self._validate_lineage(proposal_set.lineage)
        if readback != proposal_set:
            raise TitleProposalPersistenceError("proposal publication readback failed")
        return ProposalPersistence(
            proposal_set,
            replayed=False,
            current=True,
            current_set_id=proposal_set.set_id,
            superseded_set_id=None if current is None else current.set_id,
            history_count=len(readback_history),
        )

    def _row_for_id(self, set_id: str):
        return self._connection.execute(
            "SELECT set_id, identity_digest, lineage, lineage_revision, previous_set_id, execution_mode, canonical_set "
            "FROM synthetic_title_proposal_sets WHERE set_id = ?",
            (set_id,),
        ).fetchone()

    def _validate_lineage(
        self, lineage: str
    ) -> tuple[
        tuple[TitleProposalSet | LiveTitleProposalSet, ...],
        TitleProposalSet | LiveTitleProposalSet | None,
    ]:
        if (
            type(lineage) is not str
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", lineage) is None
        ):
            raise TitleProposalPersistenceError("proposal lineage is invalid")
        rows = self._connection.execute(
            "SELECT set_id, identity_digest, lineage, lineage_revision, previous_set_id, execution_mode, canonical_set "
            "FROM synthetic_title_proposal_sets WHERE lineage = ? "
            "ORDER BY lineage_revision, set_id LIMIT ?",
            (lineage, _MAX_LIVE_LINEAGE_REVISIONS + 1),
        ).fetchall()
        pointers = self._connection.execute(
            "SELECT set_id FROM current_synthetic_title_proposals WHERE lineage = ?",
            (lineage,),
        ).fetchall()
        if not rows:
            if pointers:
                raise TitleProposalPersistenceError("proposal current pointer is dangling")
            return (), None
        if len(rows) > _MAX_LIVE_LINEAGE_REVISIONS:
            raise TitleProposalPersistenceError("proposal lineage exceeds its revision bound")
        if len(pointers) != 1:
            raise TitleProposalPersistenceError("proposal lineage requires one current pointer")
        history = tuple(_hydrate(row) for row in rows)
        revision_bound = (
            _MAX_SYNTHETIC_LINEAGE_REVISIONS
            if type(history[0]) is TitleProposalSet
            else _MAX_LIVE_LINEAGE_REVISIONS
        )
        if len(history) > revision_bound:
            raise TitleProposalPersistenceError("proposal lineage exceeds its revision bound")
        expected_revisions = tuple(range(1, len(history) + 1))
        if tuple(item.lineage_revision for item in history) != expected_revisions:
            raise TitleProposalPersistenceError("proposal lineage revisions are not contiguous")
        for index, item in enumerate(history):
            if item.lineage != lineage or type(item) is not type(history[0]):
                raise TitleProposalPersistenceError("proposal history crosses lineages")
            expected_previous = None if index == 0 else history[index - 1].set_id
            if item.previous_set_id != expected_previous:
                raise TitleProposalPersistenceError("proposal predecessor chain is corrupt")
        pointer_id = pointers[0][0]
        if type(pointer_id) is not str or pointer_id != history[-1].set_id:
            raise TitleProposalPersistenceError("proposal current pointer is not the latest revision")
        return history, history[-1]


def _hydrate(row: tuple[object, ...]) -> TitleProposalSet | LiveTitleProposalSet:
    if len(row) != 7 or type(row[6]) is not str:
        raise TitleProposalPersistenceError("proposal history row is malformed")
    mode = ProposalExecutionMode(str(row[5]))
    proposal_set = (
        TitleProposalSet.from_json(row[6])
        if mode is ProposalExecutionMode.SYNTHETIC_PREVIEW
        else LiveTitleProposalSet.from_json(row[6])
    )
    if (
        row[0] != proposal_set.set_id
        or row[1] != proposal_set.identity_digest
        or row[2] != proposal_set.lineage
        or row[3] != proposal_set.lineage_revision
        or row[4] != proposal_set.previous_set_id
        or row[5] != proposal_set.mode.value
    ):
        raise TitleProposalPersistenceError("proposal history row failed identity validation")
    return proposal_set


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise TitleProposalPersistenceError("nested proposal publication is unsupported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@contextmanager
def _read_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Hold one coherent snapshot without taking ownership of a caller transaction."""

    if connection.in_transaction:
        yield
        return
    connection.execute("BEGIN")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


__all__ = ["SQLiteTitleProposalRepository", "TitleProposalPersistenceError"]
