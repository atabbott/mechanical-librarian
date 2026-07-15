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
| Evidence extraction | `enabled_formats`, `maximum_document_bytes`, `maximum_text_characters`, and the versioned `epub_package` and `epub_opening` resource profiles; shared parser limits remain under `limits` |
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
| `limits.parser_timeout_seconds` | restart, EPUB package evidence re-extraction, and evidence re-evaluation |
| `limits.temporary_bytes` | restart and evidence re-evaluation |
| Any `inventory` field | restart and inventory reconciliation |
| `evidence.enabled_formats` | restart, evidence invalidation and re-evaluation, candidate and review re-evaluation |
| `evidence.maximum_document_bytes` | restart, EPUB package evidence re-extraction, and evidence re-evaluation |
| `evidence.maximum_text_characters` | restart and evidence re-evaluation |
| Any `evidence.epub_package` numeric field | restart and EPUB package evidence re-extraction |
| Any `evidence.epub_opening` numeric field | restart and EPUB opening evidence re-extraction |
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
67,108,864 bytes, and bound retained text processing to 524,288 characters.
The closed current format vocabulary is `epub`, `m4b`, and `mp3`.

`evidence.epub_package` is the closed `epub-package-limits-v1` profile. Its
numeric values are strict integers and may be lowered for an installation, or
raised only as far as the immutable implementation caps shown below. Zero,
negative, coerced, unknown, and over-cap values fail configuration validation.
Changing any value requires restart and produces the explicit
`reextract_evidence` impact; an older extraction is not silently treated as
having run under the new profile.

| Field | Default | Immutable hard cap |
| --- | ---: | ---: |
| `source_archive_bytes` | 268,435,456 | 536,870,912 |
| `archive_entries` | 2,048 | 8,192 |
| `compressed_bytes_per_member` | 268,435,456 | 536,870,912 |
| `expanded_bytes_per_member` | 268,435,456 | 536,870,912 |
| `expanded_bytes_per_archive` | 1,073,741,824 | 2,147,483,648 |
| `compression_ratio_per_member` | 200 | 1,000 |
| `container_xml_bytes` | 65,536 | 1,048,576 |
| `opf_package_bytes` | 4,194,304 | 16,777,216 |
| `xml_nodes_per_document` | 50,000 | 200,000 |
| `xml_attributes_per_document` | 100,000 | 400,000 |
| `attributes_per_element` | 64 | 256 |
| `xml_depth` | 64 | 128 |
| `decoded_xml_text_characters` | 262,144 | 4,194,304 |
| `metadata_nodes` | 512 | 2,048 |
| `manifest_items` | 4,096 | 8,192 |
| `spine_items` | 4,096 | 8,192 |
| `guide_references` | 256 | 512 |
| `prefix_mappings` | 64 | 256 |

The profile additionally fixes a 1,024-byte UTF-8 whole-member-name cap, a
255-byte UTF-8 component-name cap, archive nesting depth one, 256 total
observations, 32 values per controlled field, 32 warnings, 16,384 Unicode scalar
values per raw or normalized observation, and 524,288 UTF-8 bytes
per public report. These constants are not configurable.

`evidence.epub_opening` is the closed `epub-opening-limits-v1` profile. It is
composed with the same effective `epub_package` profile; changing either
invalidates compatible opening extraction. Strict integer fields may be
lowered, or raised only to these immutable caps. The CPU, termination-grace,
fact, warning, excerpt, and report values are fixed literals.

| Field | Default | Immutable hard cap |
| --- | ---: | ---: |
| `selected_documents` | 5 | 8 |
| `expanded_bytes_per_resource` | 1,048,576 | 4,194,304 |
| `expanded_bytes_per_selection` | 5,242,880 | 33,554,432 |
| `text_characters_per_selection` | 524,288 | 2,097,152 |
| `nodes_per_resource` | 25,000 | 100,000 |
| `nodes_per_selection` | 100,000 | 400,000 |
| `document_depth` | 64 | 128 |
| `navigation_links` | 2,048 | 8,192 |
| `wall_seconds` | 60 | 3,600 |
| `child_cpu_seconds` | 30 | 30 |
| `termination_grace_seconds` | 2 | 2 |
| `facts` | 64 | 64 |
| `warnings` | 32 | 32 |
| `excerpt_characters` | 280 | 280 |
| `report_bytes` | 524,288 | 524,288 |

The shared `maximum_document_bytes` and `maximum_text_characters` values lower
the effective archive and opening text ceilings respectively. They never raise
a capability profile. F07a does not use retention configuration because it
retains no decoded or diagnostic markup artifact.

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

### F09b proof-of-concept boundary

F09b does not extend `config-v1`. Its separate
`hardcover-title-proposal-report` command accepts only a database path, optional
absolute secret-file reference, and output format. The fixed endpoint, query,
operation, headers, transport bounds, and generated title are not configurable.
Omitting the reference records `provider_disabled` without secret access, DNS,
or egress. There is no hidden environment-variable, encrypted-store, provider
registry, or generic egress configuration. Current `config-v1` continues to
reject detailed Hardcover and provider-secret fields as unknown.

The live-compatible command and transport/parser contract is
[documented separately](hardcover-f09b-contract.md). F09f's future production
provider must define its own versioned structured configuration, persisted
work, freshness/reuse, and restart behavior rather than turning this proof-of-
concept CLI into an implicit schema extension.

### Planned F09f configuration boundary

F09f will extend the documented versioned configuration contract rather than
reuse F09b's command-only secret reference or add hidden environment behavior.
Before F09f can move from Triaged to Ready, its exact configuration fields,
defaults, validation, compatibility identity, and migration impact must be
frozen together with its external query/authentication and response contract.
This plan does not add a current `config-v1` field or define that future schema.

The frozen F09f configuration must be disabled by default and identify
credentials only by a validated secret reference. Configuration loading may
validate the reference shape, but secret resolution occurs only for enabled
provider work after the application-built bounded query input has passed
validation.
Disabled composition constructs no provider adapter or client, resolves no
secret, and performs no DNS or egress. Tokens, secret values, and secret paths
are never configuration exports, persisted provider observations, logs,
diagnostics, audit fields, or report values.

The configured operation must allowlist only the application-built bounded
ISBN-and-title lookup, supported provider capability, expected host, and finite
transport/work policy. It cannot accept raw query text, arbitrary endpoints,
target rows, library identifiers, paths, media content, provider payloads, or
write capability. Changing any compatibility-relevant provider configuration
invalidates reuse rather than reinterpreting existing F09b or F09f history.

### AI reviewers

Structured reviewer authentication references are likewise planned beyond the
current aggregate `reviewers` impact path:

- backend type;
- model or profile selection;
- authentication state reference;
- timeout and concurrency;
- allowed prompt, evidence, and output schema versions;
- shadow or production eligibility.

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
