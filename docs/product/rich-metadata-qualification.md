# Rich-metadata qualification candidate

The F20 candidate manifest is the public, aggregate-safe record of the frozen
rich-metadata qualification contract and its exact option-level decisions. It
grants decision-only Automatic authority to the passing options; it does not
grant effect consent, construct a target write, or change provider selection.

## Current compact authority

The stable [F19a](../reference/f19a-qualification-current.json),
[F20](../reference/f20-rich-metadata-qualification-current.json) and
[F46](../reference/f46-audiobook-qualification-current.json) records each bind
canonical JSON of one `authority` object: family, media and the exact permitted
operations, with F20's dispositions and F46's paired operations. The runtime
constant of each family, and the digest every automatic decision carries, is the
SHA-256 of that object. Existing constant and serialized digest names retain that
narrower meaning. The record's provenance reference never grants authority or
rotates the digest, and the compiled runtime reads no JSON record, Git history or
network archive.

Under [ADR-0087](../architecture/decisions/0087-operation-bound-qualification-authority.md)
authority is the permission set. It rotates only when a permitted operation,
disposition or pair is added, removed or changed, or when a permitted operation
may perform a different target write. Widening also needs fresh aggregate
measurement evidence in the provenance record; narrowing does not. Every other
source change leaves the records and runtime constants unchanged and needs only
the behaviour, security and build proof any change needs. Run
`uv run python scripts/verify_qualification_records.py` to check that each
record's shape is closed and that each runtime constant, located as its actual
top-level literal assignment, equals the digest of its record's authority.

The shared [provenance record](../reference/qualification-provenance-current.json)
retains the complete F46 measurement, shared ebook measurement, fourteen
measurement artifacts and immutable measurement origins. They are frozen and
never regenerated. No historical quality was rescored, no fresh holdout is
claimed and no production write occurred. Current records do not claim their
own final commit SHA; exact artifact receipts bind the final commit, payload and
completed checks. F38 and separately consumed cover records retain their own
scope.

Only the current digest decodes: a decision minted under any earlier digest is
invalid. **Clean rebuild required** for #1396: outcomes advance to
`shadow-outcome-set-v11`, whose exact SQL constraint and API literal refuse
predecessor stores before writable use, even when no outcomes exist. There is no
adoption or migration. The sections below are frozen qualification and
measurement records. Where they say earlier decisions remain readable, they
describe the regime before #1396; earlier records are readable in Git history
only.

## Useful audiobook metadata (#1047)

[F46 v8](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v8.json) registers
73 measured individual operations and one separately guarded title/subtitle pair.
It preserves the previous 44 operations and adds reached work and recording
operations, including durable title/author no-change, complete author normalization
and Add, descriptive subtitles, original publication dates, publisher, synopsis,
explicit tag unions, associated identifiers, material cover replacement and
positive-only adult-product flags. The exact operation inventory is in the record;
an available candidate does not imply that every representation or operation is
qualified. [F19a v25](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v25.json) and
[F20 v39](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v39.json) bind the
current consumers while preserving the existing ebook authority.

Work facts require an applicable work relationship. Recording facts retain their
field-specific association requirements. Safe regional synopses use the preferred
applicable authority; equally preferred sources use stable provider/record identity
order, with alternatives retained. This is deterministic selection among equally
acceptable texts, not a claim that one market or longer text is superior.

Current retained execution and controlled synthetic effects are recorded separately
from immutable historical source quality. The exposed development examples are not
a new holdout or a population accuracy estimate. Material cover replacement remains
a controlled positive. Five reached ISBN refusals retain their actual reason and
appear as unchanged Activity history, without manufacturing a decision or write.

The retained authority/effect measurements and the final read-model source are
bound separately. Unchanged authority source is checked against the measured
commit; the later read correction reconstructs selected scalar values through
the existing effect validator, retains lookup-only narrator evidence, and shows
combined audiobook tags beside the original owner choices. These read/UI changes
have their own current consumer proof and do not rescore the retained corpus.

