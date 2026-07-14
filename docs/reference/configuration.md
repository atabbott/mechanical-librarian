# Configuration Contract

## Principles

- Configuration has a versioned schema and documented defaults.
- Secrets are references or write-only values, not ordinary exported config.
- The dashboard, file, environment, and CLI configure the same typed model.
- Unknown fields fail validation rather than being silently ignored.
- Configuration changes are audited and identify whether restart or
  re-evaluation is required.
- The current target connection credential is a scoped secret reference.
  Structured provider and reviewer credential references and role-scoped
  resolution are planned and must never share credentials implicitly by
  endpoint or adapter type.

## Current schema boundary

The initial `config-v1` model uses a strict Pydantic v2 transport schema and
maps validated data into immutable application and domain values before any
application work begins. The currently supported transport is one bounded
UTF-8 JSON file. It rejects duplicate keys, coercion, unknown fields, malformed
JSON and oversized input. Future environment, CLI, dashboard, encrypted-store,
and Docker-secret transports must call this same validator; none may introduce
an undocumented environment-variable override. Its canonical export reports
only secret configuration status and logical mount identity, never service
origins, secret references, or private paths.

The generated schema is committed as
[`config-v1.schema.json`](config-v1.schema.json). Regenerate it with
`PYTHONPATH=src uv run --frozen python scripts/generate_configuration_schema.py`;
the test suite fails if the generated artifact drifts.

Import planning accepts current and proposed mappings and validates both through
that same loader before comparing them. Its output contains only fixed schema
paths and the impacts below; it retains no old or new values and performs no
persistence, probing, secret resolution, or mutation.

In current `config-v1`, only `connections.secret_reference` is a structured
credential reference. The `providers` and `reviewers` paths participate in
aggregate impact detection but do not yet define structured credential-reference
or role-scoped resolution contracts. The detailed provider/reviewer secret
contracts below are planned extensions, not current behavior.

### Current group coverage

The bounded pre-release `config-v1` model covers every accepted configuration
group with the following current fields. The broader capabilities described in
later group sections are product direction unless named here; they do not imply
hidden fields or environment behavior.

| Group | Current fields |
| --- | --- |
| Instance | `instance_name` |
| Library connections | `id`, `adapter`, `endpoint`, `secret_reference`, `enabled_libraries`, `capability_overrides` |
| Media mounts | `name`, `library_type`, `container_path`, `service_path`, `read_only` |
| Inventory traversal and polling | `page_size`, `max_pages`, `max_items`, `max_response_bytes`, `timeout_seconds`, `stability_delay_seconds`, `poll_generation`, `max_attempts`, `max_elapsed_seconds`, `initial_backoff_seconds`, `max_backoff_seconds`, `connect_timeout_seconds`, `read_timeout_seconds`, `max_in_flight_requests` |
| Evidence extraction | `enabled_formats`, `maximum_document_bytes`, `maximum_text_characters`; shared parser limits remain under `limits` |
| Providers | ordered, unique provider identifiers; an empty list disables providers |
| Reviewers | ordered, unique reviewer identifiers; an empty list disables reviewers |
| Policy | `policy_version` |
| Backups and retention | `backups_enabled`, `evidence_days`, `job_log_days`, `maximum_artifact_bytes` |
| Notifications | ordered, unique notification adapter identifiers |

Public base URL, timezone/locale overrides, connection event policy, per-mount
evidence selection, structured provider/reviewer authentication, detailed
policy rules, notification schedules, and offsite backup targets are planned
extensions. They are deliberately rejected as unknown fields today.

### `config-v1` impact matrix

Impact identifiers have a fixed order: restart, inventory reconciliation,
evidence invalidation, evidence re-evaluation, candidate re-evaluation, review
re-evaluation, notification reconfiguration, and backup-readiness revalidation.

