# Domain Model

## Modelling principles

- Preserve raw observations separately from normalized interpretations.
- Model changes per field, not as opaque whole-item replacements.
- Treat evidence, AI decisions, human decisions, and apply outcomes as distinct
  facts.
- Never rewrite history to represent the current state.
- Use stable internal identifiers independent of service titles or paths.
- Include schema and policy versions on durable derived records.

## Core entities

### LibraryConnection

Represents one configured external library service and its declared
capabilities. Secrets are referenced through the secret store and never stored
in ordinary configuration or audit records.

### LibraryItem

Stable internal identity for a service item. It contains the connection ID,
adapter ID, adapter-scoped library ID, external item ID, media type, lifecycle
status, and links to snapshots. It does not duplicate every current metadata
field. External IDs are never assumed globally unique across connections or
libraries.

### ItemSnapshot

Immutable observation of current service state at a point in time. Contains a
canonical fingerprint, normalized fields, file and cover identity, lifecycle,
media type, adapter and service releases, concurrency evidence, relevant
capability state, and observation provenance. `ItemSnapshot.create` derives the
fingerprint from the versioned canonical input; the public constructor rejects
a supplied fingerprint or canonical input that does not describe the same
state.

`InventoryRecord` is the public aggregate boundary for a connection, item, and
snapshot. It rejects cross-adapter, cross-connection, cross-library,
cross-target, cross-snapshot, media, lifecycle, and capability mismatches before
the graph can enter application or persistence code. Adapter implementation
versions and upstream service versions use distinct, adapter-bound values.

### EvidenceRecord

Immutable bounded evidence such as:

- embedded ebook package metadata;
- bounded opening-matter, navigation, TOC, and structural facts;
- audio tags, chapters, or duration;
- future bounded spoken-credit transcript spans and structured claims;
- folder and filename facts;
- service-current values;
- provider results;
- cover dimensions and perceptual identity.

Evidence includes source type, extraction version, item fingerprint, observed
time, confidence facts, warnings, upstream lineage or correlation identity, and
an optional artifact reference. Evidence does not contain an approval decision.
Read-only media-health observations may use the same immutable storage pattern,
but cannot imply that metadata is absent or create a review case by themselves.

All evidence inputs are immutable and bounded before persistence, logging,
reviewer staging, or API projection:

| Value | Maximum |
| --- | ---: |
| controlled code | 64 characters |
| short source, lineage, correlation, or action text | 256 characters |
| one normalized field value | 4,096 characters |
| one confidence-fact value | 1,024 characters |
| ordinary excerpt | 280 characters |
| values in one field | 32 |
| facts in one evidence record | 64 |
| warnings in one evidence record | 32 |
| evidence references on one value | 64 |
| risks on one proposal | 32 |
| notices on one review case | 32 |
| records in one reviewer/readiness projection | 32 |
| one serialized evidence projection | 524,288 UTF-8 bytes |

Text rejects blank or untrimmed values, control characters, line and bidi
controls, noncharacters, and unpaired surrogates. Collections use exact
immutable tuple types and reject duplicates. Ordinary excerpts additionally
reject path-shaped content.

`evidence-persistence-v1`, `reviewer-evidence-v1`, `evidence-api-v1`, and
`evidence-readiness-v1` are distinct strict serialization boundaries. Unknown,
missing, non-canonical, incompatible, malformed, or oversized data fails
closed. Persistence retains the bounded authoritative record. Reviewer and API
details include only controlled projection-safe fact classes and omit lineage,
artifact locations, raw payloads, and target external IDs from diagnostics.

Evidence citations use target-scoped `EvidenceReference` values. Cross-target
use requires both an explicit relationship and an immutable policy version and
rule. Proposals, risks, notices, review cases, decisions through their case,
readiness, and future apply provenance retain references rather than copied
facts.

`EvidenceReadiness` is a compact projection, not workflow orchestration. It
preserves `current`, `missing`, `stale`, `unsupported`, and `approved_skip`
facts while counting unique evidence IDs and target scopes. Approved skip
requires versioned policy evidence; missing and unsupported states cannot
invent evidence.

### Candidate

Normalized provider or derived proposal source. A candidate may support several
field values but must retain its original source identity, provider response
fingerprint, upstream lineage or correlation identity, edition indicators, and
risk facts.

### FieldProposal

One proposed value for one field on one item snapshot. It references supporting
and contradicting evidence and declares whether it fills an empty field,
replaces a populated field, or represents no change.

### ReviewCase

Immutable decision surface for one target snapshot and proposal-set version. It
groups applicable field proposals and typed cover/series choices while
retaining independent field resolution. It references evidence and notices by
ID, distinguishes disposition from any human decision, and exposes apply
eligibility without owning an apply plan. A newer fingerprint or materially
changed proposal set supersedes, but does not delete, the previous case.

### ReviewGroup

