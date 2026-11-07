# ADR-0092: Keep the automatic-decision abstention gate

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1745 (A6 of the admission-rule ledger, #1350)
- Amends: ADR-0029 (defines the would-admit note; eligibility unchanged).
  ADR-0036's qualified envelope is unchanged.
- Supersedes: none
- Superseded by: none

## Context

The admission-rule ledger (#1350) ranked A6, "automatic-decision abstention",
second by score. Its proposed loosening was to admit the first-ranked
alternative wherever an automatic decision abstained. #1745 asked whether
that, or any narrower loosening, is safe.

Re-measured, A6 was not one gate:

- **Three things were counted as one.** On the ebook-744 snapshot (source
  1203cf58) the row's 5,481 blocked fields were 80 abstentions of the scalar
  decision engine, 162 `evidence_sufficiency_manual` abstentions and 5,239
  rich-preview refusals that this source collapsed into `no_eligible_action`.
  On audiobook-1294 (source 32360e99) the 6 were 5 scalar abstentions and 1
  `evidence_sufficiency_manual`, with no rich residual.
  - An `evidence_sufficiency_manual` abstention only repeats the proposal
    set's refusal, which other ledger rows own (T1, A1 to A5).
  - On main the rich-preview path keeps each refusal's own reason, so those
    fields belong to the rich rows, not to A6.
- **The real cost was missing.** "Blocked" means the proposal set already
  refused the field. The abstention's own cost is a field the proposal set
  proposes, whose policy destination is automatic, and on which the decision
  still abstains. The owner sees it as manual fallback. That population is
  23 fields on ebook-744 (authors 9, publisher 14) and 6 on audiobook-1294
  (language).
- **"First-ranked" ranks nothing.** A result's alternatives are ordered by
  `alternative_id`, a SHA-256 digest, so the first one is an arbitrary
  choice.
- **Stale engines.** The ebook source decides with policy v9 and the
  audiobook source with v22, against v27 on main. All 80 scalar ebook-744
  fields compete on title, which later elections (#974, #981, #1040) decide
  on main.

`--a6-abstention` of `scripts/admission_rule_ledger.py` measures the scalar
engine (A6a) on both populations. For every abstained field it rebuilds the
decision packet under the snapshot's own source, replays that source's
decision, and attributes each candidate to the first failing named check of
`_candidate_is_eligible`.

- **The replay is exact.** Every current case rebuilt and replayed, and
  every replayed reason equals the stored one. The mirrored checks agree
  with the source's verdict for all 202 candidates compared.
- **Support, or no eligible action, refuses.** On ebook-744 the abstentions
  fail local support (83 candidates of blocked fields, 29 of proposable
  ones) or correlation support (89 of blocked fields). On audiobook-1294
  the abstentions are 3 language policy conflicts, 7 candidates without a
  candidate action and 1 local-support failure.

It then evaluates one closed relaxation per scalar destination under main's
checks (named under Decision 3). Pooled over both snapshots and populations:

| Relaxation | Fields evaluated | Admitted | Identity TP / FP / unlabelled | Identity precision (Wilson 95% lower) |
|---|---|---|---|---|
| authors, one correlation | 9 | 9 | 15 / 0 / 11 | 1.000 (0.796) |
| title, two correlations, no local | 82 | 37 | 47 / 0 / 73 | 1.000 (0.924) |
| publisher, two correlations, no local | 14 | 4 | 8 / 0 / 0 | 1.000 (0.676) |
| language, two correlations, no local | 9 | 1 | 2 / 0 / 0 | 1.000 (0.342) |
| publishedDate, day precision, one correlation | 0 | 0 | 0 / 0 / 0 | not estimable |

In the proposable population the relaxations admit 14 of 29 fields. No
admitted candidate only preserves the current value. Only 1 admitted field
had an uncited, identifier-accepted record to compare its value with, and
that record disagreed. Both snapshots hold 0 human decisions.

Identity precision says whether an admitted value's records belong to the
work, not whether the value is right. Value correctness needs owner labels,
and none exist yet.

- **Identity n counts records, not fields.** Per field, the controls decide
  title on 16 of its 37 admitted fields, authors on 6 of 9, publisher on 3
  of 4 (plus 1 tied) and language on 1 of 1. In each decided field the
  admitted candidate is the right-work one. The other 21 title fields have
  no right-work alternative.
- **Title is stale.** Every title figure comes from ebook-744 fields decided
  by its v9 engine, before the title elections on main. That engine's
  summaries also lack the independent-title and recording features, which
  the analysis reads as absent.

## Decision

1. **Keep the gate.** The scalar decision engine's abstentions stay exactly
   as they are. No reason code, threshold, rule or qualified option changes,
   and nothing under `src/` changes.
2. **Reject "admit the first-ranked alternative"** for every destination. It
   is hash order, it mixes two engines, and it counts fields the proposal
   set already refused.
3. **A6 is the scalar engine.** The ledger splits the row in two:
   - **A6a** is the scalar engine: the source revision's
     `_SUPPORTED_DESTINATIONS`, abstaining with `insufficient_support` or
     `no_eligible_action`.
   - **A6b** is the rich-preview `no_eligible_action` residual. It is
     refiled under its rich reason's own row wherever the source can
     separate it.
   - `evidence_sufficiency_manual` is an upstream echo of the proposal
     set's refusal. The A6 analysis reports it; the ledger never counts or
     scores it.
   - Neither row is ranked.

   Any future loosening is a closed relaxation, one per destination. It
   admits the unique candidate that fails only one named support threshold,
   passes it relaxed by one step, and is not degraded. It never selects by
   ordering. The measured relaxations are these five:
   - `authors_one_correlation`;
   - `title_two_correlations_without_local`;
   - `publisher_two_correlations_without_local`;
   - `language_two_correlations_without_local`;
   - `published_date_day_one_correlation`.

   As measured, `authors_one_correlation` has no identity-bound record
   check: it counts one independent correlation only. A PR 2 relaxation for
   authors would add #1355's identity-bound record predicate.

   Narrators, degraded candidates, `target_only_preservation`, every
   conflict code, the language `field_policy_conflict`, publication event
   and paired consistency, citation and combination subsets,
   `unique_action_required` and combination atomicity stay whatever a
   measurement shows.
4. **The would-admit note.** This ADR defines it; #1355's ADR adopts it and
   does not redefine it. It stays a definition until an owner-approved
   relaxation exists:
   - **Shape.** A non-authoritative annotation on one shadow decision: the
     closed relaxation name and the packet candidate id it would admit.
   - **No authority.** The decision keeps its `manual` action and its
     abstention reason. Its disposition stays `abstention`, so no automatic
     decision is created and the field stays manual fallback. Automatic
     authority is keyed by policy category and option, not by decision
     rule, so a relaxation inside `_eligible_actions` would act at once
     under existing qualified options. The note never changes the selected
     action.
   - **Wording.** Read-time and API names say "would admit", never
     "shadow". Stored names stay as they are until the refactor that owns
     them.
   - **Where it lives.** Adding the member is a domain change: a
     decision-policy version and SHA change, a decision-set schema change
     and a clean rebuild (ADR-0013). It is made only together with a
     relaxation the owner has approved.
5. **The bound.** A relaxation may be activated only on owner-labelled value
   precision for that destination and relaxation, through #1418, under a new
   qualification identity. ADR-0036's envelope is not widened in place.
   - **Pass mark (proposal, for the owner).** A Wilson 95% lower limit of
     at least 0.95, no contradiction, and n of at least 73 at zero errors.
   - **Screens only.** Identity precision from natural controls and value
     agreement with uncited accepted records are necessary screens. An
     identity false positive disqualifies a relaxation; an identity true
     positive proves nothing about the value.

## Consequences

### Positive

- The ledger no longer charges A6 with 5,239 rich refusals and 162 upstream
  echoes it does not own. The abstention's own cost is now visible as a
  population: 29 proposable fields across both snapshots.
- The measurement replays each source's own decision, verified case by
  case. The first failing check is the mirror's attribution, checked
  against the engine's own eligibility verdict for every candidate.
- Any later loosening has closed predicates, a shared would-admit shape and a
  stated bound to meet.

### Negative

- The 29 proposable fields stay manual fallback for now. Fourteen of them
  have a unique relaxation-admitted candidate whose records are all
  positive or unlabelled.
- The ebook figures come from a v9 engine that predates three title
  elections. Every ebook-744 A6a field is stale for main.

### Neutral or deferred

- #1418's full-library re-run re-measures A6 with the same script on a
  current source. That run is also where owner labels can be collected, if
  the owner wants a relaxation tried.
- The pass mark and any would-admit hint in review are the owner's choices,
  and are asked only if a relaxation is proposed.
- The rich residual on a current source is not separable on ebook-744. On
  audiobook-1294 it is 0.

## Alternatives considered

### Admit the first-ranked alternative

Rejected (Decision 2): the order is a digest, and the population it counts
is mostly not this gate's.

### Loosen a relaxation now on identity precision

Rejected (Decision 5). Only owner-labelled value precision can qualify a
relaxation. Both snapshots hold 0 human decisions, so no owner label exists,
and the one admitted field comparable for value agreement disagreed (0 of
1). Identity cannot qualify a relaxation at any level: its n counts records,
and title's records come from stale ebook-744 fields. The pass mark is not
set; it is the owner's, and it would apply to owner-labelled values, not to
identity.

### Record the keep as an issue comment

Rejected by the lane's decision. A6 is the gate every automatic destination
shares, and the would-admit note needs one durable definition that #1355 can
adopt.

## Validation

- `tests/unit/test_admission_rule_a6.py` covers:
  - the first-failing-check attribution;
  - mirror disagreement;
  - each relaxation's admission and its refusals (none, several, degraded,
    target-only preservation, conflict codes, the language conflict), and
    a candidate outside its one step (another failing check, local
    evidence, month precision);
  - the screen's false positives and preserved values, and AO's tied,
    discriminable and no-right-work classes;
  - the echo and the rich residual kept to their own fields;
  - value agreement that never counts a cited record;
  - the proposable population kept apart from the blocked one, and the
    alternatives classes;
  - n = 0 as not estimable;
  - aggregate-only output;
  - a product-built case replayed through the real child with no mirror
    disagreement.
- `tests/unit/test_admission_rule_ledger.py` covers the A6a/A6b split, the
  echo's exclusion from A6a and the collapsed-source status.
- Re-running the documented Reproduce commands on both retained snapshots
  regenerates the tables of [the ledger](../../product/admission-rule-ledger.md)
  and of [the A6 analysis](../../product/automatic-decision-abstention.md)
  byte for byte.
