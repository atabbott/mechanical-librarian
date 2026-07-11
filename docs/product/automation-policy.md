# Automation Policy

Mechanical Librarian is designed to make useful, non-destructive decisions without routine human approval. The goal is a small unresolved queue and an accurate audit trail, not perfect certainty before every improvement.

This document defines product intent. Exact thresholds remain versioned configuration and must be qualified through shadow runs before they authorize production writes.

## Operating posture

The initial production profile is **decisive non-destructive**:

- make field-level decisions instead of accepting or rejecting a whole item;
- use embedded file and audio evidence as first-class evidence;
- accept reasonable evidence when credible sources agree and no material contradiction exists;
- automatically reject no-ops, clearly worse candidates, and inapplicable fields;
- send only genuinely unresolved choices to the active human queue;
- keep automatic approvals and rejections visible in history; and
- never automate deletion or another destructive operation.

The reference deployment may accept a measurable correction or override rate in exchange for a much smaller queue. Public defaults must expose this tradeoff plainly and allow a user to begin in shadow mode.

## Availability before curation

Ingestion and consumption do not wait for metadata curation.

1. Audiobookshelf discovers and exposes the new item using whatever metadata is initially available.
2. Mechanical Librarian observes the item after a configurable stability delay.
3. Evidence, review, and safe apply proceed asynchronously.
4. Failures leave the item available with its prior metadata and create an operational or review state as appropriate.

The curator must not quarantine an otherwise usable item merely because metadata is poor or evidence services are unavailable.

## Initial field policy

| Field or action | Intended automatic behavior | Escalate when |
| --- | --- | --- |
| Title | Fill or correct when target and work identity are supported. Recognized format, identifier, and filename noise may be removed when the meaningful title and subtitle are preserved. Meaningful series/order text may be removed only when independently supported separate structured facts are already persisted or emitted as preserved sibling proposals. | Plausible candidates identify different works, an edition-sensitive change lacks applicable edition evidence, or meaningful subtitle or series/order information lacks preserved structured capture. |
| Author or authors | Fill, normalize, reorder, or correct when identity evidence supports the people and roles. | Authorship conflicts, contributor roles cannot be mapped safely, or a collective/pseudonym choice is materially ambiguous. |
| Series name | Fill or correct when the work-to-series relationship is supported. | Sources disagree about whether the work belongs to the series or represent different series. |
| Series index | Fill or correct when numeric, decimal, or labelled order is supported. | Reading order and publication order conflict without a selected policy, or the evidence is internally inconsistent. |
| Publication date | Fill or correct from applicable embedded edition evidence; use catalogue evidence when edition applicability is credible. | Only work-level dates are available for an edition-specific field or sources materially conflict. |
| Publisher | Fill or correct from applicable embedded edition evidence or a credible edition match. | Imprint and publisher roles are ambiguous or the evidence appears to refer to another edition. |
| Identifiers | Add valid, typed identifiers and replace malformed or invalid current values under ordinary applicable field evidence. Supersede a validated current comparable identifier only when an applicable embedded/content identifier and a materially independent provider identifier agree on the same validated canonical replacement identity. Preserve raw observations and lineage. | Conflict identifies another applicable edition, or required validation, edition applicability, materially independent lineage, or agreement on the same canonical replacement identity is missing or unresolved. |
| Cover | Replace when the candidate is clearly more legible, relevant, and visually useful. Exact regional edition matching is not required. | Candidates are similarly good but materially different, work identity is uncertain, or the image is misleading for the content. |
| Description, genres, narrator, and other fields | Preserve initially unless a field-specific policy and adapter capability have passed shadow qualification. Safe empty-field fills may be enabled independently. | Replacement is subjective, provider vocabularies conflict, or evidence does not support field applicability. |
| Duplicate or media removal | Never automatic. | Always requires a separate destructive review and explicit human authorization. |
| Embedded media-file write | Disabled in the first production phase. | Requires a later feature with independent file backup, validation, and rollback policy. |

## Evidence posture

Evidence is evaluated for applicability, independence, and contradiction—not only by a single numerical confidence score.

