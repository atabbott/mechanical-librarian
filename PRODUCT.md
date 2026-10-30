# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is a technically capable self-hoster managing one personal or
household ebook and audiobook library. They want new items to become correctly
described and covered without routine manual maintenance, while retaining clear
control over exceptions and any production write.

## Product Purpose

Mechanical Librarian is a self-hosted metadata curator for ebook and audiobook
libraries. It continuously observes supported library services, gathers local
and catalogue evidence, proposes field-level changes, keeps qualified routine
work out of the active human queue, and presents exceptions for an owner
decision.

Success means that most new items become acceptably correct without human
action; unchanged items do not produce repeated changes; failures stop safely;
and every decision or effect remains understandable, auditable, and
recoverable.

## Decision quality and human attention

Optimize for useful metadata and little routine human interaction. Metadata
that is good enough for a person using the library is a successful outcome;
perfect bibliographic certainty is not a prerequisite for every automatic
improvement. Accept reasonable, evidence-supported choices with an explainable
basis and a practical correction path. Evaluate decision policies against both
metadata quality and the human attention they consume.

Reserve human review for material competing evidence or consequential ambiguity
where a person's judgment is likely to improve the result. Human review has a
cost and is not inherently more accurate than a supported deterministic or
reviewer-advised decision. Missing data, an unavailable provider, or residual
uncertainty alone does not establish a conflict or justify an owner choice.
Keep work-level evidence sufficient for work-level fields; require edition
specificity where it materially affects the field being changed.

This tolerance concerns bibliographic uncertainty. Credential, consent,
currentness, backup, supported-write, verification, and recovery boundaries
remain mandatory, as do owner-final decisions and explicit Ask me or Ignore
policy. The [automation policy](docs/product/automation-policy.md) applies this
standard to field-level decisions.

## Positioning

Mechanical Librarian is evidence-first and safety-gated rather than AI-first.
Its distinguishing mechanism is a chain of target-shaped evidence, explainable
field-level decisions, explicit authority, supported-service API effects,
read-back verification, and durable recovery state. AI reviewers are untrusted
advisers: they never receive service credentials or production write
authority.

## Operating Context

The product runs as one self-contained container beside an existing media
library. Audiobookshelf is the first production adapter. The owner works through
one private authenticated browser workspace with Library, Policy, and Activity
as its permanent destinations. Attention is an in-context queue within Library,
and review detail stays attached to the selected work.

New items remain available while curation proceeds asynchronously. Library
keeps all current work discoverable and exposes only unresolved fields that can
benefit from an owner decision; review detail records direct field choices;
Policy governs deterministic decision behavior; and Activity holds consent,
guarded work, history, and recovery.

## Capabilities and Constraints

- Normal deployment is one Linux container, one persistent volume, read-only
  media mounts, and one web port.
- The installed discovery and review pipeline supports Audiobookshelf ebook
  and audiobook libraries. Recording-admitted audiobooks retain their qualified
  field behavior; supported book-level descriptions, genres, series and titles
  also permit owner review while the recording remains unresolved. That
  work-only route grants no automatic, cover or other-field authority. Application metadata and cover
  effects use supported service APIs only.
- Metadata work is field-local, target-shaped, persisted before execution, and
  idempotent. Automatic outcomes must be qualified, deterministic, and visible
  in history.
- Target writes require library-scoped owner consent, backup readiness,
  exact-current plan validation, optimistic concurrency, read-back
  verification, and durable audit and recovery records. Destructive work always
  remains separately authorized and never runs unattended.
- AI reviewers have no credentials or production write access. Unsupported
  capabilities remain unsupported rather than falling back to database edits
  or UI automation.
- Media mounts remain read-only. The product does not replace playback,
  reading, acquisition, user management, or general library management.
- The product is pre-release. It accepts only absent or exact-current persisted
  state and does not migrate or silently interpret earlier development state.
- There is no published production image or supported release yet. Planned
  adapters, formats, reviewer backends, and embedded-file writes are direction,
  not current capability.

## Brand Commitments

The product name is Mechanical Librarian.

## Evidence on Hand

- `README.md` states the current status and the
  present runtime boundary.
- `docs/product/scope.md` records first-release scope, planned extensions,
  explicit non-goals, the user promise, and the measures of success.
- `docs/product/review-experience.md` is the product contract for the owner
  review experience.
- `docs/architecture/` contains the governing architecture, security, privacy,
  and decision records.
- `frontend/` contains the packaged React owner workspace and its executable
  tests. The current implementation is product evidence, not independent proof
  of production adoption.
- No testimonials, customer claims, production benchmarks, or published-release
  claims are available and future work must not fabricate them.

## Product Principles

1. Keep routine qualified work out of the owner's attention; ask only where a
   human decision can improve the outcome.
2. Make every recommendation and effect explainable from bounded evidence and
   retained provenance.
3. Fail closed at credentials, currentness, consent, backup, capability, and
   verification boundaries.
4. Preserve the user's library as the authority: use supported APIs, keep media
   read-only, and never edit service databases directly.
5. Prefer one operable self-hosted system with explicit recovery over hidden
   infrastructure, silent compatibility, or unattended destructive behavior.