| Changed schema path | Required impacts |
| --- | --- |
| `instance.instance_name` | restart |
| `connections.id` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.adapter` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.endpoint` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.secret_reference` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.enabled_libraries` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `connections.capability_overrides` | restart, inventory reconciliation, evidence, candidate, and review re-evaluation |
| `media_mounts.name`, `.library_type`, `.container_path`, or `.service_path` | restart, inventory reconciliation, evidence invalidation, candidate and review re-evaluation |
| `limits.parser_concurrency` | restart |
| `limits.parser_timeout_seconds` or `.temporary_bytes` | restart and evidence re-evaluation |
| Any `inventory` field | restart and inventory reconciliation |
| `evidence.enabled_formats` | restart, evidence invalidation and re-evaluation, candidate and review re-evaluation |
| `evidence.maximum_document_bytes` or `.maximum_text_characters` | restart and evidence re-evaluation |
| `providers` | restart, candidate and review re-evaluation |
| `reviewers` | restart and review re-evaluation |
| `policy.policy_version` | restart and review re-evaluation |
| `notifications` | restart and notification reconfiguration |
| `backups_enabled` | restart, backup-readiness revalidation, and review re-evaluation |
| `retention.evidence_days`, `.job_log_days`, or `.maximum_artifact_bytes` | restart |

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

Enabled-library values are opaque, adapter-scoped service identifiers. They
accept the existing bounded lowercase identifier form and canonical lowercase
UUIDv4 values; they are preserved exactly and are never exposed in change-plan
diagnostics. An adapter may narrow that generic grammar to its documented
service identity; Audiobookshelf connections require canonical lowercase
UUIDv4 library identifiers.

Inventory traversal defaults to a page size of 100, 512 cumulative pages,
10,000 cumulative items, 67,108,864 cumulative response bytes, a 300-second
deadline, and no stability delay. `max_pages` and `max_items` are at least two
because F05a requires two complete equal passes. A bound failure publishes
neither partial current state nor disappearance.

Durable polling defaults to three attempts within 900 elapsed seconds, with
5-second exponential backoff capped at 300 seconds. Connect and read timeouts
default to 10 and 30 seconds. Inventory transport concurrency is fixed at one
in-flight request. Attempt, elapsed, page, item, and byte consumption is
persisted cumulatively and does not reset after restart or duplicate delivery.
`poll_generation` defaults to zero and is the explicit operator-controlled
governing revision: increment it after a credential or authority change when a
previous authentication, authorization, configuration, or compatibility
failure is intentionally eligible for a new trigger. No credential material or
secret-reference digest is stored in poll state.

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

The connection describes a target-service role, not a metadata provider. Raw
adapter payload fields and vendor semantics stay at the adapter boundary. An
external item value crosses only as an uninterpreted `ExternalItemId` inside its
fully adapter/connection/library-scoped canonical `TargetId`, never alone;
endpoints and private paths do not enter canonical inventory or downstream
schemas.

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

Current defaults enable `epub`, `m4b`, and `mp3`, bound one document to
67,108,864 bytes, and bound retained text processing to 262,144 characters.
The closed current format vocabulary is `epub`, `m4b`, and `mp3`.

### Providers

The structured provider group is planned beyond the current aggregate
`providers` impact path:

- enabled providers and order;
- credentials by secret reference;
- timeouts, retry policy, rate limits, and cache policy;
- allowed hosts;
- declared media, field, query, and evidence capabilities; and
- independently versioned query, normalization, and response contracts.

Configuration allowlists query capabilities and bounds; it does not contain a
raw target row or reusable provider request. The application builds each minimal
bounded `ProviderQuery` from canonical allowlisted facts at execution time.

For F09b, a versioned schema extension is planned to add one structured
Hardcover provider configuration. It is disabled by default and contains only
its enabled state, a provider-scoped secret reference, and bounded transport
controls where the fixed adapter policy permits them. The endpoint remains
`https://api.hardcover.app/v1/graphql`, the method remains POST, and the adapter
owns one fixed read-only GraphQL query. Arbitrary endpoints, query text,
operation names, headers, or redirect policy are not configuration. Enabling,
disabling, changing the secret reference, or changing a permitted bound
requires restart and candidate and review re-evaluation; it never grants write
authority. F09b exposes no raw response-cache setting.

