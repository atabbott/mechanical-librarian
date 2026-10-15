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
exists only during that construction. Typed reports, cases, proposals and outcomes
are discarded afterward. Direct and writable repository consumers retain the
canonical projection.

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
ISBN refusal candidates bind the exact policy generation/library lookup and the
generation-one profile fallback, including absent lookups. These policy edges
refresh only the ISBN candidate, not ordinary automatic decision lineage.
Affected candidates are admitted before matching search. The canonical projector
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
admitted append-only rows (about 256) inside its existing read, so every row is
re-verified at least once per ⌈rows / slice⌉ refreshes; the sweep covers every
rowid, including zero and negative rowids; construction and every new borrower
generation still perform a full witness; an unchanged revision with a changed
file identity still refuses.

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
cursor lives beside the published snapshot, so a refresh that changes nothing
keeps the published store version. The product holds no route that changes an
append-only row in place: a static test refuses `OR REPLACE`, `REPLACE INTO`,
BLOB I/O and `writable_schema` in the source, and a schema test requires both
refusal triggers on every incrementally witnessed table.

Consequences replace the earlier statement that a changed revision scans all
source bytes: it now scans the mutable tables, the rows above each mark and one
sweep slice; a new borrower generation still performs a full witness against the
previous snapshot. Activity facts feed no write path, so the detection delay for
an in-place change outside SQL's ordinary routes is display-only and bounded by
the sweep. Journal mode, schema, startup admission and the canonical projection are
unchanged.
