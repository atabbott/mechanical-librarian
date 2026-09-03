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
required. Enabling a further production still requires new corpus evidence and a
new ADR.

**Decoration equivalence.** For a title result whose only reason is
`competing_evidence`, exactly two provider-backed WORK alternatives that are
equal after one closed election-grammar decoration match form one equivalence
class whose canonical member is the plain form. Matching is one step only, so
stacked decorations stay competing. The grammar step is exact: removing the
decoration must yield the plain alternative byte for byte, so a collapse never
rewrites casing or spacing the review never adjudicated, while the two guards
below compare under the closed title comparison. Two evidence-only guards close
the class further and apply to keep and tidy alike:

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

## Consequences

### Positive

- Titles that differ only by one closed decoration stop consuming owner
  attention, and works already holding the plain form resolve with no write.
- Re-executing the sealed #870 corpus (89 cases) against the candidate admitted
  5 of 5 adjudicated same-work compound cases, rejected
  2 of 2 different-work collisions, with 0 false admissions
  and 0 false rejections; every other family kept its query-only disposition.
- A collapsed title carries one alternative, so reports that withheld a
  bibliographic combination on a decoration difference can form one.
- Subtitles a cross-source segment corroborates become automatically fillable
  instead of stalling as uncorroborated.

### Negative

- The report schema and proposal policy both advance, so every pre-release
  application database needs a clean rebuild.
- The compound rule is one production, not a general composition mechanism;
  each further composition costs a new review and a new ADR.

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
