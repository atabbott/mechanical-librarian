# Automatic-decision abstention (A6)

The [admission-rule ledger](admission-rule-ledger.md)'s A6 row,
"automatic-decision abstention", measured on its own terms (#1745). The
tables below are the Markdown output of
[`scripts/admission_rule_ledger.py`](../../scripts/admission_rule_ledger.py)
`--a6-abstention`, unedited, over the ledger's two retained snapshots.
[ADR-0092](../architecture/decisions/0092-keep-the-automatic-decision-abstention-gate.md)
records the decision they support: keep the gate.

## Method

- **Engines.** The ledger's A6a row is the scalar decision engine: the
  destinations in the source revision's own
  `shadow_decision._SUPPORTED_DESTINATIONS`. The ebook-744 source decides
  with policy v9 and the audiobook-1294 source with v22; main runs v27.
- **Populations.** Each field is one current review case and destination.
  - **Blocked** fields are A6a's: the proposal set refused the field and the
    decision abstained with `insufficient_support` or `no_eligible_action`.
  - **Proposable** fields are the abstention's own cost: the proposal set
    proposes a change or an acceptable choice, the field's policy
    destination is automatic, and the decision still abstains with one of
    those reasons. The owner sees them as manual fallback.

  Both are counted by destination, reason, medium and the number of
  alternatives the field kept.
- **Upstream echo and rich residual.** An `evidence_sufficiency_manual`
  abstention repeats the proposal set's refusal; it is counted only here,
  never in the ledger, and never scored. The rich residual is `no_eligible_action` on a destination outside
  the scalar engine, over all fields. It is separable only where the source
  keeps rich refusals' own reasons.
- **Replay.** For every current case a child process imports only the
  snapshot's source tree. It rebuilds the decision packet from the stored
  review case, proposal set and report with that source's
  `ShadowReviewPacket.create`, and replays that source's decision. The replay
  table counts cases that would not rebuild and replayed reasons that differ
  from the stored ones.
- **First failing check.** Each candidate of an abstained field is attributed
  to the first failing named check of that source's `_candidate_is_eligible`,
  in this order: scope, binding, citations, publication, preservation, local
  support, correlation support, narrator, evidence-led title. Where no check
  applies, a state is named instead: the candidate passes, has no candidate
  action, or is never evaluated by its branch; or the field has no candidate
  or a language policy conflict. The checks are mirrored clause by clause.
  For every candidate with an action the mirror is compared with the
  source's own verdict, and the replay table counts disagreements.
- **Relaxations.** There is one closed relaxation per scalar destination
  except narrators. Each is evaluated only where the source's decision
  abstained with `insufficient_support`, under this checkout's checks (v27).
  - **What it admits.** The unique candidate that fails only one named
    support threshold, passes it relaxed by one step, and is not degraded.
  - **What it refuses.** Zero or several such candidates, a language policy
    conflict, and a candidate this checkout already passes ("checkout passes
    already"). No ordering is used.
- **Screens.** An admitted candidate is screened four ways:
  - **Identity precision** of its cited records, with the ledger's natural
    controls and Wilson lower limit. n = 0 is "not estimable".
  - **AO's field classes:** tied (two or more right-work alternatives),
    discriminable (exactly one, and whether the admitted candidate is it),
    or no right-work alternative.
  - **Preserving** the current value only.
  - **Value agreement** with an identifier-accepted record the candidate
    does not cite, compared ignoring case and spacing. It is "not
    estimable" where no such record states the field.

  These are necessary screens, not value correctness. Neither snapshot holds
  a human decision, so owner-labelled value precision (#1418) cannot be
  measured here.

## Breakdown

### Replay

| Snapshot | Decision engine | Scalar destinations | Rich refusals | Human decisions | Cases not replayed | Replayed reasons that differ | Mirror disagreements |
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

### Upstream echo and rich residual

| Snapshot | Upstream echo (evidence_sufficiency_manual), blocked fields | Rich no_eligible_action residual, all fields |
|---|---|---|
| ebook-744 | 162 | not separable: rich refusals collapsed |
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

| Relaxation | Snapshot | Population | Evaluated | Checkout passes already | Refused: none / degraded / ambiguous / conflict | Admitted | Identity TP/FP/unlabelled | Identity precision (Wilson 95% lower) | Tied / discriminable (right) / no right-work | Preserves current | Value agreement |
|---|---|---|---|---|---|---|---|---|---|---|---|
| title_two_correlations_without_local | ebook-744 | blocked | 80 | 0 | 43 / 0 / 0 / 0 | 37 | 47/0/73 | 1.000 (0.924) | 0 / 16 (16 right) / 21 | 0 | not estimable (0 comparable) |
| authors_one_correlation | ebook-744 | proposable | 9 | 0 | 0 / 0 / 0 / 0 | 9 | 15/0/11 | 1.000 (0.796) | 0 / 6 (6 right) / 3 | 0 | not estimable (0 comparable) |
| publisher_two_correlations_without_local | ebook-744 | proposable | 14 | 0 | 10 / 0 / 0 / 0 | 4 | 8/0/0 | 1.000 (0.676) | 1 / 3 (3 right) / 0 | 0 | 0/1 |
| title_two_correlations_without_local | audiobook-1294 | blocked | 2 | 0 | 2 / 0 / 0 / 0 | 0 | 0/0/0 | not estimable | 0 / 0 (0 right) / 0 | 0 | not estimable (0 comparable) |
| language_two_correlations_without_local | audiobook-1294 | blocked | 3 | 0 | 0 / 0 / 0 / 3 | 0 | 0/0/0 | not estimable | 0 / 0 (0 right) / 0 | 0 | not estimable (0 comparable) |
| language_two_correlations_without_local | audiobook-1294 | proposable | 6 | 0 | 5 / 0 / 0 / 0 | 1 | 2/0/0 | 1.000 (0.342) | 0 / 1 (1 right) / 0 | 0 | not estimable (0 comparable) |
| authors_one_correlation | pooled | both | 9 | 0 | 0 / 0 / 0 / 0 | 9 | 15/0/11 | 1.000 (0.796) | 0 / 6 (6 right) / 3 | 0 | not estimable (0 comparable) |
| title_two_correlations_without_local | pooled | both | 82 | 0 | 45 / 0 / 0 / 0 | 37 | 47/0/73 | 1.000 (0.924) | 0 / 16 (16 right) / 21 | 0 | not estimable (0 comparable) |
| publisher_two_correlations_without_local | pooled | both | 14 | 0 | 10 / 0 / 0 / 0 | 4 | 8/0/0 | 1.000 (0.676) | 1 / 3 (3 right) / 0 | 0 | 0/1 |
| language_two_correlations_without_local | pooled | both | 9 | 0 | 5 / 0 / 0 / 3 | 1 | 2/0/0 | 1.000 (0.342) | 0 / 1 (1 right) / 0 | 0 | not estimable (0 comparable) |
| published_date_day_one_correlation | pooled | both | 0 | 0 | 0 / 0 / 0 / 0 | 0 | 0/0/0 | not estimable | 0 / 0 (0 right) / 0 | 0 | not estimable (0 comparable) |

## Reading the analysis

- **The old row was three things.** A6's 5,481 blocked ebook-744 fields were
  80 scalar abstentions, 162 upstream echoes and 5,239 rich-preview refusals
  the ebook source collapsed into `no_eligible_action`. Audiobook-1294's 6
  were 5 and 1, with no rich residual. All 80 ebook-744 A6a fields compete on
  title, which later elections decide on main.
- **The real cost is small.** The proposable population is 23 fields on
  ebook-744 (authors 9, publisher 14) and 6 on audiobook-1294 (language).
- **Only support refuses.** The replay is exact on both snapshots, and the
  mirror agrees with every source verdict. Every abstention fails local or
  correlation support, has no candidate action, or is a language policy
  conflict.
- **No relaxation is ready.** Over both populations the relaxations admit 51
  fields. Their records give 72 positives, no negative and 84 unlabelled.
  The strongest pooled lower limit is title's 0.924, below the proposed 0.95
  pass mark, and that is identity, not value. Only one admitted field could
  be compared for value agreement, and it disagreed. Hence ADR-0092: keep
  the gate, and let #1418's owner labels decide any later relaxation.

## Reproduce

Run the [ledger's Reproduce command](admission-rule-ledger.md#reproduce)
with `--a6-abstention` added. It prints these tables instead of the
ledger's, and the JSON document carries them under `a6_abstention`. The
analysis rebuilds every current case's decision packet under its source,
which takes about forty seconds for both snapshots.
