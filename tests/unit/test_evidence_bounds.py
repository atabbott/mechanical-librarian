"""The forbidden-codepoint set and the projection scans the validators apply."""

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mechanical_librarian.domain import evidence_bounds
from mechanical_librarian.domain.evidence_bounds import (
    _ABSOLUTE_PATH_PATTERN,
    _BARE_BEARER_PATTERN,
    _C1_CONTROLS,
    _CREDENTIAL_ASSIGNMENT_PATTERN,
    _CREDENTIAL_PATTERN,
    _FILESYSTEM_URI_PATTERN,
    _FORBIDDEN_ASCII_CODEPOINTS,
    _FORBIDDEN_CODEPOINTS,
    _HOME_PATH_PATTERN,
    _HTML_TAG_SLASH_PATTERN,
    _LABELLED_PATH_PATTERN,
    _MULTIWORD_CREDENTIAL_PATTERN,
    _QUOTED_TAG_SPAN_PATTERN,
    _TAG_SPAN_PATTERN,
    _UNC_PATH_PATTERN,
    _WINDOWS_DRIVE_ROOT_PATTERN,
    EvidenceValueError,
    _credential_label_shaped,
    replace_forbidden_codepoints,
    validate_incumbent_description,
    validate_text,
)

# A literal transcription of the per-codepoint predicate the compiled class
# replaced, kept here so the class is checked against an independent statement
# of the set rather than against itself.
_BIDI_CONTROLS = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)


def _forbidden_codepoint(codepoint: int) -> bool:
    return (
        codepoint < 0x20
        or 0x7F <= codepoint <= 0x9F
        or 0xD800 <= codepoint <= 0xDFFF
        or codepoint in _BIDI_CONTROLS
        or codepoint in {0x2028, 0x2029}
        or 0xFDD0 <= codepoint <= 0xFDEF
        or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
    )


def test_pattern_matches_exactly_the_forbidden_codepoints() -> None:
    mismatches = [
        hex(codepoint)
        for codepoint in range(0x110000)
        if bool(_FORBIDDEN_CODEPOINTS.search(chr(codepoint)))
        != _forbidden_codepoint(codepoint)
    ]
    assert mismatches == []


def test_ascii_pattern_matches_exactly_the_forbidden_ascii_codepoints() -> None:
    # `validate_text` scans ASCII text with this narrower class (#1202).
    assert [
        codepoint
        for codepoint in range(0x80)
        if bool(_FORBIDDEN_ASCII_CODEPOINTS.search(chr(codepoint)))
        != _forbidden_codepoint(codepoint)
    ] == []


def test_c1_pattern_matches_exactly_the_c1_controls_all_forbidden() -> None:
    # An incumbent description refuses these with their own reason (#1251);
    # each stays in the forbidden set every other validator applies.
    c1 = [
        codepoint
        for codepoint in range(0x110000)
        if _C1_CONTROLS.search(chr(codepoint))
    ]
    assert c1 == list(range(0x80, 0xA0))
    assert all(_forbidden_codepoint(codepoint) for codepoint in c1)


_FORBIDDEN_SAMPLES = (
    "\x00",
    "\x7f",
    "\u202e",
    "\ufdd0",
    "\U0010ffff",
    chr(0xD800),
    chr(0xDFFF),
)


@pytest.mark.parametrize("character", _FORBIDDEN_SAMPLES)
@pytest.mark.parametrize("shape", ("{}Text body", "Text{}body", "Text body{}"))
@pytest.mark.parametrize("validator", (validate_text, validate_incumbent_description))
def test_forbidden_codepoint_is_refused_anywhere_in_text(
    validator, shape: str, character: str
) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validator(shape.format(character), "label", 256)
    assert type(raised.value) is EvidenceValueError
    assert str(raised.value) == "label contains forbidden control or Unicode text"


@pytest.mark.parametrize("validator", (validate_text, validate_incumbent_description))
def test_text_without_forbidden_codepoints_is_admitted(validator) -> None:
    value = "Café 語 \U0001f600 \ufffd \ufffd\U0001fffd"
    assert validator(value, "label", 256) == value


@pytest.mark.parametrize(
    "value,message",
    (
        ("a" * 257 + "\x00", "label exceeds its size limit"),
        (" \x00", "label must be non-empty trimmed text"),
        ("\x00 ", "label must be non-empty trimmed text"),
    ),
)
def test_text_bounds_precede_the_codepoint_check(value: str, message: str) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validate_text(value, "label", 256)
    assert str(raised.value) == message


@pytest.mark.parametrize(
    "value,message",
    (
        ("a" * 257 + "\x00", "label exceeds its size limit"),
        (" \t\n", "label must be non-empty text"),
    ),
)
def test_description_bounds_precede_the_codepoint_check(
    value: str, message: str
) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validate_incumbent_description(value, "label", 256)
    assert str(raised.value) == message


