"""One renderer from provider markup to the visible text a claim may carry.

Three providers publish a description as HTML.  Rendering it to visible text is
one behaviour, not three: the tag policy, the hostile-markup refusal and the
paragraph boundary decide what a `DESCRIPTION` claim is allowed to say, and a
second copy of that policy would be a second answer to the same question.

The renderer refuses rather than guesses.  Unbalanced or unterminated markup,
a rejected element, an inline event handler, and an empty rendering all return
`None`, because a description that cannot be read safely is not a description
the report may state.  An over-bound rendering returns `DESCRIPTION_OVERSIZED`
so the caller records what the provider said without projecting it.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from mechanical_librarian.domain.metadata_report import MAX_DESCRIPTION_CHARACTERS

# The rendering this module performs, named so a candidate can record that its
# description was transformed rather than stated.
HTML_VISIBLE_TEXT_VERSION = "html-visible-text-v1"

# One rendering that is faithful but longer than a claim may carry.  It is not
# an absence and not a refusal, so it is its own sentinel.
DESCRIPTION_OVERSIZED = object()


class _DescriptionParser(HTMLParser):
    _BLOCKS = frozenset(
        {"p", "div", "br", "hr", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
    )
    _REJECT = frozenset(
        {"script", "style", "iframe", "object", "embed", "svg", "math", "template"}
    )
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.safe = True
        self.open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._REJECT or any(
            name.casefold().startswith("on") for name, _ in attrs
        ):
            self.safe = False
        if tag in self._BLOCKS:
            self.parts.append("\n")
        if tag not in self._VOID:
            self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._REJECT:
            self.safe = False
        if not self.open_tags or self.open_tags.pop() != tag:
            self.safe = False
        if tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def visible_description(value: object) -> object:
    """Render provider markup to visible text, or refuse to read it at all.

    A value carrying no angle bracket is returned unchanged, so a provider that
    states plain text is never routed through a parser.
    """
    if type(value) is not str or ("<" not in value and ">" not in value):
        return value
    if len(value) > MAX_DESCRIPTION_CHARACTERS:
        return DESCRIPTION_OVERSIZED
    position = 0
    while (start := value.find("<", position)) >= 0:
        quote: str | None = None
        end = start + 1
        while end < len(value):
            character = value[end]
            if quote is not None:
                if character == quote:
                    quote = None
            elif character in {'"', "'"}:
                quote = character
            elif character == "<":
                return None
            elif character == ">":
                break
            end += 1
        if end >= len(value) or quote is not None:
            return None
        position = end + 1
    parser = _DescriptionParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, OverflowError):
        return None
    # Claims intentionally exclude control characters.  A visible bullet is a
    # stable plain-text boundary for each source paragraph/list item.
    rendered = " • ".join(
        " ".join(line.split())
        for line in "".join(parser.parts).splitlines()
        if line.split()
    )
    if len(rendered) > MAX_DESCRIPTION_CHARACTERS:
        return DESCRIPTION_OVERSIZED
    return rendered if parser.safe and not parser.open_tags and rendered else None


# The character references `html.unescape` reads, so each is decoded on its own.
_CHARACTER_REFERENCE = re.compile(
    r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)"
)


def _decoded_reference(match: re.Match[str]) -> str:
    # A reference to a no-break or other space reads as the space it shows, as
    # rendered markup's whitespace does; a line feed stays a line.
    return "".join(
        " " if character.isspace() and character != "\n" else character
        for character in html.unescape(match.group(0))
    )


def decoded_description(value: str) -> str | None:
    """Decode the character references in one plain-text description, once.

    Plain text has no markup to render, but `Fish &amp; chips` still shows as
    `Fish & chips` (#1424). Its lines and other characters are kept exactly.
    Text over the description bound is never read, and it and text that shows
    nothing once decoded are `None`, so the caller keeps them as stated.
    """
    if len(value) > MAX_DESCRIPTION_CHARACTERS:
        return None
    decoded = _CHARACTER_REFERENCE.sub(_decoded_reference, value)
    return decoded if decoded.strip() else None


def carries_markup(value: object) -> bool:
    """Whether one provider value states markup this renderer would read."""
    return type(value) is str and ("<" in value or ">" in value)


# One value a provider stated that the renderer would not read.  It is not an
# absence: an adapter's field projection maps it to `MALFORMED`, so the report
# still records that the provider said something here.
UNPROJECTABLE_DESCRIPTION = object()


def projected_description(
    *values: object, strict_primary: bool = False
) -> tuple[object, bool]:
    """Project the first stated description, and say whether it was rendered.

    The returned value is what an adapter hands its own field projection: a
    plain string, an over-bound string that projection will call oversized, the
    unprojectable sentinel it will call malformed, or ``None`` for an absence.
    The flag is true only when markup was actually rendered to visible text,
    which is exactly when a candidate should record the transformation.
    """
    for value in values:
        if strict_primary and (
            value is None or type(value) is str and not value.strip()
        ):
            continue
        if type(value) is not str:
            if strict_primary:
                return UNPROJECTABLE_DESCRIPTION, False
            continue
        if not carries_markup(value):
            return decoded_description(value) or value, False
        rendered = visible_description(value)
        if type(rendered) is str:
            return rendered, True
        if rendered is DESCRIPTION_OVERSIZED:
            return "x" * (MAX_DESCRIPTION_CHARACTERS + 1), False
        return UNPROJECTABLE_DESCRIPTION, False
    return None, False


__all__ = [
    "DESCRIPTION_OVERSIZED",
    "HTML_VISIBLE_TEXT_VERSION",
    "UNPROJECTABLE_DESCRIPTION",
    "carries_markup",
    "decoded_description",
    "projected_description",
    "visible_description",
]
