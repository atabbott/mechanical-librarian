# ADR-0092: Keep the automatic-decision abstention gate

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1745 (row A6 of the admission-rule ledger, #1350)
- Amends: ADR-0029. This ADR defines the would-admit note. Eligibility is
  unchanged. ADR-0036's qualified envelope is unchanged.
- Supersedes: none
- Superseded by: none

## Context

The admission-rule ledger (#1350) lists every rule that keeps a metadata
value from reaching a book automatically. It ranks the rules by the number
of held-back fields a loosened rule could reach, multiplied by the lower
limit of a 95% interval on how often the records it would admit belong to
the right work. Its row A6 is automatic-decision abstention: the
decision step looks at a field it is allowed to fill automatically, and
declines to pick a value, so the field falls back to manual review. The
ledger ranked A6 second. Its proposed loosening was to take the first-listed
alternative wherever the decision declined. #1745 asked whether
that, or any narrower loosening, is safe.

Two terms recur below. The proposal set is the earlier step that reads the
provider evidence and proposes a value for each field, or refuses to. The
decision step then either applies a proposed value automatically or
abstains. It has two engines. The scalar engine decides single-value fields:
title, authors, narrators, publisher, language and published date. The
rich-preview engine handles every other field, such as series.

Measured again, A6 turned out not to be one rule.

- **Three things were counted as one.** On the ebook-744 snapshot (source
  revision 1203cf58) the row held back 5,481 fields that the proposal set
  had already refused. Only 80 of them were abstentions of the scalar
  engine. Another 162 were abstentions whose reason is that the proposal
  set's evidence was insufficient (`evidence_sufficiency_manual`). The
  remaining 5,239 were refusals of the rich-preview engine, which this old
  source recorded under the generic reason "no eligible action"
  (`no_eligible_action`) instead of their own. On audiobook-1294 (source
  revision 32360e99) the 6 fields were 5 scalar abstentions and 1
  insufficient-evidence abstention, with no rich-preview refusals.
  - An insufficient-evidence abstention only repeats the proposal set's own
    refusal. That refusal belongs to the ledger rows for the proposal set's
    rules (T1 and A1 to A5), so this ADR calls it an upstream echo.
  - On main the rich-preview engine records each refusal's own reason, so
    those fields belong to the rich-preview rows, not to A6.
- **The real cost was missing.** All of those fields were already refused
  by the proposal set, so the decision step had nothing to apply. The cost
  of abstaining is a different set of fields: ones where the proposal set
  did propose a value, policy allows the field to be filled automatically,
  and the decision still abstained. The owner sees each of these as a field
  left for manual review. There are 23 on ebook-744 (9 authors, 14
  publishers) and 6 on audiobook-1294 (all language). This ADR calls them
  the proposable population, and the already-refused fields the blocked
  population.
- **"First alternative" ranks nothing.** A field's alternatives are sorted
  by `alternative_id`, which is a SHA-256 digest. The first alternative, the
  one with the lowest identifier, is therefore an arbitrary one.
- **The engines are old.** The ebook source decides with decision policy v9
  and the audiobook source with v22, while main runs v27. All 80 scalar
  ebook-744 fields are titles, and main now decides titles with later
  rules (#974, #981, #1040).

The ledger script's `--a6-abstention` option measures the scalar engine on
both populations. For every field where the decision abstained, it rebuilds
the decision's input under the snapshot's own source revision and replays
that revision's decision. It then names, for each candidate value, the first
eligibility check the candidate fails. These are the checks of
`_candidate_is_eligible`, mirrored clause by clause in the script.

- **The replay is exact.** Every current case was rebuilt and replayed, and
  every replayed reason equals the stored one. For all 202 candidates
  compared, the mirrored checks reach the same eligible-or-not verdict as
  the source's own code.
- **Support, or no eligible action, explains every abstention.** Two of the
  checks measure support. Local support asks whether the value matches the
  book file's own metadata, and correlation support counts independent
  providers that agree on it. On ebook-744 every abstained candidate fails
  one of these two: local support for 83 candidates of blocked fields and
  29 of proposable fields, and correlation support for 89 of blocked
  fields. On audiobook-1294, 3 abstentions are language fields whose
  proposals conflict with the language policy, 7 candidates have no action
  the decision could take, and 1 candidate fails local support.

The script then tries one narrow loosening per scalar field, called a
relaxation, under main's checks. Decision 3 lists them. Each relaxation
lowers one support threshold by one step. Pooled over both snapshots and
both populations, they give these results:

| Relaxation | Fields evaluated | Fields admitted | Cited records: right work / wrong work / unlabelled | Identity precision (Wilson 95% lower limit) |
|---|---|---|---|---|
| authors, one correlation | 9 | 9 | 15 / 0 / 11 | 1.000 (0.796) |
| title, two correlations, no local | 82 | 37 | 47 / 0 / 73 | 1.000 (0.924) |
| publisher, two correlations, no local | 14 | 4 | 8 / 0 / 0 | 1.000 (0.676) |
| language, two correlations, no local | 9 | 1 | 2 / 0 / 0 | 1.000 (0.342) |
| published date, day precision, one correlation | 0 | 0 | 0 / 0 / 0 | not estimable |

A cited record counts as right work or wrong work when the snapshot's own
identifier evidence already ties it to this book or to another one. Identity
precision is the share of labelled records that are right work. The Wilson
lower limit is the smallest share that the counts support with 95%
confidence. With few records it is far below the observed share.

In the proposable population the relaxations admit 14 of 29 fields. No
admitted value merely repeats the value the book already has. Only 1
admitted field could be compared with an identifier-accepted record that the
candidate does not itself cite, and that record gave a different value. Both
snapshots hold 0 owner decisions.

Identity precision says whether an admitted value's records belong to the
book. It does not say whether the value is right. Only owner-labelled values
can say that, and none exist yet. Two further limits apply.

- **The counts are records, not fields.** One field can cite many records.
  Counted by field, the labels decide which alternative is the right work
  for 16 of the 37 admitted title fields, 6 of the 9 author fields, 3 of the
  4 publisher fields (with 1 more tied between two right-work alternatives)
  and the 1 language field. In every decided field the admitted value is the
  right-work one. The other 21 title fields have no right-work alternative
  at all.
- **The title figures are stale.** Every title figure comes from ebook-744
  fields decided by the old v9 engine, before main's title rules. That
  engine's records also lack two signals main uses for titles, independent
  title evidence and recording support, and the analysis treats them as
  absent.

## Decision

1. **Keep the gate.** The scalar engine's abstentions stay exactly as they
   are. No reason code, threshold, rule or qualified option changes, and
   nothing under `src/` changes.
2. **Reject taking the first alternative** for every field. The order is a
   hash, the rule mixes two engines, and it counts fields the proposal set
   already refused.
3. **A6 is the scalar engine only.** The ledger splits the row in two, and
   ranks neither part.
   - Row A6a is the scalar engine's own abstentions: fields in the source
     revision's scalar list (`_SUPPORTED_DESTINATIONS`) where the decision
     abstained for insufficient support (`insufficient_support`) or because
     no action was eligible (`no_eligible_action`).
   - Row A6b is what is left of the rich-preview engine's "no eligible
     action" refusals. Wherever the source records each refusal's own
     reason, those fields move to that reason's own row.
   - The upstream echo, an insufficient-evidence abstention
     (`evidence_sufficiency_manual`), is reported in the A6 analysis only.
     The ledger never counts or scores it.

   Any future loosening must be a closed relaxation, one per field. It
   admits a value only when exactly one candidate fails a single named
   support threshold, passes it once the threshold is lowered by one step,
   and is not a degraded value such as a narrator credit in an author field.
   It never picks by list order. These are the five relaxations measured
   here:
   - `authors_one_correlation`: authors with one agreeing independent
     provider instead of two;
   - `title_two_correlations_without_local`: a title with two agreeing
     providers and no match in the book file;
   - `publisher_two_correlations_without_local`: the same rule for
     publisher;
   - `language_two_correlations_without_local`: the same rule for language;
   - `published_date_day_one_correlation`: a published date given to the
     day, with one agreeing provider.

   The authors relaxation as measured is looser than the one proposed in
   #1745. It counts one agreeing provider only, without checking that
   one of the records is bound to this book's identity. A later change that
   activates an authors relaxation would add that check, using the
   identity rule #1355 is defining.

   Everything else stays as it is unless a later measurement says
   otherwise: narrators, degraded values, a value supported only by the
   current one (`target_only_preservation`), every conflict reason, the
   language policy conflict (`field_policy_conflict`), the publication-event
   and consistency checks, citation and combination checks, the rule that
   one action must be unique (`unique_action_required`) and the rule that
   combined values are decided together.
4. **The would-admit note.** This ADR defines it, and #1355's ADR adopts this
   definition without changing it. It stays a definition, with nothing
   built, until the owner approves a relaxation.
   - It is an annotation on one shadow decision that carries no authority.
     It names the relaxation and the candidate it would admit.
   - The decision keeps its manual action and its abstention reason, and
     its disposition stays an abstention. No automatic decision is created,
     and the field stays in manual review. This matters because automatic
     authority depends on the field's policy category and option, not on
     which rule decided. A relaxation placed inside the decision's
     eligibility code (`_eligible_actions`) would therefore act at once
     under options that are already qualified. The note never changes the
     chosen action.
   - Anything the owner reads, and any API name, says "would admit", never
     "shadow". Stored names stay as they are until the refactor that owns
     them.
   - Adding the note changes the domain. It needs a new decision-policy
     version and hash, a decision-set schema change and a clean rebuild
     (ADR-0013). It is made only together with a relaxation the owner has
     approved.
   - Amended by ADR-0096 (#1355): a note stored beside an unchanged live
     set needs no policy or set version move.
5. **The bar for activating a relaxation.** A relaxation may be switched on
   only on the strength of owner-labelled value precision for that field
   and that relaxation, measured through #1418 under a new qualification
   identity. ADR-0036's envelope is not widened in place.
   - The pass mark is the owner's to set. The proposal for the owner is a
     Wilson 95% lower limit of at least 0.95 with no contradiction, which
     needs at least 73 labelled values with no error.
   - Identity precision and agreement with uncited accepted records are
     screens a relaxation must pass first. One wrong-work record
     disqualifies a relaxation. A right-work record proves nothing about
     whether the value is right.

## Consequences

### Positive

- The ledger no longer charges A6 with 5,239 rich-preview refusals and 162
  upstream echoes it does not own. The abstention's own cost is now
  visible: 29 proposable fields across both snapshots.
- The measurement replays each source's own decision and checks it case by
  case. The first failing check is the script's mirror of the eligibility
  code, and the mirror's verdict was checked against the source's own for
  every candidate.
- Any later loosening has a closed definition, a shared would-admit note
  and a stated bar to meet.

### Negative

- The 29 proposable fields stay in manual review for now. For 14 of them a
  relaxation would admit exactly one candidate, whose records are all right
  work or unlabelled.
- The ebook figures come from a v9 engine that predates three title rules.
  Every ebook-744 field in row A6a is stale for main.

### Neutral or deferred

- #1418's full-library run measures A6 again with the same script on a
  current source. That run is also where owner labels can be collected, if
  the owner wants a relaxation tried.
- The pass mark, and whether review shows a would-admit hint, are the
  owner's choices. They are asked only if a relaxation is proposed.
- The rich-preview leftover (row A6b) cannot be separated on ebook-744. On
  audiobook-1294 it is 0.

## Alternatives considered

### Take the first alternative

Rejected by Decision 2. The order is a hash, and most of the fields it
counts are not this gate's.

### Loosen a relaxation now on identity precision

Rejected by Decision 5. Only owner-labelled values can qualify a relaxation.
Both snapshots hold 0 owner decisions, so there is no owner label, and the
one admitted field that could be compared with an uncited accepted record
disagreed with it. Identity precision cannot qualify a relaxation at any
level, because it measures whether a cited record belongs to the right
work, not whether the admitted value is correct. It also counts records
rather than fields, and the title records come from stale ebook-744
fields. The pass mark is not set. When the owner sets it, it will apply to
owner-labelled values, not to identity.

### Record the keep as an issue comment

Rejected when #1745 was planned. A6 is the gate every automatic field
shares, and the would-admit note needs one lasting definition that #1355 can
adopt.

## Validation

- `tests/unit/test_admission_rule_a6.py` covers:
  - naming the first failing check, and counting the mirror's
    disagreements with the source;
  - each relaxation's admissions and refusals: no candidate, several,
    degraded, support from the current value only, conflict reasons and the
    language conflict;
  - a candidate outside the one-step loosening: one that fails another check
    too, a title that matches the book file, and a date given only to the
    month;
  - the screens: wrong-work records, values that repeat the current one,
    tied, decided and undecided fields, and agreement that never counts a
    record the candidate cites;
  - the upstream echo and the rich-preview leftover counted only on their
    own fields, and the proposable population kept apart from the blocked
    one;
  - n = 0 reported as not estimable, and output that holds aggregates only;
  - a product-built case replayed through the real source process with no
    mirror disagreement.
- `tests/unit/test_admission_rule_ledger.py` covers the split into rows A6a
  and A6b, the upstream echo's exclusion from A6a, and the status shown when
  a source cannot separate rich-preview reasons.
- Re-running the documented Reproduce commands on both retained snapshots
  regenerates the tables of [the ledger](../../product/admission-rule-ledger.md)
  and of [the A6 analysis](../../product/automatic-decision-abstention.md)
  byte for byte.
