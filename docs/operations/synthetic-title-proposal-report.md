# Synthetic title proposal report

`synthetic-title-proposal-report` is the F09a proof-of-concept command. It runs
one closed, licence-safe scenario through the real F06 EPUB package child, an
in-memory Audiobookshelf-shaped snapshot, and a deterministic provider fake,
then persists and reports one immutable proposal set.

It is not a live-library command. It accepts no configuration, target or media
identifier, title value, file or report, provider payload, endpoint, or
credential. It constructs no Audiobookshelf adapter, configured-media resolver,
network client, DNS lookup, secret resolver, AI reviewer, review case, approval,
apply plan, or write path.

## Run the preview

```console
mechanical-librarian synthetic-title-proposal-report \
  --database /data/mechanical-librarian.sqlite \
  --scenario agree-empty \
  --format json
```

`--format` may be `json` (the default) or `text`; both expose the same bounded
projection. The report includes scenario and lineage identity, governing
versions, generated EPUB and opaque file identities, synthetic snapshot state,
the actual F06 report/source/citation identity, fake-provider outcome and
candidate provenance, exact typed result, proposal when present, replay and
supersession state, `mode=synthetic_preview`, and
`production_applicable=false`. All shown titles and identifiers are
repository-authored synthetic values.

The closed scenarios are:

| Scenario | Result |
| --- | --- |
| `agree-empty` | One fill-empty `needs_review` proposal |
| `agree-different` | One replace-populated `needs_review` proposal |
| `current-equivalent` | `no_change` |
| `current-missing` | `current_missing` |
| `current-unavailable` | `current_unavailable` |
| `local-provider-conflict` | `local_provider_conflict` |
| `f06-missing` | `f06_missing` |
| `f06-ambiguous` | `f06_ambiguous` |
| `f06-unusable` | `f06_unusable` |
| `provider-disabled` | `provider_disabled` |
| `provider-failed` | `provider_failed` |
| `provider-empty` | `provider_empty` |
| `provider-no-match` | `provider_no_match` |
| `stale-input` | `stale_input` |
| `supersede-before` | First same-lineage `needs_review` set |
| `supersede-after` | Exact successor that supersedes `supersede-before` |
| `retire-before` | First same-lineage `needs_review` set for F10a retirement |
| `retire-after` | Exact `no_change` successor that retires the F10a case |

Run each `*-before` scenario before its paired `*-after` scenario. Repeating an
identical scenario after restart reuses its immutable history. Replaying a
`*-before` scenario after its successor reports the older set as non-current and
never rewinds the pointer. The retirement pair exists for the
[`synthetic-title-review-case-report`](synthetic-title-review-case-report.md)
demonstration and does not alter existing scenario identities.

## Exit and state behavior

- Exit `0`: stdout contains exactly one canonical report; stderr is empty.
- Exit `2`: syntax, missing values, unknown scenarios, unsupported formats, or
  `:memory:` are rejected with one fixed diagnostic on stderr. The database is
  not opened, created, or migrated.
- Exit `1`: state, migration, F06 child, provider-contract, persistence,
  serialization, or unexpected failures emit one fixed diagnostic on stderr;
  stdout is empty and no partial proposal set is published.

The proposal tables were introduced by schema 6; the current application
prepares or migrates to its complete current schema before use. Existing state
is preflighted before migration. Proposal history is
append-only and its synthetic current pointer advances in the same transaction;
malformed, tampered, unknown-mode, dangling, cross-lineage, or non-exact
predecessor state fails closed without repair.

Migration 6 is forward-only. Back up and validate durable state before an
upgrade, and restore the verified pre-migration copy to roll back. See
[Upgrades and migrations](upgrades.md) and
[Backups and recovery](backups-and-recovery.md).

## Separate live binding

F09a deliberately does not join a production F06 report to a current
Audiobookshelf snapshot. The separate
[`live-title-proposal-report`](live-title-proposal-report.md) command owns that
join. Schema 12 preserves every existing F09a row as `synthetic_preview` and
production-inapplicable; live results use a distinct execution mode in the
shared append-only proposal history.
