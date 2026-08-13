"""Draft-policy validation, impact projection, and proposal binding seams."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from mechanical_librarian.domain.metadata_policy import MetadataPolicyError, MetadataPolicyProfile, OPTION_CATALOGUE, ResolvedMetadataPolicy, resolve_policy
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.domain.automatic_decision import (
    AutomaticSafetyState,
    QUALIFIED_POLICY_OPTIONS,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreview, compile_rich_metadata_preview,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_RICH_FIELDS,
    public_summary,
)


MAX_SHADOW_SAMPLE_RESPONSE_BYTES = 1_048_576


class MetadataPolicyApplicationError(RuntimeError):
    """A fixed, value-free draft policy failure."""


class AutomaticSafetyControls:
    """Owner-only durable pause/suspension controls; recovery is explicit."""

    def __init__(self, repository: object) -> None:
        self._repository = repository

    def state(self) -> AutomaticSafetyState:
        try:
            return self._repository.automatic_safety_state()
        except Exception as error:
            raise MetadataPolicyApplicationError("automatic safety is unavailable") from error

    def pause(self, paused: bool, updated_at: int) -> AutomaticSafetyState:
        current = self.state()
        result = AutomaticSafetyState(paused, current.global_suspension, current.category_suspensions)
        try:
            self._repository.set_automatic_safety_state(result, updated_at)
        except Exception as error:
            raise MetadataPolicyApplicationError("automatic safety is unavailable") from error
        return result

    def suspend(self, reason: str, updated_at: int, category: str | None = None) -> AutomaticSafetyState:
        if type(reason) is not str or not reason or (category is not None and (type(category) is not str or not category)):
            raise MetadataPolicyApplicationError("automatic safety is invalid")
        current = self.state()
        categories = dict(current.category_suspensions)
        if category is None:
            result = AutomaticSafetyState(current.paused, reason, current.category_suspensions)
        else:
            categories[category] = reason
            result = AutomaticSafetyState(current.paused, current.global_suspension, tuple(sorted(categories.items())))
        try:
            self._repository.set_automatic_safety_state(result, updated_at)
        except Exception as error:
            raise MetadataPolicyApplicationError("automatic safety is unavailable") from error
        return result

    def recover(self, updated_at: int, category: str | None = None) -> AutomaticSafetyState:
        current = self.state()
        result = AutomaticSafetyState(current.paused, None if category is None else current.global_suspension, tuple(value for value in current.category_suspensions if value[0] != category))
        try:
            self._repository.set_automatic_safety_state(result, updated_at)
        except Exception as error:
            raise MetadataPolicyApplicationError("automatic safety is unavailable") from error
        return result


@dataclass(frozen=True, slots=True)
class LibraryPolicyImpact:
    library: str
    affected_categories: tuple[str, ...]
    current_unapplied_count: int
    resolved_policy_id: str
    cases: tuple[RichMetadataPreview, ...] = ()
    cases_total: int = 0
    cases_truncated: bool = False
    work_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyPreview:
    profile_id: str
    active_policy_id: str
    validation: tuple[str, ...]
    libraries: tuple[LibraryPolicyImpact, ...]


MAX_RICH_PREVIEW_CASES = 20


@dataclass(frozen=True, slots=True)
class ProposalPolicyBinding:
    """Immutable association for a ProposalSet; it changes no evidence."""

    proposal_set_id: str
    resolved_policy_id: str


@dataclass(frozen=True, slots=True)
class PolicyTransition:
    transition_id: str
    from_policy_id: str
    to_policy_id: str
    generation: int
    state: str
    completed: int
    total: int
    failed: int
    acknowledged: bool


@dataclass(frozen=True, slots=True)
class PolicyActivation:
    active: MetadataPolicyProfile
    transition: PolicyTransition | None


class PolicyImpactRepository(Protocol):
    def current_unapplied_count(self, library: str) -> int: ...

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]: ...

    def create_metadata_policy_draft(self, document: str) -> str: ...

    def get_metadata_policy_draft(self, policy_id: str) -> str: ...

    def active_metadata_policy(self) -> str: ...

    def activate_metadata_policy(
        self,
        policy_id: str,
        expected_active_policy_id: str,
        resolutions: tuple[tuple[str, str, str], ...],
        activated_at: int,
    ) -> PolicyTransition | None: ...

    def metadata_policy_transition(self) -> PolicyTransition | None: ...

    def retry_metadata_policy_transition(self, transition_id: str, retried_at: int) -> PolicyTransition: ...

    def acknowledge_metadata_policy_transition(self, transition_id: str, acknowledged_at: int) -> PolicyTransition: ...

    def current_resolved_policy_id(self, library: str) -> str: ...

    def current_resolved_metadata_policy(self, library: str) -> ResolvedMetadataPolicy | None: ...

    def metadata_policy_resolution_for_preview(self, library: str) -> ResolvedMetadataPolicy | None: ...

    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, ResolvedMetadataPolicy | None], ...]]: ...

    def shadow_posture(self) -> str: ...

    def enter_shadow_preview(self, updated_at: int) -> str: ...
    def activate_qualified_automatic(self, updated_at: int) -> str: ...

    def list_shadow_outcomes(self, limit: int) -> tuple[tuple[str, int], ...]: ...


def preview_policy(profile: MetadataPolicyProfile, libraries: tuple[str, ...], overrides: dict[str, dict[str, object]], repository: PolicyImpactRepository) -> PolicyPreview:
    if type(profile) is not MetadataPolicyProfile or type(libraries) is not tuple or libraries != tuple(sorted(set(libraries))):
        raise MetadataPolicyApplicationError("policy preview is invalid")
    impacts: list[LibraryPolicyImpact] = []
    remaining_cases = MAX_RICH_PREVIEW_CASES
    try:
        if hasattr(repository, "metadata_policy_preview_baseline"):
            active_document, persisted_baseline = (
                repository.metadata_policy_preview_baseline(libraries)
            )
        else:
            active_document = (
                repository.active_metadata_policy()
                if hasattr(repository, "active_metadata_policy")
                else MetadataPolicyProfile.builtin().canonical_json
            )
            persisted_baseline = tuple(
                (
                    library,
                    repository.metadata_policy_resolution_for_preview(library)
                    if hasattr(repository, "metadata_policy_resolution_for_preview")
                    else None,
                )
                for library in libraries
            )
        active_profile = MetadataPolicyProfile.from_data(json.loads(active_document))
        active_resolutions = dict(persisted_baseline)
        if tuple(sorted(active_resolutions)) != libraries:
            raise MetadataPolicyApplicationError("policy preview is unavailable")
        for library in libraries:
            resolved = resolve_policy(profile, library, overrides.get(library))
            # Existing libraries compare with the generation-bound resolution
            # actually in force. A newly configured library has no persisted
            # resolution yet and is resolved from the same active-profile
            # snapshot using its current override.
            active = active_resolutions[library] or resolve_policy(
                active_profile, library, overrides.get(library)
            )
            # Compatibility callers expose this aggregate separately; the
            # concrete repository below proves it equals the read snapshot.
            count = repository.current_unapplied_count(library)
            if type(count) is not int or count < 0:
                raise MetadataPolicyApplicationError("policy preview is unavailable")
            resolved_options = dict(resolved.options)
            resolved_destinations = dict(resolved.destinations)
            active_options = dict(active.options)
            active_destinations = dict(active.destinations)
            categories = tuple(
                field
                for field in OPTION_CATALOGUE
                if resolved_options[field] != active_options[field]
                or resolved_destinations[field] != active_destinations[field]
            )
            pairs_total, pairs = (
                (count, ())
                if remaining_cases == 0
                else repository.current_policy_impact_pairs(
                    library, min(20, remaining_cases)
                )
                if hasattr(repository, "current_policy_impact_pairs")
                else (0, ())
            )
            if type(pairs_total) is not int or pairs_total < 0 or type(pairs) is not tuple or len(pairs) > 20:
                raise MetadataPolicyApplicationError("policy preview is unavailable")
            if hasattr(repository, "current_policy_impact_pairs"):
                count = pairs_total
            # The stored pair proves exact-current lineage. Re-evaluate its
            # immutable report through the existing pure proposal compiler so
            # a changed draft receives a new exact policy binding without
            # mutating or retargeting the persisted proposal.
            if any(
                proposal.report_id != report.report_id
                or proposal.target_fingerprint != report.target.fingerprint
                for report, proposal in pairs
            ):
                raise MetadataPolicyApplicationError(
                    "policy preview is unavailable"
                )
            cases = tuple(
                compile_rich_metadata_preview(
                    resolved,
                    report,
                    evaluate_report(
                        report,
                        resolved.resolved_id,
                        dict(resolved.destinations),
                    ),
                )
                for report, _ in pairs
            )
            remaining_cases -= len(cases)
            work_labels = tuple(
                report.target.current_title or "Untitled" for report, _ in pairs
            )
            impacts.append(LibraryPolicyImpact(
                library=library,
                affected_categories=categories,
                current_unapplied_count=count,
                resolved_policy_id=resolved.resolved_id,
                cases=cases,
                cases_total=pairs_total,
                cases_truncated=pairs_total > len(pairs),
                work_labels=work_labels,
            ))
    except MetadataPolicyApplicationError:
        raise
    except (MetadataPolicyError, Exception) as error:
        raise MetadataPolicyApplicationError("policy preview is unavailable") from error
    return PolicyPreview(profile.policy_id, active_profile.policy_id, (), tuple(impacts))


def bind_proposal_policy(proposal_set_id: object, policy: ResolvedMetadataPolicy) -> ProposalPolicyBinding:
    if type(proposal_set_id) is not str or not proposal_set_id.startswith("proposal_set_") or len(proposal_set_id) != 77 or type(policy) is not ResolvedMetadataPolicy:
        raise MetadataPolicyApplicationError("proposal policy binding is invalid")
    return ProposalPolicyBinding(proposal_set_id, policy.resolved_id)


class PolicyNotebook:
    """Read-only policy notebook plus immutable draft creation.

    Configuration supplies only named-library sparse overrides and is never
    written here. Activation is exact-draft and exact-resolution bound.
    """

    def __init__(self, repository: PolicyImpactRepository, libraries: tuple[str, ...], overrides: dict[str, dict[str, object]]) -> None:
        if type(libraries) is not tuple or libraries != tuple(sorted(set(libraries))) or any(type(value) is not str for value in libraries):
            raise MetadataPolicyApplicationError("policy notebook is invalid")
        self._repository = repository
        self._libraries = libraries
        self._overrides = overrides

    def active(self) -> MetadataPolicyProfile:
        try:
            if not hasattr(self._repository, "active_metadata_policy"):
                return MetadataPolicyProfile.builtin()
            return MetadataPolicyProfile.from_data(
                json.loads(self._repository.active_metadata_policy())
            )
        except Exception as error:
            raise MetadataPolicyApplicationError("active policy is unavailable") from error

    @staticmethod
    def rich_qualification(profile: MetadataPolicyProfile) -> tuple[dict[str, object], ...]:
        """Expose only the fixed public F20 terminal dispositions."""
        if type(profile) is not MetadataPolicyProfile:
            raise MetadataPolicyApplicationError("policy validation is invalid")
        return public_summary()

    def validate(self, profile: MetadataPolicyProfile) -> MetadataPolicyProfile:
        """Allow Automatic intent only while it is separately qualified."""
        if type(profile) is not MetadataPolicyProfile:
            raise MetadataPolicyApplicationError("policy validation is invalid")
        requested = {
            f"{field}:{option}"
            for field, option in profile.options
            if dict(profile.destinations)[field] == "automatic"
        }
        rich_requested = {
            f"{field}:{option}"
            for field, option in profile.options
            if field in F20_RICH_FIELDS
            and dict(profile.destinations)[field] == "automatic"
        }
        if (
            requested and not requested.issubset(QUALIFIED_POLICY_OPTIONS)
        ) or not rich_requested.issubset(F20_PASSING_AUTOMATIC_OPTIONS):
            raise MetadataPolicyApplicationError("policy automatic unqualified")
        return profile

    def create(self, name: object, options: object, destinations: object) -> MetadataPolicyProfile:
        try:
            profile = self.validate(
                MetadataPolicyProfile.create(name, options, destinations)
            )
            policy_id = self._repository.create_metadata_policy_draft(profile.canonical_json)
            if policy_id != profile.policy_id:
                raise MetadataPolicyApplicationError("policy draft is unavailable")
            return profile
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            if "automatic intent is unqualified" in str(error):
                raise MetadataPolicyApplicationError(
                    "policy automatic unqualified"
                ) from error
            raise MetadataPolicyApplicationError("policy draft is unavailable") from error

    def read(self, policy_id: object) -> MetadataPolicyProfile:
        if type(policy_id) is not str or not policy_id.startswith("policy_"):
            raise MetadataPolicyApplicationError("policy draft is unavailable")
        try:
            profile = MetadataPolicyProfile.from_data(json.loads(self._repository.get_metadata_policy_draft(policy_id)))
            if profile.policy_id != policy_id:
                raise MetadataPolicyApplicationError("policy draft is unavailable")
            return profile
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            raise MetadataPolicyApplicationError("policy draft is unavailable") from error

    def preview(self, profile: MetadataPolicyProfile) -> PolicyPreview:
        return preview_policy(
            self.validate(profile),
            self._libraries,
            self._overrides,
            self._repository,
        )

    def shadow_state(self) -> dict[str, object]:
        try:
            posture = (
                self._repository.shadow_posture()
                if hasattr(self._repository, "shadow_posture")
                else "review"
            )
            if posture not in {"review", "preview", "automatic"}:
                raise ValueError
            return {
                "authority": "decision_only" if posture == "automatic" else "none",
                "can_leave_preview": False,
                "posture": posture,
                "schema_version": "shadow-posture-v1",
            }
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "shadow posture is unavailable"
            ) from error

    def confirm_automatic(self, updated_at: object) -> dict[str, object]:
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataPolicyApplicationError("automatic posture is invalid")
        try:
            if self._repository.activate_qualified_automatic(updated_at) != "automatic":
                raise ValueError
            return self.shadow_state()
        except Exception as error:
            raise MetadataPolicyApplicationError("automatic posture is unavailable") from error

    def enter_preview(self, updated_at: object) -> dict[str, object]:
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataPolicyApplicationError("shadow posture is invalid")
        try:
            if self._repository.enter_shadow_preview(updated_at) != "preview":
                raise ValueError
            return self.shadow_state()
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "shadow posture is unavailable"
            ) from error

    def preview_leave(self) -> dict[str, object]:
        """Describe leaving Preview; this increment has no commit method."""
        active = self.active()
        impacts = []
        for library in self._libraries:
            resolved = resolve_policy(active, library, self._overrides.get(library))
            impacts.append(
                {
                    "affected_categories": sorted(
                        field
                        for field, destination in resolved.destinations
                        if destination == "automatic"
                    ),
                    "current_unapplied_count": self._repository.current_unapplied_count(
                        library
                    ),
                    "library": library,
                }
            )
        return {
            "authority": "none",
            "can_confirm": False,
            "confirmation_required": True,
            "libraries": impacts,
            "posture": self.shadow_state()["posture"],
            "requested_posture": "qualified_decision_only_unavailable",
            "schema_version": "shadow-posture-leave-preview-v1",
        }

    def shadow_sample(self, limit: object = 20) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MetadataPolicyApplicationError("shadow sample is invalid")
        try:
            items: list[dict[str, object]] = []
            for document, evaluated_at in self._repository.list_shadow_outcomes(
                limit
            ):
                candidate = {
                    **ShadowOutcomeSet.from_json(document).to_data(),
                    "evaluated_at": evaluated_at,
                }
                bounded = {
                    "authority": "none",
                    "items": [*items, candidate],
                    "next_cursor": None,
                    "schema_version": "shadow-outcome-sample-v1",
                }
                if (
                    len(
                        json.dumps(
                            bounded,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    > MAX_SHADOW_SAMPLE_RESPONSE_BYTES
                ):
                    if not items:
                        raise ValueError
                    break
                items.append(candidate)
            return {
                "authority": "none",
                "items": items,
                "next_cursor": None,
                "schema_version": "shadow-outcome-sample-v1",
            }
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "shadow sample is unavailable"
            ) from error

    def activate(
        self,
        policy_id: object,
        expected_active_policy_id: object,
        activated_at: object,
    ) -> PolicyActivation:
        if (
            type(policy_id) is not str
            or type(expected_active_policy_id) is not str
            or type(activated_at) is not int
            or activated_at < 0
        ):
            raise MetadataPolicyApplicationError("policy activation is invalid")
        profile = self.validate(self.read(policy_id))
        try:
            resolutions = tuple(
                (
                    library,
                    resolved.resolved_id,
                    resolved.canonical_json,
                )
                for library in self._libraries
                for resolved in (
                    resolve_policy(profile, library, self._overrides.get(library)),
                )
            )
            requested = {
                f"{category}:{dict(resolved.options)[category]}"
                for _, _, document in resolutions
                for resolved in (ResolvedMetadataPolicy.from_data(json.loads(document)),)
                for category, destination in resolved.destinations
                if destination == "automatic"
            }
            if not requested.issubset(QUALIFIED_POLICY_OPTIONS):
                raise MetadataPolicyApplicationError("policy automatic unqualified")
            transition = self._repository.activate_metadata_policy(
                policy_id, expected_active_policy_id, resolutions, activated_at
            )
            return PolicyActivation(profile, transition)
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            if "stale" in str(error):
                raise MetadataPolicyApplicationError("policy activation is stale") from error
            if "processing" in str(error):
                raise MetadataPolicyApplicationError(
                    "policy transition is processing"
                ) from error
            if "unqualified" in str(error):
                raise MetadataPolicyApplicationError("policy activation is unqualified") from error
            raise MetadataPolicyApplicationError("policy activation is unavailable") from error

    def transition(self) -> PolicyTransition | None:
        try:
            return self._repository.metadata_policy_transition()
        except Exception as error:
            raise MetadataPolicyApplicationError("policy transition is unavailable") from error

    def retry(self, transition_id: object, retried_at: object) -> PolicyTransition:
        if type(transition_id) is not str or type(retried_at) is not int or retried_at < 0:
            raise MetadataPolicyApplicationError("policy transition is invalid")
        try:
            return self._repository.retry_metadata_policy_transition(transition_id, retried_at)
        except Exception as error:
            if "stale" in str(error):
                raise MetadataPolicyApplicationError("policy transition is stale") from error
            raise MetadataPolicyApplicationError("policy transition is unavailable") from error

    def acknowledge(self, transition_id: object, acknowledged_at: object) -> PolicyTransition:
        if type(transition_id) is not str or type(acknowledged_at) is not int or acknowledged_at < 0:
            raise MetadataPolicyApplicationError("policy transition is invalid")
        try:
            return self._repository.acknowledge_metadata_policy_transition(transition_id, acknowledged_at)
        except Exception as error:
            raise MetadataPolicyApplicationError("policy transition is unavailable") from error


__all__ = ["LibraryPolicyImpact", "MAX_SHADOW_SAMPLE_RESPONSE_BYTES", "MetadataPolicyApplicationError", "PolicyActivation", "PolicyImpactRepository", "PolicyNotebook", "PolicyPreview", "PolicyTransition", "ProposalPolicyBinding", "bind_proposal_policy", "preview_policy"]
