"""Report-only live title proposal values."""

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


LIVE_TITLE_PROPOSAL_SCHEMA = "live-title-proposal-set-v1"
TITLE_COMPARISON_VERSION = "unicode-title-comparison-v1"
LIVE_TITLE_PROPOSAL_POLICY_VERSION = "live-title-policy-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_HARDCOVER_PROVIDER_VERSIONS = frozenset(
    {"hardcover-title-poc-v1", "hardcover-title-poc-v2"}
)


class TitleProposalValueError(ValueError):
    """A proposal set or its persisted representation is invalid."""


class ProposalExecutionMode(StrEnum):
    PRODUCTION_REPORT_ONLY = "production_report_only"


class TitleProposalResult(StrEnum):
    NEEDS_REVIEW = "needs_review"
    NO_CHANGE = "no_change"
    SOURCE_NOT_READY = "source_not_ready"
    PROVIDER_NOT_READY = "provider_not_ready"
    EVIDENCE_DISAGREEMENT = "evidence_disagreement"


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
class LiveTitleProposalSet:
    set_id: str
    identity_digest: str
    lineage: str
    lineage_revision: int
    previous_set_id: str | None
    result: TitleProposalResult
    manifest: Mapping[str, object]
    proposals: tuple[SyntheticTitleProposal, ...]
    mode: ProposalExecutionMode = ProposalExecutionMode.PRODUCTION_REPORT_ONLY

    def __post_init__(self) -> None:
        if (
            not _IDENTIFIER.fullmatch(self.set_id)
            or not _DIGEST.fullmatch(self.identity_digest)
            or not _IDENTIFIER.fullmatch(self.lineage)
            or type(self.lineage_revision) is not int
            or not 1 <= self.lineage_revision <= 1_000_000
            or (
                self.previous_set_id is not None
                and not _IDENTIFIER.fullmatch(self.previous_set_id)
            )
            or type(self.result) is not TitleProposalResult
            or self.result
            not in {
                TitleProposalResult.NEEDS_REVIEW,
                TitleProposalResult.SOURCE_NOT_READY,
                TitleProposalResult.PROVIDER_NOT_READY,
                TitleProposalResult.EVIDENCE_DISAGREEMENT,
                TitleProposalResult.NO_CHANGE,
            }
            or self.mode is not ProposalExecutionMode.PRODUCTION_REPORT_ONLY
            or not isinstance(self.manifest, Mapping)
            or type(self.proposals) is not tuple
            or any(type(item) is not SyntheticTitleProposal for item in self.proposals)
        ):
            raise TitleProposalValueError("live proposal set is invalid")
        try:
            object.__setattr__(self, "manifest", _deep_freeze(self.manifest))
        except (TypeError, ValueError, RecursionError):
            raise TitleProposalValueError("live proposal manifest is invalid") from None
        required = {
            "comparison_version",
            "file_index",
            "inventory_fingerprint",
            "lineage",
            "lineage_revision",
            "local_evidence_id",
            "mode",
            "mount_configuration_digest",
            "mount_configuration_set_digest",
            "policy_version",
            "previous_set_id",
            "proposal_schema",
            "provider_evidence_id",
            "provider_status",
            "snapshot_reference",
            "source_fingerprint",
            "target_reference",
        }
        if set(self.manifest) != required:
            raise TitleProposalValueError("live proposal manifest has unknown or missing fields")
        if (
            self.manifest["mode"] != self.mode.value
            or self.manifest["lineage"] != self.lineage
            or self.manifest["lineage_revision"] != self.lineage_revision
            or self.manifest["previous_set_id"] != self.previous_set_id
            or self.manifest["proposal_schema"] != LIVE_TITLE_PROPOSAL_SCHEMA
            or self.manifest["comparison_version"] != TITLE_COMPARISON_VERSION
            or self.manifest["policy_version"] != LIVE_TITLE_PROPOSAL_POLICY_VERSION
            or type(self.manifest["file_index"]) is not int
            or not 0 <= self.manifest["file_index"] <= 10_000
            or self.manifest["provider_status"] not in {"ready", "not_ready"}
        ):
            raise TitleProposalValueError("live proposal manifest disagrees with its envelope")
        for field in (
            "inventory_fingerprint",
            "mount_configuration_digest",
            "mount_configuration_set_digest",
            "source_fingerprint",
        ):
            value = self.manifest[field]
            if value is not None and (type(value) is not str or _DIGEST.fullmatch(value) is None):
                raise TitleProposalValueError("live proposal evidence digest is invalid")
        for field, pattern in (
            ("target_reference", r"target_[0-9a-f]{32}"),
            ("snapshot_reference", r"snapshot_[0-9a-f]{32}"),
            ("local_evidence_id", r"evidence_[0-9a-f]{32}"),
            ("provider_evidence_id", r"[0-9a-f]{64}"),
        ):
            value = self.manifest[field]
            if value is not None and (type(value) is not str or re.fullmatch(pattern, value) is None):
                raise TitleProposalValueError("live proposal evidence reference is invalid")
        if len(self.proposals) != (1 if self.result is TitleProposalResult.NEEDS_REVIEW else 0):
            raise TitleProposalValueError("live proposal count does not match result")
        if self.result is TitleProposalResult.NEEDS_REVIEW and (
            self.manifest["provider_status"] != "ready"
            or any(
                self.manifest[field] is None
                for field in (
                    "inventory_fingerprint",
                    "local_evidence_id",
                    "mount_configuration_digest",
                    "mount_configuration_set_digest",
                    "provider_evidence_id",
                    "snapshot_reference",
                    "source_fingerprint",
                    "target_reference",
                )
            )
        ):
            raise TitleProposalValueError(
                "needs-review result requires ready live evidence"
            )
        expected = _identity_digest(self.manifest, self.result, self.proposals)
        if expected != self.identity_digest or self.set_id != f"ps-{expected[:60]}":
            raise TitleProposalValueError("live proposal identity does not match its semantics")

    @property
    def production_applicable(self) -> bool:
        return self.result is TitleProposalResult.NEEDS_REVIEW

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
            "schema": LIVE_TITLE_PROPOSAL_SCHEMA,
            "set_id": self.set_id,
        }

    def to_json(self) -> str:
        serialized = _canonical_json(self.to_data())
        if len(serialized.encode("utf-8")) > 131_072:
            raise TitleProposalValueError("live proposal report is too large")
        return serialized

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if type(serialized) is not str or len(serialized.encode("utf-8")) > 131_072:
            raise TitleProposalValueError("live proposal JSON is invalid")
        try:
            value = json.loads(serialized, object_pairs_hook=_unique_object)
            if type(value) is not dict or set(value) != {
                "identity_digest", "lineage", "lineage_revision", "manifest", "mode",
                "previous_set_id", "production_applicable", "proposals", "result",
                "schema", "set_id",
            } or value["schema"] != LIVE_TITLE_PROPOSAL_SCHEMA:
                raise TitleProposalValueError("live proposal JSON has unknown or missing fields")
            result = TitleProposalResult(value["result"])
            if value["production_applicable"] is not (result is TitleProposalResult.NEEDS_REVIEW):
                raise TitleProposalValueError("live proposal JSON has invalid authority")
            raw_proposals = value["proposals"]
            if type(raw_proposals) is not list:
                raise TitleProposalValueError("live proposal JSON is invalid")
            proposals = tuple(
                SyntheticTitleProposal(
                    id=item["id"],
                    current_value=item["current_value"],
                    proposed_value=item["proposed_value"],
                    kind=item["kind"],
                    disposition=item["disposition"],
                )
                for item in raw_proposals
                if type(item) is dict
                and set(item) == {
                    "current_value", "disposition", "field", "id", "kind", "proposed_value"
                }
                and item["field"] == "title"
            )
            if len(proposals) != len(raw_proposals):
                raise TitleProposalValueError("live proposal JSON is invalid")
            proposal_set = cls(
                set_id=value["set_id"],
                identity_digest=value["identity_digest"],
                lineage=value["lineage"],
                lineage_revision=value["lineage_revision"],
                previous_set_id=value["previous_set_id"],
                result=result,
                manifest=value["manifest"],
                proposals=proposals,
                mode=ProposalExecutionMode(value["mode"]),
            )
        except TitleProposalValueError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
            raise TitleProposalValueError("live proposal JSON is invalid") from None
        if proposal_set.to_json() != serialized:
            raise TitleProposalValueError("live proposal JSON is not canonical")
        return proposal_set


