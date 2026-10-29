# ProposalSet operation

The `CreateProposalSet` application operation creates one immutable ProposalSet
from an exact current metadata report. It is used by authenticated review
preparation; source-only contributor inspection uses the typed workbench and is
not an installed runtime capability. The operation reopens the exact report, revalidates its
current Audiobookshelf target projection, selected EPUB bytes, ABS cover, and
both blobs of every referenced private cover artifact, then evaluates the built-in
`fixed-conservative-v40` policy. It does not construct or contact a metadata or
cover provider.

An exact ProposalSet can be reopened offline through the application repository
or typed source-only inspector.

## Series automatic election

Series alternatives retain every admitted membership. When the active policy
routes a qualified series option to Automatic, one complete provider membership
may project without a second provider lineage. Provider-wide unsupported series
and sequence output with no name is no evidence. A named incomplete or
unpositioned membership, a genuine unresolved alternative, or a failed ISBN,
WORK, or ASIN query kind that could have contributed to the elected evidence
keeps the field inspectable and abstained. The reason records the contributing
failed kind. Election collapses only closed leading-article and trailing
`Series`/`Trilogy` name variants, preserving exact sequence distinctions; it
does not add fuzzy matching, provider ranking, membership synthesis, or
title-specific runtime knowledge.

## Cover choice retention

A cover with at least one bounded usable artifact remains an owner choice when
a sibling source ends in a typed timeout or another incomplete terminal state.
This includes a sole usable artifact: the exact cover-only reasons are
`cover_gallery_incomplete` and `owner_cover_choice_required`. With no usable
artifact the result remains deferred. The proposal never invents a second
choice or treats incomplete acquisition as proof that the target is empty.
If another source has the same normalized preview as the Audiobookshelf target,
the resulting consideration remains bound to the target's actual artifact,
source digest and original dimensions. Larger duplicate-source dimensions do
not qualify a replacement; distinct alternatives retain their own dimensions.
The packet producer revision is part of consideration replay authority, so an
older same-schema result is historical and receives one current successor.

Success is exactly one canonical `proposal-set-v8` JSON document plus a
newline. Failure emits no document and one value-free diagnostic.

Only `proposal-set-v8` documents under `fixed-conservative-v40` and the exact current
`proposal_sets` table envelope are accepted. A database with the earlier
`fixed-conservative-v1`, `fixed-conservative-v2`, `fixed-conservative-v3`, or
`fixed-conservative-v4`, `fixed-conservative-v5`, `fixed-conservative-v6`,
`fixed-conservative-v7`, `fixed-conservative-v8`, `fixed-conservative-v9`,
`fixed-conservative-v10`, `fixed-conservative-v11`, `fixed-conservative-v12`,
`fixed-conservative-v13`, `fixed-conservative-v14`, `fixed-conservative-v15`,
`fixed-conservative-v16`, `fixed-conservative-v17`, `fixed-conservative-v18`, or
`fixed-conservative-v19`, `fixed-conservative-v20`, or
`fixed-conservative-v21`, `fixed-conservative-v22`, `fixed-conservative-v23`, or
`fixed-conservative-v24` table shape, or any later one through
`fixed-conservative-v39`, is incompatible and is rejected before mutation. A clean
rebuild is required; there is no proposal migration, import, conversion, or
compatibility parser.

## Consensus title qualification

