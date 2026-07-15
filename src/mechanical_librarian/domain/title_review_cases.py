"""Immutable, apply-ineligible review cases for the closed F10a preview."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Self

from mechanical_librarian.domain.title_proposals import (
    ProposalExecutionMode,
    TITLE_PROPOSAL_SCHEMA,
    TitleProposalResult,
    TitleProposalSet,
    proposal_set_from_manifest,
)


SYNTHETIC_TITLE_REVIEW_BINDING_VERSION = "synthetic-title-review-binding-v1"
SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA = "synthetic-title-review-case-v1"
SYNTHETIC_REVIEW_ADAPTER = "synthetic-target-service"
SYNTHETIC_REVIEW_CONNECTION = "synthetic-connection"
SYNTHETIC_REVIEW_LIBRARY = "synthetic-library"

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_CASE_BYTES = 196_608


class TitleReviewCaseValueError(ValueError):
    """A synthetic review-case value is malformed or authority-bearing."""


@dataclass(frozen=True)
class SyntheticReviewTarget:
    adapter_id: str
    connection_id: str
    library_id: str
    external_item: str

    def __post_init__(self) -> None:
        if (
            self.adapter_id != SYNTHETIC_REVIEW_ADAPTER
            or self.connection_id != SYNTHETIC_REVIEW_CONNECTION
            or self.library_id != SYNTHETIC_REVIEW_LIBRARY
            or not _IDENTIFIER.fullmatch(self.external_item)
        ):
            raise TitleReviewCaseValueError("synthetic review target is invalid")

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical_json(self.identity_data()).encode()).hexdigest()

    def identity_data(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "connection_id": self.connection_id,
            "external_item": self.external_item,
            "library_id": self.library_id,
        }

    def to_data(self) -> dict[str, str]:
        return {**self.identity_data(), "digest": self.digest}


@dataclass(frozen=True)
class SyntheticTitleReviewCase:
    case_id: str
    identity_digest: str
    target: SyntheticReviewTarget
    lineage: str
    source_set_id: str
    source_revision: int
    snapshot_id: str
    snapshot_schema: str
    snapshot_fingerprint: str
    proposal_id: str
    current_value: str | None
    proposed_value: str
    proposal_kind: str
    policy_version: str
    comparison_version: str
    manifest: Mapping[str, object]
    binding_version: str = SYNTHETIC_TITLE_REVIEW_BINDING_VERSION

    def __post_init__(self) -> None:
        if self.binding_version != SYNTHETIC_TITLE_REVIEW_BINDING_VERSION:
            raise TitleReviewCaseValueError("review binding version is unsupported")
        if not _IDENTIFIER.fullmatch(self.case_id) or not self.case_id.startswith("case-"):
            raise TitleReviewCaseValueError("review case ID is invalid")
        if not _DIGEST.fullmatch(self.identity_digest):
            raise TitleReviewCaseValueError("review case digest is invalid")
        for value in (
            self.lineage,
            self.source_set_id,
            self.snapshot_id,
            self.snapshot_schema,
            self.proposal_id,
            self.policy_version,
            self.comparison_version,
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise TitleReviewCaseValueError("review case identity is invalid")
        if type(self.source_revision) is not int or not 1 <= self.source_revision <= 1_000_000:
            raise TitleReviewCaseValueError("review case revision is invalid")
        if not _DIGEST.fullmatch(self.snapshot_fingerprint):
            raise TitleReviewCaseValueError("review snapshot fingerprint is invalid")
        if self.proposal_kind not in {"fill_empty", "replace_populated"}:
            raise TitleReviewCaseValueError("review proposal kind is invalid")
        _title(self.proposed_value, "proposed title")
        if self.current_value is not None:
            _title(self.current_value, "current title")
        if self.proposal_kind == "fill_empty" and self.current_value is not None:
            raise TitleReviewCaseValueError("fill-empty review case has a current value")
        if self.proposal_kind == "replace_populated" and self.current_value is None:
            raise TitleReviewCaseValueError("replacement review case lacks a current value")
        if not isinstance(self.manifest, Mapping):
            raise TitleReviewCaseValueError("review provenance is invalid")
        try:
            object.__setattr__(self, "manifest", _deep_freeze(self.manifest))
        except (TypeError, ValueError, RecursionError):
            raise TitleReviewCaseValueError("review provenance is invalid") from None
        try:
            source = proposal_set_from_manifest(
                manifest=_deep_thaw(self.manifest),  # type: ignore[arg-type]
                result=TitleProposalResult.NEEDS_REVIEW,
                current_value=self.current_value,
                proposed_value=self.proposed_value,
                previous_set_id=self.manifest["previous_set_id"],  # type: ignore[arg-type]
            )
            snapshot = _mapping(
                self.manifest["snapshot"],
                {"current_fingerprint", "fingerprint", "id", "schema", "shape"},
            )
            if (
                source.set_id != self.source_set_id
                or source.lineage != self.lineage
                or source.lineage_revision != self.source_revision
                or source.proposals[0].id != self.proposal_id
                or snapshot["id"] != self.snapshot_id
                or snapshot["schema"] != self.snapshot_schema
                or snapshot["fingerprint"] != self.snapshot_fingerprint
                or snapshot["current_fingerprint"] != self.snapshot_fingerprint
                or self.manifest["policy_version"] != self.policy_version
                or self.manifest["comparison_version"] != self.comparison_version
                or review_target_from_proposal_set(source) != self.target
            ):
                raise TitleReviewCaseValueError("review case disagrees with its source")
        except TitleReviewCaseValueError:
            raise
        except Exception:
            raise TitleReviewCaseValueError("review case source is invalid") from None
        expected = _case_digest(self.identity_projection())
        if self.identity_digest != expected or self.case_id != f"case-{expected[:59]}":
            raise TitleReviewCaseValueError("review case identity does not match its projection")

    @property
    def production_applicable(self) -> bool:
        return False

    @property
    def state(self) -> str:
        return "undecided"

    def identity_projection(self) -> dict[str, object]:
        return {
            "binding_version": self.binding_version,
            "case_schema": SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA,
            "mode": ProposalExecutionMode.SYNTHETIC_PREVIEW.value,
            "policy": {
                "comparison_version": self.comparison_version,
                "policy_version": self.policy_version,
                "proposal_schema": TITLE_PROPOSAL_SCHEMA,
            },
            "proposal": {
                "current_value": self.current_value,
                "disposition": "needs_review",
                "field": "title",
                "id": self.proposal_id,
                "kind": self.proposal_kind,
                "proposed_value": self.proposed_value,
            },
            "proposal_set": {
                "id": self.source_set_id,
                "lineage": self.lineage,
                "lineage_revision": self.source_revision,
                "result": TitleProposalResult.NEEDS_REVIEW.value,
            },
            "provenance": {"manifest": _deep_thaw(self.manifest)},
            "snapshot": {
                "fingerprint": self.snapshot_fingerprint,
                "id": self.snapshot_id,
                "schema": self.snapshot_schema,
            },
            "target": self.target.to_data(),
        }

    def to_data(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "identity_digest": self.identity_digest,
            "production_applicable": self.production_applicable,
            "schema": SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA,
            "state": self.state,
            **self.identity_projection(),
        }

    def to_json(self) -> str:
        rendered = _canonical_json(self.to_data())
        if len(rendered.encode("utf-8")) > _MAX_CASE_BYTES:
            raise TitleReviewCaseValueError("review case is too large")
        return rendered

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if type(serialized) is not str or len(serialized.encode("utf-8")) > _MAX_CASE_BYTES:
            raise TitleReviewCaseValueError("review case JSON is invalid")
        try:
            value = json.loads(serialized, object_pairs_hook=_unique_object)
            if type(value) is not dict or set(value) != {
                "binding_version", "case_id", "case_schema", "identity_digest", "mode",
                "policy", "production_applicable", "proposal", "proposal_set", "provenance",
                "schema", "snapshot", "state", "target",
            }:
                raise TitleReviewCaseValueError("review case JSON has unknown or missing fields")
            if (
                value["schema"] != SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA
                or value["case_schema"] != SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA
                or value["binding_version"] != SYNTHETIC_TITLE_REVIEW_BINDING_VERSION
                or value["mode"] != ProposalExecutionMode.SYNTHETIC_PREVIEW.value
                or value["production_applicable"] is not False
                or value["state"] != "undecided"
            ):
                raise TitleReviewCaseValueError("review case JSON has invalid authority")
            target = _mapping(value["target"], {"adapter_id", "connection_id", "digest", "external_item", "library_id"})
            review_target = SyntheticReviewTarget(
                target["adapter_id"], target["connection_id"], target["library_id"], target["external_item"]
            )
            if target["digest"] != review_target.digest:
                raise TitleReviewCaseValueError("review target digest is invalid")
            proposal_set = _mapping(value["proposal_set"], {"id", "lineage", "lineage_revision", "result"})
            proposal = _mapping(
                value["proposal"],
                {"current_value", "disposition", "field", "id", "kind", "proposed_value"},
            )
            policy = _mapping(value["policy"], {"comparison_version", "policy_version", "proposal_schema"})
            snapshot = _mapping(value["snapshot"], {"fingerprint", "id", "schema"})
            provenance = _mapping(value["provenance"], {"manifest"})
            if (
                proposal_set["result"] != TitleProposalResult.NEEDS_REVIEW.value
                or proposal["field"] != "title"
                or proposal["disposition"] != "needs_review"
                or policy["proposal_schema"] != TITLE_PROPOSAL_SCHEMA
            ):
                raise TitleReviewCaseValueError("review case projection is invalid")
            result = cls(
                case_id=value["case_id"],
                identity_digest=value["identity_digest"],
                target=review_target,
                lineage=proposal_set["lineage"],
                source_set_id=proposal_set["id"],
                source_revision=proposal_set["lineage_revision"],
                snapshot_id=snapshot["id"],
                snapshot_schema=snapshot["schema"],
                snapshot_fingerprint=snapshot["fingerprint"],
                proposal_id=proposal["id"],
                current_value=proposal["current_value"],
                proposed_value=proposal["proposed_value"],
                proposal_kind=proposal["kind"],
                policy_version=policy["policy_version"],
                comparison_version=policy["comparison_version"],
                manifest=provenance["manifest"],
            )
        except TitleReviewCaseValueError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
            raise TitleReviewCaseValueError("review case JSON is invalid") from None
        if result.to_json() != serialized:
            raise TitleReviewCaseValueError("review case JSON is not canonical")
        return result


def review_case_from_proposal_set(
    proposal_set: TitleProposalSet,
) -> SyntheticTitleReviewCase | None:
    """Project one qualifying current synthetic set into an immutable case."""

    if type(proposal_set) is not TitleProposalSet:
        raise TitleReviewCaseValueError("review source is invalid")
    if proposal_set.mode is not ProposalExecutionMode.SYNTHETIC_PREVIEW:
        raise TitleReviewCaseValueError("review source mode is invalid")
    manifest = proposal_set.manifest
    snapshot = _mapping(
        manifest["snapshot"],
        {"current_fingerprint", "fingerprint", "id", "schema", "shape"},
    )
    if snapshot["current_fingerprint"] != snapshot["fingerprint"]:
        raise TitleReviewCaseValueError("review source is stale")
    if proposal_set.result is not TitleProposalResult.NEEDS_REVIEW:
        if proposal_set.proposals:
            raise TitleReviewCaseValueError("nonqualifying review source carries a proposal")
        return None
    if len(proposal_set.proposals) != 1:
        raise TitleReviewCaseValueError("qualifying review source requires one proposal")
    proposal = proposal_set.proposals[0]
    if proposal.disposition != "needs_review":
        raise TitleReviewCaseValueError("review source disposition is invalid")
    target = review_target_from_proposal_set(proposal_set)
    identity = {
        "binding_version": SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
        "case_schema": SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA,
        "mode": ProposalExecutionMode.SYNTHETIC_PREVIEW.value,
        "policy": {
            "comparison_version": manifest["comparison_version"],
            "policy_version": manifest["policy_version"],
            "proposal_schema": TITLE_PROPOSAL_SCHEMA,
        },
        "proposal": proposal.to_data(),
        "proposal_set": {
            "id": proposal_set.set_id,
            "lineage": proposal_set.lineage,
            "lineage_revision": proposal_set.lineage_revision,
            "result": proposal_set.result.value,
        },
        "provenance": {"manifest": _deep_thaw(manifest)},
        "snapshot": {
            "fingerprint": snapshot["fingerprint"],
            "id": snapshot["id"],
            "schema": snapshot["schema"],
        },
        "target": target.to_data(),
    }
    digest = _case_digest(identity)
    return SyntheticTitleReviewCase(
        case_id=f"case-{digest[:59]}",
        identity_digest=digest,
        target=target,
        lineage=proposal_set.lineage,
        source_set_id=proposal_set.set_id,
        source_revision=proposal_set.lineage_revision,
        snapshot_id=snapshot["id"],
        snapshot_schema=snapshot["schema"],
        snapshot_fingerprint=snapshot["fingerprint"],
        proposal_id=proposal.id,
        current_value=proposal.current_value,
        proposed_value=proposal.proposed_value,
        proposal_kind=proposal.kind,
        policy_version=manifest["policy_version"],
        comparison_version=manifest["comparison_version"],
        manifest=manifest,
    )


def review_target_from_proposal_set(proposal_set: TitleProposalSet) -> SyntheticReviewTarget:
    """Derive the closed synthetic target only from a validated F09 manifest."""

    if type(proposal_set) is not TitleProposalSet:
        raise TitleReviewCaseValueError("review source is invalid")
    snapshot = _mapping(
        proposal_set.manifest["snapshot"],
        {"current_fingerprint", "fingerprint", "id", "schema", "shape"},
    )
    shape = _mapping(snapshot["shape"], {"external_item", "metadata", "state"})
    return SyntheticReviewTarget(
        SYNTHETIC_REVIEW_ADAPTER,
        SYNTHETIC_REVIEW_CONNECTION,
        SYNTHETIC_REVIEW_LIBRARY,
        shape["external_item"],
    )


def _mapping(value: object, fields: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise TitleReviewCaseValueError("review case structure is invalid")
    return value


def _case_digest(projection: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(projection).encode("utf-8")).hexdigest()


def _title(value: object, label: str) -> None:
    if type(value) is not str or not value or len(value) > 512 or "\x00" in value:
        raise TitleReviewCaseValueError(f"{label} is invalid")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError):
        raise TitleReviewCaseValueError("review case value is not serializable") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise TitleReviewCaseValueError("review case JSON has duplicate fields")
        value[key] = item
    return value


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TitleReviewCaseValueError("review provenance keys are invalid")
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if type(value) in {list, tuple}:
        return tuple(_deep_freeze(item) for item in value)
    if value is None or type(value) in {str, int, bool}:
        return value
    raise TitleReviewCaseValueError("review provenance values are invalid")


def _deep_thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_deep_thaw(item) for item in value]
    return value


__all__ = [
    "SYNTHETIC_REVIEW_ADAPTER",
    "SYNTHETIC_REVIEW_CONNECTION",
    "SYNTHETIC_REVIEW_LIBRARY",
    "SYNTHETIC_TITLE_REVIEW_BINDING_VERSION",
    "SYNTHETIC_TITLE_REVIEW_CASE_SCHEMA",
    "SyntheticReviewTarget",
    "SyntheticTitleReviewCase",
    "TitleReviewCaseValueError",
    "review_case_from_proposal_set",
    "review_target_from_proposal_set",
]
