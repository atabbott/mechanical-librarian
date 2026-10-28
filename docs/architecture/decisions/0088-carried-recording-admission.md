# ADR-0088: Carry an admitted audiobook edition into the next preparation

- Status: accepted (precedence clause pending owner confirmation)
- Date: 2026-10-28
- Owners: project maintainers
- Issues: #1316
- Amends: ADR-0061 (a new ASIN origin that only obtains evidence), ADR-0064
  (tight-band reuse and the narrator gate)
- Supersedes: none
- Superseded by: none

## Context

An audiobook item with no ASIN of its own reaches its edition through the
Audible title-and-author search, and the tight runtime band then admits one
product. Between #1086 and #1294, 14 items lost an admitted edition. #1316's
owner-approved probes settled why:

- The 25-product search page is a superset of the 6-product page on 9 of 9
  query pairs, so page size is not the cause.
- On 8 of 8 lost items the #1086 ASIN is no longer listed by search, which now
  returns a sibling edition, usually the only English one.
- A direct product lookup still resolves 13 of 14: runtime unchanged and inside
  the 90-second band on 13 of 13, narrator agreeing on 10, unknown on 3,
  disagreeing on 0. The 14th is an ASIN-only stub with no title or runtime.

The edition is still live; search no longer shows it. Separately, AudioSilo
states several regional or relisted ASINs for one recording, which made 25
items show the owner a spurious ASIN choice.

A written Audiobookshelf ASIN alone does not feed identifier evidence
(ADR-0061), so the admission has to be retained on this product's side.

## Decision

1. **What is carried.** `admitted_recording_asin` reads the current report for
   the item's exact track-set identity. A changed track set reads no report and
   carries nothing. It carries one ASIN only from an audiobook report with no
   ASIN on any of the item's own surfaces, and only when every product an
   audiobook authority admitted on a stated runtime inside the tight band, with
   edition marketplace evidence and a narrator cast not disjoint from the
   recording's own credits, names the same edition. A multi-ASIN statement
   counts as the ASIN its lane looked up. A chapter-only admission is not
   carried. An item whose surfaces now name an ASIN carries nothing.
2. **A new origin that only obtains evidence (amends ADR-0061).** The carried
   ASIN is recorded as a local claim of basis `prior_admission` (kind ASIN,
   edition scope), reported under its own owner-facing source,
   `prior_admission`. Like `target_metadata`, it is an asserted basis, not an
   identifier surface: `ASSERTED_ASIN_BASES` names both, and every site that
   decides what counts as the item's own ASIN, or what may be offered as a
   local value, reads that one constant. The claim never settles identity and
   is never offered to the owner as a value.
3. **Dispatch.** The Audible catalogue gets one carried product lane in phase
   one, beside its search, dispatched before every WORK lane so a request
   budget or deadline can only starve the search. Two three-rung ladders and
   the lookup are 7 requests, inside `MAX_PROVIDER_DISCOVERY_QUERIES` (9).
   Audnexus and AudioSilo get the carried ASIN as the first non-local origin of
   the phase-two feed: local, carried, promoted, asserted, hints, inside the
   four-lane cap. A provider retry rebuilds the carried lane from the source
   report's own claim.
4. **Verification (amends ADR-0064).** The carried lane is judged exactly as
   any ASIN the item's surfaces did not name: exact work admission, the
   variant conflict and the existing tight band, with no new constant. Only
   for a lane whose requested ASIN is the carried one, a product whose
   narrator cast is disjoint from the recording's own credits is refused;
   unknown on either side passes. An ASIN-only stub has no title to admit, so
   it is refused.
5. **Carrying again.** A carried ASIN that a lookup verified is carried again.
   One whose catalogue lookup ended in a recovery-eligible status or a
   disabled provider, or was never made, is carried again unverified. Any other
   answer refutes it: it is dropped, that report cannot admit it again, and the
   search may admit a different edition.
6. **Precedence (pending owner confirmation).** At the ASIN destination a
   verified carried edition is the ASIN proposed. A tight product naming
   another ASIN whose narrator cast is not disjoint from the carried product's
   is withheld as the same recording relisted; a narrator-disjoint one is still
   offered. Search still runs and still feeds every other field. The rule lives
   in one function, `carried_asin_precedence`, so the owner's decision can
   change it in one place.
7. **Sibling ASINs.** At the proposal layer only, with no admission change and
   no adapter change, a candidate stating more than one valid edition ASIN
   contributes one value at the ASIN destination: the target's current ASIN if
   it is one of them, otherwise the ASIN its lane looked up.
8. **Versions.** The report advances to `metadata-report-v60` and the proposal
   policy to `fixed-conservative-v37`. **Clean rebuild required** under
   ADR-0013.

## Consequences

### Positive

- The projected 13 items whose editions search no longer lists keep them, at
  one more catalogue request per carried item and one feed lane at each
  per-ASIN provider.
- One recording's regional ASINs no longer appear to the owner as a choice.

### Negative

- A carried edition the catalogue delists is only refuted when the lookup
  answers; until then a recoverable failure keeps carrying it unverified.
- An owner who prefers a newer sibling edition must choose it explicitly,
  because the carried one is what is proposed.

### Neutral or deferred

- The precedence clause stands as recommended until the owner confirms or
  replaces it on #1316.
- No runtime band changes, and nothing writes the Audiobookshelf ASIN outside
  the normal proposal path.

## Alternatives considered

### Comparing both search page sizes

The probe showed the larger page is a superset of the smaller, so this would
not recover any loss.

### Treating the written Audiobookshelf ASIN as the carried value

ADR-0061 keeps an assertion from feeding identity, and the owner may never
have applied the admitted edition. The carried value is what this product
admitted, under its own basis.

### Search precedence

Letting a tight search sibling compete with a verified carried edition re-opens
the choice every preparation, which is the regression #1316 records.

## Validation

`tests/unit/test_carried_recording_admission.py` proves what is carried and
what is not, the carried lane's plan, order, query bound and starvation, the
stub and narrator refusals, carrying again after a recoverable failure, the
precedence and the sibling rule. `tests/unit/test_metadata_report_application.py`
proves the carry across two preparations of one track set and its absence
after a track change or with a local ASIN, and
`tests/unit/test_provider_retry_successor.py` proves the retry rebuilds the
carried lane.
