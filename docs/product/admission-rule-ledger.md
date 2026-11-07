# Admission-rule ledger

Every early admission, identity and exclusion gate, with where it came from,
what it blocks on each retained snapshot, and how precise loosening it would
be. The rows are ranked so tuning goes where it pays. The figures are produced
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

- **Gates.** One row per gate. The origin column names the pull request and
  issue that introduced it and any ADR; the corpus column quotes the precision
  or corpus that origin recorded, or says "not recorded".
- **Measurable or not.** A gate whose introducing commit is not an ancestor of
  the snapshot's source revision is "not measurable on that snapshot: gate
  postdates source", and nothing is computed for it even where a similar
  signal exists. A gate the source had, but whose refused input it never
  persisted or cannot separate from another gate's, is "not measurable on that
  snapshot: block not retained". Ancestry is decided by
  `git merge-base --is-ancestor`.
- **Blocked fields and works.** A field is one current review case and
  destination; a work is one current review case. A field is blocked by a gate
  when the gate's persisted signal is on the field, or on the report feeding a
  destination the gate covers, and the field's outcome is not `propose_change`,
  `acceptable_choice` or `keep`. Counts are per gate and not exclusive: one
  field can count under several gates, so every count is a ceiling and the
  ceilings do not add. EPUB narrator and explicit fields are never counted.
- **Precision if loosened.** Each row states its loosened rule. Natural
  controls replace labels. Positives are provider records the report bound to
  the work (identifier relation `accepted`, `associated` or
  `recording_associated`) and records sharing that record's provider work
  identity. Negatives are records the report bound to another work
  (`unrelated`), records carrying an edition-work contradiction, and records
  whose work identity differs from the accepted work at the same provider.
  The loosened rule is applied to the records the gate reaches: the
  first-ranked alternative's evidence for a decision-level gate, or the
  records whose covers a cover candidate shows. TP counts the positives it
  admits and FP the negatives; precision is TP / (TP + FP). Unlabelled records
  are counted, never assigned, and so are blocked fields whose loosened rule
  reaches no provider record. Controls are pooled across the snapshots;
  per-snapshot counts are in each row.
- **The bound.** The precision bound is the lower limit of a two-sided 95%
  Wilson score interval (z = 1.96, computed as 1.959964) on n = TP + FP. It is
  not a one-sided 95% bound. n = 0 is "not estimable".
- **Identity precision only.** The measure says whether the loosened rule
  admits evidence for the right work, not whether a value is well formed. A row
  whose risk is not identity (text bounds, checksums, cover source admission)
  is "not estimable from natural controls"; a row whose refused records were
  never persisted is "not estimable" for that reason.
- **Ranking.** Score = fields unblocked, summed over the snapshots where the
  gate is measurable, × the pooled Wilson lower limit. Rows with no estimable
  precision, or measurable on neither snapshot, score 0 and follow the scored
  rows by ceiling, then ID. A7 (owner-reserved and write-safety holds) is
  listed last and is out of scope.
