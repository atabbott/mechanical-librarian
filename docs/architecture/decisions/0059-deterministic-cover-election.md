# ADR-0059: Separate deterministic cover election from reviewer advice

- Status: accepted
- Date: 2026-08-28
- Owners: project maintainers
- Supersedes: ADR-0049 reviewer-dependent recommendation and manual-only cover authority

## Context

ADR-0049 activated exact report-bound cover considerations, but their useful
recommendation was coupled to optional reviewer observations. Retained evidence
already establishes conservative cover identity without a reviewer. Provider
recovery can also leave a provider waiting for retry without actively fetching.
Neither condition should disable an otherwise safe owner comparison.

## Decision

One pure domain election ranks only identity-confirmed candidates. Verified
manifestation matches rank before verified work matches. Within either tier, a
portrait-sane image (`height >= width` and `height <= 2 * width`) ranks first,
then pixel area, width and height descending, canonical packet ordinal/source
precedence, and finally the opaque candidate reference. Exact duplicate
collapse and every source binding remain upstream inputs; reviewer observations
are validated and retained but cannot admit, exclude, order, delay, or decide.
When a duplicate group contains the Audiobookshelf target, that exact target
artifact supplies the retained reference, source digest and original
dimensions. A larger sibling with identical preview bytes cannot make the
incumbent appear larger; maximum dimensions still describe duplicate groups
that contain no incumbent. The packet producer revision participates in replay
identity so earlier same-schema considerations remain readable history and are
superseded once.

Owner readiness requires at least one confirmed non-incumbent candidate and no
exact persisted provider/report lane actively fetching. Automatic choice is a
separate result. A known-empty target may use the first ranked candidate; a
confirmed target may use one only when its complete seven-part rank is strictly
better than the confirmed incumbent. Missing or inconsistent incumbent facts
abstain automatically while retaining the owner's ranked choices.
For audiobooks, ADR-0073 and #1055 supersede that rank-only replacement test:
the bound incumbent's shorter edge must be below 300 pixels, and an applicable
portrait/square alternative must reach 500 pixels and twice the incumbent's
shorter edge. The first eligible candidate in the existing order wins, even
when an earlier candidate fails those limits. Adequate or unknown incumbents
are preserved. This measured materiality rule does not change ebook election.
Checking and running are active; queued retry/backoff waiting is quiescent.
Readiness, rank and citations are persisted with the consideration. A changed
fetching state or evidence creates a successor rather than rewriting history.
Library, Attention, Detail and suspension membership re-evaluate that fetching
state at each read. SQL receives the replay reference captured before query
execution and consults only the separate recovery store; it does not call the
reviewer, Settings, or the application repository while SQLite owns its mutex.

Automatic cover authority remains default-off and is admitted only by the
frozen aggregate-safe qualification manifest. It uses the same immutable
decision and guarded effect path as other automatic fields, including current
policy, consent, capability, backup, optimistic-currentness, recovery and
read-back verification. A later strictly better candidate may supersede an
automatic cover decision while automatic authority remains active. The first
automatic decision and every later one use the same media-appropriate replacement
test. Any manual
owner cover decision is final for this slice and prevents reopening or
supersession.

## Consequences

Reviewer-less deployments retain functional owner choices while reviewer
availability varies independently as advisory evidence. A sole confirmed
choice remains actionable after a typed sibling timeout. Unconfirmed matches
remain inspectable but never selectable. The clean pre-release proposal,
consideration, policy-report, and policy contracts advance together; existing
state requires a clean rebuild under ADR-0013.

Qualification may expose only cohort aggregates. Private identities, values,
rows, media, paths and per-case outcomes never enter the repository. All three
frozen cohorts passed the exact candidate, so `cover_order:fixed_source_order`
is available as an explicit Automatic opt-in. The default destination remains
`Ask me`.

## Validation

- Domain tests freeze the total order, citations, confirmed-only admission and
  active-fetch readiness rule.
- Persistence and application tests distinguish active fetching from retry
  waiting and prove successor/current behavior.
- Browser tests cover reviewer-less, fetching, unconfirmed, recommended and
  owner-final states at compact and desktop compositions.
- The frozen qualification harness reports counts only and requires zero false
  admissions, duplicate-collapse failures, readiness disagreements, order
  disagreements and recommendation disagreements in every cohort.
