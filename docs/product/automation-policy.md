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
| Title | Fill or correct when work identity is supported by local or catalogue evidence. | Plausible candidates identify different works, editions materially change the title, or the change would discard meaningful subtitle information. |
| Author or authors | Fill, normalize, reorder, or correct when identity evidence supports the people and roles. | Authorship conflicts, contributor roles cannot be mapped safely, or a collective/pseudonym choice is materially ambiguous. |
| Series name | Fill or correct when the work-to-series relationship is supported. | Sources disagree about whether the work belongs to the series or represent different series. |
| Series index | Fill or correct when numeric, decimal, or labelled order is supported. | Reading order and publication order conflict without a selected policy, or the evidence is internally inconsistent. |
| Publication date | Fill or correct from applicable embedded edition evidence; use catalogue evidence when edition applicability is credible. | Only work-level dates are available for an edition-specific field or sources materially conflict. |
| Publisher | Fill or correct from applicable embedded edition evidence or a credible edition match. | Imprint and publisher roles are ambiguous or the evidence appears to refer to another edition. |
| Identifiers | Add valid, typed identifiers and replace malformed or contradicted identifiers when edition evidence supports the change. | A valid current identifier and a candidate identify different editions or identifier type is uncertain. |
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
- the target service's current state;
- catalogue records that can be tied to the same work or edition; and
- corroborating cross-format evidence for the same owned item.

Ambiguity risk facts include generic or very short titles, initial-only or
collapsed author names, narrator/author role confusion, swapped title and
author fields, conflicting series orders, and release-name debris. These facts
raise the evidence required for replacement but do not turn a hard-coded list
or fuzzy-match score into authority. Initial-aware name comparison may support
identity while preserving the full observed and proposed names.

An embedded value is not automatically true, and a catalogue value is not automatically edition-applicable. However, imperfect evidence can still support a non-destructive improvement when the competing explanation is materially less plausible.

Provider outage, timeout, quota exhaustion, authentication failure, and empty malformed response are operational facts. They are never evidence that a candidate or field is absent.

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
