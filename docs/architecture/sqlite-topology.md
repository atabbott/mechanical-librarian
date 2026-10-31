# SQLite topology

Mechanical Librarian keeps its state in four SQLite databases under the `/data`
volume. Each database has one owner, its own connections and its own exact
schema. The application database holds all review state; the other three hold
state that must not queue behind it or that can be rebuilt.

| Database | Path | Holds | If it is lost |
| --- | --- | --- | --- |
| Application | `/data/application.sqlite3` | Reports, proposals, review cases, decisions, policy, cover and description work, guarded field effects, preparation work, controls, reviewer access and (in store mode) owner settings and their secrets | Restore a verified database and artifact-store pair, or start clean and regenerate reports |
| Session | `/data/private/review-sessions.sqlite` | Owner session token digests and the failed-login throttle | The owner signs in again |
| Provider recovery | `/data/private/provider-recovery.sqlite` | Provider retry health, recovery jobs and their append-only events | A fresh empty store is created on start; pending recovery jobs, retry backoff and recovery history are lost |
| Catalogue | `/data/catalogue.sqlite3` | The current bounded Audiobookshelf catalogue generation | A refresh rebuilds it |

Cover originals and previews live in the artifact store at `/data/artifacts`,
not in SQLite. The application database names them by digest, so the two are
backed up and restored as a pair; see
[Backups and recovery](../operations/backups-and-recovery.md).

The executable schema is
[`schema.py`](../../src/mechanical_librarian/adapters/persistence/schema.py)
for the application database and the opening module of each other store. This
page maps responsibilities; it does not restate column definitions. Why the
tables stay separate is recorded in
[ADR-0057](decisions/0057-sustain-explicit-sqlite-aggregate-topology.md).

## Rules every database follows

