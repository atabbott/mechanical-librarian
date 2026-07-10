# Configuration Contract

## Principles

- Configuration has a versioned schema and documented defaults.
- Secrets are references or write-only values, not ordinary exported config.
- The dashboard, file, environment, and CLI configure the same typed model.
- Unknown fields fail validation rather than being silently ignored.
- Configuration changes are audited and identify whether restart or
  re-evaluation is required.

## Configuration groups

### Instance

- instance name;
- public base URL;
- timezone and locale;
- data and temporary storage limits.

### Library connections

- adapter type;
- base URL;
- secret reference;
- enabled libraries;
- event and reconciliation behavior;
- capability overrides only when explicitly supported for diagnosis.

### Media mounts

- logical library type;
- container path;
- service path mapping;
- enabled evidence types;
- read-only requirement.

### Providers

- enabled providers and order;
- credentials by secret reference;
- timeouts, retry policy, rate limits, and cache policy;
- allowed hosts.

### AI reviewers

- backend type;
- model or profile selection;
- authentication state reference;
- timeout and concurrency;
- allowed prompt, evidence, and output schema versions;
- shadow or production eligibility.

### Policy

- field-specific automatic decision and apply rules;
- destructive-action prohibition;
- stability delay;
- batch and circuit-breaker bounds;
- human review and re-evaluation behavior.

### Backups and retention

- required backup classes;
- freshness and retention;
- artifact and cache limits;
- optional offsite target reference.

### Notifications

- adapters and secret references;
- immediate alert categories;
- digest schedule.

## Secret handling

Support Docker secret files and an encrypted application secret store. Plain
environment variables may be supported for compatibility but should not be the
preferred documented production path.

APIs return only whether a secret is configured and its last successful test;
they never return the value.

## Export and import

Configuration export excludes secrets and machine-specific runtime state. It
includes schema version and validation metadata. Import provides a dry-run diff
and refuses incompatible or unknown fields.
