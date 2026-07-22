"""One foreground live title-proposal report over current durable evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from mechanical_librarian.application.hardcover_safe_executor import (
    hardcover_identity_work_key,
)
from mechanical_librarian.application.title_proposals import ProposalPersistence
from mechanical_librarian.domain.epub_inspection import (
    EpubInspection,
    EpubInspectionField,
    EpubInspectionOutcome,
)
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceLocator,
    ResolvedEpubSource,
)
from mechanical_librarian.domain.hardcover_identity import (
    isbn_identity,
    validate_private_query,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityWorkObservation,
    IdentityWorkReference,
)
from mechanical_librarian.domain.title_proposals import (
    LIVE_TITLE_PROPOSAL_POLICY_VERSION,
    LIVE_TITLE_PROPOSAL_SCHEMA,
    TITLE_COMPARISON_VERSION,
    LiveTitleProposalSet,
    ProposalExecutionMode,
    TitleProposalResult,
    live_proposal_set_from_manifest,
    normalized_title_key,
)


class LiveSourceState(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class LiveProviderState(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


@dataclass(frozen=True, repr=False)
class LiveSourceEvidence:
    locator: EpubEvidenceLocator
    state: LiveSourceState
    mount_configuration_digest: str
    inventory_fingerprint: str | None = None
    current_title_loaded: bool = False
    current_title: str | None = None
    evidence_id: str | None = None
    source_fingerprint: str | None = None
    inspection: EpubInspection | None = None

    def __post_init__(self) -> None:
        ready = self.state is LiveSourceState.READY
        if (
            type(self.locator) is not EpubEvidenceLocator
            or type(self.state) is not LiveSourceState
            or not _digest(self.mount_configuration_digest)
            or type(self.current_title_loaded) is not bool
            or ready
            != (
                _digest(self.inventory_fingerprint)
                and self.current_title_loaded
                and _evidence_id(self.evidence_id)
                and _digest(self.source_fingerprint)
                and type(self.inspection) is EpubInspection
            )
            or (
                self.current_title is not None
                and (type(self.current_title) is not str or not self.current_title)
            )
        ):
            raise ValueError("live source evidence is malformed")

    def __repr__(self) -> str:
        return "LiveSourceEvidence(<redacted>)"


@dataclass(frozen=True, repr=False)
class LiveProviderEvidence:
    state: LiveProviderState
    evidence_id: str
    provider_title: str | None = None
    work_key: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.state) is not LiveProviderState
            or not _digest(self.evidence_id)
            or (self.work_key is not None and not _digest(self.work_key))
            or (self.state is LiveProviderState.READY)
            != (type(self.provider_title) is str and bool(self.provider_title))
        ):
            raise ValueError("live provider evidence is malformed")

    def __repr__(self) -> str:
        return "LiveProviderEvidence(<redacted>)"


class LiveTitleProposalRepository(Protocol):
    def inspect_source(
        self, locator: EpubEvidenceLocator, mount_configuration_digests: tuple[str, ...]
    ) -> LiveSourceEvidence: ...

    def inspect_provider(
        self, work_key: str, observed_at: datetime
    ) -> LiveProviderEvidence: ...

    def current_live(self, lineage: str) -> LiveTitleProposalSet | None: ...

    def publish_live(
        self,
        proposal_set: LiveTitleProposalSet,
        source: LiveSourceEvidence,
        provider: LiveProviderEvidence,
        mount_configuration_digests: tuple[str, ...],
    ) -> ProposalPersistence: ...


class LiveTitleSourceReader(Protocol):
    def inspect_title_source(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> LiveSourceEvidence: ...


class LiveTitleProviderReader(Protocol):
    def inspect_title_provider(
        self, work_key: str, observed_at: datetime
    ) -> LiveProviderEvidence: ...


class LiveEpubEvidenceValidator(Protocol):
    def current_evidence_is_valid(
        self,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        evidence_id: str,
    ) -> bool: ...


class LiveTitleProviderMatchValidator(Protocol):
    def validate_current_match(
        self,
        reference: IdentityWorkReference,
        observation_id: str,
        current_since: str,
        expires_at: str,
    ) -> IdentityWorkObservation: ...


@dataclass(frozen=True)
class LiveTitleProposalReport:
    persistence: ProposalPersistence

    def __post_init__(self) -> None:
        if type(self.persistence.proposal_set) is not LiveTitleProposalSet:
            raise ValueError("live proposal report is malformed")

    @property
    def proposal_set(self) -> LiveTitleProposalSet:
        value = self.persistence.proposal_set
        assert type(value) is LiveTitleProposalSet
        return value

    def to_data(self) -> dict[str, object]:
        value = self.proposal_set
        manifest = value.manifest
        return {
            "authority": "production-report-only",
            "evidence": {
                "file_index": manifest["file_index"],
                "local_evidence_id": manifest["local_evidence_id"],
                "provider": (
                    "current_match"
                    if manifest["provider_status"] == LiveProviderState.READY.value
                    else "not_available"
                ),
                "snapshot": manifest["snapshot_reference"],
                "target": manifest["target_reference"],
            },
            "lineage": value.lineage,
            "lineage_revision": value.lineage_revision,
            "mode": value.mode.value,
            "persistence": {
                "current": self.persistence.current,
                "current_set_id": self.persistence.current_set_id,
                "history_count": self.persistence.history_count,
                "replayed": self.persistence.replayed,
                "superseded_set_id": self.persistence.superseded_set_id,
            },
            "previous_set_id": value.previous_set_id,
            "production_applicable": value.production_applicable and self.persistence.current,
            "proposals": [proposal.to_data() for proposal in value.proposals],
            "result": value.result.value,
            "schema": LIVE_TITLE_PROPOSAL_SCHEMA,
            "set_id": value.set_id,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_data(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )


def render_live_title_proposal_text(report: LiveTitleProposalReport) -> str:
    if type(report) is not LiveTitleProposalReport:
        raise ValueError("live title proposal text rendering requires a typed report")
    return "\n".join(
        f"{key}="
        + json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        for key, value in report.to_data().items()
    ) + "\n"


class ProposeLiveTitle:
    def __init__(self, repository: LiveTitleProposalRepository) -> None:
        self._repository = repository

    def run(
        self,
        locator: EpubEvidenceLocator,
        mount_configuration_digests: tuple[str, ...],
        *,
        provider_enabled: bool,
        authority_revision: int,
        observed_at: datetime,
    ) -> LiveTitleProposalReport:
        mount_set_digest = mount_configuration_set_digest(mount_configuration_digests)
        source = self._repository.inspect_source(locator, mount_configuration_digests)
        result = TitleProposalResult.SOURCE_NOT_READY
        proposed: str | None = None
        current: str | None = None
        provider = LiveProviderEvidence(
            LiveProviderState.NOT_READY,
            hashlib.sha256(b"hardcover-provider-not-used-v1").hexdigest(),
        )

        selected = _select_local(source)
        if selected is not None:
            local_title, canonical_isbn = selected
            if not provider_enabled:
                result = TitleProposalResult.PROVIDER_NOT_READY
                provider = LiveProviderEvidence(
                    LiveProviderState.NOT_READY,
                    hashlib.sha256(b"hardcover-provider-disabled-v1").hexdigest(),
                )
            else:
                query = validate_private_query(
                    {"isbn": canonical_isbn, "title": local_title}
                )
                work_key = hardcover_identity_work_key(query, authority_revision)
                provider = self._repository.inspect_provider(work_key, observed_at)
                if provider.state is LiveProviderState.NOT_READY:
                    result = TitleProposalResult.PROVIDER_NOT_READY
                elif normalized_title_key(provider.provider_title or "") != normalized_title_key(local_title):
                    result = TitleProposalResult.EVIDENCE_DISAGREEMENT
                elif source.current_title is not None and normalized_title_key(source.current_title) == normalized_title_key(local_title):
                    result = TitleProposalResult.NO_CHANGE
                else:
                    result = TitleProposalResult.NEEDS_REVIEW
                    proposed = local_title
                    current = source.current_title

        lineage = _lineage(locator)
        previous = self._repository.current_live(lineage)
        manifest = _manifest(source, provider, lineage, previous, mount_set_digest)
        if previous is not None and _same_input(previous.manifest, manifest) and previous.result is result:
            proposal_set = previous
        else:
            proposal_set = live_proposal_set_from_manifest(
                manifest=manifest,
                result=result,
                current_value=current,
                proposed_value=proposed,
            )
        # Validate the complete bounded output before the publication transaction.
        proposal_set = LiveTitleProposalSet.from_json(proposal_set.to_json())
        preview = LiveTitleProposalReport(
            ProposalPersistence(
                proposal_set,
                replayed=False,
                current=True,
                current_set_id=proposal_set.set_id,
                superseded_set_id=proposal_set.previous_set_id,
                history_count=proposal_set.lineage_revision,
            )
        )
        preview.to_json()
        render_live_title_proposal_text(preview)
        persistence = self._repository.publish_live(
            proposal_set, source, provider, mount_configuration_digests
        )
        if (
            not persistence.current
            or persistence.current_set_id != proposal_set.set_id
            or persistence.proposal_set != proposal_set
        ):
            raise ValueError("published live proposal is no longer current")
        report = LiveTitleProposalReport(persistence)
        report.to_json()
        return report


def _select_local(source: LiveSourceEvidence) -> tuple[str, str] | None:
    inspection = source.inspection
    if (
        source.state is not LiveSourceState.READY
        or inspection is None
        or inspection.outcome is not EpubInspectionOutcome.SUCCESS
    ):
        return None
    titles = {
        fact.observed_value
        for fact in inspection.facts
        if fact.field is EpubInspectionField.TITLE
    }
    if len(titles) != 1:
        return None
    canonical_isbns: set[str] = set()
    for fact in inspection.facts:
        if (
            fact.field is not EpubInspectionField.ISBN
            or fact.checksum not in {"valid_isbn_10", "valid_isbn_13"}
            or fact.media != "ebook"
        ):
            continue
        if fact.canonical_value is None:
            return None
        try:
            canonical_isbns.add(isbn_identity(fact.canonical_value).canonical_isbn_13)
        except (TypeError, ValueError):
            return None
    if len(canonical_isbns) != 1:
        return None
    return next(iter(titles)), next(iter(canonical_isbns))


def _lineage(locator: EpubEvidenceLocator) -> str:
    digest = hashlib.sha256(
        f"live-title-proposal-v1\0{locator.target_reference}\0{locator.file_index}".encode()
    ).hexdigest()
    return "live-title-" + digest[:48]


def _manifest(
    source: LiveSourceEvidence,
    provider: LiveProviderEvidence,
    lineage: str,
    previous: LiveTitleProposalSet | None,
    mount_set_digest: str,
) -> dict[str, object]:
    revision = 1 if previous is None else previous.lineage_revision + 1
    return {
        "comparison_version": TITLE_COMPARISON_VERSION,
        "file_index": source.locator.file_index,
        "inventory_fingerprint": source.inventory_fingerprint,
        "lineage": lineage,
        "lineage_revision": revision,
        "local_evidence_id": source.evidence_id,
        "mode": ProposalExecutionMode.PRODUCTION_REPORT_ONLY.value,
        "mount_configuration_digest": source.mount_configuration_digest,
        "mount_configuration_set_digest": mount_set_digest,
        "policy_version": LIVE_TITLE_PROPOSAL_POLICY_VERSION,
        "previous_set_id": None if previous is None else previous.set_id,
        "proposal_schema": LIVE_TITLE_PROPOSAL_SCHEMA,
        "provider_evidence_id": provider.evidence_id,
        "provider_status": provider.state.value,
        "snapshot_reference": source.locator.snapshot_reference,
        "source_fingerprint": source.source_fingerprint,
        "target_reference": source.locator.target_reference,
    }


def _same_input(old: object, new: dict[str, object]) -> bool:
    if not hasattr(old, "items"):
        return False
    ignored = {"lineage_revision", "previous_set_id"}
    return {
        key: value for key, value in old.items() if key not in ignored  # type: ignore[union-attr]
    } == {key: value for key, value in new.items() if key not in ignored}


def mount_configuration_set_digest(values: tuple[str, ...]) -> str:
    if (
        type(values) is not tuple
        or not values
        or values != tuple(sorted(set(values)))
        or any(not _digest(value) for value in values)
    ):
        raise ValueError("live title proposal mount set is invalid")
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and not set(value) - set("0123456789abcdef")


def _evidence_id(value: object) -> bool:
    return type(value) is str and value.startswith("evidence_") and len(value) == 41


__all__ = [
    "LiveProviderEvidence",
    "LiveProviderState",
    "LiveSourceEvidence",
    "LiveSourceState",
    "LiveTitleProposalReport",
    "LiveTitleProviderReader",
    "LiveTitleProviderMatchValidator",
    "LiveTitleSourceReader",
    "LiveEpubEvidenceValidator",
    "ProposeLiveTitle",
    "mount_configuration_set_digest",
    "render_live_title_proposal_text",
]