def test_replacement_substitutes_each_forbidden_codepoint() -> None:
    value = "\x00a\u202eb" + chr(0xD800) + chr(0xDFFF) + "c\U0010ffff\ufffe"
    assert (
        replace_forbidden_codepoints(value) == "\ufffda\ufffdb\ufffd\ufffdc\ufffd\ufffd"
    )
    clean = "Café\u00a0語 \U0001f600"
    assert replace_forbidden_codepoints(clean) == clean
    assert replace_forbidden_codepoints("") == ""


# --- Projection scan guards (#1202). ---
#
# The two scans below are `_validate_projection_safety` and
# `_credential_shaped` exactly as they read before #1202 added literal guards,
# kept verbatim so the guarded scans are compared with the unguarded ones.


def _pre_1202_validate_projection_safety(text: str, label: str) -> str:
    lowered = text.casefold()
    if (
        text.startswith("\\")
        or (
            len(text) >= 3
            and text[0].isascii()
            and text[0].isalpha()
            and text[1] == ":"
            and text[2] in "/\\"
        )
        or _FILESYSTEM_URI_PATTERN.search(text)
        or _HOME_PATH_PATTERN.search(text)
        or _LABELLED_PATH_PATTERN.search(text)
        or _ABSOLUTE_PATH_PATTERN.search(text)
        or _WINDOWS_DRIVE_ROOT_PATTERN.search(text)
        or _UNC_PATH_PATTERN.search(text)
        or "/users/" in lowered
        or "/home/" in lowered
        or "../" in text
        or "..\\" in text
    ):
        raise EvidenceValueError(f"{label} must not contain a production path")
    if _pre_1202_credential_shaped(text):
        raise EvidenceValueError(f"{label} must not contain credential-shaped text")
    return text


def _pre_1202_credential_shaped(value: str) -> bool:
    if _CREDENTIAL_PATTERN.search(value) or _MULTIWORD_CREDENTIAL_PATTERN.search(value):
        return True
    bare_bearer = _BARE_BEARER_PATTERN.search(value)
    if bare_bearer is not None:
        credential = bare_bearer.group(1)
        if (
            any(
                character.isdigit() or character in "_~+/=-" for character in credential
            )
            or credential.count(".") >= 2
        ):
            return True
    for match in _CREDENTIAL_ASSIGNMENT_PATTERN.finditer(value):
        raw_label = match.group(1)
        delimiter = match.group(2)
        structured_prefix = match.start() == 0 or (
            match.start() > 0 and value[match.start() - 1] in "?&{[,;"
        )
        if _credential_label_shaped(
            raw_label,
            reject_exact_secret=delimiter == "=" or structured_prefix,
        ):
            return True
    return False


# Each guarded pattern, the flags it compiles with, and the literal set a match
# must contain: the guard skips the scan only for text holding none of them.
_GUARDS = (
    (_FILESYSTEM_URI_PATTERN, ":"),
    (_FILESYSTEM_URI_PATTERN, "/"),
    (_HOME_PATH_PATTERN, "~"),
    (_LABELLED_PATH_PATTERN, ":="),
    (_ABSOLUTE_PATH_PATTERN, "/"),
    (_WINDOWS_DRIVE_ROOT_PATTERN, ":"),
    (_UNC_PATH_PATTERN, "\\"),
    (_MULTIWORD_CREDENTIAL_PATTERN, ":="),
    (_CREDENTIAL_ASSIGNMENT_PATTERN, ":="),
    (_HTML_TAG_SLASH_PATTERN, "<"),
)


_LITERAL_NAMES = {
    ":": "colon",
    "/": "slash",
    "~": "tilde",
    ":=": "assignment",
    "<": "angle",
}


@pytest.mark.parametrize(
    "flags,literals",
    [
        pytest.param(
            flags,
            literals,
            id=("folded_" if flags else "exact_")
            + _LITERAL_NAMES.get(literals, "backslash"),
        )
        for flags, literals in sorted(
            {(pattern.flags & re.IGNORECASE, literals) for pattern, literals in _GUARDS}
        )
    ],
)
def test_no_other_codepoint_matches_a_guard_literal(flags: int, literals: str) -> None:
    # A guard tests `in` on the exact literal; the pattern would also accept any
    # codepoint its flags fold onto that literal, so there must be none.
    literal = re.compile("[" + re.escape(literals) + "]", flags)
    assert {
        chr(codepoint)
        for codepoint in range(0x110000)
        if literal.fullmatch(chr(codepoint))
    } == set(literals)


