# Delivery Slices

Authorities: the GitHub Project and its milestones for what is planned, ready,
in progress and closed; the slice tracker issue for a slice's outcome and
ticket list; and
[functional slice delivery](../development/slice-delivery.md) for the branch,
issue and review mechanics. This document is the ordered plan and its planning
rules. It records open checkpoints only: the exit ledgers for checkpoints 0.1.0
through 0.12.0 are closed history, kept in git and in their closed milestones,
and are not restated here.

## Purpose

Mechanical Librarian is delivered through small end-to-end outcomes rather than
through completed horizontal work packages. WP0-WP9 remain historical
capability-ownership buckets, not current issue parents. A functional slice
crosses whichever capabilities are needed to produce one executable, reviewable
result.

The sequence below is the default implementation order. Each assigned row has
one slice tracker and one bounded delivery transaction; a row explicitly marked
as requiring promotion remains an outcome only until that promotion completes.
The transaction may use one `slice/...` branch and pull request or sequential
independently safe vertical increment branches and pull requests. The tracker
contains the final ticket list and may narrow the outcome before work starts.
Divide the outcome into suffixed slices such as `F05a` and `F05b` only when they
are genuinely distinct supported outcomes. Split a transaction into additional
pull requests only for distinct vertical increments or a required security or
privacy separation, never into horizontal layers or merely because review found
more work.

The tables preserve assigned and historical delivery records. A future outcome
outside them remains a concise deferred issue with no `F` ID; a stable
functional-slice designation is assigned only when that same issue is
explicitly promoted for delivery. Deferred outcomes do not acquire trackers,
constituents, milestones, or implementation design merely to reserve future
sequence.

The currently numbered delivery milestones are internal pre-release checkpoints,
not automatic release-publication commitments. Their exit criteria remain
authoritative, but completing a checkpoint does not itself create a tag, GitHub
Release, supported version, or published release artifact. The checkpoint and
version for the first published release remain undecided and require a separate
maintainer selection plus all applicable release gates and release-publication
gates.

## Focused work outside the slice sequence

Fixes, audit corrections, security work, documentation, tooling,
dependency-only changes, maintenance, and standalone tickets use focused
ticket branches and PRs. They may be native prerequisites for a
new-functionality slice, but they are never absorbed into that slice merely to
satisfy its checks. A slice may still introduce a dependency needed for its
observable outcome under the repository dependency policy.

Publication-readiness #43 remains a separate first-release gate. Any frontend
architecture decision needed for F16a is owned by #152 during promotion rather
than by the closed #192 ticket. The previously listed correction, governance,
and prerequisite tickets are completed or superseded history, not current work.

## Closed checkpoints

Checkpoints 0.1.0 through 0.12.0 are complete. Their outcome tables and exit
criteria are in the closed GitHub milestones of the same names and in this
file's git history; the last revision carrying them is recorded in the pull
request that removed them. A closed checkpoint's issue ranges are historical
context and never permission to reopen a legacy parent or constituent.

## Delivery checkpoint 0.15.1: owner-confirmed semantic cover decisions

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F21 Owner-confirmed cover decisions | #501 | One exact-current cover review shows the incumbent and safe report-bound alternatives, presents contained observation and deterministic policy only as advice, persists an explicit owner keep/select/no-action outcome and its successor history, and applies a selected original through the existing guarded Audiobookshelf path. | Activation candidate open for maintainer review. |

F21 reuses the 0.15.0 packet, identity, observer, policy, containment, and
versioned engineering benchmark evidence. Observation or recommendation
unavailability keeps identity-admitted manual selection fully supported.
Automatic model-selected writes, bulk reevaluation, silent learning, new
providers, release, tag, and deployment work are excluded.

## Delivery checkpoint 0.15.4: completed publication and recoverable advisory covers

This checkpoint composes the next bounded cross-layer capabilities without
granting a release, tag, deployment, or production operation.

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F22 Complete per-work publication | #597 | After every provider lane reaches a terminal outcome, one work publishes its complete bounded result; sibling failure stays inspectable without hiding coherent evidence. | In delivery as a one-issue activation slice; independent focused prerequisite #418 is complete. |
| F23 Qualified advisory cover review | #600 | Standard preparation automatically performs one bounded, qualified advisory cover review for the work. | Candidate complete: advice remains exact-current and owner-only; unavailable review retains manual comparison. |
| F24 Provider/configuration-scoped canary recovery | #408 | One durable provider- and configuration-scoped canary recovers safely within its bounded scope. | Activation candidate: the supervisor and owner CLI share exact canary/member claims; successful members complete #630's proposal, admission, projection, and atomic package cutover without rerunning healthy providers. |

