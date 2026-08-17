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

    A bullet is a line matching `- ` at column 0 plus every immediately
    following indented continuation line. Anything else ends it: a blank line,
    the next bullet, a heading, or unindented prose. That last case is the one
    that matters -- the file closes with a document-level paragraph, and a rule
    that ran to the next `- ` or `#` would charge those lines to the final
    bullet and report a violation nobody wrote.
    """

    lines = source.splitlines()
    bullets: list[tuple[int, tuple[str, ...]]] = []
    position = 0
    while position < len(lines):
        if not lines[position].startswith("- "):
            position += 1
            continue
        end = position + 1
        while end < len(lines) and lines[end].startswith("  "):
            end += 1
        bullets.append((position + 1, tuple(lines[position:end])))
        position = end
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


def test_reports_the_first_line_of_each_offending_entry() -> None:
    source = "- Fits. (#1)\n" + "- Runs long.\n" + "  continues\n" * LINE_BUDGET
    over = [body[0] for _, body in top_level_bullets(source) if len(body) > LINE_BUDGET]
    assert over == ["- Runs long."]
