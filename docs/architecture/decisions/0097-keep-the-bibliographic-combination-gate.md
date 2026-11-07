# ADR-0097: Keep the bibliographic combination gate

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1746 (row A3 of the admission-rule ledger, #1350)
- Amends: ADR-0021. This ADR records the measurement of the combination
  gate and the relaxations a later change may try. It extends ADR-0092's
  would-admit note to rich-preview outcomes, as a definition only. The gate,
  the same-name series rule and every veto are unchanged.
- Supersedes: none
- Superseded by: none

## Context

The admission-rule ledger (#1350) lists every rule that keeps a metadata
value from reaching a book automatically. It ranks them by the held-back
fields a loosened rule could reach, times a lower limit on how often the
admitted records belong to the right work. Its row A3 is the coherent
bibliographic combination of ADR-0021. Title, subtitle and series are
decided together, one combination per source record, and a combination is
dropped when its subtitle, series name or series number already appears in
the title. When combinations were tried and none survived, the three fields
go to manual review with the reason "no safe bibliographic combination"
(`no_safe_bibliographic_combination`). The ledger ranked A3 second, with
327 fields on 162 works of the ebook-744 snapshot (source revision
1203cf58) and 6 on 3 works of audiobook-1294 (source revision 32360e99). Its
proposed loosening was to admit the alternative with the lowest identifier
without a coherent combination. #1746 asked whether that, or a narrower
loosening, is safe.

The figures re-derive exactly on main:

- 327 and 6 blocked fields;
- natural controls of 392 right work, 13 wrong work and 243 unlabelled on
  ebook-744, and 2, 0 and 14 on audiobook-1294 (n = 407, which counts cited
  records, not fields);
- identity precision 0.968, with a Wilson 95% lower limit of 0.946;
- the lowest-identifier pick agreeing with the right-work alternative in 12
  of 20 decided fields (0.600, lower limit 0.387).

Their meaning is different from what the row implied.

- **The row is right about where the gate acts.** Only title, subtitle and
  series carry the reason, and every blocked field kept an alternative.
- **It is wrong about what the gate costs.** The proposal set overwrites
  each field: its earlier outcome becomes a choice for the owner, its
  selected value is cleared and its earlier reasons are replaced by the A3
  reason alone. So the count mixes fields only this gate holds with fields
  another rule had already refused, and with kept values the overwrite
  turned into a choice. It also hides the earlier reason from the ledger's
  stale-election note, which can never fire on A3.
- **The lowest identifier is not a pick.** Alternatives are sorted by a
  SHA-256 digest. The overwrite cleared the field's own selection, so the
  pick falls back to that order, and for a kept title it only repeats the
  current value. It picks each field on its own, so it can assemble the
  combination the gate exists to stop.
- **Identity is not the main risk.** The gate guards against the same text
  appearing twice across title, subtitle and series. Whether a cited record
  belongs to this book says nothing about that.

The ledger script's `--a3-combination` option measures the cost. For every
current case it replays the snapshot's own proposal set under its own source
revision, records every combination that source tried with its own duplicate
verdict, and on each A3 case replays again with the overwrite switched off.
That gives each field's outcome before the overwrite.

- **The replay is exact.** All 1,761 ebook-744 and 175 audiobook-1294
  cases replay to the stored results, and the second replay changes nothing
  outside the A3 fields. The script's mirror of the duplicate check reaches
  the source's own verdict for all 3,742 tried combinations.
- **The duplicate check explains every A3 work.** No work failed because
  every source record named two values for one field, or because more than
  256 combinations survived. On ebook-744 the series name equals the title
  in 104 works, sits inside it in 55, the subtitle repeats in 8 and only the
  series number repeats in 2 (a work counts under each clause that first
  vetoes one of its combinations). On audiobook-1294 the series name equals
  the new title in all 3 works.
- **Half of the ebook figure is stale.** The ebook-744 source predates the
  same-name series rule of #1099, which lets a series named exactly like a
  kept title stand beside it when the series field is empty. Under that
  rule 88 of the 162 works form a combination, so their 176 fields are stale
  for main. The audiobook-1294 source already has the rule, and none of its
  works change.
- **What the gate alone holds is much smaller.** Of the 157 fields that
  remain on main (151 ebook-744, 6 audiobook-1294):

  | Population | ebook-744 | audiobook-1294 | Total |
  |---|---|---|---|
  | Held only by this rule: value change | 70 (62 series fills, 8 subtitle fills) | 5 (3 title replacements, 2 series fills) | 75 |
  | Held only by this rule: kept value | 29 titles | 1 series | 30 |
  | Held first by another rule | 52 (45 titles, 7 series) | 0 | 52 |

  Every field held first was held by competing values (`competing_evidence`,
  ledger row T1). The 45 titles compete on a field that main's later title
  elections decide. On ebook-744 the 162 A3 titles are also exactly the 162
  fields the A6 analysis counts as an upstream echo.
- **Several main rules cannot be measured.** Neither source has three of
  them. Main joins article-less titles to a source's combination (#1496)
  and follows the work route of #1642. It also gives the reason to a
  subtitle or series change that was possible only beside a withheld title
  (the withheld-title rule, #1497). The ebook-744 source also lacks the
  joining of decorated titles (#981), the exemption of a title the evidence
  leads (#1040, ADR-0075) and the earlier work route (#1292). The
  audiobook-1294 source has those. None of these can be replayed offline
  (ADR-0013), so they stay unmeasured until #1418's full-library run.

The script then tries three relaxations, each a predicate over the whole
combination under main's checks. Decision 4 lists them.

| Relaxation | Works or fields evaluated | Main already forms one | Admitted | Cited records: right work / wrong work / unlabelled | Identity precision (Wilson 95% lower limit) |
|---|---|---|---|---|---|
| series fill beside a kept title holding the name | 165 works | 88 | 17 works, 17 fields | 16 / 0 / 6 | 1.000 (0.806) |
| same-name series beside a new title | 165 works | 88 | 2 works, 4 fields | 2 / 0 / 9 | 1.000 (0.342) |
| kept companion stays kept | 333 fields | 176 | 30 fields, no value | none | not estimable |

The series-fill relaxation admits only ebook-744 fills. In 11 of the 17 the
labels decide the right-work alternative, and in each it is the admitted
one. The other 6 have no right-work alternative. The kept-companion
relaxation would end review on no case, because each field it applies to
sits beside a sibling that stays in review. Both snapshots hold 0 owner
decisions, and no admitted value could be compared with an uncited
identifier-accepted record.

## Decision

1. **Keep the gate.** The combination rule, the duplicate check, the
   same-name series rule and the overwrite stay as they are. No reason
   code, policy version or qualified option changes, and nothing under
   `src/` changes.
2. **Reject the lowest-identifier rule** for every field. The order is a
   hash, it ignores the selection the overwrite cleared, and picking each
   field on its own can build an incoherent combination.
3. **The ledger row stays ranked with corrected text.** Its fields read
   "title, subtitle and series", its origin names #1099 and #1497, and its
   disposition is "kept (ADR-0097)". The score keeps the ledger's uniform
   method. The A3 analysis
   ([`docs/product/bibliographic-combination-gate.md`](../../product/bibliographic-combination-gate.md))
   says what the gate alone costs: value changes only it holds, kept values
   only it holds, and fields another rule held first. Fields held first are
   reported there only, and no other row's count changes.
4. **Any future loosening is a closed relaxation.** It admits only the
   selections the fields had before the overwrite, and never picks by order.
   A relaxation that changes a value reads the whole combination and names
   the one clause it lifts. It refuses when several combinations qualify,
   when a field it would change was held first by another rule, when
   another clause fires, and when the title is degraded, such as an
   announcement in place of a title (#1283). A field the combination keeps
   unchanged is not checked for another rule. These are the three measured
   here:
   - `series_fill_beside_kept_title_containing_name`: fill an empty series
     beside a kept title that already contains the series name, when the
     series had one alternative, the name inside the title is the only
     clause that fires and no cited record belongs to another work;
   - `same_name_series_beside_new_title`: admit a new title and a series
     named exactly like it beside an empty series field, which extends the
     same-name rule of #1099 from a kept title to a changed one;
   - `kept_companion_stays_kept`: leave a kept field whose every alternative
     already holds the current value out of review. It changes no value. It
     is a predicate over that one field and refuses only by shape. It does
     not check whether a sibling field was held first. Under that check 3 of
     its 29 ebook-744 titles would be refused. Its case count stays 0
     either way.

   These stay whatever a later measurement shows:
   - a subtitle or series number repeated in the title;
   - a new title that contains a component, other than the exact equality
     of the second relaxation;
   - a source record that names two values for one field;
   - the 256-combination limit;
   - the rule that a cross-source title needs two correlation groups;
   - the withheld-title rule of #1497;
   - the decision-level rule that combined values are decided together
     (ADR-0092);
   - any field another rule held first.

   No write-safety rule changes.
5. **The would-admit note extends to rich-preview outcomes, as a definition.**
   ADR-0092 defines the note on a scalar shadow decision. Series and
   subtitle are decided by the rich-preview engine, so the same note, with
   the same rules, applies to a rich-preview outcome. It names the
   relaxation and what it would admit, carries no authority, and leaves the
   outcome and the field's manual review unchanged. A relaxation is never
   built as a formed combination. A formed combination would reach two
   rich-preview options at once: the primary series and the fill of an empty
   subtitle. Both are already qualified as automatic. It would also reach
   the title's combination action. Nothing is built until
   the owner approves a relaxation, and building it needs the policy and
   decision version moves and the clean rebuild of ADR-0092's Decision 4.
6. **The bar for activating a relaxation** is owner-labelled value precision
   for that relaxation, measured through #1418 under a new qualification
   identity. The pass mark is the owner's to set. Identity precision, the
   field classes and the containment that remains after admission are
   screens a relaxation must pass first. One wrong-work record disqualifies
   it. Until #1418 collects labels, value precision is not estimable: both
   snapshots hold 0 owner decisions.

## Consequences

### Positive

- The ledger no longer reads A3's 333 fields as its own cost. 176 are stale
  for main, 52 were held first by competing values, and 30 are kept values.
  75 fields carry a value change only this rule holds.
- The measurement replays each source's own proposal set, checks it case by
  case, and checks the mirrored clauses against the source's own verdict for
  every tried combination.
- A later loosening has a closed definition, a would-admit note that covers
  series and subtitle, and a stated bar.

### Negative

- The 75 value changes stay in manual review. For 17 of them, series fills
  beside a kept title that already holds the series name, a relaxation
  would admit exactly one combination whose records are right work or
  unlabelled. Another 15 ebook-744 works have the same series shape beside
  a title that competing values held first, which main's later elections
  decide. On main the pool is therefore between 17 and 32 works.
- The ebook figures come from a v12 proposal policy that predates several
  of main's rules, and only the same-name rule could be re-checked.

### Neutral or deferred

- Whether a series should be filled when the title already contains its
  name is a question about repeated text that only the owner can answer.
  It goes to the owner after this change, as a possible sample for #1418.
- #1418's run measures A3 again with the same script on a current source,
  including main's withheld-title rule and the rules this analysis could
  not replay.

## Alternatives considered

### Admit the lowest-identifier alternative

Rejected by Decision 2.

### Loosen a relaxation now on identity precision

Rejected by Decision 6. Identity precision measures whether a cited record
belongs to the right work. The gate's risk is repeated text, which identity
cannot see, and no owner label exists to measure it.

### Form the combination a relaxation would admit

Rejected by Decision 5. The rich-preview options that read a combination are
already qualified as automatic, so a formed combination would act at once.

### Unrank the row, as for A6

Not taken. A3 is a proposal-set rule like T1, which stayed ranked after
#1744 kept it. The analysis page carries the corrected cost.

### Amend ADR-0092 for rich-preview outcomes

Not taken. The extension is a definition this gate needs, so it is recorded
here, and ADR-0092's body is unchanged.

## Validation

- `tests/unit/test_admission_rule_a3.py` covers:
  - classing each field by its outcome before the overwrite, a competing
    title counted stale on a source that predates the elections, and a
    refusal on any difference outside the A3 fields;
  - the source process itself: a stored proposal set altered outside the
    A3 fields, which it reports as differing, and a source tree whose
    duplicate check predates the same-name rule;
  - every containment clause in order, the work-level causes, and the
    count of mirror disagreements with the source;
  - main's same-name rule, which allows only a kept title beside an empty
    series and still vetoes a populated series, a new title, a name inside
    the title and a repeated series number;
  - each relaxation's admission and each listed refusal, and the
    kept-companion relaxation's case count, which excludes a case with a
    held sibling;
  - the screens, n = 0 reported as not estimable, and output that holds
    aggregates only;
  - a product-built case replayed through the real source process.
- `tests/unit/test_admission_rule_ledger.py` pins row A3's text,
  disposition and rank.
- Re-running the documented Reproduce commands on both retained snapshots
  regenerates the tables of [the ledger](../../product/admission-rule-ledger.md)
  and of [the A3 analysis](../../product/bibliographic-combination-gate.md)
  byte for byte.