Report v44, proposal v8/policy v27, packet v9, decision policy v14, action policy v4
and shadow outcome v6 require a clean rebuild. Historical records retain their
original bytes; old authority cannot authorize current effects. Category defaults,
Tags off and separate default-off application consent remain unchanged. Adding
Explicit to the sixteen supported effect destinations invalidates prior consent
envelopes; an owner must grant new permission before an explicit flag can be set.

## Historical title-consumer qualification (#1007)

The following records that qualification stage, not the current F46 registration.

The historical [v29 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v29.json)
binds #1007's title and packet consumers, including replacement-only and
complete ordered creator corrections. Published v25 remains immutable historical
evidence. The current continuity proof is
the 13-option actual persistence/effect-authority test and two current replay derivatives from retained raw capture
composed across all 13 options (26 previews). The historical 49-report
v34 and 15-report v37 corpora do not parse under the current report contract;
they were neither relabelled nor replayed for this successor. Earlier metrics
below remain historical evidence, not a current full-corpus equivalence claim.
The option dispositions remain unchanged. Exact current source bindings are
recorded separately from historical measurements.

The base integration preserves incoming main's repinned v10/v24 at their
canonical paths and retains both the original-base and intervening-main bytes under digest-qualified
historical filenames. Current v15/v29 record each origin commit, original path,
digest and retained path; immutable v11/v25 references resolve by that original
digest. All v11/v12/v13/v14/v25/v26/v27/v28 bytes remain unchanged. The same 52 title cases and
current F20 consumers are remeasured through the merged code. The reached F46
module is bound as an inert dependency: its record remains absent and its passing
set empty, so no audiobook authority is added.

The immutable v39 report objects are rejected by the current parser. The two
v40 derivatives rebuild seeds from unchanged captured target, file and local
facts, then run current provider adapters over the original raw HTTP exchanges.
They are new reports, not schema relabels or unchanged historical replay. The
manifest binds the capture, configuration, local inputs, independent truth,
reconstruction evaluator and derivative results; no external calls are made.

The current successor also measures six language-script controls separately:
four supported title opportunities and two explicit-script conflicts. Regional
variation and missing script evidence remain neutral. Three further controls
retain an existing local title result when provider titles are absent or
malformed; these are preservation checks, not automatic opportunities. The
original 52-case matrix and published v14/v28 artifacts remain unchanged.

## Exact description sources (#1001)

The [v15 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v15.json)
binds grouped description alternatives to the canonical exact representation
from an eligible ordinary provider source. Equivalent local evidence remains
inspectable, but cannot replace the applied bytes or borrow the provider's
automatic authority. Preview independently rechecks exact value, ordinary
plain-text provenance, transformation exclusion and target-work applicability.
The existing competition quality floor and provider authority order are
unchanged. V15 retains the exposed F37 description replays and the complete
F20 non-description and series continuity evidence; it makes no fresh holdout
claim.

## Contradictory work evidence (#1000)

The [v14 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v14.json)
binds the target-work contradiction guard for publication dates and automatic
genres. Claims about another work remain inspectable, but they cannot become a
field decision or persisted candidate outcome. Where identity proof is absent,
the candidate stays unresolved rather than being promoted or discarded. The
same successor preserves every previously qualified option through the existing
non-series and exact-source continuity routes. The series route replays the
original 24-work confirmation and 12-work final captures. Their canonical v37
documents are relabelled to v38 with a recomputed content-derived report
identity; every other field, sealed input binding, truth label, scorer and
threshold remains unchanged.

## Qualified series fill (#895)

The historical [v13 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v13.json)
binds the applicability repair: it refuses unrelated ISBN work candidates while
permitting the bounded
[source-attested identity formatting rule](../architecture/decisions/0070-isbn-backed-series-identity.md).
Owner-approved exposed regression retains the existing works, captures, truth,
scorer and thresholds. Both batches were already exposed; this is not a fresh
confirmation or another holdout pass.

Each option now makes 9 correct complete projections from the 24-work batch
(15 abstentions), and 4 from the 12-work batch (8 abstentions), with no wrong
projections or negative/unknown-source fills. Supported coverage is 9/19 and
4/7. Two previously correct fills remain reviewable because their identity
formatting falls outside the permitted grammar. All surviving previews retain
their exact values, order, identifiers and references; six option projections
are removed across the three options, not six independent books.

