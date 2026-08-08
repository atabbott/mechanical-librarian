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


class PolicyImpactRepository(Protocol):
    def current_unapplied_count(self, library: str) -> int: ...

    def create_metadata_policy_draft(self, document: str) -> str: ...

    def get_metadata_policy_draft(self, policy_id: str) -> str: ...


def preview_policy(profile: MetadataPolicyProfile, libraries: tuple[str, ...], overrides: dict[str, dict[str, object]], repository: PolicyImpactRepository) -> PolicyPreview:
    if type(profile) is not MetadataPolicyProfile or type(libraries) is not tuple or libraries != tuple(sorted(set(libraries))):
        raise MetadataPolicyApplicationError("policy preview is invalid")
    impacts: list[LibraryPolicyImpact] = []
    try:
        for library in libraries:
            resolved = resolve_policy(profile, library, overrides.get(library))
            count = repository.current_unapplied_count(library)
            if type(count) is not int or count < 0:
                raise MetadataPolicyApplicationError("policy preview is unavailable")
            categories = tuple(field for field in OPTION_CATALOGUE if dict(resolved.options)[field] != "preserve" or dict(resolved.destinations)[field] != "ask")
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

    This deliberately has no active-policy setter: activation belongs to the
    following cutover slice.  Configuration supplies only named-library sparse
    overrides and is never written here.
    """

    def __init__(self, repository: PolicyImpactRepository, libraries: tuple[str, ...], overrides: dict[str, dict[str, object]]) -> None:
        if type(libraries) is not tuple or libraries != tuple(sorted(set(libraries))) or any(type(value) is not str for value in libraries):
            raise MetadataPolicyApplicationError("policy notebook is invalid")
        self._repository = repository
        self._libraries = libraries
        self._overrides = overrides

    def active(self) -> MetadataPolicyProfile:
        return MetadataPolicyProfile.builtin()

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


__all__ = ["LibraryPolicyImpact", "MetadataPolicyApplicationError", "PolicyImpactRepository", "PolicyNotebook", "PolicyPreview", "ProposalPolicyBinding", "bind_proposal_policy", "preview_policy"]
