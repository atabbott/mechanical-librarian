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
and region is omitted. The bounded observed name `English` (case-insensitive)
shares the `en` class. `UND`/`und` means undetermined and never supplies a
positive language choice or title-variant vote; `mul` and `zxx` remain distinct. Therefore `en`, `eng`, `en-US`, and `en-GB`
share one class; `zh`, `zh-Hans`, and `zh-Hant` are three classes.

Proposal evaluation groups valid language observations by that class before it
creates alternatives. One deterministic admitted representation remains the
candidate value, while every grouped evidence reference and correlation is
retained. The immutable MetadataReport remains the provenance authority for
every original representation; grouping neither rewrites nor deletes evidence.
The active `language_rendering` option remains bound to the decision and
effect audit. An equivalent populated incumbent remains byte-for-byte unchanged.
When filling missing or undetermined knowledge, `display_name` prefers an
observed English name and `preserve` prefers an observed code, with deterministic
fallback to another form admitted by that same alternative. Neither option
synthesizes a value or borrows a spelling from unrelated evidence.

Different bases, different scripts, absent-versus-specified scripts, malformed
tags, and noncanonical tags remain conflicts. Publisher behavior and ordinary
field equality do not change. Language equality used by proposal assessment,
no-change terminalization, and guarded effect validation uses the same class
contract.

The #1071 proposal policy is `fixed-conservative-v29`, with report v48, action
policy v5, decision policy v16 and shadow outcome v8. Under
ADR-0013, earlier pre-release proposal and downstream decision state requires a
clean rebuild; no migration, import, repair, or conversion is supplied.

### Canonical display names

Amended by #1314, because one language was written in three spellings
(`english`, `en`, `eng`) depending on which provider was elected, and the
owner decided on 2026-10-21 that every written language is one canonical
display name. This amendment replaces the rule above that neither rendering
option synthesizes a value.

1. A fixed table (`domain/language_names.py`) names every supported favored
   base, and a class with a script is named "Base (Script)", as in "Chinese
   (Simplified)". Region is not part of a class, so it is not part of a name.
   Names come from the CLDR English names the review frontend already shows,
   with the ISO 639-2 or ISO 15924 English name where CLDR has none or where
   two bases would share a name. No two classes share a name, and no name
   reads as another base's code. A class whose script has no English name in
   either source has no display name and keeps the form its evidence
   admitted.
2. Each name, in any case, is accepted as a representation of its class,
   replacing the single bounded name `English`. A tag is read before a name, so
   `ga` stays Irish while `Ga language` is Ga. Classes do not change.
3. A language alternative states its class's display name. Fills and
   replacements therefore write that name, from either an owner's choice or
   Automatic, and the immutable MetadataReport still holds every original
   representation.
4. Only a current value spelled exactly as that name is preserved. A current
   value of the same class spelled any other way, such as `en`, `eng`, `en-GB`
   or `english`, becomes a `language_display_name` proposal that offers the
   name. Automatic decides it as it decides a fill (see the #1496 amendment
   below).
5. The `preserve` rendering option is retired; `display_name` is the only
   `language_rendering` option, so a profile or override that names
   `preserve` is refused.

### Automatic respelling

Amended by #1496, because 14 respellings on one retained library each became
an owner question that changed only how a language is written, and the owner
reversed rule 4's manual decision on 2026-10-31.

1. A `language_display_name` proposal is an ordinary automatic candidate: the
   decision policy gives it the same eligible action as a fill, on both media.
   The F46 `language_rendering:display_name:audiobook_language_normalize`
   operation is permitted again, and writing the display name of a current
   value's own class is that operation even when the current tag carries a
   region. `replace` stays unpermitted, so a different language is never
   written automatically.
2. Beside an alternative of another language, an alternative of the current
   value's class only respells it, so it is dropped and the owner chooses
   between the current value and the other language.
3. Base or script disagreements stay manual, region is not written, and no
   language is inferred.

The proposal policy moves to `fixed-conservative-v42` and the decision policy
to `deterministic-shadow-decision-policy-v26`; under ADR-0013 earlier stores
need a clean rebuild.

The #1314 proposal policy moved to `fixed-conservative-v41` and the decision policy
to `deterministic-shadow-decision-policy-v25`. A policy profile keeps its
document shape, so it is not relabelled; one naming `preserve` is refused as an
unavailable option. Under ADR-0013 earlier stores need a clean rebuild; no
adoption is supplied.

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
- The #1314 amendment synthesizes display names from a fixed table, and that
  table needs deliberate maintenance alongside the alias registry.
- Cross-language and script conflicts remain owner decisions.

## Alternatives considered

### Preserve one alternative per representation

Rejected because representation variance would continue to defeat the
demonstrable outcome and automatic unique-candidate qualification.

### Strip script as well as region

Rejected because script can materially distinguish the written language and
the settled product contract requires those decisions to survive.

### Store a synthesized display name

Rejected at first because no admitted evidence supplied that value. The #1314
amendment adopts it: the owner chose one spelling per language over observed
bytes, and the report keeps the observed bytes as provenance.

### Keep `preserve` beside the display name

Rejected in the #1314 amendment. Its only remaining effect would be to write a
code where the display name writes a name, which brings back the several
spellings the amendment removes.

## Validation

Unit tests cover ISO aliases, regional collapse, script and malformed controls,
canonical ProposalSet replay, merged evidence lineage, shared field equality,
and downstream decision behavior. A frozen real-library corpus uses disjoint
confirmation and one-shot holdout splits, two blinded reviews, aggregate-only
publication, at least three automatic predictions, 100% automatic precision,
and zero safety failures.