Membership and sequence retain precision 1.0 and at least three distinct scored
books per option per batch. The 24-work batch has nine scored membership books
and seven scored sequence books, plus one missing and one unknown position.
The 12-work batch has four distinct scored books for each claim. Its full
`all_qualified` output contains six membership occurrences, five known sequence
occurrences and one unknown; repeated convention-qualified views do not increase
independent support. Actual desired values are separately scored: four correct
memberships and positions in that batch.

The [v12 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v12.json)
qualified the three existing series options for empty Automatic targets. Its
series rules preserve populated targets and
permit an absent position without inventing a number. A unique existing source
bundle may resolve compatible subset alternatives, retaining its exact values,
order and evidence; no union is constructed. Known positions compare by exact
text or ordinary unsigned decimal equality, never inferred numbering. This
shared proposal correction also applies to those compatible Ask alternatives.
Failure checks follow the selected provider candidates and their contributing
dispatch provenance, not an unrelated provider with the same query kind.

V12 remains the unchanged historical record below. The first 15-work
confirmation produced no automatic series projections and
remains failed, spent development evidence. The repaired, frozen candidate then
passed a fresh 24-work confirmation and its once-only 12-work final capture.
Each option made 10 correct complete membership projections in confirmation and
5 in final, with no incorrect projections or negative-control false positives.
Coverage of supported works was 10/19 and 5/7 respectively; recall is measured,
not a pass gate. One confirmation and two final source-label corrections were
adjudicated independently of predictions; their original evaluations and
acceptance receipts remain bound. These offline correction replays changed no
candidate, scorer, threshold or capture and are not extra independent batches.

Membership and sequence are separate claims. Each requires precision 1.0 and
three distinct scored works per option per batch; unknown positions are
unscored, while known wrong positions block the affected option. Confirmation
has eight distinct correct sequence cases, one absent number and one unknown
number per option. Final has five distinct correct sequence cases per option.
The full `all_qualified` final output has seven raw membership occurrences,
including convention-qualified views of the same grouping, six correct sequence
occurrences and one unknown; its support denominator is still five books.
Actual target values are scored separately from full qualified memberships.

The runtime binds the v15 digest, source dependencies, closed option
matrix and tested zero-tolerance controls. Metrics alone do not authorize
activation. Historical v11/v12/v13/v14 bytes and decisions remain readable, while fresh
decisions and guarded effects require the exact current qualification. Retained
historical non-series compiler evidence for v13 compared 980 serialized
observations unchanged. V14 changes 18 genre previews in the two-scenario
continuity replay, restoring the 15 correct same-mode option projections that
the first #1000 candidate withheld; no other field changes. The rotation also
changes authority-derived identities. The earlier
50-case gold programme and operator evidence remain retained evidence, not 50
new cases. The exact-clean-head retained series and non-series replays are bound
in v14 and is retained unchanged by v15. Supported isolated field effects and installed acceptance remain
required delivery checks; the manifest does not claim a future receipt or
replace those checks.

## Historical qualification record

Qualification is field-option-local. Prior use of the same work for unrelated
extraction, provider, cover, or metadata development does not disqualify it.
A case is excluded only when its expected result was used to implement or tune
the same field option, or when that option was changed after its current output
was viewed without versioning the evaluator or rule. An unseen or final
holdout is not required.

The candidate replayed retained provider responses for 49 accepted real-work
reports through the current provider adapters, proposal evaluator, and rich
preview compiler without network access. Public output contains only aggregate
metrics and contract identities; source material, expected values, and
case-level outcomes remain private.

The gate was frozen before scoring: at least three eligible cases, at least
three automatic predictions, 100% automatic precision, and no zero-tolerance
failure. Recall is reported but is not a pass gate. This favours safe
abstention while avoiding a novelty requirement unrelated to the option being
proved.

