# ADR-0068: Closed Latin combining-mark work identity

- Status: accepted
- Date: 2026-09-03
- Owners: project maintainers

Amends [ADR-0018](0018-closed-title-creator-comparison.md).

## Decision

The existing final WORK rung may apply `latin_combining_marks`: NFD, remove
marks only from clusters headed by a Unicode `LATIN ` letter, then NFC. Orphan
marks and non-Latin letters make that field inapplicable. Compatibility
decomposition, transliteration, fuzzy matching, and substitutions including ø,
ł, æ, and ß remain excluded.

Admission requires title and complete ordered authors to become equal solely
through this family. Its canonical lineage records every changed side, field,
and author ordinal; dispatch lineage records only the request sent. All vetoes
and ambiguity rejection remain. `metadata-report-v37` requires a clean rebuild
under ADR-0013; v36 is not accepted at ordinary startup.
