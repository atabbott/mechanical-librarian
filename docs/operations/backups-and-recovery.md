# Backups and recovery

The installed runtime can apply field-local target updates only after explicit
library consent. Its durable application state includes the append-only report,
proposal, review, policy, field-effect, consent, action and audit records,
current pointers, and report-bound cover artifact store.

For important history, stop all invocations. Within that stopped consistency
boundary, make a SQLite-supported consistent backup of the complete application
database and copy its paired artifact-store directory. The database references
the artifact identities that remain current for its reports, so restore the
matching database and artifact-store pair together. This is not a transactional
cross-filesystem backup guarantee; stopped invocations are the consistency
boundary. Before backup, preserve a complete inventory of retained report IDs
from the operator's records or authenticated review history; the runtime does
not enumerate retained reports. Verify the database copy with SQLite integrity
and foreign-key checks before relying on it. Then use the bounded private
backup verifier to reopen every ID in that inventory. For every
report-bound cover artifact reference in those canonical reports, use a
read-only verifier that refuses symlinks to require regular artifact-store files
named by both `original_digest` and `preview_digest`, and require the SHA-256
of each file to equal that filename/digest. Do not print or log artifact
contents. Verify each report-bound preview through the same private recovery
adapter. Success verifies the persisted report/reference authorization and the
restored preview bytes. Preview verification alone does not verify originals,
and there is no bulk pair-verification operation. Without a complete inventory,
the pair cannot be called fully verified: restore it only as unverified
historical evidence, or start clean and regenerate reports for supported current
operation; clean regeneration does not preserve history.

The separate catalogue database and immutable batch manifests are regenerable
from current configuration and source-only contributor operations; preserve
them only when an operator needs their historical state. Runtime configuration
and target/provider authentication stores follow the host's encrypted
secret-backup policy and must not be embedded in the application database.
Contained-reviewer profiles, executable paths, and authentication belong only
to explicitly retained private workbench state, never runtime backups.

Backups expose bounded target metadata, cited evidence, sanitized deterministic
packets, decisions/outcomes, and execution facts to anyone who can read them.
Use owner-scoped access and encrypted-at-rest storage, define an operator
retention period for complete backups, and destroy expired complete copies
through the storage system.

There is no current per-record purge, repair, import, selective restore,
migration, pruning, or purge command. If the application database or paired
artifact store is corrupt, missing, or not exact-current, stop and restore a
verified matching pair or start with a new absent database path and empty
artifact store, then
regenerate supported reports. Never edit rows, pointers, or artifact files
directly to force recovery.

Restoring the matching application database and artifact store does not itself
change Audiobookshelf. After restart, consent, pause, authority and exact-current
gates are reloaded; only previously claimed work may continue without a new
claim, and ambiguous work is read back before reconciliation.

## Guarded field-effect recovery

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
The installed product can alter consented Audiobookshelf metadata and covers
through supported APIs. It cannot alter embedded media. Qualification uses only
disposable owned libraries and never a production service.

## Supported Audiobookshelf canary

The canary uses API keys and supported item/media/cover endpoints only. Before
one destination-minimal write it rereads the exact planned field and item
version; it then rereads the destination. Audiobookshelf provides no CAS, so a
same-field update can be overwritten between those reads and the service
accepting the request. Never retry an ambiguous write manually from the same
plan: use the persisted field-local reconciliation/audit outcome instead.
Expired credentials, inactive accounts, or loss of basic library/item access
persist a connection suspension. A permission failure, incompatible field
shape, or unexpected write response persists a destination suspension instead,
leaving unrelated destinations eligible. Both remain fail-closed across restart;
a fresh successful capability observation clears only the matching obsolete
suspension, never a different destination or an unobserved connection failure.

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
