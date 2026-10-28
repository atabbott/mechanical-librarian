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
   bare `Part II`, which no provider could be asked. It is band-gated: a
   candidate it keeps must admit against the alternative title
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
- Since #1254 iTunes Search is not in the audiobook dispatch set
  ([ADR-0061](0061-audiobook-provider-posture.md), clause 9), so the default set
  acquires one audiobook cover authority, the Audible catalog. With no second
  authority the cover-hash reasons (`cover_identity._cover_corroboration_reasons`)
  no longer fire. They are evidence only: the identity outcome and
  `material_audiobook_cover_reference` do not read them, so no decision changes,
  and no other audiobook cover authority is admitted in its place.

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

## Runtime-narrowed Audible overflow (#1256)

An Audible `WORK` search on an audiobook plan whose item states one composed
runtime (`local_claim_runtime_seconds`, so a chapter-endpoint coverage figure
never triggers it) asks for one 25-product page instead of six. The adapter
parses the whole page first: more than 25 products, a missing canonical ASIN or
a duplicated identity still fails it as `malformed`, out-of-band products
included. Only then, and only when more than five products parsed, does
`loose_band_survivors` keep every product whose stated runtime the 600-second
loose band does not contradict. Tight-band, loose-band and runtime-less
products all survive and count toward the unchanged five-candidate bound. More
than five survivors is `candidate_limit_exceeded`, no survivor is `no_match`
(an `empty_no_match` rung the ladder walks past), and one to five go through
ordinary admission. A page of five or fewer is not narrowed. The asserted and
alternative lanes share this path; the band grants no authority, so survivors
still need work-identity admission and recording identity still needs the tight
band. Items with no composed runtime, ebook-shaped plans and Audible ASIN
lookups send unchanged requests, but the fixed 1 MiB Audible aggregate below
applies to them too; iTunes is unchanged.

The figures behind the page size, from #1246's wider-page row (19 overflow
items, 25 results): products in the 600 s band: 0 items over 5, 13 with 1–5, 6
with 0, at most 2 per item; 11 items with a tight product, 2 admit on the
asserted title; never over 5 at any cut 6–25; largest page 142 KB, about 5.1 KB
per product.

The narrowed search reads its one 25-product page under a fixed 262,144-byte
per-response bound, whatever `provider_response_bytes` is configured to; a
larger body is `oversized` and is never truncated or parsed. Six such pages
would not fit nine times the default ceiling, so Audible's aggregate is the
fixed 1 MiB ceiling for every configured bound. Survivors persist as ordinary
candidates with no new field, but a v51 overflow or empty rung meant the
unnarrowed page, so the report advances to `metadata-report-v52`. **Clean
rebuild required** under ADR-0013.

## Band-first recording-lookup hints (#1257)

The #1004 lookup collection is unchanged: it is collected only on failed
`NO_MATCH` sets, only for products that individually match the title and the
full author set exactly, and report validation replays it. It never sees a
product refused on title, which is the population #1246 measured: 62 tail
items with a tight-band product on some all-refused rung, 37 on the earliest
refused rung of the asserted ladder, at most 2 per item, and 0 refused beside
an admitted product. The issue's "up to 19" ceiling measured a different
population, the overflow pages, and is not this bound.

