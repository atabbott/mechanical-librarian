"""Closed, non-composed provider-lane successor construction.

This is deliberately an application seam only.  F24 activation owns any
claiming, scheduling, or composition of it; this unit merely makes the
otherwise awkward successor transaction testable without granting execution.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from mechanical_librarian.application.configuration import (
    MediaMountConfiguration,
    ReportLimits,
)
from mechanical_librarian.application.metadata_report import (
    CurrentnessError,
    EpubInspector,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataProvider,
    MetadataReportError,
    ProviderBinding,
    ProviderLaneResult,
    ReportRepository,
    TargetReader,
    merge_provider_diagnostics,
    metadata_discovery_plan,
    provider_observation,
    require_report_current,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderRequestAccounting,
    ProviderStatus,
    ReportValueError,
)


class ProviderRetrySuccessorError(MetadataReportError):
    """Reject a stale, rebound, or non-retryable internal retry request."""


class ProviderRetrySharedFailure(ProviderRetrySuccessorError):
    """A bounded provider-health result that must not publish a successor."""

    def __init__(self, failure: str, retry_after: int | None = None) -> None:
        if failure not in {
            "authentication", "authorization", "configuration",
            "rate_limited", "unavailable", "incomplete",
        } or (retry_after is not None and (
            type(retry_after) is not int or not 1 <= retry_after <= 86_400
        )):
            raise ProviderRetrySuccessorError("provider retry failure is invalid")
        super().__init__("provider retry shared failure")
        self.failure = failure
        self.retry_after = retry_after


class ProviderRetryDisabled(ProviderRetrySuccessorError):
    """The exact configured provider was disabled before recovery egress."""


_RETRYABLE = frozenset({
    ProviderStatus.RATE_LIMITED,
    ProviderStatus.UNAVAILABLE,
    ProviderStatus.INCOMPLETE,
})
_GENERATION_WOKEN = frozenset({
    ProviderStatus.AUTHENTICATION,
    ProviderStatus.AUTHORIZATION,
})


@dataclass(frozen=True, slots=True)
class ConfiguredProviderRetry:
    """The current configuration identity required to replay one provider."""

    binding: ProviderBinding
    adapter: str
    generation: int

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not ProviderBinding
            or not self.binding.enabled
            or self.binding.authority != self.adapter
        ):
            raise ProviderRetrySuccessorError("provider retry configuration is invalid")
        if (
            not isinstance(self.adapter, str)
            or not self.adapter
            or type(self.generation) is not int
            or self.generation < 1
        ):
            raise ProviderRetrySuccessorError("provider retry configuration is invalid")


ProviderRetryResolver = Callable[[str, str, str, str, int], ConfiguredProviderRetry | None]


class GenerateProviderRetrySuccessor:
    """Publish one exact-current report successor from selected failed lanes.

    No composition calls this class in this increment.  The explicit adapter
    and generation arguments make a future recovery claimant prove that its
    leased configuration identity has not been rebound before any dispatch.
    """

    def __init__(
        self,
        *,
        target: TargetReader,
        inspector: EpubInspector,
        repository_factory: Callable[[], ReportRepository],
        provider_resolver: ProviderRetryResolver,
        mount: MediaMountConfiguration | None,
        limits: ReportLimits,
        # The staging result is deliberately discarded, so the callback's
        # return type is unconstrained rather than required to be None.
        after_publication: Callable[[ReportRepository, MetadataReport], object],
        heartbeat: Callable[[], None] = lambda: None,
    ) -> None:
        self._target = target
        self._inspector = inspector
        self._repository_factory = repository_factory
        if (
            not callable(provider_resolver) or not callable(after_publication)
            or not callable(heartbeat)
        ):
            raise ProviderRetrySuccessorError("provider retry composition is invalid")
        self._provider_resolver = provider_resolver
        self._mount = mount
        self._limits = limits
        self._after_publication = after_publication
        self._heartbeat = heartbeat

    def run(
        self,
        source_report_id: str,
        lane_identities: tuple[str, ...],
        *,
        provider_name: str,
        adapter: str,
        generation: int,
        generation_woken: bool = False,
    ) -> str:
        if (
            not isinstance(source_report_id, str)
            or not isinstance(provider_name, str)
            or not provider_name
            or type(lane_identities) is not tuple
            or not lane_identities
            or tuple(sorted(set(lane_identities))) != lane_identities
            or type(generation_woken) is not bool
        ):
            raise ProviderRetrySuccessorError("provider retry request is invalid")
        repository = self._repository_factory()
        try:
            source = self._source(repository, source_report_id)
            current = self._current_source(repository, source)
            plan = self._plan(source)
            provider = self._provider(source, provider_name, adapter, generation)
            original = self._observation(source, provider)
            selected = self._selected(
                plan, original, lane_identities,
                generation_woken=generation_woken,
            )
            # This is the final external preflight: no factory or provider call
            # may follow a changed source package or rebound configuration.
            if not require_report_current(
                self._target, self._inspector, self._mount, self._limits, source
            ) or self._current_source(repository, source) != current:
                raise CurrentnessError("target or media changed")
            live_provider = self._provider(source, provider_name, adapter, generation)
            if self._provider_identity(live_provider) != self._provider_identity(provider):
                raise ProviderRetrySuccessorError("provider retry configuration changed")
            self._heartbeat()
            replacement, retry_after = self._observe(
                live_provider, plan, selected, deadline=time.monotonic() + 60.0
            )
            self._heartbeat()
            # A shared provider condition is not evidence for a successor.
            # Leave the predecessor current so the recovery claim can back off
            # before any sibling is contacted.
            shared = self._shared_failure(replacement)
            if shared is not None:
                raise ProviderRetrySharedFailure(
                    shared,
                    retry_after if shared == "rate_limited" else None,
                )
            merged = self._merged(plan, original, selected, replacement)
            successor = MetadataReport.create(
                target=source.target, file=source.file,
                local_claims=source.local_claims, local_reasons=source.local_reasons,
                providers=tuple(sorted(
                    (merged if value.provider == original.provider else value for value in source.providers),
                    key=lambda value: value.provider,
                )),
                cover_sources=source.cover_sources,
            )

            def currentness() -> bool:
                if not require_report_current(self._target, self._inspector, self._mount, self._limits, source):
                    return False
                return (
                    self._current_source(repository, source) == current
                    and self._provider_identity(
                        self._provider(source, provider_name, adapter, generation)
                    ) == self._provider_identity(provider)
                )

            def stage(bound: ReportRepository, report: MetadataReport) -> None:
                self._after_publication(bound, report)

            published = repository.publish(
                successor,
                currentness,
                source.report_id,
                stage,
                expected_package=current[1],
            )
            if published != successor.to_json() or repository.get(successor.report_id) != published:
                raise ProviderRetrySuccessorError("provider retry publication failed")
            return published
        except CurrentnessError:
            raise
        except ProviderRetrySuccessorError:
            raise
        except (ReportValueError, MetadataReportError) as error:
            raise ProviderRetrySuccessorError("provider retry source is invalid") from error
        finally:
            repository.close()

    def _source(self, repository: ReportRepository, report_id: str) -> MetadataReport:
        try:
            return MetadataReport.from_json(repository.get(report_id))
        except (KeyError, ReportValueError, MetadataReportError) as error:
            raise ProviderRetrySuccessorError("provider retry source is invalid") from error

    def _current_source(
        self, repository: ReportRepository, source: MetadataReport
    ) -> tuple[str, tuple[str | None, str, str | None, int | None, str]]:
        document = repository.current_optional(
            source.target.connection, source.target.library, source.target.item, source.file.identity
        )
        package = repository.current_review_package(
            source.target.connection, source.target.library, source.target.item
        )
        if (
            document is None
            or MetadataReport.from_json(document).report_id != source.report_id
            or package is None
            or package[1] != source.report_id
        ):
            raise CurrentnessError("target or media changed")
        return document, package

    def _plan(self, source: MetadataReport) -> MetadataDiscoveryPlan:
        snapshot = self._target.read(source.target.item)
        if snapshot.media_kind != "ebook" or snapshot.title != source.target.current_title or snapshot.fields != source.target.fields:
            raise CurrentnessError("target or media changed")
        plan = metadata_discovery_plan(snapshot, source.local_claims)
        if plan.overflow:
            raise ProviderRetrySuccessorError("provider retry source is invalid")
        return plan

    def _provider(self, source: MetadataReport, name: str, adapter: str, generation: int) -> ConfiguredProviderRetry:
        value = self._provider_resolver(
            source.target.connection, source.target.library,
            name, adapter, generation,
        )
        if (
            type(value) is not ConfiguredProviderRetry
            or value.binding.name != name
            or value.adapter != adapter
            or value.generation != generation
        ):
            raise ProviderRetrySuccessorError("provider retry configuration changed")
        return value

    @staticmethod
    def _provider_identity(value: ConfiguredProviderRetry) -> tuple[str, bool, str | None, str, int]:
        """Compare persisted configuration identity, never the factory callable."""
        return (
            value.binding.name,
            value.binding.enabled,
            value.binding.authority,
            value.adapter,
            value.generation,
        )

    def _observation(self, source: MetadataReport, provider: ConfiguredProviderRetry) -> ProviderObservation:
        matches = tuple(value for value in source.providers if value.provider == provider.binding.name)
        if len(matches) != 1 or matches[0].authority != provider.binding.authority:
            raise ProviderRetrySuccessorError("provider retry configuration changed")
        return matches[0]

    def _selected(
        self, plan: MetadataDiscoveryPlan, original: ProviderObservation,
        identities: tuple[str, ...], *, generation_woken: bool = False,
    ) -> tuple[MetadataDiscoveryLane, ...]:
        planned = {lane.identity: lane for lane in plan.lanes}
        observed = {lane.identity: lane for lane in original.lanes}
        if set(planned) != set(observed):
            raise ProviderRetrySuccessorError("provider retry source is invalid")
        selected = tuple(planned[identity] for identity in identities if identity in planned)
        permitted = _RETRYABLE | (
            _GENERATION_WOKEN if generation_woken else frozenset()
        )
        if len(selected) != len(identities) or any(observed[lane.identity].status not in permitted for lane in selected):
            raise ProviderRetrySuccessorError("provider retry lane is invalid")
        return selected

    def _observe(
        self,
        provider: ConfiguredProviderRetry,
        plan: MetadataDiscoveryPlan,
        selected: tuple[MetadataDiscoveryLane, ...],
        *,
        deadline: float,
    ) -> tuple[ProviderObservation, int | None]:
        try:
            client: MetadataProvider = provider.binding.factory()
            observation = client.observe(
                MetadataDiscoveryPlan(plan.local_claims, selected), deadline=deadline
            )
        except Exception as error:
            # Provider construction and the remote observation are one dispatch
            # across a protocol that declares no error type. Either failing
            # means the retry produced no observation, and the checks below --
            # which reject a result that disagrees with the binding -- can only
            # run on a dispatch that returned. The cause is chained, and the
            # caller classifies it from the chain rather than from this code.
            raise ProviderRetrySuccessorError("provider retry dispatch failed") from error
        if type(observation) is ProviderObservation and observation.authority is None:
            observation = replace(observation, authority=provider.binding.authority)
        if observation.provider != provider.binding.name or observation.authority != provider.binding.authority:
            raise ProviderRetrySuccessorError("provider retry result is invalid")
        if {lane.identity for lane in observation.lanes} != {lane.identity for lane in selected}:
            raise ProviderRetrySuccessorError("provider retry result is invalid")
        if (
            observation.status is ProviderStatus.MALFORMED
            or any(lane.status is ProviderStatus.MALFORMED for lane in observation.lanes)
        ):
            raise ProviderRetrySuccessorError("provider retry result is invalid")
        return observation, self._retry_after(client)

    @staticmethod
    def _shared_failure(observation: ProviderObservation) -> str | None:
        """Classify provider health before any successor can be published."""
        statuses = (observation.status, *(lane.status for lane in observation.lanes))
        for status in (
            ProviderStatus.AUTHENTICATION,
            ProviderStatus.AUTHORIZATION,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.INCOMPLETE,
        ):
            if status in statuses:
                return status.value
        return None

    @staticmethod
    def _retry_after(provider: MetadataProvider) -> int | None:
        """Read only a canonical final bounded delay from adapter-safe evidence."""
        try:
            # Adapter-optional evidence the MetadataProvider contract
            # deliberately does not require; a provider without the attribute
            # is handled by the AttributeError branch below.
            records = provider.attempt_records  # type: ignore[attr-defined]  # reason: optional adapter evidence, guarded by the except clause
        except (AttributeError, TypeError, ValueError):
            return None
        if type(records) is not tuple or not records:
            return None
        record = records[-1]
        try:
            category = record.category
            delay = record.delay_seconds
            outcome = record.outcome
        except (AttributeError, TypeError, ValueError):
            return None
        if category != "http_429" or outcome not in {"exhausted", "terminal"}:
            return None
        if type(delay) not in {int, float} or isinstance(delay, bool):
            return None
        try:
            integer = int(delay)
        except (OverflowError, ValueError):
            return None
        if float(integer) != float(delay) or not 1 <= integer <= 86_400:
            return None
        return integer

    def _merged(
        self,
        plan: MetadataDiscoveryPlan,
        original: ProviderObservation,
        selected: tuple[MetadataDiscoveryLane, ...],
        replacement: ProviderObservation,
    ) -> ProviderObservation:
        selected_ids = {lane.identity for lane in selected}
        old_candidates = self._canonical_candidates(original.candidates)
        new_candidates = self._canonical_candidates(replacement.candidates)
        old_lanes = self._canonical_lanes(original.lanes)
        new_lanes = self._canonical_lanes(replacement.lanes)

        aggregate_accessed_at = replacement.accessed_at or original.accessed_at

        def result(lane: MetadataDiscoveryLane) -> ProviderLaneResult:
            observation = new_lanes[lane.identity] if lane.identity in selected_ids else old_lanes[lane.identity]
            candidates = new_candidates if lane.identity in selected_ids else old_candidates
            evidence = replacement if lane.identity in selected_ids else original
            overflow = (
                evidence.edition_enrichment_overflow
                if lane.kind.value == "work"
                and observation.status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
                else None
            )
            return ProviderLaneResult(
                lane,
                observation.status,
                tuple(candidates[item.edition_identity] for item in observation.candidates),
                aggregate_accessed_at,
                overflow,
            )

        merged = provider_observation(
            original.provider, tuple(result(lane) for lane in plan.lanes),
            authority=original.authority,
            diagnostics=merge_provider_diagnostics(
                original.diagnostics, replacement.diagnostics
            ),
            request_accounting=ProviderRequestAccounting(
                original.request_accounting.logical_requests + replacement.request_accounting.logical_requests,
                original.request_accounting.initial_dispatches + replacement.request_accounting.initial_dispatches,
                original.request_accounting.retry_dispatches + replacement.request_accounting.retry_dispatches,
                original.request_accounting.replays + replacement.request_accounting.replays,
                original.request_accounting.undispatched_incomplete + replacement.request_accounting.undispatched_incomplete,
            ),
        )
        if (
            original.edition_enrichment_overflow is not None
            and merged.edition_enrichment_overflow
            != original.edition_enrichment_overflow
        ):
            raise ProviderRetrySuccessorError("provider retry overflow cannot be retained")
        # A retry must never rewrite an unselected lane's persisted evidence.
        merged_lanes = {lane.identity: lane.to_data() for lane in merged.lanes}
        if any(merged_lanes[identity] != lane.to_data() for identity, lane in old_lanes.items() if identity not in selected_ids):
            raise ProviderRetrySuccessorError("provider retry merged sibling changed")
        return merged

    @staticmethod
    def _canonical_candidates(
        candidates: tuple[ProviderCandidate, ...],
    ) -> dict[str, ProviderCandidate]:
        result: dict[str, ProviderCandidate] = {}
        for candidate in candidates:
            previous = result.setdefault(candidate.identity, candidate)
            if previous.to_data() != candidate.to_data():
                raise ProviderRetrySuccessorError("provider retry candidates conflict")
        return result

    @staticmethod
    def _canonical_lanes(
        lanes: tuple[ProviderLaneObservation, ...],
    ) -> dict[str, ProviderLaneObservation]:
        result: dict[str, ProviderLaneObservation] = {}
        for lane in lanes:
            previous = result.setdefault(lane.identity, lane)
            if previous.to_data() != lane.to_data():
                raise ProviderRetrySuccessorError("provider retry lanes conflict")
        return result


__all__ = [
    "ConfiguredProviderRetry", "GenerateProviderRetrySuccessor",
    "ProviderRetryDisabled", "ProviderRetryResolver", "ProviderRetrySharedFailure",
    "ProviderRetrySuccessorError",
]
