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

The same parse also yields the description's formatted copy (#1995,
ADR-0107): Mechanical Librarian's own serialisation of what the markup shows,
from a closed set of tags and never an attribute. Input markup is never
forwarded. A copy exists only where it reads back as exactly the visible
text, reads as itself again, and passes the incumbent description checks;
otherwise the description is plain text only.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser

from mechanical_librarian.domain.evidence_bounds import (
    MAX_FORMATTED_DEPTH,
    EvidenceValueError,
    validate_formatted_description,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    ClaimKind,
    FieldState,
    ProviderClaim,
)

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
        # What the formatted copy is built from: each text run, and each tag
        # with whether it carried an attribute. A comment, declaration or
        # processing instruction shows nothing but is still markup a copy
        # does not keep.
        self.events: list[str | tuple[str, bool] | None] = []
        self.dropped = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._REJECT or any(
            name.casefold().startswith("on") for name, _ in attrs
        ):
            self.safe = False
        if tag in self._BLOCKS:
            self.parts.append("\n")
        self.events.append((tag, bool(attrs)))
        if tag not in self._VOID:
            self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._REJECT:
            self.safe = False
        if not self.open_tags or self.open_tags.pop() != tag:
            self.safe = False
        if tag in self._BLOCKS:
            self.parts.append("\n")
        self.events.append(None)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        self.events.append(data)

    def handle_comment(self, data: str) -> None:
        self.dropped = True

    def handle_decl(self, decl: str) -> None:
        self.dropped = True

    def handle_pi(self, data: str) -> None:
        self.dropped = True

    def unknown_decl(self, data: str) -> None:
        self.dropped = True


def visible_description(value: object) -> object:
    """Render provider markup to visible text, or refuse to read it at all.

    A value carrying no angle bracket is returned unchanged, so a provider that
    states plain text is never routed through a parser.
    """
    if type(value) is not str or ("<" not in value and ">" not in value):
        return value
    return _rendering(value)[0]


def _rendering(value: str) -> tuple[object, _DescriptionParser | None]:
    """Render one markup value, keeping the parse its formatted copy reads."""
    if len(value) > MAX_DESCRIPTION_CHARACTERS:
        return DESCRIPTION_OVERSIZED, None
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
                return None, None
            elif character == ">":
                break
            end += 1
        if end >= len(value) or quote is not None:
            return None, None
        position = end + 1
    parser = _DescriptionParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, OverflowError):
        return None, None
    # Claims intentionally exclude control characters.  A visible bullet is a
    # stable plain-text boundary for each source paragraph/list item.
    rendered = " • ".join(
        " ".join(line.split())
        for line in "".join(parser.parts).splitlines()
        if line.split()
    )
    if len(rendered) > MAX_DESCRIPTION_CHARACTERS:
        return DESCRIPTION_OVERSIZED, None
    if parser.safe and not parser.open_tags and rendered:
        return rendered, parser
    return None, None


# The closed set a formatted copy is written in (ADR-0107). Each kept tag
# keeps its name; strikethrough is one tag, and a division or heading holding
# no block is a paragraph. Every other tag is dropped with its words kept.
_FORMATTED_TAGS = {
    **{tag: tag for tag in ("p", "br", "ul", "ol", "li", "b", "strong", "i", "em")},
    "del": "del",
    "s": "del",
    "strike": "del",
    "div": "p",
    **{f"h{level}": "p" for level in range(1, 7)},
}
_INLINE_TAGS = frozenset({"b", "strong", "i", "em", "del"})
# Elements the renderer reads as a line boundary, other than a line break.
_BLOCK_ELEMENTS = _DescriptionParser._BLOCKS - {"br"}
_BLOCK_MARKUP = frozenset(
    f"<{close}{tag}>" for tag in ("p", "ul", "ol", "li") for close in ("", "/")
)
# The line breaks `str.splitlines` reads, which the renderer reads as one too.
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")
_SPACE_RUN = re.compile(r"\s+")
# A boundary the renderer reads that no kept tag carries: a line break in
# text, or a dropped block. It is written as `<br>` only between two runs of
# text on the same line.
_BOUNDARY = "\x00"


@dataclass(frozen=True, slots=True)
class _Element:
    tag: str
    attributes: bool
    children: list[_Element | str]


class _Unformattable(Exception):
    """Markup whose copy could not keep the closed set's nesting."""


