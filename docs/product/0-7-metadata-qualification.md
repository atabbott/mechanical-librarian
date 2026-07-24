# 0.7.0 metadata qualification scope

## Decision

For the 0.7.0 qualified ebook metadata-matching checkpoint, **edition** and
**rights** are independently outside the supported public `MetadataReport`
claim vocabulary. This is the decision for each field; neither is a supported
field merely because an EPUB package or opening-matter extractor can observe
related text.

Edition identity and edition applicability remain evidence context for the
supported fields that need them, especially identifiers. They do not create an
edition-valued report claim. Rights observations likewise remain outside the
report projection. A report, its persisted document, provider comparisons, and
field-result denominator must not contain either field. No compatibility,
schema, migration, registry, provider, proposal, review, apply, or write seam
is introduced by this decision.

## Qualification contract

The private evaluator has two explicitly named views:

- The **supported regression view** derives its evaluated-field set from the
  closed public report vocabulary. It excludes edition and rights before
  scoring rows, candidate atoms, per-field metrics, aggregate metrics, and
  regression thresholds.
- The **exploratory view** may retain those two fields to guide a later
  promotion decision. It is labelled exploratory, has no regression threshold,
  and cannot make the supported report appear to support either field.

This is a contract correction, not a silent threshold reduction. The evaluator
must emit both view names, the evaluated fields for each view, and their
denominators. A changed supported-field set invalidates the prior supported
baseline and requires a newly recorded aggregate-only baseline and thresholds;
it must not reuse the previous all-fields threshold file.

At the frozen private aggregate used for this decision, removing edition and
rights changes the supported value-bearing denominator from 499 to 405
(a removal of 94 cells: 47 edition and 47 rights). Those fields had 51
candidate-bearing cells and 12 matches (three edition and nine rights).
The supported aggregate therefore changes from 269 to 257 matches. The
recomputed supported-only aggregate is 64.7355% readable any-match,
57.6826% readable complete-match, 80.1775% candidate-atom precision, and
8.5366% explicit-negative false-positive rate. These are aggregate-only
figures: no row, title, identifier, path, excerpt, digest, credential, or
private report belongs in this repository.

The next private evaluator run must:

1. replace its hard-coded all-fields evaluation set with the closed supported
   vocabulary for the regression view;
2. exclude both fields consistently from every supported numerator,
   denominator, candidate-precision calculation, per-field result, and gate;
3. retain a separately labelled exploratory aggregate with no supported gate;
4. record fresh supported-only thresholds from the recomputed baseline,
   including the existing title, creator, language, identifier, precision, and
   explicit-negative controls; and
5. fail if a purported supported result includes either field or if the
   reported field set and denominators disagree.

Synthetic fixtures must prove both exclusions with positive, negative, and
ambiguous edition/rights observations. They must also prove that supported
field scoring, precision, and thresholds do not change when only those
exploratory observations change. The public regression suite separately proves
that neither name can enter the closed report claim vocabulary.

## Privacy and schema consequence

Private gold labels and evaluator outputs remain qualification inputs only.
They are never extractor inputs and remain ignored local artifacts. Public
documentation may publish only aggregate counts and rates needed to explain a
decision. In particular, it must not reproduce corpus rows or identifiers.

Because neither field enters the persisted report contract, the pre-release
clean-schema rule requires no schema change, migration, conversion, import, or
compatibility behavior. Existing reports remain valid only under their exact
current schema; this decision adds no retired-field reader or writer.

## Future promotion

The exploratory aggregate does not itself justify implementation. A separately
promoted outcome may support one field only after it defines a closed typed
claim, field-specific normalization and bounds, provenance, applicability and
false-positive controls, report persistence consequences, and the synthetic
and aggregate qualification required to restore trustworthy matches without
reducing supported precision. It must be planned and accepted independently;
this 0.7.0 decision neither creates nor authorizes that outcome.
