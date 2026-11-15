"""Reuse of a provider answer already held, with live fallback (#1064).

`BoundedProviderSession` asks one `ProviderResponseReuse` before it paces or
dispatches a request. A held answer is served in place of the network; a miss
goes live through the normal path, and the answer is held until the adapter's
observation ends. `RetainingProvider` is the one commit point: it keeps an
observation's held answers only when the observation and every lane of it
ended clean, so a failed, partial or refused answer is never stored.

A request is keyed by the adapter kind and the SHA-256 of its method, URL,
sorted query, body and the adapter's revision below. Headers never enter the
key, so neither does a credential. A revision is raised when an adapter starts
reading an answer differently enough that a held one would mislead it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx

from mechanical_librarian.adapters.persistence.provider_responses import (
    MAX_PAYLOAD_BYTES,
    SQLiteProviderResponseStore,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    MetadataProvider,
    ProviderResult,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus

_REVISIONS = {
    "audible_catalog": 1,
    "audiosilo": 1,
    "audnexus": 1,
    "google_books": 1,
    "hardcover": 1,
    "inventaire": 1,
    "itunes_search": 1,
    "open_library": 1,
}
# The statuses an observation and each of its lanes must end in for its
# answers to be kept. `not_needed` is never an adapter's own answer.
_CLEAN = frozenset(
    {
        ProviderStatus.CANDIDATES,
        ProviderStatus.NO_MATCH,
        ProviderStatus.INSUFFICIENT_INPUT,
    }
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def epoch_microseconds(timestamp: float) -> int:
    """Return the whole UTC microsecond `provider_access_time` would name."""
    observed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return (observed - _EPOCH) // timedelta(microseconds=1)


def _canonical_request(
    revision: int,
    method: str,
    url: str,
    params: dict[str, str] | httpx.QueryParams | None,
    content: bytes | None,
) -> bytes:
    """Return the exact bytes a request's digest is taken over."""
    target = httpx.URL(url).copy_merge_params(params or {})
    return json.dumps(
        [
            revision,
            method.upper(),
            f"{target.scheme}://{target.host}:{target.port}{target.path}",
            sorted(httpx.QueryParams(target.query).multi_items()),
            (content or b"").hex(),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")


class ProviderResponseReuse:
    """One adapter observation's reads from, and held answers for, the store."""

    def __init__(
        self, store: SQLiteProviderResponseStore, adapter: str, refresh: bool
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._revision = _REVISIONS[adapter]
        # A re-search asks every provider live and replaces what is held.
        self._refresh = refresh
        self._held: dict[int, tuple[str, int, bytes]] = {}

    def digest(
        self,
        method: str,
        url: str,
        params: dict[str, str] | httpx.QueryParams | None,
        content: bytes | None,
    ) -> str:
        return hashlib.sha256(
            _canonical_request(self._revision, method, url, params, content)
        ).hexdigest()

    def lookup(self, digest: str) -> tuple[bytes, int] | None:
        return None if self._refresh else self._store.get(self._adapter, digest)

    def hold(
        self, ordinal: int, digest: str, payload: bytes, fetched_at_us: int
    ) -> None:
        if 0 < len(payload) <= MAX_PAYLOAD_BYTES:
            self._held[ordinal] = (digest, fetched_at_us, payload)

    def drop(self, ordinal: int) -> None:
        """Forget the answer an adapter refused after the session accepted it."""
        self._held.pop(ordinal, None)

    def settle(self, clean: bool) -> None:
        held = tuple(self._held.values())
        self._held.clear()
        if clean:
            self._store.commit(self._adapter, held)


class RetainingProvider:
    """Keep an adapter's held answers only when its observation ended clean."""

    def __init__(
        self, provider: MetadataProvider, reuse: ProviderResponseReuse
    ) -> None:
        self._provider = provider
        self._reuse = reuse

    def observe(
        self, plan: MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> ProviderResult:
        try:
            result = self._provider.observe(plan, deadline=deadline)
        except BaseException:
            self._reuse.settle(False)
            raise
        observation = result.observation
        self._reuse.settle(
            observation.status in _CLEAN
            and all(lane.status in _CLEAN for lane in observation.lanes)
        )
        return result


def provider_response_reuse(
    store: SQLiteProviderResponseStore | None, adapter: str, refresh: bool
) -> ProviderResponseReuse | None:
    """Bind one adapter construction to the store, when there is one."""
    if store is None or adapter not in _REVISIONS:
        return None
    return ProviderResponseReuse(store, adapter, refresh)


def retaining_provider(
    provider: MetadataProvider, reuse: ProviderResponseReuse | None
) -> MetadataProvider:
    return provider if reuse is None else RetainingProvider(provider, reuse)


__all__ = [
    "ProviderResponseReuse",
    "RetainingProvider",
    "epoch_microseconds",
    "provider_response_reuse",
    "retaining_provider",
]
