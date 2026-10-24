# Rich-metadata qualification candidate

The F20 candidate manifest is the public, aggregate-safe record of the frozen
rich-metadata qualification contract and its exact option-level decisions. It
grants decision-only Automatic authority to the passing options; it does not
grant effect consent, construct a target write, or change provider selection.

## Current compact authority

The stable [F19a](../reference/f19a-qualification-current.json),
[F20](../reference/f20-rich-metadata-qualification-current.json) and
[F46](../reference/f46-audiobook-qualification-current.json) records bind canonical
JSON of their `authority` objects only: family/media, semantic revision, semantic
input digest and exact permitted operations, dispositions and pairs. Existing
runtime constant and serialized digest names retain that narrower meaning.
Evidence and predecessor references never grant authority or rotate these hashes.
The compiled runtime reads no JSON history, Git repository or network archive.

The shared [provenance record](../reference/qualification-provenance-current.json)
classifies a reviewed baseline of 572 source paths: 140 semantic and 432
provenance-only. Newly introduced record, verifier and fixture paths are explicitly
provenance-only; retired archive paths leave the active inventory. These are
starting counts, not permanent totals. New paths and changes of ownership require
explicit review of the inventory and its verifier pin. Moving authority into a
provenance-only file requires reclassification. All three families share the
semantic partition. Mixed `review_web.py`, `interfaces/web/app.py` and
`field_effect_activity.py` remain semantic even for copy edits. Frontend copy
and provenance changes still require applicable behavior, security and build proof.
Renovate-managed build declarations (`Dockerfile`, `.github/workflows/public-gate.yml`)
are dependency build provenance outside the inventory, as ADR-0076 treats the
frontend manifests; their contract tests and the remote gates prove them.
The package inventory includes non-Python resources: the setup/frontend
`settings_vocabulary.json` is explicitly provenance-only. Only the exact `py.typed`
marker and `__pycache__/*.pyc` bytecode are omitted. Frontend runtime assets and
identity are generated under the separate build tree, not exempted source paths.
Unknown source resources require classification review. Current record and
family-specific authority keysets are closed; incidental metadata cannot enter
authority. Runtime binding reads and refreshes locate actual top-level AST literal
assignments, so comments and string examples cannot impersonate a binding.
Only the precisely named existing circular-binding assignments use AST exclusions;
unknown exclusions fail. There is no general semantic classifier.

Run `uv run python scripts/verify_qualification_records.py` to check bindings;
use `--refresh` only after the preservation steps below, then inspect the diff.
Before changing semantic inputs or authority, retain the current committed record's
full Git SHA, path and byte digest in the archive index, grouped by that source SHA.
Point each successor's predecessor reference at that immutable entry, increment its
semantic revision, and append the displaced authority digest with its exact frozen
permissions to the compiled historical table. Preserve existing table entries and
extend the independent historical parity expectation; never regenerate genuine
predecessor documents. Then refresh bindings and prove old decoding/no authority,
current production and persistence admission. The command refreshes hashes only;
it does not archive records, revise history or grant migration authority. When
persisted interpretation changes, assess the clean-rebuild boundary explicitly.
For provenance-only edits these preservation steps are unnecessary because the
authority object and digest remain unchanged. No full cumulative record is created.
The provenance record retains the complete F46 measurement, shared ebook
measurement, fourteen measurement artifacts and immutable measurement origins.
No historical quality was rescored, no fresh holdout is claimed and no production
write occurred. Current records do not claim their own final commit SHA; exact
artifact receipts bind the final commit, payload and completed checks.

The [archive index](../reference/qualification-archive-index.json) binds each
retired record to an immutable commit, path and content digest. Historical links
below resolve there in Git history, never through ordinary validation or runtime.
All 140 prior digest/permission pairs and the three displaced current digests
remain historical-only compiled decoding entries, sharing ten independent frozen
permission sets. F38 and separately consumed cover records retain their own scope.

**Clean rebuild required:** outcome `shadow-outcome-set-v9`, its exact SQL
constraint and API literal refuse predecessor stores before writable use, even
when no outcomes exist. There is no adoption or migration. F20 contributes to
persisted action/outcome identities; at that boundary F46 cover decisions resolved a live rule
identity, so document decoding alone could not establish safe startup. The historical
qualification-state bridge target remains intentionally unchanged. Authored
description authority, policy elections, report and proposal versions are unchanged.

## Same-schema retained qualification decoding (#1148)

