from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"

# A changelog entry states one outcome and points at the issue that carries the
# arithmetic, the proof narrative, and the review history. Six lines is what
# that costs; past it the entry has started reproducing the pull request.
LINE_BUDGET = 6

# The other half of that bargain: an entry may be short only because the issue
# it names holds everything it left out, so an entry naming no issue has
# nowhere to have left anything. One or more issues in parentheses, which is
# the form every entry written since #717 uses. A bare `#757` in mid-sentence
# is a mention of some other issue, not this entry's reference.
ISSUE_REFERENCE = re.compile(r"\(#\d+(?:,\s*#\d+)*\)")

# The 39 entries that predate the convention, pinned by the exact first line of
# each. They are grandfathered by identity rather than by a count, because a
# count is not a rule: with a ceiling of 39, adding one unreferenced entry in
# the same change that references an old one leaves the total at 39 and passes.
# Naming them has no such hole -- a new entry is not on this list whatever
# happens to the ones that are.
#
# The list can only shrink. The stale-allowance test below fails on any line
# here that CHANGELOG.md no longer carries unreferenced, so giving one of these
# entries its issue reference, rewriting it, or deleting it also deletes its
# line below. The allowance cannot outlive what it excuses, and nothing may
# ever be added to it.
GRANDFATHERED_WITHOUT_A_REFERENCE = frozenset(
    {
        "- Qualified metadata-policy defaults now create immutable, decision-only",
        "- Add closed, canonical metadata-policy draft and sparse per-library preview",
        "- Add the qualification-only isolated Codex shadow-review path for one exact",
        "- Add exact-current atomic review admission with immutable undecided cases,",
        "- Preserve safe Audiobookshelf target values that exceed their existing field",
        "- Advance the exact-current report/database contract to `metadata-report-v8`.",
        "- Preserve malformed present Open Library cover IDs as the bounded report",
        "- Preserve valid Open Library metadata when optional cover provenance context",
        "- Fail closed during no-ISBN work fallback when a candidate title or subtitle",
        "- Activate immutable explainable ebook proposals. `proposal-set-create`",
        "- Expand the immutable ebook evidence report to `metadata-report-v7`: preserve",
        "- Expand the report-only cover gallery to at most eight provider sources and",
        "- Make public installed-artifact qualification exact-source: build with caches",
        "- Add independent, report-only Open Library evidence for an exact usable ISBN",
        "- Add adapter-specific `config-v4`, `metadata-report-v6`, and",
        "- Limit Open Library foreground batches to at most five explicit entries before",
        "- Add a synthetic, aggregate-only EPUB evidence recoverability audit contract.",
        "- Add the read-only current Audiobookshelf book catalogue with bounded refresh,",
        "- Correct catalogue refresh retention, empty plural ebook classification,",
        "- Add report-only foreground catalogue evidence-batch preview, immutable",
        "- Add report-only, source-attributed ABS, EPUB-declared, and bounded Hardcover",
        "- Expand the bounded configured Hardcover observation to persist ordered rich",
        "- Preserve each bounded Hardcover candidate's opaque provider-local work",
        "- Classify every retained ISBN claim as accepted, associated, unrelated,",
        "- Keep ISBN acceptance claim-local: only explicitly designated package",
        "- Keep provider-work ISBN collisions unresolved: an accepted ISBN family is a",
        "- Preserve source-spine slot positions for bounded local EPUB rear evidence and",
        "- Preserve validated package and opening evidence when the optional bounded",
        "- Preserve local EPUB source/DOM evidence order, explicit source capabilities,",
        "- Preserve duplicate EPUB occurrences with bounded v2 ordinals.",
        "- Exclude package `bkp` book-producer credits from bibliographic contributors",
        "- Admit standards-valid signed-offset publication date-times from structured",
        "- Preserve explicit opening-matter publisher and imprint evidence as distinct",
        "- Require every 0.5 increment to accept only an absent database or its exact",
        "- Added `metadata-report-v2` local-field evidence: complete ordered field",
        "- Activated the clean-state 0.4 neutral metadata-evidence runtime. At the 0.4",
        "- Added explicit named target connections, libraries, read-only mounts, and",
        "- Added scoped current-item Audiobookshelf reading, the isolated EPUB inspector,",
        "- Removed the pre-0.4 inventory, workflow, job, preview, proposal, review,",
    }
)


