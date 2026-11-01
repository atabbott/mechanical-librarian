# ADR-0090: Hardcover audio editions witness which recording an audiobook is

- Status: accepted (scope, same-narrator rule and independence decided by the
  owner on 2026-10-28 and 2026-10-31)
- Date: 2026-10-31
- Owners: project maintainers
- Issues: #962
- Amends: ADR-0061 clause 9 (Hardcover reads audio editions for audiobooks)
- Supersedes: none
- Superseded by: none

## Context

ADR-0061 clause 9 admits Hardcover for audiobook work facts only. Hardcover
also lists audio-format editions with a runtime in seconds, narrator
contributions and sometimes an ASIN.

Owner-approved probes measured what those editions add, using aggregates only:

- 115 of 144 audiobooks with no accepted recording were sampled. A Hardcover
  edition named the recording for 10 of them: a narrator and a runtime within
  90 s of the file. That is 8.7%, with a Wilson interval of 4.8% to 15.3%.
- The editions gave 5 ASIN leads that no retained Audible or Audnexus
  candidate held. The probe cost about 2 requests per audiobook.
- A re-cut covered 42 audiobooks that had both an Audible candidate and a
  Hardcover work, and judged each source against the file's own duration.
  - Hardcover matched 25 and Audible's candidate matched 21.
  - 5 of the Audible misses were a different recording: another narrator,
    another language or another cast. Each was a lone product that passed exact
    title admission.
  - A Hardcover edition within 90 s with a narrator named the file's recording
    in all 5.

A shared ASIN or runtime is a fact of the edition itself, not a sign that a
record was copied. Many books, and many editions, are exclusive to Audible,
though. So everything Hardcover says about such an edition can only have come
from Audible.

## Decision

1. **Role.** A Hardcover audio edition is a witness to which recording the
   file is. It is compared with the file, never with Audible. It never becomes
   recording authority, never enters the recording-authority registry and
   never replaces a value on its own.
2. **Independence.** A Hardcover edition never counts as a second independent
   source when a field is corroborated. Agreement between an edition and
   Audible is, at most, Audible repeated.
3. **Which items.** Every audiobook WORK lane whose qualified answer admits one
   or two Hardcover books asks for those books' audio editions, whether or not
   a recording was accepted. The item must state one local running time. An
   ebook, an identifier lane, or an answer of more than two books sends no
   edition request.
4. **Fetch.** One constant GraphQL document is keyed by the book id as an
   `Int`. It reads editions with `reading_format_id` 2, in id order, 100 rows a
   page, at most 2 pages per book. Every page stays inside the unchanged
   attempt, deadline, byte and pacer ceilings, and each page is one ledger
   detail request. The projection is closed: edition id, book id, ASIN, audio
   seconds and contributions. The fan-out is refused, keeping no edition, when:
   - a row names another book;
   - an edition id repeats;
   - a page is malformed or fails, or both pages of either book are full.

   The admitted work and its facts stand either way. A failed read never states
   that no edition exists.
5. **When an edition counts.** An edition is kept and counted only when it
   credits at least one narrator and its runtime is within
   `RUNTIME_BAND_TIGHT_SECONDS` (90 s) of the file's local runtime. A kept
   edition states only its ASIN, its runtime and its narrators. An observation
   keeps at most `MAX_AUDIO_EDITIONS` (16). An answer over the bound keeps none,
   because no prefix of it is a fair sample.
6. **What counted editions say** (`hardcover_edition_witness`):
   - **`no_edition`:** no edition counted.
   - **`editions_disagree`:** two counted editions credit disjoint narrator
     casts, so nothing is used.
   - **`unmatched`:** the counted editions agree, and no audiobook authority
     put a recording forward.
   - **`agrees`:** an accepted recording is inside the tight band after
     whole-minute floors, or shares a narrator with the counted editions. The
     same narrator at a nearby runtime is the same recording with an offset in
     the stated time. A recording that states no runtime or no narrator is
     never challenged.
   - **`challenged`:** every accepted recording states a runtime outside the
     band and credits only narrators the counted editions do not.
7. **Record.** The kept editions are persisted per provider as
   `audio_editions` in `metadata-report-v64`. They are never read for admission
   or as field evidence. The cold-start report counts each witness outcome per
   library.
8. **Leads.** When the witness is `challenged` or `unmatched`, the counted
   editions' ASINs (`hardcover_edition_leads`) join the phase-two ASIN feed,
   after carried, promoted and asserted ASINs and before the ASINs of products
   the search returned and the recording-lookup hints, inside the unchanged
   cap of four lanes. A lead obtains evidence only: the identifier-lane gate
   admits the product only on an exact work match inside the tight runtime
   band, and the owner reviews what it proposes, as for any admitted recording. The
   challenged match itself already proposes nothing, because the runtime veto
   keeps it out. An edition ASIN may be ISBN-shaped, as some Audible
   productions are; the work-level Hardcover ASIN rule is unchanged.

## Consequences

### Positive

- The report states, for every audiobook, whether an independent catalogue's
  edition names a different recording from the accepted match.
- About 2 more requests per audiobook, inside existing ceilings, buy a witness
  that the re-cut found right in all 5 wrong-recording cases.

### Negative

- Every audiobook spends one to four more Hardcover requests. A
  deadline-tight observation can end the fan-out early, and then the item has
  no witness.
- Narrator names come from a community catalogue. A wrong one can only
  suppress or raise a challenge, never write a value.

### Neutral or deferred

- A counted edition with no ASIN gives no lead and fills nothing. Under
  decision 2, its narrator would be Audible's word repeated, so a recording
  is only offered once Audible confirms one. The owner's current ASIN is
  never withheld.
- `metadata-report-v64` is a clean rebuild under ADR-0013. Decision 8 adds no
  persisted field.

## Alternatives considered

### Keep Hardcover work-only

Leaves about 8.7% of recording-less audiobooks, and every wrong Audible
recording, without evidence that is available and cheap.

### Gap filling only

This was the first draft. The owner rejected it on 2026-10-28, because it
leaves wrong Audible matches in place even when Hardcover names the right
recording.

### Challenge on runtime alone

Rejected by the owner on 2026-10-31. Two narrators rarely record one book at
nearly the same length, so a same-narrator match a few minutes out is Audible's
offset, not another recording.

### Fill from the edition closest in runtime when editions disagree

Rejected: the disagreement stays visible instead of a guess.

### Replace a contradicted match automatically

Rejected: a change of recording goes through a verified lookup and the owner.

### Match the edition's runtime against Audible's

Rejected: it would measure Audible against itself.

## Validation

- `tests/unit/test_hardcover_audio_editions.py` covers:
  - the fetch, its request shape and its paging;
  - the band and narrator filter;
  - no request for an ebook;
  - a failed page and a foreign row, each keeping the admitted work with no
    edition.
- `tests/unit/test_hardcover_edition_witness.py` covers every witness outcome,
  including the same-narrator and minute-floor agreements, the edition
  provenance bounds and the report round trip.
- `tests/unit/test_hardcover_asin_leads.py` covers which witness outcomes
  give leads, their place in the feed and the cap, and end to end that a lead
  Audible confirms is proposed while one outside the band proposes nothing.
- Before release, the cold-start report on a real library gives the measured
  witness counts. The re-cut expects about 5 challenges in 42 checked
  audiobooks.
