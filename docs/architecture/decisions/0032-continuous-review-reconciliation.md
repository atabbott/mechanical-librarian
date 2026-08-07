# ADR-0032: Activate bounded continuous review reconciliation

- Status: accepted
- Date: 2026-08-07
- Owners: project maintainers
- Supersedes: the polling deferral in ADR-0031

## Context

ADR-0031 delivered an owner-requested persisted preparation pipeline. F17a must
make that same decision-only pipeline the supported container's quiet background
reconciliation path without introducing a generic scheduler or write authority.

## Decision

`review-serve` requests every configured library on first startup and again at
one global bounded interval measured from its last terminal success. Duplicate
automatic and owner requests coalesce in the existing per-library scan record.
Credential and configuration failures remain terminal until the operator
corrects the configuration and explicitly retries validation. Transient
provider unavailability resets work to extraction and recovers automatically
behind the durable shared circuit deadline. A separate persisted recovery count
bounds that automatic cycle to three attempts before the failure becomes
terminal and actionable.

One durable singleton control record owns pause state, fair-claim sequence, and
the bounded shared provider circuit deadline. Pause prevents new scan admission
but does not cancel persisted work. Two fixed lanes remain the global in-flight
limit. Claims prefer a different library, age backlog work, and reserve every
fourth claim for recently changed work. Provider adapters retain their bounded
`Retry-After` handling and shared dispatch pacers. The circuit persists the
greater of its bounded exponential delay and a provider-supplied `Retry-After`.
Success from another in-flight work cannot shorten an unexpired deadline.
Work reuse is bound to a canonical fingerprint of the material Audiobookshelf
review inputs. Stable media revisions keep volatile service activity timestamps
out of that identity; an absent material revision or an independently
unversioned cover conservatively falls back to the item timestamp. The clean
catalogue schema advances to `catalogue-v2` to retain that fingerprint.

The authenticated shell exposes one compact activity control only while work is
active or operational action is needed. Its popover contains bounded counts and
pause, resume, and eligible retry controls. It never exposes raw jobs, rates,
provider payloads, secrets, paths, or metadata values.

## Consequences

The supported container now performs report-only reconciliation unattended and
survives restart with the same bounded scheduling state. Configuration gains
only `review.library_polling_interval_seconds`; concurrency and provider tuning
remain maintainer-owned. The path still cannot decide fields, construct an apply
plan, mutate a target or media file, delete data, release, tag, or deploy.

## Validation

Unit, browser, scale, clean-schema, architecture, generated-contract, and exact
installed-`review-serve` checks cover startup, interval resumption, coalescing,
pause/resume, retry eligibility, material-source drift, fair bounded claims,
circuit recovery, compact activity, keyboard focus, responsive layout, and zero
target/media writes. The installed-artifact integration processes 1,000 works
across three libraries, admits a newly discovered work during backlog, invokes
the installed `review-serve` console, interrupts and restarts automatic
discovery, authenticates to its final exact progress projection, distinguishes
transient recovery from credential correction, stays below the bounded memory
ceiling, and writes no target or media file.
