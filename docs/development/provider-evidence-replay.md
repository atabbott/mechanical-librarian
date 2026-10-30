# Provider evidence replay

`scripts/qualify_provider_evidence_replay.py` captures real metadata-provider
responses for a small private cohort once, then replays them offline through
the current code. It is private maintainer qualification, never development or
CI. The compatibility boundary is
[ADR-0013](../architecture/decisions/0013-pre-release-clean-schema.md);
credentials, egress and hostile responses are covered by the
[security model](../architecture/security.md).

- **Capture** runs the currently configured provider adapters for the selected
  reports and seals their raw HTTP exchanges, before parsing. It never reads
  target credentials, target services, media, reviewers or decisions. Keys,
  authorisation and contact identity are stripped.
- **Replay** supplies placeholder credentials and has no network fallback.
  Current adapters parse the sealed bytes, current report and proposal
  derivation reruns, and current persistence writes reports, proposals and
  admitted review cases into a fresh `current.sqlite3`. No earlier decision or
  projection is copied.

## Running it

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

The selection is canonical JSON with at most 100 unique current report IDs:

```json
{"report_ids":["report_0000000000000000000000000000000000000000000000000000000000000000"],"schema":"provider-replay-selection-v1"}
```

Preconditions:

- The checkout has no tracked or untracked change at the named head. Ignored
  files do not count.
- Inputs and output directories belong to the current owner with no group or
  other access. No ancestor of the configuration is a symlink or a
  group- or world-writable non-sticky directory.
- The database is exact-current and has no sidecars.
- Output directories are empty. Files are created exclusively and never
  overwritten or deleted. After an interrupted or ambiguous run, recover
  privately before using a new directory.

## Outputs

Capture writes `bundle.json` and `bundle.json.sha256`. Replay writes
`current.sqlite3`, `result.json` and `result.json.sha256`. These are private
evidence: never add them to the repository, logs, review input, issues or pull
requests. Standard output is aggregate only; a failure prints one fixed
value-free sentence.

Keep a bundle only while its captured responses are still useful. The command
never deletes one; deleting it is a separate, deliberate private-maintenance
step. Keep an independent copy of anything under
investigation.

## What replay checks

The bundle binds the capture head, configuration and report schemas, provider
name and adapter, canonical request, exact bounded response stream, capture
time and the captured report and proposal digests. Replay also binds its own
head and the resulting database digest.

- A changed or removed provider adapter, a newly enabled provider with no
  captured evidence, or a changed canonical request invalidates only that
  provider for that report, as `provider_contract_changed`,
  `provider_evidence_missing` or `request_contract_changed`. Replay continues
  with the other providers.
- Malformed, oversized, noncanonical, tampered, unsafe or off-endpoint
  evidence fails the whole command.
- At the capture head, any unexplained report or proposal difference fails.

Replay does not qualify the installed artifact, full-library scale, the
browser or semantics; those need their own qualification
([testing strategy](testing-strategy.md)). The synthetic tests in
`tests/unit/test_provider_evidence_replay_qualification.py` cover the format,
sanitiser, hostile-input bounds, invalidation, deterministic derivation and the
zero-network boundary without private material.
