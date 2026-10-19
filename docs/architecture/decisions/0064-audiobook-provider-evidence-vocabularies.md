# ADR-0064: Audiobook provider evidence vocabularies

- Status: accepted
- Date: 2026-08-30
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR-0061 admits four keyless audiobook providers and makes ASIN a
marketplace-scoped identifier. ADR-0063 stops an audiobook report at local
extraction with the terminal code `local_evidence_only`, and refuses a provider
observation on an audio report because none could exist yet.

The retained report's closed vocabularies were written for ebook evidence.
Nothing in them names an ASIN lookup, the marketplace it resolved in, or the
structure of a recording — and an audiobook's identity is carried mostly by
that structure: an ASIN is present on some local surface for 16.9% of the
maintainer's reference library, and the asserted and embedded values disagree
on roughly 17% of the overlap.

The report document is capped at 2 MiB (`MAX_REPORT_BYTES`), which is the
forcing bound on every representation below. The corroboration tolerances
themselves are unmeasured: ADR-0061 explicitly deferred them to slice-2
qualification.

## Decision

1. **`ASIN` joins the query-lane vocabulary.** `ProviderQueryKind` gains
   `ASIN`, and one canonical ASIN is one lane, because one ASIN is one product
   lookup. Validation is canonical form only — ten upper-case ASCII
   alphanumerics — with no `B0` shape filter: 17 of the reference library's 103
   known ASINs are ISBN-10-shaped and every one is genuine. An audiobook plan
   builds ASIN lanes from the local ASIN claims, and falls back to the
   title-and-author `WORK` lane when the item names no identifier at all, which
   is 57.6% of the reference library.
   Since #1255 that fallback may carry one conditional alternative `WORK`
   lane, from the item's first surviving discovery title, which only Audible
   dispatches and only after the asserted lane ends `NO_MATCH` (or alone, when
   the asserted title collapses to a series name the target records).
   Collapse also covers an asserted title with no query rung at all, such as a
   bare `Part II`, which no provider could be asked. It is band-gated: a candidate it keeps must admit against the alternative title
   and be runtime-corroborated, and an admitted but uncorroborated rung counts
   as refused. #1246 found a tight product admitted against an alternative for
   28 items, 14 of them runtime-corroborated with the alternative as lookup
   title and 0 against the asserted title.
2. **The resolving marketplace is provenance, not a constant.** A new
   `ProviderEvidenceKind.ASIN_MARKETPLACE` records which marketplace an ASIN
   lookup resolved in, drawn from the closed set `us, uk, de, fr, au, in, ca,
   jp, it, es`. 27 of 103 known ASINs resolve outside the US, so an ASIN
   without its marketplace names no identity.
3. **Chapter structure is one bounded evidence value.** A new
   `ProviderEvidenceKind.CHAPTER_STRUCTURE` carries a chapter count, a total
   runtime in whole seconds, and a per-chapter duration vector: whole-second
   integers, capped at 1,024 entries — the same ceiling ADR-0063 puts on
   embedded chapters — with an explicit overflow flag, and at most one such
   value per candidate. Whole seconds because that is the coarsest granularity
   any admitted provider states; the cap and the single-value rule because the
   2 MiB document is the bound that decides whether evidence can be published
   at all.
4. **Duration comparison is a band, not equality.** A new
   `ComparisonMethod.DURATION_BAND_AGREEMENT` applies to `DURATION` claims:
   two running times within 90 seconds are agreement. Providers publish
   minutes and containers publish milliseconds, so equality would report
   disagreement between two correct statements of one recording. Only an
   audiobook provider states a `DURATION` claim, so no ebook comparison
   changes.
5. **Corroboration is a closed reason vocabulary with named parameters.**
   `domain/audiobook_corroboration.py` holds three pure rules — runtime bands,
   chapter-vector alignment, and cover perceptual-hash distance — each
   returning one closed outcome with the exact reasons that produced it,
   following the `proposal_set` reason-tuple idiom. Every tolerance is a named
   constant in that one module: tight and loose runtime bands of 90 and 600
   seconds, a per-chapter tolerance of 2 seconds, an alignment that may skip
   one leading and one trailing chapter on either side for systematic brand
   intro and outro offsets, a strong-agreement floor of 5 compared chapters
   (measured cross-work collision is 2.59% at ±2s overall and 0.39% at five or
   more chapters), and a 64-bit perceptual-hash distance of 10. The cover rule
   is valid at work scope only, because an audiobook edition's art routinely
   differs from the print edition's while naming the same work; computing the
   hashes is a later concern and this module touches no image byte. The
   parameters are measured at slice-2 qualification, which is why they are
   parameters in one place rather than conditions spread through a rule.
