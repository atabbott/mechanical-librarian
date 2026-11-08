# Admission-rule ledger

Every early admission, identity and exclusion gate, with where it came from,
what it blocks on each retained snapshot, and what the natural controls can
say about loosening it. The rows are ranked so tuning goes where it pays. The figures are produced
by [`scripts/admission_rule_ledger.py`](../../scripts/admission_rule_ledger.py);
the tables below are its Markdown output, unedited.

## Snapshots

| Label | Medium | Source revision | Current cases |
|---|---|---|---|
| `ebook-744` | ebook library (#744) | `1203cf58ed8954c800e133dbaffdeab7c42193a8` | 1,761 ebook |
| `audiobook-1294` | audiobook run (#1294) | `32360e99c708ea2ac346a7bf4cb157711fd74049` | 162 audiobook, 13 ebook |

Each is the run's final state. Both are private; only the aggregates below
leave them.

## Method

- **Gates.** One row per gate. The origin column names the introducing pull
  request, issue and any ADR; the corpus column quotes the precision it
  recorded, or "not recorded".
- **Stale ceilings.** Five elections on main decide values the bare
  evaluator leaves competing: titles by provider consensus (#974), decoration
  equivalence (#981) and evidence (#1040), contributor display forms (#978)
  and ISBNs by verified manifestation (#979). A blocked field carrying
  `competing_evidence` on one of those destinations, on a snapshot whose
  source predates the election (named below the table), counts as "stale:
  later election". How many the elections settle is not measured here.
- **Gate scope.** A row counts only the destinations its gate governs. T1's
  blocking rule is the evaluator's "more than one value group is an unresolved
  choice" (#351); `competing_evidence` on a cover comes from cover election's
  multi-candidate branch, which C1 governs, so T1 leaves covers out. The other
  rows' destinations match their gates. W9 is the series identity match. The
  series reasons `series_conflicting_membership`, and
  `series_membership_incomplete` when no value was proposed, only repeat that
  the proposal set left the series unresolved. T1, A3 or A2 counts those
  fields, whichever refused them, and no series row counts them again.
- **Measurable or not.** A gate whose introducing commit is not an ancestor of
  the snapshot's source revision is "not measurable on that snapshot: gate
  postdates source", and nothing is computed for it even where a similar
  signal exists. With the same prefix, "block not retained" means the source
  never persisted the refused input, and "cause not separable" that the record
  cannot tell the gate's blocks from other causes: B6's timeouts do not say
  which deadline or failure stopped them, and W6's lane outcomes do not name
  the refusing rule. Ancestry is decided by `git merge-base --is-ancestor`.
- **Blocked fields and works.** A field is one current review case and
  destination; a work is one current review case. A field is blocked by a gate
  when the gate's persisted signal is on the field, or on the report feeding a
  destination the gate covers, and the field's outcome is not `propose_change`,
  `acceptable_choice` or `keep`. One field can count
  under several gates, so every count is a ceiling and ceilings do not add. EPUB narrator and explicit fields are never counted.
- **Replayed rows.** W9, A8 and A9 refuse a value the proposal set already
  proposed, so the outcome rule above cannot see them. Their stored reasons
  exist only for fields the policy sends to Automatic. The script therefore
  replays the product's series path under each snapshot's own source. A child
  process decodes each case with that revision's code and runs it with the
  field's stored setting, as if the field were sent to Automatic. The reason
  it returns names the row. `--breakdown W9` also shows how many fields
  reach the identity match. A refused field is blocked unless its outcome keeps the
  current value. A W9 field is actionable when the same run, with the identity
  match accepting, produces the value.
- **The pick.** The product ranks no alternatives: they are ordered by
  `alternative_id`, a SHA-256 digest, on main and on both snapshot sources. A
  decision-level row's loosened rule admits the selected alternative where the
  result names one, otherwise the lowest-identifier one: an arbitrary choice,
  not a ranking.
- **Identity precision if loosened.** Natural controls replace labels.
  Positives are provider records the report bound to the work (identifier relation `accepted`, `associated` or
  `recording_associated`) and records sharing that record's provider work
  identity. Negatives are records the report bound to another work
  (`unrelated`), records carrying an edition-work contradiction, and records
  whose work identity differs from the accepted work at the same provider.
  The loosened rule reaches the picked alternative's evidence for a
  decision-level gate, or the records whose covers a cover candidate shows,
  bound as the cover classifier binds them: same provider and work identity,
  narrowed to the cover's edition when it names one. TP counts the positives
  it admits and FP the negatives; precision is TP / (TP + FP). n counts admitted
  records, not fields. Unlabelled records are counted, never assigned, and so
  are blocked fields whose loosened rule reaches no record. Controls are
  pooled.
- **The bound.** The precision bound is the lower limit of a two-sided (not
  one-sided) 95% Wilson interval (z = 1.959964) on n; n = 0 is "not estimable".
- **What the bound measures.** How often the records behind the picked value
  belong to this work, not whether the value is correct or well formed. When
  two competing values both come from this work's records, the pick counts as
  positive whichever it is.
- **Pick agreement where controls decide.** For a decision-level row, a field
  with two or more alternatives is decidable when exactly one alternative's
  evidence reaches a positive record and no negative one. The column counts
  decidable fields whose pick is that alternative, with the same Wilson lower
  limit, and tied fields where several qualify. It is the controls' closest
  proxy for value correctness: an alternative with only unlabelled evidence
  may carry the right value. Score does not use it.
- **Owner labels.** Where a snapshot stores owner decisions, the owner's
  latest decision on an item's field names the right alternative, even when
  the item has been rescanned since. A decision from before the case was last
  reopened, or one that was withdrawn, labels nothing. Accepting or entering a
  value names the alternative with that value. Keeping the current value names
  the alternative that keeps it. A rejection, or a value no alternative
  carries, names none. The pick column adds how often the pick is the named
  alternative, with the same Wilson lower limit. This measures value
  correctness, but only on fields the owner chose to decide.
- **Corroborated pick.** `--breakdown ID` also measures one ranking. It picks
  the alternative that more independent sources state than any other, and at
  least two. Each provider authority and the book file count once, as in the
  proposal set. Otherwise it picks nothing. Its agreement with the controls
  runs high: an alternative with more sources has more records that can reach
  a positive one, and providers can copy one catalogue.
- **Breakdown.** `--breakdown ID` splits a decision-level row by destination
  and medium, below the attention tables. Each actionable field is one of decidable, tied, contaminated (no alternative is clean, but one's
  evidence reaches a positive record), no right-work alternative, or single
  (nothing competes). "Pick preserves" counts fields whose pick keeps the
  current value. Automatic pick/keep/other counts blocked fields a stored
  automatic decision already took or kept; each snapshot's row notes the total
  as "decided automatically". It does not change the score.
- **Identity precision only.** A row whose risk is not identity (text bounds,
  checksums, cover source admission) is "not estimable from natural
  controls"; a row whose refused records were never persisted is "not
  estimable" for that reason.
- **Actionable fields.** A blocked field is actionable when the loosened rule
  has something to admit: its result kept an alternative, or, for a cover
  row, a matching cover candidate exists. For T1 every blocked field is
  actionable: a competing result always keeps two or more alternatives.
- **Ranking.** Score = actionable fields, summed over the snapshots where the
  gate is measurable, × the pooled identity-precision Wilson lower limit. Rows with no estimable
  precision, or measurable on neither snapshot, score 0 and follow the scored
  rows by blocked count, then ID. Rows A6a and A6b, where the decision step
  declines to fill a field (#1745), are measured but not ranked.
  [ADR-0092](../architecture/decisions/0092-keep-the-automatic-decision-abstention-gate.md)
  keeps that gate, and the [A6 analysis](automatic-decision-abstention.md)
  measures what declining costs. Row A3 stays ranked;
  [ADR-0097](../architecture/decisions/0097-keep-the-bibliographic-combination-gate.md)
  keeps it, and the [A3 analysis](bibliographic-combination-gate.md)
  measures its own cost.
  A7 is listed last, out of scope.
- **Attention per case.** Attention follows the
  [per-case definition](../operations/cold-start-report.md#attention-per-case).
  A child process composes the per-case query from each snapshot's own
  source's `review_sql` fragments, and the run refuses when the count differs
  from that source's report query by more than the EPUB narrator and explicit
  exclusion; both match their own source's report. The ebook source stored no cover authority, so
  its "with covers" figures equal "without covers". An attention field counts once under each reason it
  carries: the automatic decision's abstention reason if any, otherwise each
  result reason.

The origin ADRs are
[0018](../architecture/decisions/0018-closed-title-creator-comparison.md),
[0019](../architecture/decisions/0019-publication-date-precision-comparison.md),
[0020](../architecture/decisions/0020-field-specific-acceptable-outcomes.md),
[0021](../architecture/decisions/0021-coherent-bibliographic-combinations.md),
[0036](../architecture/decisions/0036-qualified-decision-only-automatic-policy.md),
[0046](../architecture/decisions/0046-observe-report-bound-covers.md),
[0047](../architecture/decisions/0047-adjudicate-closed-cover-policy.md),
[0056](../architecture/decisions/0056-edition-overflow-work-witness.md),
[0059](../architecture/decisions/0059-deterministic-cover-election.md),
[0064](../architecture/decisions/0064-audiobook-provider-evidence-vocabularies.md),
[0067](../architecture/decisions/0067-decoration-equivalence-and-compound-admission.md),
[0068](../architecture/decisions/0068-latin-combining-mark-work-identity.md),
[0070](../architecture/decisions/0070-isbn-backed-series-identity.md),
[0075](../architecture/decisions/0075-independent-evidence-led-titles.md) and
[0080](../architecture/decisions/0080-isbn-evidence-without-image.md).

## Ledger

### Ranked gates

| Rank | ID | Gate | Fields | Origin | Origin corpus / precision | ebook-744 fields / works | audiobook-1294 fields / works | Loosened rule | Controls TP/FP/unlabelled | Identity precision (Wilson 95% lower) | Pick agrees where controls decide (Wilson 95% lower) | Score | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | T1 | More than one value group is an unresolved choice (equality by closed title and complete-creator agreement) | every compared destination except cover | #351 (d35215e8); ADR-0020; equality forms #414 (3586e1be), #815 (ce76c7fd), ADR-0018 | not recorded | 1,141 / 856 (1,141 actionable; 759 stale: later election; 152 decided automatically) | 191 / 125 (191 actionable; 87 decided automatically) | admit the lowest-identifier of the competing values | ebook-744 773/80/500 (321 fields reach no record); audiobook-1294 47/0/160 (44 fields reach no record); n = 900 | 0.911 (0.891) | 247/480 0.515 (0.470); 246 tied; no owner decisions | 1,186.5 | kept (#1744) |
| 2 | A3 | Coherent bibliographic combination | title, subtitle and series | #429 (cef7644a); ADR-0021; same-name series #1099 (79dbbf4c); withheld titles #1497 (ce3a172d) | not recorded | 327 / 162 (327 actionable) | 6 / 3 (6 actionable) | none: the lowest-identifier rule is rejected; closed relaxations in the A3 analysis | ebook-744 392/13/243 (32 fields reach no record); audiobook-1294 2/0/14 (0 fields reach no record); n = 407 | 0.968 (0.946) | 12/20 0.600 (0.387); 7 tied; no owner decisions | 315.1 | kept (ADR-0097) |
| 3 | C1 | Classifier outcomes (unknown, conflict block election) | cover | #577 (f7e974d0), #588 (40a97a54); ADR-0046, ADR-0047, ADR-0059 | 96/99 exact agreement; plausible 18/21, none falsely plausible | 385 / 385 (385 actionable) | 97 / 97 (97 actionable) | admit unknown and conflict cover candidates | ebook-744 42/45/343 (39 fields reach no record); audiobook-1294 0/0/24 (76 fields reach no record); n = 87 | 0.483 (0.381) | not applicable | 183.5 | already filed as #1355 |
| 4 | A2 | Independent corroboration required | a populated field other than genres, tags and description; narrators also when empty | #351 (d35215e8); narrators #966 (0ce358e9), #1642 (5558b7b3) | as A1 | 50 / 49 (50 actionable; 20 decided automatically) | 85 / 62 (85 actionable; 76 decided automatically) | admit the only alternative, which one source states | ebook-744 37/0/10 (20 fields reach no record); audiobook-1294 8/0/79 (0 fields reach no record); n = 45 | 1.000 (0.921) | not estimable (0 decidable; 0 tied); no owner decisions | 124.4 | kept (#1747) |
| 5 | C2 | Plausible only for ISBN-search records | cover | #577 (f7e974d0), #588 (40a97a54) | not measured at origin | 134 / 134 (134 actionable) | 19 / 19 (19 actionable) | admit unknown cover candidates reached only by work search | ebook-744 36/4/94 (0 fields reach no record); audiobook-1294 0/0/20 (0 fields reach no record); n = 40 | 0.900 (0.769) | not applicable | 117.7 | already filed as #1380 |
| 6 | I3 | Local ISBN acceptance classes; anything else stays unresolved | isbn | #333 (f1dd28f5) | accepted-evidence precision 38/40 (95%), recall 41/46 | 524 / 524 (25 actionable; 25 stale: later election; 98 decided automatically) | 36 / 36 (0 actionable; 2 decided automatically) | admit the lowest-identifier alternative of an unresolved local ISBN | ebook-744 19/0/0 (511 fields reach no record); audiobook-1294 0/0/0 (36 fields reach no record); n = 19 | 1.000 (0.832) | 6/6 1.000 (0.610); 7 tied; no owner decisions | 20.8 | already filed as #1356 (closed) |
| 7 | X1 | Candidate limit (an over-limit lane keeps no candidate) | every destination its provider lanes feed | #297 (f564e598) | not recorded | 5,841 / 677 (181 stale: later election) | 371 / 35 | keep the first five candidates of an over-limit lane | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 8 | A1 | Replace only with positive preservation evidence | every proposable destination | #351 (d35215e8) | 54 exact replays, zero measured systemic false positives | 1,756 / 1,281 (0 actionable; 489 decided automatically) | 310 / 142 (0 actionable; 37 decided automatically) | admit the lowest-identifier alternative without preservation evidence | ebook-744 0/0/0 (1,756 fields reach no record); audiobook-1294 0/0/0 (310 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 9 | X5 | Redundant publication context | publication destinations | #818 (e28584e6) | not recorded | 1,761 / 1,761 (0 actionable) | 138 / 138 (0 actionable) | admit the lowest-identifier redundant publication value | ebook-744 0/0/0 (1,761 fields reach no record); audiobook-1294 0/0/0 (138 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 10 | A5 | Cover gallery incomplete | cover | #351 (d35215e8) | not recorded | 1,411 / 1,411 (0 actionable) | 4 / 4 (3 actionable) | admit the lowest-identifier cover of an incomplete gallery | ebook-744 0/0/0 (1,411 fields reach no record); audiobook-1294 0/0/3 (1 field reaches no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 11 | B1 | Local text bounds and unsafe-text sentinels | the destinations the bounded local claim feeds | #296 (ef49d6c2), 102548c0 | not recorded | 898 / 889 | 57 / 57 | admit oversized or unsafe local text | not estimable from natural controls | not estimable | not applicable | 0.0 | kept |
| 12 | C4 | Cover source admission (media type, byte bound, declaration validity) | cover | #298 (cbd2a835) | not recorded | 670 / 670 | 6 / 6 | admit a refused, truncated, oversized or undeclared cover source | not estimable from natural controls | not estimable | not applicable | 0.0 | already filed as #1357 (closed) |
| 13 | I4 | Provider ISBN family applicability (print, ambiguous, unknown scope) | isbn | #388 (999edaf0) | not recorded | 499 / 499 (0 actionable; 93 decided automatically) | 3 / 3 (0 actionable; 2 decided automatically) | admit the lowest-identifier alternative where only a print, ambiguous or unknown-scope local ISBN exists | ebook-744 0/0/0 (499 fields reach no record); audiobook-1294 0/0/0 (3 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 14 | W1 | Exact work-identity admission (title and creators, transformation rules, decoration fail-closed) | every destination its provider lanes feed | #870/#916 (a0958ac2); diagnostic #1268 (a4315d6a); ADR-0018, ADR-0067, ADR-0068 | 89-case adjudicated corpus, zero false admissions | not measurable on that snapshot: gate postdates source | 354 / 32 | admit the refused work candidates | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | already filed as #1355 |
| 15 | I7 | Audiobook ISBN refusal | isbn (audiobook only) | #1059 (ed997a7f) | not recorded | not measurable on that snapshot: gate postdates source | 103 / 103 (0 actionable) | admit the lowest-identifier audiobook ISBN without a recording association | audiobook-1294 0/0/0 (103 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 16 | I1 | ISBN form and checksum | isbn | #291 (b48aae94); identifier_validity_invalid #816 (e3df6c21) | not recorded | 84 / 84 (41 stale: later election) | 13 / 13 | admit an ISBN whose form or checksum fails | not estimable from natural controls | not estimable | not applicable | 0.0 | kept |
| 17 | X4 | Generic provider subtitle | subtitle | #817 (660fac2f) | not recorded (corpus private) | 73 / 73 (0 actionable) | 0 / 0 (0 actionable) | admit the lowest-identifier generic subtitle | ebook-744 0/0/0 (73 fields reach no record); audiobook-1294 0/0/0 (0 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 18 | I5 | ISBN-first lane eligibility (opening-matter, ebook or unknown, no rear provenance, exactly one family) | every destination its provider lanes feed | #959 (9640228b) | not recorded | not measurable on that snapshot: gate postdates source | 51 / 6 | query by any valid local ISBN family | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 19 | B3 | EPUB rear-resource budget | isbn | #329 (21506f7f) | not recorded | 7 / 7 | 0 / 0 | read every rear resource | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 20 | C5 | Audiobook cover withheld | cover (audiobook only) | #985 (0ce358e9) | not recorded | not measurable on that snapshot: gate postdates source | 6 / 6 (0 actionable) | admit the lowest-identifier withheld audiobook cover | audiobook-1294 0/0/0 (6 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 21 | I6 | Competing ISBNs need a verified manifestation | isbn | #979 (8056f4d3); ADR-0080 | development 42/42, sealed holdout 30/30 | not measurable on that snapshot: gate postdates source | 3 / 3 (0 actionable) | admit the lowest-identifier competing ISBN | audiobook-1294 0/0/0 (3 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 22 | T2 | Evidence-led title identity key and ISBN-title material conflict | title | #1040 (c1963d97); ADR-0075 | constructor controls 50/50 and 75/75 | not measurable on that snapshot: gate postdates source | 1 / 1 (1 actionable) | admit the lowest-identifier title despite a material ISBN-title conflict | audiobook-1294 0/0/2 (0 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 23 | T3 | Decoration-equivalence election and its dissent reasons | title, subtitle | #981 (c843ea7f); ADR-0067 | sealed 89-case admission corpus, 0 false admissions; 1,988-report archived replay | not measurable on that snapshot: gate postdates source | 1 / 1 (1 actionable; 1 decided automatically) | admit the lowest-identifier title despite decoration dissent | audiobook-1294 0/0/1 (0 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 24 | W5 | Target-work representation for work-scope fields | work-scope destinations | #972 (da35e6df) | 7 correct decisions per tag option, 100%, 49 works | not measurable on that snapshot: gate postdates source | 1 / 1 (1 actionable) | admit the lowest-identifier alternative without target-work proof | audiobook-1294 0/0/0 (1 field reaches no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | already filed as #1355 |
| 25 | A4 | Date refinement and year conflict need corroboration | publishedDate, publishedYear | #351 (d35215e8), #415 (0d0021d1); ADR-0019 | not recorded | 0 / 0 (0 actionable) | 0 / 0 (0 actionable) | admit the lowest-identifier uncorroborated date | ebook-744 0/0/0 (0 fields reach no record); audiobook-1294 0/0/0 (0 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept |
| 26 | A8 | Series membership completeness (malformed, oversized or unpaired series claims) | series | #817 (660fac2f) | not recorded | not separable on that snapshot: rich-preview refusals collapsed | 0 / 0 | admit a series value with incomplete membership claims | not estimable from natural controls | not estimable | not applicable | 0.0 | kept |
| 27 | A9 | Series provider evidence (a provider states the value, and no lookup behind it failed) | series | #991 (f458a907), which replaced earlier refusals | not recorded | not measurable on that snapshot: gate postdates source | 0 / 0 | admit a series value only the file states, or one backed by a lookup that failed | not estimable from natural controls | not estimable | not applicable | 0.0 | kept |
| 28 | B2 | Discovery fan-out caps (8 ISBN lanes, 9 queries, 4 ASIN lanes) | every destination its provider lanes feed | #388 (999edaf0), #916, #948 | not recorded | 0 / 0 | 0 / 0 | query every distinct local ISBN family | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 29 | B4 | Audio chapter cap | every destination its provider lanes feed (audiobook only) | #931/#934 (c3214949) | not recorded | not measurable on that snapshot: gate postdates source | 0 / 0 | keep every chapter row | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 30 | B5 | Cover packet preview cap | cover | #588 (40a97a54) | not recorded | 0 / 0 | 0 / 0 | keep every preview | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 31 | B6 | Provider deadline share (20 s) | every destination its provider lanes feed | #1273 (bcdee458) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: cause not separable | let a provider run past its share | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 32 | C3 | Audiobook cross-authority perceptual-hash corroboration | cover (audiobook only) | #948 (6423552f); ADR-0064 | not recorded | not measurable on that snapshot: gate postdates source | 0 / 0 | admit an audiobook cover candidate one authority pictures | not estimable: the rule adds reasons and refuses nothing | not estimable | not applicable | 0.0 | kept |
| 33 | I2 | ISBN occurrence scoping (label-only, owned by another identifier) | isbn | #385 (a70791af) | not recorded | not measurable on that snapshot: block not retained | not measurable on that snapshot: block not retained | admit an ISBN occurrence another identifier owns | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 34 | T4 | Latin combining-mark work identity | every destination its provider lanes feed | #982 (f9be2ca2); ADR-0068 | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | admit a combining-mark variant work | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 35 | W2 | Work-identity contradiction (edition against work, ISBN against target work) | every destination its provider lanes feed | #1014 (caba7b6e) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | admit a contradicted work candidate | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 36 | W3 | Variant veto | every destination its provider lanes feed | #916 (a0958ac2) | as W1 | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | admit a vetoed variant | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 37 | W4 | Adapter work and edition candidate admission | every destination its provider lanes feed | #348 (65a884c9), #570 (e7862167) | not recorded | not measurable on that snapshot: block not retained | not measurable on that snapshot: block not retained | admit the rows an adapter refuses | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 38 | W6 | Recording binding: runtime veto, tight band, target-recording representation | every destination its provider lanes feed (audiobook only) | #1045 (0a2a193a), #1059 (ed997a7f); ADR-0064 | 12 books, 4/4/4 development, confirmation, holdout | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: cause not separable | admit the products a recording ladder refused | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 39 | W7 | Corroborated band-agreeing rule and its permission | every destination its provider lanes feed (audiobook only) | #1463 (cca9067c), #1465 (120e60a4) | 14 of 42 probe items rescued; 0 different-work admissions in 57,570 cross-item pairs | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: gate postdates source | admit a band-agreeing product before every exact path refused | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 40 | W8 | Alternative WORK lane permission | every destination its provider lanes feed (audiobook only) | #1269 (906e9a2a) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | run the alternative WORK lane after any asserted outcome | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 41 | W9 | Series identity match for automatic series values (by work, or by ISBN under ADR-0070) | series | #998 (dc8de6f5); ADR-0070 | not recorded | not measurable on that snapshot: gate postdates source | 0 / 0 (0 actionable) | admit the series value whose sources are not shown to be this work | audiobook-1294 0/0/0 (0 fields reach no record); n = 0 | not estimable | not estimable (0 decidable; 0 tied); no owner decisions | 0.0 | kept (#1748) |
| 42 | X2 | Edition-enrichment overflow keeps a work-only witness | every destination its provider lanes feed | #621 (254ed08f); ADR-0056 | not recorded | 0 / 0 | 0 / 0 | keep the overflowing editions | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| 43 | X3 | Wrong-language, control-number and untidy-author value exclusion | language-bearing and creator destinations | #1497/#1560 (ce3a172d) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: gate postdates source | admit the excluded values | not estimable: the refused records were not retained | not estimable | not applicable | 0.0 | kept |
| unranked | A6a | Automatic-decision abstention, scalar engine (insufficient support, no eligible action) (kept by ADR-0092; its own cost is the proposable population in the A6 analysis) | the source's scalar decision destinations | #442 (593e6198); ADR-0036; re-scoped #1745, ADR-0092 | development 159/172 correct at 100% precision; sealed 12/12 and 43/43 | 80 / 80 (80 actionable; 80 stale: later election) | 5 / 5 (5 actionable) | none: the lowest-identifier rule is rejected; closed relaxations in the A6 analysis | ebook-744 41/0/58 (35 fields reach no record); audiobook-1294 1/0/9 (1 field reaches no record); n = 42 | 1.000 (0.916) | 17/23 0.739 (0.535); 1 tied; no owner decisions | 0.0 | kept (ADR-0092) |
| unranked | A6b | Rich-preview no-eligible-action leftover (leftover of rich-preview refusals outside their own reasons) | rich-preview destinations | #442 (593e6198); #1745, ADR-0092 | not recorded | not separable on that snapshot: rich-preview refusals collapsed | 0 / 0 | none: refile under the rich-preview reason's own row | not estimable from natural controls | not estimable | not applicable | 0.0 | kept (ADR-0092) |
| out of scope | A7 | Owner-reserved and write-safety holds | subjective and unusable-target destinations | #351 (d35215e8) | not applicable | 372 / 337 (220 actionable; 220 decided automatically) | 145 / 100 (142 actionable; 80 decided automatically) | none: listed for completeness | ebook-744 178/9/140 (156 fields reach no record); audiobook-1294 76/0/94 (10 fields reach no record); n = 263 | 0.966 (0.936) | 6/8 0.750 (0.409); 0 tied; no owner decisions | 0.0 | kept (out of scope) |

- ebook-744: source predates #974, #978, #979, #981, #1040.
- audiobook-1294: source predates no later election.

### Attention per case

| Snapshot | Medium | Fields with covers | Cases with covers | Fields without covers | Cases without covers |
|---|---|---|---|---|---|
| ebook-744 | audiobook | 0 | 0 | 0 | 0 |
| ebook-744 | ebook | 2,012 | 1,261 | 2,012 | 1,261 |
| audiobook-1294 | audiobook | 261 | 143 | 170 | 95 |
| audiobook-1294 | ebook | 16 | 11 | 6 | 5 |

| Snapshot | Destination | Attention fields |
|---|---|---|
| ebook-744 | authors | 104 |
| ebook-744 | description | 176 |
| ebook-744 | genres | 11 |
| ebook-744 | isbn | 107 |
| ebook-744 | language | 8 |
| ebook-744 | publisher | 14 |
| ebook-744 | series | 869 |
| ebook-744 | subtitle | 177 |
| ebook-744 | tags | 2 |
| ebook-744 | title | 544 |
| audiobook-1294 | asin | 31 |
| audiobook-1294 | authors | 2 |
| audiobook-1294 | cover | 101 |
| audiobook-1294 | description | 68 |
| audiobook-1294 | genres | 33 |
| audiobook-1294 | language | 9 |
| audiobook-1294 | narrators | 6 |
| audiobook-1294 | publishedDate | 1 |
| audiobook-1294 | publisher | 3 |
| audiobook-1294 | series | 9 |
| audiobook-1294 | tags | 9 |
| audiobook-1294 | title | 5 |

| Snapshot | Reason | Attention fields |
|---|---|---|
| ebook-744 | competing_evidence | 516 |
| ebook-744 | evidence_sufficiency_manual | 45 |
| ebook-744 | field_policy_conflict | 8 |
| ebook-744 | insufficient_support | 103 |
| ebook-744 | no_eligible_action | 1,340 |
| audiobook-1294 | ambiguous_evidence | 6 |
| audiobook-1294 | competing_evidence | 90 |
| audiobook-1294 | cover_gallery_incomplete | 3 |
| audiobook-1294 | current_cover_verified | 95 |
| audiobook-1294 | description_quality_floor_not_met | 6 |
| audiobook-1294 | evidence_sufficiency_manual | 1 |
| audiobook-1294 | insufficient_independent_corroboration | 9 |
| audiobook-1294 | insufficient_support | 11 |
| audiobook-1294 | no_candidate | 3 |
| audiobook-1294 | no_safe_bibliographic_combination | 1 |
| audiobook-1294 | owner_cover_choice_required | 98 |
| audiobook-1294 | populated_subjective_field | 43 |
| audiobook-1294 | series_conflicting_membership | 4 |
| audiobook-1294 | target_work_applicability_unproved | 1 |
| audiobook-1294 | unique_safe_cover | 3 |
| audiobook-1294 | work_publication_cannot_replace | 1 |

## Reading the ledger

- **Follow-ons.** No row is filed. T1
  ([#1744](https://github.com/atabbott/mechanical-librarian/issues/1744),
  1,332 actionable fields), A6
  ([#1745](https://github.com/atabbott/mechanical-librarian/issues/1745),
  ADR-0092), A2
  ([#1747](https://github.com/atabbott/mechanical-librarian/issues/1747),
  135) and A3
  ([#1746](https://github.com/atabbott/mechanical-librarian/issues/1746),
  333, ADR-0097) were measured and kept.
- **W9** was re-scoped under
  [#1748](https://github.com/atabbott/mechanical-librarian/issues/1748) to the
  series identity match, and kept. Its earlier count was series fields the
  proposal set had left unresolved, which T1, A3 and A2 already count.
  Replayed on audiobook-1294, the match refuses 0 of the 37 series fields that
  reach it (175 replayed). Which competing series value is right is a question
  of sources. The lowest-identifier pick agrees with the controls in 36 of 62
  decidable series fields (lower limit 0.457). The corroborated pick chooses
  in 8 of 13 competing series fields, none on ebook-744, and agrees in 4 of 4
  where the controls decide.
- **Identity precision is not a case for loosening.** Where the controls can
  decide, the lowest-identifier pick agrees with the one right-work
  alternative in about half the fields: T1 247 of 480 (0.515, lower limit
  0.470), A6a 17 of 23 (0.739, 0.535; unranked), A3 12 of 20 (0.600, 0.387). Each
  A2 field has one alternative, and 96 of its 135 were already decided
  automatically. By destination (`--breakdown T1`), no T1 lower limit
  reaches 0.46. Most of T1's ebook fields (759 of
  1,141) and all of A6a's (80) are stale: a later election decides them.
- **Dispositions.** `kept` rows stay as they are. `kept (#1744)`,
  `kept (ADR-0092)`, `kept (#1747)`, `kept (ADR-0097)` and `kept (#1748)`
  were decided after measurement (#1744 to #1748). Rows an issue already
  covers link it: W1, W5 and the cover-identity part of C1 (#1355), I3 (#1356,
  closed after measurement), C4's EPUB `invalid_declaration` part (#1357,
  closed) and C2's work-search records (#1380). A filed follow-on's row gets
  its issue number by re-running the script with `--follow-on ID=ISSUE`.
- **Large ceilings that score 0.** Some rows' fields kept almost no
  alternative, so loosening them as stated unblocks almost nothing:
  A1 (2,066 blocked, none actionable), X5 (1,899, none), A5 (1,415, three
  actionable, whose cover sources reach only unlabelled records), I4 (502,
  none), I7 (103, none) and X4 (73, none). Others never persisted their
  refused records (X1 with 6,212 blocked, W1, I5, B2, B3, B4, B5, X2), or carry
  a non-identity risk (B1, C4, I1). Their ceilings stay until measured or
  given a rule that first produces a candidate.
- **Rows with no count.** Measuring B6 needs a persisted record of which deadline stopped a provider,
  and W6 a per-lane record of the recording rule that refused a product. C3's
  cover-hash rule only adds reasons and blocks no field: an audiobook cover
  classified `unknown` is C1's cost.
- **Context.** #1086's 18-work smoke (10 works needing attention, 21 fields)
  is context only. The full-library re-run (#1418) uses the same script.

## Reproduce

Export each source revision, then run the script against copies of the final
`application.sqlite3` of each snapshot and the cold-start report its source
produced:

```sh
git archive --format=tar --prefix=src-1203cf58/ \
    1203cf58ed8954c800e133dbaffdeab7c42193a8 src scripts | tar -x -C "$SCRATCH"
git archive --format=tar --prefix=src-32360e99/ \
    32360e99c708ea2ac346a7bf4cb157711fd74049 src scripts | tar -x -C "$SCRATCH"
uv run python scripts/admission_rule_ledger.py \
    --snapshot ebook-744 "$EBOOK/application.sqlite3" \
        1203cf58ed8954c800e133dbaffdeab7c42193a8 \
    --source-root ebook-744 "$SCRATCH/src-1203cf58" \
    --source-report ebook-744 "$EBOOK/cold-start-report-final.json" \
    --snapshot audiobook-1294 "$AUDIO/application.sqlite3" \
        32360e99c708ea2ac346a7bf4cb157711fd74049 \
    --source-root audiobook-1294 "$SCRATCH/src-32360e99" \
    --source-report audiobook-1294 "$AUDIO/final-report.json" \
    --format markdown
```

The checkout must hold full history: a shallow clone or an unknown revision
refuses the run. A database with a non-empty write-ahead log is refused;
checkpoint a copy first. Without `--format markdown` the script prints the
`admission-rule-ledger-v2` JSON document the tables are rendered from. Both
retained snapshots replay in about four seconds together.