The F35 (#884) provider-consensus title election is admitted by the current
#445 F19a successor manifest. Its closed title tranche is qualified separately
from the F20 rich-field route, which continues to exclude title. Earlier F19a
digests remain readable for their historical decisions but cannot authorize F35.

## Decoration equivalence

The F41 (#896) decoration-equivalence election runs immediately after F35 on a
title result whose only reason is `competing_evidence`. The two elections are
disjoint by predicate: F35 requires every provider title claim to state the
clean form, F41 requires both class members to carry provider evidence. F41
emits `decoration_equivalent_<family>` on a keep, `decoration_tidy_<family>` on
the tidy proposal, and one of `decoration_tidy_series_unpreserved`,
`decoration_equivalence_provider_dissent`, or
`decoration_equivalence_decorated_consensus` beside `competing_evidence` when a
guard closes the class.

The grammar step is exact: removing the decoration must yield the plain
alternative byte for byte, and the current value must equal one class member
byte for byte, so a collapse never rewrites casing or spacing. The dissent and
width guards compare under the closed title comparison.

A collapsed title carries one alternative. After every election guard passes,
an evaluation-local association retains the removed decorated representation's
membership with that plain alternative (#1002). Direct title lookup takes
precedence, and only alternatives surviving policy application can be bound.
This adds no evidence, correlations, component values, or persisted alias.
Subtitle and series components still need their own evidence, source coherence,
complete three-field combination and independent automatic qualification.
In particular, a decorated provider series title does not acquire applicability
to an already plain target from this association. Policy v23 requires a clean
rebuild of v22 state; report and proposal document shapes are unchanged.

The generic-title preservation branch that emits an all-PRESERVE combination is
now taken only when the title outcome is a keep, which is the only shape the
`ProposalSet` invariant admits.

The subtitle result names an uncorroborated colon segment with
`subtitle_segment_uncorroborated`, on the competing seam and on an ineligible
result with no supported evidence. A segment elects no subtitle at all: the only
election on the competing seam is the book's own explicit subtitle claim
(`local_attested_subtitle`), a segment there only reports the corroboration
guard that closed the choice, and a segment fills a subtitle only through the
automatic projection.

Segment corroboration itself is decided once, by `segment_corroboration`, for
both seams, so they cannot drift. A generic segment is rejected before
corroboration runs. A segment otherwise corroborates only when no provider
states a different subtitle (`subtitle_segment_provider_dissent`) and the width
holds: corroboration is withheld as `subtitle_segment_decorated_consensus` when
no provider authority carries the segment, or when the authorities carrying the
decorated form—the local title counted—exceed the provider authorities stating
the plain form the segment was stripped from. A withheld corroboration reports its guard where the
uncorroborated diagnostic reports: on the subtitle proposal on the competing
seam, and as the preview abstention reason when the projection is withheld.

F35's colon and series abstentions now run after its provider-agreement gate
rather than before it. This is a behaviour change: a title F35 would never have
elected no longer abstains under `provider_consensus_title_colon_unused` or
`provider_consensus_title_series_unpreserved`, and reaches the F41 election
instead.

## Canonical contributor display

F38 (#887) elects a provider display form only when two independent present
authorities state the same exact complete contributor list and the current local
list is the sole equivalent dissent. The selected alternative retains the local
claim references as provenance; provider disagreement, incomplete equivalence,
or an owner decision remains non-automatic.

The bracketed-source class currently grants election authority only to the
exact `[retail]` suffix. The broader bracket transformation remains available
for query shaping, but every other bracketed suffix abstains until corpus
evidence expands the closed election lexicon.

The publication projection retains one independently corroborated canonical
fact at the strongest shared year, month, or day precision. It never pads a
date, accepts local-only precision, or turns the derived Published year context
into a second proposal, decision, outcome, or activity event.
An edition date whose candidate explicitly names a different work is retained
in the report but casts no publication vote. A candidate whose target-work
identity is unavailable stays in the ordinary bounded evidence set; lack of
proof is not recast as contradiction.

## Fixed policy

Every ProposalSet is bound to the exact report ID, derived target fingerprint,
fixed policy version, and resolved policy identity. It
contains exactly one ordered result for title, subtitle, authors, narrators,
series, genres, publishedYear, publishedDate, publisher, description, isbn,
asin, language, explicit, tags, and cover. Each result is one of
`propose_change`, `acceptable_choice`, `unresolved_choice`, `keep`, `reject`,
`defer`, or `ineligible`.
An active metadata profile is replayed from its exact persisted per-library
resolution. Ask leaves conservative results available for human review, while
Ignore converts the category to typed non-actionable `policy_ignored` before
bibliographic combinations are formed. Automatic is qualified only for the
closed decision-only categories admitted by the active policy and safety
posture; it creates no field-effect plan or target/media write. Unqualified
categories remain human-only.
The canonical document does not serialize its content-addressed ProposalSet ID;
the ID is derived from those canonical bytes and remains the exact-get input.

Alternatives retain only their bounded candidate value, work/edition/cover
scope, assessment, stable evidence references, hashed correlation groups, and,
for publication candidates, the closed event and target precision.
`publishedDate` alternatives carry target semantics
`manifestation_publication` plus their represented `year`, `month`, or `day`
precision. `publishedYear` is the fixed redundant context result and has no
alternatives. Every other destination carries neither. The exact source event and precision
remain separately available through the evidence reference in the immutable
report.
They do not copy report excerpts, raw provider evidence, responses, URLs,
paths, or image bytes. Provider access time remains in the exact immutable
report addressed by each evidence reference; proposal derivation performs no
provider access and does not refresh or manufacture that time.

The separate `bibliographic-relationships-v1` projection lists allowed
title/subtitle/series combinations without changing a result, alternative, or
source string. Every combination covers all three destinations explicitly.
Candidate selections reference an already-admitted alternative; preserve
selections bind the exact current, missing, null, or empty target-field
fingerprint. A represented alternative assessed as preserving the target uses
the preserve selection while its source representation remains attached to the
combination. Source representation IDs hash the local evidence basis or the
provider, authority, candidate manifestation identity, and work identity.
Identical member tuples are represented once.

Only structured claims from one source representation are grouped. A clean
title with at least two independent correlation groups may replace a decorated
title inside an otherwise source-coherent component tuple. Exact normalized
subtitle or series-name containment is a veto against duplicated text; it
never splits, rewrites, or creates a candidate. Multiple same-scope source
titles and a work title that disagrees with the candidate's explicit edition
title remain report provenance but cannot become title alternatives. If a
source exposes dependent candidates but no complete safe tuple survives, each
affected destination with alternatives is `unresolved_choice` with
`no_safe_bibliographic_combination`. Destinations represented only by their
exact target state remain unchanged in the raw result; the review projection
marks all three tuple members manual without synthesizing evidence.

`propose_change` carries exactly one selected safe candidate.
`acceptable_choice` carries at least two publisher candidates that
the closed field policy accepts and selects none; it is not a conflict.
`unresolved_choice` carries one or more bounded candidates but selects none
because a material contradiction or another unresolved rule remains. `defer`
carries no candidate because evidence or capability prevents a decision
surface. `keep` normally records one positive preservation alternative.
Equivalent language evidence is grouped into that one alternative; publisher
policy may retain multiple alternatives when admitted evidence supports the
current value and every retained candidate is policy-compatible. `reject` is
a closed no-choice variant for a future
explicit policy rejection; the fixed evaluator has no deletion inference and
does not emit it. `ineligible` likewise has no choice. Proposal evaluation
grants no decision, apply, or write action.

The policy is deliberately conservative:

- populated values need positive preservation evidence for `keep`;
- The supported name `English` (case-insensitive), canonical ISO 639-1/639-2
  aliases and regional variants form one
  base-and-exact-optional-script equivalence class before alternatives are
  grouped. The representative retains admitted candidate bytes and merges every
  evidence reference and correlation; the immutable report remains the
  provenance authority for every original representation. The deprecated `bh`
  alias resolves to LoC's favored `bih` identity. Different base languages,
  specified scripts, absent-versus-specified scripts, and malformed or
  noncanonical tags remain unresolved. `und`/`UND` provides no positive language
  authority; its raw facts remain readable. `mul` and `zxx` remain distinct.
  On a fill, policy prefers an actually admitted English name or code and falls
  back to an admitted form; equivalent populated values remain unchanged;
- valid edition-bound publisher and imprint observations may each be an
  acceptable publisher-field outcome. Provider observations must also pass
  an exact `accepted` identifier relation for an ISBN claim on that same
  provider candidate plus the closed format/work contradiction checks. Their
  original values and references stay in separate alternatives: the policy
  asserts no legal equality, hierarchy, parent, imprint, brand, or fuzzy
  company match. Associated-manifestation, work-scoped, unrelated,
  unresolved-scope, malformed, and target-only statements grant no publisher
  authority;
- populated replacement candidates normally need one unique value from at
  least two materially independent correlation groups. Otherwise, bounded
  choices remain `unresolved_choice`. Connections to the same upstream adapter
  are one group; Hardcover, Inventaire and Open Library are distinct groups;
- ordered author and narrator lists, series name/sequence, and date/year remain
  complete and linked rather than being edited piecemeal; untyped contributor
  evidence cannot become a partial narrator list;
- an exact case/width/whitespace-normalized `Unknown` creator from package
  metadata is a controlled placeholder, not an author candidate. The source claim,
  provenance, report identity, and sibling results remain visible and
  unchanged; real creators in the same report keep their source order, while
  `Anonymous`, nonexact names, provider creators, and explicit opening-matter
  creators remain eligible under the ordinary rules;
- publication candidates follow the [current sufficiency rule](#publication-candidate-sufficiency).
  Applicability, event, precision, independence, and contradiction checks precede
  quorum counting; retained evidence is not automatically a vote;
- explicit wrong-work provider evidence also cannot supply automatic genre
  authority; removing a contradiction cannot elect an unsupported survivor;
- local work subtitles and exact-edition provider subtitles remain in their
  respective evidence scopes;
- populated description, genres, and tags are never merged, replaced, or
  cleaned; exact evidence may preserve them and candidates may fill only an
  empty field;
- equivalent description evidence retains every source reference, while the
  grouped alternative uses the canonical exact text of an eligible ordinary
  provider source when one exists. Automatic preview independently rechecks
  that exact text, its plain work-scoped source, and target-work applicability.
  Local or transformed variants may corroborate the group but cannot supply
  ordinary automatic authority;
- ISBNs must have an `accepted` report relationship proving validated,
  edition-applicable evidence; unresolved applicability defers, while retained
  invalid identifier lineage supplies its specific ineligible reason rather
  than appearing as absent evidence; ISBN-13 and ASIN are never manufactured;
- explicit changes require direct evidence, so absence supports neither
  `false` nor `keep`; and
- an existing verified ABS cover is kept. An absent target gets a selected
  cover only when the complete bounded gallery contains exactly one distinct
  safe artifact; complete competing bounded galleries are
  `unresolved_choice`, while incomplete galleries defer with no choices.

There are no scores, provider priority, configured policy, fuzzy matching, AI,
review admission, decision, or apply behavior.

## Publication candidate sufficiency

The current evaluator requires local publication evidence and at least two
independent provider authority correlations supporting the selected precision.
It produces one canonical `publishedDate` alternative at the strongest shared
year, month, or day precision, retaining the supporting evidence references
and correlations. `publishedYear` is derived target context, not a
second selectable publication decision. Multiple connections to one authority
supply one provider correlation; provider failure supplies no vote and is
neither absence nor disagreement.

Provider dates must pass manifestation applicability, including the matching
valid local ISBN family, before voting. Explicit wrong-work evidence remains
inspectable but cannot vote (#1000). Unavailable work identity is neither a
contradiction nor positive identity proof: it does not itself disqualify an
otherwise applicable date or supply missing support. Work-first, copyright,
modification, unknown-event, and incompatible-manifestation evidence never vote.

All admitted observations must be compatible at the projected precision. With
local and two independent provider observations supporting it:

- agreeing `2001-02-03` values yield that day;
- days differing only within February 2001 yield `2001-02`;
- different months in 2001 yield `2001`;
- conflicting years yield no candidate.

A local year plus two agreeing provider days supports only the year under the
current quorum: local evidence does not support the missing month or day.
A timestamp with `Z` or an exact signed `HH:MM` offset may support its stated
calendar day without timezone conversion. Naive timestamps and offset-second
forms remain visible but non-voting. No missing component is invented.

A candidate conflicting with a retained target year remains an unresolved
choice. Candidate existence is separate from deterministic recommendation,
policy routing, qualified persistence authority, and guarded target writes.
The current publication preview independently requires local evidence plus two
provider correlations and consistent publication context; a preview alone grants
no write authority. The current SQL Ask-routing rule names title, series, and
subtitle, not publication. A settled ebook publication candidate therefore does
not by itself establish an eligible Library owner action in Ask mode, as
[the retained #1000 observation](https://github.com/atabbott/mechanical-librarian/issues/1000#issuecomment-5578211874)
records.

The bounded [#1003 investigation](https://github.com/atabbott/mechanical-librarian/issues/1003)
retains this rule pending publication-specific usefulness evidence. Its offline
hypothesis of two supporting correlation groups, including a provider authority,
exposes synthetic candidate opportunities; it is not a delivered or qualified
policy. Retained synthetic qualification and genre-preservation results do not
establish adjudicated real-library publication gains or below-quorum owner-action
benefit. Any candidate-only change needs separate owner acceptance; automatic
authority expansion remains subject to #445 and its qualification boundary.

## Currentness and persistence

External target, media, cover, and artifact validation finishes before the
ProposalSet insertion transaction. The transaction then immediately rechecks
that the immutable report document has the expected digest and that
`current_metadata_reports` still points to that exact report for its complete
scope. Drift inserts nothing. Audiobookshelf or media may change after the last
external read; perpetual cross-system atomicity is not claimed, and the future
review-admission stage must validate freshness again.

`proposal_sets` is append-only and has no current pointer, lifecycle, retry,
job, or normalized evidence tables. Identical exact inputs converge on the
same ID and bytes. The report database accepts only an absent database or its
exact current report-plus-ProposalSet schema; earlier development schemas are
rejected without migration, import, or conversion.

## ISBN-linked titles

An existing provider title can be selected from a successful retained ISBN lane
and positive creator identity without first deciding subtitle or series. The
proposal retains alternatives and meaningful source associations. One provider
is sufficient; same-work print and ebook ISBNs may coexist. Material competing
work, author, title or variant evidence withholds the new election. The current
reason is `isbn_creator_supported_title`; Ask keeps the choice available and
Ignore suppresses its action. See [ADR-0075](../architecture/decisions/0075-independent-evidence-led-titles.md).

## Author evidence authority

The `fixed-conservative-v28` Authors proposal requires complete creator sets. A neutral package set contributes only when an explicit opening author set or independent typed provider set corroborates every member; observations from the same file remain one correlation. Automatic Authors admission follows proposal sufficiency and material conflicts. Independent provider agreement needs no local witness; explicit local-only fill or preservation remains supported, while local-only replacement of populated authors remains manual. Ask, Ignore and owner-final choices remain authoritative (#1083).


## Malformed provider titles

Title and subtitle admission rejects text that the unchanged closed comparison cannot consume. Proposal evaluation ignores typed nonvalid claims while preserving admissible local, same-candidate and sibling-provider evidence. Existing work/recording admission remains authoritative: an unadmitted candidate or recording field does not acquire authority merely because its rejection is typed. Report v47 changes report-derived bindings, while proposal policy `fixed-conservative-v28`, comparison v5 and shadow policy v15 remain unchanged (#1084).

## Unchanged title authority

A supported title preservation can legitimately carry citations without a
bibliographic combination. The qualified no-change witness binds that exact
outcome; it cannot select a replacement. Existing combinations and paired-title
rules continue to govern changes. Explicit title recovery reuses the current
ProposalSet and report rather than acquiring new evidence (#1073).


Genre applicability now uses accepted closed creator display equivalence only to
remove false explicit contradictions (#1074). Publication-date votes share this
negative-only classification. Exact current report v49/proposal policy v32 state
is required; report v48 and proposal policy v29 are historical and cannot be
replayed as current authority. **Clean rebuild required**, with no adoption.


## Same-name series membership (#1075)

A supported series may have the same name as the book whose title is preserved.
The bounded exception applies only to the series name when the title is
preserved (a KEEP title or, since #1253, a work-route title masked for want of a
cleanup or a title set to Ignore) and a candidate fills missing, null or empty
series. Equality compares whole normalized tokens; the original title and series
spelling are retained. Sequence, appended-component and subtitle duplication
checks still apply. Work association, competing positions and independent title
evidence retain their existing authority. Ask, Ignore and owner-final decisions
remain authoritative.
Proposal policy v31 requires a clean rebuild; report v49 remains readable and
can be re-evaluated. Old proposal/store state is rejected before mutation.


## Image-independent ebook ISBN election (#1077)

An empty ISBN may be filled from a valid ebook edition claim with exact accepted
provider/candidate/work/reference binding and retained exact-edition provenance,
even when the associated image is absent or unavailable. A resolved work,
compatible publication/format facts and exactly one verified ISBN family remain
mandatory. Missing provenance, unrelated evidence and competing families refuse.
The selected lexical value follows `preserve_exact` or `prefer_isbn13`; no ISBN
is invented. Populated values, Ask, Ignore and owner-final decisions are unchanged.
This grants no cover selection or write consent. Policy v32 requires a clean
rebuild; report v49 and outcome v9 retain their existing document shapes.
