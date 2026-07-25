# 0.7.0 metadata qualification scope

## Status

The 0.7.0 checkpoint is the completed qualified ebook metadata-matching
programme. It did not add a provider or widen the report-only product boundary.
The resulting delivery units are:

| Unit | Resulting state | Delivered outcome |
| --- | --- | --- |
| #319 | Done | Fixed the supported qualification vocabulary and excluded edition and rights from the supported regression view. |
| #320 | Done | Classified remaining aggregate evidence gaps by recoverability without creating implementation tickets from every mismatch. |
| #321 | Done | Preserved validated package and opening evidence when the optional rear pass exhausts its resource budget. |
| #324 | Done | Preserved bounded provider-local work identity independently from edition identity. |
| F09n #328 | Done | Classified retained ISBN evidence as accepted, associated, unrelated, invalid, or unresolved without discarding claims. |
| F07c #322 | Done | Excluded package `bkp` production credits from bibliographic contributors and admitted one strictly bounded paragraph series-position cue. |
| F06a #323 | Done | Admitted standards-valid signed-offset publication date-times with event-specific applicability. |
| F09m #327 | Done | Preserved explicit publisher and imprint roles separately without inferring a hierarchy. |
| #325 | Done at reconciliation merge | Reconciled the completed 0.7.0 checkpoint and independent 0.8.0 provider plan. |

The final installed-wheel aggregate retained 49 reports from the 50-case
qualification cohort, with the known malformed EPUB remaining an expected item
failure. Relative to the accepted F09n baseline, matched rows increased from
258 to 266, complete rows from 230 to 238, candidate-atom precision from
0.805233 to 0.819484, and explicit-negative false positives fell from 7 to 0.
Title, creator, language, identifier, supported population, and the report-only
safety boundary did not regress. These figures are aggregate qualification
results, not release promises or private corpus artifacts.

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

The delivered private evaluator now:

1. replaces its hard-coded all-fields evaluation set with the closed supported
   vocabulary for the regression view;
2. excludes both fields consistently from every supported numerator,
   denominator, candidate-precision calculation, per-field result, and gate;
3. retains a separately labelled exploratory aggregate with no supported gate;
4. records fresh supported-only thresholds from the recomputed baseline,
   including the existing title, creator, language, identifier, precision, and
   explicit-negative controls; and
5. fails if a purported supported result includes either field or if the
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
