# ADR-0082: Reuse readable Activity facts with exact source witnesses

- Status: accepted
- Date: 2026-10-07
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Independent Activity readers avoid the application admission queue, but repeated
historical document parsing still holds SQLite snapshots long enough to delay
preparation commits and other owner reads. #1150 requires exact readable search,
counts and paging throughout the supported retained-library envelope.

An immutable row identifier is insufficient evidence of unchanged content.
SQLite replacement and an ordinary commit followed by an out-of-band byte change
can retain that identifier. Different connections' `data_version` counters are
not comparable identities.

## Decision

The existing runtime root owns one bounded, in-memory store of serialized readable
Activity events, search text, exact source witnesses and forward dependencies.
Generation-owned, explicitly configured query-only Activity readers borrow it.
The store is initialized before readiness; bounded exact-document parser reuse
exists only during that construction and each later reprojection (#1218). Typed
cases, proposals and outcomes are discarded afterward; validated reports are kept
by their exact bytes (#1231, below). Direct and
writable repository consumers retain the canonical projection.

A new borrower streams every source witness in its own existing SQLite snapshot
before sharing facts. Each witness contains every scalar column with exact type
encoding, the SQL type of each document column, and SHA-256 of its raw bytes.
An existing borrower reuses facts only when its own revision, database file
identity and published store version agree. Schema drift disables optimization;
unchanged revision with changed file identity refuses. The existing final feasible
file/currentness recheck remains mandatory.

Changed sources identify affected candidates through both old and new bindings,
including excluded candidates, missing joins, deletion and replacement. A temporary
reverse index computes that closure and is discarded before canonical reprojection.
Reprojection restricts each union arm to the requested candidates' whole ranking
partitions, so the canonical ranking sees every row it would rank, in an order
fixed by the explicit tiebreak: rows tied within a partition rank the lowest
witnessed identifier first, and a cover selection before an automatic decision
of the same id (#1218).
ISBN refusal candidates bind the exact policy generation/library lookup and the
generation-one profile fallback, including absent lookups. These policy edges
refresh only the ISBN candidate, not ordinary automatic decision lineage.
Affected candidates are admitted before matching search. Every path orders
events by one total key: newest first, then event id descending, then source kind
and group identity ascending (#1218). The canonical projector
still owns membership, lineage, target identity, readable composition and
query-scoped refusal. Initialization or refresh that cannot admit the complete
optimized representation falls back to the original query-scoped path. This
includes SQLite text-decoding failures in unrelated witness columns; ordinary
SQLite busy failures retain the bounded retry behavior.

The content bound is 64 MiB, including witness keys and values, forward bindings,
excluded candidates, serialized entries, search text and ordering references.
Construction stops on overflow, discards the optimization and serves the complete
canonical result. It never truncates counts or returns a capacity refusal.
Transient construction and refresh memory are measured separately. The shared
lock only snapshots or conditionally publishes immutable references; it never
spans SQL or parsing. Borrower teardown does not erase successor facts.

This changes no schema, startup admission, journal mode, permission, effect or
backup authority. There is no persistent projection, adoption operation, repair,
producer notification protocol or background warmup service.

## Consequences

Initialization does more work before readiness. Normal historical searches avoid
repeated parsing; a changed revision still scans all source bytes and reprojects
only dependent candidates. Cold I/O remains an explicit responsiveness risk.
Overflow or malformed history can use the slower canonical path; that fallback
preserves the original query's result or refusal rather than inventing availability.

## Alternatives considered

Identifier-only reuse with replacement guards cannot detect the combined ordinary
commit and raw-byte corruption case. A persistent Activity projection would add
another durable authority and recovery contract. Global domain-object caches,
connection-counter comparisons and best-effort pager eviction do not provide the
required ownership and corruption evidence.

## Validation

Actual application output must equal canonical search/filter/count/order/page
results. Focused proofs cover exact scalar/document types, fresh borrowers,
combined commit and byte corruption, old/new dependency closure, excluded
membership, bounded overflow, schema drift and generation drain. Exact-installed
startup, retained changed-revision and idle reads, preparation contention, RSS,
and the existing owner browser journey retain their original budgets.

## Amendment: incremental witness with rolling re-verification (#1200)

A full witness held one shared read for the whole history on every changed
revision. In rollback-journal mode the preparation writer cannot commit while
that read is held, so it waits while holding root admission (ADR-0077) and owner
reads queue behind it. The witness is therefore admitted incrementally where the
schema makes that exact, and verified in full by a rolling sweep.

A changed revision re-witnesses every row of `field_effects`,
`description_advice_attempts`, `metadata_policy_transitions`,
`metadata_policy_resolutions`, `current_cover_considerations`, and
`field_effect_no_changes` (no rowid). For every other source table, whose schema
raises on every UPDATE and DELETE, the store records `max(rowid)` and `count(*)`
with its witnesses; a later borrower, in its own snapshot, witnesses only rows
above the mark and admits the table incrementally only when the mark has not
decreased, no row above the mark carries a previously witnessed key, and
`count(*)` equals the recorded count plus those rows; otherwise it re-witnesses
the whole table.

At the next refresh this detects every SQL insert, every deletion whose row is
not replaced at or below the mark (including `INSERT OR REPLACE`'s silent delete
when the replacement takes a new rowid) and rowid renumbering. Changes that
preserve the mark and count (out-of-band file bytes with an ordinary commit,
`REPLACE` naming an existing rowid or an unused rowid at or below the mark,
incremental BLOB I/O, trigger removal through `writable_schema` without a
schema-version change) are detected by the rolling re-verification within its
bound, not the next refresh.

Rolling re-verification: each refresh re-verifies the next bounded slice of
admitted append-only rows (about 256) inside its existing read, and an Activity
read whose revision is unchanged advances the same sweep without republishing
unless it finds a difference. Every row present when the sweep enters its table
is re-verified within ⌈rows / slice⌉ refreshes or reads that advance the sweep,
and a row appended while the sweep is inside its table within about two laps
(#1218), so stale facts cannot stay visible beyond that bound even on an idle
library; the sweep covers every rowid,
including zero and negative rowids; construction and every new borrower
generation still perform a full witness; an unchanged revision with a changed
file identity still refuses. Concurrent reads may repeat a slice but never skip
one, so the bound counts reads that advance the sweep.

A clean slice in an unchanged-revision read moves only the cursor: the published
snapshot stays the same object and nothing is reprojected. A slice that finds a
difference re-witnesses that table and republishes exactly as a changed refresh
does. The cursor moves only by compare-and-swap against the snapshot and cursor
the read started from, so a borrower that loses that race skips its advance and
never publishes over a newer snapshot. A changed refresh still publishes its
snapshot but keeps a cursor that unchanged reads advanced while it ran. Each
table's pass stops at the mark the table had when the cursor entered it, so rows
appended meanwhile wait for the next lap, which is why such a row's bound is about
two laps, and appends cannot hold the sweep in one table.

A row whose witness differs re-witnesses its whole table in that refresh, and so
does any table whose `max(rowid)` is SQLite's largest rowid: SQLite then
allocates new rowids at random below it, so a replacement could keep both mark
and count. A new borrower generation performs a full witness against the
previous snapshot in its own read: it streams every row of every source table,
reprojects only candidates whose witnesses differ, records fresh marks and
counts, and restarts the sweep. It never admits a table from marks recorded by
an earlier generation.

The table classification is derived from the schema's triggers and verified at
construction, so a table that loses its append-only triggers falls back to full
re-witnessing. A table qualifies only when an unconditional `BEFORE UPDATE` and
`BEFORE DELETE` trigger each raise, it has a rowid, and no column shadows it;
the classification is re-derived inside every refresh's own read. Shadow outcomes
bound to a review case appended above its mark are re-bound within the same
refresh, and a review-case table re-witnessed with any difference re-witnesses
every outcome.

Marks and the sweep cursor count toward the 64 MiB bound. Overflow, a decoding
failure and schema drift keep the canonical fallback. The classification reads
`pragma_table_list`, so the store requires SQLite 3.37 or later. The sweep
cursor lives beside the published snapshot, so a refresh or unchanged read that
changes nothing keeps the published store version. The product holds no route
that changes an append-only row in place: a static test refuses `OR REPLACE`,
`REPLACE INTO`, BLOB I/O and `writable_schema` in the source, and a schema test
requires both refusal triggers on every incrementally witnessed table.

Consequences replace the earlier statement that a changed revision scans all
source bytes: it now scans the mutable tables, the rows above each mark and one
sweep slice, and an unchanged-revision read scans one sweep slice; a new
borrower generation still performs a full witness against the previous snapshot.
Activity facts feed no write path, so the detection delay for an in-place change
outside SQL's ordinary routes is display-only and bounded by the sweep. Journal
mode, schema, startup admission and the canonical projection are unchanged.

## Amendment: bounded projection per read transaction (#1225)

Witnessing is cheap per row; projecting a candidate is not. After a large history
change, the first Activity read projected every affected candidate in one read
transaction, about 0.8 ms each, and at 8,000 owner decisions that read held SHARED
past the writer's 5 s busy timeout, so its COMMIT failed with `database is
locked`. One refresh now projects at most 512 affected candidates and records
the rest as pending in the published snapshot. A snapshot with pending candidates
is never served.

Witnessing, marks, the sweep, overflow and fallback are unchanged. A changed
revision still runs them in full in its own read; an unchanged revision with
candidates pending advances the sweep and reuses the witnesses admitted at that
revision, since nothing they witness has changed. The next refresh adds the pending
candidates to the candidates its own changed witnesses affect, so a pending
candidate is always projected from the snapshot of the refresh that projects it.
A fact projected by an earlier step stays exact for as long as its witnesses are
unchanged, which is the rule every reuse already relies on, and a step whose
witnesses differ reprojects through the ordinary closure. Once nothing is pending,
the facts are exactly what one unbounded refresh of that snapshot admits, and only
then is the total order computed and the snapshot served. Candidates are taken
grouped by review case so a step parses each report once. The order changes only
which candidates a step projects first, never what a fact contains. Pending
identities count toward the 64 MiB bound. An explicit candidate set cannot name
an identity that contains NUL, so a construction that witnesses one keeps the one
unbounded projection, which falls back only when a later refresh touches that
candidate, exactly as before.

A read whose refresh leaves candidates pending abandons its read transaction. It
projects the pending candidates in bounded read transactions of their own, between
which a waiting writer commits, and then retries. It stops early when four steps
in a row leave no fewer pending, or on any refusal, and the retry then decides the
outcome exactly as the read would have alone. The third attempt that still finds
candidates pending serves the canonical projection and keeps the store. A read
nested inside an Activity snapshot leaves the catch-up to that snapshot, which
retries as a whole, so the summary, page, counts and readiness of one response
still share one read. Permission readiness reads no Activity facts, so it runs no
catch-up between its fresh consistency check and its snapshot. Initialization
performs the same bounded steps.

Witness values are now encoded with Python's literal form, and document hashes
with their hexadecimal digest. The encoding remains injective and keeps exact
SQLite types (1, 1.0, '1' and b'1' differ, and every float, -0.0 included,
round-trips), and it costs a fraction of the JSON encoding on every witnessed row
of a changed revision. Each witness counts its own bytes once, when it is made.

After a large change, the longest Activity read transaction on a path that
reaches the store is now one step or the witness of the rows appended since the
previous read, whichever is longer: 0.5-0.8 s at 8,000 owner decisions, against
8-10 s before. Two costs are not bounded by this amendment. The appended-row
witness costs about 30 µs per row. The third-attempt canonical fallback costs what
the canonical projection does, about 1.0 s for a page and 2.9 s for a search at
8,000 decisions, and grows with history; in an Activity snapshot it runs inside
that snapshot's read.

The bound trades latency for the writer. The first Activity read after a large
change now takes longer to complete, about 11.5-13 s against 8-9 s at 8,000
decisions, because each step repeats a small fixed cost. In exchange, measured at
8,000 decisions on the paths that reach the store, no Activity read held the
database long enough to fail a commit, and preparation did not fail; the
canonical fallback and a NUL-identity construction, stated above, remain single
unbounded reads. Journal mode, the busy timeout, schema, startup admission, the
ADR-0077 lane and the canonical projection are unchanged.

## Amendment: background catch-up and exact document reuse (#1231)

After a large history change the first Activity request still performed the
whole bounded catch-up itself, 11-20 s at 8,000 owner decisions, and at startup
the store was constructed before readiness. A background projector now runs the
same bounded steps off the request path. This replaces the statements above that
the store is initialized before readiness, that parser reuse exists only during
one construction or reprojection, and that there is no background warmup service.

**Projector.** The history reader that borrows the store starts one daemon thread
when its generation composes it. The thread has no write authority. It uses that
reader's own query-only connection and local admission, classified background,
and never the root admission or the ADR-0077 lane. It runs the exact step a
request runs, under the reader's cache lock, once at startup and then whenever
candidates are pending, the published store version has changed or the reader's
revision has moved. While candidates are pending it waits 20 ms between steps,
until four steps in a row leave no fewer pending, after which it waits a second.
Otherwise it looks for a changed revision once a second, and refreshes once the
revision has been still for one look or five seconds after it first fell behind,
so a steady run of commits costs one refresh every five seconds. A request
waiting on the cache lock takes it between steps and finishes any remainder
itself, exactly as before. A request therefore either finds the store current at
its own snapshot or completes the remaining steps. Pending or stale facts are
never served, and no staleness marker exists.

A busy or refused step is retried at the next look. A fallback to the canonical
path stops the projector. An unexpected error ends the thread, and requests keep
their own catch-up. Every exit writes one value-free structured line naming its
cause and, for an unexpected error, only the exception's type. The work is
derived, held in memory, idempotent (a deterministic refresh published by
compare-and-swap) and rebuilt from durable state, so it is exempt from persisting
background work before it runs. Shutdown and Settings cutover stop it and join
it before the reader's admission closes. The join waits for at most the step in
progress and gives up after 30 s with a warning line. The successor generation's
projector performs the full witness every new borrower performs.

**Readiness window.** The generation's two readers share one lock. Permission
readiness holds it from its fresh consistency check to the end of its snapshot.
The projector takes the reader's cache lock first and then tries the window
without waiting; when readiness holds it, the projector skips that step and
tries again 20 ms later. So no background step runs between readiness's check
and its snapshot, and readiness waits at most for the one step already in
progress, never for a history request the projector is queued behind. The check
is neither memoised nor deferred.

**Writer and cost.** Each background step is one bounded read transaction, so a
writer waits for at most one step, as #1225 bounds a request's steps. The
projector spends CPU whether or not anyone reads Activity: the whole catch-up
after a large change, which a request would otherwise run on its next read, and
at most one refresh every five seconds while commits keep arriving.

**Document reuse.** The per-call report memo is replaced by one
`ActivityDocuments` store per store root. It holds the metadata reports that
schema validation and both Activity readers of every generation validate, keyed
by the SHA-256 of their exact document bytes; the text itself is not kept. A
report is deeply immutable, so it is handed out without copying. A refusal is
never kept, and a tampered or unreadable report refuses exactly as before. The
store is bounded at 48 Mi characters of report text, least recently used first:
about twice the 2,300-work library's reports (27 Mi), so a catch-up parses each
report once. Cases and outcomes keep #1218's reuse within one projection and are
not held across steps. A review case keeps the proposal set it validated, so
neither its proposals nor Activity parse that proposal set again; Activity takes
it only when the proposal row's bytes equal the case's. Browse keeps its own
ADR-0083 cache, so a catch-up cannot evict browse's working set.

**Startup.** Schema validation parsed each cover consideration's report twice:
once to compact it and once for currentness. Activity initialization then parsed
it a third time. Validation now parses each report once, into the root's store,
where the first projection finds it. Activity initialization finishes after the
server binds. Until it does, an Activity request finishes the remainder itself.

Measured at 2,300 works with 8,000 owner decisions on SQLite 3.40.1 (Linux),
interleaved with the predecessor, two to five runs each:

| Measure | Before | After |
|---|---|---|
| First Activity read, about 1 s after the last commit | 13.6-13.8 s | 0.8-1.0 s |
| Same, change lands before any background step | 13.3-13.7 s | 8.2-9.1 s |
| Resident memory after those reads | 310-311 MiB | 376-388 MiB |
| Restart until the server answers | 23.2-23.7 s | 12.0-12.3 s |
| First Activity read after restart, from login | 0.04-0.08 s | 5.5-7.0 s |
| Same, from process start | 23.3-23.8 s | 17.7-19.4 s |
| Resident memory after restart | 331-337 MiB | 400-411 MiB |
| Permission readiness 3 s into that read | 0.14-0.17 s | 0.14-0.17 s |

The report store costs 65-77 MiB of resident memory. Holding all four kinds at
128 Mi characters cost 194 MiB for a slower held read (8.8-9.5 s), and reports
with outcomes 93-96 MiB for no faster one (8.5-9.3 s). In the held read the
refresh's own time is 0.8 s against 0.9 s before, and no full collection runs
during it (two, 0.2 s, before). No commit failed. After a restart the server
answers 11 s sooner, but the first Activity read then finishes the initial
construction itself, so it takes 5-7 s; the first page still arrives 4-6 s
sooner than before.

Witnessing, marks, the sweep, the bound, overflow, the canonical fallback, the
third-attempt rule, journal mode, the busy timeout, schema, startup admission and
the ADR-0077 lane are unchanged.
