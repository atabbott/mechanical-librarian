"""Preparation's link phase: reading an item's owner link (#1477, ADR-0104).

Before any other source is asked, the link's own sources read the record the
owner chose: a key lookup at the work link's source, or the link's ISBN or
ASIN on its own lane at the medium's identifier sources. An edition or
recording record is re-checked against the file, a failing one is dropped,
and what was found becomes the report's `owner_link` facts. While the link
anchors, the main phase asks every other source with the kept record's title
and creators, and each provider's two observations become one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mechanical_librarian.application.metadata_report import (
    OWNER_LINK_MERGED_DIAGNOSTIC,
    MetadataDiscoveryPlan,
    ProviderCoverDescriptor,
    ProviderLaneResult,
    merge_phase_observations,
    owner_link_discovery_lane,
    owner_link_plan,
    persisted_lane_result,
    provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    DispatchOutcome,
    OwnerLinkFacts,
    ProviderCandidate,
    ProviderLaneDispatch,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderRequestAccounting,
    ProviderStatus,
    owner_link_anchor_values,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLink,
    OwnerLinkContradiction,
    OwnerLinkLookup,
    OwnerLinkOutcome,
    OwnerLinkScope,
    owner_link_anchors,
    owner_link_contradiction,
)

# Lane statuses that are an answer about the record rather than a failure to
# read it: a recovery never retries them.
_ANSWERED = frozenset(
    {ProviderStatus.CANDIDATES, ProviderStatus.NO_MATCH, ProviderStatus.DISABLED}
)


@dataclass(frozen=True, slots=True)
class OwnerLinkPhase:
    """What the link phase read, and what the main phase is asked with."""

    facts: OwnerLinkFacts | None = None
    plan: MetadataDiscoveryPlan | None = None
    observations: tuple[ProviderObservation, ...] = ()
    descriptors: tuple[ProviderCoverDescriptor, ...] = ()

    @property
    def anchored(self) -> bool:
        """Whether the main phase's WORK lanes ask with the link's anchor."""
        return (
            self.facts is not None
            and self.facts.outcome != OwnerLinkOutcome.MEDIUM_CHANGED
            and owner_link_anchors(OwnerLinkScope(self.facts.scope))
        )

    @property
    def anchor(self) -> tuple[str, tuple[str, ...]] | None:
        return None if self.facts is None else self.facts.anchor

    @property
    def linked_source(self) -> str | None:
        """A work link's source, which the main phase does not ask again."""
        if self.facts is None or self.facts.outcome == OwnerLinkOutcome.MEDIUM_CHANGED:
            return None
        return self.facts.provider

    def merged(
        self,
        plans: tuple[MetadataDiscoveryPlan, ...],
        observations: tuple[ProviderObservation, ...],
    ) -> tuple[ProviderObservation, ...]:
        """Each provider's link-phase and main-phase observations, as one.

        `plans` are every plan the main phase handed out, whose lanes its
        observations carry: the plan, the catalogue's and the ASIN feed's.
        """
        if self.plan is None or not self.observations:
            return observations
        linked = {value.provider: value for value in self.observations}
        main = {value.provider: value for value in observations}
        result: list[ProviderObservation] = []
        for name in sorted(set(linked) | set(main)):
            first, second = linked.get(name), main.get(name)
            if first is None or not first.lanes:
                result.append(main[name] if second is not None else linked[name])
            elif second is None or not second.lanes:
                result.append(first)
            else:
                result.append(
                    merge_phase_observations((((self.plan,), first), (plans, second)))
                )
        return tuple(result)


def medium_changed_phase(link: OwnerLink) -> OwnerLinkPhase:
    """A link recorded for the other medium is not read at all."""
    lookup = link.lookup
    return OwnerLinkPhase(
        OwnerLinkFacts(
            lookup.medium,
            lookup.scope.value,
            lookup.provider,
            lookup.identity,
            OwnerLinkOutcome.MEDIUM_CHANGED.value,
        )
    )


def link_phase_plan(
    plan: MetadataDiscoveryPlan, lookup: OwnerLinkLookup
) -> MetadataDiscoveryPlan:
    """The plan the link's own sources are handed: the link's lane alone."""
    return owner_link_plan(plan, lookup)