- **Absent or exact.** A store accepts only a missing file, which it creates
  with the exact schema, or its exact current schema. An earlier schema, an
  extra object, altered SQL or a failed integrity check refuses before any
  mutation. Nothing is migrated, imported or repaired
  ([ADR-0013](decisions/0013-pre-release-clean-schema.md)). What open-time
  admission checks is stated once in
  [Schema admission](security.md#schema-admission); recovery from a refusal
  is a clean rebuild, described in [Upgrades](../operations/upgrades.md).
- **Owner-only files.** The application, session and catalogue databases are
  created `0600` and must be owned by the runtime user. The application and
  catalogue databases refuse a parent directory other users can access. The
  session and provider-recovery databases live in `/data/private`, which must
  be a private directory.
- **Serialised writes.** Every connection has its own serialisation lock, and
  every write starts with `BEGIN IMMEDIATE`, so a writer takes SQLite's write
  lock up front instead of failing part-way through. The application database
  has one root connection plus dependent connections that share its binding
  (see [Opening and file binding](#opening-and-file-binding)); each other
  store has a single connection.
- **No external I/O inside a write.** Network, device and filesystem work
  happens before or after a transaction, never inside one. A slow provider or
  Audiobookshelf call never holds a write lock.
- **Never edit rows.** There is no repair, purge, selective restore or
  pruning command. Editing rows, pointers or schema by hand is refused at the
  next open or produces state the runtime will not trust.

## Application database

### Connection settings

The root connection and its dependent writers run with:

- `foreign_keys=ON`
- `journal_mode=DELETE` (a rollback journal; WAL is not used)
- `synchronous=FULL`
- `busy_timeout=5000` (milliseconds)

The Activity reader and the shadow preview use their own settings, described
below.

Two Python SQL functions are registered on every connection: `ml_casefold`,
which folds text for search and browse keys, and
`ml_cover_consideration_current`, which decides cover currentness inside SQL.
The report browse-fact trigger calls `ml_casefold`, so a connection that has
not registered it cannot insert a report.

### Opening and file binding

On Linux the root opener binds the database to one inode before SQLite sees
it:

1. It opens the file with `O_NOFOLLOW`, checks the header records
   rollback-journal mode, and walks the parent chain without following links.
2. It takes an exclusive, non-blocking `flock` on the parent directory, so a
   second root over the same directory refuses.
3. It opens SQLite through a private hard-link alias,
   `.metadata-report-binding-<32 hex>.sqlite`, so renaming or replacing the
   configured path cannot redirect the connection.
4. It refuses a `-journal`, `-wal` or `-shm` sidecar, except during
   [crash recovery](#crash-recovery).
5. It validates the full schema read-only, then marks the binding admitted.

Before each use the binding rechecks that the configured name still resolves
to the same inode and that the parent is still owner-only. Dependent
connections (Activity readers, preparation workers, shadow owners) borrow the
root's binding and its schema admission instead of repeating the database-wide
check. Off Linux there is no alias or parent lock; the root records the inode
it admitted, and dependents check against that.

Shadow-decision preview opens the database read-only and immutable, with
`query_only=ON`, and refuses if the file's identity changes during the read.

### What it holds

Tables fall into six kinds. The kind decides the table's write rule.

| Kind | Write rule | Tables |
| --- | --- | --- |
| Canonical immutable documents | Insert only; triggers refuse `UPDATE` and `DELETE` | `metadata_reports`, `metadata_report_cover_references`, `proposal_sets`, `review_cases`, `metadata_policy_drafts`, `description_advice_candidates` |
| Append-only audit and decisions | Insert only; triggers refuse `UPDATE` and `DELETE` | `review_case_transitions`, `human_decisions`, `human_decision_events`, `automatic_decisions`, `shadow_decision_outcomes`, `cover_considerations`, `cover_consideration_events`, `cover_selection_authorities`, `description_decisions`, `field_effect_events`, `field_effect_no_changes` |
| Current pointers | Updated in the transaction that publishes or admits their target | `current_metadata_reports`, `current_metadata_report_lineages`, `current_review_case_lineages`, `current_review_packages`, `current_cover_considerations`, `current_cover_selection_authorities`, `current_description_advice`, `current_description_decisions`, `review_case_episodes` |
| Derived facts | Written only by triggers, in the same statement or transaction as their source | `review_case_field_facts`, `shadow_outcome_field_facts`, `metadata_report_browse_facts`, `metadata_report_browse_authors`, `proposal_set_browse_facts`, `review_attention_facts` |
| Operational and recovery state | Mutable state machines with their own restart rules | `review_preparation_scans`, `review_preparation_work`, `review_preparation_absences`, `review_preparation_connection_conditions`, `review_preparation_presentation`, `description_preparation_pending`, `description_advice_attempts`, `provider_recovery_outbox`, `metadata_policy_transitions`, `metadata_policy_transition_items`, `metadata_policy_resolutions`, `field_effects`, `field_effect_actions`, `field_effect_cleanups` |
| Controls, settings and credentials | Singletons or scoped rows changed only by their own command | `metadata_policy_state`, `review_preparation_control`, `automatic_safety_state`, `field_effect_control`, `field_effect_suspensions`, `field_effect_authorities`, `field_effect_consents`, `field_effect_manual_holds`, `browse_fact_identity`, `owner_settings`, `owner_setting_secrets`, `reviewer_access`, `reviewer_access_secrets` |

There is also one view, `review_attention_refresh`, used only to drive
attention-fact maintenance (see [Derived facts](#derived-facts)).

What the main aggregates mean, where the table names alone do not say:

- **Reports and pointers.** A `metadata_reports` row is the evidence authority
  for one observation of one item. `current_metadata_reports` includes the
  file identity, so it detects replacement of the same item's file;
  `current_metadata_report_lineages` omits it and gives one current report per
  target. `metadata_report_cover_references` binds each report's cover sources
  to artifact digests.
- **Proposals, cases and lineage.** A `proposal_sets` row is the evaluated
  consequence of one report under one policy; a `review_cases` row is the
  owner-actionable packet. `current_review_case_lineages` names the live case
  and its latest transition and guards optimistic currentness checks.
  `current_review_packages` is the owner-visible projection for Library and
  Attention, including report-only outcomes that have no case. A case admitted
  again after its work left it gets a `review_case_episodes` row: the owner
  decisions and writes before it are history to that case.
- **Decisions.** `human_decisions` holds owner decisions (accept, keep
  current, reject, an owner-entered value, or withdrawal) with their
  idempotency key and target fingerprint. `automatic_decisions` holds one
  policy decision per case and destination. `shadow_decision_outcomes` is
  reviewer evidence, never authority.
- **Covers and descriptions.** Each has candidate packets, events or attempts,
  an owner decision and `current_*` pointers per live case. A current cover
  selection pointer is checked by trigger against the authority row it names.
- **Policy.** `metadata_policy_state` selects the active policy and
  generation. `metadata_policy_resolutions` binds a resolved policy to a
  library for one generation; `metadata_policy_transitions` and their items
  run one generation cutover and its per-item settlement.
- **Preparation.** `review_preparation_work` holds a restartable row per item.
  `review_preparation_absences` stages missing items so only a fully
  successful scan can tombstone a lineage.
  `review_preparation_connection_conditions` pauses only the work of a failed
  connection.
- **Guarded field effects.** `field_effects` holds each write plan and its
  retry state; `field_effect_actions` is its restartable action queue and
  `field_effect_cleanups` its cleanup boundary. `field_effect_no_changes`
  records an automatic candidate equal to the current value as a terminal
  fact, so it is never re-planned. Authorities, consents, suspensions, manual
  holds and the pause control each gate claims independently; a manual hold
  follows the work across cases (see [Transactions](#transactions)). See
  [Backups and recovery](../operations/backups-and-recovery.md#guarded-field-effect-recovery)
  for what restart does with in-flight effects.
- **Provider recovery hand-off.** `provider_recovery_outbox` makes each
  package cutover durable before it is handed to the provider-recovery
  database; it keeps replay and acknowledgement state.
- **Settings and credentials.** `owner_settings` is a revisioned singleton
  that also holds incomplete first-run setup state.
  `owner_setting_secrets` and `reviewer_access_secrets` hold credentials;
  reads report only whether a value is set. In store mode this makes the
  application database, and every backup of it, secret-bearing.

Foreign keys tie every pointer, fact, event and work row to the canonical rows
it names. `PRAGMA foreign_key_list(<table>)` on a fresh database shows the
exact edges.

### Immutability

Triggers refuse `UPDATE` and `DELETE` on every insert-only table listed
above. Supersession never deletes: a newer report, case or consideration is a
new row, and the pointer moves. No product code writes with `REPLACE`, which
would bypass delete triggers.

### Derived facts

Derived fact tables let Library, Attention and search filter in SQL without
parsing a JSON document per candidate. They are a relational projection of
canonical documents, not a cache and not an alternate authority.

- `review_cases_project_field_facts` and `shadow_outcomes_project_field_facts`
  project per-destination case and outcome facts after insert.
- `metadata_reports_project_browse_facts` projects one browse-fact row per
  report (folded title, format, provider-limited flag and provider match
  state) and one row per distinct folded author.
- `proposal_sets_project_browse_facts` records each proposal's resolved
  policy id.
- `review_attention_facts` holds one row per current package, report and field
  whose stored inputs ask for the owner. An after-write trigger on each input
  table inserts into the `review_attention_refresh` view, whose
  `INSTEAD OF INSERT` trigger re-derives exactly the affected rows in the
  writer's statement. A cover row keeps what each read needs to make the live
  currentness check
  ([ADR-0084](decisions/0084-persisted-attention-facts.md)).

`browse_fact_identity` records the Unicode version the facts were folded
under. Open refuses any other version, so a Python upgrade that changes
Unicode data needs a clean rebuild.

Open compares every derived relation with its canonical documents. A partial
write, manual edit or interrupted projection refuses the database rather than
showing stale owner-visible state.

### Compact cover considerations

A stored consideration does not repeat its report. It carries
`report_document_digest`, bound to the immutable report named by `report_id`.
Reads join that report in the same statement and rebuild the byte-identical
document that selection, replay and effect hashes bind. A missing or
mismatched report fails closed.

A report document is at most 2 MiB and an expanded consideration at most
12 MiB. `currentness_document` (at most 4 KiB) is validated at publication
and at open, so currentness checks do not parse the whole packet.

### Indexes

Primary and unique keys provide most indexes, and open validates those
automatic indexes too. Every named index serves a current query: cover-artifact
lookups, preparation claims and the progress view, case navigation, decision
and policy lookups, and the live field-effect queues.
`field_effects_companion_decision` is a partial expression index using
`json_extract`, so a `field_effects` document that is not well-formed JSON is
refused at write time.

## Transactions

All application persistence goes through one re-entrant transaction scope
([`transactions.py`](../../src/mechanical_librarian/adapters/persistence/transactions.py)):

- An outermost write scope issues `BEGIN IMMEDIATE`. A scope entered inside an
  open transaction uses a `SAVEPOINT`, so a nested call that fails undoes only
  its own work.
- A consistent read uses a deferred `BEGIN`, so one response reads one
  snapshot. Library, Attention, Activity, policy preview and shadow preview
  read this way.
- If rollback or release cannot be proven, the scope revokes the connection
  before releasing its lock, and keeps the lock if revocation also cannot be
  proven.

These units are atomic:

- **Report publication.** The report, its cover references, both current
  pointers, its browse facts and any package update commit together, after
  rechecking target and media currentness inside the transaction.
- **Admission.** Proposal and case creation recheck the report and pointers
  inside SQLite. The case, its transition, lineage pointer, package, derived
  facts and the work's active manual holds commit together. A hold follows the
  work to each successor case; a released hold, or one with a return pending,
  is not carried
  ([ADR-0085](decisions/0085-owner-entered-values-withdrawal-and-reselection.md)).
- **Package cutover.** Lineage, package and `provider_recovery_outbox` enqueue
  commit together; the outbox is drained after commit.
- **Decisions.** A human decision and its event commit together under one
  idempotency key. Each automatic decision, cover step, description step and
  field-effect state change is its own command transaction.
- **Policy activation.** Draft, state, resolutions, transition and items
  commit together; item settlement is restartable.
- **Attention facts.** Recomputed inside the statement that changed their
  inputs.

## Concurrency

### Admission

One local-operation admission coordinator serves each application-database
root ([ADR-0077](decisions/0077-owned-responsive-review-reads.md)). Every
application connection except the Activity reader acquires admission before
its connection lock. Foreground requests run in finite cohorts; a cohort yields
to the oldest background waiter before the next starts. Worker startup,
recovery and scheduling are classified as background. Classification itself
holds no permit, so it can span external work; no network or device operation
holds admission.

Generation shutdown refuses new reads, waits for accepted and queued reads,
then closes the generation's own connections before the root. Only final root
shutdown closes the shared coordinator. A drain timeout keeps the reader and
its dependencies open for a later stop rather than closing them under a read.

### Shared connections

A connection shared across threads runs every statement under that
connection's serialization lock. This is required, not defensive: CPython
holds SQLite's connection mutex while it retakes the GIL to call a registered
SQL function, while binding a parameter takes that mutex with the GIL held.
Two threads using one connection at once can therefore deadlock the process.
Any statement run on a shared connection without its lock reintroduces that
hang. After changing how a shared connection is used, run:

```text
.venv/bin/python -m pytest tests/integration/test_shared_connection_serialization.py
```

### Activity reads

Activity uses its own query-only connection (`query_only=ON` is verified),
with a 150 ms busy timeout and its own admission coordinator, so a Library
operation cannot queue an Activity read behind itself.

Activity reads a root-owned serialized fact store of at most 64 MiB
([ADR-0082](decisions/0082-runtime-activity-facts.md)). It never grants
authority and never feeds a write path. Its rules:

- A new reader generation witnesses every source row once. After that, the
  store is reused only while the database revision, file identity and store
  version are unchanged; a change refreshes only the affected candidates, as
  follows.
- Mutable source tables are re-witnessed in full. Insert-only tables (those
  whose triggers refuse update and delete) record `max(rowid)` and `count(*)`;
  a refresh streams only rows above that mark. A lower mark, a reused key or an
  unexplained count re-witnesses the whole table.
- Every refresh, and every Activity read whose revision is unchanged,
  re-verifies the next 256 insert-only rows in a rolling sweep, so an in-place
  edit that keeps mark and count is still found.
- One refresh projects at most 512 affected candidates. A snapshot with
  pending candidates is never served. A read that leaves candidates pending
  ends its read transaction, projects them in further bounded read
  transactions, and retries; the third attempt serves the canonical query
  instead. A waiting writer can commit between steps.
- A background projector runs the same bounded steps between requests. Each
  step is one read transaction under the reader's own admission, never the
  root's, and runs only when no request holds the reader. It waits for
  nothing, so a request, a stop or a writer waits at most for the step in
  progress.
- Overflow or schema drift falls back to the canonical query path.

The trigger classification it relies on is read from the live schema with
`pragma_table_list`, which needs SQLite 3.37 or later.

Activity also keeps its last prepared counts and permission-readiness example
on that connection and serves them only while the revision and file identity
are unchanged. A refusal is never kept; it is read again every time.

### Library and Attention

Library and Attention read membership, counts and row facts in one short
deferred transaction, so a page's rows and totals come from one filtered
relation. Visible rows are projected after that transaction ends, still
inside admission.

Browse documents and cover currentness are reused only for the exact bytes
they were validated from; the limits are in
[Read caches](security.md#read-caches).

### Other stores

The session, provider-recovery and catalogue databases each have their own
connection and lock and never wait on the application database. Their
admission is independent; a recovery call that enters the application
repository uses the application coordinator.

## Durability and recovery

### Journal and sync

Application and session databases use a rollback journal (`DELETE` mode) with
`synchronous=FULL`. A committed transaction is on disk before commit returns.
The catalogue uses WAL. No other store uses WAL, and the application opener
refuses `-wal` and `-shm` files.

### Crash recovery

A crash mid-write leaves a rollback journal beside the private alias. The
container's root open runs explicit crash recovery under the exclusive parent
lock:

1. It inventories the directory. Any unrecognised alias, more than one
   journal, a journal whose alias is missing, or a file that is not a `0600`
   regular file owned by the runtime refuses.
2. It validates the journal's segment geometry and checksums without letting
   SQLite touch the source.
3. It copies the database and journal into an owner-only scratch directory,
   verifies the copies by digest, lets SQLite roll the copy back, and runs full
   read-only schema validation on the result.
4. Only then does it let SQLite roll back the real file through the retained
   alias, and removes the scratch directory and stale aliases.

Scratch copies left by an interrupted recovery are cleaned up first.
Anything unknown or malformed refuses with
"database recovery state is unsupported"; it is never repaired.

The session database admits its own rollback journal on open, so an
interrupted sign-in rolls back on the next start.

### Restart

Restart reopens each store under the same exact-schema rules. Preparation
claims are requeued, with a third crashed attempt becoming a bounded failure
([ADR-0078](decisions/0078-preparation-claim-and-crash-recovery.md)). Field
effects stranded before a request was sent return to `retryable`. Undelivered
outbox rows are drained again. Policy transition items resume settlement.

### Backups

Back up the application database and the artifact store together while the
container is stopped; see
[Backups and recovery](../operations/backups-and-recovery.md).

## Session database

`/data/private/review-sessions.sqlite` holds two tables
([ADR-0060](decisions/0060-dedicated-review-session-database.md)):

- `review_sessions`: session and CSRF token digests, expiry and revocation.
  No password or token is stored.
- `review_login_failures`: failed-login timestamps keyed only by a
  verifier-bound HMAC of the client, never an address.

Every session operation touches both tables, so they form one aggregate in
one database. It is separate from the application database so owner sign-in
never waits behind a scan's write transaction. Settings are `DELETE` journal,
`synchronous=FULL` and a 5,000 ms busy timeout. A corrupt or foreign file
stops startup without being changed; removing it signs every browser out and
forgets at most five minutes of throttle history (see
[Backups and recovery](../operations/backups-and-recovery.md)).

## Provider-recovery database

`/data/private/provider-recovery.sqlite` holds four `STRICT` tables:

- `provider_recovery_members`: each connection, adapter and scope that can be
  recovered.
- `provider_recovery_health`: per-scope retry state (`waiting`, `dormant`,
  `checking`, `fanout`, `recovered`, `exhausted`), attempt count and claim.
- `provider_recovery_jobs`: per-item recovery jobs with dispatch rank and
  lease.
- `provider_recovery_events`: append-only history; triggers refuse update and
  delete.

Foreign keys are on and writes use `BEGIN IMMEDIATE` under the store's lock. A
claim's rollback snapshot is kept only in process; durable history is the
source of truth after restart. Maintainer commands that only read it open it
read-only. How recovery runs is in
[Provider recovery](../operations/provider-recovery.md).

## Catalogue database

`/data/catalogue.sqlite3` holds the catalogue schema
([current versions](../reference/compatibility.md#current-versions)): a schema
marker, numbered generations with at most one current, and up to 10,000
entries per generation. A refresh takes the write lock with a zero busy
timeout, so a second concurrent refresh returns busy instead of waiting. A new
generation is published in one transaction, and the previous one stays
readable until then. The catalogue is regenerable and need not be backed up.
See [Current book catalogue](../operations/catalogue.md).

## Changing the topology

Every apparent duplication in the application database has a current consumer
and a distinct authority, key, lifecycle or restart rule
([ADR-0057](decisions/0057-sustain-explicit-sqlite-aggregate-topology.md)). A
proposal to merge or remove tables must name the tables removed, the single
transaction that keeps results equal, and how rollback, restart, stale
currentness, idempotency, audit retention and exact-schema rejection still
hold. Any schema change is a clean rebuild under ADR-0013.

To see which tables a bounded set of public tests writes, run:

```text
.venv/bin/python scripts/audit_sqlite_row_deltas.py
```

It reports committed row-count deltas per table against a fresh schema. It
cannot see update-only changes or rolled-back work.
