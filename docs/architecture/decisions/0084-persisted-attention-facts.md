# ADR-0084: Persist which fields ask for the owner, and keep live cover authority per request

- Status: accepted
- Date: 2026-10-16
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The #1162 re-baseline at 441e571f found two population statements to be the main
warm cost of Library, search, the attention lens and Attention, and of the root
admission lane under readers: each re-evaluated the whole attention predicate
over every current field on every request (#1232). ADR-0083 removed the
per-request document parsing, but not that predicate, which reads decisions,
holds, suspensions, policy, safety state and cover authority for every
candidate.

## Decision

`review_attention_facts` holds one row for each current package, report and
field whose stored inputs ask for the owner. A package is keyed under both the
report it names, which Library reads, and its case's report, which Attention
reads. `_ATTENTION_FACT_SOURCE_SQL` is the one definition: the attention
predicate the browse statements evaluated before, with its cover authority
reduced to the stored part.

- **Inputs.** Thirteen tables: current packages, case field facts, report and
  proposal browse facts, policy resolutions, shadow outcome facts, automatic
  safety state, human and automatic decisions, current description decisions,
  current cover considerations and selections, and field-effect suspensions.
  Review cases, reports, cover considerations and selections are reached only
  through those children's immediate foreign keys, so inserting one changes no
  fact.
- **Maintenance.** An `AFTER` trigger on each input operation that can happen
  names the packages, and the field when the row has one, that the changed row
  can reach. It inserts them into the view `review_attention_refresh`, whose one
  `INSTEAD OF` trigger deletes those facts and re-derives them from current
  state. This runs in the writer's own statement and transaction. An update
  fires only when a column the facts read changes value, so re-recording the
  safety state's time, a suspension's reason or a package's checkpoint
  refreshes nothing; a test derives those columns from the source statement.
  Nothing in `src` or `scripts` writes with `REPLACE`, and no table declares a
  `REPLACE` conflict clause, either of which would skip a delete trigger; a
  test holds that.
- **What stays per request.** The active cover policy reference and replay
  reference are bound per runtime generation, and `ml_cover_consideration_current`
  reads live cover currentness. Neither is written to the database when it
  changes. A cover fact whose case has a current consideration therefore keeps
  that consideration, its policy reference and whether a refresh admits it, and
  each read tests `_ATTENTION_FACT_CURRENT_SQL` with the live values, exactly as
  the earlier statement did. Library tests a work's live cover authority only
  when no other field already put it in attention, as before. No input depends
  on the clock.
- **Readers.** Library, search and the lens read whether a work has a fact;
  Attention's page and count, the per-case attention count, and field-effect
  suspension's attentive cases join the facts. Results, ordering and refusals
  are unchanged.
- **Admission.** Open compares the table with its definition in both
  directions, and any difference refuses startup. The objects join the exact
  schema contract.
- **Schema posture.** The facts are reconstructible derived objects, so #1232
  delivers ADR-0013's exact-predecessor adoption plan,
  `review-attention-facts-v1`, through the offline `schema-adopt` command. It
  accepts only the exact 441e571f schema with valid contents, makes and verifies
  a private backup beside the database, creates the objects and derives every
  fact in one exclusive transaction, and then re-validates the result and
  proves every other table's rows unchanged. Ordinary startup still accepts
  only absent or exact-current state. #1245's report schema change retired the
  plan and the command; under ADR-0013 earlier state needs a clean rebuild.
- **Medium.** No reason in the definition is medium-specific. #1249 removed the
  audiobook clause that made every owner-actionable recording field ask, so the
  definition, and with it the refresh trigger's text, changed; ADR-0013's exact
  schema comparison refuses a store built before it, which needs a clean
  rebuild. EPUB facts are unchanged.

## Consequences

### Positive

- Browse no longer evaluates the attention predicate per candidate. Library,
  search, the lens and Attention select facts by package key.

### Negative

- Every input write pays to re-derive the facts it can reach. Measured on
  SQLite 3.40.1 at 2,300 works and again after 8,000 owner decisions, as
  medians of five over two rounds, against 2-7 ms for the same write before:
  - An owner decision re-derives one field, at 14.6-19.4 ms a decision against
    14.9-16.8 ms before; the difference is not resolvable.
  - A connection-wide suspension or its clearing re-derives every package on
    the connection: 90-107 ms.
  - A cover suspension or its clearing re-derives every package's cover field:
    71-92 ms.
  - Pausing or resuming automatic safety re-derives every package with an
    automatically decided field: 188-214 ms.
  - A policy activation re-derives every package whose proposal it resolves; it
    was not measured.

  These are owner actions and failures, not per-request work. Re-recording a
  suspension's reason or the safety state's time re-derives nothing: 6-10 ms.
- The facts are one more derived relation that open validates.

### Neutral or deferred

- Live cover currentness is still evaluated per cover fact on each read.

## Alternatives considered

### Maintain the facts in application code

Each writer would have to remember every fact it can change. A trigger per input
makes a forgotten writer impossible, and the input map is tested against the
definition's tables.

### Persist live cover currentness

It changes without a database write, so a stored copy could disagree with the
recomputed answer.

### Cache attention in process memory

A cache would need invalidation on every input write and would still pay the
predicate on each miss.

### Declare a clean rebuild

ADR-0013 permits adoption because only derived objects are added, and the owner
preferred it over discarding retained state.