Current-schema Preview outcomes remain readable after a qualification binding
rotation only when reconstruction from current inputs matches every field and
canonical identity under one known historical F20 digest. Only actions matching
the current F20 identity formula participate; ordinary ebook ASIN actions remain
unchanged and exact. Their authority remains
`none`; this does not adopt an older schema or change historical rebuild decisions.
Audiobook cover decisions retain their recorded F46 digest and exact frozen option
permissions. Newly created decisions bind current qualification internally.
Historical decisions remain readable but are refused by authority resolution,
scheduling and persisted-plan currentness checks before any effect. Ebook cover
qualification is unchanged.

## First-throttle reason successor (#1367)

The current F19a, F20 and F46 records move to semantic revision 65 over base
`aae7c4dc`, whose record bytes the archive index keeps. Their runtime digests
join the prior-manifest histories with their frozen permission sets, so
decisions minted under them stay readable without current authority. The
revision follows provider recovery naming an ebook lane's classified
non-terminal failure when its recovery job is enrolled, without changing the
scope's health, schedule or budgets. No schema or persisted document changes,
so no clean rebuild is required, and no operation gains or loses automatic
authority.

## Policy activation package successor (#1369)

The current F19a, F20 and F46 records move to semantic revision 64 over base
`96e1728e`, whose record bytes the archive index keeps. Their runtime digests
join the prior-manifest histories with their frozen permission sets, so
decisions minted under them stay readable without current authority. The
revision follows policy activation retiring each cohort item's current review
package with its lineage and superseding an undismissed finished transition,
and retry resolving an item superseded by a newer report and reopening the
scan of work it restarts. No schema or persisted document changes, so no clean rebuild is
required, and no operation gains or loses automatic authority.

## Owner-entered values successor (#1285)

The current F19a, F20 and F46 records move to semantic revision 63 over base
`b30b2ed1`, whose record bytes the archive index keeps. Their runtime digests
join the prior-manifest histories with their frozen permission sets, so
decisions minted under them stay readable without current authority. The
revision follows the owner-decision document's new outcomes, the widened
decision check, the withdraw eligibility and previous-value reads, and the
admission that carries a manual hold to the successor case. A clean rebuild is
required; no proposal, shadow, automatic-decision, policy or qualification path
reads an owner-entered or previous value, and no operation gains or loses
automatic authority.

## Provider throttling successor (#1282)

The current F19a, F20 and F46 records move to semantic revision 62 over base
`2b71e4fc`, whose record bytes the archive index keeps. Their runtime digests
join the prior-manifest histories with their frozen permission sets, so
decisions minted under them stay readable without current authority. The
revision follows the web interface's new optional preparation field and owner
Retry now command, the recovery store's read-only throttling query and the
composition's post-commit recovery cutover: report bytes are unchanged, no
clean rebuild is required, and no operation gains or loses automatic authority.

## Activity check groups successor (#1281)

The current F19a, F20 and F46 records move to semantic revision 61 over base
`b1b7a49c`, whose record bytes the archive index keeps. Their runtime digests
join the prior-manifest histories with their frozen permission sets, so
decisions minted under them stay readable without current authority. The
revision follows the web interface's new optional Activity field: report bytes
are unchanged, no clean rebuild is required, and no operation gains or loses
automatic authority.

## Degraded candidate successor (#1283)

The current F19a, F20 and F46 records move to semantic revision 60 over
predecessor `8bed9dff`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Operation membership and historical measurements are unchanged.

Only automatic authority narrows. An author or narrator candidate that only
strips a current member's accents now preserves that spelling, so a former
replacement is a preservation. Any other contributor candidate that strips a
current member's accents, a credit-prefixed contributor candidate, a title
candidate with an announcement part and a mostly quoted praise description are
withdrawn from automatic selection and recommendation and remain owner choices.
Proposal policy v36 and decision policy v23 require a clean rebuild.

## ASIN-only plan successor (#1306)

The current F19a, F20 and F46 records move to semantic revision 59 over
predecessor `32360e99`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Operation membership and historical measurements are unchanged.

Hardcover and Open Library now answer an audiobook plan made only of ASIN lanes
with no request and no lane instead of an `unavailable` observation. That
changes no proposal or decision, so no operation gains or loses authority.

## Provider log events successor (#1299)

The current F19a, F20 and F46 records move to semantic revision 58 over
predecessor `cda45e0c`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Report bytes are unchanged, so no clean rebuild is required. Operation
membership and historical measurements are unchanged.

