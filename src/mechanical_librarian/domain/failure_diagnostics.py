"""Closed, aggregate-safe failure values shared by application projections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Self

MAX_DIAGNOSTIC_COUNT = 2_147_483_647


class FailureCategory(str, Enum):
    SETUP_CONFIGURATION = "setup_configuration"
    AUTHENTICATION = "authentication"
    LOOKUP_CONSTRUCTION = "lookup_construction"
    PROVIDER_DECLARED_ERROR = "provider_declared_error"
    HTTP_NON_SUCCESS = "http_non_success"
    QUOTA_RATE_LIMIT = "quota_rate_limit"
    BURST_THROTTLE = "burst_throttle"
    DAILY_QUOTA_EXHAUSTED = "daily_quota_exhausted"
    EMPTY_NO_MATCH = "empty_no_match"
    MALFORMED_RESPONSE = "malformed_response"
    OVERSIZED_RESPONSE = "oversized_response"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    SCHEMA_CONSTRUCTION = "schema_construction"
    LIVE_SCHEMA_REJECTION = "live_schema_rejection"
    SPAWN_RUNTIME = "spawn_runtime"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    MALFORMED_OUTPUT = "malformed_output"
    CITATION_MEMBERSHIP = "citation_membership"
    IDENTITY = "identity"
    WORK_IDENTITY_REFUSED = "work_identity_refused"
    CURRENTNESS = "currentness"
    PERSISTENCE = "persistence"
    UNKNOWN = "unknown"


class FailureComponent(str, Enum):
    CONFIGURATION = "configuration"
    TARGET = "target"
    INSPECTOR = "inspector"
    PROVIDER = "provider"
    REVIEWER = "reviewer"
    REPORT = "report"
    PERSISTENCE = "persistence"
    CLI = "cli"


class FailureStage(str, Enum):
    SETUP = "setup"
    AUTHENTICATION = "authentication"
    LOOKUP = "lookup"
    REQUEST = "request"
    RESPONSE = "response"
    SCHEMA = "schema"
    SPAWN = "spawn"
    EXECUTION = "execution"
    VALIDATION = "validation"
    IDENTITY = "identity"
    CURRENTNESS = "currentness"
    PERSISTENCE = "persistence"


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    """The complete value permitted across a public or durable failure boundary."""

    category: FailureCategory
    component: FailureComponent
    stage: FailureStage
    retryable: bool | None = None
    current: bool | None = None
    count: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.category) is not FailureCategory
            or type(self.component) is not FailureComponent
            or type(self.stage) is not FailureStage
            or (self.retryable is not None and type(self.retryable) is not bool)
            or (self.current is not None and type(self.current) is not bool)
            or type(self.count) is not int
            or not 0 <= self.count <= MAX_DIAGNOSTIC_COUNT
        ):
            raise ValueError("failure diagnostic is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "component": self.component.value,
            "count": self.count,
            "current": self.current,
            "retryable": self.retryable,
            "stage": self.stage.value,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_data(), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if (
            type(serialized) is not str
            or not serialized
            or len(serialized.encode("utf-8")) > 512
        ):
            raise ValueError("failure diagnostic document is invalid")
        try:
            source = json.loads(
                serialized,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            if type(source) is not dict or set(source) != {
                "category",
                "component",
                "count",
                "current",
                "retryable",
                "stage",
            }:
                raise ValueError
            value = cls(
                FailureCategory(source["category"]),
                FailureComponent(source["component"]),
                FailureStage(source["stage"]),
                source["retryable"],
                source["current"],
                source["count"],
            )
        except (
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            RecursionError,
            json.JSONDecodeError,
        ) as error:
            raise ValueError("failure diagnostic document is invalid") from error
        if value.to_json() != serialized:
            raise ValueError("failure diagnostic document is not canonical")
        return value


def diagnostic_aggregate_key(
    diagnostic: FailureDiagnostic,
) -> tuple[str, str, str, int, int]:
    """Return the canonical identity and ordering key for one aggregate."""

    return (
        diagnostic.category.value,
        diagnostic.component.value,
        diagnostic.stage.value,
        -1 if diagnostic.retryable is None else int(diagnostic.retryable),
        -1 if diagnostic.current is None else int(diagnostic.current),
    )


def diagnostics_are_canonical(
    diagnostics: tuple[FailureDiagnostic, ...],
) -> bool:
    """Require one sorted aggregate per category/component/stage state."""

    if type(diagnostics) is not tuple or any(
        type(value) is not FailureDiagnostic for value in diagnostics
    ):
        return False
    keys = tuple(diagnostic_aggregate_key(value) for value in diagnostics)
    return keys == tuple(sorted(set(keys)))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("failure diagnostic object contains a duplicate field")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ValueError("failure diagnostic number is invalid")


__all__ = [
    "FailureCategory",
    "FailureComponent",
    "FailureDiagnostic",
    "FailureStage",
    "MAX_DIAGNOSTIC_COUNT",
    "diagnostic_aggregate_key",
    "diagnostics_are_canonical",
]