class _Copy:
    """Serialise one parse into the closed set, noting anything it drops.

    Tokens are escaped text, tags of the set, and `_BOUNDARY`, which no
    escaped text can hold because the incumbent checks refuse NUL.
    """

    def __init__(self) -> None:
        self.lossless = True

    def tokens(self, nodes: list[_Element | str], context: str) -> list[str]:
        out: list[str] = []
        for node in nodes:
            if isinstance(node, str):
                self._text(node, context, out)
                continue
            tag = node.tag
            name = _FORMATTED_TAGS.get(tag)
            if node.attributes or name != tag:
                self.lossless = False
            if tag in _DescriptionParser._VOID:
                if context == "list":
                    self.lossless = False
                elif name == "br":
                    out.append("<br>")
                elif tag in _BLOCK_ELEMENTS:
                    out.append(_BOUNDARY)
            elif context == "list" and name == "li":
                out.extend(self._element("li", node.children, "flow", block=True))
            elif (
                name is not None
                and context != "list"
                and not _holds_block(node.children)
                and (name in _INLINE_TAGS or name == "p" and context == "flow")
            ):
                out.extend(
                    self._element(name, node.children, "inline", block=name == "p")
                )
            elif context == "flow" and name in {"ul", "ol"}:
                out.extend(self._element(tag, node.children, "list", block=True))
            else:
                # Kept as its words: a wrapper, or a tag the set can't nest here.
                self.lossless = False
                block = context != "list" and tag in _BLOCK_ELEMENTS
                out.extend(
                    [_BOUNDARY] * block
                    + self.tokens(node.children, context)
                    + [_BOUNDARY] * block
                )
        return out

    def _text(self, text: str, context: str, out: list[str]) -> None:
        if context == "list":
            if text.strip():
                raise _Unformattable
            return
        for index, line in enumerate(_LINE_BREAK.split(text)):
            if index:
                out.append(_BOUNDARY)
            if line:
                out.append(html.escape(_SPACE_RUN.sub(" ", line), quote=False))

    def _element(
        self, name: str, children: list[_Element | str], context: str, *, block: bool
    ) -> list[str]:
        inner = self.tokens(children, context)
        if any(_shows_text(token) for token in inner):
            return [f"<{name}>", *inner, f"</{name}>"]
        # An element showing no text is dropped, keeping the boundary or
        # space it stood for.
        self.lossless = False
        if block or any(token in {"<br>", _BOUNDARY} for token in inner):
            return [_BOUNDARY]
        return [" "] if inner else []


def _shows_text(token: str) -> bool:
    return not token.startswith("<") and token != _BOUNDARY and bool(token.strip())


def _holds_block(nodes: list[_Element | str]) -> bool:
    return any(
        not isinstance(node, str)
        and (node.tag in _BLOCK_ELEMENTS or _holds_block(node.children))
        for node in nodes
    )


def _tree(
    events: list[str | tuple[str, bool] | None],
) -> list[_Element | str] | None:
    root: list[_Element | str] = []
    stack = [root]
    for event in events:
        if event is None:
            stack.pop()
        elif isinstance(event, str):
            stack[-1].append(event)
        else:
            element = _Element(event[0], event[1], [])
            stack[-1].append(element)
            if element.tag not in _DescriptionParser._VOID:
                stack.append(element.children)
                if len(stack) > MAX_FORMATTED_DEPTH + 1:
                    return None
    return root


def _serialised(parser: _DescriptionParser) -> tuple[str | None, bool]:
    """One parse as its copy, and whether the copy kept every piece of markup."""
    tree = _tree(parser.events)
    if tree is None:
        return None, False
    copy = _Copy()
    try:
        tokens = copy.tokens(tree, "flow")
    except _Unformattable:
        return None, False
    following: list[str | None] = [None] * len(tokens)
    after: str | None = None
    for index in range(len(tokens) - 1, -1, -1):
        following[index] = after
        if tokens[index] != _BOUNDARY:
            after = tokens[index]
    written: list[str] = []
    for index, token in enumerate(tokens):
        if token != _BOUNDARY:
            written.append(token)
        elif (
            written
            and following[index] is not None
            and written[-1] not in _BLOCK_MARKUP | {"<br>"}
            and following[index] not in _BLOCK_MARKUP
        ):
            written.append("<br>")
    return "".join(written), copy.lossless and not parser.dropped


