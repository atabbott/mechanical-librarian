# Recoverable synthetic job heartbeat

The `job-heartbeat` command is the supported F02 demonstration of Mechanical
Librarian's durable report-only scheduler. It uses application-owned SQLite
state and a fixed synthetic graph only. It does not construct a library-service
adapter, resolve a credential, open a network connection, read media, or hold
production write authority.

## Run the demonstration

From an installed checkout, choose an isolated synthetic database path:

```text
mechanical-librarian job-heartbeat --database ./synthetic-heartbeat.sqlite
```

Success exits with status `0` and prints a fixed, path-free report:

```text
Job heartbeat passed.
checks=complete-set,lease-recovery,stale-rejected,fan-in,negative-fan-in,retry-bounded,cancellation,dependency-cancellation,compatible-resume,incompatible-replan,transitive-invalidation
authority=synthetic-report-only
source=heartbeat-main state=superseded version=heartbeat-v1 digest=<sha256>
replacement=<opaque-run-id> state=waiting version=heartbeat-v2 digest=<sha256>
retry=failed_terminal attempts=2
cancellation=cancelled job=cancelled
required-cancellation=failed_terminal stages=root:cancelled,left:dependency_cancelled,right:dependency_cancelled,join:dependency_cancelled
negative-fan-in=failed_terminal
```

Repeating the command, including from a new process, verifies and reuses the
durable result. Failure exits with status `1` and prints one fixed message. The
configured path, SQLite errors, stored values, lease owner, and exception text
are never included.

## Frozen graph and policy

The version-one graph is:

```text
root -> left  \
                -> join
root -> right /
```

Every edge is required. Jobs receive at most two execution attempts. The only
retry delay in this synthetic demonstration is a fixed five seconds under an
injected UTC clock; there is no jitter.

A claim carries an opaque worker identifier, an expiry, and a monotonically
increasing fence. Starting, renewing, accepting an outcome, writing a
checkpoint, recording accepted completion, and advancing fan-in all require the
current unexpired owner-and-fence pair. Lease loss permits at-least-once
execution but never a second accepted outcome.

Workflow `waiting` is a nonterminal dependency state. It is distinct from a
job's `retry_wait`, which always has a persisted next-eligible timestamp and a
finite remaining attempt budget. Cancelling a run prevents new claims and
requests cancellation of every nonterminal job. Cancelled or terminally failed
required work gives descendants a typed terminal dependency result rather than
leaving them waiting indefinitely.

## Resume and incompatible replan

A run resumes only when both its workflow-definition version and canonical
graph digest exactly match. The incompatible demonstration changes `left` in
version two. The replacement records `replanned_from`; the original atomically
records `superseded_by`. The original graph, checkpoints, outcomes, attempts,
and audit history remain immutable.

Only `left` and its transitive descendant `join` are ineligible for reuse and
current applicability in the replacement. The unchanged `root` and `right`
checkpoints remain eligible.

## Migration and downgrade

F02 adds one forward-only, additive, transactional migration after the F01
schema. Empty state and genuine F01 state both migrate to the same current
schema. Startup validates the exact contiguous migration checksums and every
managed table and index before work is exposed. A failed migration rolls back
all F02 objects and its migration record; partial, newer, tampered, altered, or
unrecognized state is rejected without repair.

Downgrade is restore-only. Before running a prior binary, stop Mechanical
Librarian and restore a verified synthetic pre-F02 application-state copy. Do
not start an F01 binary against an F02 database. F02 does not add a backup
service or operate on production state.

## Criterion-to-scenario matrix

| Contract | Automated and executable evidence |
| --- | --- |
| Complete eligible set and deduplication | Duplicate enqueue and bounded-claim integration scenarios; `complete-set` |
| Lease expiry and monotonic fencing | Expiry/reclaim test with two owners and stale unchanged-state assertion; `lease-recovery`, `stale-rejected` |
| One accepted outcome and fan-in | Checkpoint/audit row-count assertions and required fork/join execution; `fan-in` |
| Durable retry versus waiting | Restart, backward-clock, five-second eligibility, and two-attempt exhaustion; `retry-bounded` |
| Cancellation | Pre-claim cancellation, restart, no subsequent claim, typed run state; `cancellation` |
| Compatible resume | Exact version-and-digest replay; `compatible-resume` |
| Atomic incompatible replan | Idempotent reciprocal linkage and immutable-source assertions; `incompatible-replan` |
| Transitive-only invalidation | `left`/`join` false and `root`/`right` true assertions; `transitive-invalidation` |
| Migration and corruption rejection | Empty/F01 migration, rollback, checksum, managed-object, newer/partial-state tests |
| Privacy and least authority | Fixed CLI golden tests, architecture boundaries, sentinel redaction, and synthetic-only composition |

## Exact scenario reproduction

Run individual high-risk scenarios with these selectors:

```text
# Competing schedulers and workers; transactional fan-out rollback
uv run --frozen --no-editable python -m unittest \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_competing_schedulers_converge_on_one_job \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_competing_workers_claim_distinct_jobs_with_bounded_capacity \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_failed_fanout_transaction_leaves_no_partial_sibling_set

# Worker, scheduler, and whole-process death
uv run --frozen --no-editable python -m unittest \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_worker_process_death_recovers_under_a_new_fence \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_scheduler_process_death_during_fanout_recovers_complete_set \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_whole_process_death_after_acknowledged_schedule_recovers

# Stale publication, retry, cancellation, and negative fan-in
uv run --frozen --no-editable python -m unittest \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_expiry_reclaims_with_higher_fence_and_stale_publication_is_inert \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_retry_deadline_budget_and_restart_are_durable \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_required_stage_cancellation_propagates_and_retry_wait_cannot_reclaim \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_missing_or_malformed_outcome_cannot_advance_fan_in \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_required_failure_terminates_descendants_instead_of_waiting

# Compatible resume, incompatible replan, crash recovery, and immutable history
uv run --frozen --no-editable python -m unittest \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_version_only_and_graph_only_incompatibility_each_replan \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_process_death_during_replan_leaves_no_half_link \
  tests.integration.test_job_heartbeat.SQLiteJobHeartbeatTests.test_replan_preserves_complete_original_history

# Additive migration and fail-closed schema verification
uv run --frozen --no-editable python -m unittest \
  tests.unit.test_sqlite.SqliteFoundationTests.test_f02_migration_is_additive_from_genuine_f01_state \
  tests.unit.test_sqlite.SqliteFoundationTests.test_failed_migration_rolls_back_all_schema_objects_and_records \
  tests.unit.test_sqlite.SqliteFoundationTests.test_partial_schema_is_rejected_without_modification \
  tests.unit.test_sqlite.SqliteFoundationTests.test_newer_schema_and_unrecognized_objects_fail_closed
```

## Clean-checkout qualification

```text
uv sync --frozen --no-editable --reinstall-package mechanical-librarian
uv run --frozen --no-editable python -m unittest discover -s tests -p 'test_*.py'
uv build
uv run --frozen --no-editable mechanical-librarian state-check --database ./synthetic-state.sqlite
uv run --frozen --no-editable mechanical-librarian job-heartbeat --database ./synthetic-heartbeat.sqlite
uv run --frozen --no-editable mechanical-librarian job-heartbeat --database ./synthetic-heartbeat.sqlite
```

Use disposable paths and remove them after qualification. Never substitute a
contributor's library or application database.
