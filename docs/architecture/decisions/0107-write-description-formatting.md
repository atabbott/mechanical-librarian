# ADR-0107: Write a description's formatting from a closed set of tags

- Status: proposed
- Date: 2026-11-19
- Owners: project maintainers
- Issues: #1995
- Amends: ADR-0106 (decision 6, display only), ADR-0105 (`restore_unavailable`)
- Supersedes: none
- Superseded by: none

## Context

Provider descriptions usually arrive as HTML. Mechanical Librarian reads them
to visible text, joining paragraphs and list items with ` • ` and dropping
bold and italics, and writes that text to Audiobookshelf. A chosen
description therefore loses its paragraphs, lists and emphasis in the
library. ADR-0106 shows formatting in the browser, but only for markup that
reaches it unrendered, and keeps what is stored and written unchanged. A
"Before your change" keep over any formatted description is refused, because
writing its visible text back would flatten it.

## Decision

1. **One reading.** The description renderer that produces the visible text
   also produces a formatted copy from the same parse. The visible text is
   unchanged and stays the only value compared, proposed, pinned, planned as
   `desired_value`, pre-read and read back.
2. **Our own copy.** The copy is Mechanical Librarian's serialisation, never
   the input markup. Its tags are `p`, `br`, `ul`, `ol`, `li`, `b`, `strong`,
   `i`, `em` and `del`, with no attribute. `s` and `strike` become `del`;
   `div` and `h1` to `h6` become paragraphs when they hold no block. Every
   other tag, including links, is dropped with its words kept. Text is
   escaped, a line break in text becomes `br`, empty elements are dropped and
   nesting is valid and at most 32 deep.
3. **A copy exists only when it is exact.** It shows exactly the visible
   text, reads as itself again, also in the forms Audiobookshelf stores, fits
   the description bound without being cut and passes the description checks
   with its tags removed. Otherwise the description is plain text only.
4. **Stored.** A rendered provider description claim and the incumbent
   description in a report carry `formatted` (`metadata-report-v83`); a
   review case's target carries it (`review-case-v3`). A description plan
   carries `desired_form`, part of its identity (`field-effect-plan-v2`).
   Decisions, offers and pins stay plain.
5. **Which copy is written.** An accepted or automatic value writes the one
   copy its cited claims agree on; none, or two different copies, write plain
   text. The adviser's text is read by the same reader at write time: its
   visible text is the value and its copy the form; text the reader refuses
   can't be chosen.
6. **The write seam checks again.** The adapter sends the copy only when it
   reads as itself and shows exactly the plan's visible text. Otherwise
   nothing is sent, nothing is suspended, and the change closes as
   `not_sent`.
7. **Formatting alone never writes.** Equal visible text is no change, and a
   difference only in formatting is never offered.
8. **Before your change.** A keep writes back a formatted description whose
   stored markup is entirely in the set without attributes, comments or
   declarations, as its copy. `restore_unavailable` remains for a link, an
   attribute, any other tag or markup the renderer refuses.
9. **Shown.** Cards, advice and the Library summary show the copy in place of
   the text; advice without a copy shows the visible text that is written,
   never its own markup. Names, sentences and evidence keep the visible text.
10. Sources that record no rendering keep their description as stated, as
    before.

## Consequences

### Positive

- A chosen description reaches Audiobookshelf with its paragraphs, lists and
  emphasis, and the card shows what will be written.
- Before your change works for most formatted descriptions.

### Negative

- Reports, review cases and plans change shape: clean rebuild required.
- Read-back compares visible text, so a service that drops a formatting tag
  while keeping its words still verifies.
- A plan holds three description values, so a very long non-ASCII
  description reaches the plan size bound sooner.

### Neutral or deferred

- History and Activity still show visible text; a later change shows the
  copy there.
- A description already stored flattened stays flattened: formatting alone
  is never a reason to write.

## Alternatives considered

### Write the provider's markup

It would forward input markup to the library. Writing only our own
serialisation of a closed set keeps the written form ours.

### Amend ADR-0106

ADR-0106 is a browser display decision. This one changes stored documents,
the write path and restore, and can be accepted or rejected on its own.

### Write plain text and only display formatting

The card would show formatting the library never receives.

## Validation

`tests/unit/test_description_formatting.py` checks each copy rule over a
synthetic corpus and generated markup. `tests/unit/test_audiobookshelf_field_effect.py`
pins the written body, its read-back and a refused form that sends nothing.
`tests/integration/test_review_case_lifecycle.py` pins Before your change for
formatted and linked descriptions. Real-service landing rows for each tag are
maintainer-manual qualification; until they exist the evidence is
Audiobookshelf's sanitiser source, whose allowed tags include the whole set.
