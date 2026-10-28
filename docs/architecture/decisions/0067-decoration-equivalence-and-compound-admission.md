# ADR-0067: Decoration-equivalent title election and compound work-identity admission

- Status: accepted
- Date: 2026-09-02
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

Amends [ADR-0018](0018-closed-title-creator-comparison.md).

## Context

ADR-0018's #870 amendment left the work-title admission subset empty: no single
transformation family cleared both independently corroborated positive evidence
and same-author boundary evidence, so every table row stayed query-only and the
nullable transformation-lineage field had no canonical value. The sealed #870
corpus nevertheless contained one repeatedly observed shape that no isolated
family describes: a local filename title carrying the complete first-author
prefix followed by an underscore-separated remainder. Admitting either part
alone would open a wide class of same-author collisions; admitting the observed
composition does not.

Separately, the archived #744 snapshot showed titles competing only because one
provider states a decorated form and another the plain form of the same work.
The engine treated these as substantive disagreement, so the owner decided a
difference that carries no bibliographic information, and the affected reports
withheld bibliographic combinations they could safely have formed.

A blinded review of a frozen challenge set drawn from that snapshot (two
independent reviews, 70 of 75 agreeing, five disagreements adjudicated under a
recorded convention) established which collapses are safe and derived the two
guards below. The same evidence showed a stripped colon segment is often the
only independent corroboration an otherwise unelectable subtitle has.

## Decision

**Compound admission.** Work-title admission is granted only by a closed
*compound production*: a named, explicitly reviewed ordered composition of two
or more transformation families. Composition is never implicit. A production
admitting never admits any of its constituent families alone; every row of the
transformation table stays `admission_enabled=False`. One production is
enabled: `author_prefix_then_underscores` — the complete first-author token
prefix, a separator from the closed set, and a remainder containing at least one
filename underscore that normalizes to the provider title under the unchanged
closed comparison. The side is always local. The identity floor is unchanged:
exact complete ordered authors, exactly one provider-local work identity,
original claims untouched, ambiguity rejected, and every translation,
adaptation, abridgement, and variant-marker veto preserved.

The transformation lineage becomes the ordered families plus the derived
production name and side. It is valid only for an admission-enabled production
with side `local`, so no single-family lineage can exist and a persisted lineage
whose production name does not derive from its families is rejected on replay.
The persisted report schema advances `metadata-report-v35` to
`metadata-report-v36`; ADR-0013 rejects v35 at startup and a clean rebuild is
required. Enabling a further production requires its own per-family evidence
and an amendment to this record; the #1260 amendment below revises how many one
amendment may enable.

**Decoration equivalence.** For a title result whose only reason is
`competing_evidence`, exactly two provider-backed WORK alternatives that are
equal after one closed election-grammar decoration match form one equivalence
class whose canonical member is the plain form. Matching is one step only, so
stacked decorations stay competing. The grammar step is exact: removing the
decoration must yield the plain alternative byte for byte, and the current value
must equal one class member byte for byte, so a collapse never rewrites casing
or spacing the review never adjudicated. The two guards below compare under the
closed title comparison. Two evidence-only guards close the class further and
apply to keep and tidy alike:

- *Provider dissent*: every valid WORK-scope provider title claim in the report,
  including claims on candidates that admission or observation rejected, must
  key into the class under the engine's closed title comparison. Any other
  provider work title yields `decoration_equivalence_provider_dissent`.
- *Attestation width*: at least as many distinct provider authorities must
  attest the plain form as the decorated form, otherwise the decoration is
  plausibly integral and the result carries
  `decoration_equivalence_decorated_consensus`. Width is counted over the same
  raw provider WORK-scope title claims the dissent guard reads, not over the
  admitted alternatives: a form stated by a candidate that admission rejected
  still attests it, and counting an alternative's correlations would miss it.
  Local claims are excluded from both sides.

When the current value is the plain form the outcome is a keep with zero writes
(`decoration_equivalent_<family>`); when it is the decorated form the plain
alternative is proposed (`decoration_tidy_<family>`). A parenthesized series
name is tidied only when it is preserved as structured series evidence,
otherwise `decoration_tidy_series_unpreserved`. The tidy tranche is a qualified
automatic decision under the policy identity
`title:replace_target:decoration_tidy`, safety lane `title_layout`, bound to the
F19a successor manifest v5. The proposal policy advances
`fixed-conservative-v18` to `fixed-conservative-v19`.