Genres, tags, and descriptions do not have one objectively correct semantic
answer. They are therefore not scored against a preferred taxonomy, tag set,
or prose description. Their objective gate is exact provenance and operation:
one complete ordered work-bound source list, no cross-source synthesis or
taxonomy translation, and the exact selected fill/add/replace behavior; or one
exact safe work-bound description, without rewriting or silent truncation.
Google visible-text remains a separate transformation and cannot qualify as
ordinary safe plaintext.

All three series policies passed after series correctness was separated from
preference: a prediction is correct when it is an allowed membership for the
bound work, while preference remains a separate descriptive ordering. The
series-abstention correction reviewed 49 series rows and 118 retained reports,
including 19 direct EPUB reopenings: its 49-v1, 49-v2, series-20, and retained
provider-document development cohorts each reached 100% precision on their
positive scored denominators. The two mixed cohorts each include two allowed
memberships with explicitly unknown position, which are unscored rather than
treated as exact proof. This is development-exposed evidence, not a holdout;
that historical record required the separately frozen confirmation now recorded
above. The historical v5 record gave the two ISBN policies 22 correct
predictions from 41 eligible opportunities, each at 100% precision.
The admitted proposal and decision retain every qualified membership; the
existing Audiobookshelf effect planner projects only the preferred first
membership because that target accepts one series value.

Subtitle was adjudicated more narrowly at ebook-manifestation scope. Only 2 of
7 fill-empty predictions were valid: two other proposals duplicated text
already present in the title, and the remainder were series marketing,
proof-only, or later franchise descriptors. Corroborated split made no
predictions. One valid proposal exactly matched a subtitle extracted from the
ebook package. The other was established by revised-edition evidence that the
current extractor does not yet expose as a structured subtitle claim. Two of
the invalid proposals were nevertheless repeated by multiple catalogues, so
provider count is not a safe substitute. A future subtitle candidate must bind
the selected ebook manifestation, remain distinct from the title, and exclude
marketing or status text. Both subtitle policies were therefore initially
`not_qualified`.

The eighth candidate execution requalified `subtitle:fill_empty` by making the
book's own packaged metadata the required corroborator. When the target
subtitle is empty and provider catalogues disagree, the proposal engine now
elects only the one competing value the book's own metadata attests
(`local_attested_subtitle`); the automatic projection separately abstains from
any fill whose value no independent claim matches
(`subtitle_local_corroboration_required`). Every previously invalid
prediction — series marketing, proof-only, and franchise descriptors — lacked
a matching local claim and stays an owner decision. Evaluated with the target
subtitle varied across the supported `missing`, `null`, and `empty` states
while preserving observed provider evidence, `subtitle:fill_empty` made 5/5
correct predictions in each state, 15 total at 100% precision and 55.6%
recall, and is now `passing_automatic`. `subtitle:corroborated_split` again
made no predictions and remains `not_qualified`.

The ninth candidate execution hardened the corroboration boundary: the elected
and projected fill must itself be provider-backed, so the book's packaged claim
corroborates a catalogue value and never elects itself when no provider offers
a subtitle. Three of the eighth execution's predictions were exactly such
local-only fills and now abstain as owner decisions. Re-evaluated across the
same three synthesized target states, `subtitle:fill_empty` makes 4/4 correct
predictions in each state, 12 total at 100% precision and 44.4% recall, and
remains `passing_automatic`. The same execution refreshed the genre evidence
under the current field-parameterized state matrix: genre fill-empty makes 5/5
correct predictions in each state, 15 total, and genre add and replace each
make 4, all at 100% precision.

The tenth candidate execution verified every recorded metric unchanged after
the projection began citing the corroborating local subtitle claim alongside
the provider evidence, so a persisted automatic subtitle decision's durable
record names its decisive corroborator.

F36 rotates only the exact automatic-decision envelope around those results.
Its attempted replay stopped in preflight when the current Open Library adapter
requested beyond the retained response set, before scoring and without network
access, so it did not consume an eleventh candidate execution. A source-bound
full-output-equivalence receipt instead verifies the compiler, policy, target
shape, option dispositions, and guarded persisted effect path unchanged, with
the prior complete 49-report result retained. The candidate execution count
therefore remains ten.

