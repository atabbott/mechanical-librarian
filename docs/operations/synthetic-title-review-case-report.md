# Synthetic Title Review Case Report

`synthetic-title-review-case-report` is the F10a report-only inspection surface.
It admits only the already-persisted current F09a proposal set selected by one
closed synthetic scenario. It does not generate media, invoke a provider, read
configuration, resolve credentials, contact a target, call a reviewer, record a
decision, or prepare a write.

## Usage

First persist the applicable F09a scenario, then inspect its review-case state:

```console
mechanical-librarian synthetic-title-proposal-report \
  --database /data/mechanical-librarian.sqlite \
  --scenario agree-empty

mechanical-librarian synthetic-title-review-case-report \
  --database /data/mechanical-librarian.sqlite \
  --scenario agree-empty \
  --format json
```

The command accepts only `--database`, one scenario from the closed F09a
vocabulary, and `--format json|text`. It accepts no target, snapshot,
proposal-set, proposal, title, path other than the application database,
endpoint, provider payload, reviewer output, disposition, or authority input.
Invalid syntax exits `2` before state access. State, migration, persistence, or
serialization failure exits `1` with a fixed redacted diagnostic. A valid
current-case or typed no-case result exits `0` with one canonical report.

## Admission and lifecycle

Only a current immutable `synthetic_preview` set containing exactly one
`needs_review` title proposal creates an undecided case. The case uses
`synthetic-title-review-binding-v1` and binds the closed synthetic target,
manifest-derived item, exact snapshot and fingerprint, set and proposal IDs,
field and values, disposition, policy and comparison versions, and bounded
F09a/F06 provenance. Its ID is a deterministic digest of that complete
projection.

- Exact replay returns the same case and creates no duplicate case or
  transition.
- A current qualifying same-lineage successor creates a replacement and an
  immutable supersession transition.
- A current nonqualifying same-lineage successor retires the prior case,
  records the successor set and reason, and creates no empty replacement.
- A stale, non-current, reverse-replayed, malformed, arbitrary, or cross-lineage
  source cannot rewind, revive, supersede, retire, or otherwise mutate state.
- Fresh nonqualifying F09a results—including no-change, conflict, missing,
  unavailable, failed, unsupported, empty, no-match, unusable, and
  ambiguous—record a typed no-case result. A stale set is rejected without an
  F10a transition.

A current no-case or retired pointer is terminal for this bounded two-revision
preview. A later changed set cannot create, revive, or append another no-case
transition; exact replay remains inspectable and duplicate-free.

The linked `retire-before` and `retire-after` scenarios demonstrate retirement.
The existing `supersede-before` and `supersede-after` scenarios demonstrate
replacement. Run the proposal command followed by the review-case command for
each step, using the same isolated database.

## Persistence and recovery

Schema 7 adds immutable synthetic case history, append-only attributable
lifecycle transitions, and one scoped current state that also records the
latest admitted no-case set. Proposal input is already-committed F09a state.
F10a revalidates the exact current set inside its write transaction; case,
pointer, link, actor, and reason commit atomically. A failure rolls back the
whole F10a transition without rolling back or rewriting F09a history.

Before upgrading important state, retain and verify a pre-schema-7 application
state copy. Schema 7 is forward-only. Downgrade requires stopping the newer
process and restoring that verified copy; do not delete the new tables or open
schema-7 state with an older binary. After restore, run `state-check`, the
applicable F09a proposal report, and this command against non-production state.

## Authority and privacy

Every report states `authority=synthetic-report-only` and derives
`production_applicable=false`; neither value is an operator-controlled or
writable database field. The case is not an ordinary production review case and
cannot be reinterpreted as configured or live. It grants no AI recommendation,
human decision, approval, browser or bulk action, apply plan, target write,
media write, or destructive authority.

Reports and durable rows retain only the bounded canonical case and required
synthetic F09a/F06 provenance. They contain no database or media path,
credential, raw provider traffic, upstream provider ID, generated EPUB bytes,
or fabricated `EvidenceRecord` or `EvidenceReference` history.