**Segment corroboration.** A colon segment stripped by the subtitle-segment
match from any WORK-scope title claim, local or provider, inherits that claim's
source correlation and work scope. It never elects a subtitle alone and never
corroborates a subtitle stated by its own source. Segment corroboration elects
nothing among competing provider subtitles; it only fills an empty subtitle
through the automatic `subtitle:fill_empty` projection, under the three guards
below. This supersedes the earlier rule that a colon-derived suffix is never F20
corroboration. An existing segment that corroborates nothing is stated as
`subtitle_segment_uncorroborated`.

Corroboration is decided once, by a single helper both surfaces call, under
three guards:

- *Generic vocabulary*: a segment whose value is a generic provider subtitle is
  rejected as `generic_provider_subtitle` before corroboration runs. The closed
  generic lexicon is `a novel` and `stories`.
- *Provider subtitle dissent*: no valid provider subtitle claim anywhere in the
  report, on any candidate and in any scope, may key to a different value.
  Otherwise the corroboration is withheld as
  `subtitle_segment_provider_dissent`.
- *Attestation width*: at least one provider authority must carry the segment,
  and the distinct provider authorities carrying it, plus one where the local
  claim also carries it, must not exceed the distinct provider authorities
  stating a plain title those segments were stripped from. Otherwise the
  segment reads as integral title text and the corroboration is withheld as
  `subtitle_segment_decorated_consensus`.

A withheld corroboration reports its guard on the seams the uncorroborated
diagnostic uses: the subtitle proposal reason when the election is withheld, and
the preview abstention reason when the projection is. The plain absence of any
corroboration keeps `subtitle_local_corroboration_required`.

**Ordering.** F35's colon and series abstentions now run after its
provider-agreement gate rather than before it. This is a behaviour change in
F35: a title F35 would never have elected no longer abstains under
`provider_consensus_title_colon_unused` or
`provider_consensus_title_series_unpreserved`, and reaches the F41 election
instead. Without it the two elections are not disjoint — F35's abstentions
pre-empted the subtitle-segment and series families of F41 entirely.

### Audiobook single-family lineages (#1255)

`leading_sequence_token` and `right_hand_segment` (ADR-0018) are permitted
single-family dispatch lineages on the audiobook ladder only. No compound
production is added: neither family composes with another, and neither admits.

### Band-corroborated productions (#1260)

The owner decided on 2026-10-19 to amend this record's one-production
consequence before admitting the band-corroborated productions. One amendment
may now enable several productions, provided each carries its own per-family
evidence, each closes its own constituent where it is delivered in increments,
and no production composes with another or with an existing production.

