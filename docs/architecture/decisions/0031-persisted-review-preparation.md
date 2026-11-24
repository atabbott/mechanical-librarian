# ADR-0031: Persist progressive review preparation beside the review boundary

- Status: accepted
- Date: 2026-08-07
- Owners: project maintainers
- Supersedes: none
- Superseded by: ADR-0032 for the polling deferral and complete-baseline catalogue publication boundary

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
Before any staged tombstone or retirement commits, one confirmation discovery
re-enumerates the library; reappearance or renewed eligibility cancels the
staged transition. A failed rediscovery makes the scan terminal and its queued
children are not claimable.
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
The same typed-not-found handling applies between proposal creation and
ReviewCase admission.

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
- Restart recovery retains owner-visible high water only while the exact
  authoritative work cycle carries its matching semantic witness.
- A slow work does not impose a library-wide stage barrier.
- Existing report, proposal, admission, provider-pacing, and review contracts
  remain the only semantic authorities.
- The owner sees compact progress in the existing Library surface.
- A material progress/state change refreshes the Library projection so newly
  admitted, tombstoned, or retired cases are not left stale on screen.

### Negative

- The exact-current pre-release database schema changes and older stores need a
  clean rebuild; recovery witnesses are validated without migration or repair.
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
retirements, confirmation rediscovery and reappearance cancellation,
failed-rediscovery child isolation, failed-scan discard and reappearance,
synchronized shared provider
pacing, Linux derived database authority, typed proposal currentness, unchanged-work
reuse, source-revision invalidation, atomic report/checkpoint rollback and
restart, slow-currentness writer-lock independence, final single-file drift,
stale rediscovery without publication, stage progression, source failure before proposal, third-attempt crash
containment, completed-ledger recovery, and two-lane independence. The
exact-artifact qualifier proves the installed review server, generated OpenAPI,
full public suite, and static architecture boundary.

## Amendment: a kept case expires through the unchanged-work branch (#1523, ADR-0105, proposed)

An unchanged later scan still reuses completed state, with one exception. When
the work's current case was kept only for a recent write (transition reason
`recent_write`) and the scan's time is at least 30 days after the work's
latest verified write, the work goes back to admission instead of
completing. Admission then declines to replay that case and retires it,
because nothing is actionable, unless a pin on it reads changed, which keeps
it until the pin no longer does (ADR-0105, Amendment 1). Reuse no longer projects a kept case again for
a pending description or a cover compared under another policy, since a kept
case takes no part in either. A kept case therefore lasts until the first scan
of the work once its 30 days have passed
([ADR-0105](0105-going-back-after-a-write.md)).

## Amendment: a re-preparation that identifies nothing retires the case (#1953)

When a requested refresh, a changed file or a recovery reset completes a work
as evidence only, preparation retires the work's case in the same transaction,
as an absence retirement does: a `retired` transition by
`review-preparation-v1` with reason `unidentified`, from the case, keeping the
lineage's proposal set and report. Every write still waiting on a case closes
with it ([ADR-0039](0039-continuous-guarded-application.md), #1658). A
scheduled search that identifies nothing still keeps the case
([ADR-0102](0102-search-every-finished-book-again.md)). Because preparation
retired it, a later preparation that identifies the book again admits its case
as a new episode rather than replaying the retirement.

## Amendment: an item Audiobookshelf cannot give is present, not absent (#2013)

Status: proposed (#2013)

An item the library lists whose detail cannot be used (missing or invalid in
Audiobookshelf, unreadable, over the response limit, or changed between the
list and the detail) no longer fails discovery for the whole library. The
catalogue keeps it as an unread entry with a closed reason, and discovery
enrols it as a work that could not finish at extraction, named from what could
be read. Unread items are neither absent nor ineligible: they stage no
tombstone or retirement, so being unreadable never retires a case. A per-item
404 is the typed stale-input signal this ADR already names; such an item is
enrolled `stale` and requests the bounded rediscovery. An item with no media
file is listed as could not finish but does not end its scan with failures,
so the library keeps polling and a file added in Audiobookshelf is picked up.
Conditions of the library itself still fail its discovery, and the scan now
keeps that failure's closed reason until the scan moves on.
