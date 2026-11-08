# Automatic-decision abstention (A6)

The [admission-rule ledger](admission-rule-ledger.md) lists the rules that keep
a metadata value from reaching a book automatically. Its row A6 is
automatic-decision abstention: the decision step is allowed to fill a field
automatically, but declines to pick a value, so the field falls back to manual
review. This page measures that row (#1745). The tables are the unedited
Markdown output of
[`scripts/admission_rule_ledger.py`](../../scripts/admission_rule_ledger.py)
with `--a6-abstention`, over the ledger's two snapshots.
[ADR-0092](../architecture/decisions/0092-keep-the-automatic-decision-abstention-gate.md)
records the decision they support: keep the gate.

## Method

- The proposal set reads the provider evidence and proposes a value for each
  field, or refuses to. The decision step then applies a proposed value
  automatically or abstains. Its scalar engine decides single-value fields:
  title, authors, narrators, publisher, language and published date. The source
  revision lists them itself in `shadow_decision._SUPPORTED_DESTINATIONS`. Its
  rich-preview engine handles every other field, such as series. The ebook-744
  source decides with decision policy v9 and the audiobook-1294 source with
  v22. Main runs v27. The ledger's row A6a is the scalar engine's abstentions,
  and row A6b is the rich-preview engine's leftover.
- A field is one destination of one current review case. Both populations count
  fields where the decision abstained for insufficient support
  (`insufficient_support`) or because no action was eligible
  (`no_eligible_action`).
  - A blocked field is one the proposal set had already refused. These are row
    A6a's fields.
  - A proposable field is one where the proposal set proposed a change or an
    acceptable choice, policy allows the field to be filled automatically, and
    the decision still abstained. These are the abstention's own cost, and the
    owner sees each as a field left for manual review.

  Both are counted by field, reason, medium, and whether the field kept no, one
  or more alternatives.
- An abstention for insufficient evidence (`evidence_sufficiency_manual`) only
  repeats the proposal set's own refusal. It is counted on this page only. The
  ledger neither counts nor scores it. The rich-preview leftover is "no
  eligible action" on a field outside the scalar engine, over all fields. It
  can be separated only where the source records each rich-preview refusal's
  own reason.
- For every current case, a separate process imports only the snapshot's own
  source tree. It rebuilds the decision's input from the stored review case,
  proposal set and report, using that source's `ShadowReviewPacket.create`, and
  replays that source's decision. The replay table counts cases that would not
  rebuild, and replayed reasons that differ from the stored ones.
- For each candidate value of an abstained field, the script names the first
  eligibility check it fails. The checks are those of the source's
  `_candidate_is_eligible`, mirrored clause by clause, in this order: degraded
  value, scope, binding, citations, publication, preservation, local support,
  correlation support, narrator, evidence-led title. From policy v23 the
  source refuses a degraded value (`degraded`), such as a narrator credit in
  the authors field, before any other check. Local support (`support_local`) asks
  whether the value matches the book file's own metadata. Correlation support
  (`support_correlation`) counts the independent providers that agree on it.
  Where no check applies, the table names a state instead. A candidate can pass
  (`passes`), have no action the decision could take (`no_action`), or sit on a
  branch that never evaluates it (`not_evaluated`). A field can have no
  candidate (`no_candidate`), or a proposal that conflicts with the language
  policy (`language_conflict`). For every candidate with an action, the
  mirror's verdict is compared with the source's own, and the replay table
  counts disagreements.
- A relaxation is a narrow loosening of one support threshold by one step, one
  per scalar field except narrators. Each is tried only where the source's
  decision abstained for insufficient support, and under main's checks (v27).
  - It admits a value only when exactly one candidate fails a single named
    support threshold, passes it once the threshold is lowered, and is not a
    degraded value such as a narrator credit in an author field.
  - It refuses when no candidate or several candidates qualify, when the
    proposal conflicts with the language policy, and when main's checks already
    pass a candidate ("main already passes"). It never picks by list order.
  - The authors relaxation as measured (`authors_one_correlation`) counts one
    agreeing provider only. It does not check that a record is bound to this
    book's identity, as the version proposed in #1745 does. A later change that
    activates it would add that check, using the identity rule #1355 is
    defining. ADR-0092's Decision 3 says what each relaxation in the table
    admits.
- Each admitted value is screened in four ways.
  - Identity precision: each cited record counts as right work or wrong work
    when the snapshot's identifier evidence already ties it to this book or to
    another one, and as unlabelled otherwise. Precision is the share of
    labelled records that are right work, with its Wilson 95% lower limit, the
    smallest share the counts support. With no labelled record it reads "not
    estimable".
  - The field's class, as #1744 defines it: tied (two or more right-work
    alternatives), decided (exactly one, and whether the admitted value is it),
    or no right-work alternative.
  - Whether the admitted value only repeats the book's current value.
  - Agreement with an identifier-accepted record that the candidate does not
    itself cite, ignoring case and spacing. It reads "not estimable" where no
    such record gives the field.

  These screens are necessary, but they do not show that a value is right.
  Neither snapshot holds an owner decision, so owner-labelled value precision
  (#1418) cannot be measured here.

## Tables

### Replay

| Snapshot | Decision engine | Scalar fields | Rich-preview reasons | Owner decisions | Cases not replayed | Replayed reasons that differ | Mirror disagreements with the source |
|---|---|---|---|---|---|---|---|
| ebook-744 | deterministic-shadow-decision-policy-v9 | authors, language, publishedDate, publisher, title | collapsed | 0 | 0 | 0 | 0 of 201 |
| audiobook-1294 | deterministic-shadow-decision-policy-v22 | authors, language, narrators, publishedDate, publisher, title | separate reasons | 0 | 0 | 0 | 0 of 1 |

### Populations

| Snapshot | Population | Destination | Reason | Medium | Fields (no / one / more alternatives) |
|---|---|---|---|---|---|
| ebook-744 | blocked | title | insufficient_support | ebook | 80 (0 / 0 / 80) |
| ebook-744 | proposable | authors | insufficient_support | ebook | 9 (0 / 9 / 0) |
| ebook-744 | proposable | publisher | insufficient_support | ebook | 14 (0 / 8 / 6) |
| audiobook-1294 | blocked | language | insufficient_support | audiobook | 3 (0 / 0 / 3) |
| audiobook-1294 | blocked | title | insufficient_support | audiobook | 2 (0 / 2 / 0) |
| audiobook-1294 | proposable | language | insufficient_support | audiobook | 5 (0 / 5 / 0) |
| audiobook-1294 | proposable | language | insufficient_support | ebook | 1 (0 / 1 / 0) |

### Upstream echo and rich-preview leftover

| Snapshot | Upstream echo (insufficient evidence), blocked fields | Rich-preview no-eligible-action leftover, all fields |
|---|---|---|
| ebook-744 | 162 | not separable: rich-preview refusals collapsed |
| audiobook-1294 | 1 | 0 |

### First failing check

| Snapshot | Population | Reason | First failing check or state | Candidates |
|---|---|---|---|---|
| ebook-744 | blocked | insufficient_support | support_local | 83 |
| ebook-744 | blocked | insufficient_support | support_correlation | 89 |
| ebook-744 | proposable | insufficient_support | support_local | 29 |
| audiobook-1294 | blocked | insufficient_support | no_action | 2 |
| audiobook-1294 | blocked | insufficient_support | language_conflict | 3 |
| audiobook-1294 | proposable | insufficient_support | support_local | 1 |
| audiobook-1294 | proposable | insufficient_support | no_action | 5 |

### Relaxations

| Relaxation | Snapshot | Population | Evaluated | Main already passes | Refused: none / degraded / ambiguous / conflict | Admitted | Cited records: right work / wrong work / unlabelled | Identity precision (Wilson 95% lower limit) | Fields: tied / decided (admitted is the right-work one) / no right-work | Repeats current value | Value agreement with an uncited record |
|---|---|---|---|---|---|---|---|---|---|---|---|
| title_two_correlations_without_local | ebook-744 | blocked | 80 | 0 | 43 / 0 / 0 / 0 | 37 | 47/0/73 | 1.000 (0.924) | 0 / 16 (16 right-work) / 21 | 0 | not estimable (0 comparable) |
| authors_one_correlation | ebook-744 | proposable | 9 | 0 | 0 / 0 / 0 / 0 | 9 | 15/0/11 | 1.000 (0.796) | 0 / 6 (6 right-work) / 3 | 0 | not estimable (0 comparable) |
| publisher_two_correlations_without_local | ebook-744 | proposable | 14 | 0 | 10 / 0 / 0 / 0 | 4 | 8/0/0 | 1.000 (0.676) | 1 / 3 (3 right-work) / 0 | 0 | 0/1 |
| title_two_correlations_without_local | audiobook-1294 | blocked | 2 | 0 | 2 / 0 / 0 / 0 | 0 | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) |
| language_two_correlations_without_local | audiobook-1294 | blocked | 3 | 0 | 0 / 0 / 0 / 3 | 0 | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) |
| language_two_correlations_without_local | audiobook-1294 | proposable | 6 | 0 | 5 / 0 / 0 / 0 | 1 | 2/0/0 | 1.000 (0.342) | 0 / 1 (1 right-work) / 0 | 0 | not estimable (0 comparable) |
| authors_one_correlation | pooled | both | 9 | 0 | 0 / 0 / 0 / 0 | 9 | 15/0/11 | 1.000 (0.796) | 0 / 6 (6 right-work) / 3 | 0 | not estimable (0 comparable) |
| title_two_correlations_without_local | pooled | both | 82 | 0 | 45 / 0 / 0 / 0 | 37 | 47/0/73 | 1.000 (0.924) | 0 / 16 (16 right-work) / 21 | 0 | not estimable (0 comparable) |
| publisher_two_correlations_without_local | pooled | both | 14 | 0 | 10 / 0 / 0 / 0 | 4 | 8/0/0 | 1.000 (0.676) | 1 / 3 (3 right-work) / 0 | 0 | 0/1 |
| language_two_correlations_without_local | pooled | both | 9 | 0 | 5 / 0 / 0 / 3 | 1 | 2/0/0 | 1.000 (0.342) | 0 / 1 (1 right-work) / 0 | 0 | not estimable (0 comparable) |
| published_date_day_one_correlation | pooled | both | 0 | 0 | 0 / 0 / 0 / 0 | 0 | 0/0/0 | not estimable | 0 / 0 (0 right-work) / 0 | 0 | not estimable (0 comparable) |

