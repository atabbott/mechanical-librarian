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
    --mount-root books=/host/path/to/mount
```

It writes nothing and prints one `cold-start-report-v2` JSON document. It can
run at any point during or after preparation, repeatedly. `--mount-root`
remaps a configured mount name to a host path for the extension census,
because the configuration records container paths.

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
by default, so it cannot support throughput claims. When a repeated scan has
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
