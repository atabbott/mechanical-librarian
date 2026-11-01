"""Stable, aggregate-safe failure diagnostics for application boundaries."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, cast

from mechanical_librarian.application.provider_evidence import (
    ProviderIncompleteCause,
    ProviderOperationalEvidence,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.failure_diagnostics import (
    MAX_DIAGNOSTIC_COUNT,
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    diagnostics_are_canonical,
)
from mechanical_librarian.domain.metadata_report import (
    PROVIDER_AUTHORITIES,
    DispatchOutcome,
    ProviderObservation,
    ProviderStatus,
    ReportValueError,
)
from mechanical_librarian.domain.provider_recovery import (
    MAX_RECOVERY_DELAY,
    RECOVERY_OUTCOMES,
    ProviderRecoveryJob,
    RecoveryAttemptEvidence,
)
from mechanical_librarian.domain.validation import (
    FieldCheck,
    bounded_integer,
    matching,
    one_of,
)

if TYPE_CHECKING:
    from mechanical_librarian.application.metadata_report import (
        MetadataDiscoveryLane,
    )


def provider_attempt_diagnostics(
    attempts: object,
    *,
    status: str | None = None,
    incomplete_causes: object = (),
    identity_conflict_only: bool = False,
    work_identity_refused_only: bool = False,
    adapter_fault: bool = False,
) -> tuple[FailureDiagnostic, ...]:
    """Discard attempt payload details and return deterministic bounded aggregates.

    `adapter_fault` says the unavailable status is the adapter's own fault,
    which its own diagnostic names, so it is not also counted as unknown.
    """

    counts: Counter[tuple[FailureCategory, FailureStage, bool | None]] = Counter()
    if type(attempts) is tuple:
        bounded_attempts: tuple[object, ...] = attempts
    else:
        bounded_attempts = ()
        status = "unknown"
    for attempt in bounded_attempts:
        try:
            category = getattr(attempt, "category", None)
            outcome = getattr(attempt, "outcome", None)
        except Exception:
            # ``attempt`` is whatever a provider adapter put in its records, so
            # these may be computed properties with no declared error type. A
            # missing attribute already yields ``None``; only a property that
            # fails reaches here, and ``None`` routes it to the same unknown
            # bucket as a value of the wrong type on the next line. Nothing is
            # dropped -- every attempt is still counted.
            category = None
            outcome = None
        key: tuple[FailureCategory, FailureStage, bool | None]
        if type(category) is not str or type(outcome) is not str:
            key = (
                FailureCategory.UNKNOWN,
                FailureStage.RESPONSE,
                None,
            )
        else:
            mapped = _provider_attempt_category(category)
            if mapped is None:
                continue
            category, stage = mapped
            retryable = (
                True
                if outcome in {"retry", "exhausted"}
                else (
                    False
                    if outcome in {"terminal", "cancelled", "deadline_exhausted"}
                    else None
                )
            )
            key = (category, stage, retryable)
        counts[key] = min(counts[key] + 1, MAX_DIAGNOSTIC_COUNT)
    status_mapping: dict[
        str | None, tuple[FailureCategory, FailureStage, bool | None]
    ] = {
        "authentication": (
            FailureCategory.AUTHENTICATION,
            FailureStage.RESPONSE,
            False,
        ),
        "authorization": (
            FailureCategory.AUTHENTICATION,
            FailureStage.RESPONSE,
            False,
        ),
        "rate_limited": (
            FailureCategory.QUOTA_RATE_LIMIT,
            FailureStage.RESPONSE,
            None,
        ),
        "no_match": (
            FailureCategory.EMPTY_NO_MATCH,
            FailureStage.RESPONSE,
            False,
        ),
        "malformed": (
            FailureCategory.MALFORMED_RESPONSE,
            FailureStage.RESPONSE,
            False,
        ),
        "oversized": (
            FailureCategory.OVERSIZED_RESPONSE,
            FailureStage.RESPONSE,
            False,
        ),
        "candidate_limit_exceeded": (
            FailureCategory.CANDIDATE_LIMIT_EXCEEDED,
            FailureStage.RESPONSE,
            False,
        ),
        "query_limit_exceeded": (
            FailureCategory.LOOKUP_CONSTRUCTION,
            FailureStage.LOOKUP,
            False,
        ),
        "unavailable": (
            FailureCategory.UNKNOWN,
            FailureStage.RESPONSE,
            None,
        ),
        "unknown": (
            FailureCategory.UNKNOWN,
            FailureStage.RESPONSE,
            None,
        ),
    }
    invalid_incomplete_causes = False
    if status == "incomplete":
        if type(incomplete_causes) is not tuple:
            invalid_incomplete_causes = True
        else:
            cause_mapping = {
                "deadline": (FailureCategory.TIMEOUT, FailureStage.REQUEST),
                "aggregate_bytes": (
                    FailureCategory.OVERSIZED_RESPONSE,
                    FailureStage.RESPONSE,
                ),
                "dispatch_count": (
                    FailureCategory.LOOKUP_CONSTRUCTION,
                    FailureStage.LOOKUP,
                ),
            }
            for record in incomplete_causes:
                try:
                    cause = getattr(record, "cause", None)
                except Exception:
                    # Same adapter-supplied record and the same reasoning as
                    # the attempt loop. ``None`` fails the mapping below, which
                    # marks the causes invalid rather than silently accepting
                    # an aggregate built from a cause it could not read.
                    cause = None
                mapped = cause_mapping.get(cause) if isinstance(cause, str) else None
                if mapped is None:
                    invalid_incomplete_causes = True
                    continue
                counts[(mapped[0], mapped[1], False)] = min(
                    counts[(mapped[0], mapped[1], False)] + 1,
                    MAX_DIAGNOSTIC_COUNT,
                )
    required = status_mapping.get(status)
    if (
        required is not None
        and (required[0] is not FailureCategory.UNKNOWN or not counts)
        and not (
            status == "rate_limited"
            and any(
                category
                in {
                    FailureCategory.BURST_THROTTLE,
                    FailureCategory.DAILY_QUOTA_EXHAUSTED,
                }
                for category, _, _ in counts
            )
        )
        and not (
            # An ambiguous provider identity is already stated on every lane the
            # conflict failed, and it is not a malformed response. The caller
            # resolves that from the lanes, so a lane that was malformed for its
            # own reason still keeps the status-derived aggregate alongside the
            # identity diagnostic.
            status == "malformed" and identity_conflict_only
        )
        and not (
            # Products that work-identity admission refused on every rung are
            # stated by the lanes as refusals, not as an empty response.
            status == "no_match" and work_identity_refused_only
        )
        and not (status == "unavailable" and adapter_fault)
        and not any(category is required[0] for category, _, _ in counts)
    ):
        counts[required] = 1
    if status == "incomplete" and (invalid_incomplete_causes or not incomplete_causes):
        counts[(FailureCategory.UNKNOWN, FailureStage.RESPONSE, None)] = 1
    return tuple(
        FailureDiagnostic(
            category,
            FailureComponent.PROVIDER,
            stage,
            retryable=retryable,
            count=count,
        )
        for (category, stage, retryable), count in sorted(
            counts.items(),
            key=lambda item: (
                item[0][0].value,
                item[0][1].value,
                -1 if item[0][2] is None else int(item[0][2]),
            ),
        )
    )


# The closed classes of a fault an adapter raised itself (#1498). The class is
# read from the exception's type alone, never its text: a domain object refusing
# what the adapter built from a response, or a programming error in the adapter.
# Anything else -- a transport library's own error, say -- is not named a fault
# and keeps the unavailable lane and its scheduled recovery.
ADAPTER_FAULT_CLASSES = ("report_validation", "programming")
ADAPTER_FAULT_DIAGNOSTIC = FailureDiagnostic(
    FailureCategory.ADAPTER_FAULT,
    FailureComponent.PROVIDER,
    FailureStage.EXECUTION,
    retryable=False,
)


def adapter_fault_class(error: BaseException) -> str | None:
    """Name the closed class of an adapter's own fault, or None."""
    cause = error.__cause__ if error.__cause__ is not None else error
    if isinstance(cause, (ReportValueError, EvidenceValueError)):
        return "report_validation"
    if isinstance(
        cause,
        (
            ArithmeticError,
            AssertionError,
            AttributeError,
            LookupError,
            RecursionError,
            TypeError,
            ValueError,
        ),
    ):
        return "programming"
    return None