While qualification uses only the fake transport, the configured Hardcover API
revision is `experimental`. Moving it to `tested` or `supported` requires the
separate opt-in synthetic-account release qualification defined by the testing
and compatibility contracts; ordinary CI and the experimental preview require
no live token.

The supported F09b contract must freeze the exact outbound field allowlist and
per-field disclosure purpose before F09b can be marked Ready or implementation
begins. That allowlist is adapter policy, not operator configuration, and is not
invented by this planning contract. Permitted transport controls remain finite
and may only tighten or select within the supported request-byte, response,
nesting, text, result-count, deadline, single-request concurrency, attempt, and
retry-backoff bounds; they cannot widen the fixed origin, path, method, query,
or field list.

Until that extension and its generated schema are delivered, current
`config-v1` still accepts only the aggregate ordered provider identifiers above
and rejects detailed Hardcover or provider-secret fields as unknown. This
document does not define a hidden environment-variable or runtime shortcut.

Under the planned provider contract, configuration state, declared capability,
connection availability, execution outcome, candidate count, and evidence
assessment are orthogonal. An entry may be absent or explicitly disabled
without creating a failed job; unsupported, unavailable, degraded, failed, and
successful-empty are distinct typed scoped states. A provider-disabled
configuration is valid for report-only inventory, local evidence, deterministic
policy, bounded proposals, and human review under the broader 1.0 sufficiency
model. In the narrower 0.2 title technical preview, provider-disabled state
retains inventory and F06 evidence but produces a typed zero-proposal set;
composition constructs no adapter or client, resolves no provider secret, and
performs no DNS lookup or egress.

### AI reviewers

Structured reviewer authentication references are likewise planned beyond the
current aggregate `reviewers` impact path:

- backend type;
- model or profile selection;
- authentication state reference;
- timeout and concurrency;
- allowed prompt, evidence, and output schema versions;
- shadow or production eligibility.

AI reviewer configuration and invocation are 1.0 work and do not enter the 0.2
title technical preview.

### Policy

- field-specific automatic decision and apply rules;
- versioned evidence sufficiency rules keyed by field, media type, operation,
  and change class—including fill-empty, replace-populated, no-op, rejection,
  and edition-sensitive work—stating when current/local evidence suffices,
  independent provider evidence is required, or missing provider evidence
  routes to review or unsupported;
- destructive-action prohibition;
- stability delay;
- batch and circuit-breaker bounds;
- human review and re-evaluation behavior.

### Backups and retention

- required backup classes;
- freshness and retention;
- artifact and cache limits;
- optional offsite target reference.

The current model exposes `backups_enabled` plus bounded `retention` values for
evidence days, job-log days, and maximum retained artifact bytes. Their defaults
are 90 days, 30 days, and 1,073,741,824 bytes respectively. Backup execution
and deletion workflows remain outside F03.
Backups enabled defaults to true.

### Notifications

- adapters and secret references;
- immediate alert categories;
- digest schedule.

## Secret handling

Support Docker secret files and an encrypted application secret store. Plain
environment variables may be supported for compatibility but should not be the
preferred documented production path.

Current APIs expose only target connection secret status where supported and
never return the value. Planned provider/reviewer status surfaces follow the
same rule after their structured credential contracts exist.

The planned secret-resolution contract produces role-scoped handles: a target-
service adapter cannot resolve provider or reviewer secrets, a provider adapter
cannot resolve target or reviewer secrets, and a reviewer backend cannot resolve
target or provider secrets. Canonical exports preserve only typed configuration
status, never a credential value or reusable cross-role handle.

## Export and import

Configuration export excludes secrets and machine-specific runtime state. It
includes schema version and validation metadata. Import provides a dry-run diff
and refuses incompatible or unknown fields.