def _formatted_copy(parser: _DescriptionParser, shown: str) -> tuple[str | None, bool]:
    """The copy a rendering keeps, or `None` where it can't meet its rules.

    A copy reads back as exactly the visible text, reads as itself again, is
    within the description bound and passes the incumbent description checks
    with and without its tags. Any other copy is dropped, never cut.
    """
    copy, lossless = _serialised(parser)
    if copy is None:
        return None, False
    try:
        validate_formatted_description(
            copy, "formatted description", MAX_DESCRIPTION_CHARACTERS
        )
    except EvidenceValueError:
        return None, False
    shown_again, reparsed = _rendering(copy)
    if shown_again != shown or reparsed is None or _serialised(reparsed)[0] != copy:
        return None, False
    return copy, lossless


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
    shown, rendered, _, _ = read_description(value)
    return shown, rendered


def read_description(value: str) -> tuple[object, bool, str | None, bool]:
    """`shown_description`'s reading, with its formatted copy (ADR-0107).

    The last two are the copy, `None` unless a rendering kept formatting, and
    whether it keeps every piece of the markup it was read from: each tag in
    the set under its own name, with no attribute, comment or declaration.
    The visible text is the same text `shown_description` returns.
    """
    if len(value) > MAX_DESCRIPTION_CHARACTERS:
        return DESCRIPTION_OVERSIZED, False, None, False
    text = value if carries_markup(value) else decoded_description(value)
    if text is None:
        return value, False, None, False
    rendered = carries_markup(value) or _TAG.search(text) is not None
    parser = None
    if rendered:
        shown, parser = _rendering(text)
        if type(shown) is not str:
            return shown, True, None, False
        text = shown
    if _TAG.search(text) is not None or decoded_description(text) != text:
        return None, rendered, None, False
    if parser is None:
        return text, rendered, None, False
    return (text, rendered, *_formatted_copy(parser, text))


def carries_markup(value: object) -> bool:
    """Whether one provider value states markup this renderer would read."""
    return type(value) is str and ("<" in value or ">" in value)


# One value a provider stated that the renderer would not read.  It is not an
# absence: an adapter's field projection maps it to `MALFORMED`, so the report
# still records that the provider said something here.
UNPROJECTABLE_DESCRIPTION = object()


def projected_description(
    *values: object, strict_primary: bool = False
) -> tuple[object, bool, str | None]:
    """Project the first stated description, say whether it was rendered, and
    give its formatted copy.

    The returned value is what an adapter hands its own field projection: a
    plain string, an over-bound string that projection will call oversized, the
    unprojectable sentinel it will call malformed, or ``None`` for an absence.
    The flag is true only when markup was actually rendered to visible text,
    which is exactly when a candidate should record the transformation. The
    copy is `None` unless that rendering kept formatting; `formatted_claim`
    attaches it to the claim.
    """
    for value in values:
        if strict_primary and (
            value is None or type(value) is str and not value.strip()
        ):
            continue
        if type(value) is not str:
            if strict_primary:
                return UNPROJECTABLE_DESCRIPTION, False, None
            continue
        shown, rendered, formatted, _ = read_description(value)
        if type(shown) is str:
            return shown, rendered, formatted
        if shown is DESCRIPTION_OVERSIZED:
            return "x" * (MAX_DESCRIPTION_CHARACTERS + 1), False, None
        return UNPROJECTABLE_DESCRIPTION, False, None
    return None, False, None


def formatted_claim(
    claim: ProviderClaim, shown: object, formatted: str | None
) -> ProviderClaim:
    """Attach a description's formatted copy to the claim its text became.

    Only a valid description claim stating exactly that text carries it.
    """
    if (
        formatted is None
        or claim.kind is not ClaimKind.DESCRIPTION
        or claim.state is not FieldState.VALID
        or claim.value != shown
    ):
        return claim
    return replace(claim, formatted=formatted)


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
    projected, rendered, _ = projected_description(value)
    return UNPROJECTABLE_DESCRIPTION if rendered else projected


__all__ = [
    "DESCRIPTION_OVERSIZED",
    "HTML_VISIBLE_TEXT_VERSION",
    "UNPROJECTABLE_DESCRIPTION",
    "carries_markup",
    "decoded_description",
    "formatted_claim",
    "projected_description",
    "read_description",
    "shown_description",
    "stated_description",
    "visible_description",
]
