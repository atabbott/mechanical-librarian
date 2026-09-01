# ADR-0065: Group language alternatives by equivalence class

- Status: accepted
- Date: 2026-09-01
- Owners: project maintainers
- Supersedes: ADR-0020 for language alternative grouping and regional equivalence
- Superseded by: none

## Context

ADR-0020 kept every admitted language representation as a separate proposal
alternative. That preserved evidence exactly, but made `en`, `eng`, and
`en-US` look like three owner decisions and prevented unique-candidate
automatic policy from operating on the language they all name.

The 2026-08-25 retained-library run found eight attention fields in this class,
across five works. The volume is small, but the false choices arise from a
stable semantic distinction: representation and region are not separate
language outcomes, while script and base-language differences are.

## Decision

The language decision equivalence class is the favored ISO 639 base plus its
exact optional script. ISO aliases map to the favored base before grouping,
and region is omitted. Therefore `en`, `eng`, `en-US`, and `en-GB`
share one class; `zh`, `zh-Hans`, and `zh-Hant` are three classes.

Proposal evaluation groups valid language observations by that class before it
creates alternatives. One deterministic admitted representation remains the
candidate value, while every grouped evidence reference and correlation is
retained. The immutable MetadataReport remains the provenance authority for
every original representation; grouping neither rewrites nor deletes evidence.
The active `language_rendering` option remains bound to the decision and
effect audit, but does not synthesize a language name or a value absent from
the evidence.

Different bases, different scripts, absent-versus-specified scripts, malformed
tags, and noncanonical tags remain conflicts. Publisher behavior and ordinary
field equality do not change. Language equality used by proposal assessment,
no-change terminalization, and guarded effect validation uses the same class
contract.

The exact proposal policy advances to `fixed-conservative-v13`. Under
ADR-0013, earlier pre-release proposal and downstream decision state requires a
clean rebuild; no migration, import, repair, or conversion is supplied.

## Consequences

### Positive

- Representation and regional variance no longer creates false owner choices.
- One class becomes one deterministic automatic candidate or preservation
  outcome without losing source provenance.
- Proposal grouping, target comparison, and no-change validation share one
  exact language contract.

### Negative

- A representative value is selected deterministically even when several raw
  forms supplied the same evidence class.
- The closed ISO alias, script, and region registries still require deliberate
  maintenance.
- Existing pre-release proposal state must be rebuilt.

### Neutral or deferred

- No language is inferred from content and no policy option is added.
- Human-readable language-name synthesis remains outside this decision.
- Cross-language and script conflicts remain owner decisions.

## Alternatives considered

### Preserve one alternative per representation

Rejected because representation variance would continue to defeat the
demonstrable outcome and automatic unique-candidate qualification.

### Strip script as well as region

Rejected because script can materially distinguish the written language and
the settled product contract requires those decisions to survive.

### Store a synthesized display name

Rejected because no admitted evidence supplied that value and the existing
policy records rendering intent without inventing candidate bytes.

## Validation

Unit tests cover ISO aliases, regional collapse, script and malformed controls,
canonical ProposalSet replay, merged evidence lineage, shared field equality,
and downstream decision behavior. A frozen real-library corpus uses disjoint
confirmation and one-shot holdout splits, two blinded reviews, aggregate-only
publication, at least three automatic predictions, 100% automatic precision,
and zero safety failures.
