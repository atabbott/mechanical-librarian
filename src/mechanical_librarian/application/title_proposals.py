"""Closed synthetic-preview title proposal orchestration for F09a and F09b."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    MetadataCandidate,
    MetadataProvider,
    OperationContext,
    Page,
    PageRequest,
    PortResult,
    ProviderFact,
    ProviderFactCode,
    ProviderQuery,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.domain.evidence import FieldValue
from mechanical_librarian.domain.identifiers import SchemaVersion
from mechanical_librarian.domain.inventory import FieldName, MediaType
from mechanical_librarian.domain.local_evidence import (
    LocalEvidenceField,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
    ObservationApplicability,
)
from mechanical_librarian.domain.title_proposals import (
    ProposalExecutionMode,
    SyntheticTitleState,
    TITLE_COMPARISON_VERSION,
    TITLE_PROPOSAL_POLICY_VERSION,
    TITLE_PROPOSAL_SCHEMA,
    TitleProposalResult,
    TitleProposalSet,
    normalized_title_key,
    proposal_set_from_manifest,
)


SCENARIO_REVISION = 1
GENERATOR_VERSION = "synthetic-epub-generator-v1"
FIXTURE_VERSION = "synthetic-title-scenarios-v1"
PROVIDER_SCHEMA = SchemaVersion("synthetic-title-query-v1")
SYNTHETIC_LINEAGE = "synthetic-title-preview"
HARDCOVER_POC_LINEAGE = "hardcover-poc"
HARDCOVER_POC_PROVIDER_VERSION = "hardcover-title-poc-v1"


class ProviderPreviewProfile(StrEnum):
    SYNTHETIC = "synthetic"
    HARDCOVER_POC = "hardcover_poc"


class TitleProposalRunError(RuntimeError):
    """A bounded synthetic preview failed before or during publication."""


@dataclass(frozen=True)
class SyntheticScenario:
    name: str
    lineage_revision: int = 1
    current_state: SyntheticTitleState = SyntheticTitleState.LOADED
    current_title: str | None = "Existing Synthetic Title"
    local_titles: tuple[str, ...] = ("A Synthetic Book",)
    stale: bool = False


SCENARIOS: dict[str, SyntheticScenario] = {
    "agree-empty": SyntheticScenario("agree-empty", current_title=None),
    "agree-different": SyntheticScenario("agree-different"),
    "current-equivalent": SyntheticScenario(
        "current-equivalent", current_title="  a\u00a0SYNTHETIC\tbook  "
    ),
    "current-missing": SyntheticScenario(
        "current-missing", current_state=SyntheticTitleState.MISSING, current_title=None
    ),
    "current-unavailable": SyntheticScenario(
        "current-unavailable", current_state=SyntheticTitleState.UNAVAILABLE, current_title=None
    ),
    "local-provider-conflict": SyntheticScenario("local-provider-conflict"),
    "f06-missing": SyntheticScenario("f06-missing", local_titles=()),
    "f06-ambiguous": SyntheticScenario(
        "f06-ambiguous", local_titles=("First Synthetic Title", "Second Synthetic Title"),
    ),
    "f06-unusable": SyntheticScenario(
        "f06-unusable", local_titles=("x" * 600,)
    ),
    "provider-disabled": SyntheticScenario("provider-disabled"),
    "provider-failed": SyntheticScenario("provider-failed"),
    "provider-empty": SyntheticScenario("provider-empty"),
    "provider-no-match": SyntheticScenario("provider-no-match"),
    "stale-input": SyntheticScenario("stale-input", stale=True),
    "supersede-before": SyntheticScenario(
        "supersede-before", current_title="Original Synthetic Title",
        local_titles=("First Proposed Title",),
    ),
    "supersede-after": SyntheticScenario(
        "supersede-after", lineage_revision=2, current_title="Original Synthetic Title",
        local_titles=("Second Proposed Title",),
    ),
}


@dataclass(frozen=True)
class _ProviderObservation:
    outcome: str
    result: TitleProposalResult | None
    candidate: MetadataCandidate | None
    failure_kind: FailureKind | None = None


@dataclass(frozen=True)
class ProposalPersistence:
    proposal_set: TitleProposalSet
    replayed: bool
    current: bool
    current_set_id: str
    superseded_set_id: str | None
    history_count: int

    def __post_init__(self) -> None:
        if (
            type(self.proposal_set) is not TitleProposalSet
            or type(self.replayed) is not bool
            or type(self.current) is not bool
            or type(self.current_set_id) is not str
            or not self.current_set_id.startswith("ps-")
            or (self.superseded_set_id is not None and type(self.superseded_set_id) is not str)
            or type(self.history_count) is not int
            or self.history_count < 1
        ):
            raise ValueError("proposal persistence projection is invalid")
        if self.current and self.current_set_id != self.proposal_set.set_id:
            raise ValueError("current proposal projection disagrees with its pointer")


class TitleProposalRepository(Protocol):
    def current(self, lineage: str) -> TitleProposalSet | None: ...

    def store(self, proposal_set: TitleProposalSet) -> ProposalPersistence: ...


@dataclass(frozen=True)
class SyntheticTitleProposalReport:
    persistence: ProposalPersistence

    @property
    def proposal_set(self) -> TitleProposalSet:
        return self.persistence.proposal_set

    def to_data(self) -> dict[str, object]:
        value = self.proposal_set.to_data()
        value["persistence"] = {
            "current": self.persistence.current,
            "history_count": self.persistence.history_count,
            "replayed": self.persistence.replayed,
            "current_set_id": self.persistence.current_set_id,
            "superseded_set_id": self.persistence.superseded_set_id,
        }
        value["authority"] = "synthetic-report-only"
        return value

    def to_json(self) -> str:
        return json.dumps(
            self.to_data(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )


def render_title_proposal_text(report: SyntheticTitleProposalReport) -> str:
    if type(report) is not SyntheticTitleProposalReport:
        raise ValueError("title proposal text rendering requires a typed report")
    lines: list[str] = []
    for key, value in report.to_data().items():
        lines.append(
            f"{key}="
            + json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
    return "\n".join(lines) + "\n"


def scenario_named(name: str) -> SyntheticScenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise TitleProposalRunError("synthetic scenario is unsupported") from None


class ProposeSyntheticTitle:
    def __init__(
        self,
        provider: MetadataProvider | None,
        repository: TitleProposalRepository,
        *,
        profile: ProviderPreviewProfile = ProviderPreviewProfile.SYNTHETIC,
        unavailable_kind: FailureKind | None = None,
    ) -> None:
        if type(profile) is not ProviderPreviewProfile:
            raise ValueError("title proposal provider profile is invalid")
        if profile is ProviderPreviewProfile.SYNTHETIC and (
            provider is None or unavailable_kind is not None
        ):
            raise ValueError("synthetic title preview requires its deterministic provider")
        if provider is not None and unavailable_kind is not None:
            raise ValueError("provider availability state is ambiguous")
        if unavailable_kind is not None and type(unavailable_kind) is not FailureKind:
            raise ValueError("provider availability failure is invalid")
        self._provider = provider
        self._repository = repository
        self._profile = profile
        self._unavailable_kind = unavailable_kind

    def run(
        self,
        scenario: SyntheticScenario,
        local_report: LocalEvidenceReport,
        *,
        epub_digest: str,
        opaque_file_identity: str,
    ) -> SyntheticTitleProposalReport:
        if type(scenario) is not SyntheticScenario or type(local_report) is not LocalEvidenceReport:
            raise TitleProposalRunError("synthetic proposal input is invalid")
        if local_report.outcome is not LocalEvidenceOutcome.SUCCESS:
            raise TitleProposalRunError("local evidence extraction failed")
        if local_report.source_fingerprint is None or local_report.source_fingerprint.digest != epub_digest:
            raise TitleProposalRunError("local evidence source identity changed")
        local_result, selected = _select_local_title(local_report)
        if local_result is None and selected is not None:
            if not _usable_local_title(selected):
                local_result = TitleProposalResult.F06_UNUSABLE

        provider_candidate: MetadataCandidate | None = None
        provider_state = "not_invoked"
        provider_result: TitleProposalResult | None = None
        provider_failure_kind: FailureKind | None = None
        if local_result is None and selected is not None:
            provider = self._lookup_provider(
                selected.normalized_value or selected.raw_value
            )
            provider_state = provider.outcome
            provider_result = provider.result
            provider_candidate = provider.candidate
            provider_failure_kind = provider.failure_kind

        result = local_result
        proposed_value: str | None = None
        current_value: str | None = None
        if result is None:
            assert selected is not None
            local_value = selected.normalized_value
            if scenario.current_state is SyntheticTitleState.MISSING:
                result = TitleProposalResult.CURRENT_MISSING
            elif scenario.current_state is SyntheticTitleState.UNAVAILABLE:
                result = TitleProposalResult.CURRENT_UNAVAILABLE
            elif scenario.stale:
                result = TitleProposalResult.STALE_INPUT
            elif provider_result is not None:
                result = provider_result
            elif provider_candidate is None:
                raise TitleProposalRunError("provider output is malformed")
            elif len(provider_candidate.value.values) != 1:
                raise TitleProposalRunError("provider output is ambiguous")
            elif not _usable_provider_title(provider_candidate.value.values[0]):
                result = TitleProposalResult.PROVIDER_NO_MATCH
            elif normalized_title_key(provider_candidate.value.values[0]) != normalized_title_key(local_value):
                result = TitleProposalResult.LOCAL_PROVIDER_CONFLICT
            elif scenario.current_title is not None and normalized_title_key(scenario.current_title) == normalized_title_key(local_value):
                result = TitleProposalResult.NO_CHANGE
            else:
                result = TitleProposalResult.NEEDS_REVIEW
                proposed_value = local_value
                current_value = scenario.current_title

        provider_data = _provider_manifest(
            self._profile,
            provider_candidate,
            provider_state,
            provider_failure_kind,
        )
        lineage = _proposal_lineage(scenario, self._profile, provider_data)
        previous = self._repository.current(lineage)
        previous_set_id = None
        if scenario.lineage_revision == 2:
            if (
                previous is not None
                and previous.lineage_revision == 1
                and previous.scenario == "supersede-before"
            ):
                previous_set_id = previous.set_id
            elif (
                previous is not None
                and previous.lineage_revision == 2
                and previous.scenario == "supersede-after"
                and previous.previous_set_id is not None
            ):
                previous_set_id = previous.previous_set_id
            else:
                raise TitleProposalRunError("synthetic supersession predecessor is missing")
        elif previous is not None and scenario.name.startswith("supersede-"):
            if previous.set_id != previous_set_id and previous.lineage_revision >= scenario.lineage_revision:
                # Replay remains valid and the repository must not rewind its pointer.
                previous_set_id = None

        manifest = _manifest(
            scenario, local_report, selected, provider_data, lineage,
            epub_digest, opaque_file_identity, previous_set_id,
        )
        proposal_set = proposal_set_from_manifest(
            manifest=manifest,
            result=result,
            current_value=current_value,
            proposed_value=proposed_value,
            previous_set_id=previous_set_id,
        )
        # Complete all bounded render/serialization validation before publication.
        proposal_set = TitleProposalSet.from_json(proposal_set.to_json())
        preview = SyntheticTitleProposalReport(
            ProposalPersistence(
                proposal_set,
                replayed=False,
                current=True,
                current_set_id=proposal_set.set_id,
                superseded_set_id=previous_set_id,
                history_count=scenario.lineage_revision,
            )
        )
        preview.to_json()
        render_title_proposal_text(preview)
        persistence = self._repository.store(proposal_set)
        if type(persistence) is not ProposalPersistence or persistence.proposal_set != proposal_set:
            raise TitleProposalRunError("proposal persistence readback failed")
        report = SyntheticTitleProposalReport(persistence)
        report.to_json()
        render_title_proposal_text(report)
        return report

    def _lookup_provider(self, local_title: str) -> _ProviderObservation:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        live_clock = self._profile is ProviderPreviewProfile.HARDCOVER_POC
        observed_now = datetime.now(timezone.utc) if live_clock else now
        context = OperationContext(
            Deadline(observed_now + timedelta(seconds=5)),
            CancellationProbe(CancellationId("synthetic-preview"), lambda: False),
            ResourceBounds(16, 65_536),
            TimeProbe(lambda: datetime.now(timezone.utc) if live_clock else now),
        )
        query = ProviderQuery(
            MediaType.EBOOK,
            FieldName.TITLE,
            (ProviderFact(ProviderFactCode.TITLE, (local_title,)),),
            PROVIDER_SCHEMA,
        )
        if self._provider is None:
            if self._unavailable_kind is None:
                return _ProviderObservation(
                    "disabled", TitleProposalResult.PROVIDER_DISABLED, None
                )
            return _ProviderObservation(
                "failed",
                TitleProposalResult.PROVIDER_FAILED,
                None,
                self._unavailable_kind,
            )
        page_request = PageRequest(
            None, 1 if self._profile is ProviderPreviewProfile.HARDCOVER_POC else 2
        )
        result = self._provider.lookup(query, page_request, context)
        if type(result) is not PortResult:
            raise TitleProposalRunError("provider output is malformed")
        if result.failure is not None:
            if self._profile is ProviderPreviewProfile.SYNTHETIC:
                mapped = {
                    FailureKind.UNSUPPORTED: ("disabled", TitleProposalResult.PROVIDER_DISABLED),
                    FailureKind.UNAVAILABLE: ("failed", TitleProposalResult.PROVIDER_FAILED),
                    FailureKind.NOT_FOUND: ("no_match", TitleProposalResult.PROVIDER_NO_MATCH),
                }.get(result.failure.kind)
                if mapped is not None:
                    return _ProviderObservation(mapped[0], mapped[1], None)
                raise TitleProposalRunError("provider lookup failed unexpectedly")
            if result.failure.kind is FailureKind.INVALID_REQUEST:
                raise TitleProposalRunError("provider rejected a valid application request")
            if result.failure.kind is FailureKind.NOT_FOUND:
                return _ProviderObservation(
                    "no_match", TitleProposalResult.PROVIDER_NO_MATCH, None
                )
            return _ProviderObservation(
                "failed",
                TitleProposalResult.PROVIDER_FAILED,
                None,
                result.failure.kind,
            )
        if type(result.value) is not Page or result.value.next_cursor is not None:
            raise TitleProposalRunError("provider output is malformed")
        if not all(type(item) is MetadataCandidate for item in result.value.items):
            raise TitleProposalRunError("provider output is malformed")
        if len(result.value.items) == 0:
            return _ProviderObservation(
                "empty", TitleProposalResult.PROVIDER_EMPTY, None
            )
        if len(result.value.items) != 1:
            raise TitleProposalRunError("provider output is ambiguous")
        candidate = result.value.items[0]
        if candidate.field is not FieldName.TITLE:
            raise TitleProposalRunError("provider output has the wrong field")
        return _ProviderObservation("candidate", None, candidate)


def _select_local_title(report: LocalEvidenceReport):
    titles = tuple(
        item
        for item in report.observations
        if item.field is LocalEvidenceField.TITLE
        and item.applicability is not ObservationApplicability.INAPPLICABLE
    )
    if not titles:
        return TitleProposalResult.F06_MISSING, None
    if len(titles) != 1:
        return TitleProposalResult.F06_AMBIGUOUS, None
    return None, titles[0]


def _usable_local_title(observation) -> bool:
    value = observation.normalized_value
    return (
        type(value) is str
        and 0 < len(value) <= 512
        and bool(normalized_title_key(value))
        and not value.startswith(("unsafe-source-text-sha256:", "oversized-source-text-sha256:"))
        and not observation.raw_value.startswith(("unsafe-source-text-sha256:", "oversized-source-text-sha256:"))
    )


def _usable_provider_title(value: str) -> bool:
    return (
        0 < len(value) <= 512
        and bool(normalized_title_key(value))
        and not value.startswith(("unsafe-source-text-sha256:", "oversized-source-text-sha256:"))
    )


def _manifest(
    scenario: SyntheticScenario,
    report: LocalEvidenceReport,
    selected,
    provider_data: dict[str, object],
    lineage: str,
    epub_digest: str,
    opaque_file_identity: str,
    previous_set_id: str | None,
) -> dict[str, object]:
    snapshot_value = {
        "external_item": "synthetic-item",
        "metadata": {"title": scenario.current_title},
        "state": scenario.current_state.value,
    }
    snapshot_digest = hashlib.sha256(
        json.dumps(snapshot_value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    current_digest = "0" * 64 if scenario.stale else snapshot_digest
    citation = None
    chosen_observation = None
    if selected is not None:
        citation = {
            "expanded_name": selected.citation.expanded_name,
            "resource_identity": selected.citation.resource_identity,
            "section": selected.citation.section.value,
            "selector": selected.citation.selector,
            "source_ordinal": selected.citation.source_ordinal,
        }
        chosen_observation = {
            "applicability": selected.applicability.value,
            "compatibility_version": selected.compatibility_version.value,
            "extractor_version": selected.extractor_version.value,
            "normalized_value": selected.normalized_value,
            "raw_value": selected.raw_value,
            "schema_version": selected.schema_version.value,
        }
    return {
        "comparison_version": TITLE_COMPARISON_VERSION,
        "epub_digest": epub_digest,
        "f06": {
            "chosen_citation": citation,
            "chosen_observation": chosen_observation,
            "extractor_versions": sorted({item.extractor_version.value for item in report.observations}),
            "report_identity": hashlib.sha256(report.to_json().encode("utf-8")).hexdigest(),
            "report_schema": report.schema_version.value,
            "source_fingerprint": report.source_fingerprint.digest,
        },
        "fixture_version": FIXTURE_VERSION,
        "generator_version": GENERATOR_VERSION,
        "lineage": lineage,
        "lineage_revision": scenario.lineage_revision,
        "mode": ProposalExecutionMode.SYNTHETIC_PREVIEW.value,
        "opaque_file_identity": opaque_file_identity,
        "policy_version": TITLE_PROPOSAL_POLICY_VERSION,
        "previous_set_id": previous_set_id,
        "proposal_schema": TITLE_PROPOSAL_SCHEMA,
        "provider": provider_data,
        "scenario": scenario.name,
        "scenario_revision": SCENARIO_REVISION,
        "snapshot": {
            "current_fingerprint": current_digest,
            "fingerprint": snapshot_digest,
            "id": f"synthetic-snapshot-{scenario.lineage_revision}",
            "schema": "synthetic-library-snapshot-v1",
            "shape": snapshot_value,
        },
        "unicode_version": unicodedata.unidata_version,
    }


def _provider_manifest(
    profile: ProviderPreviewProfile,
    provider_candidate: MetadataCandidate | None,
    provider_state: str,
    failure_kind: FailureKind | None,
) -> dict[str, object]:
    provider_data: dict[str, object] = {
        "candidate": None,
        "outcome": provider_state,
        "version": (
            "synthetic-title-provider-v1"
            if profile is ProviderPreviewProfile.SYNTHETIC
            else HARDCOVER_POC_PROVIDER_VERSION
        ),
    }
    if profile is ProviderPreviewProfile.HARDCOVER_POC:
        provider_data["failure_kind"] = (
            None if failure_kind is None else failure_kind.value
        )
    if provider_candidate is not None:
        provider_data["candidate"] = {
            "correlation_id": provider_candidate.provenance.correlation_id,
            "field": provider_candidate.field.value,
            "fingerprint": provider_candidate.provenance.fingerprint.digest,
            "source_id": provider_candidate.provenance.source_id,
            "values": list(provider_candidate.value.values),
        }
    return provider_data


def _proposal_lineage(
    scenario: SyntheticScenario,
    profile: ProviderPreviewProfile,
    provider_data: dict[str, object],
) -> str:
    if profile is ProviderPreviewProfile.SYNTHETIC:
        return _scenario_lineage(scenario)
    observation = json.dumps(
        provider_data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{HARDCOVER_POC_LINEAGE}-{hashlib.sha256(observation).hexdigest()[:48]}"


def _scenario_lineage(scenario: SyntheticScenario) -> str:
    if scenario.name in {"supersede-before", "supersede-after"}:
        return f"{SYNTHETIC_LINEAGE}-supersession"
    return f"{SYNTHETIC_LINEAGE}-{scenario.name}"


__all__ = [
    "FIXTURE_VERSION", "GENERATOR_VERSION", "HARDCOVER_POC_LINEAGE",
    "HARDCOVER_POC_PROVIDER_VERSION", "ProposalPersistence", "ProposeSyntheticTitle",
    "ProviderPreviewProfile", "SCENARIOS", "SCENARIO_REVISION", "SYNTHETIC_LINEAGE", "SyntheticScenario",
    "SyntheticTitleProposalReport", "TitleProposalRepository", "TitleProposalRunError",
    "render_title_proposal_text", "scenario_named",
]
