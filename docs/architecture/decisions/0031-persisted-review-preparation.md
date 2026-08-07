# ADR-0031: Persist progressive review preparation beside the review boundary

- Status: accepted
- Date: 2026-08-07
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The foreground batch proves bounded evidence generation, but it intentionally
has no durable work state, resume, parallelism, proposal, or review admission.
Preparing a library for ordinary review therefore requires several separate
commands and one slow work delays every work after it. The authenticated review
boundary is the only supported owner surface that can present this progress
without creating another operational interface.

The preparation path reads configured services and media and can contact
configured metadata providers. It must retain the existing report, proposal,
review-admission, currentness, credential, pacing, and redaction boundaries. It
must not decide a proposal or gain apply, target-write, media-write, deletion,
polling, or production-service authority.

## Decision

`review-serve` composes one purpose-built persisted review-preparation pipeline.
An authenticated, CSRF-protected owner request scans one configured library and
coalesces duplicate requests. SQLite stores bounded operational scan and work
state before execution. Each eligible work advances independently through
catalogue discovery, the existing report operation, the existing deterministic
ProposalSet operation, exact-current ReviewCase admission, and read-only review
projection.

Two fixed worker lanes may advance different works concurrently. A restart
returns claimed discovery and work to the queue. Each stage reuses the existing
application service and its currentness checks; provider technical failure
stops that work before proposal creation. Each work ledger binds the exact
source revision, file identity, and report-producer fingerprint and resumes at
the first missing extraction, provider-completeness, proposal, admission, or
projection checkpoint. An unchanged later scan reuses completed state rather
than forcing reprocessing or accumulating an unbounded run history. At most
three discovery generations may converge one owner request while a target is
changing. Absence candidates are staged after a complete successful library
enumeration. They become authoritative and may tombstone that library's current
review lineage only when every work in that scan completes without failure or
stale input; a partial or failed scan discards them.
Discovery is serialized across the two lanes because the catalogue has one
current-generation publication boundary. The first library request for an
absent or configuration-stale catalogue enumerates every configured library to
establish a complete baseline; later library refreshes retain the other
libraries. Queued work validates its exact item identity and source revision in
the latest current generation, so an unrelated library refresh does not make it
stale. Provider pacers serialize shared dispatch starts across both lanes.
Present items that cease to be eligible are staged and retire their current
review lineage only under the same whole-scan success gate as confirmed
absences. Preparation-owned tombstones and retirements permit later
exact-current readmission if the item reappears or becomes eligible again.
The final pipeline publication gate also requires the target to remain a
single-file work; a second file appearing during extraction is stale input and
enters bounded rediscovery without publishing a report or case.
Report publication and its extraction checkpoint commit in the same SQLite
transaction, closing the crash window between an immutable report and durable
stage progress. Live target, media, cover, and artifact currentness checks run
before the short local write transaction; the transaction still rechecks the
expected database pointer. A slow external check therefore cannot hold the
SQLite writer lock against the other lane. Artifact-orphan recovery is
serialized once before the first
pipeline extraction, so concurrent extraction lanes cannot remove one another's
staged cover artifacts. Every configured provider lane, as well as its aggregate
observation, must be complete before proposal creation.
An item-specific service 404 is a typed stale-input signal and requests bounded
rediscovery; credential denial and general service failure remain technical
failures rather than masquerading as target drift.
Target drift detected during ProposalSet currentness validation is likewise a
typed stale-input result rather than a terminal proposal-publication failure.

On Linux, the review server retains the owner-only parent-directory lock for
its lifetime and passes an identity-bound authority to its same-process
pipeline repository connections. Derived connections revalidate that live
authority and the exact database identity without attempting a second blocking
lock on the same directory.

The pipeline is manually requested. It is not a generic queue or scheduler and
does not poll, retry provider failures, rank work, decide fields, construct an
apply plan, or write a target or media file.

## Consequences

### Positive

- Restart and duplicate requests cannot lose or duplicate durable work.
- A slow work does not impose a library-wide stage barrier.
- Existing report, proposal, admission, provider-pacing, and review contracts
  remain the only semantic authorities.
- The owner sees compact progress in the existing Library surface.

### Negative

- The exact-current pre-release database schema changes and older stores need a
  clean rebuild.
- `review-serve` now requires explicit catalogue and artifact-store paths.
- Two long-lived worker threads and operational SQLite rows exist while the
  review server is running.

### Neutral or deferred

- Automatic polling, scheduled reconciliation, retry policy, global budgets,
  policy decisions, and apply remain separate delivery decisions.

## Alternatives considered

### Extend the foreground batch

Rejected because its published contract deliberately excludes persistence,
resume, concurrency, proposal, and review admission.

### Add a generic job framework

Rejected because one closed pipeline is sufficient and a generic scheduler,
broker, or plugin seam would add unused policy and authority.

## Validation

Focused tests cover request coalescing, exact schema validation, complete and
empty discovery, complete first-refresh baselines, item-scoped currentness
across unrelated refreshes, successful-scan absence tombstones and ineligible
retirements, failed-scan discard and reappearance, synchronized shared provider
pacing, Linux derived database authority, typed proposal currentness, unchanged-work
reuse, source-revision invalidation, atomic report/checkpoint rollback and
restart, slow-currentness writer-lock independence, final single-file drift,
stale rediscovery without publication, stage progression, source failure before proposal, third-attempt crash
containment, completed-ledger recovery, and two-lane independence. The
exact-artifact qualifier proves the installed review server, generated OpenAPI,
full public suite, and static architecture boundary.
