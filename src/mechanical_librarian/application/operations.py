"""Bounded operational evidence for the single-process product runtime.

Operational evidence is deliberately advisory.  It contains only codes,
scopes, counts and durations; durable workflow and audit records remain the
authority for every field effect.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol, Sequence, cast

STAGES = (
    "extraction",
    "provider_search",
    "policy_evaluation",
    "human_wait",
    "apply",
    "verification",
    "reconciliation",
)
WAIT_CLASSES = ("none", "owner", "provider", "target", "recovery")
KINDS = (
    "healthy_activity",
    "owner_action_required",
    "automatic_recovery",
    "field_suspended",
    "category_suspended",
    "authentication_failure",
    "backup_failure",
    "service_incompatibility",
)
SCOPES = ("runtime", "connection", "library", "category", "work", "field")
MAX_TIMINGS = 256
MAX_INCIDENTS = 64
MAX_SUPPORT_BYTES = 65_536
_PREPARATION_REFERENCE = re.compile(r"^prep-[0-9a-f]{16}$")
_PREPARATION_STAGES = frozenset(
    ("extraction", "provider", "proposal", "admission", "projection")
)
_TERMINAL_PREPARATION_CODES = frozenset(
    (
        "provider",
        "configuration",
        "authentication",
        "stale",
        "transient",
        "projection",
        "failed",
        "currentness",
        "persistence",
        "unsupported_media",
        "unreadable_media",
        "parser_refused",
        "missing_required_input",
        "malformed_retained_data",
        "internal_failure",
        "unknown",
        # The signal query filters this one out, so no incident should carry
        # it.  It is admitted anyway: a code the vocabulary rejects fails the
        # whole status projection closed, and an audiobook that reached this
        # far is the one case where nothing is actually wrong.
        "local_evidence_only",
    )
)


class OperationsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage: str
    duration_ms: int
    wait_class: str
    recorded_at: int

    def __post_init__(self) -> None:
        if (
            self.stage not in STAGES
            or self.wait_class not in WAIT_CLASSES
            or type(self.duration_ms) is not int
            or not 0 <= self.duration_ms <= 86_400_000
            or type(self.recorded_at) is not int
            or self.recorded_at < 0
        ):
            raise OperationsError("invalid_timing")

    def to_data(self) -> dict[str, object]:
        return {
            "duration_ms": self.duration_ms,
            "recorded_at": self.recorded_at,
            "stage": self.stage,
            "wait_class": self.wait_class,
        }

    @classmethod
    def from_data(cls, value: object) -> "StageTiming":
        if type(value) is not dict or set(value) != {
            "duration_ms",
            "recorded_at",
            "stage",
            "wait_class",
        }:
            raise OperationsError("invalid_timing")
        return cls(
            value["stage"],
            value["duration_ms"],
            value["wait_class"],
            value["recorded_at"],
        )


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: str
    kind: str
    cause: str
    scope: str
    connection: str | None = None
    library: str | None = None
    category: str | None = None
    work_id: str | None = None
    field: str | None = None
    diagnostic_reference: str | None = None
    stage: str | None = None
    code: str | None = None
    attempt: int | None = None
    count: int = 1

    def __post_init__(self) -> None:
        identifiers = (
            self.connection,
            self.library,
            self.category,
            self.work_id,
            self.field,
            self.diagnostic_reference,
        )
        preparation = (self.diagnostic_reference, self.stage, self.code, self.attempt)
        if (
            self.kind not in KINDS
            or self.scope not in SCOPES
            or not 1 <= len(self.incident_id) <= 96
            or not 1 <= len(self.cause) <= 64
            or type(self.count) is not int
            or not 1 <= self.count <= 10_000
            or any(
                value is not None and (not value or len(value) > 512)
                for value in identifiers
            )
            or (
                preparation != (None, None, None, None)
                and (
                    type(self.diagnostic_reference) is not str
                    or _PREPARATION_REFERENCE.fullmatch(self.diagnostic_reference)
                    is None
                    or self.stage not in _PREPARATION_STAGES
                    or self.code not in _TERMINAL_PREPARATION_CODES
                    or type(self.attempt) is not int
                    or not 0 <= self.attempt <= 3
                )
            )
        ):
            raise OperationsError("invalid_incident")

    def to_data(self) -> dict[str, object]:
        return {
            "category": self.category,
            "code": self.code,
            "cause": self.cause,
            "connection": self.connection,
            "count": self.count,
            "field": self.field,
            "diagnostic_reference": self.diagnostic_reference,
            "incident_id": self.incident_id,
            "kind": self.kind,
            "library": self.library,
            "scope": self.scope,
            "stage": self.stage,
            "work_id": self.work_id,
            "attempt": self.attempt,
        }


class TimingRepository(Protocol):
    def append(self, timing: StageTiming) -> None: ...
    def list(self) -> tuple[StageTiming, ...]: ...


class StageTimer:
    """Best-effort diagnostic timing that can never become workflow authority."""

    def __init__(
        self,
        repository: TimingRepository | None,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._repository = repository
        self._monotonic_ns = monotonic_ns
        self._clock = clock

    @contextmanager
    def measure(self, stage: str, wait_class: str) -> Iterator[None]:
        started = self._monotonic_ns()
        try:
            yield
        finally:
            if self._repository is not None:
                try:
                    elapsed = self._monotonic_ns() - started
                except Exception:
                    # Diagnostics are deliberately unable to affect durable work.
                    pass
                else:
                    self.record(stage, wait_class, elapsed // 1_000_000)

    def record(
        self,
        stage: str,
        wait_class: str,
        duration_ms: int,
        *,
        recorded_at: int | None = None,
    ) -> None:
        """Append one already-observed duration without affecting its authority."""
        if self._repository is None:
            return
        try:
            bounded = min(max(duration_ms, 0), 86_400_000)
            self._repository.append(
                StageTiming(
                    stage,
                    bounded,
                    wait_class,
                    self._clock() if recorded_at is None else recorded_at,
                )
            )
        except Exception:
            # Diagnostics are deliberately unable to affect durable work.
            pass


class SupportExporter(Protocol):
    def preview(self) -> dict[str, object]: ...
    def export(self) -> bytes: ...


class OperationalStatus:
    """Compose one typed, value-free runtime snapshot."""

    def __init__(
        self,
        *,
        snapshot: Callable[[], dict[str, object]],
        timings: TimingRepository,
        support: SupportExporter | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._timings = timings
        self._support = support
        self._started = False
        self._draining = False

    def start(self) -> None:
        # Snapshot access proves exact-current repository reopen before ready.
        self._snapshot()
        self._started = True

    def begin_drain(self) -> None:
        self._draining = True

    def ready(self) -> bool:
        return self._started and not self._draining

    def status(self) -> dict[str, object]:
        incidents = operational_incidents(self._snapshot())
        return {
            "incidents": [value.to_data() for value in incidents],
            "readiness": "ready" if self.ready() else "draining",
            "schema_version": "operational-status-v1",
            "summary": {
                "automatic_recovery": sum(
                    value.kind == "automatic_recovery" for value in incidents
                ),
                "healthy": not any(
                    value.kind not in {"healthy_activity", "automatic_recovery"}
                    for value in incidents
                ),
                "owner_action_required": sum(
                    value.kind == "owner_action_required" for value in incidents
                ),
            },
            "timings": [value.to_data() for value in self._timings.list()],
        }

    def support_preview(self) -> dict[str, object]:
        if self._support is None:
            raise OperationsError("support_unavailable")
        return self._support.preview()

    def support_export(self) -> bytes:
        if self._support is None:
            raise OperationsError("support_unavailable")
        return self._support.export()


def aggregate_incidents(signals: object) -> tuple[Incident, ...]:
    # ``len`` and the iteration below are only reached once the exact-type test
    # has proven ``signals`` is a list or a tuple.
    if (
        type(signals) not in (list, tuple)
        or len(cast("Sequence[object]", signals)) > 1_000
    ):
        raise OperationsError("invalid_status")
    aggregated: dict[tuple[object, ...], Incident] = {}
    for signal in cast("Sequence[object]", signals):
        if type(signal) is not dict:
            raise OperationsError("invalid_status")
        kind = signal.get("kind")
        cause = signal.get("cause")
        scope = signal.get("scope")
        connection = signal.get("connection")
        library = signal.get("library")
        category = signal.get("category")
        work_id = signal.get("work_id")
        field = signal.get("field")
        diagnostic_reference = signal.get("diagnostic_reference")
        stage = signal.get("stage")
        code = signal.get("code")
        attempt = signal.get("attempt")
        # Currentness conflicts are intentionally work/field-local. Shared
        # service causes aggregate at their narrow service scope.
        key = (
            (
                kind,
                cause,
                scope,
                connection,
                library,
                category,
                work_id,
                field,
                diagnostic_reference,
                stage,
                code,
                attempt,
            )
            if cause == "currentness_conflict"
            else (
                kind,
                cause,
                scope,
                connection,
                library,
                category,
                None,
                field,
                diagnostic_reference,
                stage,
                code,
                attempt,
            )
        )
        previous = aggregated.get(key)
        readable_key = ":".join("-" if value is None else str(value) for value in key)
        canonical_key = json.dumps(
            key, ensure_ascii=True, separators=(",", ":")
        ).encode("ascii")
        incident_id = (
            "incident:"
            + readable_key[:22]
            + ":"
            + hashlib.sha256(canonical_key).hexdigest()
        )
        # ``Incident.__post_init__`` rejects any non-string kind, cause, or
        # scope before the value escapes this call.
        candidate = Incident(
            incident_id,
            cast("str", kind),
            cast("str", cause),
            cast("str", scope),
            connection,
            library,
            category,
            work_id if cause == "currentness_conflict" else None,
            field,
            diagnostic_reference,
            stage,
            code,
            attempt,
            1 if previous is None else previous.count + 1,
        )
        aggregated[key] = candidate
    return tuple(sorted(aggregated.values(), key=lambda item: item.incident_id))[
        :MAX_INCIDENTS
    ]


def operational_incidents(snapshot: dict[str, object]) -> tuple[Incident, ...]:
    """Project the bounded incident contract from one repository snapshot."""
    return aggregate_incidents(snapshot.get("signals", ()))


def support_preparation_incidents(snapshot: dict[str, object]) -> dict[str, object]:
    """Return only terminal preparation diagnostics for a re-redacted bundle."""
    incidents = operational_incidents(snapshot)
    return {
        "incidents": [
            {
                "attempt": incident.attempt,
                "code": incident.code,
                "diagnostic_reference": incident.diagnostic_reference,
                "stage": incident.stage,
            }
            for incident in incidents
            if incident.diagnostic_reference is not None
        ],
        "schema_version": "support-preparation-incidents-v1",
    }


def canonical_support(value: dict[str, object]) -> bytes:
    encoded = (
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )
    if len(encoded) > MAX_SUPPORT_BYTES:
        raise OperationsError("support_too_large")
    return encoded
