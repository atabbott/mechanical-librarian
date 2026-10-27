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

Every plain-text description is read once, through `shown_description`: the
incumbent's, each provider's and each local file's (#1424). Its character
references are decoded once. Text that carries a tag once decoded is markup,
held to the same renderer and its refusals as stated markup, and a reading that
would still carry a tag or decode again is refused rather than shown. Markup a
source states without recording a rendering is kept as stated; see
`stated_description`.
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


# A tag as HTML opens one: `<` and then a letter, `/`, `!` or `?`.  Any other
# `<`, as in `2 < 3`, is text.
_TAG = re.compile(r"<[A-Za-z/!?]")


def shown_description(value: str) -> tuple[object, bool]:
    """Read one description as the text it shows, and say if it was rendered.

    Stated markup, and plain text that carries a tag once its references are
    decoded, are rendered; anything else is decoded once. The reading is the
    text, `DESCRIPTION_OVERSIZED` over the bound, or `None` where the renderer
    refuses or the text would still carry a tag or decode again, so no
    reading ever needs a second one. Text that shows nothing once decoded is
    returned as stated, for its caller to judge.
    """
    if len(value) > MAX_DESCRIPTION_CHARACTERS:
        return DESCRIPTION_OVERSIZED, False
    text = value if carries_markup(value) else decoded_description(value)
    if text is None:
        return value, False
    rendered = carries_markup(value) or _TAG.search(text) is not None
    if rendered:
        shown = visible_description(text)
        if type(shown) is not str:
            return shown, True
        text = shown
    final = _TAG.search(text) is None and decoded_description(text) == text
    return (text if final else None), rendered


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
        shown, rendered = shown_description(value)
        if type(shown) is str:
            return shown, rendered
        if shown is DESCRIPTION_OVERSIZED:
            return "x" * (MAX_DESCRIPTION_CHARACTERS + 1), False
        return UNPROJECTABLE_DESCRIPTION, False
    return None, False


def stated_description(value: object) -> object:
    """Project one description from a source that records no rendering.

    Markup the source states is kept as stated, as before #1424. The field
    projection refuses some of it, but slash-free markup such as
    `<img src=x onerror=alert(1)>Hi` remains a valid claim; the guard is the
    proposal policy's `_eligible_description`, which makes any description
    carrying `<` or `>` ineligible, so it never reaches a write. Plain text is
    read through `shown_description`; text that would need rendering once
    decoded is unprojectable, because this source has no transformation to
    record it with.
    """
    if type(value) is not str or carries_markup(value):
        return value
    projected, rendered = projected_description(value)
    return UNPROJECTABLE_DESCRIPTION if rendered else projected


__all__ = [
    "DESCRIPTION_OVERSIZED",
    "HTML_VISIBLE_TEXT_VERSION",
    "UNPROJECTABLE_DESCRIPTION",
    "carries_markup",
    "decoded_description",
    "projected_description",
    "shown_description",
    "stated_description",
    "visible_description",
]
