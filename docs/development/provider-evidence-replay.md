# Provider evidence replay qualification

This procedure preserves expensive provider observations for one explicitly
authorized private cohort without making an application database schema or a
historical parser authoritative. It is maintainer qualification only. ADR-0013
owns the compatibility boundary and the security model owns credentials,
provider egress, hostile response bytes, private paths, and diagnostics.

## Boundary

Capture starts from a clean exact source head, an exact-current owner-only
application database, the current owner-only configuration, and an owner-only
canonical selection document. It runs the current configured metadata provider
adapters for those reports and seals their pre-parser HTTP exchanges. It never
reads target credentials, target services, media, reviewers, or downstream
decisions.

Replay starts from a clean exact source head, the current configuration, and one
sealed bundle plus its digest. It supplies placeholder provider credentials and
has no network fallback. Current adapters decode and parse the sealed response
bytes, current report and proposal derivation reruns, and current persistence
publishes reports, proposals, and admitted review cases into an absent database.
No historical decision or projection is copied.

The command is intentionally source-bound:

```text
uv run python scripts/qualify_provider_evidence_replay.py capture \
  --repository ABSOLUTE_CLEAN_CHECKOUT \
  --expected-head 40_HEX_SOURCE_HEAD \
  --config ABSOLUTE_PRIVATE_CONFIG \
  --database ABSOLUTE_EXACT_CURRENT_DATABASE \
  --selection ABSOLUTE_PRIVATE_SELECTION \
  --output-directory ABSOLUTE_EMPTY_PRIVATE_CAPTURE_DIRECTORY

uv run python scripts/qualify_provider_evidence_replay.py replay \
  --repository ABSOLUTE_CLEAN_CHECKOUT \
  --expected-head 40_HEX_SOURCE_HEAD \
  --config ABSOLUTE_PRIVATE_CONFIG \
  --bundle ABSOLUTE_PRIVATE_BUNDLE \
  --bundle-digest ABSOLUTE_PRIVATE_BUNDLE_DIGEST \
  --output-directory ABSOLUTE_EMPTY_PRIVATE_REPLAY_DIRECTORY
```

The checkout must have no tracked or untracked change at the named head. Ignored
private qualification artifacts remain outside that source identity.

Input leaves and output directories must belong to the current owner and grant
no group or other access. A configuration's complete ancestor chain must contain
no symlink or group/world-writable non-sticky directory, and the descriptor-bound
document that passed those checks is the document parsed. Database inputs must
also have no sidecars. Output directories must be empty. The command creates
fixed names exclusively and never overwrites or deletes an incumbent. An
interrupted or ambiguous run requires explicit private recovery before a new
directory is used.

## Selection and outputs

The selection is strict canonical JSON, at most 100 unique current report IDs:

```json
{"report_ids":["report_0000000000000000000000000000000000000000000000000000000000000000"],"schema":"provider-replay-selection-v1"}
```

Capture creates `bundle.json` and `bundle.json.sha256`. Replay creates
`current.sqlite3`, `result.json`, and `result.json.sha256`. The bundle and result
are private evidence, not support artifacts: do not add them to the repository,
logs, review input, or issue and pull-request attachments. Standard output is
aggregate only. Failure output is a fixed value-free sentence.

The operator retains a bundle only while its exact captured lanes remain useful.
Deletion is a separate explicit recoverable private-maintenance action; this
command has no deletion option. Preserve an independent retained source when a
capture or replay is being investigated, and never treat the command's output as
the sole retained copy of qualification state.

## Invalidation and interpretation

The bundle binds the capture head, configuration and report schemas, provider
name and adapter, canonical request, exact bounded response stream, capture
time, private source seed, and live/current report and proposal digests. Replay
also binds its source head and resulting database digest.

A removed or changed configured provider name/adapter, a newly enabled provider
without captured evidence, or a changed canonical current request yields a
provider-scoped `provider_contract_changed`, `provider_evidence_missing`, or
`request_contract_changed` invalidation. Replay continues only with unaffected
providers for that report.
Malformed, oversized, noncanonical, tampered, unsafe, or endpoint-unconfined
evidence fails the whole command. At the exact capture head, any otherwise
unexplained report/proposal difference fails rather than being reclassified as
expected drift.

## Validation matrix

| Evidence | Required interpretation |
| --- | --- |
| Public synthetic tests | The format, sanitizer, hostile-input bounds, current-adapter replay, selective invalidation, deterministic derivation, fresh persistence, and zero-network boundary work without private material. |
| Private same-cohort capture/replay | The exact captured live/current reports and proposals reproduce offline at that head; current success and failure states are represented; credentials and contact are absent. |
| Exact-installed qualification | Still required separately for the candidate artifact. Replay does not qualify packaging or the installed command surface. |
| Complete-library scale, browser, and semantics | Still required separately over a complete representative database. Use #938 until later accepted evidence proves replay covers those responsibilities. |

Issue #939's accepted proof used 12 works across ISBN-plus-work, ISBN-only, and
work-only discovery and all three configured providers. It did not capture the
roughly 2,000-work library. Therefore it narrows no #938 responsibility and
makes no full-library replay claim.
