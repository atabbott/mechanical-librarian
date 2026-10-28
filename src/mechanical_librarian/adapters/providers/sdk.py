"""One bounded request session shared by report-only provider adapters.

Every metadata adapter carried its own copy of the same dispatch machinery:
the three-axis retry bound, the aggregate response budget, the deadline
arithmetic, the transport classification, and the attempt and incomplete-cause
evidence.  This module owns that machinery once.  What a provider actually
means -- its URL, its query grammar, its payload schema, and its status
vocabulary -- stays in the adapter, which supplies the request and one
classifier for the statuses it recognises.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderFailure,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
    bounded_attempt_elapsed,
    bounded_retry_after,
    transient_transport_failure,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderAttemptRecord,
    ProviderIncompleteCause,
    ProviderIncompleteCauseRecord,
)
from mechanical_librarian.domain.metadata_report import (
    ProviderStatus,
    provider_access_time,
)

_MAX_BACKOFF_SECONDS = 16.0


@dataclass(frozen=True, slots=True)
class ProviderBounds:
    """One observation's closed dispatch, byte and timing ceilings."""

    maximum_bytes: int
    timeout_seconds: float
    attempts_per_request: int
    attempts_per_observation: int
    aggregate_response_bytes: int


@dataclass(frozen=True, slots=True)
class ProviderClocks:
    """The four injectable clocks a bounded session reads, never wall time."""

    clock: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[], float] = random.random


@dataclass(frozen=True, slots=True)
class ProviderResponseFacts:
    """The bounded response facts an adapter may classify a status from.

    The payload is already charged against the observation's byte budget and
    the headers are the ones the provider returned, so a classifier never
    reads the stream or the clock itself.
    """

    status_code: int
    headers: httpx.Headers
    payload: bytes
    now: float


ProviderResponseClassifier = Callable[[ProviderResponseFacts], ProviderFailure | None]


def provider_bounds(
    maximum_bytes: int,
    timeout_seconds: float,
    *,
    attempts_per_request: int,
    attempts_per_observation: int,
    aggregate_multiple: int,
    aggregate_ceiling_bytes: int,
) -> ProviderBounds:
    """Validate one adapter's caller-supplied bounds before any dispatch."""
    if (
        type(maximum_bytes) is not int
        or isinstance(maximum_bytes, bool)
        or maximum_bytes < 1
        or type(timeout_seconds) not in {int, float}
        or isinstance(timeout_seconds, bool)
        or not 0 < timeout_seconds <= 60
    ):
        raise ValueError("provider bounds are invalid")
    return ProviderBounds(
        maximum_bytes,
        float(timeout_seconds),
        attempts_per_request,
        attempts_per_observation,
        min(maximum_bytes * aggregate_multiple, aggregate_ceiling_bytes),
    )


