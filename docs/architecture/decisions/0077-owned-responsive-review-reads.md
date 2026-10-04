# ADR-0077: Admit foreground owner work and validate compact currentness facts

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
Installed measurements also exposed rollback-journal writer contention and
external work held under the application gate. Owner interaction must take the
next safe local-operation boundary while background work continues fairly.

## Decision

Library, Attention and report detail use the existing application repository and
physical owner. Global local-operation admission makes another browse connection
unnecessary. Preparation and Activity retain their existing connection owners.
Generation shutdown drains an active browse, refuses subsequent reads, then
closes its existing owners. No pool, per-request repository, new database or
journal-mode change is introduced. ADR-0060 continues to govern session isolation.

The generation counts each accepted browse from before its first external capture
through its final recheck. This lifetime is separate from database admission:
shutdown waits without holding the database permit or physical owner. A deadline
failure retains every dependency the unfinished browse can still access; a later
stop can complete the drain. Temporary scheduler suspension remains reversible.

One explicit admission coordinator belongs to the persistent application-database
root. Store controls and overlapping predecessor/successor generations share that
same coordinator; generation teardown never closes it. File mode retains it until
its root closes. The coordinator admits one bounded local database operation at a
time, before its connection lock. Nested operations retain the owning thread's
original classification. Waiting foreground work forms a finite cohort, followed
by the oldest waiting background operation; later arrivals cannot extend that
cohort or steal a reserved turn. Scheduling grants no domain authority.

Activity is the narrow query-only exception to shared root admission (#1142).
Its existing reader owns a local coordinator and verified SQLite `query_only`
mode; description-authority binding installs passive callbacks only. The same
request classification reaches both coordinators without taking a permit. Other
operations retain root fairness, including preparation transactions through commit.
Each generation counts Activity pages and transition reads before their reader
lock, alongside accepted browses. Drain refuses new reads, waits for active and
queued reads, and retains all dependencies on timeout for a later stop. It closes
the reader and its coordinator before the owner, never the shared root coordinator.
Preparation workers drain within the same deadline, but their provider-recovery
store and observation remain open until all accepted Activity and Library reads
finish. A timed-out stop retains these dependencies for the later drain retry.

A retained-copy HTTP diagnostic found an Activity request spending 1.43 seconds
behind Library in root admission. A paired production-admitted actor comparison
measured Activity p95 at 2.310 seconds shared and 0.966 seconds independent.
The latter has little margin and is not a capacity guarantee. A separate trial
with external writers bypassing admission produced unavailable reads and a
3.333-second Library request; it does not establish the supported actor envelope.
This exception adds no schema, journal-mode, or authority change.

Classification is separate from admission. Background worker entry includes
dormant startup, recovery and scheduling; explicit owner operations retain
foreground classification. Artifact-recovery borrowers share root admission for
local writes and reference reads. No permit spans target/provider or
reviewer I/O, device authentication, filesystem recovery, or a whole preparation
stage. Blocking actor and reviewer-command locks are acquired without admission.
Transactions retain their permit through commit or proven rollback/revocation.
Raw preparation output retains its own balanced lease, including existing
publisher-owned transaction entry. Shutdown drains accepted work; it never
interrupts SQLite or abandons an executing mutation.

Target effects retain their durable claimed/applying/reconciling authority while
external work runs outside local admission. Reviewer observations capture one
atomic document/credential snapshot, then recheck its revision after external
work, including responses that make no write. An instance-owned reentrant
reviewer command lock serializes mapping publishers with credential changes;
cross-generation revision checks remain authoritative. A successful credential
change followed by a lost mapping compare-and-set is still a partial failure:
no guessed restoration, repeat deletion, or automatic command retry is allowed.

Settings prepares live policy/replay references outside admission, binds them
inside its admitted commit, and publishes the immutable service policy only after
the durable write succeeds. Rollback restores the previous binding. External
captures derive both references from one published policy and reject drift.

Only Library and Attention watch transport disconnection while their existing
authentication, application read and response validation run in a worker. A
request-scoped active check runs before waiting for admission and again
after acquiring its connection gate. A disconnected queued request performs no snapshot or
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
captures live policy/replay and provider-fetching identities outside admission
and its connection gate. One admitted browse operation installs that immutable
observation for SQL and visible projection, clears it before releasing the gate,
then rechecks external identities outside both gates. On fetching-only drift,
the application discards the whole result and makes at most one complete fresh
capture, admission, read and recheck. The first policy/replay authority pair stays
frozen: authority drift, capture failure or a second fetching drift yields
unavailable. Direct repository readers still refuse drift. Both attempts share
one shutdown lifetime and the original disconnect check; all ownership and
admission are released between attempts. This bounds work, not elapsed time,
and leaves SQLite's existing busy timeout unchanged. No mixed counts escape. This is a transient observation, not
cross-database atomicity. Direct nested reads reuse installed facts only when the
calling thread owns the physical operation scope; another thread captures its own
facts and waits for admission. The new read path does not nest application and recovery
locks. Existing effect admission independently revalidates at call time.

The browser mounts authenticated navigation and truthful pending collection
states before requests finish. Detail, appearance and preparation start
independently. A pending search may retain only an already established public
identity for the same selected work; settled absence, errors and a changed
selection end that retention. Detail documents do not supply otherwise absent
public identity. Superseded requests remain abortable.

Authenticated reads carry an explicit foreground/background scheduling hint;
missing or unrecognized hints are foreground, and mutations are foreground.
Authentication, CSRF and every existing authority predicate are unchanged. The
browser classifies each actual Query fetch through fetch metadata, including
paused/offline work. Required navigation, search, pagination and explicit retries
promote a pending background fetch. Live invalidations, convergence reads,
recurring status sampling are background work. Optional Activity data preloading
is disabled through the router's supported route option, so navigation never
inherits a background loader flight.

One QueryClient-owned completion-debt coordinator serves every background
invalidation family. Events during an active fetch retain it and owe one exact
follow-up; repeated events coalesce. Native completion retires or schedules that
obligation, whereas manual cache publication cannot confirm it. Timer sampling
alone creates no debt while a read is running or its cadence is already satisfied.
Inactive, disabled, static or hidden queries do not start automatic refreshes.
Removal never transfers debt to another cache object. Stream replacement retains
this owner; final Shell teardown cancels its actual query reads and preserves
stale state, without cancelling submitted mutations.

Field, cover and authored-description commands durably record the decision and
project its actual case confirmation under one foreground admission lifetime.
The acknowledgement contains either a validated confirmed case or an explicit
unavailable confirmation. Failed post-commit readback never implies that the
write failed; an idempotent repeat can confirm without adding another decision.
Effect wakes and announcements follow release of this local operation.

The browser serializes same-case command submission through response adoption,
pins the existing exact Query object with a disabled observer, and holds background
invalidations behind it. It adopts only validated confirmed server state into that
same object. Navigation cannot cancel a submitted write or attach its response to
another case; removed queries are never recreated. Unavailable confirmation stays
recorded but unconfirmed until explicit reconciliation. A confirmed superseded
case follows existing stale handling and never advances optimistically.

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