Preparation and recovery dispatch now write value-free provider events to
stderr after each dispatch or settlement they describe. The events change no
request, report, proposal or decision, so no operation gains or loses authority.

## Band-corroborated admission successor (#1260)

The current F19a, F20 and F46 records move to semantic revision 57 over
predecessor `78f3e2c6`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Report v57 binds the new semantics; **clean rebuild required**. Operation
membership and historical measurements are unchanged, and focused synthetic
proof is not library scoring.

An audiobook's asserted work lane may now admit a recording through a
band-corroborated production. That changes which products reach the decision
pipeline, not what any qualified operation may do with them, so no operation
gains or loses authority.

## Dispatch classification successor (#1258)

The current F19a, F20 and F46 records move to semantic revision 56 over
predecessor `e7cf0afb`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Report v56 binds the new semantics; **clean rebuild required**. Operation
membership and historical measurements are unchanged, and focused synthetic
proof is not library scoring.

Each audiobook provider lane records how its dispatch ended. The record is pure
observation: it changes no request, admission or decision, so no qualified
operation gains or loses authority from it.

## Work-level series and title successor (#1253)

The current F19a, F20 and F46 records move to semantic revision 55 over
predecessor `b34ece63`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Proposal policy v35 binds the new semantics; **clean rebuild required**.
Operation membership and historical measurements are unchanged, and focused
synthetic proof is not library scoring.

The work-only audiobook route offers series and title choices from witnessed
book sources and marks description and genre choices that only trim or extend
the current value. It creates no automatic decision or effect, so no qualified
operation gains or loses authority from it.

## Audiobook local claims and quality floor successor (#1250)

The current F19a, F20 and F46 records move to semantic revision 54 over
predecessor `4012ed61`, whose record bytes the archive index keeps. Their
runtime digests join the prior-manifest histories with their frozen permission
sets, so decisions minted under them stay readable without current authority.
Shadow decision policy v22 and report v55 bind the new semantics; **clean
rebuild required**. Operation membership and historical measurements are
unchanged, and focused synthetic proof is not library scoring.

An audiobook synopsis that is only a credit line, a format word, a numbered
marker, an encoder watermark or markup with no text is a placeholder incumbent
and fails the candidate quality floor. An audiobook genre list naming only the recording's format is
empty-equivalent: its fill is a fill operation, and a candidate list of that
shape is no candidate for an Automatic destination. A description longer than
512 characters reaches the report as an oversized claim with no canonical value,
which no proposal admits. Ebook descriptions and genres are unchanged.

## Historical ebook synopsis successor (#1076)

[F19a v37](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v37.json),
[F20 v51](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v51.json) and
[F46 v20](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v20.json) bind shared
shadow decision policy v18. **Clean rebuild required**; historical measurements,
evaluators and operation memberships remain frozen, with no new quality score.

Automatic ebook fills preserve exact eligible same-work source text, with no
length floor. Whole normalized `(duplicate)` and ASCII integer `pages ;` ASCII
integer `cm` values are excluded only from automatic authority. Safety, WORK
applicability and conflicting text within one source record are checked first.
Existing authority order then stable provider/record identity chooses between
eligible alternatives. Populated ebook descriptions and audiobook rules remain
unchanged. Ask exposes admitted choices, including placeholders; Ignore exposes
no actions, and an explicit owner choice remains final. Current Library and
Policy explain refusal while History retains its existing generic disposition.

## Prior audiobook synopsis successor (#1070)

[F46 v19](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v19.json),
[F19a v36](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v36.json) and
[F20 v50](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v50.json) retain
all 486 prior source members and 73 prior operations, adding preservation under
both synopsis options and empty fill under replacement. Shadow policy v17 binds
these semantics; clean rebuild required for old derived stores. Historical
measurements are unchanged, and focused synthetic proof is not library scoring.

Fill-only preserves populated audiobook prose. Replacement also fills empty
values, but replaces only whole `chapter`/`track` plus ASCII digits, or
`no description`, `no description available`, `description unavailable`,
`no synopsis`, `no synopsis available`, `synopsis unavailable`, optionally ending
in one full stop. Comparison uses canonical whitespace/case only; retained text
is unchanged. Other safe plain incumbents are preserved. Safety, identity and
same-record conflict refusals precede this rule. Preferred source authority and
stable source-record identity determine the original text; length does not.

## Same-name series successor (#1075)

