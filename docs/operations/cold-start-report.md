# Cold-start report

`scripts/cold_start_report.py` measures preparation on a real library. It is
a workbench tool, not part of the installed product. Run it from a checkout
against the configuration and copies of the databases:

```
uv run python scripts/cold_start_report.py \
    --config /copy/config.json \
    --database /copy/application.sqlite3 \
    --catalogue /copy/catalogue.sqlite3 \
    --mount-root books=/host/path/to/mount \
    --stage-timings /copy/stage-timings.json \
    --recovery-database /copy/provider-recovery.sqlite
```

It writes nothing, can run at any time, and prints one
`cold-start-report-v5` JSON document.

- `--mount-root NAME=PATH` (optional, repeatable) maps a configured mount to a
  host path, because the configuration records container paths; without it
  the container path is walked.
- `--stage-timings` (optional) is a copy of the diagnostic stage-timing file,
  mode 0600 in a 0700 directory owned by the user running the report.
- `--recovery-database` (optional) is a copy of the provider recovery
  database.

A named optional file that is missing or unreadable stops the report; an
omitted one gives a `null` block.

## Privacy

The report is aggregate-only: configured names, destinations, reason codes,
file extensions and counts. It never holds a title, author, filename, media
path or item identifier. Attention reuses the Attention surface's own SQL, so
the report agrees with the interface.

## Per-library blocks

Each library carries these blocks, defined fully in the script's docstrings.

| Key | Contents |
| --- | --- |
| `scan` | Latest preparation scan: state, discovered total, timestamps. |
| `works` | Current, attention, quiet, ready, blocked and absent works. An in-flight `report_only` package counts once its proposals are complete. |
| `attention_fields` | Attention by destination, exact cause and cause class (conflict, owner_subjective, capability, absence, safety, other). |
| `attention_stored_basis` | Attention read from the cover authority stored with each consideration. Always present. |
| `providers` | Lane outcomes per configured provider; `zero_lane` marks one with no lane. |
| `works_without_provider_candidates` | Works with no candidates from any source. |
| `not_matched` | Library's Not matched works: `total` and `by_reason` (`refused`, `unavailable`, `no_match`). |
| `timing` | First-ready latency, elapsed time, works per hour, projected remaining. |
| `scan_fidelity` | Mount files by extension, catalogue items by classification and format, ledger works. |
| `automatic_decision_corroboration` | Automatic decisions by corroborating provider sources (`0`, `1`, `2`, `3_plus`). |
| `automatic_decisions` | Persisted automatic decisions on current cases, per destination and `outcome`. |
| `audiobook_dispatch` | How each audiobook report's lanes ended, by kind, provider class, Audible ladder depth, runtime `band` and `near_miss`. |
| `audiobook_outcomes` | Audiobook reports split into `recording_admitted`, `work_only` and `evidence_only`; `hardcover_witness` counts what Hardcover's audio editions say about each ([ADR-0090](../architecture/decisions/0090-hardcover-audio-edition-witness.md)). |
| `audiobook_cover_reads` | Audiobookshelf cover read states; `without_read` counts reports with none. |
| `dispatches` | Initial, retry and replayed provider requests, per item and per provider, and `never_dispatched`. |

The offline report has no live cover policy. A library with a current cover
consideration reports its attention figures, and the top-level ones, as `null`
with `attention_unavailable_reason: current_cover_authority_unavailable`. Use
`attention_stored_basis` instead. Only the attention figures go null; total
works, scan, provider, timing and fidelity figures remain. Historical cover
considerations do not trigger this.

Each `evidence_only` audiobook report has exactly one reason, judged in this
order from its lanes' runtime bands:

1. `no_retained_recording_candidate`: no lane kept a candidate;
2. `tight_product_refused_by_title`: every band `unstated`, but a product
   refused on an ASIN lane states a tight runtime;
3. `no_comparable_runtime`: every band `unstated` otherwise;
4. `runtime_contradiction`: every band `outside`;
5. `runtime_near_miss_only_or_mixed`: anything else.

`starved` provider counts are a lower bound
([ADR-0064](../architecture/decisions/0064-audiobook-provider-evidence-vocabularies.md)).
`never_dispatched` counts audiobooks an enabled provider sent no request for.

## Recovery blocks

With `--recovery-database`:

- `recovery_jobs` counts jobs by provider, adapter, target connection,
  library and state.
- `recovery_events` is global. It gives the latest event and job attempt
  times, per-provider attempts, outcomes, `classes` (`throttle`,
  `source_failure`, `no_match`, `completed`), `physical_requests` and
  `health_outcomes`.

For total requests, add only the `throttle` and `source_failure` entries of
`physical_requests.by_class` to `dispatches`; the other classes publish a
report and are already counted there.

## Top level and determinism

Top-level totals give the quiet fraction for configured libraries. The
determinism block flags works
in one library with identical content and target context but different
persisted proposal results.

## Timing and limits

Throughput comes from the scan and work ledgers. The diagnostic timing ring
holds at most 256 samples and is off by default, so `stage_timings` describes
only its tail: per stage and wait class, the count and p50, p95 and maximum
milliseconds.

Until a repeated scan publishes its new catalogue generation, the previous
cycle's work and absence rows are hidden.

The mount walk counts regular files by extension without following symlinks,
up to 1,000,000. Over the cap, or on any unreadable directory, it records
`null` rather than partial counts. An unreadable catalogue gives `null`
fidelity.

## Persistence census

`scripts/persistence_census.py` reads copies of the two databases and prints
table row counts, the index inventory, and hot-query plans with full-table
scans flagged.
