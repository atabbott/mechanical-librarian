"""A description's formatted copy and the one an alternative carries (#1995).

The copy is Mechanical Librarian's own serialisation of what a description's
markup shows, from a closed set of tags with no attributes (ADR-0107). Each
invariant below is checked over a synthetic corpus of benign and hostile
markup and over generated markup.
"""

from __future__ import annotations

import random
import re
from html.parser import HTMLParser

import pytest

from mechanical_librarian.adapters.providers.visible_text import (
    MAX_FORMATTED_DEPTH,
    read_description,
    visible_description,
)
from mechanical_librarian.domain.description_formatting import chosen_formatted
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_formatted_description,
)
from mechanical_librarian.domain.metadata_report import MAX_DESCRIPTION_CHARACTERS

_WRITTEN = frozenset({"p", "br", "ul", "ol", "li", "b", "strong", "i", "em", "del"})
_INLINE = frozenset({"b", "strong", "i", "em", "del"})
_TAG = re.compile(r"<[^>]*>")

CORPUS = (
    "<p>A <b>bold</b> one.</p><p>Two &amp; three</p><ul><li>a</li><li>b</li></ul>",
    "<p>One<br>two<br/>three</p>",
    "<p>Fish &amp; chips &lt;3</p>",
    '<p>He said "yes" &quot;twice&quot;.</p>',
    "<ol><li>First</li><li>Second <em>point</em></li></ol>",
    "<ul><li>Outer<ul><li>Inner</li></ul></li></ul>",
    "<b><p>A paragraph in bold</p></b>",
    "<p>a<p>nested</p>b</p>",
    "a<div><p>b</p>c</div>d",
    "<div>Plain division</div><h2>Heading</h2><p>Body</p>",
    "<s>gone</s> and <strike>gone too</strike>",
    '<a href="https://example.org/">a link</a> stays as words',
    "<u>underlined</u> <span style='color:red'>styled</span> <font>old</font>",
    "<p>One</p>\n\n<p>Two</p>\n",
    "<p>Line one\nline two</p>",
    "<p> spaced text </p>",
    "<!-- note --><p>After a comment</p>",
    "<p></p>a<p>  </p>b<b></b>c",
    "<li>orphan item</li> text",
    "<ul> <li>a</li> <br> <li>b</li> </ul>",
    "text<hr>more",
    "<table><tr><td>cell</td><td>other</td></tr></table>",
    "<p>2 &lt; 3 and 3 &gt; 2</p>",
    "<p>café — résumé</p>",
)
HOSTILE = (
    '<a onclick="steal()">click</a>',
    '<img src=x onerror="alert(1)">Hi',
    "<script>alert(1)</script>words",
    "<p>unclosed",
    "<b><i>crossed</b></i>",
    "<p onmouseover=x>text</p>",
)


def _service_forms(copy: str) -> tuple[str, ...]:
    """Forms Audiobookshelf may store a copy in: a void tag closed, quotes
    escaped, and the copy itself."""
    return (copy, copy.replace("<br>", "<br />"), copy.replace('"', "&quot;"))


class _Nesting(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.depth = 0
        self.valid = True
        self.attributes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.attributes += len(attrs)
        parent = self.stack[-1] if self.stack else ""
        allowed = (
            tag == "li"
            if parent in {"ul", "ol"}
            else tag in _INLINE | {"br"}
            if parent in _INLINE | {"p"}
            else tag in _WRITTEN - {"li"}
        )
        self.valid = self.valid and tag in _WRITTEN and allowed
        if tag != "br":
            self.stack.append(tag)
            self.depth = max(self.depth, len(self.stack))

    def handle_endtag(self, tag: str) -> None:
        self.valid = self.valid and bool(self.stack) and self.stack.pop() == tag

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] in {"ul", "ol"} and data.strip():
            self.valid = False


def _assert_copy_invariants(value: str) -> None:
    plain, _, copy, _ = read_description(value)
    if copy is None:
        return
    assert type(plain) is str
    # It shows exactly the visible text, which stays the only value compared.
    assert visible_description(copy) == plain
    # It reads as itself again, also in each form the service stores it in.
    for form in _service_forms(copy):
        assert read_description(form)[:3] == (plain, True, copy)
    # Only bare tags of the closed set, validly nested, never too deep.
    assert {tag.strip("</>") for tag in _TAG.findall(copy)} <= _WRITTEN
    nesting = _Nesting()
    nesting.feed(copy)
    nesting.close()
    assert nesting.valid and not nesting.stack and nesting.attributes == 0
    assert nesting.depth <= MAX_FORMATTED_DEPTH
    # Text is escaped: nothing but a tag of the set uses a bracket, and an
    # ampersand only starts the three references escaping writes.
    bare = _TAG.sub("", copy)
    assert "<" not in bare and ">" not in bare
    assert not re.search(r"&(?!amp;|lt;|gt;)", bare)
    assert validate_formatted_description(copy, "copy", MAX_DESCRIPTION_CHARACTERS) == (
        copy
    )


@pytest.mark.parametrize("value", CORPUS + HOSTILE)
def test_every_copy_meets_its_invariants(value: str) -> None:
    _assert_copy_invariants(value)


