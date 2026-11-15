# ADR-0099: Qualify automatic description advice on the owner's decisions

- Status: proposed
- Date: 2026-11-15
- Owners: project maintainers
- Issues: #1886
- Relates to: ADR-0036 (the qualified automatic envelope), ADR-0087
  (operation-bound qualification identity), ADR-0092 Decision 5 (the bar for
  activating a relaxation)
- Supersedes: none
- Superseded by: none

## Context

With **Automatically apply ready description advice** on, the app writes the
description adviser's text without asking and may replace a description the
owner already has. Nothing yet measures how often that text is the one the
owner would have chosen.

The owner's decisions on descriptions are already stored. On a description,
`human_decisions` holds `keep_current`, `accept` (a source's description) and,
as history only, `reject`, which the work's page no longer offers.
`owner_entered` cannot be recorded on a description, which is not an owner
text destination, and a withdrawal is refused, since a description cannot be
reverted. Choosing the adviser's text records an authored decision in
`description_decisions` with origin `human` and points the case's
`current_description_decisions` row at it. A later human decision on the
description deletes that pointer. On one case the latest decision is
therefore the authored selection where the pointer exists, and otherwise the
latest human decision on the case. A case that returns as a new episode keeps
its earlier decisions as history; the label reads that history.

A rescan admits a new case for the same library item. As the admission-rule
ledger does, an item is the case's target connection, library and item. Advice
is reused across cases: its request identity covers the provider texts, the
cleanup choices, the reviewer-access revision, the engine, the model and the
profile. A second advice for one request identity arises only when the owner
presses **Retry description advice**. A verified changing write retires the
case's advice binding, so after a write and rescan the successor binds the
same advice, which now matches the library. Labels must be read from decision
history, not from current state.

Each case's report holds the library's description at the time of the case.
The product's no-change test (`values_match` on the description) says whether
advice repeats it.

The authored decision's `qualification_manifest_sha256` binds this checkout's
instruction, output schema and cleanup choices, not the engine or model. The
advice records the engine, model, instruction, schema and cleanup digests and
the profile digest. An OpenRouter profile is the engine and model alone; a
Codex profile digest comes from the image's build and cannot be reproduced
from a checkout.

The advice choice on the work's page is enabled only when the field is
editable, which needs a source description that differs from the library's.
Where every source repeats the library's description, the owner can neither
choose the advice nor keep the current description. Until #1895, Automatic
still admitted and wrote there, because admission checked neither the policy
destination nor the decision actions.

## Decision

1. **Labels are the owner's decisions in the app.** Each label is a real
   choice the owner makes on the work's page in the Library, where keeping
   the current description is recorded like any other choice. No tool
   requests advice to make labels. The owner chose labelling in the Library,
   with keeping the current description counting as wrong, on 2026-11-15.
2. **One label per library item.** On each case the latest decision is the
   current authored selection, else the latest human decision on the case,
   over every episode. Across the item's cases the latest by `occurred_at` wins, with
   ties to the case identifier. A withdrawal labels nothing and is counted as
   withdrawn. A current authored decision with origin `automatic_policy` is
   not a label and is counted as decided by Automatic.
3. **The advice judged.** The displayed advice D is the chosen advice for an
   adviser choice. Otherwise it is the newest advice created at or before the
   decision that the decided case X could have shown. That advice is found
   from X's own report, not only from X's current advice pointer, which a
   later preparation overwrites when X reused another case's advice: any
   advice whose request this checkout would send for X's report under one of
   the eight cleanup combinations, besides the request bound to X now and
   every attempt bound to X. With no such advice the
   decision is counted as decided before any advice. L, the answer Automatic
   would have acted on, is the earliest advice with D's request identity
   created at or before the decision, eligible or not. When L is not eligible
   the decision is counted as not eligible, under D's identity: advice that
   exists only because the owner retried an answer that needed review or
   fell below the floor is advice Automatic would never have written unasked.
4. **Eligible advice and strata.** Advice is eligible when it is ready, meets
   the description floor (`automatic_description_floor_met`) and does not
   match the description in X's report under the product's no-change test.
   The stratum is `fills` when that description is missing, null or empty,
   and `replaces` otherwise. Eligibility is judged at the decision's time by
   the advice's creation time, which approximates what the page showed: a
   decision made while advice was out of date can count as wrong.
5. **Right and wrong.** With L eligible and D not L, the label is wrong
   (asked for new advice): the owner pressed Retry over text Automatic would
   have written. Otherwise choosing the adviser's text is right, and keeping the
   current description, choosing a source's description, declining the
   change or entering your own is wrong. Withdrawn, decided by Automatic, not
   eligible, decided before any advice, and current eligible advice still
   undecided are not labels. The owner chose that keeping the current
   description counts as wrong on 2026-11-15.
6. **Fields the owner cannot choose.** Where every source repeats the
   library's description, the advice cannot be chosen, so those fields cannot
   be labelled and qualification does not cover them. Since #1895 (issue
   #1892), Automatic never writes a description field with no decision
   actions, so the labelled population is the whole population Automatic may
   write.
7. **Identity.** Labels are pooled by the advice's engine, model, instruction
   digest, output-schema digest and cleanup digest, as ADR-0087 binds a
   qualification to the operation it permits. Each of the eight cleanup
   combinations is its own identity. The qualification manifest is
   `description_qualification_manifest(preferences)`, the value every authored
   decision records, and is reported only where the instruction and schema
   are this checkout's and the cleanup combination is known. The profile
   digest is reported but is not part of the identity, so a Codex build does
   not reset the labels.
8. **The bar.** An identity passes when the Wilson 95% lower limit of its
   pooled labels is at least 0.90: 35 labels with no error, 53 with one, 69
   with two. Strata get their own counts and limits but no bar. The owner
   chose this bar on 2026-11-15.
9. **Instruments.** `scripts/description_advice_labels.py` reads a copy of
   the database read-only through the product's immutable, query-only
   reader and requests nothing. `sample` lists the descriptions to decide;
   `score` counts the labels per identity, in text or as shareable JSON
   (`description-advice-label-score-v1`). The
   [owner guide](../../operations/description-advice-qualification.md) runs
   them.
10. **Enforcement.** Automatic description advice only suggests until a
    release records a passing result for the adviser's identity: automatic
    admission refuses unless a shipped qualification record names the
    advice's engine, model, instruction digest, schema digest and cleanup
    choices. The switch stays choosable but only proposes until then. The
    owner chose this on 2026-11-15; it is built in a separate change. This
    decision changes no behaviour now.

## Consequences

### Positive

- Qualification costs no extra requests: the labels are decisions the owner
  makes anyway, on the text the app would have written.
- The score is shareable: counts, digests, engine and model only.

### Negative

- Organic decisions made before the run count, with whatever selection bias
  they carry. Decisions made before any advice existed do not count.
- Works whose sources share one text share one request identity, so the
  earliest-L rule is an approximation where they were decided at different
  times.

### Neutral or deferred

- The qualification record and the admission check are built in a later change
  (Decision 10).

## Alternatives considered

### A blind sheet with requests the script makes

Offered to the owner and declined: real choices in the Library cost nothing
extra and judge the text the app would write.

### One label per advice

Rejected: a work decided twice, or across a rescan, would count twice.

### Agreement with a reference text

Rejected: there is no reference description to agree with.

### The description floor as qualification

Rejected: the floor checks the text's shape, not whether it is the right
description.

### Binding the Codex build into the identity

Rejected: it changes with every CLI update and would reset the labels.

## Validation

`tests/unit/test_description_advice_labels.py` proves the refusals, every
`sample` count, the label rule, the identity groups and the manifest relation
on product-built stores. The owner's score JSON from the run is the
qualification evidence.