Agreement is meaningful only when the agreeing observations are materially
independent. Several adapters repeating one upstream catalogue record count as
correlated support, not several independent confirmations. Policy records this
relationship before source agreement can increase automatic-write eligibility.

Preferred evidence includes:

- embedded EPUB package metadata and bounded front matter;
- valid identifiers mined from the file;
- audio container tags, chapters, and duration;
- qualified bounded spoken-credit claims when the optional
  [opening-audio evidence](opening-audio-evidence.md) extension is enabled;
- the target service's current state;
- catalogue records that can be tied to the same work or edition; and
- corroborating cross-format evidence for the same owned item.

Current service metadata is one sourced observation with its own provenance and
version. It is not privileged truth: policy compares it with applicable local
and provider evidence while preserving it for explanation, stale-plan checks,
and rollback.

EPUB package and opening-matter facts are first-class evidence records, not
merely hints supplied to a catalogue or reviewer. Applicable local facts may
establish an edition or support a field proposal without a remote catalogue
match. Package metadata and front matter from the same file can corroborate
internally but retain one correlated file lineage.

Ambiguity risk facts include generic or very short titles, initial-only or
collapsed author names, narrator/author role confusion, swapped title and
author fields, conflicting series orders, and release-name debris. These facts
raise the evidence required for replacement but do not turn a hard-coded list
or fuzzy-match score into authority. Initial-aware name comparison may support
identity while preserving the full observed and proposed names.

An embedded value is not automatically true, and a catalogue value is not automatically edition-applicable. However, imperfect evidence can still support a non-destructive improvement when the competing explanation is materially less plausible.

Provider outage, timeout, quota exhaustion, authentication failure, and empty malformed response are operational facts. They are never evidence that a candidate or field is absent.

Candidate sourcing is accounted for by a versioned manifest derived from the
target, adapter, media, field, and configured provider capabilities. For every
derived lane it records applicability. Applicable lanes record a typed execution
outcome. Only successfully completed applicable lanes record candidate count,
which may be zero, and execution mode `executed` or `reused`; `reused` is valid
only for the exact target scope, matching input fingerprint, compatible
provider, query, configuration, and artifact-contract versions, and a validated
complete expected artifact. A missing derived lane is a coverage failure.
Failed execution is never reusable; failed, stale, partial, or incompatible
execution follows governed retry, backoff, and suppression and cannot be
silently skipped.

### Directional title arbitration

Title normalization is directional. With supported target and work identity,
policy may remove recognized format, identifier, and filename noise when the
removal preserves the meaningful title and subtitle. Before removing meaningful
series/order text, independently supported separate structured series and order
facts must already be persisted or emitted as preserved sibling proposals.
Merely leaving existing structured fields unchanged is insufficient. Edition
identity or applicability is additionally required when the field policy or
proposed change is edition-sensitive.

The inverse is not equivalent evidence: a candidate that adds a token classified
as recognized noise is rejected. Supported meaningful subtitle or structured
series/order information is not noise. A cleanup that deterministically
preserves meaning, or a candidate that deterministically degrades the title,
resolves through policy rather than becoming AI `manual`. Genuine supported
alternatives remain eligible for review.

### Candidate aggregation

When equal normalized candidate values are combined, every source assessment,
lineage, applicability fact, and contradiction remains individually auditable.
The policy engine recomputes the aggregate disposition deterministically from
the current assessments. It neither unions all historical adverse facts nor
allows a strongest-row winner to erase a genuine conflict.

### Identifier arbitration

Identifier ingestion preserves the raw value and declared source type before
validation. Policy validates syntax, canonicalizes recognized aliases and
comparable ISBN forms, and compares values only within compatible namespaces.
Non-comparable identifier types are neutral: neither agreement nor conflict can
be inferred from them. Source lineage and correlation remain attached through
canonicalization.

One distinct checksum-valid ISBN with typed format- and edition-applicable
content provenance can strongly establish an edition only when a materially
independent, format-appropriate provider returns the same canonical ISBN,
including ISBN-10/ISBN-13 equivalence, and the target, work, and media identity
agree. Multiple distinct content ISBNs, unrelated preview or other-work claims,
correlated providers, and edition, format, narrator, abridgement, dramatization,
omnibus, or cross-media conflicts remain unresolved. A spoken ISBN is useful
typed audio evidence but is not equivalent to an embedded/content ISBN for this
rule.

