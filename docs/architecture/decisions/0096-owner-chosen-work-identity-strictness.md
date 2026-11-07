# ADR-0096: Let the owner choose how strictly title and creator agreement establishes work identity

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1355
- Amends: ADR-0059 (which candidates are identity-confirmed for covers),
  ADR-0093 (the eligible set and its order); adopts ADR-0092's would-admit
  note unchanged
- Supersedes: none
- Superseded by: none

## Context

A provider record informs a work-level field only once it is bound to the
work. Today the binding depends on how the record was found and on the field:

- A non-cover work field admits a record from a title and author search
  through that lane's closed title and creator set admission (ADR-0018,
  ADR-0067, ADR-0068).
- A record from an ISBN search needs an ISBN family match and exact title and
  creator agreement before it can supply series or a description.
- A cover candidate is verified only by an accepted or associated identifier,
  or by a retained recording admission. A candidate whose title merely agrees
  is classified as a plausible match and is not eligible.

On the retained full-library snapshot every blocked cover work had matched
some provider, but the records found by title and author were never bound to
the work for covers. In the 2026-10-25 evaluation sample, 4 of 17 description
fields needing attention were refused as not shown to describe the work, each
with one synopsis for the right book.

An offline replay on 2026-10-24 swapped the identity rule while running each
snapshot's own source; baseline replays reproduced the persisted outcomes
exactly (1,955 of 1,955 non-cover cases, 1,761 of 1,761 on the #744 ebook
library, 2,424 of 2,424 cover identities). Precision was measured on natural
controls: positives are records with accepted identifier evidence; negatives
are unrelated identifiers, contradictions or a different accepted work.

| Predicate | Coverage of positives | Precision | Precision, excluding same-book duplicate provider ids |
|---|---|---|---|
| Exact title and all creators | 0.407 | 0.955 | 0.985 |
| Decoration-grammar title and any creator | 0.510 | 0.957 | 0.984 |
| Today's work-lane predicate | 0.414 | 0.953 | 0.981 |

Different books by the same author matched on title in 2 of 15,103 pairs,
both library duplicates. Coverage is limited mainly by records that state no
creator. Audiobooks have no natural negatives, so their precision cannot be
measured this way.

On the full library's ebooks, the looser predicate gives 685 cover fields an
eligible same-work alternative, including 593 of 625 that had none, and gives
190 works an automatic series or description write that would need F20
re-measurement. Everything else, and every audiobook, is unchanged.

## Decision

### One setting with three levels

`review.work_identity` is one closed configuration field. Configuration
admission refuses any value but these three:

| Level | Admits for work-scope fields |
|---|---|
| `identifier` (default) | exactly what the product admits today, as described under Context |
| `title_and_creator_exact` | also a record whose title is equal under the closed title comparison and whose creator set equals the target's |
| `title_and_creator` | also a record whose title is equal under the decoration grammar of ADR-0067 and that shares at least one creator with the target |

The levels are monotone: each admits everything the level before it admits.
Choosing a level never removes an admission `identifier` makes, so the
shipped default changes nothing.

Each added rule is one closed domain predicate over already-admitted claims,
with no fuzzy distance and no stripping of diacritics. At every level it
refuses:

- a record that states no creator;
- an inverted creator form that only the query used (#1380);
- an omnibus or box set against one of its volumes;
- any retained identifier, edition, work or creator contradiction, which keeps
  precedence over title agreement.

### Scope

Only work-scope fields read the level: title, contributors, description,
genres, series, cover and the other work-level fields. Manifestation-scope
fields (ISBN, publisher, publication date, page count, narrator and ASIN)
never read it and keep their current identity rules; that is not configurable.
Both media read the level.

For covers, a candidate admitted only by the level's predicate gets its own
closed identity outcome, `title_creator_work_match`, recording the level and
rule. In ADR-0093's order it ranks after verified manifestation matches and
after identifier-verified work matches.

### Lineage

Every admission records the level and the closed rule that made it, as
ADR-0067 and ADR-0068 do for theirs, so review, Activity and replays can show
why a record counted.

### Shadow first

Until the owner-run qualification held by #1418 passes (owner labels on a
sample of the covers the predicates admit, the F20 re-measurement for the new
series and description writes, the cover election qualification re-run with
considerations reclassified, and the choice of default), an admission that
only a level's predicate makes reaches owner review, cover candidates and the
shadow record, and never an automatic decision, whatever level the owner
chooses. That holds for ADR-0093's deterministic switches and fills and for
ADR-0095's automatic mode: such a candidate may be proposed, never applied
automatically.

