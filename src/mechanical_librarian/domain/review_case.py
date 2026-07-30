"""Immutable, undecided production review cases derived from ProposalSets."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Self

from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, ProposalSet

REVIEW_CASE_SCHEMA = "review-case-v1"
REVIEW_ADMISSION_SCHEMA = "review-admission-v1"
_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
_PROPOSAL_SET_ID = re.compile(r"^proposal_set_[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_ADMISSION_STATUS = frozenset(
    {"admitted", "replayed", "superseded", "no_case", "retired", "stale"}
)
_ADMISSION_FAILURE = frozenset(
    {"invalid", "incompatible", "currentness", "persistence"}
)
_TRANSITION_KIND = frozenset({"admitted", "superseded", "no_case", "retired"})
MAX_REVIEW_CASE_BYTES = 2_097_152


class ReviewCaseValueError(ValueError):
    """A review case is outside the closed production-admission contract."""


class ReviewAdmissionStaleError(RuntimeError):
    """The exact ProposalSet lost currentness before atomic admission."""


class ReviewAdmissionIncompatibleError(RuntimeError):
    """The ProposalSet is not the exact evaluator output for its report."""


class _FrozenJsonDict(dict[str, object]):
    """A JSON-object value that preserves dict equality and serialization."""

    def _reject_mutation(self, *args: object, **kwargs: object) -> None:
        raise TypeError("review case content is immutable")

    __delitem__ = _reject_mutation
    __ior__ = _reject_mutation
    __setitem__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation
    setdefault = _reject_mutation
    update = _reject_mutation


class _FrozenJsonList(list[object]):
    """A JSON-array value that preserves list equality and serialization."""

    def _reject_mutation(self, *args: object, **kwargs: object) -> None:
        raise TypeError("review case content is immutable")

    __delitem__ = _reject_mutation
    __iadd__ = _reject_mutation
    __imul__ = _reject_mutation
    __setitem__ = _reject_mutation
    append = _reject_mutation
    clear = _reject_mutation
    extend = _reject_mutation
    insert = _reject_mutation
    pop = _reject_mutation
    remove = _reject_mutation
    reverse = _reject_mutation
    sort = _reject_mutation


def _freeze_json(value: object) -> object:
    if type(value) is dict:
        return _FrozenJsonDict(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if type(value) is list:
        return _FrozenJsonList([_freeze_json(item) for item in value])
    return value


def _freeze_json_object(value: dict[str, object]) -> dict[str, object]:
    return _FrozenJsonDict(
        {key: _freeze_json(item) for key, item in value.items()}
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _thaw_json_object(value: dict[str, object]) -> dict[str, object]:
    return {key: _thaw_json(item) for key, item in value.items()}


@dataclass(frozen=True, slots=True)
class ReviewAdmissionResult:
    """Closed canonical outcome of one review-admission attempt."""
    status: str
    review_case_id: str | None
    schema_version: str = REVIEW_ADMISSION_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.status not in _ADMISSION_STATUS
            or self.schema_version != REVIEW_ADMISSION_SCHEMA
        ):
            raise ReviewCaseValueError("review admission result is invalid")
        if (
            self.review_case_id is not None
            and _CASE_ID.fullmatch(self.review_case_id) is None
        ):
            raise ReviewCaseValueError("review admission result is invalid")
        if (self.status in {"admitted", "replayed", "superseded"}) != (
            self.review_case_id is not None
        ):
            raise ReviewCaseValueError("review admission result is invalid")

    def to_json(self) -> str:
        return _canonical(
            {
                "review_case_id": self.review_case_id,
                "schema_version": self.schema_version,
                "status": self.status,
            }
        )

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            raw = json.loads(serialized, object_pairs_hook=_unique_object)
            if type(raw) is not dict or set(raw) != {
                "review_case_id",
                "schema_version",
                "status",
            }:
                raise ValueError
            value = cls(raw["status"], raw["review_case_id"], raw["schema_version"])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReviewCaseValueError("review admission result is invalid") from error
        if value.to_json() != serialized:
            raise ReviewCaseValueError("review admission result is not canonical")
        return value


@dataclass(frozen=True, slots=True)
class ReviewAdmissionFailure:
    """Closed redacted failure result for untrusted admission failures."""
    code: str
    schema_version: str = "review-admission-failure-v1"

    def __post_init__(self) -> None:
        if (
            self.code not in _ADMISSION_FAILURE
            or self.schema_version != "review-admission-failure-v1"
        ):
            raise ReviewCaseValueError("review admission failure is invalid")

    def to_json(self) -> str:
        return _canonical({"code": self.code, "schema_version": self.schema_version})


@dataclass(frozen=True, slots=True)
class ReviewTransition:
    """Immutable attributed lineage checkpoint transition."""
    connection: str
    library: str
    item: str
    checkpoint: int
    proposal_set_id: str
    report_id: str
    kind: str
    from_review_case_id: str | None
    to_review_case_id: str | None
    reason: str
    actor: str = "review-admission-v1"

    def __post_init__(self) -> None:
        if (
            type(self.checkpoint) is not int
            or self.checkpoint < 1
            or self.actor != "review-admission-v1"
            or self.kind not in _TRANSITION_KIND
            or self.reason not in {"actionable", "nonactionable"}
            or type(self.proposal_set_id) is not str
            or _PROPOSAL_SET_ID.fullmatch(self.proposal_set_id) is None
            or type(self.report_id) is not str
            or _REPORT_ID.fullmatch(self.report_id) is None
            or any(
                type(value) is not str or not value
                for value in (self.connection, self.library, self.item)
            )
            or any(
                value is not None
                and (
                    type(value) is not str
                    or _CASE_ID.fullmatch(value) is None
                )
                for value in (
                    self.from_review_case_id,
                    self.to_review_case_id,
                )
            )
        ):
            raise ReviewCaseValueError("review transition is invalid")
        if self.kind in {"admitted", "superseded"} and (
            self.to_review_case_id is None or self.reason != "actionable"
        ):
            raise ReviewCaseValueError("review transition is invalid")
        if self.kind in {"no_case", "retired"} and (
            self.to_review_case_id is not None or self.reason != "nonactionable"
        ):
            raise ReviewCaseValueError("review transition is invalid")
        if (self.kind in {"admitted", "no_case"}) != (
            self.from_review_case_id is None
        ):
            raise ReviewCaseValueError("review transition is invalid")

    @property
    def transition_id(self) -> str:
        return "review_transition_" + hashlib.sha256(
            _canonical(self.to_data()).encode("utf-8")
        ).hexdigest()

    def to_data(self) -> dict[str, object]:
        return {"actor": self.actor, "checkpoint": self.checkpoint, "connection": self.connection, "from_review_case_id": self.from_review_case_id, "item": self.item, "kind": self.kind, "library": self.library, "proposal_set_id": self.proposal_set_id, "reason": self.reason, "report_id": self.report_id, "to_review_case_id": self.to_review_case_id}

    def to_json(self) -> str:
        return _canonical(self.to_data())


@dataclass(frozen=True, slots=True)
class ReviewCase:
    review_case_id: str
    proposal_set_id: str
    report_id: str
    _target: dict[str, object] = field(repr=False)
    target_fingerprint: str
    file_identity: str
    _results: tuple[dict[str, object], ...] = field(repr=False)
    proposal_document: str
    disposition: str = "undecided"
    schema_version: str = REVIEW_CASE_SCHEMA

    def __post_init__(self) -> None:
        if (
            _CASE_ID.fullmatch(self.review_case_id) is None
            or self.schema_version != REVIEW_CASE_SCHEMA
            or self.disposition != "undecided"
            or len(self._results) != len(TargetDestination)
            or not isinstance(self._target, dict)
        ):
            raise ReviewCaseValueError("review case is invalid")
        try:
            proposal = ProposalSet.from_json(self.proposal_document)
        except Exception as error:
            raise ReviewCaseValueError("review case proposal is invalid") from error
        if (
            proposal.proposal_set_id != self.proposal_set_id
            or proposal.report_id != self.report_id
            or proposal.target_fingerprint != self.target_fingerprint
            or tuple(value.to_data() for value in proposal.results) != self._results
        ):
            raise ReviewCaseValueError("review case proposal is invalid")
        if self.review_case_id != self._expected_id():
            raise ReviewCaseValueError("review case identity is inconsistent")
        object.__setattr__(self, "_target", _freeze_json_object(self._target))
        object.__setattr__(
            self,
            "_results",
            tuple(_freeze_json_object(value) for value in self._results),
        )

    @classmethod
    def create(cls, proposal_set: ProposalSet, report: MetadataReport) -> Self:
        if type(proposal_set) is not ProposalSet or type(report) is not MetadataReport:
            raise ReviewCaseValueError("review case input is invalid")
        if (
            proposal_set.report_id != report.report_id
            or proposal_set.target_fingerprint != report.target.fingerprint
        ):
            raise ReviewCaseValueError("review case binding is invalid")
        results = tuple(value.to_data() for value in proposal_set.results)
        data = cls._data(proposal_set.proposal_set_id, report.report_id, report.target.to_data(), report.target.fingerprint, report.file.identity, results, proposal_set.to_json())
        identity = "review_case_" + hashlib.sha256(
            _canonical(data).encode("utf-8")
        ).hexdigest()
        return cls(identity, proposal_set.proposal_set_id, report.report_id, report.target.to_data(), report.target.fingerprint, report.file.identity, results, proposal_set.to_json())

    @staticmethod
    def _data(proposal_set_id: str, report_id: str, target: dict[str, object], target_fingerprint: str, file_identity: str, results: tuple[dict[str, object], ...], proposal_document: str) -> dict[str, object]:
        return {"disposition": "undecided", "proposal_set_id": proposal_set_id, "report_id": report_id, "target": _thaw_json_object(target),
                "target_fingerprint": target_fingerprint, "file_identity": file_identity,
                "results": [_thaw_json_object(value) for value in results], "proposal_document": proposal_document, "schema_version": REVIEW_CASE_SCHEMA}

    def _expected_id(self) -> str:
        return "review_case_" + hashlib.sha256(_canonical(self.to_data()).encode("utf-8")).hexdigest()

    @property
    def target(self) -> dict[str, object]:
        """Return a detached projection of the canonical target."""
        return _thaw_json_object(self._target)

    @property
    def results(self) -> tuple[dict[str, object], ...]:
        """Return detached target-ordered evaluator results."""
        return tuple(_thaw_json_object(value) for value in self._results)

    @property
    def actionable(self) -> tuple[dict[str, object], ...]:
        return tuple(
            _thaw_json_object(value)
            for value in self._results
            if value.get("outcome")
            in {
                ProposalOutcome.ACCEPTABLE_CHOICE.value,
                ProposalOutcome.PROPOSE_CHANGE.value,
                ProposalOutcome.UNRESOLVED_CHOICE.value,
            }
        )

    @property
    def proposals(self) -> tuple[dict[str, object], ...]:
        """Target-ordered immutable proposal projections with stable identities."""
        return tuple(
            {
                "destination": result["destination"],
                "proposal_id": "review_proposal_" + hashlib.sha256(
                    _canonical(
                        {
                            "destination": result["destination"],
                            "proposal_set_id": self.proposal_set_id,
                            "result": result,
                        }
                    ).encode("utf-8")
                ).hexdigest(),
                "result": _thaw_json_object(result),
            }
            for result in self._results
        )

    def to_data(self) -> dict[str, object]:
        return self._data(self.proposal_set_id, self.report_id, self._target, self.target_fingerprint, self.file_identity, self._results, self.proposal_document)

    def to_json(self) -> str:
        return _canonical(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            if type(serialized) is not str or not serialized or len(serialized.encode("utf-8")) > MAX_REVIEW_CASE_BYTES:
                raise ValueError
            raw = json.loads(serialized, object_pairs_hook=_unique_object)
            if type(raw) is not dict or set(raw) != {"disposition","proposal_set_id","report_id","target","target_fingerprint","file_identity","results","proposal_document","schema_version"}:
                raise ValueError
            value = cls.create_from_data(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReviewCaseValueError("review case is invalid") from error
        if value.to_json() != serialized:
            raise ReviewCaseValueError("review case is not canonical")
        return value

    @classmethod
    def create_from_data(cls, raw: dict[str, object]) -> Self:
        if raw.get("schema_version") != REVIEW_CASE_SCHEMA or raw.get("disposition") != "undecided" or type(raw.get("results")) is not list or type(raw.get("target")) is not dict:
            raise ReviewCaseValueError("review case is invalid")
        try:
            target = TargetFacts.from_data(raw["target"])
        except Exception as error:
            raise ReviewCaseValueError("review case target is invalid") from error
        results = tuple(raw["results"])
        if any(type(value) is not dict for value in results):
            raise ReviewCaseValueError("review case is invalid")
        if target.to_data() != raw["target"] or target.fingerprint != raw["target_fingerprint"]:
            raise ReviewCaseValueError("review case target is invalid")
        data = cls._data(raw["proposal_set_id"], raw["report_id"], raw["target"], raw["target_fingerprint"], raw["file_identity"], results, raw["proposal_document"])  # type: ignore[arg-type]
        identity = "review_case_" + hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
        return cls(identity, raw["proposal_set_id"], raw["report_id"], raw["target"], raw["target_fingerprint"], raw["file_identity"], results, raw["proposal_document"])  # type: ignore[arg-type]


def is_actionable(proposal_set: ProposalSet) -> bool:
    return any(
        value.outcome
        in {
            ProposalOutcome.ACCEPTABLE_CHOICE,
            ProposalOutcome.PROPOSE_CHANGE,
            ProposalOutcome.UNRESOLVED_CHOICE,
        }
        for value in proposal_set.results
    )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewCaseValueError("review case has duplicate keys")
        result[key] = value
    return result


__all__ = ["MAX_REVIEW_CASE_BYTES", "REVIEW_ADMISSION_SCHEMA", "REVIEW_CASE_SCHEMA", "ReviewAdmissionFailure", "ReviewAdmissionIncompatibleError", "ReviewAdmissionResult", "ReviewAdmissionStaleError", "ReviewCase", "ReviewCaseValueError", "is_actionable"]
