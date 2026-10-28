"""Closed, non-composed provider-lane successor construction.

This is deliberately an application seam only.  F24 activation owns any
claiming, scheduling, or composition of it; this unit merely makes the
otherwise awkward successor transaction testable without granting execution.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial

from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.configuration import (
    AUDIOBOOK_DISPATCH_ADAPTERS,
    MediaMountConfiguration,
    ReportLimits,
)
from mechanical_librarian.application.failure_diagnostics import (
    emit_provider_events,
    merge_provider_diagnostics,
    provider_dispatch_events,
    provider_item_ref,
    provider_result_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    AudioInspector,
    AudioItemInspectionError,
    CurrentnessError,
    EpubInspector,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataProvider,
    MetadataReportError,
    ProviderBinding,
    ProviderLaneResult,
    ProviderObservationFailure,
    ReportRepository,
    TargetReader,
    audiobook_asin_feed_plan,
    audiobook_carried_plan,
    audiobook_discovery_plan,
    metadata_discovery_plan,
    provider_observation,
    require_report_current,
    with_lane_dispatch,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderOperationalEvidence,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    ProviderRecoveryAttemptFailure,
    observed_recovery_evidence,
    partial_recovery_evidence,
)
from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    MetadataReport,
    ProviderCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderRequestAccounting,
    ProviderStatus,
    ReportValueError,
    TargetFacts,
    candidate_asins,
)
from mechanical_librarian.domain.provider_recovery import (
    MAX_RECOVERY_DELAY,
    MAX_TIMESTAMP,
    RecoveryAttemptEvidence,
)


class ProviderRetrySuccessorError(MetadataReportError):
    """Reject a stale, rebound, or non-retryable internal retry request."""

    def __init__(
        self, message: str, *, evidence: RecoveryAttemptEvidence | None = None
    ) -> None:
        super().__init__(message)
        self.evidence = evidence or RecoveryAttemptEvidence("terminal_failure")


class ProviderRetrySharedFailure(ProviderRetrySuccessorError):
    """A bounded provider-health result that must not publish a successor."""

    def __init__(
        self,
        failure: str,
        retry_after: int | None = None,
        *,
        evidence: RecoveryAttemptEvidence | None = None,
    ) -> None:
        if failure not in {
            "authentication",
            "authorization",
            "configuration",
            "rate_limited",
            "burst_throttled",
            "quota_exhausted",
            "transient",
            "unavailable",
            "incomplete",
        } or (
            retry_after is not None
            and (
                type(retry_after) is not int
                or not 1 <= retry_after <= MAX_RECOVERY_DELAY
                or failure != "quota_exhausted"
                and retry_after > 86_400
            )
        ):
            raise ProviderRetrySuccessorError("provider retry failure is invalid")
        super().__init__("provider retry shared failure", evidence=evidence)
        self.failure = failure
        self.retry_after = retry_after


class ProviderRetryDisabled(ProviderRetrySuccessorError):
    """The exact configured provider was disabled before recovery egress."""


_RETRYABLE = frozenset(
    {
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
    }
)
_GENERATION_WOKEN = frozenset(
    {
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.AUTHORIZATION,
    }
)


@dataclass(frozen=True, slots=True)
class ConfiguredProviderRetry:
    """The current configuration identity required to replay one provider."""

    binding: ProviderBinding
    adapter: str
    generation: int
    google_burst_remaining: Callable[[], int | None] | None = None

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


ProviderRetryResolver = Callable[
    [str, str, str, str, int], ConfiguredProviderRetry | None
]


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
        # An audiobook's currentness is re-derived from the file and every
        # recognized sidecar beside it, so a retry over an audio report needs
        # the same isolated inspector its publication used. Without one the
        # currentness guard refuses, which is the correct fail-closed answer
        # for a composition that cannot re-read what it published from.
        audio_inspector: AudioInspector | None = None,
        provider_resolver: ProviderRetryResolver,
        mount: MediaMountConfiguration | None,
        limits: ReportLimits,
        # The staging result is deliberately discarded, so the callback's
        # return type is unconstrained rather than required to be None.
        after_publication: Callable[[ReportRepository, MetadataReport], object],
        heartbeat: Callable[[], None] = lambda: None,
        wall_clock: Callable[[], float] = time.time,
        operational_evidence: Callable[[RecoveryAttemptEvidence], None] | None = None,
    ) -> None:
        self._target = target
        self._inspector = inspector
        self._audio_inspector = audio_inspector
        self._repository_factory = repository_factory
        if (
            not callable(provider_resolver)
            or not callable(after_publication)
            or not callable(heartbeat)
            or not callable(wall_clock)
        ):
            raise ProviderRetrySuccessorError("provider retry composition is invalid")
        self._provider_resolver = provider_resolver
        self._mount = mount
        self._limits = limits
        self._after_publication = after_publication
        self._heartbeat = heartbeat
        self._wall_clock = wall_clock
        self._operational_evidence = operational_evidence

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
        evidence = RecoveryAttemptEvidence("terminal_failure")
        try:
            source = self._source(repository, source_report_id)
            # A job queued before ADR-0061 clause 9 can name an adapter that is
            # no longer dispatched for audiobooks. It closes as stale -- that
            # job alone, recording no failure evidence -- before any resolver,
            # target read or factory call could spend egress on it.
            if _is_audio(source) and adapter not in AUDIOBOOK_DISPATCH_ADAPTERS:
                raise CurrentnessError("provider is not dispatched for audiobooks")
            current = self._current_source(repository, source)
            # The provider and its retained observation are resolved before the
            # plan: which plan a per-ASIN provider was observed under is a fact
            # about that observation, not about the report alone.
            provider = self._provider(source, provider_name, adapter, generation)
            original = self._observation(source, provider)
            plan = self._plan(source, original)
            selected = self._selected(
                plan,
                original,
                lane_identities,
                generation_woken=generation_woken,
            )
            # This is the final external preflight: no factory or provider call
            # may follow a changed source package or rebound configuration.
            if (
                not require_report_current(
                    self._target,
                    self._inspector,
                    self._mount,
                    self._limits,
                    source,
                    audio_inspector=self._audio_inspector,
                )
                or self._current_source(repository, source) != current
            ):
                raise CurrentnessError("target or media changed")
            live_provider = self._provider(source, provider_name, adapter, generation)
            if self._provider_identity(live_provider) != self._provider_identity(
                provider
            ):
                raise ProviderRetrySuccessorError(
                    "provider retry configuration changed"
                )
            self._heartbeat()
            replacement, retry_after, evidence = self._observe(
                live_provider,
                plan,
                selected,
                original,
                source.target,
                deadline=time.monotonic() + 60.0,
            )
            self._heartbeat()
            # A shared provider condition is not evidence for a successor.
            # Leave the predecessor current so the recovery claim can back off
            # before any sibling is contacted.
            reading = self._wall_clock()
            if (
                type(reading) not in {int, float}
                or isinstance(reading, bool)
                or not math.isfinite(reading)
                or not 0 <= reading <= MAX_TIMESTAMP
            ):
                raise ProviderRetrySuccessorError("provider retry clock is invalid")
            decision = provider_recovery_decision(
                replacement,
                now=int(reading),
                google_burst_remaining=live_provider.google_burst_remaining,
            )
            if decision is not None:
                failure, scheduled_delay = decision
                raise ProviderRetrySharedFailure(
                    failure,
                    scheduled_delay
                    if scheduled_delay is not None
                    else retry_after
                    if failure == "rate_limited"
                    else None,
                    evidence=replace(evidence, outcome=failure),
                )
            merged = self._merged(plan, original, selected, replacement)
            successor = MetadataReport.create(
                target=source.target,
                file=source.file,
                local_claims=source.local_claims,
                local_reasons=source.local_reasons,
                providers=tuple(
                    sorted(
                        (
                            merged if value.provider == original.provider else value
                            for value in source.providers
                        ),
                        key=lambda value: value.provider,
                    )
                ),
                cover_sources=source.cover_sources,
                # A successor replaces one provider's lanes and nothing the
                # inspector read. The audio section travels unchanged, and the
                # report refuses an audio file without one, so a retry over an
                # audiobook could not publish without carrying it.
                audio=source.audio,
            )

            def currentness() -> bool:
                if not require_report_current(
                    self._target,
                    self._inspector,
                    self._mount,
                    self._limits,
                    source,
                    audio_inspector=self._audio_inspector,
                ):
                    return False
                return self._current_source(
                    repository, source
                ) == current and self._provider_identity(
                    self._provider(source, provider_name, adapter, generation)
                ) == self._provider_identity(provider)

            def stage(bound: ReportRepository, report: MetadataReport) -> None:
                self._after_publication(bound, report)

            published = repository.publish(
                successor,
                currentness,
                source.report_id,
                stage,
                expected_package=current[1],
            )
            if (
                published != successor.to_json()
                or repository.get(successor.report_id) != published
            ):
                raise ProviderRetrySuccessorError("provider retry publication failed")
            if self._operational_evidence is not None:
                self._operational_evidence(
                    replace(
                        evidence,
                        outcome="no_match"
                        if all(
                            lane.status is ProviderStatus.NO_MATCH
                            for lane in replacement.lanes
                        )
                        else "completed",
                    )
                )
            return published
        except (CurrentnessError, TargetConnectionError):
            raise
        except ProviderRetrySuccessorError as error:
            if (
                error.evidence.request_count_status == "unknown"
                and evidence.request_count_status != "unknown"
            ):
                error.evidence = replace(evidence, outcome=error.evidence.outcome)
            raise
        except AudioItemInspectionError as error:
            # Revalidating a composed audiobook re-digests every track under
            # the item budget, and a successor that outran it has not shown
            # its source current.  That is the same answer a changed source
            # gets: no successor is published, and the item is prepared again
            # where the budget states itself as its own terminal.
            raise CurrentnessError("target or media changed") from error
        except (ReportValueError, MetadataReportError) as error:
            raise ProviderRetrySuccessorError(
                "provider retry source is invalid", evidence=evidence
            ) from error
        except ProviderRecoveryAttemptFailure:
            raise
        except Exception as error:
            raise ProviderRecoveryAttemptFailure(evidence) from error
        finally:
            repository.close()

    def _source(self, repository: ReportRepository, report_id: str) -> MetadataReport:
        try:
            return MetadataReport.from_json(repository.get(report_id))
        except (KeyError, ReportValueError, MetadataReportError) as error:
            raise ProviderRetrySuccessorError(
                "provider retry source is invalid"
            ) from error

    def _current_source(
        self, repository: ReportRepository, source: MetadataReport
    ) -> tuple[str, tuple[str | None, str, str | None, int | None, str]]:
        document = repository.current_optional(
            source.target.connection,
            source.target.library,
            source.target.item,
            source.file.identity,
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

    def _plan(
        self, source: MetadataReport, original: ProviderObservation
    ) -> MetadataDiscoveryPlan:
        """Rebuild the plan the source report was observed under.

        The media kind decides which plan builder rebuilds it: an audiobook's
        identifier is an ASIN and its lanes come from `audiobook_discovery_plan`
        rather than the ISBN one. Both builders are deterministic in the local
        claims, so a rebuilt lane identity is the same lane identity the source
        observation recorded and the caller's reconciliation is unchanged.

        One audiobook observation was made under a second plan: a per-ASIN
        provider fed the ASINs its pass promoted or the target asserted, whose
        lanes are all ASIN lanes over a report whose own plan has none. That
        feed is rebuilt from the retained observations by the same function
        that built it, and a feed that cannot be rebuilt is refused rather than
        retried against a plan the provider never saw.

        The Audible catalogue may have been handed its plan with the carried
        product lane added (#1316). That lane is rebuilt from the source's own
        `prior_admission` claim by the same function, so a retry looks the
        carried ASIN up again under the lane identity it failed under.
        """
        snapshot = self._target.read(source.target.item)
        audio = _is_audio(source)
        if (
            snapshot.media_kind != ("audiobook" if audio else "ebook")
            or snapshot.title != source.target.current_title
            or snapshot.fields != source.target.fields
        ):
            raise CurrentnessError("target or media changed")
        plan = (
            audiobook_discovery_plan(snapshot, source.local_claims, audio=source.audio)
            if audio
            else metadata_discovery_plan(snapshot, source.local_claims)
        )
        if plan.overflow:
            raise ProviderRetrySuccessorError("provider retry source is invalid")
        if audio and original.authority == "audible_catalog":
            carried = audiobook_carried_plan(plan)
            if carried.lanes != plan.lanes:
                return carried
        if (
            audio
            and original.lanes
            and all(lane.kind is ProviderQueryKind.ASIN for lane in original.lanes)
            and not any(lane.kind is ProviderQueryKind.ASIN for lane in plan.lanes)
        ):
            feed = audiobook_asin_feed_plan(snapshot, plan, source.providers)
            if feed is None:
                raise ProviderRetrySuccessorError("provider retry source is invalid")
            return feed
        return plan

    def _provider(
        self, source: MetadataReport, name: str, adapter: str, generation: int
    ) -> ConfiguredProviderRetry:
        value = self._provider_resolver(
            source.target.connection,
            source.target.library,
            name,
            adapter,
            generation,
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
    def _provider_identity(
        value: ConfiguredProviderRetry,
    ) -> tuple[str, bool, str | None, str, int]:
        """Compare persisted configuration identity, never the factory callable."""
        return (
            value.binding.name,
            value.binding.enabled,
            value.binding.authority,
            value.adapter,
            value.generation,
        )

    def _observation(
        self, source: MetadataReport, provider: ConfiguredProviderRetry
    ) -> ProviderObservation:
        matches = tuple(
            value
            for value in source.providers
            if value.provider == provider.binding.name
        )
        if len(matches) != 1 or matches[0].authority != provider.binding.authority:
            raise ProviderRetrySuccessorError("provider retry configuration changed")
        return matches[0]

    def _selected(
        self,
        plan: MetadataDiscoveryPlan,
        original: ProviderObservation,
        identities: tuple[str, ...],
        *,
        generation_woken: bool = False,
    ) -> tuple[MetadataDiscoveryLane, ...]:
        planned = {lane.identity: lane for lane in plan.lanes}
        observed = {lane.identity: lane for lane in original.lanes}
        # A suppressed alternative work lane is planned and never observed;
        # every other planned lane must have been (#1255).
        if not set(observed) <= set(planned) or any(
            planned[identity].title_basis is None
            for identity in set(planned) - set(observed)
        ):
            raise ProviderRetrySuccessorError("provider retry source is invalid")
        selected = tuple(
            planned[identity] for identity in identities if identity in observed
        )
        permitted = _RETRYABLE | (
            _GENERATION_WOKEN if generation_woken else frozenset()
        )
        if len(selected) != len(identities) or any(
            observed[lane.identity].status not in permitted for lane in selected
        ):
            raise ProviderRetrySuccessorError("provider retry lane is invalid")
        return selected

    def _observe(
        self,
        provider: ConfiguredProviderRetry,
        plan: MetadataDiscoveryPlan,
        selected: tuple[MetadataDiscoveryLane, ...],
        original: ProviderObservation,
        target: TargetFacts,
        *,
        deadline: float,
    ) -> tuple[ProviderObservation, int | None, RecoveryAttemptEvidence]:
        """Dispatch the selected lanes once, within the recovery window.

        The dispatch's failures, throttles and deadline stops are written as
        value-free provider events whether it returns or raises (#1299).
        """
        started = time.monotonic()
        events = partial(
            provider_dispatch_events,
            adapter=provider.binding.authority,
            provider=provider.binding.name,
            item_ref=provider_item_ref(target.connection, target.library, target.item),
            phase="recovery",
            lanes=selected,
            audio=plan.audio is not None,
            deadline_seconds=max(0.0, deadline - started),
            deadline_kind="recovery_window",
        )
        try:
            client: MetadataProvider = provider.binding.factory()
            result = client.observe(
                _retry_plan(plan, selected, original), deadline=deadline
            )
        except Exception as error:
            elapsed, failure = time.monotonic() - started, error
            # The partial evidence is read inside the event guard: a snapshot
            # the adapter malformed must not displace the failure below.
            emit_provider_events(
                lambda: events(
                    observation=None,
                    evidence=(
                        failure.snapshot.evidence
                        if isinstance(failure, ProviderObservationFailure)
                        else ProviderOperationalEvidence()
                    ),
                    elapsed_seconds=elapsed,
                )
            )
            # Provider construction and the remote observation are one dispatch
            # across the complete result/failure contract. Either failing
            # means the retry produced no observation, and the checks below --
            # which reject a result that disagrees with the binding -- can only
            # run on a dispatch that returned. The cause is chained, and the
            # caller classifies it from the chain rather than from this code.
            raise ProviderRetrySuccessorError(
                "provider retry dispatch failed",
                evidence=partial_recovery_evidence(error, provider.binding.name),
            ) from error
        elapsed = time.monotonic() - started

        def dispatched(value: ProviderObservation) -> ProviderObservation:
            # A replacement lane is starved only where the merged successor's
            # diagnostics, which keep the original's, still back it (#1258).
            return with_lane_dispatch(
                replace(
                    value,
                    diagnostics=merge_provider_diagnostics(
                        original.diagnostics,
                        provider_result_diagnostics(value, result.evidence),
                    ),
                ),
                result,
                audio=plan.audio is not None,
            )

        # The events read the lane dispatch the successor records, so a starved
        # lane is named; reading the result inside the guard keeps every check
        # below where it was.
        emit_provider_events(
            lambda: events(
                observation=dispatched(result.observation),
                evidence=result.evidence,
                elapsed_seconds=elapsed,
            )
        )
        observation = result.observation
        evidence = observed_recovery_evidence(observation)
        try:
            if (
                type(observation) is ProviderObservation
                and observation.authority is None
            ):
                observation = replace(observation, authority=provider.binding.authority)
            if (
                observation.provider != provider.binding.name
                or observation.authority != provider.binding.authority
            ):
                raise ProviderRetrySuccessorError(
                    "provider retry result is invalid", evidence=evidence
                )
            if {lane.identity for lane in observation.lanes} != {
                lane.identity for lane in selected
            }:
                raise ProviderRetrySuccessorError(
                    "provider retry result is invalid", evidence=evidence
                )
            if observation.status is ProviderStatus.MALFORMED or any(
                lane.status is ProviderStatus.MALFORMED for lane in observation.lanes
            ):
                raise ProviderRetrySuccessorError(
                    "provider retry result is invalid",
                    evidence=replace(evidence, outcome="terminal_response"),
                )
            observation = replace(
                dispatched(observation),
                diagnostics=provider_result_diagnostics(observation, result.evidence),
            )
            return observation, self._retry_after(result.evidence), evidence
        except ProviderRetrySuccessorError:
            raise
        except (ReportValueError, MetadataReportError) as error:
            raise ProviderRetrySuccessorError(
                "provider retry result is invalid", evidence=evidence
            ) from error
        except Exception as error:
            raise ProviderRecoveryAttemptFailure(evidence) from error

    @staticmethod
    def _retry_after(evidence: ProviderOperationalEvidence) -> int | None:
        """Read only a canonical final bounded delay from the result snapshot."""
        records = evidence.attempt_records
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
        if delay is None or type(delay) not in {int, float} or isinstance(delay, bool):
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
            observation = (
                new_lanes[lane.identity]
                if lane.identity in selected_ids
                else old_lanes[lane.identity]
            )
            candidates = (
                new_candidates if lane.identity in selected_ids else old_candidates
            )
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
                tuple(
                    candidates[item.edition_identity] for item in observation.candidates
                ),
                aggregate_accessed_at,
                overflow,
                observation.diagnostics,
                observation.dispatch_lineage,
                observation.transformation_lineage,
                exhausted_dispatch=observation.exhausted_dispatch,
                recording_lookup_hints=observation.recording_lookup_hints,
                dispatch=observation.dispatch,
                # What an ASIN lane refused stays with the stub it explains.
                refused_products=tuple(
                    product
                    for product in evidence.refused_products
                    if lane.kind is ProviderQueryKind.ASIN
                    and any(
                        item.edition_identity == product.identity
                        for item in observation.candidates
                    )
                ),
            )

        # Merge over the observed lanes only: retrying an asserted lane that
        # now finds nothing never dispatches the alternative it would have
        # permitted, a limitation the successor states rather than widens.
        merged = provider_observation(
            original.provider,
            tuple(result(lane) for lane in plan.lanes if lane.identity in old_lanes),
            authority=original.authority,
            diagnostics=merge_provider_diagnostics(
                original.diagnostics, replacement.diagnostics
            ),
            request_accounting=ProviderRequestAccounting(
                original.request_accounting.logical_requests
                + replacement.request_accounting.logical_requests,
                original.request_accounting.initial_dispatches
                + replacement.request_accounting.initial_dispatches,
                original.request_accounting.retry_dispatches
                + replacement.request_accounting.retry_dispatches,
                original.request_accounting.replays
                + replacement.request_accounting.replays,
                original.request_accounting.undispatched_incomplete
                + replacement.request_accounting.undispatched_incomplete,
            ),
        )
        if (
            original.edition_enrichment_overflow is not None
            and merged.edition_enrichment_overflow
            != original.edition_enrichment_overflow
        ):
            raise ProviderRetrySuccessorError(
                "provider retry overflow cannot be retained"
            )
        # A retry must never rewrite an unselected lane's persisted evidence,
        # except a recording-lookup hint (#1257) whose product it now retains.
        merged_lanes = {lane.identity: lane.to_data() for lane in merged.lanes}
        held = candidate_asins(
            (*merged.candidates, *merged.recording_lookup_candidates)
        )
        if any(
            merged_lanes[identity]
            != replace(
                lane,
                recording_lookup_hints=tuple(
                    hint
                    for hint in lane.recording_lookup_hints
                    if hint.asin not in held
                ),
            ).to_data()
            for identity, lane in old_lanes.items()
            if identity not in selected_ids
        ):
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


def _is_audio(source: MetadataReport) -> bool:
    """Whether one source report is an audiobook's.

    The retained `audio` section and the file format each name the same fact,
    and both are read so a report cannot be one media kind by format and the
    other by section.
    """
    return source.file.format in AUDIO_FILE_FORMATS or source.audio is not None


def _retry_plan(
    plan: MetadataDiscoveryPlan,
    selected: tuple[MetadataDiscoveryLane, ...],
    original: ProviderObservation,
) -> MetadataDiscoveryPlan:
    """Narrow a plan to the retried lanes, keeping the rule only where safe.

    The corroborated rule (#1307) reads only once every exact path refused.
    A retry sees only its own lanes, so it keeps the rule only when every
    alternative WORK lane the plan carries is retried here or already ended
    `no_match`, and no ASIN lane left out of the retry answered with a product. Otherwise the
    retry runs without it: an alternative lane suppressed behind a failed
    asserted lane was never asked, and a refused page is not retained, so a
    later retry of that lane alone cannot re-arm the rule. The ladder itself is
    unchanged, its leading-number rung included, since the report requires
    every rung of the asserted lane however the rule ends.
    """
    retried = {lane.identity for lane in selected}
    observed = {lane.identity: lane for lane in original.lanes}
    keeps_rule = all(
        lane.identity in retried
        or (
            (prior := observed.get(lane.identity)) is not None
            and prior.status is ProviderStatus.NO_MATCH
        )
        for lane in plan.lanes
        if lane.kind is ProviderQueryKind.WORK and lane.title_basis is not None
    ) and not any(
        lane.kind is ProviderQueryKind.ASIN
        and lane.identity not in retried
        and lane.status is ProviderStatus.CANDIDATES
        for lane in original.lanes
    )
    return replace(
        plan,
        lanes=selected,
        corroborated_rule=plan.corroborated_rule and keeps_rule,
    )


__all__ = [
    "ConfiguredProviderRetry",
    "GenerateProviderRetrySuccessor",
    "ProviderRetryDisabled",
    "ProviderRetryResolver",
    "ProviderRetrySharedFailure",
    "ProviderRetrySuccessorError",
]