The shadow record is ADR-0092's would-admit note, adopted without change. It
names `work_identity:<level>` and the candidate, the decision keeps its manual
action and its abstention reason, and nothing the owner reads says "shadow".

### The setting

The setting has no environment or hidden source. Its store-managed equivalent
changes only through the session-, CSRF- and Origin-bound settings command
with an optimistic revision; a file-managed value is read-only in the
browser. A change makes pending work-scope decisions and cover effects stale,
and none executes under the old level.

The shipped default stays `identifier`. Moving it is the owner's choice from
#1418's measurements, in its own activation pull request. The 2026-10-24
measurement recommended `title_and_creator`; that is evidence for the choice,
not the choice.

### Re-qualification

Qualification records whose admission changes are rotated under ADR-0087's
displacement procedure, with their displaced digests kept readable and
without authority. The cover classifier reference rotates and considerations
are reclassified.

## Consequences

### Positive

- Evidence the product already retrieved can inform covers, series and
  descriptions, at a strictness the owner picks.
- The shipped default changes nothing, and nothing new decides automatically
  until owner labels qualify it.
- #1380's edition covers and #1481's sidecar check have one predicate to
  bind to instead of defining their own.

### Negative

- At either title-and-creator level some wrong-work records are admitted:
  about 4.5% of admitted records on the natural controls, two thirds of them same-book
  duplicate provider records. They reach owner review and proposals.
- Audiobook precision cannot be measured without owner labels.

### Neutral or deferred

- No new provider request, disclosure or model input: the predicates read
  evidence already retrieved. Public measurement is aggregate only.
- #1744's candidate rule, admitting the single alternative whose evidence is
  identity-bound when no competitor's is, is a value-choice relaxation under
  ADR-0092's bar, not an identity level. If it is taken up, it reuses this
  ADR's lineage and the would-admit note.
- Clean rebuild required under ADR-0013: the configuration schema moves to the
  successor of `config-v9`, and the report, proposal and shadow policy
  versions that bind identity change.
- On acceptance, `security.md` (› Authority, provider title comparison
  admission and the cover identity classification paragraph of › Cover
  observer), `configuration.md`, `automation-policy.md` and the changelog
  change in the activation pull request.

## Alternatives considered

### A literal identifier-only level as the default

The issue describes `identifier` as today's behaviour, and today non-cover
work fields already admit work-lane records by title and creator. A literal
identifier-only rule would remove those admissions on the default, which
nothing asks for.

### Ship `title_and_creator` as the default now

The natural controls cannot measure value correctness or audiobooks, and the
new series and description writes need F20 re-measurement. The default waits
for #1418.

### Fuzzy title matching

It would raise coverage at the cost of the closed comparison every existing
title rule relies on.

## Validation

- Domain tests for each level's predicate over synthetic controls: exact
  title with all creators; decoration-grammar title with any creator; a
  record that states no creator; an inverted creator name; a provider record
  that agrees on title with a different creator; a different book by the same
  author with the same title; an omnibus or box set against one of its
  volumes; and a retained contradiction beside title agreement. Monotonicity
  between levels is tested.
- Configuration admission refuses an unknown level, and the regenerated
  configuration schema and OpenAPI document match.
- An integration test shows a title-and-creator admission in owner review, as
  a cover candidate and in the shadow record, with no automatic decision at
  any level, including ADR-0093 switches and ADR-0095 automatic mode.
- The implementation re-derives the 2026-10-24 figures on the #744 ebook
  snapshot and reports any difference. An offline replay over the #744 and
  #1294 snapshots reports, per field and level, which automatic decisions
  would change value.
- The owner-run qualification held by #1418, whose owner-labelled sample
  counts omnibus and volume confusions separately.