def live_proposal_set_from_manifest(
    *,
    manifest: dict[str, object],
    result: TitleProposalResult,
    current_value: str | None = None,
    proposed_value: str | None = None,
) -> LiveTitleProposalSet:
    proposals: tuple[SyntheticTitleProposal, ...] = ()
    if result is TitleProposalResult.NEEDS_REVIEW:
        if proposed_value is None:
            raise TitleProposalValueError("needs-review result requires a proposed title")
        kind = "fill_empty" if current_value is None else "replace_populated"
        proposal_id = _deterministic_proposal_id(
            manifest,
            result,
            current_value=current_value,
            proposed_value=proposed_value,
            kind=kind,
            disposition="needs_review",
        )
        proposals = (SyntheticTitleProposal(
            proposal_id, current_value, proposed_value, kind
        ),)
    digest = _identity_digest(manifest, result, proposals)
    return LiveTitleProposalSet(
        set_id=f"ps-{digest[:60]}",
        identity_digest=digest,
        lineage=manifest["lineage"],
        lineage_revision=manifest["lineage_revision"],
        previous_set_id=manifest["previous_set_id"],
        result=result,
        manifest=manifest,
        proposals=proposals,
    )


def normalized_title_key(value: str) -> str:
    """Return the versioned NFC/casefold/NFC/Unicode-space comparison key."""

    _title(value, "title")
    folded = unicodedata.normalize("NFC", value).casefold()
    normalized = unicodedata.normalize("NFC", folded)
    return " ".join(normalized.split())


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
        or any(unicodedata.category(character) == "Cs" for character in value)
    ):
        raise TitleProposalValueError(f"{label} is invalid")
    return value



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
    "LIVE_TITLE_PROPOSAL_POLICY_VERSION", "LIVE_TITLE_PROPOSAL_SCHEMA",
    "LiveTitleProposalSet", "ProposalExecutionMode", "SyntheticTitleProposal",
    "TITLE_COMPARISON_VERSION", "TitleProposalResult", "TitleProposalValueError",
    "live_proposal_set_from_manifest", "normalized_title_key",
]