**Band precondition.** These productions admit only on an audiobook plan's
asserted WORK lane, from an audiobook provider authority, with the candidate
inside the tight band of the item's single local runtime. The band is
ADR-0064's `RUNTIME_BAND_TIGHT_SECONDS`, compared through its
`runtime_agreement`, and the candidate must have no recording language or
format conflict. An item with no single local runtime gets no widening. This
inverts ADR-0064's admission-then-band order for these productions only. The
alternative-title lane (#1255), fed ASIN lanes, the overflow witness, ebooks
and every other authority keep their rules byte-identical.

**Three productions.** Each is single-application or authors-only, with the
side and field stated, and none composes:

- `band_parenthesized_series_name` and `band_order_inverted_series_prefix`:
  the lane's dispatch lineage is the normalized rung with exactly that one family, with or without the narrowing
  retry. The provider title and ordered authors must admit exactly, with no
  transformation, against the title of the ladder query that carries that
  lineage. Reordered authors do not admit. The product's own stated series
  must corroborate the decoration the rung stripped: one membership's name is
  closed-equal to the stripped series name and, where the rung stripped a
  position, that same membership's position equals it as a decimal number. An
  inverted prefix always strips one. A parenthetical strips one when it ends in
  an optional comma, an optional marker from the shared series vocabulary or
  `#`, and a decimal number, so "(Example Saga 3)" and "(Example Saga, Book 3)" need
  "Example Saga" at position 3; any other parenthetical is a name alone. A
  product with no series, a different one, or the name and position on two
  memberships never admits. The original local title carries neither a
  variant marker nor a whole-word recording-format term (dramatised,
  dramatized, dramatisation, dramatization, dramatisations, dramatizations,
  abridged, unabridged, abridgement, abridgment, full cast, full-cast or
  radio); "The Dramatist" is not a dramatisation. A product that
  `provider_candidate_work_identity_contradiction` classifies as an explicit
  contradiction of the asserted title never admits, even where the rung title
  matches. The lineage is one local title application of that family.
- `band_initials_fold`: the titles are closed-equal with no other
  transformation, the author counts are equal and the authors are compared in
  order. Each pair is closed-equal, or closed-equal once runs of single
  letters each followed by a full stop are folded to the same letters
  separated by spaces, and at least one pair needs the fold. Tokens split on
  the whitespace the closed comparison collapses, no-break and thin spaces
  included. "J.R.R." and
  "J. R. R." fold to "J R R"; "JRR" does not change. Every folded name keeps a
  token that is not a single letter. The lineage records one local or provider
  authors application per changed side, with the author's ordinal. The new
  `initials_fold` family is a table row that is neither query- nor
  admission-enabled and is audiobook-only.

**Trailing series sequence: not enabled.** A trailing sequence ("Part II",
"Book 2") strips a position and no series name, so a product's series cannot
corroborate it: "Henry IV, Part II" would admit a same-author "Henry IV" in
any series at position 2. The replay recorded on #1260 found 0 positives for
it. No `band_trailing_series_sequence` production exists, and a lineage
naming one is refused.

**Band-first set rule: deferred.** The proposed rule would admit the one
tight product when every other admitted product lies outside the 600-second
loose band. It ships only with at least one positive and no false admission,
and the replay recorded on #1260 found none, in the engine or in a predicate
scan over every retained page: of the 68 refused pages it reached, 66 had no
admitted candidate and 2 had two candidates inside the band, correctly
refused. It is not implemented.

**Unchanged floors.** The variant, translation, adaptation and abridgement
vetoes; `recording_variant_conflict`; exact fed-ASIN admission (#1257); the
alternative-lane rule (#1255); ebook byte-identity; and the rule that the band
never admits alone all hold. Set admission first runs exactly as before and
widens only when that refuses, so every set it admitted before admits with the
same candidates and lineage.

**Evidence.** The replay recorded on #1260, over the #1086 snapshot and the
retained provider bodies:

- Floors: 91 of 91 case-bearing admissions with 0 on chapter basis, 238 of
  238 duration claims equal to their track sums, the band refusing the same
  21 of 108 candidate-bearing items, and the wrong-title control refusing 38
  of 38.
- All 81 stored audiobook work-lane admissions are identical.
- Title productions: 11 admissions (8 inverted series prefix, 3 parenthesized
  series name), 0 false. Initials fold: 0.
- 4 newly corroborated items gain 22 automatic outcomes, none wrong; hints
  fed fall from 58 to 47, and measured items with no retained Audible
  candidate from 59 to 57.

**Persistence.** The productions advance the report to `metadata-report-v57`.
**Clean rebuild required** under ADR-0013. The lineage shape is unchanged.

**Validation.** Unit matrices for each production in both directions,
including the explicit-contradiction guard and an alternative-lane rung,
dispatch canonicality, lineage round trip and forged-lineage rejection,
report replay with and without band claims, v57 persistence and v56
rejection, and integration through the real Audible and Audnexus adapters.

### Corroborated admission and the band-first set rule (#1307)

The owner approved on 2026-10-21 and 2026-10-22 admitting band-agreeing
audiobook products that exact title admission refuses, through one
corroborated rule rather than a production per refusal shape, together with
the band-first set rule deferred above. A clean rebuild under ADR-0013 was
accepted.

**Where it applies.** Only Audible's asserted WORK lane on an audiobook plan,
and only after every exact path admitted nothing: set admission first runs
exactly as before, then with the #1260 band productions, and only a page every
path refused reaches the rule. The asserted lane walks its whole ladder first,
and the #1255 alternative lane is then asked as before; only when every
alternative lane the plan carries was asked and ended `no_match`, and no ASIN
lane (the carried one included) admitted, does the rule read the asserted
lane's refused pages. An exact admission on any lane therefore always comes
first, and report validation refuses a rule admission beside an alternative
lane that was not asked or did not end `no_match`, or beside an admitted ASIN
lane. A rule admission records every rung the walk sent.

The three kinds of lane block the rule differently. An admitted ASIN lane,
the carried one included, blocks it, because an exact path already answered.
A carried lookup that failed does not: under #1316 a failed carried lookup is
no answer, so the rule reads the asserted lane's refused pages as if no ASIN
had been carried. A failed alternative lane does block it, because that lane
was never answered as `no_match` and an exact admission there may still be
waiting.

A provider retry (#1257) sees only the lanes it retries. It keeps the rule
only when every planned alternative lane is retried with it or already ended
`no_match`, and no ASIN lane left out of it admitted. An alternative lane
suppressed behind a failed asserted lane was never asked, so a retry of the
asserted lane alone runs without the rule. The refused pages are not
retained, so a later retry of the alternative lane alone that ends
`no_match` cannot re-arm the rule; the item waits for its next full
preparation. The carried
lane (#1316, ADR-0088) may verify a refused product through the same rule. The alternative-title lane, every other
fed ASIN lane, the overflow witness, ebooks and every other authority keep
their rules unchanged.

**The rule.** One product admits when all of these hold:

- its runtime is inside ADR-0064's tight band of the item's single local
  runtime, read as a whole-minute floor;
- its title is related to the lane title: after accents, case, articles,
  apostrophes and punctuation fold, the titles are equal, the product's title
  and subtitle joined equal the lane title, one contains the other as a
  contiguous run of words, the product's main title before a colon is
  contained in the lane title, or the two share at least half of their
  combined words;
- its narrator agrees with the target's or the recording's narrators, or its
  series position equals the target's for a series whose normalized name is
  equal;
- nothing contradicts it: no disjoint narrator cast, no disjoint author list,
  no different position in the same series on any pair of memberships, even
  where another pair agrees, no recording language or format
  conflict, and no work-identity veto (an abridger, adapter or translator
  credit, or a variant marker in the title).

A language contradiction needs a language the local file states, as
`recording_variant_conflict` reads it everywhere; most audiobook files state
none. A product in another language then fails the rule only by lacking a
narrator or series agreement, or by its runtime.

A title that runs on past the other with a new word is the shape of a sequel
by the same author and narrator, so it admits only when its series position
agrees; a narrator agreement alone does not admit it. The product title is
read joined to its subtitle, and either side may be the longer: "Example
Journey Home", "Example Journey: The Return" and a separate subtitle "The
Return" all run on past "Example Journey", and "Example Journey Home" runs on
past a product "Example Journey". Decoration does not run on: the stop list
both audiobook title grammars share ("book", "edition", "unabridged", ...),
"a novel" and the series names either side states. A number or roman numeral
is decoration only before the shorter title and only when it is not the
product's own series position: a bare "12 Example Journey" beside "Example
Journey" does not need a series, unless the product sits at 12 in its
series. After the shorter title it is a sequel's mark, so "Example Journey
2", "Example Journey II", "Example Journey: Volume 2" and "Example Journey:
Example Saga, Book 2" need their series to agree. A roman numeral is two to
six letters worth at most 39, so a word that only spells one ("mix", "mi",
"i") runs on like any other word. So "Example Journey: A Novel" and
"Example Journey (Unabridged)" do not need a series. Neither the number rule
nor the numeral bound changed any rescue in the #1294 replay. A local title with a
subtitle the product lacks ("Example Journey: Into the Fold" beside "Example
Journey") does; no measured rescue had that shape.

When several products qualify, each must state a narrator and every pair must
share one, or none admits: an unnarrated edition beside a narrated one could
be any recording. Otherwise the closest runtime, then the lowest identity, is
chosen. The title guard is mandatory. Without it every rule tried admitted
same-author, same-narrator neighbours in the same series.

**Residual risk, pinned by tests.** Two titles that share at least half their
words ("Example and the Iron Gate" beside "Example and the Silver Gate") are
related, and with the same author, narrator and band and no local series to
contradict, such a neighbour admits. No guard was found that refuses it
without also refusing a measured rescue of the same shape; a known local
series position does refuse it. The same holds for an edition in another
language when the file states no language.

**Lineage.** The production is `band_corroborated_title_relation`: one local
title application of the new `corroborated_title_relation` family, a table row
that is neither query- nor admission-enabled and is audiobook-only. The
dispatch lineage is the rung the page was asked on, which must be a rung of
the lane's own ladder. Replay recomputes the rule from the report's own
claims, and a forged narrator or series claim fails it.

**Band-first set rule.** When at least two products admit one by one, exactly
one of them is inside the tight band and every other one is outside the loose
band, the tight one is kept, unless it has a recording-variant conflict or its
narrators contradict the target's. It then replays alone through the path
that admitted it, so its lineage is that path's, and it is recorded as that
path stops: its rungs and hints run only to the rung its lineage names, so
validation still requires an exact admission's count. Because nothing on the
lane names it, validation cannot tell a band-first admission from an exact
one, and the deferral is the only thing that orders it after the exact
paths.

**Per-shape productions: not added.** Title and subtitle join, subtitle
stripping with series corroboration, article fold, right-hand segment and the
band-gated rung title are each a case of the rule. A separate production for
any of them would admit nothing the rule refuses, and would need its own
evidence. The `leading_number_stripped` query rung (#1307) is query-only:
what it finds admits by exact admission or by the rule.

**Evidence.** An offline replay over the #1294 snapshot and the retained probe
bodies, driving the real Audible adapter, compared with the #1316 tree:

- Probe items: 42, with 92 requests against the retained bodies. Rescued: 14, 8 through the rule on the leading-number rung,
  2 through the rule on the normalized rung, and 4 through the floor reading
  on the exact path. Every admitted product is inside the band (largest floor
  distance 69 seconds) and none has a narrator disagreement; 2 of the 15 have
  no known narrator and agree on series position. The one earlier admission
  is unchanged.
- Stored reports: all 125 admitted audiobook identities, 83 of them with a
  recording ASIN, are unchanged. The floor reading adds 6, all inside the band
  (37 to 77 seconds) with no narrator disagreement.
- Cross-item control: 57,570 product and item pairs, 3,043 of them
  same-author. 18 admit, every one the same book: 17 a product the item's own
  pool also holds, and 1 a series-position title naming the product's book.
  Different-work admissions: 0.
- Holdout control: with the chosen product removed, 0 of 203 items admit
  another product.
- Different-recording control: on the 4 items where Hardcover matched the file
  and Audible's candidate is another recording, nothing admits.
- The band-first rule found no positive in this replay; its tests are
  synthetic.

**Persistence.** The report advances to `metadata-report-v61`, which retains
refused products, and the proposal policy to `fixed-conservative-v38`.
**Clean rebuild required** under ADR-0013.

**Validation.** Unit matrices for the rule's admit and refuse cases, title
relation, narrator set agreement, language conflict, band-first, set
admission and lineage canonicality; and the real Audible adapter end to end,
covering the post-walk retry, the leading-number rung, the dispatch bound,
forged-narrator replay refusal, retained refused products and carried-lane
verification.

## Consequences

### Positive

- Titles that differ only by one closed decoration stop consuming owner
  attention, and works already holding the plain form resolve with no write.
- Re-executing the sealed #870 corpus (89 cases) against the candidate admitted
  5 of 5 adjudicated same-work compound cases, rejected
  2 of 2 different-work collisions, with 0 false admissions
  and 0 false rejections; every other family kept its query-only disposition.
- A collapsed title carries one alternative. #1002 retains an evaluation-local
  association from each eligible removed decorated representation to the plain
  alternative after all election guards pass. Complete bibliographic tuples may
  retain independently supported components without adding evidence or borrowing
  source identity. Direct lookup and surviving policy membership remain authoritative;
  series applicability and automatic qualification remain separate gates.
- Subtitles a cross-source segment corroborates become automatically fillable
  instead of stalling as uncorroborated.

### Negative

- The report schema and proposal policy both advance, so every pre-release
  application database needs a clean rebuild.
- The compound rule is not a general composition mechanism. One reviewed
  amendment may enable several productions, provided each carries its own
  per-family evidence, each closes its own constituent where it is delivered in
  increments, and no production composes with another (#1260).

### Neutral or deferred

- Stacked decorations, fuzzy similarity, and three-or-more-member classes stay
  competing evidence and remain owner decisions.
- No effect or write authority is granted: the tidy tranche is decision-only.

## Alternatives considered

### Enabling `filename_author_title_prefix` alone

The #870 review rejected it: the prefix rule alone admits same-author
collisions where the remainder is a different work by the same author. The
compound's underscore requirement is what separates the observed filename shape
from ordinary prose titles.

### Allowing N decorated members in one class

The frozen challenge set adjudicated every multi-alternative case as abstain,
so there is no evidence for a rule wider than one plain and one decorated form.

### Requiring the stripped colon segment to be preserved as a subtitle

The review accepted 17 tidies of which 10 carry a colon segment no provider
subtitle claim states; such segments are decoration dropped from the title. The
two guards already exclude integral-colon titles, so a title-side preservation
requirement would only suppress accepted collapses.

## Validation

Unit coverage for each family in both directions, both guards, stacked and
multi-alternative fail-closed, series preservation, the reordered
provider-consensus abstentions, the compound admission matrix and its negatives
(prefix-only, underscores-only, wrong author, author subset, remainder
collision, separator outside the closed set, provider side), each segment
corroboration guard on the election and the projection seam, persisted lineage
round trip and replay rejection, schema v36 readback and immediate-prior
rejection, the shadow decision rule table and the tidy tranche's persisted
automatic decision, and the frontend labels and comparison notices. The private
qualification runs the F35-route tidy challenge set, the F20 retained-document
subtitle re-score, and the #870 compound corpus re-execution; only aggregates
leave the private lab.
