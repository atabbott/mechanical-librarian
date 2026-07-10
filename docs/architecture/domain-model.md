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

Stable internal identity for a service item. It contains the adapter ID,
external target ID, library type, lifecycle status, and links to snapshots. It
does not duplicate every current metadata field.

### ItemSnapshot

Immutable observation of current service state at a point in time. Contains a
canonical fingerprint, normalized fields, file references, cover reference,
service version, and observation provenance.

### EvidenceRecord

Immutable bounded evidence such as:

- embedded ebook metadata;
- front matter or TOC facts;
- audio tags, chapters, or duration;
- folder and filename facts;
- service-current values;
- provider results;
- cover dimensions and perceptual identity.

Evidence includes source type, extraction version, item fingerprint, observed
time, confidence facts, warnings, and an optional artifact reference. Evidence
does not contain an approval decision.

### Candidate

Normalized provider or derived proposal source. A candidate may support several
field values but must retain its original source identity, provider response
fingerprint, edition indicators, and risk facts.

### FieldProposal

One proposed value for one field on one item snapshot. It references supporting
and contradicting evidence and declares whether it fills an empty field,
replaces a populated field, or represents no change.

### ReviewCase

The current decision surface for one target. It groups applicable field
proposals and cover choices while retaining independent field resolution. A
newer item fingerprint supersedes, but does not delete, the previous case.

### AIDecision

Immutable reviewer output for a field proposal or action. Includes decision,
reason, problems, cited evidence IDs, reviewer backend, model, prompt version,
policy version, schema version, and execution ID.

### HumanDecision

Explicit user action on a field, item, or destructive proposal. It records the
reviewed proposal version and becomes stale if its guarded fingerprint or
proposal changes.

### ApplyPlan

Validated, immutable set of field changes for one target and one expected item
fingerprint. It records decision provenance, capability checks, validation
version, idempotency key, and required backup class.

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
outcomes.

### AuditEvent

Append-only product event such as discovery, evidence reuse, decision, policy
override, approval, apply, verification, rollback, authentication failure, or
circuit-breaker transition.

## Value objects

- `TargetId`: internal stable item identity.
- `ExternalItemId`: adapter-scoped service identity.
- `Fingerprint`: versioned digest of canonical observed state.
- `FieldName`: controlled vocabulary with adapter mappings.
- `FieldValue`: typed normalized scalar or collection.
- `EvidenceId`, `ProposalId`, `DecisionId`, `ApplyPlanId`: immutable IDs.
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
- Unsupported fields cannot enter an apply plan.
- Destructive actions cannot receive `auto_apply` disposition.
- Verification is mandatory before an apply attempt can become successful.
- Rollback never overwrites state that changed after the failed apply attempt.