The current policy separately treats the exact provider-edition label `A Novel`
as a generic marketing subtitle. It preserves a blank target without creating
a subtitle proposal. The vocabulary is provider-only and deliberately limited
to that exact normalized label; local package evidence and any longer subtitle
remain ordinary evidence. This is a proposal-policy rule, not F20 automatic
authority.
The twelfth candidate execution applies exact target-work applicability to
automatic tag selection. Conflicting, component, enclosing, incomplete, and
lane-less tag evidence remains available to the owner but cannot influence
automatic uniqueness, quality, or authority. A target-backed WORK lane proves
identity through the exact normalized title and complete contributor set; a
target-backed ISBN lane must additionally prove that every usable candidate
ISBN resolves to the target family, because an edition identifier alone cannot
bind enclosing or component work metadata. Prior-manifest tag decisions remain
readable history but are not current effect authority. A fresh 49-work capture made 456
requests through the three current provider adapters. Main and the candidate
each produced seven correct decisions for every tag option at 100% precision,
with no changed outcomes, lost correct projections, or gained incorrect
projections. A separate replay of the retained provider documents also matched
main's aggregate metrics exactly. An exposed real-library confirmation split
retained 58 candidates across 23 cases and the applicability gate admitted 20
at 100% precision with zero wrong-work admissions. The final F37 holdout was
not used for tag qualification.

The same current-provider evidence did not justify extending the rule to every
other work-bound field. For genres, correct add and replace decisions each fell
from 9 to 4, while fill-empty decisions across the three target states fell
from 30 to 12. Current main and candidate adapters both produced zero series
decisions, so they could not positively demonstrate preservation of the prior
19/20/20 qualified series results. Genre and series applicability are therefore
deferred; their earlier automatic dispositions and metrics remain authoritative
and unchanged rather than being dequalified by a different evidence mode.

The fifteenth and sixteenth candidate executions qualified the automatic
description tiebreaker after adopting the same target-work applicability rule.
In that historical qualification, applicable descriptions had to pass the frozen
80-character, 16-word floor and
must not end in a visible cutoff; the survivors then follow the fixed Hardcover,
Google Books, Open Library authority order without combining text. The exposed
confirmation selected 13 of 23 cases and the one-shot sealed holdout selected 14
of 25, both at 100% precision with zero failures. After review found the floor
counting an inapplicable alternative, the seventeenth and eighteenth executions
replayed both splits against the corrected compiler. Results were unchanged;
the holdout is explicitly recorded as spent correction continuity, not a second
unseen execution. A further review found transformed provider text still counted
as safe plain-text competition. The nineteenth and twentieth executions replayed
both splits after filtering that text before the single-versus-multiple-candidate
decision; results again remained unchanged. The v9 manifest binds the latest
correction receipts, whose chain retains the earlier correction and original
one-shot receipts. Review then found that an otherwise-qualified description
from an authority outside the frozen order could disappear from a competition.
The twenty-first and twenty-second executions replayed both splits after making
that competition abstain; results again remained unchanged, and v9 binds those
latest correction receipts.

F20 v9 preserves the separately qualified two ISBN-form options under the
current deterministic identity rule. Their final development result and archived replay
show 100% precision across the supported empty-field states, while the sealed
holdout is carried forward by a continuity receipt for the unresolved-work
identity guard. The goal
is useful work metadata; manifestation identity is the evidence that binds it.
A useful published-work ISBN carried by a proof remains eligible only through
that bound identity evidence; proof status alone neither elects nor disqualifies it.