Display/query projection connecting confirmed instances of the same work, such
as an ebook and audiobook. It contains link method, confidence, labels, and
ordered sibling case IDs only. It cannot own or propagate selections, human
decisions, readiness, or apply state.

### ReviewNotice

Reviewer-facing interpretation of a risk or diagnostic condition. Its class is
`blocking`, `needs_attention`, `resolved`, or `rejected_alternative`; it records
the affected choice, explanation, required action, and underlying diagnostic
codes. Operational failures retain separate job or health states.

### AIDecision

Immutable reviewer output for a field proposal or action. Includes decision,
reason, problems, cited evidence IDs, reviewer backend, model, prompt version,
policy version, schema version, and execution ID.

### HumanDecision

Explicit user action on a field, item, or destructive proposal. It records the
reviewed case and proposal versions, field selections, action, notes, actor,
mode, and time. It becomes stale if its guarded fingerprint or proposal set
changes. Deferral creates no approval and does not resolve a case.

### DecisionAuthorization

Immutable record explaining why selected changes may enter an apply plan. It
references either a qualified automatic policy disposition or a current human
decision, plus the applicable policy, proposal, case, and fingerprint versions.
AI recommendation alone can never be an authorization.

### ApplyPlan

Validated, immutable set of field changes for one target and one expected item
fingerprint. It records decision authorization, field-level provenance,
capability checks, validation version, idempotency key, and required backup
class.

### BulkDecisionBatch

Auditable batch created from a bounded server-side preview. It records the
filter scope, exact eligible case/proposal versions, exclusions, counts,
idempotency key, actor, and the individual human decisions created at commit.
It is not an apply plan and cannot include destructive work.

### ApplyAttempt

One attempt to execute an apply plan. It records pre-state evidence, request
summary, response summary, timing, outcome, failure classification, and
verification reference. Secrets and full sensitive payloads are excluded.

### VerificationResult

Field-level comparison of expected and observed post-apply state. It records
cover availability checks, warnings, rollback eligibility, and the observed
post-apply fingerprint.

### BackupRecord

Evidence that a required backup exists and is usable. Includes backup type,
scope, creation time, integrity result, retention class, and opaque location.
Locations exposed through user interfaces must be redacted or logical.

### Job

Durable unit of background work. Jobs have explicit input references,
deduplication keys, lease state, attempts, retry classification, and terminal
outcomes. Retry eligibility and suppression outcomes are persisted rather than
inferred from process-local timers.

### AuditEvent

Append-only product event such as discovery, evidence reuse, decision, policy
override, approval, apply, verification, rollback, authentication failure, or
circuit-breaker transition.

## Value objects

- `TargetId`: internal stable identity for one connection-, library-, and
  adapter-scoped external item.
- `ExternalItemId`: adapter-scoped service identity.
- `Fingerprint`: versioned digest of canonical observed state.
- `CanonicalSnapshotInput`: immutable `snapshot-v1` canonical JSON defining
  snapshot state equality and the fingerprint input.
- `ConcurrencyToken`: bounded, opaque upstream stale-state evidence; its value
  is redacted and only a one-way identity enters canonical serialization.
- `FieldName`: controlled vocabulary with adapter mappings.
- `FieldValue`: typed normalized scalar or collection.
- `EvidenceId`, `ProposalId`, `ReviewCaseId`, `DecisionId`, `ApplyPlanId`:
  immutable IDs.
- `PolicyVersion`, `SchemaVersion`, `ExtractorVersion`: reproducibility keys.
- `Risk`: structured code, severity, blocking status, and evidence reference.
- `Disposition`: `auto_apply`, `needs_review`, `closed_rejected`, or
  `unsupported`.

## Invariants

- An apply plan references exactly one expected item fingerprint.
- A field is applied at most once for a given idempotency key.
- An AI decision cannot create a new value not present in its supplied proposal
  set unless the policy explicitly supports a typed derived field.
- A proposal cannot cite evidence from a different target unless the evidence
  relationship is explicit and policy-approved.
- A snapshot's target, provenance, adapter release, service release, media type,
  capability state, canonical input, and fingerprint must agree at construction.
- A connection, item, and snapshot become one inventory record only when their
  adapter, connection, library, target, snapshot, media, lifecycle, and
  capability scopes agree.
- Evidence facts are stored once and referenced by ID; browser projections do
  not become a second source of truth.
- A ReviewGroup cannot propagate a selection, decision, disposition, or apply
  state between siblings.
- A human decision applies only to the exact reviewed case, proposal set, and
  expected fingerprint.
- An apply plan has exactly one current authorization source: a qualified
  automatic policy disposition or an applicable human decision.
- Provider, job, authentication, and other operational failures cannot be
  represented as human metadata decisions.
- Unsupported fields cannot enter an apply plan.
- Destructive actions cannot receive `auto_apply` disposition.
- Verification is mandatory before an apply attempt can become successful.
- Rollback never overwrites state that changed after the failed apply attempt.

The [review-experience contract](../product/review-experience.md) defines the
authoritative user-facing projection of these entities.
