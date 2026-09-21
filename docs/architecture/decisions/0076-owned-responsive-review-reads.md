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

## Consequences

Collection cost no longer includes full-report validation per SQL candidate.
Visible rows retain their complete validation, and root-open validation still
scales with persisted state. Concurrent owner operations have independent
connection ownership, but SQLite retains its ordinary transaction and locking
limits. The full installed synthetic journey and timing envelope in #1072 are
the acceptance authority; a fast count or health response is insufficient.