[F46 v18](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v18.json),
[F19a v35](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v35.json) and
[F20 v49](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v49.json) bind
the series-name-only exception for supported title preservation and empty-series
fill. Proposal policy v31 requires a clean rebuild; report v49 is unchanged.
All 453 prior source members, option registrations and historical measurements
remain retained. Focused synthetic proof does not rescore library quality.

## Genre applicability successor (#1074)

[F46 v17](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v17.json),
[F19a v34](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v34.json) and
[F20 v48](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v48.json) bind
negative-only creator display equivalence and reachable Ask genre choices.
A supported singleton inversion cannot veto an otherwise eligible genre fill.
The classifier returns unavailable identity, never positive WORK admission;
Inventaire retains that distinction through pruning, limits and request accounting.
Report v49 and proposal policy v30 require a clean rebuild. The successors retain
all 435 prior source members, qualified options and historical measurements.
Focused synthetic consumer proof does not rescore historical library outcomes.

## Language evidence successor (#1071)

[F46 v16](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v16.json),
[F19a v33](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v33.json) and
[F20 v47](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v47.json) bind
supported English/code equivalence, unknown exclusion and source-bound observed
forms through persisted decisions, effects and owner views. These successors
preserve all 419 inherited sources and historical qualified options and measurements.

## Author evidence source successor (#1083)

[F46 v13](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v13.json),
[F19a v30](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v30.json) and
[F20 v44](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v44.json) bind
whole-statement author attribution and complete-set proposal-governed automatic
admission. Their explicit synthetic controls exercise isolated extraction,
SQLite publication, owner projections, reopen/replay and consent-guarded fake
effects. All 386 inherited source paths and existing options remain bound;
new current consumers extend that inventory. Report v46 requires a clean rebuild.
Inherited quality measurements are historical and were not rescored.

## Incumbent paragraph source successor (#1080)

[F46 v12](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v12.json),
[F19a v29](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v29.json) and
[F20 v43](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v43.json) bind
exact multiline incumbent description admission and comparison to the current
consumer closure. This is a behavioral successor, not whole-source equivalence or
new quality measurement. Historical manifests, evaluators, measurement artifacts
and qualified option sets remain unchanged. Report v45 and its exact SQL contract
require a clean rebuild under ADR-0013; no migration or adoption is supplied.
Synthetic adapter, persistence, decision and guarded-effect proof checks exact
retention, populated-value protection and source-sensitive currentness for both
media. No production library, provider or model measurement is claimed.

## Preparation recovery source successor (#1085)

[F46 v11](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v11.json),
[F19a v28](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v28.json) and
[F20 v42](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v42.json) bind
committed preparation claim ownership, a truthful runtime recovery stop and
source-preserving Linux crash admission. These are behavioral coordination and
recovery changes with current consumer proof, not binding-only equivalence or
new metadata-quality measurements. All historical manifests, measurements,
evaluators, passing options, paired options and fixed-conservative-v27 policy
remain unchanged. No persisted schema changes in this successor; the inherited
clean-rebuild declaration describes the existing schema lineage.

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

## Historical cover-choice source rotation (#999)

The [v17 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v17.json)
binds the incumbent-artifact and live cover-currentness follow-up. All 35 F20
behavior-source hashes were reviewed: the only changes in that closure are
cover-attention repository wiring and read-only recognition of v16. Proposal,
election, admission, option outcomes and non-cover scoring inputs are unchanged,
so the retained non-cover receipts remain bound without a fresh holdout claim.
V16 remains byte-identical and readable for historical decisions.

The historical [v16 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v16.json)
rotates the exact authority after the proposal contract learned to retain a
usable confirmed cover across an incomplete sibling source. That branch is
cover-only. Both 49-report F20 modes, all 980 non-series previews, all 108
series previews, and the exposed F37 confirmation and spent holdout replayed
with complete non-cover equality and no lost correct or gained incorrect
projection. V15 remains immutable historical evidence whose decisions are
readable but cannot authorize new writes.
The v16 source binding also records the later Activity-only projection
correction that removes a duplicate owner-attributed row for an automatic cover
selection. It changes no F20 scoring input or option outcome, so the retained
receipts remain bound to their original measured behavior head.

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

## Inventaire preparation source closure (#1006)