def settle_owner_link(
    lookup: OwnerLinkLookup,
    plan: MetadataDiscoveryPlan,
    observations: tuple[ProviderObservation, ...],
    descriptors: tuple[ProviderCoverDescriptor, ...],
    *,
    work_title: str,
) -> OwnerLinkPhase:
    """Re-check what the link phase read and name what it found.

    A record that fails its edition or recording re-check is dropped from its
    lane, which then found nothing; the first such failure is the reason. The
    outcome is `admitted` when any source kept the record, otherwise the
    re-check's `contradicted`, then `unavailable` when a source could not be
    read (recovery retries it), then `merged`, `missing`, and finally
    `source_disabled` when no source of the link was asked.
    """
    kept: list[tuple[str, ProviderCandidate]] = []
    reasons: list[OwnerLinkContradiction] = []
    settled: list[ProviderObservation] = []
    for observation in observations:
        if len(observation.lanes) != 1:
            settled.append(observation)
            continue
        (lane,) = observation.lanes
        candidates = {value.identity: value for value in observation.candidates}
        survivors: list[ProviderCandidate] = []
        for member in lane.candidates:
            candidate = candidates[member.edition_identity]
            reason = owner_link_contradiction(
                lookup.scope,
                candidate,
                link_claim=lookup.claim,
                local_claims=plan.local_claims,
                work_title=work_title,
                library_languages=plan.library_languages,
                echoed_languages=plan.echoed_languages,
                echoed_narrators=plan.echoed_narrators,
            )
            if reason is None:
                survivors.append(candidate)
            else:
                reasons.append(reason)
        kept.extend((observation.provider, value) for value in survivors)
        settled.append(
            observation
            if len(survivors) == len(lane.candidates)
            else _without_records(observation, plan, tuple(survivors))
        )
    facts = _settled_facts(
        lookup,
        tuple(lane for value in settled for lane in value.lanes),
        tuple(kept),
        tuple(reasons),
    )
    return OwnerLinkPhase(facts, plan, tuple(settled), descriptors)


def _settled_facts(
    lookup: OwnerLinkLookup,
    lanes: tuple[ProviderLaneObservation, ...],
    kept: tuple[tuple[str, ProviderCandidate], ...],
    reasons: tuple[OwnerLinkContradiction, ...],
) -> OwnerLinkFacts:
    """Name what the link's lanes found, in the outcome precedence."""
    statuses = {lane.status for lane in lanes}
    if kept:
        outcome = OwnerLinkOutcome.ADMITTED
    elif reasons:
        outcome = OwnerLinkOutcome.CONTRADICTED
    elif not statuses <= _ANSWERED:
        outcome = OwnerLinkOutcome.UNAVAILABLE
    elif any(
        OWNER_LINK_MERGED_DIAGNOSTIC in lane.diagnostics
        and lane.status is ProviderStatus.NO_MATCH
        for lane in lanes
    ):
        outcome = OwnerLinkOutcome.MERGED
    elif ProviderStatus.NO_MATCH in statuses:
        outcome = OwnerLinkOutcome.MISSING
    else:
        outcome = OwnerLinkOutcome.SOURCE_DISABLED
    anchor = next(
        (
            values
            for _, candidate in sorted(
                kept, key=lambda item: (item[0], item[1].identity)
            )
            if (values := owner_link_anchor_values(candidate)) is not None
        ),
        None,
    )
    return OwnerLinkFacts(
        lookup.medium,
        lookup.scope.value,
        lookup.provider,
        lookup.identity,
        outcome.value,
        reasons[0].value if outcome is OwnerLinkOutcome.CONTRADICTED else None,
        anchor,
    )


def resettled_owner_link(
    facts: OwnerLinkFacts,
    providers: tuple[ProviderObservation, ...],
    reason: str | None,
) -> OwnerLinkFacts:
    """A recovery successor's link facts, over every provider's link lane.

    The retried lane was re-checked before it joined (`reason` names its
    failure, if any); every other link lane is the source's, already
    re-checked, so what each keeps is kept and the earlier contradiction
    still counts. An admitted source's anchor stays the anchor, none
    included: the carried main-phase lanes were asked and admitted against
    it, even when a record the retry newly kept would come first or supply
    the first anchor (`owner_link_anchor_moved`).
    """
    settled = _resettled(facts, providers, reason)
    if (
        facts.outcome == OwnerLinkOutcome.ADMITTED.value
        and settled.outcome == OwnerLinkOutcome.ADMITTED.value
        and settled.anchor != facts.anchor
    ):
        return replace(settled, anchor=facts.anchor)
    return settled


