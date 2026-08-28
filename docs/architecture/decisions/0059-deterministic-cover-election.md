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

Owner and automatic paths share one readiness rule: at least one confirmed
candidate and no exact persisted provider/report lane actively fetching.
Checking and running are active; queued retry/backoff waiting is quiescent.
Readiness, rank and citations are persisted with the consideration. A changed
fetching state or evidence creates a successor rather than rewriting history.

Automatic cover authority remains default-off and is admitted only by the
frozen aggregate-safe qualification manifest. It uses the same immutable
decision and guarded effect path as other automatic fields, including current
policy, consent, capability, backup, optimistic-currentness, recovery and
read-back verification. A later strictly better candidate may supersede an
automatic cover decision while automatic authority remains active. Any manual
owner cover decision is final for this slice and prevents reopening or
supersession.

## Consequences

Reviewer-less deployments receive a functional recommendation, while reviewer
availability varies independently as advisory evidence. Unconfirmed matches
remain inspectable but never selectable. The clean pre-release schema and
generated API contract advance together; existing state requires a clean
rebuild under ADR-0013.

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
