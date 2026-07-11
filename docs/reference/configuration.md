# Configuration Contract

## Principles

- Configuration has a versioned schema and documented defaults.
- Secrets are references or write-only values, not ordinary exported config.
- The dashboard, file, environment, and CLI configure the same typed model.
- Unknown fields fail validation rather than being silently ignored.
- Configuration changes are audited and identify whether restart or
  re-evaluation is required.

## Current schema boundary

The initial `config-v1` model validates an untrusted in-memory mapping before
any application work begins. File, environment, CLI, dashboard, encrypted-store,
and Docker-secret transports must call this same validator; none may introduce
an undocumented environment-variable override. Its canonical export reports
only secret configuration status and logical mount identity, never service
origins, secret references, or private paths.

Import planning accepts current and proposed mappings and validates both through
that same loader before comparing them. Its output contains only fixed schema
paths and the impacts below; it retains no old or new values and performs no
persistence, probing, secret resolution, or mutation.

### `config-v1` impact matrix

Impact identifiers have a fixed order: restart, inventory reconciliation,
evidence invalidation, evidence re-evaluation, candidate re-evaluation, review
re-evaluation, notification reconfiguration, and backup-readiness revalidation.

| Changed schema path | Required impacts |
| --- | --- |
| `instance.instance_name` | restart |
| `connections.adapter` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.endpoint` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.secret_reference` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.enabled_libraries` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.capability_overrides` | restart, inventory reconciliation, evidence, candidate, and review re-evaluation |
| `media_mounts.name`, `.library_type`, `.container_path`, or `.service_path` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `limits.parser_concurrency` | restart |
| `limits.parser_timeout_seconds` or `.temporary_bytes` | restart and evidence re-evaluation |
| `providers` | restart, candidate and review re-evaluation |
| `reviewers` | restart and review re-evaluation |
| `policy.policy_version` | restart and review re-evaluation |
| `notifications` | restart and notification reconfiguration |
| `backups_enabled` | restart, backup-readiness revalidation, and review re-evaluation |

Connection and mount collections are compared by fixed aggregate field paths;
plans never expose a collection index, endpoint, secret reference, library
identifier, or path. A connection endpoint or credential-reference change is
treated conservatively as a possible account, tenant, or target-scope change.
Capability overrides are bounded diagnostic identifiers only and cannot grant
write authority. The closed read-only vocabulary is `inventory-read`,
`metadata-read`, `media-file-read`, `notifications`, `pagination`,
`concurrency-tokens`, and `cover-read`; other values fail validation. The
omitted policy default is `policy-v1`. Provider, reviewer, connection, and
notification ordering is significant in `config-v1`. Enabled-library and
capability-override ordering is not significant and is canonicalized before
comparison.

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

### Evidence extraction

- enabled format and evidence capabilities;
- per-format document, byte, text, node, nesting, duration, and temporary-space
  limits;
- semantic selection and bounded fallback policy versions;
- retained excerpt and diagnostic-artifact limits;
- parser concurrency, timeout, retry, and circuit-breaker bounds; and
- changes that require evidence invalidation and re-extraction.

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
