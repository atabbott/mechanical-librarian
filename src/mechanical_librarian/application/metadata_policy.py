"""Draft-policy validation, impact projection, and proposal binding seams."""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["LibraryPolicyImpact", "MetadataPolicyApplicationError", "PolicyImpactRepository", "PolicyPreview", "ProposalPolicyBinding", "bind_proposal_policy", "preview_policy"]