class BoundedProviderSession:
    """Dispatch one observation's requests under its closed bounds.

    The session is single-observation state: its byte budget, dispatch count,
    attempt records and incomplete causes all belong to the observation the
    owning adapter is producing, and none of them reset.
    """

    def __init__(
        self,
        name: str,
        client: httpx.Client,
        bounds: ProviderBounds,
        ledger: _DiscoveryRequestLedger,
        *,
        pacer: ProviderRequestPacer | None = None,
        clocks: ProviderClocks | None = None,
        accept_content_types: tuple[str, ...] = ("application/json",),
    ) -> None:
        if (
            type(accept_content_types) is not tuple
            or not accept_content_types
            or any(
                type(value) is not str or value != value.strip().lower()
                for value in accept_content_types
            )
        ):
            raise ValueError("provider content types are invalid")
        self._name = name
        self._client = client
        self._bounds = bounds
        self._ledger = ledger
        self._pacer = pacer
        self._clocks = ProviderClocks() if clocks is None else clocks
        # The closed set of media types this provider is permitted to return.
        # Anything outside it is `MALFORMED`, exactly as before: widening is a
        # per-adapter admission and never a fallback.
        self._accept_content_types = frozenset(accept_content_types)
        self.attempt_count = 0
        self.remaining_response_bytes = bounds.aggregate_response_bytes
        self._attempt_records: list[ProviderAttemptRecord] = []
        self._incomplete_causes: list[ProviderIncompleteCauseRecord] = []
        self._accessed_at: str | None = None

    @property
    def ledger(self) -> _DiscoveryRequestLedger:
        return self._ledger

    @ledger.setter
    def ledger(self, value: _DiscoveryRequestLedger) -> None:
        self._ledger = value

    @property
    def attempt_records(self) -> tuple[ProviderAttemptRecord, ...]:
        return tuple(self._attempt_records)

    @property
    def incomplete_causes(self) -> tuple[ProviderIncompleteCauseRecord, ...]:
        return tuple(self._incomplete_causes)

    @property
    def accessed_at(self) -> str | None:
        return self._accessed_at

    def request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, str] | httpx.QueryParams | None = None,
        headers: dict[str, str],
        deadline: float | None,
        query_retry: bool = False,
        classify: ProviderResponseClassifier,
        maximum_bytes: int | None = None,
        oversize_keeps_budget: bool = False,
    ) -> dict[str, Any]:
        """Return one decoded JSON object within the observation's bounds.

        `maximum_bytes` replaces the configured per-response bound for this one
        request only. It never lifts the observation's aggregate budget, which
        still caps every body it reads.

        A body over its bound ends the observation's byte budget, unless
        `oversize_keeps_budget` says the body is optional: then it costs only
        the bytes actually read, so a later lane can still be answered (#1308).
        A body that overruns the aggregate budget itself always ends it.
        """
        if maximum_bytes is not None and (
            type(maximum_bytes) is not int or maximum_bytes < 1
        ):
            raise ValueError("provider bounds are invalid")
        for retry_index in range(self._bounds.attempts_per_request):
            started = self._clocks.clock()
            attempts_before_dispatch = self.attempt_count
            try:
                value = self._request_once(
                    url,
                    method=method,
                    params=params,
                    headers=headers,
                    deadline=deadline,
                    is_retry=query_retry or retry_index > 0,
                    classify=classify,
                    maximum_bytes=maximum_bytes,
                    oversize_keeps_budget=oversize_keeps_budget,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                if self.attempt_count != attempts_before_dispatch:
                    self._record_attempt(
                        started,
                        category="cancellation",
                        delay=None,
                        outcome="cancelled",
                    )
                raise
            except ProviderFailure as error:
                if self.attempt_count == attempts_before_dispatch:
                    raise
                can_retry = (
                    error.retryable
                    and retry_index + 1 < self._bounds.attempts_per_request
                    and self.attempt_count < self._bounds.attempts_per_observation
                    and self._ledger.allows_retry(
                        self.attempt_count, self._bounds.attempts_per_observation
                    )
                )
                delay = (
                    error.retry_after
                    if error.retry_after is not None
                    else self._retry_delay(retry_index)
                    if can_retry
                    else None
                )
                if (
                    can_retry
                    and delay is not None
                    and not self._delay_fits(delay, deadline=deadline)
                ):
                    can_retry = False
                if not can_retry or delay is None:
                    self._record_attempt(
                        started,
                        category=error.category,
                        delay=delay,
                        outcome=("exhausted" if error.retryable else "terminal"),
                    )
                    raise
                try:
                    self._clocks.sleep(delay)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    self._record_attempt(
                        started,
                        category="cancellation",
                        delay=delay,
                        outcome="cancelled",
                    )
                    raise
                if deadline is not None and self._clocks.clock() >= deadline:
                    self._record_attempt(
                        started,
                        category=error.category,
                        delay=delay,
                        outcome="deadline_exhausted",
                    )
                    raise ProviderFailure(ProviderStatus.UNAVAILABLE) from None
                self._record_attempt(
                    started,
                    category=error.category,
                    delay=delay,
                    outcome="retry",
                )
                continue
            self._record_attempt(
                started,
                category="none",
                delay=None,
                outcome="response_received",
            )
            return value
        raise ProviderFailure(ProviderStatus.UNAVAILABLE)

    def _request_once(
        self,
        url: str,
        *,
        method: str,
        params: dict[str, str] | httpx.QueryParams | None,
        headers: dict[str, str],
        deadline: float | None,
        is_retry: bool,
        classify: ProviderResponseClassifier,
        maximum_bytes: int | None,
        oversize_keeps_budget: bool,
    ) -> dict[str, Any]:
        if self.remaining_response_bytes <= 0:
            raise self._incomplete(ProviderIncompleteCause.AGGREGATE_BYTES)
        if self.attempt_count >= self._bounds.attempts_per_observation:
            raise self._incomplete(ProviderIncompleteCause.DISPATCH_COUNT)
        if self._pacer is not None and not self._pacer.wait(deadline):
            raise self._incomplete(ProviderIncompleteCause.DEADLINE)
        remaining = self._bounds.timeout_seconds
        if deadline is not None:
            remaining = min(remaining, deadline - self._clocks.clock())
        if remaining <= 0:
            raise self._incomplete(ProviderIncompleteCause.DEADLINE)
        try:
            if deadline is not None and self._clocks.clock() >= deadline:
                raise self._incomplete(ProviderIncompleteCause.DEADLINE)
            if self._accessed_at is None:
                self._accessed_at = provider_access_time(self._clocks.wall_clock())
            self._ledger.dispatch(retry=is_retry)
            self.attempt_count += 1
            with self._client.stream(
                method,
                url,
                params=params,
                headers=headers,
                timeout=remaining,
            ) as response:
                identity_encoding = response.headers.get(
                    "content-encoding", "identity"
                ).strip().lower() in {"", "identity"}
                if response.is_stream_consumed and not identity_encoding:
                    raise ProviderFailure(
                        ProviderStatus.MALFORMED, category="response_malformed"
                    )
                payload = _body(
                    response,
                    min(
                        maximum_bytes or self._bounds.maximum_bytes,
                        self.remaining_response_bytes,
                    ),
                    deadline=deadline,
                    clock=self._clocks.clock,
                    consume=self._consume_response_bytes,
                )
                if not identity_encoding:
                    raise ProviderFailure(
                        ProviderStatus.MALFORMED, category="response_malformed"
                    )
                if response.status_code != 200:
                    raise self._failure(response, payload, classify=classify)
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    not in self._accept_content_types
                ):
                    raise ProviderFailure(
                        ProviderStatus.MALFORMED, category="response_malformed"
                    )
        except ProviderFailure as error:
            if error.status is ProviderStatus.OVERSIZED and not oversize_keeps_budget:
                self.remaining_response_bytes = 0
            raise
        except httpx.HTTPError as error:
            category = transient_transport_failure(error)
            if category is not None:
                raise ProviderFailure(
                    ProviderStatus.UNAVAILABLE,
                    category=category,
                    retryable=True,
                ) from None
            raise ProviderFailure(ProviderStatus.UNAVAILABLE) from None
        except (OSError, ValueError):
            raise ProviderFailure(ProviderStatus.UNAVAILABLE) from None
        try:
            value = decode_json(payload)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ProviderFailure(
                ProviderStatus.MALFORMED, category="response_malformed"
            ) from None
        if type(value) is not dict:
            raise ProviderFailure(
                ProviderStatus.MALFORMED, category="response_malformed"
            )
        return value

    def _failure(
        self,
        response: httpx.Response,
        payload: bytes,
        *,
        classify: ProviderResponseClassifier,
    ) -> ProviderFailure:
        """Turn one non-success status into this adapter's terminal outcome.

        A 503 carries the one retry signal every provider states the same way,
        so the session bounds it here.  Every other status belongs to the
        provider's own vocabulary, and an unrecognised one is not retryable.
        """
        now = self._clocks.wall_clock()
        if response.status_code == 503:
            return ProviderFailure(
                ProviderStatus.UNAVAILABLE,
                category="http_503",
                retryable=True,
                retry_after=bounded_retry_after(
                    response.headers.get("retry-after"),
                    now=now,
                ),
            )
        classified = classify(
            ProviderResponseFacts(
                response.status_code,
                response.headers,
                payload,
                now,
            )
        )
        return (
            ProviderFailure(
                ProviderStatus.UNAVAILABLE,
                category=f"http_{response.status_code}",
            )
            if classified is None
            else classified
        )

    def _consume_response_bytes(self, amount: int) -> None:
        self.remaining_response_bytes -= amount
        if self.remaining_response_bytes < 0:
            self.remaining_response_bytes = 0
            raise ProviderFailure(
                ProviderStatus.OVERSIZED, category="response_oversized"
            )

    def _incomplete(self, cause: ProviderIncompleteCause) -> ProviderFailure:
        self._incomplete_causes.append(ProviderIncompleteCauseRecord(cause))
        return ProviderFailure(ProviderStatus.INCOMPLETE)

    def _retry_delay(self, retry_index: int) -> float:
        jitter = self._clocks.jitter()
        if (
            type(jitter) not in {int, float}
            or isinstance(jitter, bool)
            or not 0.0 <= float(jitter) < 1.0
        ):
            jitter = 0.0
        return min(float(2**retry_index) + float(jitter), _MAX_BACKOFF_SECONDS)

    def _delay_fits(self, delay: float, *, deadline: float | None) -> bool:
        if deadline is None:
            return True
        return self._clocks.clock() + delay < deadline

    def _record_attempt(
        self,
        started: float,
        *,
        category: str,
        delay: float | None,
        outcome: str,
    ) -> None:
        self._attempt_records.append(
            ProviderAttemptRecord(
                provider=self._name,
                ordinal=self.attempt_count,
                category=category,
                delay_seconds=delay,
                elapsed_seconds=bounded_attempt_elapsed(started, self._clocks.clock()),
                outcome=outcome,
            )
        )


