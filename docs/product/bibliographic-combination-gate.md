# Bibliographic combination gate (A3)

The [admission-rule ledger](admission-rule-ledger.md) lists the rules that keep
a metadata value from reaching a book automatically. Its row A3 is the
coherent bibliographic combination: the title, subtitle and series of a book
are decided together, and a combination is dropped when one field's text
already appears in the title. When no combination survives, the three fields
go to manual review with the reason "no safe bibliographic combination"
(`no_safe_bibliographic_combination`). This page measures that row (#1746).
The tables are the unedited Markdown output of
[`scripts/admission_rule_ledger.py`](../../scripts/admission_rule_ledger.py)
with `--a3-combination`, over the ledger's two retained snapshots.
[ADR-0097](../architecture/decisions/0097-keep-the-bibliographic-combination-gate.md)
records the decision they support: keep the gate.

## Method

- The proposal set reads the provider evidence and proposes a value for each
  field, or refuses to. For title, subtitle and series it also forms
  combinations: one per source record, covering all three fields. The
  duplicate check (`_duplicates_bibliographic_component`) drops a
  combination whose subtitle, series name or series number already appears
  in the title. When combinations were tried and none survived, each of the
  three fields that kept an alternative is overwritten. Its outcome becomes
  a choice for the owner, its selected value is cleared, and its earlier
  reasons are replaced by the A3 reason alone.
- The ebook-744 source (proposal policy v12) predates the same-name series
  rule of #1099. That rule lets a series named exactly like a kept title
  stand beside it when the series field is empty. The audiobook-1294 source
  (v35) has it. Main runs v45, which also adds a second way to emit the
  reason, for a change that only a withheld title allowed (#1497). Neither
  source has that second way.
- For every current case, a separate process imports only the snapshot's own
  source tree, rebuilds the report and replays that source's proposal set.
  The replay table counts cases whose replayed results differ from the
  stored ones, and cases that would not rebuild. The process also records
  every combination the source tried, with the facts its duplicate check
  reads and its own verdict.
- On a case with A3 fields the process replays again with the overwrite
  switched off. That gives each field's outcome before the overwrite. Any
  other difference from the stored proposal set refuses the run. Each A3
  field then falls in one of three populations.
  - Held only by this rule, value change: before the overwrite the field
    proposed a change (`propose_change` or `acceptable_choice`). The table
    splits fills of an empty field from replacements.
  - Held only by this rule, kept value: the field kept its current value
    (`keep`), and the overwrite turned it into a choice.
  - Held first by another rule: the field was already refused, and the
    overwrite hid that rule's reason. The second table names the reason.
    Where the reason is competing values on a field a later election decides
    on main, the cell's bracket counts it as stale.
- The script names the first containment clause that vetoes each tried
  combination, in this order: subtitle in title, series name equals title,
  series name in title, series number in title. The series name with its
  number can appear only where the bare name does, so it is counted with
  the name. The clauses are mirrored in the script, and for every tried
  combination the mirror's verdict is compared with the source's own. A work
  counts under each first clause of its combinations. A work with no tried
  combination, because every source record named two values for one field,
  counts as "ambiguous source". More than 256 surviving combinations counts
  as "too many combinations".
- Each work is checked again under main's same-name rule, through the
  mirror. A work where a tried combination would survive on main is "stale:
  later rule (#1099)", and its fields leave the populations. The other rules
  main added after either source (#981 and #1496 title joins, the work route
  of #1642 and the #1497 emitter) cannot be replayed offline and stay
  unmeasured until #1418's run.
- The ledger's "decided together" rule is separate: the decision step marks a
  title as a conflict (`bibliographic_combination_unresolved`) when no single
  action matches a combination. ADR-0092 keeps it. Its fields are counted
  here and not scored.
- A relaxation is a closed loosening over the whole combination, never one
  field, evaluated under main's checks. It names the one clause it lifts. It
  admits only the selections the fields had before the overwrite, never a
  value picked by order.
  - `series_fill_beside_kept_title_containing_name` admits a series fill
    when the title is kept, the series had one alternative that fills an
    empty field, and the only clause that fires is the series name inside
    the title. A fill citing a record of another work is refused.
  - `same_name_series_beside_new_title` admits a new title and a series
    named exactly like it, beside an empty series field: main's same-name
    rule extended to a changed title. No other clause may fire.
  - `kept_companion_stays_kept` admits no value. It applies to a kept field
    whose every alternative already holds the current value, and would end
    only that field's own review.

  Each refuses when the fields do not have its shape, when a field it would
  change was held first by another rule, when the series field is
  populated, when another clause fires, when several combinations qualify,
  and when the title is degraded, such as an announcement in place of a
  title (#1283). A work that main already forms a combination for is not
  relaxed. The first two relaxations count works, the third counts fields.
  "Cases with no held sibling" counts cases where the third relaxation
  would end review on every A3 field.
- Each admitted field is screened like the A6 relaxations. Its cited records
  are counted as right work, wrong work or unlabelled from the snapshot's
  own identifier evidence, with the Wilson 95% lower limit on identity
  precision. The field is classed as tied, decided or with no right-work
  alternative. The table counts admitted values that repeat the current one
  and compares each with an identifier-accepted record the field does not
  cite. The last column says which clause still fires on the admitted
  combination. For the first relaxation the series name stays inside the
  title by definition.

## Tables

### Replay

| Snapshot | Proposal policy | Same-name series rule (#1099) at source | Owner decisions | Cases replayed | Replayed results that differ | Cases not replayed | Combinations tried | Mirror disagreements with the source |
|---|---|---|---|---|---|---|---|---|
| ebook-744 | fixed-conservative-v12 | no | 0 | 1,761 | 0 | 0 | 3,452 | 0 of 3,452 |
| audiobook-1294 | fixed-conservative-v35 | yes | 0 | 175 | 0 | 0 | 290 | 0 of 290 |

### Populations

| Snapshot | Field | Blocked | Stale: later rule (#1099) | Held only by this rule: value change (fill / replacement) | Held only by this rule: kept value | Held first by another rule (stale: later election) | Decided together, left unresolved (not scored) |
|---|---|---|---|---|---|---|---|
| ebook-744 | title | 162 | 88 | 0 (0 / 0) | 29 | 45 (45) | 357 |
| ebook-744 | subtitle | 8 | 0 | 8 (8 / 0) | 0 | 0 (0) | 0 |
| ebook-744 | series | 157 | 88 | 62 (62 / 0) | 0 | 7 (0) | 0 |
| audiobook-1294 | title | 3 | 0 | 3 (0 / 3) | 0 | 0 (0) | 23 |
| audiobook-1294 | subtitle | 0 | 0 | 0 (0 / 0) | 0 | 0 (0) | 0 |
| audiobook-1294 | series | 3 | 0 | 2 (2 / 0) | 1 | 0 (0) | 0 |

| Snapshot | Field | Reason it was held first | Fields |
|---|---|---|---|
| ebook-744 | title | competing_evidence | 45 |
| ebook-744 | series | competing_evidence | 7 |

### Why no combination survived

| Snapshot | Cause | Works | Fields | Combinations |
|---|---|---|---|---|
| ebook-744 | subtitle in title | 8 | 19 | 11 |
| ebook-744 | series name equals title | 104 | 208 | 362 |
| ebook-744 | series name in title | 55 | 113 | 118 |
| ebook-744 | series number in title | 2 | 4 | 2 |
| ebook-744 | ambiguous source | 0 | 0 | 0 |
| ebook-744 | too many combinations | 0 | 0 | 0 |
| audiobook-1294 | subtitle in title | 0 | 0 | 0 |
| audiobook-1294 | series name equals title | 3 | 6 | 12 |
| audiobook-1294 | series name in title | 0 | 0 | 0 |
| audiobook-1294 | series number in title | 0 | 0 | 0 |
| audiobook-1294 | ambiguous source | 0 | 0 | 0 |
| audiobook-1294 | too many combinations | 0 | 0 | 0 |

### Stale for main

| Snapshot | Works | Stale: later rule (#1099) | Works with no combination on main | Withheld-title rule (#1497) |
|---|---|---|---|---|
| ebook-744 | 162 | 88 | 74 | not present at source |
| audiobook-1294 | 3 | 0 | 3 | not present at source |

### Relaxations

| Relaxation | Snapshot | Evaluated | Main already forms one | Refused: shape / held first / series populated / other clause / several / degraded title / wrong-work record | Admitted | Admitted fields | Cases with no held sibling | Cited records: right work / wrong work / unlabelled | Identity precision (Wilson 95% lower limit) | Fields: tied / decided (admitted is the right-work one) / no right-work | Repeats current value | Value agreement with an uncited record | Containment kept after admission |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| series_fill_beside_kept_title_containing_name | ebook-744 | 162 | 88 | 0 / 51 / 0 / 6 / 0 / 0 / 0 | 17 | 17 | not applicable | 16/0/6 | 1.000 (0.806) | 0 / 11 (11 right-work) / 6 | 0 | not estimable (0 comparable) | series name in title 17 |
| same_name_series_beside_new_title | ebook-744 | 162 | 88 | 23 / 51 / 0 / 0 / 0 / 0 / 0 | 0 | 0 | not applicable | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) | none |
| kept_companion_stays_kept | ebook-744 | 327 | 176 | 122 / 0 / 0 / 0 / 0 / 0 / 0 | 29 | none: no value changes | 0 | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) | none |
| series_fill_beside_kept_title_containing_name | audiobook-1294 | 3 | 0 | 3 / 0 / 0 / 0 / 0 / 0 / 0 | 0 | 0 | not applicable | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) | none |
| same_name_series_beside_new_title | audiobook-1294 | 3 | 0 | 1 / 0 / 0 / 0 / 0 / 0 / 0 | 2 | 4 | not applicable | 2/0/9 | 1.000 (0.342) | 0 / 2 (2 right-work) / 2 | 0 | not estimable (0 comparable) | series name equals title 2 |
| kept_companion_stays_kept | audiobook-1294 | 6 | 0 | 5 / 0 / 0 / 0 / 0 / 0 / 0 | 1 | none: no value changes | 0 | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) | none |
| series_fill_beside_kept_title_containing_name | pooled | 165 | 88 | 3 / 51 / 0 / 6 / 0 / 0 / 0 | 17 | 17 | not applicable | 16/0/6 | 1.000 (0.806) | 0 / 11 (11 right-work) / 6 | 0 | not estimable (0 comparable) | series name in title 17 |
| same_name_series_beside_new_title | pooled | 165 | 88 | 24 / 51 / 0 / 0 / 0 / 0 / 0 | 2 | 4 | not applicable | 2/0/9 | 1.000 (0.342) | 0 / 2 (2 right-work) / 2 | 0 | not estimable (0 comparable) | series name equals title 2 |
| kept_companion_stays_kept | pooled | 333 | 176 | 127 / 0 / 0 / 0 / 0 / 0 / 0 | 30 | none: no value changes | 0 | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) | none |

## Reading the analysis

The ledger's 333 blocked fields mix three things. On ebook-744, 88 of the 162
works form a safe combination under main's same-name rule, so their 176
fields are stale for main. Of the 151 fields left there, 70 are value
changes only this rule holds (62 series fills and 8 subtitle fills), 29 are
kept titles the overwrite turned into a choice, and 52 were held first by
competing values (45 titles and 7 series). All 45 of those titles compete on
a field that main's later title elections decide. On audiobook-1294 all 6
fields remain: 3 title replacements, 2 series fills and 1 kept series.
Across both snapshots, 75 fields carry a value change only this rule holds.

The duplicate check explains every A3 work. The replay is exact on both
snapshots, and the mirror reaches the source's own verdict for all 3,742
tried combinations. No work failed because its source records were
ambiguous or because too many combinations survived. On ebook-744 the
series name equals the title in 104 works, sits inside it in 55, the
subtitle repeats in 8 and only the series number repeats in 2. On
audiobook-1294 the series name equals the new title in all 3 works.

The ledger's lowest-identifier pick means little here. The overwrite cleared
each field's selection, so the pick falls back to hash order, and for a kept
title it only repeats the current value. It also picks each field on its
own, which can assemble the combination this rule exists to stop.

No relaxation can be switched on. The series-fill relaxation admits 17
ebook-744 fills, whose cited records are 16 right work, none wrong work and
6 unlabelled. Whether a series should be filled when the title already holds
its name is a question of taste about repeated text, and only owner labels
can answer it. Neither snapshot holds one. The same-name relaxation admits 2
audiobook-1294 works. The kept-companion relaxation would end review on no
case, because a held sibling field stays in review beside every kept field
it applies to. ADR-0097 therefore keeps the gate.

## Reproduce

Run the [ledger's Reproduce command](admission-rule-ledger.md#reproduce)
with `--a3-combination` added. It prints these tables instead of the
ledger's, and the JSON document carries them under `a3_combination`. The
analysis replays every current case's proposal set under its own source,
twice for each A3 case, which takes about thirty seconds for both snapshots.