def merge_provider_diagnostics(
    *groups: tuple[FailureDiagnostic, ...],
) -> tuple[FailureDiagnostic, ...]:
    """Canonically coalesce bounded provider aggregates without replay inflation."""

    counts: Counter[tuple[FailureCategory, FailureStage, bool | None]] = Counter()
    for group in groups:
        if not diagnostics_are_canonical(group):
            raise ValueError("provider diagnostics are invalid")
        for value in group:
            if (
                value.component is not FailureComponent.PROVIDER
                or value.current is not None
            ):
                raise ValueError("provider diagnostics are invalid")
            key = (value.category, value.stage, value.retryable)
            counts[key] = min(counts[key] + value.count, MAX_DIAGNOSTIC_COUNT)
    return tuple(
        FailureDiagnostic(
            category, FailureComponent.PROVIDER, stage, retryable=retryable, count=count
        )
        for (category, stage, retryable), count in sorted(
            counts.items(),
            key=lambda item: (
                item[0][0].value,
                item[0][1].value,
                -1 if item[0][2] is None else int(item[0][2]),
            ),
        )
    )


def _provider_attempt_category(
    category: str,
) -> tuple[FailureCategory, FailureStage] | None:
    if category == "none":
        return None
    if category == "cancellation":
        return FailureCategory.CANCELLATION, FailureStage.EXECUTION
    if category == "deadline" or category.endswith("_timeout"):
        return FailureCategory.TIMEOUT, FailureStage.REQUEST
    if category.startswith("transport_"):
        return FailureCategory.TRANSPORT, FailureStage.REQUEST
    if category in {"http_401", "http_403"}:
        return FailureCategory.AUTHENTICATION, FailureStage.RESPONSE
    if category == "burst_throttled":
        return FailureCategory.BURST_THROTTLE, FailureStage.RESPONSE
    if category == "quota_exhausted":
        return FailureCategory.DAILY_QUOTA_EXHAUSTED, FailureStage.RESPONSE
    if category == "http_429":
        return FailureCategory.QUOTA_RATE_LIMIT, FailureStage.RESPONSE
    if category.startswith("http_"):
        return FailureCategory.HTTP_NON_SUCCESS, FailureStage.RESPONSE
    if category == "not_found":
        return FailureCategory.EMPTY_NO_MATCH, FailureStage.RESPONSE
    if category == "response_malformed":
        return FailureCategory.MALFORMED_RESPONSE, FailureStage.RESPONSE
    if category == "response_oversized":
        return FailureCategory.OVERSIZED_RESPONSE, FailureStage.RESPONSE
    if category == "candidate_limit_exceeded":
        return FailureCategory.CANDIDATE_LIMIT_EXCEEDED, FailureStage.RESPONSE
    if category == "provider_declared_error":
        return FailureCategory.PROVIDER_DECLARED_ERROR, FailureStage.RESPONSE
    if category == "provider_failure":
        return FailureCategory.UNKNOWN, FailureStage.RESPONSE
    return FailureCategory.UNKNOWN, FailureStage.RESPONSE