6. **`local_evidence_only` is renamed `evidence_only`.** With providers active
   the terminal disposition means "evidence complete, read-only — no write
   authority", not "providers were skipped". The rename is implemented by a
   later packet under ADR-0013's clean-rebuild rule; the meaning is decided
   here so the implementing packet is a rename rather than a re-decision.
7. **The report advances to `metadata-report-v32`.** The audio-provider refusal
   of ADR-0063 is lifted: a `v32` audio report may carry provider observations,
   because what an audiobook dispatches is a pipeline decision and not a
   property of retained evidence. The pipeline still stops an audiobook before
   any provider until the activation packet lands, so the section is empty in
   practice rather than by refusal. `v32` also carries the recognized-sidecar
   manifest — the `(name, sha256)` pair for every sidecar present at
   inspection — which is a currentness witness rather than evidence. The
   configuration schema advances to `config-v7` for the four adapters. Both
   are clean rebuilds with no migration code, under ADR-0013.

## Consequences

### Positive

- An ASIN in the retained report always names the marketplace it resolved in,
  so an identity cannot be read out of a report that never established one.
- Structural corroboration has one tuning surface. Qualification changes six
  numbers in one module rather than editing rules.
- Currentness now covers every surface an audiobook's evidence was read from.
  Before this record only the audio file was re-digested, so an edited sidecar
  left a report silently stale.

### Negative

- The tolerances are provisional. Every one of them is a placeholder until
  slice-2 qualification measures it, and the reason vocabulary is what makes a
  wrong parameter visible rather than silent.
- A full chapter vector costs roughly 6 KB of the 2 MiB document per candidate.
  Five candidates each carrying one is about 30 KB, which is affordable, but it
  is the first evidence value whose size scales with the media rather than
  being scalar.

### Neutral or deferred

- Nothing here dispatches an audiobook provider, computes a perceptual hash, or
  attaches a corroboration signal to a decision. The vocabularies exist so the
  adapter and activation packets have somewhere true to put their results.
- Report-layer ASIN claims accept ISBN-10-shaped ASINs. `ProviderClaim`
  validates against the same `canonical_asin` grammar the ASIN lane is built
  from, per clause 1 and ADR-0061 clause 4, so one identifier family has one
  grammar rather than two that disagree. The originally deferred narrower
  proposal and packet checks were subsequently superseded: #957 aligned proposal
  eligibility, and [ADR-0073](0073-audiobook-decision-qualification.md) aligned
  shadow-review linkage with the existing canonical grammar. This accepts
  ISBN-10-shaped identifiers without granting recording applicability or
  Automatic ASIN qualification; provenance and field-specific guards remain.
- Audiobook cover acquisition is granted on two of the four adapters. The
  Audible catalog and iTunes Search each state a cover asset of their own, so
  `config-v7` carries a real `cover_enabled` bool on their enabled forms,
  defaulting on. Audnexus and AudioSilo restate Audible's CDN asset, so they
  carry no cover surface at all: a second copy of one image is not a second
  opinion about it, and the cover rule needs two independent authorities.
  Acquisition rides the existing bounded cover pipeline, and the hash the rule
  compares is computed in the isolated decode child beside the preview. This
  grants acquisition and evidence only; no cover election semantics change and
  an audiobook still has no write authority.

## Alternatives considered

### Encoding the chapter vector inside the existing evidence value

Rejected: 1,024 integers do not fit in the 512-character scalar bound, and
widening that bound for one kind would turn a typed vector into a string whose
overflow flag had to be encoded in the same string.

### Attaching chapter structure to the candidate rather than to evidence

Rejected: it would split one fact across two places — a kind naming the runtime
and a separate structure holding the vector — for the sole benefit of leaving
two strict key sets in the shadow reviewer untouched.

### Equality on duration, with providers normalised to minutes

Rejected: it discards the container's own precision to make a comparison
succeed, and it would make a genuinely different recording within the same
minute read as exact agreement.

### Fixing the corroboration tolerances in this record

Rejected: ADR-0061 already deferred them to slice-2 qualification, and a number
recorded before it is measured is a number nobody may change without an ADR.

