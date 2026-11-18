# ADR-0106: Show a description's formatting from a closed set of tags

- Status: proposed
- Date: 2026-11-18
- Owners: project maintainers
- Issues: #TBD
- Amends: ADR-0043 (hostile metadata is rendered as text)
- Supersedes: none
- Superseded by: none

## Context

ADR-0043 renders hostile metadata as text. Three kinds of description still
reach the browser carrying markup: the description adviser's text, which the
owner can choose and which is written as it is, so Audiobookshelf shows its
formatting; a stored description the server's renderer will not read, which is
admitted as stored; and a description from a source that records no rendering.
The page showed their tags, so the owner read `<strong>` where Audiobookshelf
shows bold.

## Decision

1. The browser reads a description's markup into React elements from a closed
   set: `b` and `strong` as strong, `i` and `em` as emphasis, `u`, `del`, `s`
   and `strike` as deleted text, `p`, `div` and `h1` to `h6` as paragraphs,
   `br` and `hr` as breaks, and `ul`, `ol` and `li`. Every attribute is
   dropped. Any other tag is dropped and its words kept.
2. A description is shown as written, as before, when it holds a refused
   element, an event-handler attribute, a link, a comment, declaration or
   processing instruction, an unterminated tag or quote, markup that does not
   close as it opens, nesting deeper than 32 elements, no words, or more than
   16,384 characters. Audiobookshelf keeps links, so dropping one would hide
   from the owner a link they would be choosing to write.
3. Character references in formatted text are decoded once, from the HTML 4
   named set and numeric references. A reference to a code point the server
   refuses in a value stays as written, and an escaped tag stays text.
4. No description markup is handed to a browser parser: no `innerHTML`,
   `dangerouslySetInnerHTML` or `DOMParser`. Text cannot name an element or
   an attribute.
5. The elements form valid HTML: no block inside a paragraph or inline
   element, and list items only in lists.
6. This is display only. What is stored, compared, proposed and written is
   unchanged. Where a description sits in a sentence or a single line, its
   visible text is shown. Every other field stays text.

## Consequences

### Positive

- The owner reads a description as Audiobookshelf will show it, including
  the adviser's text before choosing it.

### Negative

- A second reader of description markup sits beside the server's renderer.
  It is display only, and it must keep the server's refusals.
- A formatted description always takes the long card layout, so its choice is
  named as a description rather than by its text.

### Neutral or deferred

- Descriptions the server already renders to visible text keep their
  paragraph joins. Showing their formatting would mean keeping markup in
  stored reports.
- Activity's change line is composed on the server and cut short, so it
  stays text. Activity's values, cut at 512 characters, are read with a
  trailing incomplete tag dropped and open elements closed.

## Alternatives considered

### Show any description with a tag outside the set as written

This hides nothing, but it would show tags again for common provider markup
such as `span` and `font`.

### Hand the markup to a sanitiser and `innerHTML`

This adds a dependency, and it gives untrusted markup to the browser's parser,
which ADR-0043 and the content-security policy avoid.

### Render on the server into a structured value

This changes stored reports and the API, and needs a clean rebuild.

## Validation

`frontend/tests/descriptionFormatting.test.tsx` pins the closed set, the
refusals, linear reading time and valid nesting. The review browser tests pin
formatted rendering and that hostile markup stays inert.