__all__ = [
    "FailureCategory",
    "FailureComponent",
    "FailureDiagnostic",
    "FailureStage",
    "MAX_DIAGNOSTIC_COUNT",
    "diagnostics_are_canonical",
    "merge_provider_diagnostics",
    "provider_attempt_diagnostics",
]


def _identity_conflict_is_the_only_malformed_reason(
    observation: ProviderObservation,
) -> bool:
    """Report whether an ambiguous identity is the whole of this malformed status.

    Only a lane the identity conflict itself failed carries the identity
    diagnostic, so a lane that is malformed for its own reason -- an
    independently unparsable page -- keeps the aggregate. An oversized or
    candidate-limited lane names its own category and is not read here.
    """
    if not any(
        value.category is FailureCategory.IDENTITY for value in observation.diagnostics
    ):
        return False
    malformed = tuple(
        lane for lane in observation.lanes if lane.status is ProviderStatus.MALFORMED
    )
    return bool(malformed) and all(
        any(value.category is FailureCategory.IDENTITY for value in lane.diagnostics)
        for lane in malformed
    )


def _work_identity_refusal_is_the_only_no_match_reason(
    observation: ProviderObservation,
) -> bool:
    """Report whether every no-match lane had all its products refused."""
    no_match = tuple(
        lane for lane in observation.lanes if lane.status is ProviderStatus.NO_MATCH
    )
    return bool(no_match) and all(
        lane.exhausted_dispatch
        and not any(
            value.category is FailureCategory.EMPTY_NO_MATCH
            for value in lane.diagnostics
        )
        for lane in no_match
    )


