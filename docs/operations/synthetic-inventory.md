# Synthetic Inventory Round Trip

F03 provides a report-only executable path that validates configuration,
constructs one credential-free synthetic target reader, persists one canonical
snapshot, and inspects it through the application service and CLI. It performs
no network request, secret resolution, scheduling, media access, or mutation.

## Clean-checkout demonstration

From the repository root:

```bash
uv sync --frozen --reinstall-package mechanical-librarian
state_dir="$(mktemp -d)"
uv run --frozen --no-editable mechanical-librarian synthetic-inventory \
  --config docs/examples/synthetic-config.json \
  --database "$state_dir/state.sqlite"
uv run --frozen --no-editable mechanical-librarian synthetic-inventory \
  --config docs/examples/synthetic-config.json \
  --database "$state_dir/state.sqlite"
```

The first run reports `persistence=accepted`; subsequent processes report
`persistence=reused`. Every successful run reports:

```text
authority=synthetic-inventory-read-only
scope=synthetic/synthetic-connection/synthetic-library
items=1 history=1 current=1
```

The snapshot fingerprint remains stable. Output never includes the
configuration path, database path, endpoint, secret reference, external item
value, media path, metadata value, or vendor field.

## Validation and failure behavior

The JSON file is bounded to 1 MiB and is decoded as UTF-8. Duplicate keys,
unknown fields, unsupported schema versions, coercion, unsafe limits, writable
mounts, unsupported capabilities, and malformed values fail before target or
database construction. Provider, reviewer, and notification lists must be
empty for this supported synthetic composition.

Adapter, persistence, schema, migration, interruption, and readback failures
return fixed path-free CLI failure text and publish no partial current
observation. Automated tests inject failure after immutable-history insertion
and during migration-3 DDL, verify rollback to usable F02 state, and then prove
a clean retry produces exactly one history and current observation.

## Compatibility and recovery

Migration 3 is forward-only and additive from genuine migration-2 state. It
preserves audit, workflow, stage, checkpoint, transition, attempt, and instance
metadata. Reopening is idempotent. Newer, checksum-tampered, partially applied,
definition-modified, or unrecognized schemas fail closed.

Before upgrading an important state database, make the documented verified
backup. Rollback means stopping the newer binary and restoring that verified
pre-F03 copy; an older binary must not be run against migration-3 state. The
application never edits a target-service database or media file.