#418 is an independent focused prerequisite to F22, not an F22 constituent.
#594, #595, #596, and #418 are focused defects with `Delivery slice: Not
applicable`. #598 and #599 are focused defects blocked on the final installed
#591 Activity baseline. Milestone 23 remains externally owned and is neither
copied nor modified by this checkpoint. This checkpoint creates no horizontal
plan or additional constituents.

## Delivery checkpoint 0.17.0: full-library evidence foundation

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F25 Bounded EPUB series evidence | #375 | Exact installed inspection and report paths retain explicit package, front, and rear series/sequence indicia without prose or catalogue inference. | In delivery as one activation slice. |

## Delivery checkpoint 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.
The original WP1-WP9 parents and their constituent forests are closed,
superseded planning history; they are not current delivery tickets and must not
be reopened or recreated.

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | Retained planned designation. |

The checkpoint's other outcomes are delivered and their trackers are closed:
F15a supported container operation #151, F16a authenticated browser review and
decision #152 (through #447, #448, #449, #450 and the milestone-13 corrections
to #455), F17a continuous review orchestration #473, F13 guarded fake-service
apply #149, F14 Audiobookshelf canary apply #150, and F17 continuous pre-release
operation #153. Cross-case history remains #195.

The outcomes are not one serial chain. Readiness records the real dependencies:
#70 supplies explainable proposals; F10b admits eligible proposals; F16a records
applicable current human decisions and qualifies the first-release browser
baseline; F13 supplies the common human and qualified-automatic guarded
fake-service path; F14 follows completed F13 qualification for its disposable
Audiobookshelf canary; F11 is terminal historical context with only its
deterministic packet and decision projection retained; F15a supplies the
supported operating surface; and F17 composes the useful guarded path under
repeated operation without depending on future target CAS. F08 remains an
independent evidence outcome. A retained `F` ID is a planned designation on an
assigned outcome issue, not proof of `Ready` status or authority to begin.

### 1.0.0 checkpoint exit

- every write is current, authorized, backed up, idempotent, verified, audited,
  and recoverable;
- read-only discovery and diagnosis continue when writes are disabled;
- unattended report-only continuation resumes from durable scheduler state
  without widening its initiating principal, authorization context, scope,
  versions, bounds, expiry, or mutation authority; each transition actor is
  recorded against that authority, and process IDs or detached watchers remain
  diagnostics only;
- the normal deployment is one supported container with one persistent volume,
  read-only media mounts, and one HTTP port;
- one authenticated owner can inspect a current case and make one persistent,
  server-authoritative individual decision through the F16a first-release
  responsive, keyboard/focus, accessibility, private caching/storage,
  content-security, hostile-rendering, performance, synthetic-artifact, and
  primary-supported-browser baseline; and
- the complete useful path passes bounded continuous-operation qualification
  against exact-current state without adding release or compatibility promises.

Satisfying this checkpoint exit does not itself publish a release. First-release
artifact production #94 and publication-readiness audit #43 are separate gates
in the `first-release` milestone and require an explicit maintainer publication
decision. Post-release migration and older-schema restore recovery remain #57;
neither is F17 acceptance.

## Deferred outcomes

The current deferred product outcomes are:

- opening-audio evidence #24; its former #104–#110 decomposition is closed
  superseded history;
- one outcome per further metadata provider: LibraryThing #211, Project
  Gutenberg #212, LibriVox #213, and Audible #214;
  the old F09c/F09d/F09e trackers #205, #206, and #208 are closed superseded;
- bulk browser review and extended qualification #195 after the first public
  release;
- Calibre-Web Automated library-provider support #316 after the first public
  release;
- optional Audiobookshelf notifications and polling enhancement #65;
- post-release migration and restore recovery #57; and
- the [additional reviewer backends](future-reviewer-backends.md), embedded-file
  updates, plugin loading, and other optional extensions.

These outcomes stay concise and have no functional-slice designation, milestone,
or pre-created constituents unless explicitly selected. Open Library #210 is
no longer deferred: it is the completed F09l slice in milestone 0.8.0 with no
constituents. Closed speculative trackers, WP parents, and constituent forests
remain historical inputs and never become current work automatically.

## Planning rules

- A named `Deliver slice <ID>` instruction selects that assigned, unblocked
  `Ready` tracker. Without a named ID, the highest-priority assigned, unblocked
  `Ready` tracker determines execution order across work packages. Neither form
  selects or promotes an `Unassigned backlog` outcome.
- The Project `Delivery slice` field records each new-functionality ticket's
  primary slice. Focused work uses `Not applicable`.
- Slice trackers carry their exact closure mapping. Historical issue ranges in
  completed checkpoint records are context, not permission to reopen or close a
  legacy parent or constituent.
- A deliberately narrow adapter or interface is acceptable when it completely
  demonstrates the slice and preserves compatibility for later extension.
- No slice may defer its required safety, recovery, observability,
  documentation, clean-schema handling (or post-release migration), or test
  obligations to a later slice.
- Historical work-package parents remain closed; a current slice neither
  reopens them nor inherits their superseded constituent forest.