def provider_result_diagnostics(
    observation: ProviderObservation, evidence: ProviderOperationalEvidence
) -> tuple[FailureDiagnostic, ...]:
    """Merge adapter diagnostics, lane refusals and operational evidence once.

    The refused-product count is lifted from the lanes here and nowhere else, so
    a retry successor that merges two of these results never counts it twice.
    """
    return merge_provider_diagnostics(
        observation.diagnostics,
        *(
            tuple(
                value
                for value in lane.diagnostics
                if value.category is FailureCategory.WORK_IDENTITY_REFUSED
            )
            for lane in observation.lanes
        ),
        provider_attempt_diagnostics(
            evidence.attempt_records,
            status=observation.status.value,
            incomplete_causes=evidence.incomplete_causes,
            identity_conflict_only=_identity_conflict_is_the_only_malformed_reason(
                observation
            ),
            work_identity_refused_only=(
                _work_identity_refusal_is_the_only_no_match_reason(observation)
            ),
            adapter_fault=ADAPTER_FAULT_DIAGNOSTIC.category
            in {value.category for value in observation.diagnostics},
        ),
    )


# Provider log events (#1299). Each is one value-free JSON line on stderr: every
# key is allow-listed and every value is a closed word, a bounded integer or
# null, so no title, author, URL, body, credential, path or item id can reach
# it. `emit_provider_events` is the one writer, as `review_pipeline` writes its
# own diagnostics: a function call on `sys.stderr`, with no handler, level,
# switch or global sink.
_THROTTLES = frozenset(
    {
        FailureCategory.QUOTA_RATE_LIMIT,
        FailureCategory.BURST_THROTTLE,
        FailureCategory.DAILY_QUOTA_EXHAUSTED,
    }
)
_HTTP_STATUS = re.compile(r"http_([1-5][0-9]{2})")
# A provider's ordinary "not found", which its adapter answers as no match. A
# tuple compares by equality, so an unhashable category cannot raise here.
# `not_found` is an adapter's own statement that the status was a miss (#1308),
# so it never names a failure; the HTTP forms do only beside a failed lane.
_NOT_FOUND = ("http_404", "region_unavailable")
_MAX_EVENT_MS = 86_400_000


_PROVIDER_EVENT_FIELDS: dict[str, FieldCheck] = {
    "event": one_of(
        "provider_request_failed",
        "provider_throttled",
        "provider_deadline_stop",
        "provider_recovery_job",
        "provider_adapter_fault",
    ),
    "level": one_of("info", "warning"),
    "adapter": one_of(*(value for value in PROVIDER_AUTHORITIES if value)),
    "provider": matching(re.compile(r"[a-z][a-z0-9_-]{0,63}")),
    "item_ref": matching(re.compile(r"item-[0-9a-f]{16}")),
    "phase": one_of("discovery", "asin_feed", "recovery"),
    "lane_kind": one_of("isbn", "work", "asin", "mixed"),
    "query_family": one_of("identifier", "asserted", "path_leaf", "title_tag", "mixed"),
    "outcome_class": one_of(
        "held", *(value.value for value in FailureCategory), *RECOVERY_OUTCOMES
    ),
    "http_status": bounded_integer(100, 599),
    "attempt_outcome": one_of(
        "retry", "exhausted", "terminal", "cancelled", "deadline_exhausted"
    ),
    "stop": one_of("before_dispatch", "in_flight"),
    "fault_class": one_of(*ADAPTER_FAULT_CLASSES),
    "count": bounded_integer(1, MAX_DIAGNOSTIC_COUNT),
    "retry_after_ms": bounded_integer(0, MAX_RECOVERY_DELAY * 1000),
    "elapsed_ms": bounded_integer(0, _MAX_EVENT_MS),
    "deadline_ms": bounded_integer(0, _MAX_EVENT_MS),
    "deadline_kind": one_of("provider_share", "aggregate", "recovery_window"),
    "transition": one_of("started", "settled", "failed", "stale", "held"),
    "job_attempt": bounded_integer(0, 5),
    "physical_requests": bounded_integer(0, 54),
    "dispatch_reason": one_of(
        "missing_corroboration",
        "zero_provider_evidence",
        "settleable_disagreement",
        "blanket_scan",
    ),
}