## Validation

- The fast tier proves each corroboration rule table-driven, including the
  intro and outro offset cases and the sub-five-chapter weak case; the ASIN
  lane and plan builder; the sidecar manifest surviving persistence and each of
  add, remove, and edit failing currentness; and `config-v7` accepting the four
  keyless entries while refusing any credential surface, an unknown marketplace
  code, and `marketplaces` on any other adapter.
- Slice-2 qualification measures the runtime bands, the chapter tolerance and
  strong-agreement floor, and the cover hash distance against the reference
  library, and replaces the provisional values here.
- Reopen triggers: a measured tolerance that no single parameter set satisfies
  across providers, or a provider whose chapter statement is not expressible as
  a whole-second vector.

## First-increment chapter-runtime admission (#1042)

The descriptive matcher is unchanged. Owner-review admission may use complete
chapter structure only after existing stated-runtime admission fails, for the sole
retained local-ASIN envelope in ADR-0069. Local positive contiguous spans begin at
zero and end within 1000 milliseconds of the canonical local runtime; composition
is single-file or concatenated embedded, with neither truncation nor observed row
omission. Provider count equals the complete positive vector length and its total
satisfies `0 <= runtime_seconds - sum(durations_seconds) <= chapter_count`. The
aggregate must meet the same 90-second tight band. Equal complete vectors must meet
the existing strong minimum and per-chapter tolerance without offsets; uniform
vectors on either side fail. This bounds rounding and does not assert independent
recording provenance. No generic matcher, provider lookup or automatic authority
is added. Missing-local-runtime and remaining paths remain under #1004.

## Complete chapter recording corroboration (#1004)

This amendment defines F51's activation increment; it does not claim that the
increment is implemented or qualified. The #1042 decision above records the
delivered first increment. The descriptive chapter matcher and its offset
rules remain unchanged.

The admission predicate also accepts missing local runtime only for a
`SINGLE_FILE` composition with exactly one track whose duration is `None`, no
local `DURATION` claim, exactly one actual `DURATION_UNSTATED` observation
naming track 1, and no `TRACK_DURATION_UNAVAILABLE` observation. Complete
positive contiguous chapters must begin at zero, with no truncation or observed
omission. Missing-runtime multi-file composition remains unsupported.

The final chapter endpoint supplies coverage for comparison only; it never
creates a duration claim. With known local runtime, that endpoint must remain
within 1000 milliseconds of it. Both complete vectors have equal counts of at
least five chapters, positive nonuniform durations, and direct alignment within
the existing two-second per-chapter tolerance on the existing rounded local
whole-second projection, with no offsets or skipped rows. Millisecond
differences are not compared against a newly tightened threshold.
Provider aggregate flooring remains bounded by
`0 <= runtime_seconds - sum(durations_seconds) <= chapter_count`, and its
aggregate must meet the existing 90-second tight band against local coverage.
Equal totals do not excuse a different interior cut.

Failed-set audiobook `WORK` observations gain a separate bounded
`recording_lookup_candidates` collection. It retains every individually exact
title/full-author matched candidate, including original references, source
order, claim states, subtitle and contributor veto inputs. The ordinary result
remains `NO_MATCH` with empty candidates and no dispatch or transformation
lineage. Report validation binds the lookup collection separately to exactly
one actual WORK lane and its local input references, and replays individual
work guards. No common work identity or set verdict is fabricated.

Only explicit ASIN acquisition and matched-runtime veto consumers read this
collection. Generic proposals, identifier election, covers, promotion and
owner provider-claim projection continue to read ordinary candidates only.
Successful WORK-set candidates are not duplicated into the collection: they
retain source-ordered informational ASIN and DURATION claims alongside earned
work claims, while unearned marketplace, chapter, narrator and edition
applicability is stripped. Promotion requires corroborated `ASIN_MARKETPLACE`
evidence; an informational ASIN alone grants no promotion or identity.
This supersedes the earlier claim that every retained ASIN names a resolving
marketplace: only corroborated recording identity requires that marketplace;
lookup hints may retain an ASIN without it.

The new collection advances the report to `metadata-report-v42` and participates
in its canonical digest. **Clean rebuild required**, with no adoption or
migration; prior reports and report databases refuse unchanged under ADR-0013.
The audio child and owner detail API remain v4. Historical qualification
sources and measurements remain immutable; current source bindings advance
separately and do not upgrade historical completeness provenance.
