"""Reuse of a provider answer already held, with live fallback (#1064).

`BoundedProviderSession` asks one `ProviderResponseReuse` before it paces or
dispatches a request. A held answer is served in place of the network; a miss
goes live through the normal path, and the answer is held until the adapter's
observation ends. `RetainingProvider` is the one commit point: it keeps an
observation's held answers only when the observation and every lane of it
ended clean, so a failed, partial or refused answer is never stored. Nor is an
answer to a request one of whose dispatches failed in that observation: the
failure is in the live accounting, and a held answer would be served in its
place. Every answer an observation keeps is dated to its first live dispatch,
and an observation reports the earliest date among the answers it used, so
one that reuses answers reads as the observation that stored them.

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
        # The dispatches served from the store, by ordinal.
        self._served: dict[int, str] = {}
        # The requests one of whose dispatches failed in this observation.
        self._failed: set[str] = set()

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

    def failed(self, digest: str) -> None:
        """Keep no answer to a request one of whose dispatches failed.

        The failed dispatch is in the live observation's accounting and
        diagnostics. A held answer would be served in its place, so a retried
        request is asked again until it succeeds at once.
        """
        self._failed.add(digest)

    def served(self, ordinal: int, digest: str) -> None:
        self._served[ordinal] = digest

    def drop(self, ordinal: int) -> None:
        """Forget the answer a reader or adapter refused.

        A live answer is no longer held for the commit. A reused one is removed
        from the store: it was read clean when it was kept, so what reads it
        has changed, and the next request for it goes live.
        """
        self._held.pop(ordinal, None)
        digest = self._served.pop(ordinal, None)
        if digest is not None:
            self._store.discard(self._adapter, digest)

    def settle(self, clean: bool) -> None:
        held = tuple(row for row in self._held.values() if row[0] not in self._failed)
        self._held.clear()
        self._served.clear()
        self._failed.clear()
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