def decode_json(payload: bytes) -> object:
    """Decode strict JSON, rejecting duplicates, constants and float drift."""
    return json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )


def _body(
    response: httpx.Response,
    maximum: int,
    *,
    deadline: float | None,
    clock: Callable[[], float],
    consume: Callable[[int], None],
) -> bytes:
    length = response.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
            if declared < 0:
                raise ProviderFailure(
                    ProviderStatus.MALFORMED, category="response_malformed"
                )
            if declared > maximum:
                raise ProviderFailure(
                    ProviderStatus.OVERSIZED, category="response_oversized"
                )
        except ValueError:
            raise ProviderFailure(
                ProviderStatus.MALFORMED, category="response_malformed"
            ) from None
    chunks: list[bytes] = []
    total = 0
    raw_chunks = (
        (response.content,) if response.is_stream_consumed else response.iter_raw()
    )
    for chunk in raw_chunks:
        consume(len(chunk))
        total += len(chunk)
        if total > maximum:
            raise ProviderFailure(
                ProviderStatus.OVERSIZED, category="response_oversized"
            )
        chunks.append(chunk)
        if deadline is not None and clock() >= deadline:
            raise ProviderFailure(ProviderStatus.UNAVAILABLE)
    return b"".join(chunks)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("provider object contains a duplicate field")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ValueError("provider number is invalid")


__all__ = [
    "BoundedProviderSession",
    "ProviderBounds",
    "ProviderClocks",
    "ProviderResponseClassifier",
    "ProviderResponseFacts",
    "decode_json",
    "provider_bounds",
]


@contextmanager
def provider_client_lifetime(client: httpx.Client) -> Iterator[None]:
    """Close at the existing boundary without replacing its dispatch failure."""
    try:
        yield
    except BaseException:
        try:
            client.close()
        except BaseException:
            pass
        raise
    else:
        client.close()
