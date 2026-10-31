"""Canonical comparison of values already admitted for one target field.

The audiobook synopsis and genre floor (#1250) lives here too, beside the
comparison forms it reads, so the proposal evaluator and the rich preview
both read one recognizer.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Final, TypeGuard, cast

from mechanical_librarian.domain.evidence_bounds import validate_incumbent_description
from mechanical_librarian.domain.field_outcomes import language_outcomes_compatible
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    TargetDestination,
    TargetSeries,
    isbn_family,
    normalize_title,
)


def canonical_text(destination: TargetDestination, value: str) -> str:
    """Return the proposal evaluator's comparison form for one text field.

    A synopsis compares without a leading boilerplate header line (#1317).
    """
    if destination is TargetDestination.DESCRIPTION:
        return " ".join(
            unicodedata.normalize(
                "NFC",
                _synopsis_after_header(
                    validate_incumbent_description(
                        value,
                        "proposal value",
                        MAX_DESCRIPTION_CHARACTERS,
                    )
                ),
            ).split()
        ).casefold()
    return normalize_title(value).casefold()


# A closed boilerplate heading a synopsis may open with, alone on its first
# line, optionally followed by a colon. Matched whole against the line's
# whitespace-collapsed, casefolded text (#1317).
_SYNOPSIS_HEADER: Final = re.compile(
    r"(?:about (?:the|this) book|(?:book )?description|synopsis|summary"
    r"|publisher['’]?s summary) ?:?"
)
_FIRST_LINE_END: Final = re.compile(r"\r?\n|\u2028")


def _synopsis_after_header(value: str) -> str:
    """The text after a leading header line, or the whole value without one."""
    parts = _FIRST_LINE_END.split(value.lstrip(), maxsplit=1)
    if (
        len(parts) == 2
        and parts[1].strip()
        and _SYNOPSIS_HEADER.fullmatch(
            " ".join(unicodedata.normalize("NFC", parts[0]).split()).casefold()
        )
        is not None
    ):
        return parts[1]
    return value


def synopsis_header(value: object) -> bool:
    """Whether a synopsis opens with a boilerplate header line (#1317).

    Comparison ignores the header; the retained bytes keep it. Pure, and
    never raises.
    """
    return type(value) is str and _synopsis_after_header(value) != value


def title_identity_key(value: str) -> str:
    """Compare title identity without treating a possessive apostrophe as dissent.

    This comparison form never replaces an original candidate's stored text.
    """
    return canonical_text(TargetDestination.TITLE, value.replace("’", "'"))


_LEADING_TITLE_ARTICLES: Final = frozenset({"the", "a", "an"})


def title_article_key(value: str) -> str:
    """Compare material titles without one leading `The`, `A` or `An` (#1496).

    The article belongs to the title as read, so this form only decides that
    two titles are the same; it never replaces either one's text.
    """
    key = title_identity_key(value)
    first, _, rest = key.partition(" ")
    return rest if first in _LEADING_TITLE_ARTICLES and rest else key


def title_has_leading_article(value: str) -> bool:
    """Whether a title opens with an article that `title_article_key` folds."""
    return title_article_key(value) != title_identity_key(value)


def values_match(
    destination: TargetDestination,
    current: object,
    candidate: object,
) -> bool:
    """Compare admitted target values with the evaluator's field semantics."""
    if destination is TargetDestination.EXPLICIT:
        return (
            type(current) is bool and type(candidate) is bool and current == candidate
        )
    if destination is TargetDestination.SERIES:
        current_series = _series_values(current)
        candidate_series = _series_values(candidate)
        return current_series is not None and current_series == candidate_series
    if destination is TargetDestination.ISBN:
        return _isbn_values_match(current, candidate)
    current_sequence = _text_sequence(current)
    candidate_sequence = _text_sequence(candidate)
    if current_sequence is not None and candidate_sequence is not None:
        return tuple(
            canonical_text(destination, value) for value in current_sequence
        ) == tuple(canonical_text(destination, value) for value in candidate_sequence)
    if type(current) is str and type(candidate) is str:
        return (
            language_outcomes_compatible(current, candidate)
            if destination is TargetDestination.LANGUAGE
            else canonical_text(destination, current)
            == canonical_text(destination, candidate)
        )
    return current == candidate


def creator_list_reordered(current: object, candidate: object) -> bool:
    """Whether two creator lists name the same members in another order.

    Members compare in the evaluator's author form, as a multiset, so a list
    that adds, drops, repeats or respells a member is not a reorder (#1252).
    """
    current_sequence = _text_sequence(current)
    candidate_sequence = _text_sequence(candidate)
    if current_sequence is None or candidate_sequence is None:
        return False
    left = tuple(
        canonical_text(TargetDestination.AUTHORS, value) for value in current_sequence
    )
    right = tuple(
        canonical_text(TargetDestination.AUTHORS, value) for value in candidate_sequence
    )
    return left != right and sorted(left) == sorted(right)


def _isbn_values_match(current: object, candidate: object) -> bool:
    if type(current) is not str or type(candidate) is not str:
        return False
    current_family = isbn_family(current)
    candidate_family = isbn_family(candidate)
    return (
        current_family is not None
        and candidate_family is not None
        and current_family == candidate_family
        and _isbn_representation(current) == _isbn_representation(candidate)
    )


def _isbn_representation(value: str) -> str:
    return "".join(
        character for character in value if character.isdigit() or character in "Xx"
    ).upper()


def _text_sequence(value: object) -> tuple[str, ...] | None:
    if type(value) not in {list, tuple} or any(
        type(item) is not str for item in cast(Sequence[object], value)
    ):
        return None
    return tuple(cast(Sequence[str], value))


def _series_values(value: object) -> tuple[tuple[str, str | None], ...] | None:
    if type(value) not in {list, tuple}:
        return None
    members: list[tuple[str, str | None]] = []
    for item in cast(Sequence[object], value):
        if type(item) is TargetSeries:
            members.append(
                (canonical_text(TargetDestination.SERIES, item.name), item.sequence)
            )
        elif (
            type(item) is dict
            and set(item) == {"name", "sequence"}
            and type(item["name"]) is str
            and (item["sequence"] is None or type(item["sequence"]) is str)
        ):
            members.append(
                (
                    canonical_text(TargetDestination.SERIES, item["name"]),
                    item["sequence"],
                )
            )
        else:
            return None
    return tuple(members)


# The closed words an audiobook's genre list or synopsis may hold in place of
# anything about the work: they name the recording's format, and nothing else.
# Matched whole against NFC, whitespace-collapsed, casefolded text, optionally
# in parentheses (#1250).
_FORMAT_WORD: Final = re.compile(
    r"(?P<open>\()?"
    r"(?:(?:un)?abridged(?: audio ?book)?|audible audio ?book|audio ?books?"
    r"|audio cd|mp3 cd)"
    r"(?(open)\))"
)


def audiobook_format_word(text: str) -> bool:
    """Whether one already-canonical text is only a closed format word."""
    return _FORMAT_WORD.fullmatch(text) is not None


def audiobook_format_only_genres(values: object) -> bool:
    """Whether every member of one genre list names only the recording's format.

    Such a list says nothing about the work, so an audiobook treats it as
    empty: an incumbent holding one is filled rather than preserved, and a
    candidate holding one is no candidate. An empty list is not format-only;
    it is already empty. Pure, and never raises (#1250).
    """
    return (
        type(values) in {list, tuple}
        and bool(values)
        and all(
            type(value) is str
            and audiobook_format_word(
                " ".join(unicodedata.normalize("NFC", value).split()).casefold()
            )
            for value in cast(Sequence[object], values)
        )
    )


DESCRIPTION_VISIBLE_CUTOFFS: Final = ("...", "…", "--")

_SYNOPSIS_MARKER: Final = re.compile(
    r"(?:(?:chapter|track|part|section|disc|cd|side) [0-9]+(?: of [0-9]+)?"
    r"|no (?:description|synopsis)(?: available)?|(?:description|synopsis) unavailable"
    r"|n/a|none|unknown|tbd)\.?"
)
# A credit segment separator: `;`, `/`, `|`, ` - `, or a full stop that ends a
# word longer than one letter, so an initial stays inside its name. A comma
# separates segments too, but may also join the names of one credit.
_CREDIT_SEPARATOR: Final = re.compile(
    r"\s*(?:[;/|]|\s-\s|(?<!\b[^\W\d_])\.(?=\s|$))\s*"
)
_CREDIT_ROLE: Final = (
    r"(?:narrated|read|performed|written|translated|adapted|abridged|produced"
    r"|directed|introduced|edited|recorded|voiced)"
)
_CREDIT_BY: Final = re.compile(rf"{_CREDIT_ROLE}(?: (?:and|&) {_CREDIT_ROLE})* by (.+)")
# A ripper or tagger credit names a handle, which may hold digits and `_`.
_RIPPER_ROLE: Final = r"(?:tagged|tag|ripped|encoded|created)"
_RIPPER_BY: Final = re.compile(rf"{_RIPPER_ROLE}(?: (?:and|&) {_RIPPER_ROLE})* by (.+)")
_CREDIT_BARE: Final = re.compile(
    rf"(?:{_CREDIT_ROLE}|{_RIPPER_ROLE})(?: (?:and|&) (?:{_CREDIT_ROLE}|{_RIPPER_ROLE}))* by ?:?"
)
_DURATION: Final = r"[0-9]+ ?(?:hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)"
_CREDIT_LENGTH: Final = re.compile(rf"length ?: ?{_DURATION}(?: (?:and )?{_DURATION})*")
_MONTH: Final = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE: Final = (
    r"(?:[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?|[0-9]{1,2}[.-][0-9]{1,2}[.-][0-9]{4}"
    rf"|{_MONTH} [0-9]{{1,2}}(?:st|nd|rd|th)?,? [0-9]{{4}}"
    rf"|[0-9]{{1,2}}(?:st|nd|rd|th)? {_MONTH},? [0-9]{{4}}|{_MONTH},? [0-9]{{4}})"
)
# A publisher or release label runs to the end of its segment, commas included.
_CREDIT_PUBLISHER: Final = re.compile(r"publisher ?: ?\S.*")
_CREDIT_RELEASE: Final = re.compile(rf"(?:release date|released) ?: ?{_DATE}")
_UNABRIDGED: Final = "unabridged"
_MIN_UNABRIDGED_PREFIX: Final = 4
# Real tag syntax only: a closed set of formatting elements whose attributes
# are quoted name=value pairs, or a comment. A bracketed phrase such as
# "<A story>" or "<1984: a dystopia>" is text, not markup (#1250).
SYNOPSIS_MARKUP_TAG: Final = re.compile(
    r"<(?:/?(?:a|abbr|b|big|blockquote|br|center|cite|code|dd|div|dl|dt|em|font"
    r"|h[1-6]|hr|i|li|ol|p|pre|q|s|small|span|strike|strong|sub|sup|table|tbody"
    r"|td|tfoot|th|thead|tr|tt|u|ul)"
    r"""(?:\s+[a-z][-a-z0-9]*\s*=\s*(?:"[^"<>]*"|'[^'<>]*'))*\s*/?"""
    r"|!--[^<>]*--)>",
    re.ASCII | re.IGNORECASE,
)
_ENCODER_WATERMARKS: Final = frozenset({"fre:ac - free audio converter"})
_MARKABLE_WATERMARK: Final = " created by markable"
_CREDIT_LABEL: Final = re.compile(
    r"(?:narrators?|reader|authors?|translator|performer|cast) ?: ?(.+)"
)
_CREDIT_NAME_JOIN: Final = re.compile(r" (?:and|&) ")
_CREDIT_NAME_TOKEN: Final = re.compile(r"[^\W\d_](?:[^\W\d_]|[.'’-])*")
_RIPPER_NAME_TOKEN: Final = re.compile(r"\w(?:\w|[.'’-])*")
# Words that make a phrase prose rather than a name: "narrated by the ghost of
# her sister" and "written by a master of suspense" credit nobody.
# fmt: off
_CREDIT_PROSE_WORDS: Final = frozenset({
    "a", "about", "after", "an", "and", "are", "as", "at", "be", "been",
    "before", "but", "by", "for", "from", "he", "her", "him", "his", "i",
    "in", "into", "is", "it", "its", "me", "my", "no", "not", "of", "on",
    "or", "our", "over", "she", "so", "than", "that", "the", "their", "them",
    "then", "these", "they", "this", "those", "through", "to", "us", "was",
    "we", "were", "what", "when", "where", "which", "who", "whom", "whose",
    "with", "you", "your",
})
# fmt: on
_MAX_CREDIT_LINE: Final = 240
_MAX_CREDIT_SEGMENTS: Final = 6
_MAX_CREDIT_NAMES: Final = 6
_MAX_CREDIT_NAME_TOKENS: Final = 5
# A comma continuation is likelier prose than a credit's first name.
_MAX_CONTINUATION_NAME_TOKENS: Final = 3


def recognized_synopsis_placeholder(value: str) -> bool:
    """Compare only the closed marker vocabulary; never rewrite retained text.

    A whole value is a placeholder when it is a marker -- a chapter, track,
    part, section, disc, CD or side number, a "no description" phrase, `n/a`,
    `none`, `unknown` or `tbd` -- a closed format word, a closed encoder
    watermark, markup with no text, or a structured credit line (#1250). Each
    recognizer reads shape, never length alone.
    """
    text = canonical_text(TargetDestination.DESCRIPTION, value)
    return (
        _SYNOPSIS_MARKER.fullmatch(text) is not None
        or _synopsis_format_word(text.removesuffix("."))
        or not SYNOPSIS_MARKUP_TAG.sub("", text).strip()
        or text in _ENCODER_WATERMARKS
        or (
            text.endswith(_MARKABLE_WATERMARK)
            and _synopsis_format_word(text.removesuffix(_MARKABLE_WATERMARK))
        )
        or _credit_line(text)
    )


def _synopsis_format_word(text: str) -> bool:
    """A closed format word, or "unabridged" cut to four or more letters."""
    return audiobook_format_word(text) or (
        len(text) >= _MIN_UNABRIDGED_PREFIX and _UNABRIDGED.startswith(text)
    )


def _credit_line(text: str) -> bool:
    """Read one canonical value as one to six credit or format segments.

    A segment is a role verb (or two joined by "and") followed by "by" and
    names, a role label followed by ":" and names, a bare role verb and "by", a
    closed format word, a length, or a publisher or release-date label running
    to the end of its segment; at least one must be a credit. A credit names one
    to six people, each one to five letter tokens, joined by "and", "&" or a
    comma, where a name after a comma has at most three tokens; a ripper or tagger credit's tokens may also hold digits and `_`. A
    prose word in any name makes the value prose. The whole value is at most
    240 characters.
    """
    if len(text) > _MAX_CREDIT_LINE:
        return False
    pieces = _CREDIT_SEPARATOR.split(text)
    if pieces and not pieces[-1]:
        pieces.pop()
    segments = 0
    credits = 0
    open_names: int | None = None
    for piece in pieces:
        open_names = None
        parts = piece.split(",")
        for index, part in enumerate(parts):
            part = part.strip()
            rest = ",".join(parts[index:]).strip()
            tokens = _CREDIT_NAME_TOKEN
            match = _CREDIT_BY.fullmatch(part) or _CREDIT_LABEL.fullmatch(part)
            if match is None and (match := _RIPPER_BY.fullmatch(part)) is not None:
                tokens = _RIPPER_NAME_TOKEN
            if match is not None:
                open_names = _credit_names(match.group(1), tokens)
                if open_names is None:
                    return False
                segments += 1
                credits += 1
            elif _CREDIT_BARE.fullmatch(part):
                open_names = None
                segments += 1
                credits += 1
            elif _CREDIT_PUBLISHER.fullmatch(rest) or _CREDIT_RELEASE.fullmatch(rest):
                segments += 1
                break
            elif _synopsis_format_word(part) or _CREDIT_LENGTH.fullmatch(part):
                open_names = None
                segments += 1
            elif (
                open_names is not None
                and (
                    more := _credit_names(
                        part, max_tokens=_MAX_CONTINUATION_NAME_TOKENS
                    )
                )
                is not None
            ):
                open_names += more
            else:
                return False
            if segments > _MAX_CREDIT_SEGMENTS or (open_names or 0) > _MAX_CREDIT_NAMES:
                return False
    return credits > 0 and segments <= _MAX_CREDIT_SEGMENTS


def _credit_names(
    text: str,
    token_pattern: re.Pattern[str] = _CREDIT_NAME_TOKEN,
    *,
    max_tokens: int = _MAX_CREDIT_NAME_TOKENS,
) -> int | None:
    names = _CREDIT_NAME_JOIN.split(text)
    for name in names:
        tokens = name.split(" ")
        if not 1 <= len(tokens) <= max_tokens or any(
            token_pattern.fullmatch(token) is None or token in _CREDIT_PROSE_WORDS
            for token in tokens
        ):
            return None
    return len(names)


def contributor_credit_prefixed(value: object) -> bool:
    """Whether one contributor member reads as a credit line, not a name.

    The canonical member is a role verb and "by" with or without names
    ("Narrated by Example Reader", "Read by:"), or a role label and a colon
    ("Narrator: Example Reader"), by the synopsis credit grammar. A name that
    merely holds a role word, such as "Byron Read", is a name. Pure, and never
    rewrites the value (#1283).
    """
    if type(value) is not str:
        return False
    text = canonical_text(TargetDestination.NARRATORS, value)
    return any(
        pattern.fullmatch(text) is not None
        for pattern in (_CREDIT_BY, _CREDIT_LABEL, _CREDIT_BARE)
    )


def promotional_announcement(value: object) -> bool:
    """Recognize a whole-segment announcement while retaining descriptive wording."""
    if type(value) is not str:
        return False
    normalized = " ".join(value.split())
    return (
        re.fullmatch(
            r"(?:winner of (?:the )?.+\b(?:award|prize)(?: \d{4})?"
            r"|(?:the |a |an )?(?:(?:international|new york times|sunday times|#1|number one) )?bestseller"
            r"|now (?:a|an) (?:major )?(?:motion picture|film|television series|tv series|netflix series))",
            normalized,
            re.IGNORECASE,
        )
        is not None
    )


# Phrases that sell a book rather than name it: sales and award claims, an
# author's other books, and screen adaptations. Searched anywhere in the
# whitespace-collapsed value, case-insensitively (#1317).
_MARKETING_COPY: Final = re.compile(
    r"\bbest-?sell(?:er|ers|ing)\b"
    r"|\b(?:award|prize)[- ]winn(?:er|ing)\b"
    r"|\bwinner of\b.*\b(?:award|prize)\b"
    r"|\b(?:from|by) the (?:[\w-]+ ){0,2}(?:author|creator|writer)s? of\b"
    r"|\bnow (?:on|streaming|a (?:major )?(?:motion picture|film|movie"
    r"|television series|tv series))\b"
    r"|\binspired the (?:[\w+]+ ){0,3}(?:show|series|film|movie)\b"
    r"|\b(?:netflix|apple tv\+|prime video|hbo max|disney\+|hulu)(?!\w)"
    r"|\bmust-read\b|\bmillion copies\b|(?<!\w)#1(?!\d)",
    re.IGNORECASE,
)


def marketing_copy(value: object) -> bool:
    """Whether one subtitle candidate is marketing copy rather than a subtitle.

    A whole-value announcement counts, and so does any phrase above: a sales
    or award claim, "from the author of", "now on" a streaming service or a
    screen adaptation. Shape only, so a real subtitle that quotes such a
    phrase stays the owner's to choose. Pure, and never raises (#1317).
    """
    return type(value) is str and (
        promotional_announcement(value)
        or _MARKETING_COPY.search(" ".join(value.split())) is not None
    )


# A quoted span and the dash attribution that may follow it. The attribution
# stops at a line end, the next quote, or a full stop that ends a word longer
# than one letter, so an initial stays inside it and a following synopsis
# sentence does not.
_QUOTE_BLOCK: Final = re.compile(
    r'(?:"[^"]+"|“[^“”]+”)'
    r'(?P<attribution>(?:\s*[—–]|\s+-\s)(?:[^"“\n.]|(?<=\b[^\W\d_])\.)*)?'
)
PRAISE_QUOTED_SHARE: Final = 0.5
PRAISE_OPENING: Final = "praise for"


def mostly_quoted_praise(value: object) -> bool:
    """Whether a description is a run of attributed praise, not a synopsis.

    A quote block is one balanced straight or curly double-quoted span with
    the dash attribution after it, read as the comment above `_QUOTE_BLOCK`
    bounds it. The value is praise when its blocks
    hold at least half its non-whitespace characters and at least one block is
    attributed by a dash (an em or en dash, or a spaced hyphen), or when its
    canonical text opens with "praise for". Shape only: an attributed dialogue
    excerpt can read as praise, and the owner may still choose it. Pure, and
    never raises (#1283).
    """
    if type(value) is not str:
        return False
    folded = " ".join(unicodedata.normalize("NFC", value).split()).casefold()
    if folded.startswith(PRAISE_OPENING):
        return True
    blocks = list(_QUOTE_BLOCK.finditer(value))
    quoted = sum(
        not character.isspace() for block in blocks for character in block.group()
    )
    total = sum(not character.isspace() for character in value)
    return (
        total > 0
        and quoted >= PRAISE_QUOTED_SHARE * total
        and any(block.group("attribution") for block in blocks)
    )


def plain_audiobook_synopsis(value: object) -> TypeGuard[str]:
    """Admit plain incumbent/candidate text before any replacement comparison."""
    if type(value) is not str:
        return False
    try:
        canonical_text(TargetDestination.DESCRIPTION, value)
    except ValueError:
        return False
    return (
        "<" not in value
        and ">" not in value
        and not value.rstrip().endswith(DESCRIPTION_VISIBLE_CUTOFFS)
    )


_WORD: Final = re.compile(r"\w+")


def cut_off_synopsis(value: object) -> TypeGuard[str]:
    """Whether a synopsis is plain text that ends in a visible cutoff (#1317)."""
    if (
        type(value) is not str
        or "<" in value
        or ">" in value
        or not value.rstrip().endswith(DESCRIPTION_VISIBLE_CUTOFFS)
    ):
        return False
    try:
        canonical_text(TargetDestination.DESCRIPTION, value)
    except ValueError:
        return False
    return True


def description_extends(cut_off: object, complete: object) -> bool:
    """Whether a visibly cut-off synopsis is the opening of a complete one.

    `cut_off` is plain text ending in a visible cutoff; `complete` passes the
    synopsis floor. Both are read without a leading header, NFC-normalised,
    casefolded and split into words. The cut-off words, except a last word
    that may itself be cut, open the complete text, the last one opens the
    complete word in its place, and the complete text says more. Pure, and
    never raises (#1317).
    """
    if not cut_off_synopsis(cut_off) or not plain_audiobook_synopsis(complete):
        return False
    cut = _synopsis_words(_synopsis_after_header(cut_off))
    full = _synopsis_words(_synopsis_after_header(complete))
    return (
        bool(cut)
        and len(full) >= len(cut)
        and full != cut
        and full[: len(cut) - 1] == cut[:-1]
        and full[len(cut) - 1].startswith(cut[-1])
    )


def synopsis_missing(current: object, desired: object) -> bool:
    """Whether an audiobook incumbent synopsis holds no knowledge to keep.

    A placeholder incumbent -- plain or markup-only text the closed marker
    vocabulary recognises -- is missing, as is a cut-off incumbent that
    `desired` extends, so a safe complete synopsis fills either (#1317).
    Pure, and never raises.
    """
    if type(current) is not str:
        return False
    try:
        placeholder = recognized_synopsis_placeholder(current)
    except ValueError:
        return False
    return (
        placeholder
        and (
            plain_audiobook_synopsis(current)
            or not SYNOPSIS_MARKUP_TAG.sub("", current).strip()
        )
    ) or description_extends(current, desired)


def description_alternative_immaterial(current: object, candidate: object) -> bool:
    """Whether a description alternative only trims or extends a usable incumbent.

    The incumbent must pass the audiobook synopsis floor, so it is plain text
    with no markup, no visible cut-off and no placeholder; an incumbent below
    the floor makes every alternative material. The alternative is read with
    its markup tags removed, both texts are NFC-normalised, casefolded and split
    into words, and the alternative is immaterial when one word sequence equals
    the other or runs inside it contiguously, in either direction. Pure, and
    never raises (#1253).
    """
    if (
        not plain_audiobook_synopsis(current)
        or recognized_synopsis_placeholder(current)
        or type(candidate) is not str
    ):
        return False
    shorter, longer = sorted(
        (_synopsis_words(current), _synopsis_words(candidate)), key=len
    )
    return bool(shorter) and any(
        longer[index : index + len(shorter)] == shorter
        for index in range(len(longer) - len(shorter) + 1)
    )


def genre_alternative_immaterial(current: object, candidate: object) -> bool:
    """Whether a genre alternative only trims or extends a usable incumbent list.

    Format words are removed from both lists first, as the audiobook genre
    floor reads them, and the alternative is immaterial when either remaining
    set contains the other. An empty or format-only incumbent makes every
    alternative material. Pure, and never raises (#1253).
    """
    if audiobook_format_only_genres(current):
        return False
    left = _work_genres(current)
    right = _work_genres(candidate)
    if not left or right is None:
        return False
    return left <= right or right <= left


def _synopsis_words(value: str) -> tuple[str, ...]:
    return tuple(
        _WORD.findall(
            unicodedata.normalize("NFC", SYNOPSIS_MARKUP_TAG.sub(" ", value)).casefold()
        )
    )


def _work_genres(value: object) -> frozenset[str] | None:
    members = _text_sequence(value)
    if members is None:
        return None
    try:
        keys = {canonical_text(TargetDestination.GENRES, member) for member in members}
    except ValueError:
        return None
    return frozenset(key for key in keys if not audiobook_format_word(key))


__all__ = [
    "DESCRIPTION_VISIBLE_CUTOFFS",
    "PRAISE_OPENING",
    "PRAISE_QUOTED_SHARE",
    "SYNOPSIS_MARKUP_TAG",
    "audiobook_format_only_genres",
    "audiobook_format_word",
    "canonical_text",
    "contributor_credit_prefixed",
    "creator_list_reordered",
    "cut_off_synopsis",
    "description_alternative_immaterial",
    "description_extends",
    "genre_alternative_immaterial",
    "marketing_copy",
    "mostly_quoted_praise",
    "plain_audiobook_synopsis",
    "promotional_announcement",
    "recognized_synopsis_placeholder",
    "synopsis_header",
    "synopsis_missing",
    "values_match",
]