An Audible `WORK` rung of the asserted ladder on an audio plan now records a
refused product as a recording-lookup hint when its stated runtime is inside
the 90-second tight band of the item's composed runtime, it states one valid
canonical ASIN, and no retained candidate or #1004 lookup candidate already
holds that ASIN. Each hint records the ASIN, the stated runtime in seconds, the
basis `runtime_within_tight_band` and the rung's dispatch lineage. Each
Audible provider keeps at most 2 on its one asserted lane, the earliest rungs
first and sorted by ASIN, so they are a
deterministic function of the survivor pages and the local claims. Hints stay
on an exhausted `NO_MATCH` lane and beside `CANDIDATES`, from a refused rung
before the admitting one (after #1245) or from the admitting rung itself. A
terminal failure drops them with the refusals before it. The alternative lane,
ebook plans and every other provider produce none; only Audible can, and
report validation and the feed refuse a hint on any other authority.

The #956 feed sends hinted ASINs to Audnexus and AudioSilo in the same
preparation pass, after every existing origin and inside the four-lane cap,
with no second round. A hint asserts nothing: it reaches admission only as a
fed ASIN lane. Report validation refuses, in this order, a hint whose
provenance is not an Audible asserted lane on an audio plan, a rung
outside that lane's ladder or after its admitting rung, an ASIN a retained
candidate holds, a runtime outside the tight band, and more than 2 hints on
one provider observation. A retry that admits a hinted product on another lane
drops that hint from the unselected lane, as preparation would have.

A fed ASIN lane the item's own surfaces did not name (a target assertion, a
promotion, a #1004 lookup candidate or a hint) admits a recording only with
both the tight band and exact work-identity admission of that candidate
against the item's title and authors; with no title or authors to admit
against, none admits. A candidate that fails exact admission
now keeps only its ASIN claims, with no evidence and no media-health
comparison. Before this, its stated runtime let report-level stated-runtime
admission read it as recording identity, and its work statements reached
the proposal set beside an admitted product. This applies to every fed
origin. An embedded, sidecar or path ASIN is unchanged.

Hints advance the report to `metadata-report-v53`. **Clean rebuild
required** under ADR-0013.

## Dispatch classification and near-miss (#1258)

Every provider lane in an audiobook report now records why its dispatch ended,
as `dispatch: {outcome, rungs, near_miss_seconds}`. Ebook lanes serialize
`dispatch: null`. The outcome is one of `admitted`, `empty`, `refused`,
`overflow`, `unavailable`, `starved`, `not_dispatched` and `empty_or_refused`.
It is an outcome rather than a stage, so it is never read as an ADR-0017
diagnostic stage or a stage-timing one.

The lane status decides the outcome. `candidates` is admitted and
`candidate_limit_exceeded` is overflow. `disabled` and `insufficient_input` are
not dispatched. Every request failure (`unavailable`, `rate_limited`,
`authentication`, `authorization`, `malformed`, `oversized`) is unavailable.
An `incomplete` lane is starved only when every incomplete cause the adapter
recorded is the deadline, the persisted non-retryable request timeout
diagnostic backs it, and the provider states no byte or dispatch-count budget
diagnostic. The deadline must have stopped the request the lane was about to
send; earlier rungs or attempts may have been sent, so a lane can send its
first rung, find nothing and be starved at its second, and a pacer that blocks
a retry past the deadline starves it the same way. Any other incomplete lane is
unavailable, and so is a request that was sent and then ran out of time. A
`no_match` ASIN lane is empty. A `no_match`
WORK lane is refused when it walked its whole ladder and the
`work_identity_refused` diagnostic is present, and empty when it walked the
ladder without one. A provider that keeps no ladder, which is Hardcover and
Open Library, cannot tell the two apart, and its lane is `empty_or_refused`.
A provider's own class is derived and never persisted. Its aggregate status
already ranks its lanes, so a refusal outranks an empty ladder on a `no_match`
provider, one starved lane makes an `incomplete` provider starved, and a plan
over its query limit is not dispatched.

`rungs` is stated only on an Audible WORK lane the plan names. It counts the
rungs walked: the admitting rung's ladder position when admitted, the exhausted
ladder's length when empty or refused, zero when not dispatched, and between
one and the ladder's length otherwise. Every other lane, and a lane whose
adapter failed or was never built, states `null`.

The near-miss is measured only on Audible's asserted WORK lane, and only for an
item with one composed local runtime. Over every rung it walked, it reads the
whole parsed page before #1256 narrows it. Of the products the 600-second loose
band contradicts, it keeps those that exact work-identity admission accepts
against the lane's title and full author set. Their stated runtime minus the
item's, in whole seconds, gives the value: the smallest magnitude, the shorter
product on a tie. It is a measurement: it adds no request, never changes the
narrowed set or a lane status, and nothing reads it as evidence. The
alternative lane and every other provider state `null`.

Report validation replays the records in this order, and the first rule a
report breaks is the one it is refused for:

1. A record is present on exactly the lanes of an audiobook report.
2. Each outcome is re-derived from its lane, and only a WORK lane states rungs
   or a near-miss.
3. A starved lane must be backed by the diagnostics above.
4. Rungs appear only on a planned Audible WORK lane, and they replay against
   that lane's ladder.
5. A near-miss appears only on Audible's asserted lane of an item with a
   composed runtime. Its magnitude must lie outside the loose band and must not
   exceed the one-week duration bound.

A retry successor keeps an unselected lane's record byte-identical and
recomputes the record of the lane it retries.

Starvation is under-counted, never over-counted. The request timeout diagnostic
that backs it is emitted only when the provider's aggregate status is
`incomplete`, so a deadline-starved lane whose sibling lane's status outranks
`incomplete` (`candidates`, `rate_limited`, `malformed`, `oversized`,
`candidate_limit_exceeded`, `authentication` or `authorization`) has no backing
diagnostic and is recorded `unavailable`. This affects Audible, Audnexus,
AudioSilo and Open Library; Hardcover is unaffected, because its lane
diagnostics carry the cause. The budget-diagnostic exclusion is over-inclusive
for the same reason: it reads the provider's diagnostics, not the lane's. The
diagnostics themselves are unchanged.

The record holds enum values and integers only, with no provider text, title
or ASIN. No new ADR-0017 category or stage is added: a starved lane is backed
by the existing timeout diagnostic at the provider request stage. The
cold-start report counts the outcomes per provider and lane kind, and buckets
the near-miss.

Earlier reports have no `dispatch` key, so the report advances to
`metadata-report-v56`. **Clean rebuild required** under ADR-0013.

## Runtime band calibration (#1259)

The owner confirmed both runtime bands on 2026-10-20: the tight band stays at
90 seconds and the loose band at 600. The basis is a replay at main d62bb26e
over retained inputs, sending zero requests: the #1086 snapshot, 51 earlier
probe bodies and #1246's Audible bodies. The regression floor held: 91 of 91
case-bearing admissions with none on chapter basis, 238 of 238 duration claims
equal to their track sums, and the band refusing the same 21 of 108
candidate-bearing items. The loose band keeps 600 seconds on #1258's near-miss
evidence: both lanes with a near-miss differ from the item's runtime by more
than 900 seconds. This settles the runtime bands that the Consequences and
Validation sections leave provisional.

| Tight band | 60 s | 90 s | 120 s |
| --- | --- | --- | --- |
| Snapshot stated-runtime admissions | 81 | 91 | 93 |
| Band refusals, of 108 | 30 | 21 | 19 |
| Admissions without case support | 0 | 0 | 2 |
| Replay: corroborated items | 24 | 25 | 25 |
| Title-refused wrong-work products within the band, #1246's bodies | 10 of 256 | 11 of 256 | 12 of 256 |

A 120-second tight band was considered and rejected. It corroborates no further
item in replay. It adds two snapshot admissions without case support, whose
duration claims the 90-second claim link would not link, and one more
title-refused wrong-work product inside the band on #1246's bodies.

Reopen the tight band if #1294's full audiobook library run, or later
measurement under #990 or #1238, finds title-admitted items whose runtime
differs by between 90 and 120 seconds at a material rate with independent
corroboration such as narrator agreement, or if #1260's delivered rules show
their own in-band wrong-work rate rising.

The per-chapter tolerance and strong-agreement floor keep the measured basis
stated in clause 5, but this replay did not exercise them: no snapshot
admission was on chapter basis. The 64-bit cover-hash distance of 10 stays
provisional, and it cannot be exercised while only one audiobook cover
authority is dispatched. `DURATION_AGREEMENT_BAND_SECONDS` stays a separate
parameter, unchanged at 90. No constant changes, so the report version is
unchanged and no clean rebuild is required. The full aggregates are on #1259.

## Band-first work admission (#1260)

The corroboration gate runs after work-identity admission: admission decides
that a candidate set names the item's title and authors, and only then may the
tight band widen one candidate to edition scope. ADR-0067's band-corroborated
productions invert that order for themselves alone. On an audiobook plan's
asserted WORK lane at an audiobook authority, a candidate inside the tight band
of the item's single local runtime, with no recording language or format
conflict, may admit through `band_parenthesized_series_name`,
`band_order_inverted_series_prefix` or `band_initials_fold` where the closed comparison alone refuses it. The band is
this record's `RUNTIME_BAND_TIGHT_SECONDS`, compared through
`runtime_agreement`; there is no second constant. An item with no single local
runtime gets no widening.

Set admission first runs without the band. Only a set that refuses is tried
again with the band-corroborated productions, so every set admitted before is
admitted with the same candidates and lineage, and none of the band's refusals
can admit. A product admitted this way is tight by construction, so the
unchanged corroboration gate selects it, it keeps its marketplace edition
evidence and stated-runtime admission grants it recording identity.
`recording_identity_basis` stays `null`. The alternative-title lane (#1255),
fed ASIN lanes and exact fed-ASIN admission (#1257), recording-lookup hints,
the overflow witness and loose-band narrowing (#1256), the near-miss (#1258)
and every ebook path keep their rules. A hint is still only a product the
lane did not retain.

Report validation and the proposal set replay the same band-aware set
admission with the item's own local claims, on the asserted lane of an audio
report at an audiobook authority only. A band
lineage on an alternative lane, on an item with no single local runtime or
with its candidate outside the tight band does not replay and is refused as an
inconsistent lineage. The report advances to `metadata-report-v57`. **Clean
rebuild required** under ADR-0013.

## Carried admission (#1316)

[ADR-0088](0088-carried-recording-admission.md) reuses the tight band and the
fed-ASIN admission above, with no new constant, for the carried lookup of an
earlier admission, and adds one gate on that lane alone: a product whose
narrator cast is disjoint from the recording's own credits is refused, while an
unknown cast on either side passes.

## Whole-minute provider floors (#1307)

The owner approved on 2026-10-22 reading a provider's whole-minute runtime as
a floor when it is compared with the tight band. Audible, Audnexus and
AudioSilo state runtime in whole minutes, each carried as that many minutes
times 60 seconds, and on #1294's snapshot they sit a median 40 seconds below
the exact chapter runtime. A provider runtime of `m` minutes now stands for the
interval `[60m, 60m + 59]` seconds: the distance to the item's runtime is zero
inside it and otherwise measured to its nearer end, so a recording 100 seconds
longer than the stated minutes is 41 seconds away. A runtime that is not a
whole number of minutes keeps the exact distance.

This changes what the band measures, not its width. `RUNTIME_BAND_TIGHT_SECONDS`
stays 90 and the loose band stays 600, read exactly as before. The floor
applies wherever a provider runtime meets the tight band: the lane band, the
recording-lookup hints, the band-aware set admission and its runtime veto,
and stated-runtime corroboration. Corroboration picks a product tight under
the exact reading first and falls back to the floor reading only when none
is, so every earlier choice is unchanged. `DURATION_AGREEMENT_BAND_SECONDS`
for claim links is unchanged.

The band was not reopened. Once sibling-edition re-selections were removed,
#1294 left 1 of 154 genuine 90 to 120 second cases. On the #1307 replay the
floor reading adds 6 admissions among the stored reports and 4 among the
probe items, all within the floor-read band and none with a narrator
disagreement. All 125 stored admissions and every control are unchanged. It
ships with ADR-0067's #1307 amendment in `metadata-report-v61`. **Clean
rebuild required** under ADR-0013.