The twenty-third candidate execution (#896) re-scores `subtitle:fill_empty` on
segment corroboration. A colon segment stripped from any WORK-scope title claim,
local or provider, now corroborates the fill on cross-source terms; a segment
from the candidate's own source never does, and no provider may state a
different subtitle. At least one provider must carry the segment, and the
provider authorities stating the plain title it was stripped from must be at
least as many as the authorities carrying the decorated form, counting the local
title on the decorated side. Every other option replays unchanged, and the
option keeps 1.0 precision with zero structural or unsupported outputs.

The historical machine-readable candidate described in this section is
[`f20-rich-metadata-qualification-candidate-v11.json`](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v11.json).
The v10 manifest remains a readable historical record.

## Retained title-component associations (#1002)

The [v18 F20](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v18.json)
and [v6 F19a](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v6.json)
successors bind proposal policy v23. Exposed retained replays preserve 17 correct
title tidies and 19 keeps across 75 cases, 21 correct subtitle fills in each of
missing/null/empty target states across 113 cases, all 108 series projections or
abstentions, and all 980 other-option projections or abstentions. No false
decisions were observed; subtitle recall remains 21/76 per state. These are
continuity replays, not fresh holdouts or a library-wide yield claim.

Synthetic admitted-case proof covers the repaired one-plain-authority tuple.
Series tidy remains qualified under all three options; an already plain title
with only a decorated provider work title still requires owner review because
the association cannot supply independent series work applicability. Earlier
manifests remain immutable historical evidence and cannot authorize new effects.

Settled retained components remain actionable under their case-bound Ask policy.
Detail, Library counts and Attention use that original policy consistently;
Automatic precedence and recorded owner decisions remain unchanged. This review
projection follow-up changes no scoring input or option outcome, so the retained
measurements remain bound to their original measured source commit.

## Historical audiobook operation qualification (#967)

This section records the earlier six-field qualification, before #1047.

F20 retains its ebook authority; F46 separately qualifies audiobook operations
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).
That registration’s 44 identities covered supported title, author, narrator, language,
series and genre operations. Complete recording credits and admitted work
associations reach current proposal, decision and guarded-effect consumers.
ASIN and other unqualified operations remain manual.

Title and author preserve operations are withheld because retained software
correspondence rejected author no-change authority and suspended title
preservation admission. Their independently accepted source quality is unchanged.
The remaining registration passed all 530 retained software scenarios, with
1,165 verified synthetic-service effects, 162 no-change facts and no errors,
value/operation mismatches or unintended suspension. These repeated scenarios
are not independent books. The default policy produced 25 actual changes across
six of the twelve source-quality books; eleven source-correct title/author
preservations remain manual after the operation withdrawal.

Adding or withdrawing qualification changes persisted interpretation. The exact
shadow-outcome successor and clean rebuild prevent old stored decisions from
being reinterpreted under the new set. Ordinary startup accepts absent or
exact-current state only; no adoption or relabelling is offered.

## Image-independent ISBN evidence (#1077)

Both existing F20 ISBN options retain their permissions under proposal policy
v32. A frozen subset of 19 exposed development cases was reconstructed through
current constructors and round-trip validation against unchanged accepted gold.
For each option, original and labelled image-unavailable scenarios each produced
15 distinct correct positives with precision 1.0 across missing/null/empty
states, with zero wrong, invented or preview-bypass results. The baseline reached
9 distinct positives on original reports and none after image unavailability;
removing exact-edition provenance yielded no predictions in either version.

Sixteen development cases remained unavailable (10 missing WORK lineage, six
unmapped). This bounded correspondence evidence is neither a fresh holdout nor a
population accuracy estimate. Historical evaluators, gold and spent holdout were
unchanged. All three shared compact authorities rotate while preserving their
exact permission sets and decoding-only historical identities. Clean rebuild
required; cover authority and existing guarded-effect requirements are unchanged.

Synthetic Inventaire ISBN responses without an image also traverse the actual
adapter, cover observer, report constructor and persisted owner outcome. They
retain exact edition/work provenance without image acquisition; missing binding,
unknown media and work-search controls confer no ISBN authority.

## Material ISBN/title conflict precedence (#1132)

An explicit ISBN/title material conflict remains manual even when a supported
candidate equals the current title. Automatic records a conflict without
suspending title automation; Ask and Ignore retain their policy behavior.
Ordinary title preservation, independently supported replacements and explicit
owner choices retain their existing authority and effect guards. Shadow policy
v19 and compact successor qualification bindings require a clean rebuild.