def top_level_bullets(source: str) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Every top-level bullet in `source`, with the 1-based line it starts on.

    A bullet starts at `- ` in column 0. What continues it follows Markdown
    rather than this file's house style, because the budget is only as honest
    as the shortest way around it:

    - after a content line, an unindented line that is not a new `- ` bullet
      and not a `#` heading is a lazy continuation and belongs to the item;
    - after a blank line, an indented line continues the item as a second
      paragraph, and an unindented line ends it.

    Reading only indented lines would let an author drop the budget to two
    counted lines by inserting one blank, or to one by writing flush left. The
    blank-then-unindented rule is what still ends the final bullet at the
    document footer, so that case stays correctly attributed. Trailing blank
    lines belong to no item and are never counted.
    """

    lines = source.splitlines()
    bullets: list[tuple[int, tuple[str, ...]]] = []
    position = 0
    while position < len(lines):
        if not lines[position].startswith("- "):
            position += 1
            continue
        cursor = position + 1
        after_blank = False
        while cursor < len(lines):
            line = lines[cursor]
            if not line.strip():
                after_blank = True
                cursor += 1
                continue
            if line.startswith("- ") or line.startswith("#"):
                break
            if after_blank and not line[:1].isspace():
                break
            after_blank = False
            cursor += 1
        end = cursor
        while end > position + 1 and not lines[end - 1].strip():
            end -= 1
        bullets.append((position + 1, tuple(lines[position:end])))
        position = cursor
    return tuple(bullets)


def unreferenced_entries(source: str) -> tuple[str, ...]:
    """The first line of every entry in `source` that names no issue.

    The whole entry is searched, not only its first line: the reference
    conventionally closes the entry, so it usually sits on the last one.
    """

    return tuple(
        body[0]
        for _, body in top_level_bullets(source)
        if not ISSUE_REFERENCE.search("\n".join(body))
    )


def test_no_top_level_bullet_exceeds_the_line_budget() -> None:
    bullets = top_level_bullets(CHANGELOG.read_text(encoding="utf-8"))
    assert bullets, "CHANGELOG.md has no top-level bullets to check"
    over = [
        f"  line {start}: {body[0]} ({len(body)} lines)"
        for start, body in bullets
        if len(body) > LINE_BUDGET
    ]
    assert not over, (
        f"CHANGELOG.md entries over {LINE_BUDGET} lines:\n"
        + "\n".join(over)
        + "\nState the outcome and leave the accounting to the referenced"
        " issue or pull request."
    )


def test_trailing_document_paragraph_is_not_charged_to_the_last_bullet() -> None:
    source = (
        "- One outcome. (#1)\n"
        "  Its second line.\n"
        "\n"
        "No tag, release, deployment, or production write authority is created\n"
        "by this development checkpoint.\n"
    )
    bullets = top_level_bullets(source)
    assert len(bullets) == 1
    assert bullets[0] == (1, ("- One outcome. (#1)", "  Its second line."))


def test_a_blank_separated_paragraph_stays_inside_the_item() -> None:
    """One blank line must not reset the count to the paragraph before it."""

    source = "- One outcome.\n  Its second line.\n\n  Its fourth line.\n  Its fifth.\n"
    bullets = top_level_bullets(source)
    assert len(bullets) == 1
    assert len(bullets[0][1]) == 5


def test_a_lazy_unindented_continuation_stays_inside_the_item() -> None:
    """Flush-left prose after a bullet is that bullet, not a new document."""

    source = "- One outcome.\n" + "".join(f"line {n}\n" for n in range(2, 8))
    bullets = top_level_bullets(source)
    assert len(bullets) == 1
    assert len(bullets[0][1]) == 7


def test_reports_the_first_line_of_each_offending_entry() -> None:
    source = "- Fits. (#1)\n" + "- Runs long.\n" + "  continues\n" * LINE_BUDGET
    over = [body[0] for _, body in top_level_bullets(source) if len(body) > LINE_BUDGET]
    assert over == ["- Runs long."]


def test_every_entry_names_the_issue_its_accounting_lives_in() -> None:
    unreferenced = set(unreferenced_entries(CHANGELOG.read_text(encoding="utf-8")))
    new = sorted(unreferenced - GRANDFATHERED_WITHOUT_A_REFERENCE)
    assert not new, (
        "CHANGELOG.md entries naming no issue:\n"
        + "\n".join(f"  {line}" for line in new)
        + "\nEnd the entry with its issue, as `(#123)` or `(#12, #34)`. The"
        " budget above is affordable only because the issue holds the rest."
    )


def test_the_allowance_holds_only_entries_that_still_need_it() -> None:
    """The allowance shrinks with the debt and never outlives it.

    An entry that gains its reference, is rewritten, or is deleted leaves a
    line here excusing nothing, and a stale excuse is how an allowance quietly
    becomes a permission. Deleting the line is the whole fix.
    """

    unreferenced = set(unreferenced_entries(CHANGELOG.read_text(encoding="utf-8")))
    stale = sorted(GRANDFATHERED_WITHOUT_A_REFERENCE - unreferenced)
    assert not stale, (
        "GRANDFATHERED_WITHOUT_A_REFERENCE excuses entries CHANGELOG.md no"
        " longer carries unreferenced:\n"
        + "\n".join(f"  {line}" for line in stale)
        + "\nDelete each one from the allowance."
    )


def test_a_new_entry_naming_no_issue_is_refused() -> None:
    source = "- Referenced. (#1)\n- Not referenced.\n  Its second line.\n"
    assert unreferenced_entries(source) == ("- Not referenced.",)


def test_a_reference_on_the_closing_line_counts_for_the_whole_entry() -> None:
    source = "- One outcome,\n  stated over two lines. (#738)\n"
    assert unreferenced_entries(source) == ()


def test_several_issues_in_one_reference_satisfy_the_rule() -> None:
    source = "- One outcome across three tickets. (#683, #684, #685)\n"
    assert unreferenced_entries(source) == ()


def test_a_mid_sentence_mention_of_another_issue_is_not_a_reference() -> None:
    """`#757` in prose names what the entry discusses, not what owns it."""

    source = "- Prose describing what #757 removed now states what the code does.\n"
    assert unreferenced_entries(source) == (source.rstrip("\n"),)
