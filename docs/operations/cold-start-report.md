# Cold-start report

`scripts/cold_start_report.py` is a workbench measurement instrument,
deliberately not part of the installed product: the shipped command surface is
unchanged. Run it from a checkout against the configuration file and copies of
(or read-only access to) the application and catalogue databases:

```
uv run python scripts/cold_start_report.py \
    --config /copy/config.json \
    --database /copy/application.sqlite3 \
    --catalogue /copy/catalogue.sqlite3 \
    --mount-root books=/host/path/to/mount \
    --stage-timings /copy/stage-timings.json \
    --recovery-database /copy/provider-recovery.sqlite
```

It writes nothing and prints one `cold-start-report-v4` JSON document. It can
run at any point during or after preparation, repeatedly. `--mount-root`
remaps a configured mount name to a host path for the extension census,
because the configuration records container paths. `--stage-timings` is
optional and names a copy of the diagnostic stage-timing file.
`--recovery-database` is optional and names a copy of the provider recovery
database; it is opened read-only like the others.

The report is aggregate-only by construction. It contains connection, library,
provider, and mount names from the operator's own configuration, destination
names, closed reason codes, file extensions, and counts. It never contains a
title, author, filename, media path, or item identifier, and the attention
predicate is not re-derived: the queries compose the same shared SQL fragments
the Attention surface executes, so the report cannot disagree with the
interface about which field needs attention when that predicate is available.
The offline workbench has no live reviewer revision, model profile, or cover
policy/replay binding. If a current library item points to a cover
consideration, that library therefore reports `works.attention`, `works.quiet`,
and `attention_fields` as `null`, with
`attention_unavailable_reason: current_cover_authority_unavailable`. It does
not treat the missing runtime authority as evidence that the cover is quiet.
The library's total works, preparation state, provider outcomes, timing, and
catalogue measurements remain available. Historical cover considerations do
not trigger this state, and an unavailable unconfigured library does not make
the configured headline totals unavailable.