## Reading the analysis

The old A6 row counted three different things. On ebook-744 its 5,481
fields were 80 abstentions of the scalar engine, 162 upstream echoes, and
5,239 rich-preview refusals that the old ebook source recorded under the
generic "no eligible action" reason. On audiobook-1294 its 6 fields were 5
scalar abstentions and 1 echo, with no rich-preview leftover. All 80
ebook-744 fields in row A6a are titles, which main now decides with later
rules.

The abstention's own cost is small. The proposable population is 23 fields
on ebook-744 (9 authors and 14 publishers) and 6 on audiobook-1294 (all
language).

Support, or the lack of an eligible action, explains every abstention. The
replay is exact on both snapshots, and the mirror reaches the source's own
verdict for every candidate. Every abstained candidate fails local or
correlation support, has no action the decision could take, or belongs to a
language field whose proposal conflicts with the language policy.

No relaxation can be switched on. Only owner-labelled values can qualify a
relaxation, and neither snapshot holds an owner label. The one admitted
field that could be compared with an uncited accepted record disagreed with
it. Identity precision is a screen, never the bar, for two reasons.

- It counts records, not fields. Over both populations the relaxations
  admit 51 fields, whose cited records are 72 right work, none wrong work
  and 84 unlabelled. Counted by field, the labels decide the right-work
  alternative for 16 of the 37 admitted title fields, 6 of the 9 author
  fields, 3 of the 4 publisher fields (with 1 more tied) and the 1 language
  field. In every decided field the admitted value is the right-work one.
- The title figures are stale. They all come from ebook-744 fields decided
  by the old v9 engine, before main's title rules.

The pass mark is not set. It is the owner's to set, and it will apply to
owner-labelled values, not to identity. ADR-0092 therefore keeps the gate,
and leaves any later relaxation to the owner labels that #1418 can collect.

## Reproduce

Run the [ledger's Reproduce command](admission-rule-ledger.md#reproduce)
with `--a6-abstention` added. It prints these tables instead of the
ledger's, and the JSON document carries them under `a6_abstention`. The
analysis rebuilds every current case's decision input under its own source,
which takes about forty seconds for both snapshots.
