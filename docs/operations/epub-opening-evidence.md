# Durable EPUB Opening Evidence

`epub-opening-evidence` executes or reuses the bounded F07a opening extractor
for one opaque selector produced by `inventory-inspect`.

```console
mechanical-librarian inventory-inspect \
  --config /run/config/mechanical-librarian.json \
  --database /data/state.sqlite3

mechanical-librarian epub-opening-evidence \
  --database /data/state.sqlite3 \
  --config /run/config/mechanical-librarian.json \
  --target target_0123456789abcdef0123456789abcdef \
  --snapshot snapshot_0123456789abcdef0123456789abcdef \
  --file-index 0
```

The first command emits a selector only for a current active EPUB whose
Audiobookshelf `path` and `relPath` were validated against exactly one
configured read-only ebook mount during inventory publication. The selector
contains no service ID, mount, filename, relative path, title, source hash, or
content. A snapshot created before migration 8, or one whose path projection
was missing, malformed, mismatched, or ambiguous, emits no selector; run a new
complete reconciliation rather than supplying a path manually.

## Result

A successful result reports `mode=executed` or `mode=reused`, an immutable
`evidence_…` identity, current applicability, a typed EPUB outcome, aggregate
claim/citation/selected/rejected/warning counts, and the aggregate parser mode.
It never prints the persisted report, excerpts, citations, source fingerprint,
member names, or media locator. An identical invocation after restart reuses
the same evidence identity. A changed source generation, extractor, parser,
recovery-policy, projection, selection, normalization, vocabulary, schema,
limit, or material mount configuration creates new work and evidence. Replaying
an older source generation reuses its immutable history with `current=false`
and does not roll back the current pointer.

Publishable outcomes are `success`, `no_applicable_evidence`,
`unsupported_format`, `unsupported_version`, `unsupported_compression`,
`unsupported_multiple_renditions`, `encrypted`, `malformed`, and
`integrity_failure`. `no_applicable_evidence` means the bounded selector found
no usable opening claim; it is not evidence that metadata is absent.
Cancellation, timeout, resource exhaustion, unstable/unavailable source,
child failure, and other operational failures remain job/attempt health and do
not create evidence. A present source larger than the effective
`source_archive_bytes` limit returns `resource_limit` before work is enqueued;
an inaccessible or unreadable source returns `source_reference_unavailable`.

## Recovery and safety

Work is durable before extraction and has at most three attempts. A worker must
hold the current owner, attempt, live lease, and fence at publication. Expired
or stale workers cannot insert evidence, advance current applicability, finish
the job, or write the checkpoint/event. Restart may reclaim expired work; a
live duplicate reports `busy` rather than running another child.

To restart the oldest compatible persisted request without redelivering its
selector, use the same database and configuration with `--resume` instead of
`--target`, `--snapshot`, and `--file-index`. Recovery is bounded to the exact
current mount and governing configuration. Claim admission enforces configured
parser concurrency, final lease exhaustion becomes `retry_exhausted`, and
cancellation is a fenced durable transition. Replaying terminal work returns
its validated stored cause, including `resource_limit`, `cancelled`, or genuine
`retry_exhausted`, rather than collapsing distinct operator outcomes. Attempt
rows permit exactly one unfinished-to-finished update; finalized attribution
and results are immutable, and terminal replay fails `corrupt_state` when
attempt and event history disagree. Claim, publication, failure, cancellation,
and reuse clocks cannot precede the latest durable event or job update; a
regressing transition or finalization clock rolls the whole transaction back.

Selector failures remain distinct and path-free: missing, ambiguous,
wrong-target, stale-snapshot, out-of-range, non-EPUB, mount-mismatch,
binding-integrity, and corrupt-state outcomes create no work. Publication
records initiating authority and immutable owner/attempt/fence attribution.
Older incompatible work may finish into immutable history but cannot replace a
newer current lane.

The source is opened through the configured container path with no-follow
component traversal and is hashed before acceptance and again by the F07a
stable-source execution. No target credential or network call is needed after
inventory. The command has no write route to Audiobookshelf or the media mount.

Migration 8 is forward-only. Before upgrading important state, verify a backup.
On migration failure, stop and restore the pre-schema-8 copy. Never delete the
new tables or open schema 8 with an older binary.

## Qualification

`scripts/qualify_epub_opening_evidence.py` copies only regular EPUBs into a
disposable shadow cohort, assigns path-independent random synthetic target
identities, and prepares state through the normal reconciliation application
service using the exact installed package,
verifies selector emission, executes the whole cohort, restarts for identity
reuse, changes one shadow source for isolated invalidation, proves reverse
replay, injects one unavailable-source failure, then proves an unrelated book
still reuses its unchanged current evidence. It emits only aggregate
outcome and parser-mode counts plus durability/failure-isolation totals, and
deletes the selector map and SQLite/WAL state. Private cohort paths,
filenames, hashes, titles, reports, excerpts, and per-book outcomes must never
enter repository artifacts, CI logs, issues, or pull requests.
