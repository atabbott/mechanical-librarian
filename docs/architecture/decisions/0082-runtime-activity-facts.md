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