- **Attention per case.** Attention follows the
  [per-case definition](../operations/cold-start-report.md#attention-per-case).
  It is read under each snapshot's own source: a child process imports only
  that revision's tree and composes the per-case query from that revision's
  own `review_sql` fragments, and the run refuses when the count differs from
  that source's report query by anything other than the EPUB narrator and
  explicit exclusion. Both snapshots reconcile with the report their own
  source produced. The ebook source stored no cover authority, so its "with
  covers" figures equal "without covers" by construction. Each attention field
  counts once under every reason it carries: the automatic decision's
  abstention reason when there is one, otherwise each result reason.

The origin ADRs are
[0018](../architecture/decisions/0018-closed-title-creator-comparison.md),
[0019](../architecture/decisions/0019-publication-date-precision-comparison.md),
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

| Rank | ID | Gate | Fields | Origin | Origin corpus / precision | ebook-744 fields / works | audiobook-1294 fields / works | Loosened rule | Controls TP/FP/unlabelled | Precision (Wilson 95% lower) | Score | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A6 | Automatic-decision abstention | every automatic destination | #442 (593e6198); ADR-0036 | development 159/172 correct at 100% precision; sealed 12/12 and 43/43 | 5,481 / 1,745 | 6 / 6 | admit the first-ranked alternative an automatic decision abstained on | ebook-744 808/43/411 (4,786 fields reach no record); audiobook-1294 1/0/9 (2 fields reach no record); n = 852 | 0.950 (0.933) | 5,117.8 | proposed follow-on |
| 2 | T1 | Closed title and complete-creator agreement | title, subtitle, authors and other compared destinations | #414 (3586e1be), #815 (ce76c7fd); ADR-0018 | not recorded | 1,343 / 977 | 191 / 125 | admit the first-ranked of competing values | ebook-744 802/80/471 (523 fields reach no record); audiobook-1294 47/0/160 (44 fields reach no record); n = 929 | 0.914 (0.894) | 1,371.5 | proposed follow-on |
| 3 | I3 | Local ISBN acceptance classes; anything else stays unresolved | isbn | #333 (f1dd28f5) | accepted-evidence precision 38/40 (95%), recall 41/46 | 524 / 524 | 36 / 36 | admit the first-ranked alternative of an unresolved local ISBN | ebook-744 19/0/0 (511 fields reach no record); audiobook-1294 0/0/0 (36 fields reach no record); n = 19 | 1.000 (0.832) | 465.8 | already filed as #1356 (closed) |
| 4 | A3 | Coherent bibliographic combination | bibliographic destinations | #429 (cef7644a); ADR-0021 | not recorded | 327 / 162 | 6 / 3 | admit the first-ranked alternative without a coherent combination | ebook-744 399/13/236 (32 fields reach no record); audiobook-1294 2/0/14 (0 fields reach no record); n = 414 | 0.969 (0.947) | 315.4 | proposed follow-on |
| 5 | C1 | Classifier outcomes (unknown, conflict block election) | cover | #577 (f7e974d0), #588 (40a97a54); ADR-0046, ADR-0047, ADR-0059 | 96/99 exact agreement; plausible 18/21, none falsely plausible | 385 / 385 | 97 / 97 | admit unknown and conflict cover candidates | ebook-744 42/33/284 (73 fields reach no record); audiobook-1294 0/0/19 (80 fields reach no record); n = 75 | 0.560 (0.448) | 215.7 | already filed as #1355 |
| 6 | A2 | Independent corroboration required | every proposable destination | #351 (d35215e8) | as A1 | 50 / 49 | 85 / 62 | admit the first-ranked single-source alternative | ebook-744 37/0/10 (20 fields reach no record); audiobook-1294 8/0/79 (0 fields reach no record); n = 45 | 1.000 (0.921) | 124.4 | proposed follow-on |
| 7 | C2 | Plausible only for ISBN-search records | cover | #577 (f7e974d0), #588 (40a97a54) | not measured at origin | 121 / 121 | 15 / 15 | admit unknown cover candidates reached only by work search | ebook-744 36/2/83 (0 fields reach no record); audiobook-1294 0/0/15 (0 fields reach no record); n = 38 | 0.947 (0.827) | 112.5 | already filed as #1380 |
| 8 | W9 | Series identity match for series fill | series | #998 (dc8de6f5); ADR-0070 | not recorded | not measurable on that snapshot: gate postdates source | 5 / 5 | admit the first-ranked series alternative | audiobook-1294 2/0/4 (1 fields reach no record); n = 2 | 1.000 (0.342) | 1.7 | proposed follow-on |
| 9 | X1 | Candidate limit (an over-limit lane keeps no candidate) | every destination its provider lanes feed | #297 (f564e598) | not recorded | 5,841 / 677 | 371 / 35 | keep the first five candidates of an over-limit lane | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 10 | A1 | Replace only with positive preservation evidence | every proposable destination | #351 (d35215e8) | 54 exact replays, zero measured systemic false positives | 1,756 / 1,281 | 310 / 142 | admit the first-ranked alternative without preservation evidence | ebook-744 0/0/0 (1,756 fields reach no record); audiobook-1294 0/0/0 (310 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 11 | X5 | Redundant publication context | publication destinations | #818 (e28584e6) | not recorded | 1,761 / 1,761 | 138 / 138 | admit the first-ranked redundant publication value | ebook-744 0/0/0 (1,761 fields reach no record); audiobook-1294 0/0/0 (138 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 12 | A5 | Cover gallery incomplete | cover | #351 (d35215e8) | not recorded | 1,411 / 1,411 | 4 / 4 | admit the first-ranked cover of an incomplete gallery | ebook-744 0/0/0 (1,411 fields reach no record); audiobook-1294 0/0/0 (4 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 13 | B1 | Local text bounds and unsafe-text sentinels | the destinations the bounded local claim feeds | #296 (ef49d6c2), 102548c0 | not recorded | 898 / 889 | 57 / 57 | admit oversized or unsafe local text | not estimable from natural controls | not estimable | 0.0 | kept |
| 14 | W6 | Recording binding: runtime veto, tight band, target-recording representation | every destination its provider lanes feed (audiobook only) | #1045 (0a2a193a), #1059 (ed997a7f); ADR-0064 | 12 books, 4/4/4 development, confirmation, holdout | not measurable on that snapshot: gate postdates source | 846 / 81 | admit the products a recording ladder refused | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 15 | C4 | Cover source admission (media type, byte bound, declaration validity) | cover | #298 (cbd2a835) | not recorded | 670 / 670 | 6 / 6 | admit a refused, truncated, oversized or undeclared cover source | not estimable from natural controls | not estimable | 0.0 | already filed as #1357 (closed) |
| 16 | I4 | Provider ISBN family applicability (print, ambiguous, unknown scope) | isbn | #388 (999edaf0) | not recorded | 499 / 499 | 3 / 3 | admit the first-ranked alternative where only a print, ambiguous or unknown-scope local ISBN exists | ebook-744 0/0/0 (499 fields reach no record); audiobook-1294 0/0/0 (3 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 17 | W1 | Exact work-identity admission (title and creators, transformation rules, decoration fail-closed) | every destination its provider lanes feed | #870/#916 (a0958ac2); diagnostic #1268 (a4315d6a); ADR-0018, ADR-0067, ADR-0068 | 89-case adjudicated corpus, zero false admissions | not measurable on that snapshot: gate postdates source | 354 / 32 | admit the refused work candidates | not estimable: the refused records were not retained | not estimable | 0.0 | already filed as #1355 |
| 18 | I7 | Audiobook ISBN refusal | isbn (audiobook only) | #1059 (ed997a7f) | not recorded | not measurable on that snapshot: gate postdates source | 103 / 103 | admit the first-ranked audiobook ISBN without a recording association | audiobook-1294 0/0/0 (103 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 19 | I1 | ISBN form and checksum | isbn | #291 (b48aae94); identifier_validity_invalid #816 (e3df6c21) | not recorded | 84 / 84 | 13 / 13 | admit an ISBN whose form or checksum fails | not estimable from natural controls | not estimable | 0.0 | kept |
| 20 | B6 | Provider deadline share (20 s) | every destination its provider lanes feed | #1273 (bcdee458) | not recorded | not measurable on that snapshot: gate postdates source | 96 / 12 | let a provider run past its share | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 21 | C3 | Audiobook cross-authority perceptual-hash corroboration | cover (audiobook only) | #948 (6423552f); ADR-0064 | not recorded | not measurable on that snapshot: gate postdates source | 87 / 87 | admit an audiobook cover candidate one authority pictures | audiobook-1294 0/0/7 (80 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 22 | X4 | Generic provider subtitle | subtitle | #817 (660fac2f) | not recorded (corpus private) | 73 / 73 | 0 / 0 | admit the first-ranked generic subtitle | ebook-744 0/0/0 (73 fields reach no record); audiobook-1294 0/0/0 (0 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 23 | I5 | ISBN-first lane eligibility (opening-matter, ebook or unknown, no rear provenance, exactly one family) | every destination its provider lanes feed | #959 (9640228b) | not recorded | not measurable on that snapshot: gate postdates source | 51 / 6 | query by any valid local ISBN family | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 24 | B3 | EPUB rear-resource budget | isbn | #329 (21506f7f) | not recorded | 7 / 7 | 0 / 0 | read every rear resource | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 25 | C5 | Audiobook cover withheld | cover (audiobook only) | #985 (0ce358e9) | not recorded | not measurable on that snapshot: gate postdates source | 6 / 6 | admit the first-ranked withheld audiobook cover | audiobook-1294 0/0/0 (6 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 26 | I6 | Competing ISBNs need a verified manifestation | isbn | #979 (8056f4d3); ADR-0080 | development 42/42, sealed holdout 30/30 | not measurable on that snapshot: gate postdates source | 3 / 3 | admit the first-ranked competing ISBN | audiobook-1294 0/0/0 (3 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 27 | T2 | Evidence-led title identity key and ISBN-title material conflict | title | #1040 (c1963d97); ADR-0075 | constructor controls 50/50 and 75/75 | not measurable on that snapshot: gate postdates source | 1 / 1 | admit the first-ranked title despite a material ISBN-title conflict | audiobook-1294 0/0/2 (0 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 28 | T3 | Decoration-equivalence election and its dissent reasons | title, subtitle | #981 (c843ea7f); ADR-0067 | sealed 89-case admission corpus, 0 false admissions; 1,988-report archived replay | not measurable on that snapshot: gate postdates source | 1 / 1 | admit the first-ranked title despite decoration dissent | audiobook-1294 0/0/1 (0 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 29 | W5 | Target-work representation for work-scope fields | work-scope destinations | #972 (da35e6df) | 7 correct decisions per tag option, 100%, 49 works | not measurable on that snapshot: gate postdates source | 1 / 1 | admit the first-ranked alternative without target-work proof | audiobook-1294 0/0/0 (1 fields reach no record); n = 0 | not estimable | 0.0 | already filed as #1355 |
| 30 | A4 | Date refinement and year conflict need corroboration | publishedDate, publishedYear | #351 (d35215e8), #415 (0d0021d1); ADR-0019 | not recorded | 0 / 0 | 0 / 0 | admit the first-ranked uncorroborated date | ebook-744 0/0/0 (0 fields reach no record); audiobook-1294 0/0/0 (0 fields reach no record); n = 0 | not estimable | 0.0 | kept |
| 31 | B2 | Discovery fan-out caps (8 ISBN lanes, 9 queries, 4 ASIN lanes) | every destination its provider lanes feed | #388 (999edaf0), #916, #948 | not recorded | 0 / 0 | 0 / 0 | query every distinct local ISBN family | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 32 | B4 | Audio chapter cap | every destination its provider lanes feed (audiobook only) | #931/#934 (c3214949) | not recorded | not measurable on that snapshot: gate postdates source | 0 / 0 | keep every chapter row | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 33 | B5 | Cover packet preview cap | cover | #588 (40a97a54) | not recorded | 0 / 0 | 0 / 0 | keep every preview | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 34 | I2 | ISBN occurrence scoping (label-only, owned by another identifier) | isbn | #385 (a70791af) | not recorded | not measurable on that snapshot: block not retained | not measurable on that snapshot: block not retained | admit an ISBN occurrence another identifier owns | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 35 | T4 | Latin combining-mark work identity | every destination its provider lanes feed | #982 (f9be2ca2); ADR-0068 | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | admit a combining-mark variant work | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 36 | W2 | Work-identity contradiction (edition against work, ISBN against target work) | every destination its provider lanes feed | #1014 (caba7b6e) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | admit a contradicted work candidate | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 37 | W3 | Variant veto | every destination its provider lanes feed | #916 (a0958ac2) | as W1 | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | admit a vetoed variant | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 38 | W4 | Adapter work and edition candidate admission | every destination its provider lanes feed | #348 (65a884c9), #570 (e7862167) | not recorded | not measurable on that snapshot: block not retained | not measurable on that snapshot: block not retained | admit the rows an adapter refuses | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 39 | W7 | Corroborated band-agreeing rule and its permission | every destination its provider lanes feed (audiobook only) | #1463 (cca9067c), #1465 (120e60a4) | 14 of 42 probe items rescued; 0 different-work admissions in 57,570 cross-item pairs | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: gate postdates source | admit a band-agreeing product before every exact path refused | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 40 | W8 | Alternative WORK lane permission | every destination its provider lanes feed (audiobook only) | #1269 (906e9a2a) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: block not retained | run the alternative WORK lane after any asserted outcome | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 41 | X2 | Edition-enrichment overflow keeps a work-only witness | every destination its provider lanes feed | #621 (254ed08f); ADR-0056 | not recorded | 0 / 0 | 0 / 0 | keep the overflowing editions | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| 42 | X3 | Wrong-language, control-number and untidy-author value exclusion | language-bearing and creator destinations | #1497/#1560 (ce3a172d) | not recorded | not measurable on that snapshot: gate postdates source | not measurable on that snapshot: gate postdates source | admit the excluded values | not estimable: the refused records were not retained | not estimable | 0.0 | kept |
| out of scope | A7 | Owner-reserved and write-safety holds | subjective and unusable-target destinations | #351 (d35215e8) | not applicable | 372 / 337 | 145 / 100 | none: listed for completeness | ebook-744 178/9/140 (156 fields reach no record); audiobook-1294 76/0/94 (10 fields reach no record); n = 263 | 0.966 (0.936) | 0.0 | kept (out of scope) |

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

- **Dispositions.** `kept` rows stay as they are. Rows already covered by an
  issue link it: W1, W5 and the cover-identity part of C1 (#1355), I3 (#1356,
  closed after measurement), C4's EPUB `invalid_declaration` part (#1357,
  closed) and C2's work-search records (#1380). The five highest-ranked rows
  not already filed are the proposed follow-ons; each gets its issue number by
  re-running the script with `--follow-on ID=ISSUE`.
- **Large unestimable ceilings.** Rows whose refused records were never
  persisted, or whose risk is not identity, score 0 however large their
  ceiling. Their ceilings remain in the table, so a maintainer can promote one
  once a measurement for it exists.
- **Context.** The reviewer-free measurement (#1086) published a bounded
  18-work smoke with 10 works needing attention and 21 attention fields; its
  full-corpus attention denominator stayed unavailable. Those figures are
  context only and enter no row or score. Ranking what still asks after the
  per-case settlements is #1721's work; this ledger measures gates, not
  attention causes. The full-library re-run (#1418) uses the same script.

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
`admission-rule-ledger-v1` JSON document the tables are rendered from. Both
retained snapshots replay in about four seconds together.