def test_generated_markup_meets_the_copy_invariants() -> None:
    generator = random.Random(1995)
    tags = (*sorted(_WRITTEN - {"br"}), "div", "h3", "s", "a", "u", "span", "q")
    texts = ("word", " ", "\n", "a & b", "2 < 3", '"quoted"', "x y", "")

    def fragment(depth: int) -> str:
        parts = []
        for _ in range(generator.randint(1, 3)):
            choice = generator.random()
            if choice < 0.4 or depth > 5:
                parts.append(
                    generator.choice(texts).replace("&", "&amp;").replace("<", "&lt;")
                )
            elif choice < 0.5:
                parts.append(generator.choice(("<br>", "<br/>", "<hr>")))
            else:
                tag = generator.choice(tags)
                attribute = ' title="t"' if generator.random() < 0.1 else ""
                parts.append(f"<{tag}{attribute}>{fragment(depth + 1)}</{tag}>")
        return "".join(parts)

    copies = 0
    for _ in range(2_000):
        value = fragment(0)
        _assert_copy_invariants(value)
        copies += read_description(value)[2] is not None
    # The corpus exercises copies, not only refusals.
    assert copies > 500


@pytest.mark.parametrize(
    ("value", "plain", "copy", "lossless"),
    (
        (
            "<p>A <b>bold</b> one.</p><p>Two &amp; three</p><ul><li>a</li></ul>",
            "A bold one. • Two & three • a",
            "<p>A <b>bold</b> one.</p><p>Two &amp; three</p><ul><li>a</li></ul>",
            True,
        ),
        # A raw line break in text is a break, as the renderer reads it.
        (
            "<p>Line one\nline two</p>",
            "Line one • line two",
            "<p>Line one<br>line two</p>",
            True,
        ),
        # Mapped and dropped tags keep their words; nothing is an attribute.
        ("<s>gone</s>", "gone", "<del>gone</del>", False),
        (
            "<h2>Head</h2><div>Body</div>",
            "Head • Body",
            "<p>Head</p><p>Body</p>",
            False,
        ),
        (
            '<p>See <a href="https://example.org/">this</a>.</p>',
            "See this.",
            "<p>See this.</p>",
            False,
        ),
        ('<p class="lead">Lead</p>', "Lead", "<p>Lead</p>", False),
        # A paragraph in bold keeps the paragraph; a dropped block keeps its line.
        (
            "<b><p>Bold paragraph</p></b>",
            "Bold paragraph",
            "<p>Bold paragraph</p>",
            False,
        ),
        ("a<div><p>b</p>c</div>d", "a • b • c • d", "a<p>b</p>c<br>d", False),
        # Empty elements are dropped.
        ("<p>a</p><p></p><p>b<b></b></p>", "a • b", "<p>a</p><p>b</p>", False),
    ),
)
def test_a_copy_is_our_own_serialisation(
    value: str, plain: str, copy: str, lossless: bool
) -> None:
    assert read_description(value) == (plain, True, copy, lossless)


@pytest.mark.parametrize(
    "value",
    (
        # The renderer's refusals still refuse; nothing is copied from them.
        *HOSTILE,
        # Plain text, or markup that keeps no tag once read, has no copy.
        "Fish &amp; chips",
        "<span>Only a span</span>",
        # Text directly in a list can't be nested validly.
        "<ul>loose<li>item</li></ul>",
        # Nesting deeper than the bound.
        "<i>" * (MAX_FORMATTED_DEPTH + 1) + "deep" + "</i>" * (MAX_FORMATTED_DEPTH + 1),
        # A copy over the bound is dropped, never cut: each `&` grows to `&amp;`.
        "<p>" + "&" * (MAX_DESCRIPTION_CHARACTERS // 2) + "</p>",
        # A copy failing the description checks once its tags are removed.
        "<p>/home/<b>someone</b>/library</p>",
    ),
)
def test_no_copy_where_one_cannot_meet_its_rules(value: str) -> None:
    assert read_description(value)[2] is None


def test_the_visible_text_never_changes() -> None:
    # The plain reading is the one `visible_description` gives, byte for byte.
    for value in CORPUS:
        assert read_description(value)[0] == visible_description(value)


@pytest.mark.parametrize(
    "value",
    (
        "no tag at all",
        "<p>A <a>link</a></p>",
        '<p class="x">attribute</p>',
        "<p>unescaped < bracket</p>",
        "<P>upper</P>",
    ),
)
def test_a_stored_copy_carries_only_bare_tags_of_the_set(value: str) -> None:
    with pytest.raises(EvidenceValueError):
        validate_formatted_description(value, "copy", MAX_DESCRIPTION_CHARACTERS)


@pytest.mark.parametrize(
    ("copies", "chosen"),
    (
        ((), None),
        ((None, None), None),
        (("<p>A</p>",), "<p>A</p>"),
        (("<p>A</p>", None, "<p>A</p>"), "<p>A</p>"),
        # Two different copies behind the same words: plain only.
        (("<p>A</p>", "<p><b>A</b></p>"), None),
    ),
)
def test_an_alternative_carries_a_copy_only_when_its_claims_agree(
    copies: tuple[str | None, ...], chosen: str | None
) -> None:
    assert chosen_formatted(copies) == chosen