_SCAN_FRAGMENTS = (
    "path",
    "File",
    "location",
    "source",
    "smb",
    "FILE://",
    "://",
    "~/",
    "~\\",
    "/",
    "//",
    "\\",
    "\\\\",
    "C:",
    "c:\\",
    "\u212a:/",
    ":",
    "=",
    " ",
    "\t",
    "api key",
    "Secret Access Key",
    "client_secret",
    "token",
    "apiKey",
    "authorization",
    "Bearer ",
    "abc.def.ghi",
    "sk-proj-abcdefgh12",
    "user:pass@",
    "/users/",
    "/Home/",
    "../",
    "..\\",
    "?",
    "&",
    "{",
    '"',
    "Straße",
    "\u017f",
    "word",
    "42",
)


def _assert_scans_agree(text: str) -> None:
    assert evidence_bounds._credential_shaped(text) is _pre_1202_credential_shaped(text)
    outcomes = []
    for scan in (
        evidence_bounds._validate_projection_safety,
        _pre_1202_validate_projection_safety,
    ):
        try:
            outcomes.append(("admitted", scan(text, "label")))
        except EvidenceValueError as error:
            outcomes.append((type(error).__name__, str(error)))
    assert outcomes[0] == outcomes[1]


@settings(max_examples=400, deadline=None)
@given(
    st.lists(
        st.one_of(st.sampled_from(_SCAN_FRAGMENTS), st.text(max_size=4)),
        max_size=14,
    ).map("".join)
)
def test_guarded_scans_equal_the_unguarded_scans(text: str) -> None:
    _assert_scans_agree(text)


@pytest.mark.parametrize(
    "text",
    (
        "A synthetic account of shared libraries and careful decisions.",
        "Book 1: a plain subtitle",
        "and/or",
        "a = b",
        "path: /srv/library",
        "File://share",
        "SMB://host/share",
        "path=\\share",
        "~/books",
        "\\\\server\\share",
        "see D:\\media",
        "api key: abc",
        "access_token=abc",
        "Bearer abc.def.ghi",
        "",
    ),
)
def test_guarded_scans_keep_each_refusal(text: str) -> None:
    _assert_scans_agree(text)


# --- Incumbent description markup and line separators (#1251). ---


@pytest.mark.parametrize(
    "value",
    (
        "<p>Plain synopsis.</p>",
        "<P>X</P>",
        "</h2>",
        "a<br />b",
        "<br/>x",
        '<a href="https://example.com/page">x</a>',
        "https://example.com/x</a>",
        "Line one\u2028Line two",
        "Text\u2028",
        "\u2028Text",
        "<p>One</p>\r\n\u2028<p>Two</p>",
        "<p>Plain synopsis.</p><p>Second<br/>para.</p>",
        "<p>Secret history of Rome.</p>",
        '<p>Read the <a href="https://example.com/book">site</a>.</p>',
        '<p>He said "hi" > all.</p>',
    ),
)
def test_incumbent_description_admits_tag_slashes_and_line_separators(
    value: str,
) -> None:
    assert validate_incumbent_description(value, "label", 256) is value


@pytest.mark.parametrize(
    "value,message",
    [
        *(
            (value, "label must not contain a production path")
            for value in (
                "<p>Read /srv/media/b.m4b</p>",
                "</p>/srv/media",
                "a</p>/etc",
                "</etc/passwd>",
                '<a href="/about">x</a>',
                '<img src="/srv/x.jpg" />',
                "&lt;/p&gt;",
                "<p>~/Books</p>",
                "<p>C:\\Books</p>",
                "<p>location: /x</p>",
                "<p>\\\\server\\share\\x</p>",
                "\u2028\\srv",
                "</p>/home/x",
                # Only an ASCII tag name and ASCII spacing make a tag (re.ASCII).
                "</\u017f>",
                "</\u212a>",
                "</p\u00a0>",
                "a<br\u2003/>",
                # A tag splitting a non-absolute path (R5).
                "file:</p>//server/share",
                "C:</p>\\Books\\secret",
                "..</p>\\secret",
                "x \\</p>\\server\\share",
                "~</p>/x",
                "/Use</b>rs/x",
                "x </p>/ho</b>me/y",
                # An opening tag splitting one (R6).
                "C<b>:</b>\\Books\\x",
                "file<i>:</i>//server/share",
            )
        ),
        *(
            (value, "label must not contain credential-shaped text")
            for value in (
                "</p> api_key=synthetic",
                "https://user:pa<br/>ss@host",
                "https://alice:s3cr3t</p>@db.example.com/x",
                "https://u:p</i>@h",
                '<a href="https://user:hunter2</b>@example.com">',
                # A tag splitting a credential label (R5).
                "<span>api</span>_key=hunter2",
                "<b>pass</b>word=hunter2",
                "to</p>ken=abc",
                "pass</b>word: hunter2",
                "Bear</b>er abc.def.ghi",
                # An opening or attribute tag splitting one, or a leading tag
                # hiding a start-anchored label (R6).
                "pass<b>word</b>=hunter2",
                "to<b>ken</b>=abc",
                "api<i>_key</i>=x",
                "<p>Secret: hunter2</p>",
                "Bearer abcdefg1</p><p>More text",
                'pass<a href="x">word</a>=hunter2',
                "</p> secret: x",
                # A quoted `>` or an unbalanced quote inside a tag (R7).
                'pas<a title="a>b">sword=hunter2</p>',
                # Distinct text, not only quotes: the tier runner's safe identity
                # alphabet folds both quote characters to one.
                "pas<a title='c>d'>sword=hunter2</p>",
                'pas<a title="x>sword=hunter2</p>',
            )
        ),
        *(
            (value, "label contains forbidden control or Unicode text")
            for value in (
                "a\r\u2028b",
                "Text\u2029",
                "Text\x85\u202e",
                "\x92Text\u2029",
                "Text\u2028\x00",
                "\u2003\u2028Text",
            )
        ),
        *(
            (value, "label contains C1 control text")
            for value in (
                "It\x92s",
                "Text\x85",
                "\x85Text",
                "Text\x92 /srv/x",
                "Text\x92 api_key=synthetic",
                "\x80\x9f Text",
            )
        ),
        (" \u2028 ", "label must be non-empty text"),
    ],
)
def test_incumbent_description_rejection_order(value: str, message: str) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validate_incumbent_description(value, "label", 256)
    assert type(raised.value) is EvidenceValueError
    assert str(raised.value) == message


