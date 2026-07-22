# ADR-0010: Set the 0.4 Clean-state Neutral Evidence Baseline

- Status: accepted
- Date: 2026-07-22
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The qualified 0.3 F09g delivery (#238) is the last pre-reset behaviour. It
reads one current Audiobookshelf ebook and selected read-only EPUB source,
extracts title and ISBN evidence locally, observes a compatible Hardcover
record, applies final currentness checks, preserves bounded and redacted
evidence, and persists an immutable report/proposal lineage. Its final gate
turns that evidence into either a `needs_review`, `production_report_only`
title proposal or a typed zero-proposal result.

Mechanical Librarian has no published release or supported upgrade source. The
next checkpoint should retain #238's qualified reading, extraction,
observation, currentness, privacy, and persistence lessons without retaining
its proposal gate, review-oriented terminology, or accumulated experimental
runtime lanes as permanent compatibility commitments.

## Decision

0.4.0 is a pre-deployment simplification checkpoint. Its supported end state is
one synchronous, report-only metadata-evidence command for one explicitly
selected EPUB ebook:

```
metadata-report --config PATH --database PATH --connection NAME --library NAME --item ITEM_ID --file-index INDEX
```

Both path inputs are required documented inputs. There are no defaults, CWD
lookup, implicit filenames, undocumented environment fallback, alternate output
modes, or presentation flags. On success the command emits exactly its
persisted canonical JSON report and one trailing newline. On failure it emits
no stdout, one bounded redacted diagnostic on stderr, and a nonzero exit.

The #238 contract is characterization evidence, not the 0.4 public result:

- target/media reading, local EPUB title and ISBN extraction, compatible
  provider observation, final currentness, bounded privacy, and durable exact
  replay remain evidence that the replacement must preserve where applicable;
- #257 may replace the internal EPUB evidence layers with one isolated
  inspector, but it preserves the qualified final-0.3 report path until
  activation; and
- #197 alone replaces the final proposal gate with a neutral durable evidence
  report. It does not admit a review queue, decision, approval, apply, or write
  authority.

Only a newly initialized 0.4 database is a supported starting state. Pre-0.4
development databases have no recognition, migration, import, preservation, or
tailored compatibility path. The activation must not delete, reset, repair, or
reinterpret them. This clean-state boundary does not weaken transactional
publication, validation, durable auditability of the new report, secret/private
path redaction, read-only media handling, or report-only/no-write safeguards.

The delivery graph is intentionally small and explicit:

```
qualified #238 -> #253 -> #255 ─┐
                       #257 ───┼-> #197 activation -> 0.4.0 exit
                       #261 ───┘
```

#253 freezes this baseline. #255 corrects fixture ownership, #257 supplies the
isolated EPUB inspector, and #261 simplifies the linked-work gate. These four
contributors must be complete before #197, which is the sole activation and
only runtime/schema reset. Before that activation, incomplete 0.4 behaviour is
not exposed and the existing final-0.3 report remains report-only. The four
historical superseded issues (#254, #256, #258, and #260) must be reconciled at
activation; they are not contributors.

0.4 excludes inventory browsing, bulk execution, review workflows, automation,
AI evaluation, apply, production or media writes, background work, retry,
provider caching, compatibility machinery, tags, releases, and publication.
It is not a release train, supported version, or release artifact. Completion
records an internal milestone only; any release still requires a separate
maintainer decision and the full release process.

## Consequences

### Positive

- The activation has one observable command and a small, testable authority
  boundary.
- The #238 characterization keeps proven evidence and safety properties visible
  while allowing the proposal-specific runtime to be removed deliberately.
- Contributors can assess dependency order and the activation boundary without
  treating historical cleanup tickets as active implementation work.

### Negative

- Contributors must start 0.4 state clean instead of carrying forward internal
  development databases.
- The final public proposal result from #238 is deliberately unavailable after
  #197 activates the neutral report contract.

### Neutral or deferred

- This decision neither implements the command nor changes configuration,
  schema, credentials, network paths, product authority, tags, or artifacts.
- Human or AI review and all mutation authority remain later product work.

## Alternatives considered

### Preserve the proposal gate as the 0.4 result

That would keep review-oriented semantics and a future decision boundary in a
report-only checkpoint. It is rejected because 0.4 needs neutral evidence for
later review, not partial review authority.

### Migrate every internal development database

That would turn unpublished experiments into a compatibility promise and widen
the reset's safety surface. It is rejected because there is no supported source
deployment to protect.

## Validation

- #253's governing documentation agrees with the milestone, #197, and all four
  contributing tickets.
- #257 proves #238-equivalent EPUB evidence behaviour through the isolated
  child while the final-0.3 report remains usable.
- #197 demonstrates the installed one-command contract from clean state,
  canonical report replay, no-write/report-only boundaries, and the required
  hostile-input, privacy, currentness, and transactional checks.
- The final `main` state reconciles the four superseded issues and records no
  tag, release, or release artifact as a consequence of milestone completion.
