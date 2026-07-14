"""Closed, apply-ineligible title proposal preview values.

The F09a preview deliberately has no review or apply authority.  Its persisted
identity binds the complete synthetic input manifest and can only represent the
single ``synthetic_preview`` execution mode.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Self


TITLE_PROPOSAL_SCHEMA = "title-proposal-set-v1"
TITLE_COMPARISON_VERSION = "unicode-title-comparison-v1"
TITLE_PROPOSAL_POLICY_VERSION = "synthetic-title-policy-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class TitleProposalValueError(ValueError):
    """A proposal set or its persisted representation is invalid."""


class ProposalExecutionMode(StrEnum):
    SYNTHETIC_PREVIEW = "synthetic_preview"


class TitleProposalResult(StrEnum):
    NEEDS_REVIEW = "needs_review"
    NO_CHANGE = "no_change"
    CURRENT_MISSING = "current_missing"
    CURRENT_UNAVAILABLE = "current_unavailable"
    LOCAL_PROVIDER_CONFLICT = "local_provider_conflict"
    F06_MISSING = "f06_missing"
    F06_AMBIGUOUS = "f06_ambiguous"
    F06_UNUSABLE = "f06_unusable"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_EMPTY = "provider_empty"
    PROVIDER_NO_MATCH = "provider_no_match"
    STALE_INPUT = "stale_input"


class SyntheticTitleState(StrEnum):
    LOADED = "loaded"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SyntheticTitleProposal:
    id: str
    current_value: str | None
    proposed_value: str
    kind: str
    disposition: str = "needs_review"

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.id):
            raise TitleProposalValueError("proposal ID is invalid")
        if self.kind not in {"fill_empty", "replace_populated"}:
            raise TitleProposalValueError("proposal kind is invalid")
        if self.disposition != "needs_review":
            raise TitleProposalValueError("preview proposals require review")
        _title(self.proposed_value, "proposed title")
        if self.current_value is not None:
            _title(self.current_value, "current title")
        if self.kind == "fill_empty" and self.current_value is not None:
            raise TitleProposalValueError("fill-empty proposal has a current value")
        if self.kind == "replace_populated" and self.current_value is None:
            raise TitleProposalValueError("replacement proposal lacks a current value")

    def to_data(self) -> dict[str, object]:
        return {
            "current_value": self.current_value,
            "disposition": self.disposition,
            "field": "title",
            "id": self.id,
            "kind": self.kind,
            "proposed_value": self.proposed_value,
        }


@dataclass(frozen=True)
class TitleProposalSet:
    set_id: str
    identity_digest: str
    scenario: str
    lineage: str
    lineage_revision: int
    previous_set_id: str | None
    result: TitleProposalResult
    manifest: Mapping[str, object]
    proposals: tuple[SyntheticTitleProposal, ...]
    mode: ProposalExecutionMode = ProposalExecutionMode.SYNTHETIC_PREVIEW

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.set_id):
            raise TitleProposalValueError("proposal-set ID is invalid")
        if not _DIGEST.fullmatch(self.identity_digest):
            raise TitleProposalValueError("proposal-set digest is invalid")
        if not _IDENTIFIER.fullmatch(self.scenario) or not _IDENTIFIER.fullmatch(self.lineage):
            raise TitleProposalValueError("proposal-set scope is invalid")
        if type(self.lineage_revision) is not int or self.lineage_revision < 1:
            raise TitleProposalValueError("proposal-set revision is invalid")
        if self.previous_set_id is not None and not _IDENTIFIER.fullmatch(self.previous_set_id):
            raise TitleProposalValueError("proposal-set predecessor is invalid")
        if type(self.result) is not TitleProposalResult:
            raise TitleProposalValueError("proposal-set result is invalid")
        if type(self.mode) is not ProposalExecutionMode or self.mode is not ProposalExecutionMode.SYNTHETIC_PREVIEW:
            raise TitleProposalValueError("proposal-set mode is invalid")
        if not isinstance(self.manifest, Mapping):
            raise TitleProposalValueError("proposal-set manifest is invalid")
        try:
            object.__setattr__(self, "manifest", _deep_freeze(self.manifest))
        except (TypeError, ValueError, RecursionError):
            raise TitleProposalValueError("proposal-set manifest is invalid") from None
        if type(self.proposals) is not tuple or any(type(item) is not SyntheticTitleProposal for item in self.proposals):
            raise TitleProposalValueError("proposal-set proposals are invalid")
        if len(self.proposals) > 1:
            raise TitleProposalValueError("title preview permits at most one proposal")
        if (self.result is TitleProposalResult.NEEDS_REVIEW) != (len(self.proposals) == 1):
            raise TitleProposalValueError("proposal count does not match result")
        if self.result is TitleProposalResult.NO_CHANGE and self.proposals:
            raise TitleProposalValueError("no-change result cannot carry a proposal")
        required_manifest = {
            "comparison_version", "epub_digest", "f06", "fixture_version",
            "generator_version", "lineage", "lineage_revision", "mode",
            "opaque_file_identity", "policy_version", "proposal_schema",
            "previous_set_id", "provider", "scenario", "scenario_revision", "snapshot",
            "unicode_version",
        }
        if set(self.manifest) != required_manifest:
            raise TitleProposalValueError("proposal-set manifest has unknown or missing fields")
        if (
            self.manifest["mode"] != self.mode.value
            or self.manifest["scenario"] != self.scenario
            or self.manifest["lineage"] != self.lineage
            or self.manifest["lineage_revision"] != self.lineage_revision
            or self.manifest["previous_set_id"] != self.previous_set_id
            or self.manifest["proposal_schema"] != TITLE_PROPOSAL_SCHEMA
            or self.manifest["comparison_version"] != TITLE_COMPARISON_VERSION
            or self.manifest["policy_version"] != TITLE_PROPOSAL_POLICY_VERSION
        ):
            raise TitleProposalValueError("proposal-set manifest disagrees with its envelope")
        _validate_manifest(self.manifest)
        _validate_proposal_projection(self.manifest, self.result, self.proposals)
        expected = _identity_digest(self.manifest, self.result, self.proposals)
        if expected != self.identity_digest or self.set_id != f"ps-{expected[:60]}":
            raise TitleProposalValueError("proposal-set identity does not match its semantics")

    @property
    def production_applicable(self) -> bool:
        return False

    def to_data(self) -> dict[str, object]:
        return {
            "identity_digest": self.identity_digest,
            "lineage": self.lineage,
            "lineage_revision": self.lineage_revision,
            "manifest": _deep_thaw(self.manifest),
            "mode": self.mode.value,
            "previous_set_id": self.previous_set_id,
            "production_applicable": self.production_applicable,
            "proposals": [item.to_data() for item in self.proposals],
            "result": self.result.value,
            "scenario": self.scenario,
            "schema": TITLE_PROPOSAL_SCHEMA,
            "set_id": self.set_id,
        }

    def to_json(self) -> str:
        serialized = _canonical_json(self.to_data())
        if len(serialized.encode("utf-8")) > 131_072:
            raise TitleProposalValueError("proposal-set report is too large")
        return serialized

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if type(serialized) is not str or len(serialized.encode("utf-8")) > 131_072:
            raise TitleProposalValueError("proposal-set JSON is invalid")
        try:
            value = json.loads(serialized, object_pairs_hook=_unique_object)
            if type(value) is not dict or set(value) != {
                "identity_digest", "lineage", "lineage_revision", "manifest", "mode",
                "previous_set_id", "production_applicable", "proposals", "result",
                "scenario", "schema", "set_id",
            }:
                raise TitleProposalValueError("proposal-set JSON has unknown or missing fields")
            if value["schema"] != TITLE_PROPOSAL_SCHEMA or value["production_applicable"] is not False:
                raise TitleProposalValueError("proposal-set JSON has invalid authority")
            proposals_value = value["proposals"]
            if type(proposals_value) is not list:
                raise TitleProposalValueError("proposal-set proposals are invalid")
            proposals: list[SyntheticTitleProposal] = []
            for proposal in proposals_value:
                if type(proposal) is not dict or set(proposal) != {
                    "current_value", "disposition", "field", "id", "kind", "proposed_value"
                } or proposal["field"] != "title":
                    raise TitleProposalValueError("proposal-set proposal is invalid")
                proposals.append(SyntheticTitleProposal(
                    id=proposal["id"], current_value=proposal["current_value"],
                    proposed_value=proposal["proposed_value"], kind=proposal["kind"],
                    disposition=proposal["disposition"],
                ))
            result = cls(
                set_id=value["set_id"], identity_digest=value["identity_digest"],
                scenario=value["scenario"], lineage=value["lineage"],
                lineage_revision=value["lineage_revision"], previous_set_id=value["previous_set_id"],
                result=TitleProposalResult(value["result"]), manifest=value["manifest"],
                proposals=tuple(proposals), mode=ProposalExecutionMode(value["mode"]),
            )
        except TitleProposalValueError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
            raise TitleProposalValueError("proposal-set JSON is invalid") from None
        if result.to_json() != serialized:
            raise TitleProposalValueError("proposal-set JSON is not canonical")
        return result


def normalized_title_key(value: str) -> str:
    """Return the versioned NFC/casefold/NFC/Unicode-space comparison key."""

    _title(value, "title")
    folded = unicodedata.normalize("NFC", value).casefold()
    normalized = unicodedata.normalize("NFC", folded)
    return " ".join(normalized.split())


def proposal_set_from_manifest(
    *, manifest: dict[str, object], result: TitleProposalResult,
    current_value: str | None = None, proposed_value: str | None = None,
    previous_set_id: str | None = None,
) -> TitleProposalSet:
    proposals: tuple[SyntheticTitleProposal, ...] = ()
    if result is TitleProposalResult.NEEDS_REVIEW:
        if proposed_value is None:
            raise TitleProposalValueError("needs-review result requires a proposed title")
        kind = "fill_empty" if current_value is None else "replace_populated"
        proposals = (SyntheticTitleProposal(
            id=_deterministic_proposal_id(
                manifest,
                result,
                current_value=current_value,
                proposed_value=proposed_value,
                kind=kind,
                disposition="needs_review",
            ),
            current_value=current_value,
            proposed_value=proposed_value,
            kind=kind,
        ),)
    digest = _identity_digest(manifest, result, proposals)
    return TitleProposalSet(
        set_id=f"ps-{digest[:60]}", identity_digest=digest,
        scenario=manifest["scenario"], lineage=manifest["lineage"],
        lineage_revision=manifest["lineage_revision"], previous_set_id=previous_set_id,
        result=result, manifest=manifest, proposals=proposals,
    )


def _identity_digest(
    manifest: Mapping[str, object],
    result: TitleProposalResult,
    proposals: tuple[SyntheticTitleProposal, ...],
) -> str:
    payload = {
        "manifest": _deep_thaw(manifest),
        "proposals": [proposal.to_data() for proposal in proposals],
        "result": result.value,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _deterministic_proposal_id(
    manifest: Mapping[str, object],
    result: TitleProposalResult,
    *,
    current_value: str | None,
    proposed_value: str,
    kind: str,
    disposition: str,
) -> str:
    # Proposal identity has its own non-circular semantic seed. The proposal ID
    # is then included in the proposal-set identity payload above.
    seed = {
        "manifest": _deep_thaw(manifest),
        "proposal": {
            "current_value": current_value,
            "disposition": disposition,
            "field": "title",
            "kind": kind,
            "proposed_value": proposed_value,
        },
        "result": result.value,
    }
    digest = hashlib.sha256(_canonical_json(seed).encode("utf-8")).hexdigest()
    return f"proposal-{digest[:55]}"


def _validate_proposal_projection(
    manifest: Mapping[str, object],
    result: TitleProposalResult,
    proposals: tuple[SyntheticTitleProposal, ...],
) -> None:
    if result is not TitleProposalResult.NEEDS_REVIEW:
        if proposals:
            raise TitleProposalValueError("zero-proposal result carries a proposal")
        return
    if len(proposals) != 1:
        raise TitleProposalValueError("needs-review result requires one proposal")
    proposal = proposals[0]
    f06 = manifest["f06"]
    snapshot = manifest["snapshot"]
    if not isinstance(f06, Mapping) or not isinstance(snapshot, Mapping):
        raise TitleProposalValueError("proposal-set semantic source is invalid")
    observation = f06["chosen_observation"]
    shape = snapshot["shape"]
    if not isinstance(observation, Mapping) or not isinstance(shape, Mapping):
        raise TitleProposalValueError("needs-review result lacks selected evidence")
    metadata = shape["metadata"]
    if not isinstance(metadata, Mapping):
        raise TitleProposalValueError("proposal snapshot metadata is invalid")
    proposed_value = observation["normalized_value"]
    current_value = metadata["title"]
    if type(proposed_value) is not str:
        raise TitleProposalValueError("needs-review evidence lacks a normalized title")
    kind = "fill_empty" if current_value is None else "replace_populated"
    expected_id = _deterministic_proposal_id(
        manifest,
        result,
        current_value=current_value,
        proposed_value=proposed_value,
        kind=kind,
        disposition="needs_review",
    )
    if (
        proposal.current_value != current_value
        or proposal.proposed_value != proposed_value
        or proposal.kind != kind
        or proposal.disposition != "needs_review"
        or proposal.id != expected_id
    ):
        raise TitleProposalValueError("proposal is not the deterministic evidence projection")


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if type(value) in {list, tuple}:
        return tuple(_deep_freeze(item) for item in value)
    if value is None or type(value) in {str, int, bool}:
        return value
    raise TypeError("manifest contains an unsupported value")


def _deep_thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_deep_thaw(item) for item in value]
    return value


def _title(value: object, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 16_384
        or "\x00" in value
        or any(ord(character) < 32 and character not in "\t\n\r" for character in value)
    ):
        raise TitleProposalValueError(f"{label} is invalid")
    return value


def _validate_manifest(value: Mapping[str, object]) -> None:
    for field in (
        "fixture_version", "generator_version", "unicode_version"
    ):
        item = value[field]
        if type(item) is not str or not item or len(item) > 64:
            raise TitleProposalValueError("proposal-set manifest version is invalid")
    if value["fixture_version"] != "synthetic-title-scenarios-v1" or value["generator_version"] != "synthetic-epub-generator-v1":
        raise TitleProposalValueError("proposal-set manifest version is unsupported")
    if type(value["scenario_revision"]) is not int or value["scenario_revision"] != 1:
        raise TitleProposalValueError("proposal-set scenario revision is unsupported")
    for field in ("epub_digest", "opaque_file_identity"):
        if type(value[field]) is not str or _DIGEST.fullmatch(value[field]) is None:
            raise TitleProposalValueError("proposal-set manifest digest is invalid")

    f06 = value["f06"]
    if not isinstance(f06, Mapping) or set(f06) != {
        "chosen_citation", "chosen_observation", "extractor_versions",
        "report_identity", "report_schema", "source_fingerprint",
    }:
        raise TitleProposalValueError("proposal-set F06 manifest is invalid")
    for field in ("report_identity", "source_fingerprint"):
        if type(f06[field]) is not str or _DIGEST.fullmatch(f06[field]) is None:
            raise TitleProposalValueError("proposal-set F06 identity is invalid")
    if f06["source_fingerprint"] != value["epub_digest"]:
        raise TitleProposalValueError("proposal-set F06 source does not match the EPUB")
    if type(f06["report_schema"]) is not str or not f06["report_schema"]:
        raise TitleProposalValueError("proposal-set F06 schema is invalid")
    versions = f06["extractor_versions"]
    if type(versions) is not tuple or len(versions) > 16 or any(type(item) is not str or not item for item in versions):
        raise TitleProposalValueError("proposal-set F06 versions are invalid")
    citation = f06["chosen_citation"]
    observation = f06["chosen_observation"]
    if (citation is None) != (observation is None):
        raise TitleProposalValueError("proposal-set F06 selection is incomplete")
    if citation is not None:
        if not isinstance(citation, Mapping) or set(citation) != {
            "expanded_name", "resource_identity", "section", "selector", "source_ordinal"
        }:
            raise TitleProposalValueError("proposal-set F06 citation is invalid")
        if type(citation["resource_identity"]) is not str or _DIGEST.fullmatch(citation["resource_identity"]) is None:
            raise TitleProposalValueError("proposal-set F06 citation identity is invalid")
        if type(citation["source_ordinal"]) is not int or citation["source_ordinal"] < 1:
            raise TitleProposalValueError("proposal-set F06 citation order is invalid")
        if not isinstance(observation, Mapping) or set(observation) != {
            "applicability", "compatibility_version", "extractor_version",
            "normalized_value", "raw_value", "schema_version",
        }:
            raise TitleProposalValueError("proposal-set F06 observation is invalid")
        _title(observation["raw_value"], "F06 raw title")
        if observation["normalized_value"] is not None:
            _title(observation["normalized_value"], "F06 normalized title")

    provider = value["provider"]
    if not isinstance(provider, Mapping) or set(provider) != {"candidate", "outcome", "version"}:
        raise TitleProposalValueError("proposal-set provider manifest is invalid")
    if provider["outcome"] not in {"candidate", "disabled", "failed", "empty", "no_match", "not_invoked"} or provider["version"] != "synthetic-title-provider-v1":
        raise TitleProposalValueError("proposal-set provider outcome is invalid")
    candidate = provider["candidate"]
    if candidate is not None:
        if not isinstance(candidate, Mapping) or set(candidate) != {
            "correlation_id", "field", "fingerprint", "source_id", "values"
        }:
            raise TitleProposalValueError("proposal-set provider candidate is invalid")
        if candidate["field"] != "title" or type(candidate["fingerprint"]) is not str or _DIGEST.fullmatch(candidate["fingerprint"]) is None:
            raise TitleProposalValueError("proposal-set provider candidate identity is invalid")
        candidate_values = candidate["values"]
        if type(candidate_values) is not tuple or len(candidate_values) != 1:
            raise TitleProposalValueError("proposal-set provider title is invalid")
        _title(candidate_values[0], "provider title")
    if (provider["outcome"] == "candidate") != (candidate is not None):
        raise TitleProposalValueError("proposal-set provider candidate disagrees with outcome")

    snapshot = value["snapshot"]
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "current_fingerprint", "fingerprint", "id", "schema", "shape"
    }:
        raise TitleProposalValueError("proposal-set snapshot manifest is invalid")
    for field in ("current_fingerprint", "fingerprint"):
        if type(snapshot[field]) is not str or _DIGEST.fullmatch(snapshot[field]) is None:
            raise TitleProposalValueError("proposal-set snapshot fingerprint is invalid")
    if type(snapshot["id"]) is not str or not _IDENTIFIER.fullmatch(snapshot["id"]) or snapshot["schema"] != "synthetic-library-snapshot-v1":
        raise TitleProposalValueError("proposal-set snapshot identity is invalid")
    shape = snapshot["shape"]
    if not isinstance(shape, Mapping) or set(shape) != {"external_item", "metadata", "state"} or shape["external_item"] != "synthetic-item" or shape["state"] not in {"loaded", "missing", "unavailable"}:
        raise TitleProposalValueError("proposal-set snapshot shape is invalid")
    metadata = shape["metadata"]
    if not isinstance(metadata, Mapping) or set(metadata) != {"title"}:
        raise TitleProposalValueError("proposal-set snapshot metadata is invalid")
    if metadata["title"] is not None:
        _title(metadata["title"], "snapshot title")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _deep_thaw(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TitleProposalValueError("proposal-set JSON contains a duplicate field")
        result[key] = value
    return result


__all__ = [
    "ProposalExecutionMode", "SyntheticTitleProposal", "SyntheticTitleState",
    "TITLE_COMPARISON_VERSION", "TITLE_PROPOSAL_POLICY_VERSION", "TITLE_PROPOSAL_SCHEMA",
    "TitleProposalResult", "TitleProposalSet", "TitleProposalValueError",
    "normalized_title_key", "proposal_set_from_manifest",
]