A malformed or invalid current identifier may be replaced under ordinary
applicable field evidence. A validated current comparable identifier may be
classified as stale and superseded only when an applicable embedded or content
identifier and a materially independent provider identifier agree on the same
validated canonical replacement identity. Otherwise the conflict remains
unresolved or the candidate is rejected according to field policy. Resolving an
identifier, title, or any other field cannot erase or implicitly decide sibling
field proposals.

### Series-label reconciliation and projection

A provider series label may be canonicalized only by a qualified, versioned
mapping applicable to the supported work identity and source contract. The
evidence record retains the raw, normalized, and canonical labels, source
lineage, work identity, mapping-rule version, and replay or supersession
relationship.
Unknown or out-of-scope labels remain unchanged and visible; production-learned
author-, title-, or library-specific aliases are not silently hard-coded into
policy.

Shared evidence may be projected to another confirmed target, but a source AI
recommendation travels only as provenance. It is not evidence, independent
agreement, target disposition, human decision, or apply authority. The
destination independently revalidates target and work identity, edition or
field applicability, current state, evidence lineage, contradictions, and the
current policy version before creating or resolving a proposal.

## Supervised report-only continuation

Unattended continuation is allowed only for report-only work already authorized
for a bounded run. Its durable authority fixes the initiating principal and
authorization context, target and operation scope, configuration, policy and
schema versions, resource and time bounds, cancellation behavior, and expiry
before scheduling begins. Every actual transition actor is recorded and linked
to that authority reference. Resume may recover or complete that exact work; it
cannot widen scope, enable apply or destruction, change skills or policy, or
replace the governing authority record.

The application scheduler owns continuation through durable jobs, leases,
fencing, and validated outcomes. Operating-system PIDs, detached watchers,
shell sessions, and tool sessions are diagnostic implementation details only.

## Cover policy

Cover selection optimizes for usefulness rather than exact edition purity:

- correct work identity is mandatory;
- readable title and relevant artwork matter more than regional imprint;
- UK and US variants are ordinarily interchangeable;
- resolution, aspect ratio, crop, artefacts, and text legibility are quality facts;
- a known illustrated/non-illustrated mismatch is a negative relevance fact, not an automatic rejection when no better cover exists; and
- an existing acceptable cover remains when no candidate is clearly better.

A cover change keeps the previous cover as rollback evidence before the target API is called.

## Queue policy

The active queue contains only work that can benefit from a human choice:

- two or more materially plausible candidates;
- evidence contradictions that policy cannot resolve;
- unsupported but potentially actionable target capability;
- high-impact non-destructive changes outside the qualified policy; or
- destructive actions requiring explicit authorization.

Operational failures belong in health and job views, not the metadata decision queue. Automatic rejections are closed by default but remain searchable for audit and quality sampling.

Deterministic title cleanup and degradation outcomes also remain outside the
active queue. AI `manual` is reserved for materially plausible supported
alternatives, not for policy-resolved normalization direction.

## Qualification and monitoring

Before a field policy becomes write-capable:

1. Run it in report-only mode on synthetic and representative fixtures.
2. Run a bounded shadow cohort against real items without applying changes.
3. Review a sample of approvals, rejections, and manual outcomes.
4. Record false-positive, false-negative, correction, override, unresolved, and verification-failure rates.
5. Enable a small canary with strict batch and circuit-breaker limits.
6. Expand only when failures are understood and rollback evidence is complete.

Quality monitoring continues after enablement. A material rise in human overrides, stale plans, verification mismatches, or correlated errors automatically disables the affected policy category while read-only processing continues.

## Policy versioning

Every decision records:

- policy profile and version;
- evidence and candidate versions;
- deterministic rules applied;
- reviewer backend, model/profile, prompt, and schema versions when AI is used;
- resulting disposition; and
- later human override or rollback, if any.

Changing applicability rules, automatic-write eligibility, evidence weighting, or material prompts creates a new policy version and evaluation cohort.