def emit_provider_events(
    build: Callable[..., Iterable[Mapping[str, object]]], /, **arguments: object
) -> None:
    """Write each event `build(**arguments)` returns as one JSON line; never raise.

    `build` runs inside the guard, so neither building nor writing can change
    the dispatch or settlement the events describe. A key outside the allow-list
    is dropped and an out-of-shape value becomes null; a line over 1024 bytes is
    dropped whole. Each line is one `write`, which keeps concurrent writers'
    lines whole on a pipe.
    """
    try:
        for event in build(**arguments):
            line = (
                json.dumps(
                    {
                        name: event[name] if check(event[name]) else None
                        for name, check in _PROVIDER_EVENT_FIELDS.items()
                        if name in event
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            if len(line) <= 1024:
                sys.stderr.write(line)
    except Exception:
        # Observation only: an unwritable stderr or an unexpected value drops
        # the line, never the dispatch or settlement it described.
        pass


def provider_item_ref(connection: str, library: str, item: str) -> str:
    """Correlate one item's events without logging its raw identifier."""
    # `surrogatepass` keeps this total over any text, so a caller may compute it
    # outside the event guard.
    material = "\0".join((connection, library, item)).encode("utf-8", "surrogatepass")
    return "item-" + hashlib.sha256(material).hexdigest()[:16]


def _milliseconds(seconds: object) -> int | None:
    # The bound is checked before rounding, so NaN, an infinity or a huge
    # finite value names no duration instead of raising.
    if type(seconds) not in {int, float}:
        return None
    if not abs(cast(float, seconds)) * 1000 <= _MAX_EVENT_MS:
        return None
    return round(cast(float, seconds) * 1000)


def _lane_shape(lane: MetadataDiscoveryLane) -> tuple[str, str]:
    kind = lane.kind.value
    return kind, "identifier" if kind != "work" else lane.title_basis or "asserted"


def provider_dispatch_events(
    *,
    adapter: str | None,
    provider: str,
    item_ref: str,
    phase: str,
    lanes: tuple[MetadataDiscoveryLane, ...],
    observation: ProviderObservation | None,
    evidence: ProviderOperationalEvidence,
    audio: bool,
    elapsed_seconds: float,
    deadline_seconds: float,
    deadline_kind: str,
) -> tuple[dict[str, object], ...]:
    """Name each request failure, throttle and deadline stop of one dispatch.

    Attempts carry no lane, so an attempt event states the lanes the
    observation answered, `mixed` when they differ. One event stands for each
    distinct (event, class, HTTP status, attempt outcome), with its `count`. A
    malformed lane, or a rate-limited lane the shared pacer held, is named from
    the lane when no attempt already named that class. A request the deadline
    stopped before dispatch is counted from the incomplete causes, and on an
    audiobook only where a lane was recorded as starved. A clean dispatch has
    no event, and neither has an adapter's `not_found` attempt, or an HTTP "not
    found" attempt when every lane answered with candidates or no match: that is
    the provider's ordinary answer.
    """
    shapes = {lane.identity: _lane_shape(lane) for lane in lanes}
    answered = (
        tuple(shapes.values())
        if observation is None
        else tuple(
            shapes[lane.identity]
            for lane in observation.lanes
            if lane.identity in shapes
        )
    )

    def event(
        name: str, shape: Iterable[tuple[str, str]], outcome: str, count: int
    ) -> dict[str, object]:
        kinds = {kind for kind, _ in shape}
        families = {family for _, family in shape}
        return {
            "event": name,
            "level": "warning",
            "adapter": adapter,
            "provider": provider,
            "item_ref": item_ref,
            "phase": phase,
            "lane_kind": kinds.pop() if len(kinds) == 1 else "mixed" if kinds else None,
            "query_family": (
                families.pop() if len(families) == 1 else "mixed" if families else None
            ),
            "outcome_class": outcome,
            "http_status": None,
            "elapsed_ms": _milliseconds(elapsed_seconds),
            "deadline_ms": _milliseconds(deadline_seconds),
            "deadline_kind": deadline_kind,
            "count": min(count, MAX_DIAGNOSTIC_COUNT),
        }

    answered_cleanly = (
        observation is not None
        and bool(observation.lanes)
        and all(
            lane.status in {ProviderStatus.NO_MATCH, ProviderStatus.CANDIDATES}
            for lane in observation.lanes
        )
    )
    attempts: Counter[tuple[str, str, int | None, object]] = Counter()
    delays: dict[tuple[str, str, int | None, object], int | None] = {}
    for record in evidence.attempt_records or ():
        category = record.category
        mapped = (
            _provider_attempt_category(category)
            if type(category) is str
            else (FailureCategory.UNKNOWN, FailureStage.RESPONSE)
        )
        if (
            mapped is None
            or category == "not_found"
            or (answered_cleanly and category in _NOT_FOUND)
        ):
            continue
        http = _HTTP_STATUS.fullmatch(category) if type(category) is str else None
        attempt_key = (
            "provider_deadline_stop"
            if record.outcome == "deadline_exhausted"
            else "provider_throttled"
            if mapped[0] in _THROTTLES
            else "provider_request_failed",
            mapped[0].value,
            None if http is None else int(http.group(1)),
            record.outcome,
        )
        attempts[attempt_key] += 1
        delays[attempt_key] = _milliseconds(record.delay_seconds)
    events = []
    if evidence.adapter_fault is not None:
        # The adapter itself raised (#1498): one event names its closed class,
        # never the exception or anything it carried.
        value = event(
            "provider_adapter_fault",
            answered,
            FailureCategory.ADAPTER_FAULT.value,
            1,
        )
        value["fault_class"] = evidence.adapter_fault
        events.append(value)
    for key, count in attempts.items():
        name, outcome, status, attempt = key
        value = event(name, answered, outcome, count)
        value.update(http_status=status, attempt_outcome=attempt)
        if name == "provider_throttled":
            value["retry_after_ms"] = delays[key]
        if name == "provider_deadline_stop":
            value["stop"] = "in_flight"
        events.append(value)
    named = {outcome for _, outcome, _, _ in attempts}
    observed = () if observation is None else observation.lanes
    for lane_status, name, outcome, covered in (
        (
            ProviderStatus.MALFORMED,
            "provider_request_failed",
            FailureCategory.MALFORMED_RESPONSE.value,
            FailureCategory.MALFORMED_RESPONSE.value in named,
        ),
        (
            ProviderStatus.RATE_LIMITED,
            "provider_throttled",
            "held",
            any(value.value in named for value in _THROTTLES),
        ),
    ):
        if covered:
            continue
        groups = Counter(
            shapes[lane.identity]
            for lane in observed
            if lane.status is lane_status and lane.identity in shapes
        )
        for shape, count in sorted(groups.items()):
            value = event(name, (shape,), outcome, count)
            if name == "provider_throttled":
                value["retry_after_ms"] = None
            events.append(value)
    stopped = sum(
        record.cause is ProviderIncompleteCause.DEADLINE
        for record in evidence.incomplete_causes or ()
    )
    stops: list[tuple[tuple[tuple[str, str], ...], int]]
    if audio:
        starved = Counter(
            shapes[lane.identity]
            for lane in observed
            if lane.identity in shapes
            and lane.dispatch is not None
            and lane.dispatch.outcome is DispatchOutcome.STARVED
        )
        stops = [((shape,), count) for shape, count in sorted(starved.items())]
    else:
        stops = [(answered, stopped)] if stopped else []
    for stopped_shape, count in stops:
        value = event(
            "provider_deadline_stop",
            stopped_shape,
            FailureCategory.TIMEOUT.value,
            count,
        )
        value["stop"] = "before_dispatch"
        events.append(value)
    return tuple(events)


def provider_recovery_job_events(
    *,
    job: ProviderRecoveryJob,
    transition: str,
    evidence: RecoveryAttemptEvidence | None,
    elapsed_seconds: float,
) -> tuple[dict[str, object]]:
    """Name one committed recovery-job transition (#1299)."""
    return (
        {
            "event": "provider_recovery_job",
            "level": "warning" if transition == "failed" else "info",
            "adapter": job.adapter,
            "provider": job.connection,
            "item_ref": provider_item_ref(job.target_connection, job.library, job.item),
            "phase": "recovery",
            "lane_kind": None,
            "query_family": None,
            "outcome_class": None if evidence is None else evidence.outcome,
            "http_status": None,
            "elapsed_ms": _milliseconds(elapsed_seconds),
            "deadline_ms": None,
            "deadline_kind": None,
            "transition": transition,
            "job_attempt": job.attempt,
            "physical_requests": None
            if evidence is None
            else evidence.physical_requests,
            "dispatch_reason": job.dispatch_reason,
        },
    )
