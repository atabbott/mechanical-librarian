"""Read-only SQLite join projections for the F09g live title report."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from mechanical_librarian.application.live_title_proposals import (
    LiveEpubEvidenceValidator,
    LiveProviderEvidence,
    LiveProviderState,
    LiveSourceEvidence,
    LiveSourceState,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceLocator,
    EpubSourceResolution,
    ResolvedEpubSource,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityWorkObservation,
    IdentityWorkReference,
)
from mechanical_librarian.domain.inventory_v2 import (
    InventoryObservation,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    opaque_snapshot_reference,
    opaque_target_reference,
)


class EpubSourceResolver(Protocol):
    def resolve(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> EpubSourceResolution: ...


class HardcoverMatchValidator(Protocol):
    def _validate_current_match(
        self,
        reference: IdentityWorkReference,
        observation_id: str,
        current_since: str,
        expires_at: str,
    ) -> IdentityWorkObservation: ...


class SQLiteLiveTitleEvidenceReader:
    """Project already-current F07b and F09f rows without new work or egress."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        source_resolver: EpubSourceResolver,
        evidence_validator: LiveEpubEvidenceValidator,
        provider_validator: HardcoverMatchValidator,
        source_fingerprint: Callable[[ResolvedEpubSource], str],
        governing_digest: str,
    ) -> None:
        self._connection = connection
        self._source_resolver = source_resolver
        self._evidence_validator = evidence_validator
        self._provider_validator = provider_validator
        self._source_fingerprint = source_fingerprint
        self._governing_digest = governing_digest

    def inspect_title_source(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> LiveSourceEvidence:
        source = self._source_resolver.resolve(
            locator, mount_configuration_digest
        ).source
        if source is None:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        row = self._connection.execute(
            "SELECT s.snapshot_id,s.fingerprint_digest,s.canonical_observation,s.observed_at "
            "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "JOIN inventory_snapshot_locator_index i USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE i.target_reference=? AND i.snapshot_reference=?",
            (locator.target_reference, locator.snapshot_reference),
        ).fetchone()
        if row is None:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        observation = InventoryObservation.from_json(
            str(row[2]), _parse_time(str(row[3]))
        )
        snapshot = InventorySnapshotV2.create(observation)
        if (
            snapshot.id.value != str(row[0])
            or snapshot.fingerprint.digest != str(row[1])
            or opaque_target_reference(observation.target) != locator.target_reference
            or opaque_snapshot_reference(snapshot) != locator.snapshot_reference
        ):
            raise ValueError("live inventory identity is inconsistent")
        titles = tuple(
            value
            for value in observation.fields
            if value.field is ObservationField.TITLE
        )
        if len(titles) != 1:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        title = titles[0]
        if title.state is ObservationState.PRESENT and len(title.values) == 1:
            current_title_loaded, current_title = True, title.values[0]
        elif title.state in {ObservationState.NULL, ObservationState.EMPTY}:
            current_title_loaded, current_title = True, None
        else:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        evidence = self._connection.execute(
            "SELECT e.evidence_id,e.source_fingerprint,e.report_json "
            "FROM current_epub_opening_evidence c JOIN epub_opening_evidence e "
            "ON e.evidence_id=c.evidence_id WHERE c.target_reference=? AND c.file_index=? "
            "AND e.snapshot_reference=? AND e.binding_digest=?",
            (
                locator.target_reference,
                locator.file_index,
                locator.snapshot_reference,
                source.binding_digest,
            ),
        ).fetchone()
        if evidence is None or type(evidence[2]) is not str:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        if not self._evidence_validator.current_evidence_is_valid(
            source,
            str(evidence[1]),
            self._governing_digest,
            str(evidence[0]),
        ):
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        report = EpubOpeningReport.from_json(evidence[2])
        try:
            current_source_fingerprint = self._source_fingerprint(source)
        except OSError:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        if current_source_fingerprint != evidence[1]:
            return LiveSourceEvidence(
                locator, LiveSourceState.NOT_READY, mount_configuration_digest
            )
        return LiveSourceEvidence(
            locator,
            LiveSourceState.READY,
            mount_configuration_digest,
            inventory_fingerprint=snapshot.fingerprint.digest,
            current_title_loaded=current_title_loaded,
            current_title=current_title,
            evidence_id=str(evidence[0]),
            source_fingerprint=str(evidence[1]),
            report=report,
        )

    def inspect_title_provider(
        self, work_key: str, observed_at: datetime
    ) -> LiveProviderEvidence:
        rows = self._connection.execute(
            "SELECT generation,observation_id,status,current_since,expires_at "
            "FROM current_hardcover_identity WHERE work_key=?",
            (work_key,),
        ).fetchall()
        if len(rows) != 1:
            return LiveProviderEvidence(
                LiveProviderState.NOT_READY,
                _not_ready_provider_identity(work_key, repr(rows)),
                work_key=work_key,
            )
        generation, observation_id, status, current_since, expires_at = rows[0]
        current_since_time = _parse_time(str(current_since))
        expires_at_time = _parse_time(str(expires_at))
        if (
            status != "matched"
            or observed_at < current_since_time
            or observed_at > expires_at_time
        ):
            return LiveProviderEvidence(
                LiveProviderState.NOT_READY,
                _not_ready_provider_identity(work_key, repr(rows[0])),
                work_key=work_key,
            )
        observation = self._provider_validator._validate_current_match(
            IdentityWorkReference(work_key, int(generation)),
            str(observation_id),
            str(current_since),
            str(expires_at),
        )
        if not observation.reusable or observation.provider_title is None:
            return LiveProviderEvidence(
                LiveProviderState.NOT_READY,
                _not_ready_provider_identity(work_key, "legacy"),
                work_key=work_key,
            )
        return LiveProviderEvidence(
            LiveProviderState.READY,
            str(observation_id),
            observation.provider_title,
            work_key,
        )


def _not_ready_provider_identity(work_key: object, state: str) -> str:
    value = work_key if type(work_key) is str else "invalid"
    return hashlib.sha256(
        f"live-provider-not-ready-v1\0{value}\0{state}".encode("utf-8")
    ).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("timestamp is invalid")
    return parsed


__all__ = ["SQLiteLiveTitleEvidenceReader"]
