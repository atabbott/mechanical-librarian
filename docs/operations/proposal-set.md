# ProposalSet operation

The `CreateProposalSet` application operation creates one immutable ProposalSet
from an exact current metadata report. It is used by authenticated review
preparation; source-only contributor inspection uses the typed workbench and is
not an installed runtime capability. The operation reopens the exact report, revalidates its
current Audiobookshelf target projection, selected EPUB bytes, ABS cover, and
both blobs of every referenced private cover artifact, then evaluates the built-in
`fixed-conservative-v19` policy. It does not construct or contact a metadata or
cover provider.

An exact ProposalSet can be reopened offline through the application repository
or typed source-only inspector.

Success is exactly one canonical `proposal-set-v5` JSON document plus a
newline. Failure emits no document and one value-free diagnostic.

Only `proposal-set-v5` documents under `fixed-conservative-v19` and the exact current
`proposal_sets` table envelope are accepted. A database with the earlier
`fixed-conservative-v1`, `fixed-conservative-v2`, `fixed-conservative-v3`, or
`fixed-conservative-v4`, `fixed-conservative-v5`, `fixed-conservative-v6`,
`fixed-conservative-v7`, `fixed-conservative-v8`, `fixed-conservative-v9`,
`fixed-conservative-v10`, `fixed-conservative-v11`, `fixed-conservative-v12`,
`fixed-conservative-v13`, `fixed-conservative-v14`, `fixed-conservative-v15`, or
`fixed-conservative-v16`, `fixed-conservative-v17`, or `fixed-conservative-v18`
table shape is incompatible
and is rejected before mutation. A clean rebuild is required; there is no
proposal migration, import, conversion, or compatibility parser.

## Consensus title qualification

The F35 (#884) provider-consensus title election is admitted by the current
#445 F19a successor manifest. Its closed title tranche is qualified separately
from the current F20 v4 route, which continues to exclude title. Earlier F19a
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
alternative byte for byte, so a collapse never rewrites casing or spacing. The
dissent and width guards compare under the closed title comparison.

A collapsed title carries one alternative, so a representation that once mapped
to two competing title forms no longer withholds a bibliographic combination;
the `no_safe_bibliographic_combination` release follows from the collapse itself.
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
- canonical ISO 639-1/639-2 aliases and regional variants form one
  base-and-exact-optional-script equivalence class before alternatives are
  grouped. The representative retains admitted candidate bytes and merges every
  evidence reference and correlation; the immutable report remains the
  provenance authority for every original representation. The deprecated `bh`
  alias resolves to LoC's favored `bih` identity. Different base languages,
  specified scripts, absent-versus-specified scripts, and malformed or
  noncanonical tags remain unresolved;
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
- selecting a `publishedDate` at year, month, or full-day precision always
  requires two materially independent correlation groups;
- two independent month-capable observations that agree through `YYYY-MM`
  produce one month candidate. Differing exact days in that month are
  complementary month evidence, not competing month candidates. The candidate
  carries the union of their evidence references and correlation groups;
- work first-publication, copyright, modification, and unknown-event evidence
  never vote for manifestation date or year fields;
- independent direct year-only edition evidence becomes a year-precision
  `publishedDate` candidate and is not padded into a month or day;
- one month observation alone has no candidate. A month candidate
  requires independent month-capable support; day evidence may become a
  `publishedDate` candidate, while a signed-offset timestamp may reduce to that
  exact stated day without offset conversion. Source precision remains in the
  cited report fact;
- a timestamp without `Z` or an exact signed `HH:MM` offset remains visible in
  the report but supplies neither a year nor day vote; this includes naive and
  parser-admitted offset-second forms;
- a day plus an agreeing year supports the shared year twice but the day only
  once. A day plus an independently agreeing month supports that month.
  Different months preserve only their shared year; different years remain
  conflicting. No rule fills or invents a missing month or day;
- provider publication text whose event or manifestation linkage is unresolved
  remains visible evidence and never becomes a selectable candidate;
- local work subtitles and exact-edition provider subtitles remain in their
  respective evidence scopes;
- populated description, genres, and tags are never merged, replaced, or
  cleaned; exact evidence may preserve them and candidates may fill only an
  empty field;
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