The [v19 F20](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v19.json)
and [v7 F19a](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v7.json) successors bind
canonical Inventaire cover references through report and proposal admission,
validated missing ISBN responses and bounded WORK identity filtering.
All 980 non-series and 108 series retained previews match the current baseline.
The proposal evaluator changes only its evidence-reference prefix literal;
F19a's historical measurements remain unchanged, with no fresh scoring or
holdout claim. Synthetic proposal roundtrip and persisted case admission cover
the repaired consumer. Prior v6/v18 decisions remain readable but cannot regain
current effect authority. Policy v23, qualified options and schemas are unchanged.

The [v20 F20 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v20.json)
rotates source bindings for the application-owned provider result contract
(#1005). Its explicit closure covers all eight adapters, the SDK and retry
primitives, evidence types, initial normalization, retry consumption and recovery
failure classification. It
retains v19's measurements, evaluators, policy v23 and all 13 passing options;
this is neither rescoring nor a fresh holdout claim. V19 remains byte-frozen and
readable as history, but grants no current effect authority. F19a v7 is unchanged.


The [local-transport v21 F20 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v21-local-transport.json)
rotates the exact source closure for explicit local transport (#844). Shared
validation adds strict HTTP/HTTPS origin admission; existing validation functions,
provider sources, proposal/election behavior, policy v23 and all 13 passing options
are unchanged. The supported Audiobookshelf qualifier now uses current configuration,
a dedicated Activity reader and serialized live repository observations. V20 and
all historical measurements remain byte-frozen; no rescoring or fresh holdout is
claimed. V20 decisions remain readable history but are stale for new effects;
only the current qualification can authorize newly prepared automatic effects.

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

## Provider recovery explanations source closure (#891)

The [v21 F20 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v21.json)
and [v8 F19a manifest](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v8.json) bind the
read-only current review projection and its persisted policy reader. They retain
all earlier measurements, scoring, evaluator identities and qualified options;
this is source closure, not fresh scoring or a fresh holdout. The complete F20
provider and behavior source sets remain bound alongside the six projection
sources. Policy v23, classifications, recovery and persisted schemas are unchanged.

Prior F20 v20 and F19a v7 decisions remain readable as history but grant no current
effect authority. Existing currentness checks reject them before scheduling or
capability access. New decisions use the successor identities; historical
manifests and decisions are never rewritten.

The #891 successor inherits the guarded audiobook source envelope from merged
v20 (`12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5`).
Both that digest and the earlier published v20 digest remain readable history
only. Its existing frontend byte binding includes the bounded diagnostic return
context; no router or view file is added to automatic qualification bindings.

The [v22 F20 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v22.json)
is the retained combined source closure for local transport and provider recovery
explanations (#844, #891). Its primary predecessor is the canonical provider-recovery
v21; the exact local-transport v21 is an explicit additional predecessor. Both
remain byte-frozen and readable history, without current effect authority. V22
retains all 13 options, scoring and measurements and the six projection bindings
shared with retained F19a v8. Its provider closure restates the three bindings
that carry ambiguous provider identity and retains the other twelve, and its
frozen contract restates the provider identity diagnostic stage map; the digest
v22 published before that restatement remains readable history only. It adds no
rescoring or fresh holdout claim.


The [v23 F20 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v23.json)
and [v9 F19a manifest](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v9.json) are the
current source-only successors for authored description integration (#892).
V23 inherits the delivered v39 identity-diagnostic bindings from repinned v22;
the [original v22 artifact](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v22-before-identity-diagnostics.json)
is retained separately with its exact bytes. Both v22 digests remain readable
history for the same 13 options, without current effect authority. All inherited
measurements, options and scoring remain unchanged; no fresh qualification or
model-quality claim is made.

## Audiobook media-health projection source closure (#144)

The [v24 F20 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v24.json)
and [v10 F19a manifest](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v10.json) are the
current qualification. They restate the review-projection binding for the review
web application, which now projects read-time audiobook media-health observations,
and rotate the frontend byte binding for the same reason. V23 and F19a v9 remain
byte-frozen and readable history without current effect authority. All 13 options,
the 15 provider bindings, scoring, measurements, policy v23 and both closure flags
are unchanged; no rescoring or fresh holdout is claimed. Each candidate's source
classification states the delta against its immediate predecessor alone, so a
source restated by an earlier candidate reads `unchanged` in the successor.

The pair is then re-pinned in place for the inert F46 audiobook tranche wiring
(#967), which restates seven frozen-contract bindings and all three bindings of
the shared projection closure: its two persistence adapters and the review web
application. The digests both manifests published before that restatement
remain readable history only, and no option, score, measurement or authority
rule moves.


## Historical expanded audiobook source bindings (#967)

The following limits describe the earlier record; the current successor is above.

The [F46 audiobook record](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v7.json)
names 44 accepted field/operation identities across title, authors, narrators,
language, series and genres. Its source-quality corpus and candidate operations
are frozen separately from subsequent Automatic/effect correspondence. Dates,
publisher, description, ASIN and cover lack the required selected-positive
confirmation floor; subtitle/tags lack the source floor, and ISBN/explicit
semantics remain unresolved. No new authority is inferred from those candidates.
The [F19a v24](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v24.json) and
[F20 v38](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v38.json)
successors bind the current consumers without rewriting historical records or
claiming fresh ebook scoring. Report v43, shadow outcome v5 and policy profile
v3 require a clean rebuild; prior state is never relabelled as current.

Title and author preserve operations remain withheld after retained software
correspondence failures; their source quality was not rejected. Other registered
operations retain their independent qualification.

The current correction retains complete Audiosilo credit sets and ordered series
pairs, including malformed companions that prevent partial Automatic values.
Recording-bound WORK subtitles are available for owner review under proposal
policy v26; subtitle Automatic remains withheld. These consumer corrections
do not claim fresh source scoring or repeat the historical 530-scenario run.

## Whole author evidence successor

The #1083 successors retain every inherited source path and qualification option.
They bind whole-statement EPUB attribution, neutral complete-set corroboration,
proposal-governed Authors admission, the real SQLite/owner/effect consumers and
new generated fixtures. Historical measurements and evaluators remain unchanged;
new synthetic attribution and lifecycle controls are distinct from library quality
measurements. No provider, model or production-library requests were made.
Report v46 requires a clean rebuild; old automatic records cannot mint current
authority.


## Provider title admission successor

The #1084 successors F19 v31, F20 v45 and F46 v14 retain all 397 inherited source paths, add the actual title-admission consumers, and preserve all five binding exclusions and existing qualified options. New synthetic provider, report and persistence checks are separate from unchanged historical quality measurements. Report v47 requires a clean rebuild; proposal policy v28, comparison v5 and shadow policy v15 are unchanged. No provider, model or production-library requests were made.

The #1071 language successors F19 v33, F20 v47 and F46 v16 retain all 419
inherited sources and the five binding exclusions, adding the actual language
producer, persistence and owner-view consumers. Existing qualified options and
historical measurements remain unchanged. Focused synthetic proof binds
English/code grouping, unknown exclusion, source-bound observed-form selection
and persisted effects; it is not new library-wide quality scoring. Report v48,
proposal policy v29, action policy v5, decision policy v16 and outcome v8 require
a clean rebuild. Proposal shape v8, packet v9 and automatic-decision v1 remain.


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

## Shared provider admission preservation (#1082)

Current F19a, F20 and F46 records advance their semantic revision for shared
Google burst admission and remaining-delay recovery. Their exact predecessors
are retained as immutable history and displaced permission sets remain decode-only.
The 12, 13 and 76 automatic options and the one F46 paired operation are unchanged.
Synthetic current-producer, persistence, guarded-effect and predecessor-decoding
proof protects this source change; it is not a new population qualification,
historical rescore or real-provider success measurement. Historical v44 evidence
and the earlier 1,624-report and final 1,916-report observations remain separate,
immutable snapshots, not counts of individually failed HTTP requests.

## Durable refusal source closure (#1081)

F19a, F20 and F46 advance semantic revision 3 to 4 for final refusal preservation
and the snapshot reader. The 12, 13 and 76 permitted operations, F20's 19
dispositions and F46's paired operation are unchanged. Exact revision-3 documents
decode as history and cannot mint current authority. Outcome v10 rejects v9 state
before mutation: **clean rebuild required**, with no adoption. The compact base
fixture binds the actual predecessor table, complete object-inventory digest and
three producer-created decisions. Current reason counts may use the refined codes;
retained #877/v44 measurements remain historical, partial and curtailed.

## Material ISBN/title conflict precedence (#1132)

An explicit ISBN/title material conflict remains manual even when a supported
candidate equals the current title. Automatic records a conflict without
suspending title automation; Ask and Ignore retain their policy behavior.
Ordinary title preservation, independently supported replacements and explicit
owner choices retain their existing authority and effect guards. Shadow policy
v19 and compact successor qualification bindings require a clean rebuild.