Every library also carries `attention_stored_basis` (#1299), whether or not its
attention aggregates are available. It counts attention on the cover
authority persisted with each consideration, which needs no live binding.
Its keys:
- `basis: stored_cover_authority`;
- `non_cover_fields`, in the same shape as `attention_fields`;
- `cover_fields_with_owner_actions`, the cover fields whose stored
  consideration is ready for the owner and agrees with its case and report;
- `works`, the works with any such field.

Without a cover consideration it equals the live read. With one, it is the
read under the persisted authority, not a claim about the live one.

Per configured library the document reports: the latest preparation scan
(state, discovered total, timestamps); work counts (current, attention, quiet,
preparation-ledger ready/blocked, absences by kind), excluding an in-flight
`report_only` package until proposal derivation has produced a completed
outcome; attention fields split by
destination, by exact cause (the shadow abstention reason code when one
exists, otherwise the joined proposal reasons, otherwise the automatic-safety
suspension), and by cause class (conflict, owner_subjective, capability,
absence, safety, other); provider lane outcomes per configured provider with a
`zero_lane` flag for a provider that produced no lane at all; works with no
provider candidates from any source; throughput timing derived from the scan
request and the preparation ledger's completion timestamps (first-ready
latency, elapsed, works per hour, projected remaining) — a completed work
counts as ready whether or not it admitted a review case, because a quiet work
finishes with none; scan fidelity (files by extension on the mount, target
items by classification and format from the catalogue, works in the
preparation ledger); and automatic decisions bucketed by how many independent
provider sources corroborate the chosen candidate (0, 1, 2, 3_plus).
`automatic_decisions` lists one row per destination and decision `outcome`, with
its `count`. Each row is a persisted automatic decision on a current review
case in that library (#1299).

Each library also carries an `audiobook_dispatch` block (#1258) counting how
the provider lanes of every current audiobook report ended, `report_only`
packages included because discovery reaches every audiobook. `reports` is the
number of audiobook reports; `lanes` counts each provider's lanes by kind
(`asin`, `work`, or `work_alternative`) and dispatch outcome; `providers`
counts each provider's derived class per report, where a refusal outranks an
empty ladder in a no-match and one starved lane makes an incomplete provider
starved; `audible_rungs` counts Audible WORK lanes by `lane` kind, outcome and
the number of ladder rungs attempted; `band` counts each provider's lanes by
`lane` kind and the best runtime band a retained candidate reaches (`tight`,
`loose`, `outside`, `unstated`, or `none` for a lane that retained nothing);
`refusal_diagnostic` counts the `items` and, per provider, the reports where a
no-match provider states a work-identity refusal, including one whose lane is
`empty_or_refused` because it did not walk its whole ladder; and `near_miss`
counts the recorded Audible near-misses by sign and magnitude bucket
(`600-900`, `900-1800`, `1800-3600`, `3600+` seconds, each closed on its upper
bound). An ebook-only library reports zero reports and empty lists.
`starved` is a lower bound: a deadline-starved lane whose provider has a
sibling lane with a status that outranks `incomplete` carries no backing
timeout diagnostic and is counted `unavailable` (ADR-0064).

Over the same audiobook reports, `audiobook_outcomes` splits each library's
`reports` by the case its evidence earns. `recording_admitted` is a report
whose recording identity is admitted and `work_only` one whose book evidence
alone opens a work-level case, both read from the product's own review scope
rather than re-derived. Every other report is `evidence_only`, with a `total`
and a count for each of four reasons. They are judged, in this order, by the
set of best runtime bands its audiobook-authority lanes reach, as `band`
reports them, ignoring lanes that retained nothing, so each report has exactly
one: `no_retained_recording_candidate` when no such lane retained a candidate;
`no_comparable_runtime` when every band is `unstated`, because the retained
candidates state no runtime or the item has no composed local runtime;
`runtime_contradiction` when every band is `outside`; and
`runtime_near_miss_only_or_mixed` for every other set, which is any set holding
`loose`, or `outside` beside `unstated`. A retained `tight` candidate is
admitted under current admission, so it never reaches evidence only, and the
single "tight band but not admitted" item #1086 measured has no equivalent
here. `audiobook_cover_reads` counts the Audiobookshelf cover read each
report records by `state` and, for a `timeout`, its `timeout_scope`
(`attempted`, or `not_started_due_to_aggregate_deadline` when the window had
already closed); `without_read` counts reports with no such read, as when
cover evidence is switched off.

`audiobook_outcomes` also names every report a recording did not admit by the
same four reasons, so work-only reports are counted too (#1299):
- `not_recording_admitted` has a `total` and a count per reason;
- `work_only_by_reason` gives the same counts for work-only reports alone;
- `work_only_cases` counts the work-only reports that reached a review case.

Each library also carries `dispatches`, over every current item of any media
kind, `report_only` packages included, read from each provider's persisted
request accounting: the `items` counted, the `initial` and `retry` dispatch
totals, their `mean_per_item` (`null` with no items), `per_item` counting
items by the dispatches they spent (`0`, `1`, `2`, `3-5`, `6-10`, `11-20`,
`21+`, each closed on its upper bound), and `replays` with
`mean_per_item_with_replays`. `providers` has one row for each provider in
the library's `provider_connections`, zeros included, and one for each
provider a report names. Each row has a `configured` flag and an `audiobook`
and an `ebook` split of `initial`, `retry` and `replays`. Its `never_dispatched` block is the source for how many items
had a provider that was never dispatched (#1294): over the library's
`audiobook_items`, it counts per provider the current audiobooks to which an
enabled provider in the audiobook dispatch set sent no initial request, and
`items` counts the audiobooks with at least one such provider. It is not
`starved`: it also counts a provider with nothing to answer, such as a
per-ASIN provider for an item with no ASIN, and a starved lane may have sent
earlier rungs. `starved_items` counts, overall and per provider, the audiobooks
where a provider was stopped by the deadline before its first request: the
provider is `incomplete`, has no initial dispatch, and has at least one
undispatched incomplete request.

With `--recovery-database`, the top-level `recovery_jobs` block counts the
recovery database's jobs: a total `jobs` and one group per provider
connection, adapter, target connection, library and state. Without the
option it is `null`; a named file that does not exist or cannot be read as a
recovery database stops the report.

`recovery_events` is read from the same database in the same transaction.
The event table has no library column, so the block is global. It is `null`
without the option. Its keys:
- `last_occurred_at`: the latest event of any kind.
- `last_job_attempt_at`: the latest settled job attempt. This and
  `last_occurred_at` show whether recovery is quiet.
- `providers`: per provider connection and adapter, the `job_attempts`,
  `request_count_status`, `outcomes`, and `classes` (`throttle`,
  `source_failure`, `no_match`, `completed`). It also gives
  `physical_requests` with a `total` and the same `by_class` split.
- `health_outcomes`: the ordinary health failures per provider and outcome.

A `completed` or `no_match` attempt publishes a successor report. Its requests
are already in that report's request accounting, and so in `dispatches`. A
`throttle` or `source_failure` attempt publishes nothing, so its requests are
counted only here. Add `physical_requests.by_class` for those two classes to
the dispatch counts, never the total.

Top-level totals give the quiet fraction — the headline first-boot attention
rate is its complement and includes only libraries still present in the loaded
configuration. If any configured library has unavailable cover authority, the
top-level attention, quiet, and quiet-fraction aggregates are also `null` with
the same closed reason; the exact total-work count remains available. The
determinism block compares works in the same configured
library that share a content digest: members are bucketed by their current
target-field context first, and a group counts as divergent only when members
with identical bytes and identical context carry different persisted proposal
results, including quiet `no_case` outcomes. A copy whose target already differs
is treated as a different input rather than nondeterminism.

Timing uses the preparation scan and work ledgers, not the diagnostic timing
ring buffer: the ring holds at most 256 uncorrelated samples and is disabled
by default, so it cannot support throughput claims. What the ring can show is
where one item's preparation time goes, so with `--stage-timings` the document
adds a `stage_timings` block over the retained tail only: its record count,
first and last recording times, and per stage and wait class the sample count
and nearest-rank p50, p95 and maximum duration in milliseconds. An audiobook
item writes roughly 14 to 17 records (about 11 to 14 before #1258 split out the
two cover reads and the report publication), so the tail covers roughly its
last 15 to 18 items. The cover stages are recorded at close to 0 ms when no
cover observer is configured, which pulls their p50 toward zero. The copied
timing file must be mode 0600 inside a 0700 directory owned by the user
running the report, or the product's reader refuses it.
Without the option the block is `null`; a named file that does not exist or
that the product's own reader refuses stops the report rather than reading as
an empty tail. When a repeated scan has
not yet published its new catalogue generation, retained work and absence rows
from the preceding cycle are suppressed rather than presented as current.

The mount walk counts regular files by lowercased extension without following
symlinks. It is capped at 1,000,000 files and records `null` rather than partial
counts when the cap is exceeded or any directory or entry cannot be read; a
root that is not a readable directory is also `null`. A missing or unreadable
catalogue likewise records `null` fidelity tiers instead of failing the report.

`scripts/persistence_census.py` is the companion workbench tool for the
persistence health census. Run it against copies of the two database files,
never the live instance's: it emits per-table row counts, the full index
inventory, and `EXPLAIN QUERY PLAN` over the hot query shapes with full-table
scans flagged, feeding the index-versus-simplify persistence decision.
