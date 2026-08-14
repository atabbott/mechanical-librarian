"""Closed, non-composed provider-lane successor construction.

This is deliberately an application seam only.  F24 activation owns any
claiming, scheduling, or composition of it; this unit merely makes the
otherwise awkward successor transaction testable without granting execution.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.application.metadata_report import (
    CurrentnessError,
    MetadataDiscoveryPlan,
    MetadataProvider,
    MetadataReportError,
    ProviderBinding,
    ProviderLaneResult,
    ReportRepository,
    TargetReader,
    metadata_discovery_plan,
    provider_observation,
    require_report_current,
)
from mechanical_librarian.application.configuration import (
    MediaMountConfiguration,
    ReportLimits,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    ReportValueError,
)


class ProviderRetrySuccessorError(MetadataReportError):
    """Reject a stale, rebound, or non-retryable internal retry request."""


_RETRYABLE = frozenset({
    ProviderStatus.RATE_LIMITED,
    ProviderStatus.UNAVAILABLE,
    ProviderStatus.INCOMPLETE,
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
        inspector,
        repository_factory: Callable[[], ReportRepository],
        provider_resolver: ProviderRetryResolver,
        mount: MediaMountConfiguration | None,
        limits: ReportLimits,
        after_publication: Callable[[ReportRepository, MetadataReport], bool],
    ) -> None:
        self._target = target
        self._inspector = inspector
        self._repository_factory = repository_factory
        if not callable(provider_resolver) or not callable(after_publication):
            raise ProviderRetrySuccessorError("provider retry composition is invalid")
        self._provider_resolver = provider_resolver
        self._mount = mount
        self._limits = limits
        self._after_publication = after_publication

    def run(
        self,
        source_report_id: str,
        lane_identities: tuple[str, ...],
        *,
        provider_name: str,
        adapter: str,
        generation: int,
    ) -> str:
        if (
            not isinstance(source_report_id, str)
            or not isinstance(provider_name, str)
            or not provider_name
            or type(lane_identities) is not tuple
            or not lane_identities
            or tuple(sorted(set(lane_identities))) != lane_identities
        ):
            raise ProviderRetrySuccessorError("provider retry request is invalid")
        repository = self._repository_factory()
        try:
            source = self._source(repository, source_report_id)
            current = self._current_source(repository, source)
            plan = self._plan(source)
            provider = self._provider(source, provider_name, adapter, generation)
            original = self._observation(source, provider)
            selected = self._selected(plan, original, lane_identities)
            replacement = self._observe(provider, plan, selected)
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
                    and self._provider(source, provider_name, adapter, generation) == provider
                )

            def stage(bound: ReportRepository, report: MetadataReport) -> None:
                if self._after_publication(bound, report) is not True:
                    raise ProviderRetrySuccessorError("provider retry staging failed")

            published = repository.publish(successor, currentness, source.report_id, stage)
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

    def _current_source(self, repository: ReportRepository, source: MetadataReport) -> str:
        document = repository.current_optional(
            source.target.connection, source.target.library, source.target.item, source.file.identity
        )
        if (
            document is None
            or MetadataReport.from_json(document).report_id != source.report_id
            or repository.current_review_package_report(
                source.target.connection, source.target.library, source.target.item
            ) != source.report_id
        ):
            raise CurrentnessError("target or media changed")
        return document

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

    def _observation(self, source: MetadataReport, provider: ConfiguredProviderRetry) -> ProviderObservation:
        matches = tuple(value for value in source.providers if value.provider == provider.binding.name)
        if len(matches) != 1 or matches[0].authority != provider.binding.authority:
            raise ProviderRetrySuccessorError("provider retry configuration changed")
        return matches[0]

    def _selected(self, plan: MetadataDiscoveryPlan, original: ProviderObservation, identities: tuple[str, ...]):
        planned = {lane.identity: lane for lane in plan.lanes}
        observed = {lane.identity: lane for lane in original.lanes}
        if set(planned) != set(observed):
            raise ProviderRetrySuccessorError("provider retry source is invalid")
        selected = tuple(planned[identity] for identity in identities if identity in planned)
        if len(selected) != len(identities) or any(observed[lane.identity].status not in _RETRYABLE for lane in selected):
            raise ProviderRetrySuccessorError("provider retry lane is invalid")
        return selected

    def _observe(self, provider: ConfiguredProviderRetry, plan: MetadataDiscoveryPlan, selected: tuple) -> ProviderObservation:
        try:
            client: MetadataProvider = provider.binding.factory()
            observation = client.observe(MetadataDiscoveryPlan(plan.local_claims, selected))
        except Exception as error:
            raise ProviderRetrySuccessorError("provider retry dispatch failed") from error
        if observation.provider != provider.binding.name or observation.authority != provider.binding.authority:
            raise ProviderRetrySuccessorError("provider retry result is invalid")
        if {lane.identity for lane in observation.lanes} != {lane.identity for lane in selected}:
            raise ProviderRetrySuccessorError("provider retry result is invalid")
        return observation

    def _merged(self, plan, original, selected, replacement) -> ProviderObservation:
        selected_ids = {lane.identity for lane in selected}
        old_candidates = self._canonical_candidates(original.candidates)
        new_candidates = self._canonical_candidates(replacement.candidates)
        old_lanes = self._canonical_lanes(original.lanes)
        new_lanes = self._canonical_lanes(replacement.lanes)

        def result(lane):
            observation = new_lanes[lane.identity] if lane.identity in selected_ids else old_lanes[lane.identity]
            candidates = new_candidates if lane.identity in selected_ids else old_candidates
            return ProviderLaneResult(lane, observation.status, tuple(
                candidates[item.edition_identity] for item in observation.candidates
            ), replacement.accessed_at if lane.identity in selected_ids else original.accessed_at)

        merged = provider_observation(
            original.provider, tuple(result(lane) for lane in plan.lanes),
            authority=original.authority,
        )
        # A retry must never rewrite an unselected lane's persisted evidence.
        merged_lanes = {lane.identity: lane.to_data() for lane in merged.lanes}
        if any(merged_lanes[identity] != lane.to_data() for identity, lane in old_lanes.items() if identity not in selected_ids):
            raise ProviderRetrySuccessorError("provider retry merged sibling changed")
        return merged

    @staticmethod
    def _canonical_candidates(candidates):
        result = {}
        for candidate in candidates:
            previous = result.setdefault(candidate.identity, candidate)
            if previous.to_data() != candidate.to_data():
                raise ProviderRetrySuccessorError("provider retry candidates conflict")
        return result

    @staticmethod
    def _canonical_lanes(lanes):
        result = {}
        for lane in lanes:
            previous = result.setdefault(lane.identity, lane)
            if previous.to_data() != lane.to_data():
                raise ProviderRetrySuccessorError("provider retry lanes conflict")
        return result


__all__ = ["ConfiguredProviderRetry", "GenerateProviderRetrySuccessor", "ProviderRetryResolver", "ProviderRetrySuccessorError"]
