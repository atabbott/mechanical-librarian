"""Immutable, undecided production review cases derived from ProposalSets."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Self

from mechanical_librarian.domain.metadata_report import MetadataReport, TargetDestination, TargetFacts
from mechanical_librarian.domain.proposal_set import ProposalOutcome, ProposalSet

REVIEW_CASE_SCHEMA = "review-case-v1"
REVIEW_ADMISSION_SCHEMA = "review-admission-v1"
_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
_ADMISSION_STATUS = frozenset({"admitted", "replayed", "superseded", "no_case", "retired", "stale"})
MAX_REVIEW_CASE_BYTES = 2_097_152


class ReviewCaseValueError(ValueError):
    """A review case is outside the closed production-admission contract."""


@dataclass(frozen=True, slots=True)
class ReviewAdmissionResult:
    """Closed canonical outcome of one review-admission attempt."""
    status: str
    review_case_id: str | None
    schema_version: str = REVIEW_ADMISSION_SCHEMA

    def __post_init__(self) -> None:
        if self.status not in _ADMISSION_STATUS or self.schema_version != REVIEW_ADMISSION_SCHEMA:
            raise ReviewCaseValueError("review admission result is invalid")
        if self.review_case_id is not None and _CASE_ID.fullmatch(self.review_case_id) is None:
            raise ReviewCaseValueError("review admission result is invalid")
        if (self.status in {"admitted", "replayed", "superseded"}) != (self.review_case_id is not None):
            raise ReviewCaseValueError("review admission result is invalid")

    def to_json(self) -> str:
        return _canonical({"review_case_id": self.review_case_id, "schema_version": self.schema_version, "status": self.status})


@dataclass(frozen=True, slots=True)
class ReviewCase:
    review_case_id: str
    proposal_set_id: str
    report_id: str
    target: dict[str, object]
    target_fingerprint: str
    file_identity: str
    results: tuple[dict[str, object], ...]
    disposition: str = "undecided"
    schema_version: str = REVIEW_CASE_SCHEMA

    def __post_init__(self) -> None:
        if (not _CASE_ID.fullmatch(self.review_case_id) or self.schema_version != REVIEW_CASE_SCHEMA or self.disposition != "undecided"
                or len(self.results) != len(TargetDestination) or not isinstance(self.target, dict)):
            raise ReviewCaseValueError("review case is invalid")
        if self.review_case_id != self._expected_id():
            raise ReviewCaseValueError("review case identity is inconsistent")

    @classmethod
    def create(cls, proposal_set: ProposalSet, report: MetadataReport) -> Self:
        if type(proposal_set) is not ProposalSet or type(report) is not MetadataReport:
            raise ReviewCaseValueError("review case input is invalid")
        if proposal_set.report_id != report.report_id or proposal_set.target_fingerprint != report.target.fingerprint:
            raise ReviewCaseValueError("review case binding is invalid")
        results = tuple(value.to_data() for value in proposal_set.results)
        data = cls._data(proposal_set.proposal_set_id, report.report_id, report.target.to_data(), report.target.fingerprint, report.file.identity, results)
        identity = "review_case_" + hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
        return cls(identity, proposal_set.proposal_set_id, report.report_id, report.target.to_data(), report.target.fingerprint, report.file.identity, results)

    @staticmethod
    def _data(proposal_set_id: str, report_id: str, target: dict[str, object], target_fingerprint: str, file_identity: str, results: tuple[dict[str, object], ...]) -> dict[str, object]:
        return {"disposition": "undecided", "proposal_set_id": proposal_set_id, "report_id": report_id, "target": target,
                "target_fingerprint": target_fingerprint, "file_identity": file_identity,
                "results": list(results), "schema_version": REVIEW_CASE_SCHEMA}

    def _expected_id(self) -> str:
        return "review_case_" + hashlib.sha256(_canonical(self.to_data()).encode("utf-8")).hexdigest()

    @property
    def actionable(self) -> tuple[dict[str, object], ...]:
        return tuple(value for value in self.results if value.get("outcome") in {ProposalOutcome.PROPOSE_CHANGE.value, ProposalOutcome.UNRESOLVED_CHOICE.value})

    def to_data(self) -> dict[str, object]:
        return self._data(self.proposal_set_id, self.report_id, self.target, self.target_fingerprint, self.file_identity, self.results)

    def to_json(self) -> str:
        return _canonical(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            if type(serialized) is not str or not serialized or len(serialized.encode("utf-8")) > MAX_REVIEW_CASE_BYTES:
                raise ValueError
            raw = json.loads(serialized, object_pairs_hook=_unique_object)
            if type(raw) is not dict or set(raw) != {"disposition","proposal_set_id","report_id","target","target_fingerprint","file_identity","results","schema_version"}:
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
        data = cls._data(raw["proposal_set_id"], raw["report_id"], raw["target"], raw["target_fingerprint"], raw["file_identity"], results)  # type: ignore[arg-type]
        identity = "review_case_" + hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
        return cls(identity, raw["proposal_set_id"], raw["report_id"], raw["target"], raw["target_fingerprint"], raw["file_identity"], results)  # type: ignore[arg-type]


def is_actionable(proposal_set: ProposalSet) -> bool:
    return any(value.outcome in {ProposalOutcome.PROPOSE_CHANGE, ProposalOutcome.UNRESOLVED_CHOICE} for value in proposal_set.results)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewCaseValueError("review case has duplicate keys")
        result[key] = value
    return result


__all__ = ["MAX_REVIEW_CASE_BYTES", "REVIEW_ADMISSION_SCHEMA", "REVIEW_CASE_SCHEMA", "ReviewAdmissionResult", "ReviewCase", "ReviewCaseValueError", "is_actionable"]
