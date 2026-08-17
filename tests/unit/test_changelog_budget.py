from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"

# A changelog entry states one outcome and points at the issue that carries the
# arithmetic, the proof narrative, and the review history. Six lines is what
# that costs; past it the entry has started reproducing the pull request.
LINE_BUDGET = 6


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