@pytest.mark.parametrize("value", ("</p>", "Text\u2028more", "It\x92s"))
def test_shared_projection_grammar_is_unchanged(value: str) -> None:
    # Provider, local-claim and discovery text keep today's grammar (#1251).
    with pytest.raises(EvidenceValueError):
        evidence_bounds.validate_projection_text(value, "label", 256)


def _tag_free(text: str) -> tuple[str, ...]:
    return tuple(
        spans.sub("", text).strip(" \t\r\n")
        for spans in (_TAG_SPAN_PATTERN, _QUOTED_TAG_SPAN_PATTERN)
    )


def _projection_outcome(text: str, *, markup: bool) -> tuple[str, str]:
    try:
        return (
            "admitted",
            evidence_bounds._validate_projection_safety(text, "label", markup=markup),
        )
    except EvidenceValueError as error:
        return (type(error).__name__, str(error))


_MARKUP_FRAGMENTS = (
    "<",
    ">",
    "/",
    "</",
    "/>",
    "p",
    "br",
    "h2",
    " ",
    "srv",
    "<p>",
    "</p>",
    "<br />",
    "https://",
    "user:pw@",
    "~",
    "=",
    "api_key",
    "api",
    "_key=x",
    "</b>",
    "word=x",
    "file:",
    "..",
    "<b>",
    "<i>",
    '<a href="x">',
    "Secret: x",
    '<a title="',
    "'>",
    '">',
    "sword=x",
)


@settings(max_examples=400, deadline=None)
@given(
    st.lists(
        st.one_of(
            st.sampled_from(_MARKUP_FRAGMENTS),
            st.sampled_from(_SCAN_FRAGMENTS),
            st.text(max_size=3),
        ),
        max_size=12,
    ).map("".join)
)
def test_markup_scan_differs_only_on_a_tag_slash(text: str) -> None:
    # The incumbent validator strips edges before this scan.
    text = text.strip(" \t\r\n")
    markup = _projection_outcome(text, markup=True)
    if _HTML_TAG_SLASH_PATTERN.search(text) is None:
        # Without a tag slash the scan is the default one, as on base.
        assert markup == _projection_outcome(text, markup=False)
    elif markup[0] == "admitted":
        # Whatever markup admits, the default scan admits with every tag-like
        # span removed and the edges stripped, read plainly and quote-aware.
        for removed in _tag_free(text):
            assert _projection_outcome(removed, markup=False) == ("admitted", removed)


@pytest.mark.parametrize(
    "text",
    (
        "Coo</b>kie: abc",
        "pass<b>word</b>=hunter2",
        "pass</b>word: hunter2",
        "file:</p>//server/share",
        "<p>Plain synopsis.</p>",
    ),
)
def test_split_text_reads_as_its_tag_free_form(text: str) -> None:
    # The default scan does not refuse a bare cookie header, so neither does a
    # split one; every other split shape is refused as its tag-free form is.
    expected = _projection_outcome(_tag_free(text)[0], markup=False)
    assert _projection_outcome(text, markup=True) == (
        expected if expected[0] != "admitted" else ("admitted", text)
    )
