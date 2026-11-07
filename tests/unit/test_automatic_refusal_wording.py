"""Prove an automatic refusal reads in one sentence on every screen (#1765).

The review case reads `_AUTOMATIC_REFUSAL_TEXT`; the policy preview reads the
browser's `automaticRefusalReasons`. A state is said in the same words on
every page, so the two tables hold the same codes and the same sentences.
"""

from __future__ import annotations

import re
from pathlib import Path

from mechanical_librarian.application.review_web import _AUTOMATIC_REFUSAL_TEXT

_LABELS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "labels.ts"
_ENTRY = re.compile(r"(\w+):\s*(['\"])(.*?)\2,", re.DOTALL)


def _browser_reasons() -> dict[str, str]:
    source = _LABELS.read_text(encoding="utf-8")
    start = source.index("export const automaticRefusalReasons")
    block = source[source.index("{", start) + 1 : source.index("\n}\n", start)]
    return {code: text for code, _, text in _ENTRY.findall(block)}


def test_browser_and_review_case_word_each_refusal_alike() -> None:
    assert _browser_reasons() == _AUTOMATIC_REFUSAL_TEXT
