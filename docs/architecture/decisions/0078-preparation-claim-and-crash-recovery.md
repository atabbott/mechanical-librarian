# ADR-0078: Retain preparation ownership and admit private crash recovery

- Status: accepted
- Date: 2026-09-22
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

A stage can commit its successor before notification, report readback or recovery
outbox delivery fails. Settling the predecessor loses the running successor.
SQLite contention can also prevent the failure settlement itself from committing.
A process killed while using its private inode binding can leave an alias and a
hot DELETE journal. Native SQLite rollback alone tolerates some malformed journals;
it cannot establish the existing side-effect-free admission boundary. #1085 owns
these recovery failures, without changing scheduling or persisted schemas.

## Decision

Each executing lane retains its exact validated claimed action. A scoped
repository binding transfers a prepared receipt immediately after successful
outer SQLite COMMIT and before releasing transaction ownership. A receipt may
explicitly release ownership; absence of a receipt is distinct from that terminal
state. Savepoint release and failed commit never transfer ownership. Report
publication retains its transactional checkpoint callback and public signature.
Readback, parsing, notification and recovery delivery observe the committed owner.

Extraction publication enters the existing claim gate only after serialization
and external currentness, before acquiring its writer. The gate is nonblocking:
contention or draining allows publication of the current checkpoint but no next
claim. It remains held through commit/rollback and transaction release. Direct
advance also attempts the gate without waiting because it may already own a
writer. Neither gate spans parsing, provider work or post-publication reads.

An uncommittable settlement retains the exact action and a closed primary cause
in process memory, optionally with `settlement_code: persistence`. Preparation
latches a stop and closes both initial and successor admission. Other active
lanes finish their safe checkpoint. Refresh, resume and Scan cannot reopen that
runtime. Published books remain browsable; authenticated preparation, operations
and support projections say that an ordinary service restart is required. These
incidents have runtime scope and do not invent a durable terminal work row. A
fresh runtime uses existing durable recovery and the existing attempt limit.
Subscriber wake failure is contained per subscriber without changing producer
success, bounded queues or shutdown.

Only ordinary Linux server roots and the container's complete-store probe opt in
to retained-binding recovery. Borrowers, CLI operations and the qualification
bridge remain reject-only. Under the exclusive parent lock, every candidate alias
must have the exact private name, regular no-follow owner-only file shape and
same database inode. Unknown binding sidecars, multiple journals, foreign inodes,
changed parents and live owners refuse. Clean aliases require full exact-current
admission before removal. Configured-basename journals and WAL remain refused.

For one supported private DELETE journal, a streaming envelope reader rejects
unsupported geometry, counts, padding, page numbers, sparse nonce checksums and
incomplete tails. Nonces can differ across segments, repeated pages are legal,
and complete records in the final unsynced header are checked. This reader never
performs rollback and its checksums are not authenticity evidence.

Before source rollback, create one random owner-private `0700` directory under
the held parent. Copy exactly the captured database and journal lengths into
exclusive no-follow `0600` files, using at most 1 MiB buffers and complete writes.
Verify both copied digests against the captured source digests. Native SQLite
rolls back only this copy first; the existing full schema, integrity, foreign-key
and canonical-content validator must admit it. Recheck source identities and both
source digests before native SQLite rolls back through the original retained
alias. Parent inventory snapshots use fresh directory descriptors verified against
the held parent inode, avoiding stale directory streams while preserving exact
inventory equality. Never replace the database from the copy or manually delete
its journal.

Scratch cleanup accepts only the exact private name, ownership, mode and closed
database/journal inventory. It never recursively deletes unknown objects. A later
startup can remove a verified interrupted copy under the same parent lock.
Cleanup cannot replace a primary admission failure. Setup classification rereads
actual directory contents after the complete-store probe; it does not subtract
presumed recovered names.

## Consequences

Recovery needs temporary space proportional to the captured database and journal.
Short reads/writes, full storage, malformed copies and source drift refuse before
authoritative replay. A corrupt or unsupported store remains refused. This is
transaction rollback for exact-current state, not repair, migration, import or
adoption; ADR-0013 is unchanged and this change adds no persisted schema.

Shutdown keeps identified active owners until their deadline and refuses shared
repository closure while they remain active. Actual container signal and restart
qualification is required in addition to dispatcher and source-level tests. The
reliability correction does not establish completion of a real-library metadata
measurement or change decision, consent, backup or target-write authority.

## Amendment 1: admit what a killed write leaves before its journal sync (2026-11-26)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted; until then the body above stays the accepted record.

SQLite writes a journal segment header with zeroed magic and count, appends
each record as page number, page and checksum in three writes, and writes the
magic only when it syncs the journal, before it writes any database page. A
process killed between those writes leaves a state the reader above refused:
an empty journal, a zeroed first header, or a final record torn at a write
boundary. A store killed during a small write, or in the last segment of a
large one, then would not start. In the large case the database is already partly written, so removing
the journal by hand would corrupt it.

The envelope reader now admits exactly those states and no others:

- An empty journal, and an unsynced header (zeroed magic and count) in any
  segment, the first included. An unsynced segment stays terminal.
- In an unsynced segment, complete records are checked as before, and the
  only tail allowed is one record torn at a write boundary: a page number
  alone, or a page number and page without checksum. A torn page number must
  be in range.

Everything else still refuses, including a non-zero first byte that is not the
magic (SQLite would treat it as hot), a zeroed header with a non-zero count,
any other tail length and a synced segment with an incomplete tail. A write cut
short inside a single system call, or by power loss, can leave other lengths;
those stay refused.

Native SQLite treats a journal as hot only when its first byte is non-zero
(and the database is not empty, which admission already requires). An empty
journal or one with a zeroed first header is never replayed or removed by
SQLite, and its database was never written. After the full
copy-first admission and source validation above, recovery removes such a
journal itself, checking that its inode, size, modification time and digest are
unchanged; opening it as root may change only its owner metadata. Without this,
the journal would outlive its alias and refuse every later start. A hot journal
is still rolled back and removed only by native SQLite, never by hand.
