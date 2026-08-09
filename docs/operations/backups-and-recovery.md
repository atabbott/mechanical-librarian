# Backups and recovery

The 0.10 runtime performs no target or media writes. Its durable application
state is the append-only report/ProposalSet/ReviewCase/shadow-review database,
current pointers, and report-bound cover artifact store.

For important history, stop all invocations. Within that stopped consistency
boundary, make a SQLite-supported consistent backup of the complete application
database and copy its paired artifact-store directory. The database references
the artifact identities that remain current for its reports, so restore the
matching database and artifact-store pair together. This is not a transactional
cross-filesystem backup guarantee; stopped invocations are the consistency
boundary. Before backup, preserve a complete inventory of retained report IDs
from the operator's records or the IDs returned by report creation; the CLI
cannot enumerate retained reports. Verify the database copy with SQLite
integrity and foreign-key checks before relying on it. Then use
`metadata-report-get` to reopen every ID in that inventory. For every
report-bound cover artifact reference in those canonical reports, use a
read-only verifier that refuses symlinks to require regular artifact-store files
named by both `original_digest` and `preview_digest`, and require the SHA-256
of each file to equal that filename/digest. Do not print or log artifact
contents. Also run `metadata-report-cover-preview` with that report ID,
artifact ID, and preview variant, directing its raw stdout to a non-interactive
sink. Success verifies the persisted report/reference authorization and the
restored preview bytes. The preview command alone does not verify originals,
and there is no bulk pair-verification command. Without a complete inventory,
the pair cannot be called fully verified: restore it only as unverified
historical evidence, or start clean and regenerate reports for supported current
operation; clean regeneration does not preserve history.

The separate catalogue database and immutable batch manifests are regenerable
from current configuration and supported catalogue/batch operations; preserve
them only when an operator needs their historical state. Configuration,
qualification profiles, executable paths, and reviewer/target/provider auth
stores follow the host's encrypted secret-backup policy and must not be
embedded in the application database.

Backups expose bounded target metadata, cited evidence, sanitized shadow
packets, validated advice, and execution facts to anyone who can read them.
Use owner-scoped access and encrypted-at-rest storage, define an operator
retention period for complete backups, and destroy expired complete copies
through the storage system.

There is no 0.10 per-record purge, repair, import, selective restore, migration,
pruning, or purge command. If the application database or paired artifact store
is corrupt, missing, or not exact-current, stop and restore a verified matching
pair or start with a new absent database path and empty artifact store, then
regenerate supported reports. Never edit rows, pointers, or artifact files
directly to force recovery.

Restoring the matching application database and artifact store cannot change
Audiobookshelf or media because this runtime has no such write authority.

## Guarded fake-effect recovery

Stop effect processing before copying the exact-current application database.
Restart reads each persisted state rather than inferring completion: planned
and retryable work crosses every gate again; already claimed work continues
under its granted claim, while applying or ambiguous work performs its
persisted targeted read and enters reconciliation; verified, stale,
superseded, and abandoned work remains terminal while any pending cover-cleanup
obligation is retried. Desired target state verifies, the captured
pre-state may requeue only under current authority and freshness, and a third
value stays stale for forward re-evaluation. Never edit rows or blindly repeat
an ambiguous request.

After opening the exact-current repository, invoke the bounded field-effect
recovery entry point. It claims and dispatches at most 100 closed targeted-read
or forward-reevaluation actions, then retries at most 100 pending cover-cleanup
obligations. Interrupted claimed actions remain restartable; completion is
recorded only after their existing application seam succeeds.

Cover recovery files are transient private material, not a general backup or
Undo system. Preserve their owner-only directory only while corresponding
nonterminal cover work exists. A capture failure blocks that cover alone.
Capture is temp-file/fsync/atomic-rename/directory-fsync. Incomplete temporary
or invalid final captures are rejected and cleaned where safely owned. Every
terminal cover path persists cleanup and restart retries a failed removal.
This fake-only increment cannot alter
Audiobookshelf or embedded media.

## Container restart and private recovery

Stop the container gracefully before copying `/data`. Readiness changes to
draining before background claim admission stops. A current claim may finish
only to its existing durable checkpoint; shutdown does not acknowledge an
unfinished claim. Restart reopens the exact-current database and preserves
ready, claimed, applying, verifying and reconciling facts through their existing
state and action records. Pause and revoked authority still prevent new claims.

Copy the complete stopped `/data` state under owner-only encrypted backup
controls. Cover recovery files are temporary operational material needed only
for non-terminal cover reconciliation; they are not ordinary backup artifacts
or Undo. Restore only an absent or exact-current state. Permission, capacity,
read-only-filesystem or cleanup failure remains a typed degraded condition and
must not be bypassed by editing SQLite or recovery files.
