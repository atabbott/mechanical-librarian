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

**Recording and work review admission (#1166).** Recording admission continues
through the unchanged runtime and chapter corroboration predicates. Independently,
a report can support owner review of description and genres when each contributing
source has both a complete title/full-author work witness and the retained relation
to the target work. Lookup-only candidates cannot supply that witness. Another
source's identity match cannot authorize a candidate's values.

The derived review scope is `recording`, `work`, or absent. Work scope leaves the
recording unresolved, excludes local-only values and every destination other than
description and genres (and, since #1253, series and title; see below) before
combinations are formed, and preserves existing
field safety and election rules. A report without supported work values or an
admitted recording completes as evidence-only. Ignore can leave no case. Counts
retain the same ready/recorded/blocked partition.

Work-only choices use existing durable owner decisions and guarded manual effects.
They create no automatic decisions or effects, actionable cover consideration, or
authored-description advice, including retry and direct selection paths. Automatic
configuration becomes Ask for these supported values; Ignore remains Ignore.
This initial route does not qualify work-only automation. Case/report detail v5
requires nullable scope separately from unchanged recording attribution. Proposal
policy `fixed-conservative-v33` requires a clean rebuild; report, proposal, case
and HumanDecision document shapes remain unchanged.

**Work-level series and title (#1253).** Work scope also answers series, series
position and title, from the same witnessed sources only: local tags and
lookup-only candidates never supply them, a title cleanup is offered only as a
witnessed provider title, and subtitle, cover and every recording field stay
excluded. The duplicate-component and combination checks still run over title,
subtitle and series. A report opens work scope when a witnessed source states a
description, genres or a series, or a title that cleans up the current one. A
cleanup removes or moves no accent or other combining mark the current title
carries, each mark staying on the same letter of the aligned titles, and differs
by more than quote form; any other witnessed title is no choice, and without a
cleanup the title stays masked exactly as before #1253.

A work-scope description or genre choice whose every alternative only trims or
extends the current value, by contiguous words or by genre-set containment,
carries `work_alternative_immaterial`. The bound applies only when the current
value passes the audiobook synopsis and genre floor: a placeholder, a text
holding markup, a visibly cut-off text ending "..." or "--", or a format-only
genre list keeps every alternative material. It stays an owner choice but sets no
`unresolved_choice` fact, so it is not queued unless its policy explicitly asks.
Work-scope fills follow the same shared attention predicate: one held under an
Automatic destination is not queued and reads as `owner_choosable`, while an
explicit Ask queues it. Proposal policy `fixed-conservative-v35` requires a clean
rebuild; document shapes remain unchanged.

**Explicitly absent provider runtime (#1042).** Existing stated-runtime admission
runs first and yields no new attribution. Otherwise one canonical non-target local
ASIN, its retained matched-ASIN evidence, usable local runtime and complete strong
unshifted nonuniform chapter vectors may admit owner review. Every retained matched
candidate is checked for runtime vetoes before deterministic source selection.
Missing duration claims are unknown; exactly one ABSENT claim is required for support.
Malformed, multiple, nonpositive or outside-tight-band runtime statements veto it.
The numerical boundary lives in `complete_chapter_runtime_agreement`; admission and
required-nullable case/report-detail v4 attribution share `recording_identity_basis`.
The source names a retained provider and candidate. Field applicability, owner
choice and all automatic/effect authority remain unchanged. #1004 retains missing
local runtime and remaining discovery-envelope work.

**Complete chapter fallback across discovery origins (#1004).** This amendment
defines F51 activation. Existing
stated-runtime admission runs first unchanged, including its historical local
ASIN behavior. The chapter fallback then covers local-ASIN, target-asserted,
promoted-ASIN and WORK-derived acquisition using ADR-0064's complete numerical
predicate, including its narrowly witnessed missing-local-runtime branch.
Current WORK adapters require stated local runtime to earn promotion; their
missing-runtime results remain lookup hints. The promoted-ASIN path preserves
that existing runtime-qualified flow and cannot promote from chapter or ASIN
presence alone.

Before deterministic support selection, inspect every retained matched
same-ASIN runtime statement, including lookup-only evidence. A supporting book
runtime is exactly one VALID positive value consistent with chapter coverage,
or exactly one explicit ABSENT claim. Missing claims remain unknown. Malformed,
multiple, nonpositive or conflicting statements veto fallback rather than
becoming absence when a candidate is shaped. Exact title and complete author
context, including variant and contributor vetoes, qualify raw candidates on
the new paths before edition evidence is stripped.

Discovery and retry carry transient audio, work title and full work authors;
work context is complete or absent, with absent defaults for unchanged callers.
This context changes neither ASIN HTTP title/author semantics nor lane identity.
WORK lookup hints follow existing local, promoted and asserted ASIN priorities,
using the originating WORK lane's local input references. Deduplication and
reference union preserve the four-ASIN cap, canonical lane order and existing
query, byte and deadline limits. All matched veto evidence survives the request
cap. There is one bounded second pass, no recursion, and no new retry policy.
Retry rebuilds the same context and feed from verified source evidence and
preserves them when filtering lanes; stale or unrebuildable sources still stop
before dispatch.

The shared `recording_identity_basis` retains the existing provider/candidate
attribution shape and states: “Complete chapter timing and structure corroborate
this recording where overall runtime is unavailable.” Lookup-only candidates
remain outside admitted provider-claim projection and cannot themselves create
work or edition alternatives, an identity, or an owner choice. Admitted evidence
passes through the existing report, proposal, case, owner decision and reopen
path. This fallback grants owner review only: field applicability, independent
authority, competing evidence, owner-final/Ask me/Ignore policy and guarded
writes remain unchanged, and no automatic or effect authority is activated.

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

> Revised by #1249: a field being the owner's no longer puts it in the
> attention queue. A recording joins the queue under exactly an ebook's
> conditions, so a settled fill no record decided asks for nothing there,
> though it stays choosable in its case, as an optional, uncounted choice.

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