def owner_link_anchor_moved(
    facts: OwnerLinkFacts,
    providers: tuple[ProviderObservation, ...],
    reason: str | None,
) -> bool:
    """Whether a fresh settlement would read a usable anchor the source lacks.

    That is another anchor than the source's, or the first one for an
    admitted source that read none. The successor keeps the source's anchor;
    the work is then prepared afresh so the next ordinary preparation asks
    every source with the anchor it would read now.
    """
    return facts.outcome == OwnerLinkOutcome.ADMITTED.value and _resettled(
        facts, providers, reason
    ).anchor not in (None, facts.anchor)


def _resettled(
    facts: OwnerLinkFacts,
    providers: tuple[ProviderObservation, ...],
    reason: str | None,
) -> OwnerLinkFacts:
    lookup = facts.lookup
    identity = owner_link_discovery_lane(lookup).identity
    lanes: list[ProviderLaneObservation] = []
    kept: list[tuple[str, ProviderCandidate]] = []
    for observation in providers:
        candidates = {value.identity: value for value in observation.candidates}
        for lane in observation.lanes:
            if lane.identity != identity:
                continue
            lanes.append(lane)
            kept.extend(
                (observation.provider, candidates[member.edition_identity])
                for member in lane.candidates
            )
    reasons = tuple(
        OwnerLinkContradiction(value)
        for value in (reason, facts.reason)
        if value is not None
    )
    return _settled_facts(lookup, tuple(lanes), tuple(kept), reasons)


def phase_part(
    plan: MetadataDiscoveryPlan,
    observation: ProviderObservation,
    identities: frozenset[str],
    *,
    accounted: bool,
) -> ProviderObservation:
    """One phase's lanes of a provider's observation, carried byte for byte.

    A recovery successor retries one phase at a time (S22). The retried
    phase's part carries the observation's request accounting and the other's
    none, so the two rejoin to exactly what was spent.
    """
    planned = {lane.identity: lane for lane in plan.lanes}
    candidates = {value.identity: value for value in observation.candidates}
    return provider_observation(
        observation.provider,
        tuple(
            persisted_lane_result(
                planned[lane.identity],
                lane,
                candidates,
                observation,
                observation.accessed_at,
            )
            for lane in observation.lanes
            if lane.identity in identities
        ),
        authority=observation.authority,
        diagnostics=observation.diagnostics,
        request_accounting=(
            observation.request_accounting if accounted else ProviderRequestAccounting()
        ),
    )


def _without_records(
    observation: ProviderObservation,
    plan: MetadataDiscoveryPlan,
    survivors: tuple[ProviderCandidate, ...],
) -> ProviderObservation:
    """The link lane with only the records that passed their re-check."""
    (lane,) = observation.lanes
    (planned,) = (value for value in plan.lanes if value.identity == lane.identity)
    status = ProviderStatus.CANDIDATES if survivors else ProviderStatus.NO_MATCH
    return provider_observation(
        observation.provider,
        (
            ProviderLaneResult(
                planned,
                status,
                survivors,
                observation.accessed_at,
                diagnostics=lane.diagnostics,
                dispatch=(
                    None
                    if lane.dispatch is None
                    else ProviderLaneDispatch(
                        DispatchOutcome.ADMITTED if survivors else DispatchOutcome.EMPTY
                    )
                ),
                admission_bases=tuple(
                    sorted(
                        (member.edition_identity, member.admission_basis)
                        for member in lane.candidates
                        if member.admission_basis is not None
                        and any(
                            value.identity == member.edition_identity
                            for value in survivors
                        )
                    )
                ),
                admission_bases_recorded=True,
            ),
        ),
        authority=observation.authority,
        diagnostics=observation.diagnostics,
        request_accounting=observation.request_accounting,
    )
