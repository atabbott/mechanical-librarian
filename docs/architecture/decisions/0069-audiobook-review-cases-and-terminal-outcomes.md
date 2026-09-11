# ADR-0069: Audiobook review cases and the caseless-complete terminal

- Status: accepted
- Date: 2026-09-03
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR-0061 admitted keyless audiobook providers, and ADR-0064 set the runtime
corroboration band that decides whether one provider candidate is this exact
recording. Together they produce retained evidence: an admitted ASIN, a running
time, a narrator cast, a marketplace.

Nothing consumed it. Preparation for an audiobook completed by publishing its
report and then failing with the code `evidence_only`, which reused the failure
vocabulary to describe an outcome that is not a failure. Two consequences
followed. The owner's library counted every prepared audiobook as blocked work
beside genuine provider faults. And the evaluator ran over a recording's report
producing nothing at all, so an owner who could see the evidence in the report
panel could not act on any of it.

Two further facts were found while shaping this decision. A narrator claim is
stated at `ClaimScope.WORK` by every source, because that is the scope
`claim_scope` gives `ClaimKind.CONTRIBUTOR`, while the proposal path read
narrators at `ClaimScope.EDITION` -- so the narrators destination observed
nothing whatsoever, for ebooks as well as recordings. And the proposal path
kept a second, narrower ASIN grammar (`^B[0-9A-Z]{9}$`) than the one ADR-0061
clause 4 admits, refusing ISBN-10-shaped ASINs the report layer had already
accepted (#957).

## Decision

**One terminal vocabulary.** `evidence_only` leaves the failure-code vocabulary
entirely and becomes a completion `outcome` on `review_preparation_work`,
alongside `case` and `no_case`. A completed work names the outcome it completed
on; the schema holds `(state='complete') = (outcome IS NOT NULL)` and binds each
outcome to the artefact it claims. A scan partitions completed work into `ready`
(a case is waiting for the owner) and `recorded` (nothing is being asked), so
`checked = ready + recorded + blocked` still holds and no audiobook is counted
as blocked. This resolves #940 finding 3: an ebook that completes on `no_case`
moves from `ready` to `recorded` with it, because it is asking nothing either.

**Proposal formation behind the corroboration band.** Preparation forms
audiobook proposals only when ADR-0061's runtime corroboration band admits some
candidate the report retains: `admitted_audiobook_identity` recomputes
`runtime_corroborated_candidate` over each retained observation's candidates and
the report's own local claims, which is deterministic and reads no candidate's
shape. Nothing else admits. A work-shape retention does not: qualification keeps
a title-and-author match rather than dropping it, and naming a work is not
naming this product. The item's own ASIN does not either: an embedded, sidecar,
or path identifier naming a different cut of the same work is the disagreement
the band exists to settle, and the 0.20.0 measurement settled such
disagreements through the band rather than around it. A report with no admitted
identity has nothing to compare a proposal against, so its evidence is recorded
and the work completes in the transaction that published the report.

**One evaluator.** An admitted recording runs through the existing election
rules unchanged. Audio-specific behaviour is confined to four localised facts: a
`local:selected_audio` correlation label, `media_kind` bound into the proposal
set's identity, the cover destination held `INELIGIBLE` with
`audiobook_cover_withheld`, and a narrator corroboration guard. That guard turns
a `PROPOSE_CHANGE` supported by exactly one provider, over a recording whose own
tags name no narrator, into `UNRESOLVED_CHOICE` on the existing
`insufficient_independent_corroboration` reason: who read a recording is the
fact an audiobook item is most often wrong about, and one source is not
agreement. No policy version is bumped for it.

**Narrator scope corrected; one ASIN grammar.** The proposal path reads narrator
claims at `ClaimScope.WORK`, where every source states them, and projects them
as `EDITION` evidence, because who read this recording is a fact about the
recording. `_eligible_identifier` asks `canonical_asin(value) is not None`, the
same question the report layer answers, and a provider ASIN is manifestation
evidence when its EDITION-scope claim sits on an audiobook-authority candidate
that carries retained EDITION-scope marketplace evidence -- which the report
retains only for a corroborated candidate, or for a lane built from the item's
own ASIN, where the candidate is this recording's by construction (ADR-0061).
Reading it reuses that admission rather than repeating it.

**Recorded, not applied.** F47 owns audiobook writes. A `HumanDecision` formed
against an audiobook case carries `application='recorded_only'`, bound into its
request digest and therefore into its identity. The guarded application planner
refuses such a decision by name, the continuous scheduler never plans one, and
the review history states `recorded, not applied — audiobook writes arrive with
F47` where the owner reads it. Nothing automatic is ever offered a recording:
the shadow projection abstains before it reads policy, so no shadow outcome
exists for an automatic decision to be minted from.

> Superseded by
> [ADR-0073](0073-audiobook-decision-qualification.md): a recording's field may
> now be decided automatically, but only under an F46 record that qualified
> that exact audiobook identity. Every destination no record names is still the
> owner's, by the same abstention.

## Consequences

### Positive

- Every prepared audiobook ends in a first-class completed state, and the
  library stops reporting recordings as blocked work.
- An admitted recording is a review case: narrators, series and sequence, ASIN,
  description, language, title and authors are decisions the owner can make,
  through the same rules and the same surfaces an ebook uses.
- The narrators destination observes evidence for the first time, on both media.
- One ASIN grammar spans the report and proposal paths (#957).

### Negative

- The proposal-set document gains `media_kind`, so every proposal-set identity
  changes and the schema moves to `proposal-set-v6`.
- `HumanDecision` gains `application`, so the decision document moves to
  `human-decision-v2` and every decision identity changes with it.
- The narrator scope correction moves EPUB narrator results too. Replayed over
  the retained ebook corpus, every other destination is unchanged and 2.1% of
  reports move on narrators alone, always away from action: an automatic
  proposal becomes a choice or nothing. No EPUB surface changes, because
  narrators is already omitted from the EPUB field inventory and from the EPUB
  attention count.

### Neutral or deferred

- Audiobook cover election stays withheld; the field is locked behind provider
  recovery regardless of media.
- Subtitle is withheld for a recording and never forms: a manifestation claim
  needs an `ebook`-typed applicability no audiobook authority states, and the
  subtitle an audiobook store carries is retail copy rather than a typed
  bibliographic subtitle. Deciding one waits on typed audiobook subtitles.
- Duration and chapter structure remain evidence and never become destinations.
- The narrators destination is not write-capable on either medium. A cast is
  decided and recorded, and the field is withheld from the guarded-effect
  destination set until F47 brings audiobook writes, so nothing plans, consents
  to, or validates a narrator write in the meantime.
- Audiobook writes, and the target-side capability they need, are F47's.

## Alternatives considered

### Keep `evidence_only` as a failure code and filter it downstream

Every consumer would have to remember the exception, and one that forgot would
report a recording as a fault. The filtering had already spread to five
different queries and projections before this decision was taken.

### Infer admission from the shape of a retained candidate

Cheaper, and wrong: a retained candidate is evidence the report carries, not a
statement that this recording is the work the provider named. Replaying the
qualification is what makes the answer the one the pipeline would have given.

### Give audiobooks their own evaluator

It would duplicate every election rule and let the two drift. The rules are
about evidence, not about containers.

## Validation

- The terminal vocabulary is held equal between the Python frozensets, the
  SQLite `CHECK` constraints, and the exact-schema object contract.
- An offline replay over retained production reports exercises the election
  rules on real audiobook evidence, and an ebook replay at base and at head
  proves the ebook outcomes are unchanged apart from version strings.
- The restart validation of the #954 class is re-run against the candidate.
