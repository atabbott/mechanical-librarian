"""Draft-policy validation, impact projection, and proposal binding seams."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from mechanical_librarian.domain.metadata_policy import MetadataPolicyError, MetadataPolicyProfile, OPTION_CATALOGUE, ResolvedMetadataPolicy, resolve_policy


class MetadataPolicyApplicationError(RuntimeError):
    """A fixed, value-free draft policy failure."""


@dataclass(frozen=True, slots=True)
class LibraryPolicyImpact:
    library: str
    affected_categories: tuple[str, ...]
    current_unapplied_count: int
    resolved_policy_id: str


@dataclass(frozen=True, slots=True)
class PolicyPreview:
    profile_id: str
    validation: tuple[str, ...]
    libraries: tuple[LibraryPolicyImpact, ...]


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


def preview_policy(profile: MetadataPolicyProfile, libraries: tuple[str, ...], overrides: dict[str, dict[str, object]], repository: PolicyImpactRepository) -> PolicyPreview:
    if type(profile) is not MetadataPolicyProfile or type(libraries) is not tuple or libraries != tuple(sorted(set(libraries))):
        raise MetadataPolicyApplicationError("policy preview is invalid")
    impacts: list[LibraryPolicyImpact] = []
    try:
        for library in libraries:
            resolved = resolve_policy(profile, library, overrides.get(library))
            active_document = (
                repository.active_metadata_policy()
                if hasattr(repository, "active_metadata_policy")
                else MetadataPolicyProfile.builtin().canonical_json
            )
            persisted_active = (
                repository.current_resolved_metadata_policy(library)
                if hasattr(repository, "current_resolved_metadata_policy")
                else None
            )
            active = persisted_active or resolve_policy(
                MetadataPolicyProfile.from_data(json.loads(active_document)),
                library,
                overrides.get(library),
            )
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
            impacts.append(LibraryPolicyImpact(library, categories, count, resolved.resolved_id))
    except MetadataPolicyApplicationError:
        raise
    except (MetadataPolicyError, Exception) as error:
        raise MetadataPolicyApplicationError("policy preview is unavailable") from error
    return PolicyPreview(profile.policy_id, (), tuple(impacts))


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

    def create(self, name: object, options: object, destinations: object) -> MetadataPolicyProfile:
        try:
            profile = MetadataPolicyProfile.create(name, options, destinations)
            policy_id = self._repository.create_metadata_policy_draft(profile.canonical_json)
            if policy_id != profile.policy_id:
                raise MetadataPolicyApplicationError("policy draft is unavailable")
            return profile
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
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
        return preview_policy(profile, self._libraries, self._overrides, self._repository)

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
        profile = self.read(policy_id)
        if any(value == "automatic" for _, value in profile.destinations):
            raise MetadataPolicyApplicationError("policy activation is unqualified")
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
            transition = self._repository.activate_metadata_policy(
                policy_id, expected_active_policy_id, resolutions, activated_at
            )
            return PolicyActivation(profile, transition)
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            if "stale" in str(error):
                raise MetadataPolicyApplicationError("policy activation is stale") from error
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


__all__ = ["LibraryPolicyImpact", "MetadataPolicyApplicationError", "PolicyActivation", "PolicyImpactRepository", "PolicyNotebook", "PolicyPreview", "PolicyTransition", "ProposalPolicyBinding", "bind_proposal_policy", "preview_policy"]
