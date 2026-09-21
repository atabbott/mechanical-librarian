# ADR-0076: Own responsive review reads and validate compact currentness facts

- Status: accepted
- Date: 2026-09-21
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

At approximately 2,300 works, Library and Attention repeatedly parsed complete
cover reports inside collection SQL. Sharing the ordinary application gate also
made those reads delay unrelated owner operations. #1072 requires useful pending
navigation and bounded collection work without weakening currentness or effects.

## Decision

Library and Attention share one persistent, separately locked connection borrowed
from the existing root database authority. Ordinary detail, decisions, Policy,
Settings and preparation retain their existing owner; Activity retains its own.
The browse borrower closes before root authority during shutdown or failed
activation. No pool, per-request repository, new database or journal-mode change
is introduced. ADR-0060 continues to govern session isolation.

Only Library and Attention watch transport disconnection while their existing
authentication, application read and response validation run in a worker. A
request-scoped active check runs before waiting for the browse gate and again
after acquiring it. A disconnected queued request performs no snapshot or
projection. Once executing, a read finishes normally under exclusive ownership
and currentness recheck; neither disconnect nor handler cancellation interrupts
SQLite or releases its connection early. The transport awaits worker and watcher
cleanup. Ordinary application callers need no cancellation callback.

One immutable `currentness_document` column accompanies each canonical cover
consideration. Full producer validation derives its target, report identity,
fetching state and saved replay reference; invalid or nonadmissible input derives
canonical `null`. The consideration, digest and facts are written atomically.
Root startup rederives every stored projection and rejects disagreement. Existing
append-only triggers protect the whole row. Under ADR-0013 this schema change
requires a **clean rebuild**; no predecessor adoption or migration is provided.

Collection SQL uses these bounded facts while retaining case, report, policy,
selection and owner-readiness joins. Full visible-row validation occurs after a
short transaction containing membership, counts and row facts. Each operation
captures live policy/replay and provider-fetching identities before that SQL
snapshot, and rechecks them after projection. Drift yields unavailable; it does
not retry silently or return mixed counts. This is a transient observation, not
cross-database atomicity. The new read path does not nest application and recovery
locks. Existing effect admission independently revalidates at call time.

The browser mounts authenticated navigation and truthful pending collection
states before requests finish. Detail, appearance and preparation start
independently. A pending search may retain only an already established public
identity for the same selected work; settled absence, errors and a changed
selection end that retention. Detail documents do not supply otherwise absent
public identity. Superseded requests remain abortable.

Live Library and Attention invalidations preserve each active collection request
and attach at most one completion debt to its concrete cached query. A response
that may predate an announcement remains stale and receives one exact follow-up
through the existing coalescer. Repeated announcements do not cancel and restart
that read. Search/cursor changes still cancel their old requests; debt never
transfers to another key or replacement cache object. Hidden or inactive queries
do not start background refreshes, and failures retain the existing retry path.
Teardown cancels outstanding debt reads before preserving their stale state.
Frames remain invalidation notices, never authoritative cache data.

Decision confirmation owns the existing exact-case query until its native
post-acknowledgement read settles. Same-case confirmations serialize; other
cases and history/evidence reads remain independent. A temporary disabled
observer keeps route departure from cancelling that read. Case invalidations
coalesce behind it, then refresh once if the bridge is visible and active; a
failed confirmation remains stale/error without a retry loop. Removed queries
are never recreated, and cancellation rollback cannot count as fresh authority.

## Consequences

Collection cost no longer includes full-report validation per SQL candidate.
Visible rows retain their complete validation, and root-open validation still
scales with persisted state. Concurrent owner operations have independent
connection ownership, but SQLite retains its ordinary transaction and locking
limits. The full installed synthetic journey and timing envelope in #1072 are
the acceptance authority; a fast count or health response is insufficient.

Preparation output can already hold the SQLite writer lock when it completes a
stage. It tries the existing claim gate without waiting: when another initial
claim owns that gate, the stage still publishes atomically and leaves its
successor queued for an ordinary worker claim. This avoids waiting for a claimant
that is itself waiting for the writer. The gate still serializes initial claims
and draining; lease, recovery, worker-count and publication authority are unchanged.
