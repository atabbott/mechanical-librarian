#!/usr/bin/env python3
"""Keep the compatibility page's version identifiers equal to the code.

`docs/reference/compatibility.md` names the exact version of every document,
store and response this build accepts. Each backticked `<name>-vN` on that page
is the current value of one constant in `CURRENT_VERSIONS`, so a version move
edits the constant and its pin (`tests/architecture/test_document_versions.py`)
and then regenerates the page:

    uv run python scripts/compatibility_versions.py --write

`scripts/verify_documentation_integrity.py` fails while an identifier is stale,
names a document this list does not define, or a listed document is missing
from the page. The descriptions beside the identifiers stay hand-written and
name no number of their own document, so a move that keeps a document's
meaning edits none of them (#1847).
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from pathlib import Path

PAGE = Path("docs") / "reference" / "compatibility.md"
_CHECKOUT = Path(__file__).resolve().parents[1]

# (owning module, constant), in page order. A document joins when the page
# first names it.
CURRENT_VERSIONS: tuple[tuple[str, str], ...] = (
    ("mechanical_librarian.application.configuration", "CONFIG_SCHEMA"),
    (
        "mechanical_librarian.infrastructure.settings_authority",
        "REVIEW_BOOTSTRAP_SCHEMA",
    ),
    ("mechanical_librarian.domain.owner_settings", "OWNER_SETTINGS_SCHEMA"),
    ("mechanical_librarian.domain.setup_state", "SETUP_STATE_SCHEMA"),
    ("mechanical_librarian.domain.reviewer_access", "REVIEWER_ACCESS_SCHEMA"),
    ("mechanical_librarian.domain.metadata_report", "REPORT_SCHEMA"),
    ("mechanical_librarian.domain.proposal_set", "PROPOSAL_SCHEMA"),
    ("mechanical_librarian.domain.proposal_set", "POLICY_VERSION"),
    ("mechanical_librarian.domain.metadata_report", "COMPARISON_VERSION"),
    ("mechanical_librarian.domain.metadata_policy", "POLICY_SCHEMA_VERSION"),
    ("mechanical_librarian.domain.review_case", "REVIEW_CASE_SCHEMA"),
    ("mechanical_librarian.domain.proposal_set", "BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA"),
    (
        "mechanical_librarian.adapters.providers.google_books_metadata",
        "GOOGLE_BOOKS_HTML_VISIBLE_TEXT_VERSION",
    ),
    ("mechanical_librarian.domain.shadow_review", "PACKET_SCHEMA"),
    ("mechanical_librarian.domain.shadow_decision", "DECISION_SET_SCHEMA"),
    ("mechanical_librarian.domain.shadow_decision", "DECISION_POLICY_VERSION"),
    ("mechanical_librarian.domain.shadow_review_actions", "ACTION_POLICY_VERSION"),
    ("mechanical_librarian.domain.shadow_outcome", "SHADOW_OUTCOME_SCHEMA"),
    ("mechanical_librarian.domain.human_decision", "HUMAN_DECISION_SCHEMA"),
    ("mechanical_librarian.domain.human_decision", "HUMAN_DECISION_EVENT_SCHEMA"),
    ("mechanical_librarian.application.review_web", "HUMAN_DECISION_ACK_SCHEMA"),
    ("mechanical_librarian.application.cover_consideration", "SCHEMA_VERSION"),
    ("mechanical_librarian.application.cover_identity", "SCHEMA_VERSION"),
    ("mechanical_librarian.application.cover_policy", "POLICY_SCHEMA_VERSION"),
    ("mechanical_librarian.application.cover_policy", "SCHEMA_VERSION"),
    ("mechanical_librarian.domain.cover_automatic_decision", "SCHEMA_VERSION"),
    (
        "mechanical_librarian.domain.human_decision",
        "AUTOMATIC_COVER_SELECTION_AUTHORITY_SCHEMA",
    ),
    ("mechanical_librarian.domain.human_decision", "COVER_SELECTION_AUTHORITY_SCHEMA"),
    (
        "mechanical_librarian.application.cover_taste_contract",
        "COVER_TASTE_BUILD_PACKET_SCHEMA",
    ),
    ("mechanical_librarian.domain.cover_taste", "COVER_TASTE_PROFILE_SCHEMA"),
    (
        "mechanical_librarian.adapters.persistence.field_effect_validation",
        "FIELD_EFFECT_CONSENT_STATE_SCHEMA",
    ),
    (
        "mechanical_librarian.adapters.persistence.field_effect_activity",
        "FIELD_EFFECT_CONSENT_ENVELOPE_SCHEMA",
    ),
    ("mechanical_librarian.domain.field_effect", "FIELD_EFFECT_PLAN_SCHEMA"),
    ("mechanical_librarian.domain.field_effect", "FIELD_EFFECT_OPERATION_SCHEMA"),
    ("mechanical_librarian.domain.field_effect", "FIELD_EFFECT_EVENT_SCHEMA"),
    ("mechanical_librarian.adapters.persistence.catalogue", "CATALOGUE_SCHEMA"),
    ("mechanical_librarian.domain.audio_inspection", "AUDIO_INSPECTION_SCHEMA"),
    ("mechanical_librarian.domain.epub_inspection", "EPUB_INSPECTION_SCHEMA"),
    ("mechanical_librarian.application.batch", "BATCH_MANIFEST_SCHEMA"),
    ("scripts.cold_start_report", "COLD_START_REPORT_SCHEMA"),
    ("scripts.work_route_measurement", "WORK_ROUTE_MEASUREMENT_SCHEMA"),
    ("mechanical_librarian.interfaces.web.react_assets", "REVIEW_REACT_ASSETS_SCHEMA"),
    ("mechanical_librarian.interfaces.web.app", "REVIEW_SESSION_SCHEMA"),
    ("mechanical_librarian.application.review_web", "REVIEW_LIBRARY_SCHEMA"),
    (
        "mechanical_librarian.application.review_application",
        "REVIEW_PREPARATION_SCHEMA",
    ),
    ("mechanical_librarian.application.review_web", "REVIEW_CASE_DETAIL_SCHEMA"),
    ("mechanical_librarian.application.review_web", "REVIEW_CASE_EVIDENCE_SCHEMA"),
    ("mechanical_librarian.application.review_web", "REVIEW_REPORT_DETAIL_SCHEMA"),
    ("mechanical_librarian.application.review_web", "REVIEW_CASE_HISTORY_SCHEMA"),
    ("mechanical_librarian.application.review_web", "REVIEW_ATTENTION_SCHEMA"),
    (
        "mechanical_librarian.application.continuous_field_effects",
        "CONTINUOUS_FIELD_ACTIVITY_SCHEMA",
    ),
)

# A backticked identifier: the document's name, then its version number.
_IDENTIFIER = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)*)-v([1-9][0-9]*)`")


def _document(label: str) -> str:
    return label.rsplit("-v", 1)[0]


def current_versions(
    registry: tuple[tuple[str, str], ...] = CURRENT_VERSIONS,
) -> tuple[str, ...]:
    """Each registered constant's current label, read from the code."""

    if str(_CHECKOUT) not in sys.path:
        # `scripts.*` resolves as a namespace package from the checkout; the
        # checkout root holds no product package, so nothing is shadowed.
        sys.path.append(str(_CHECKOUT))
    labels: list[str] = []
    for module, constant in registry:
        value = getattr(importlib.import_module(module), constant)
        # A `SchemaVersion` wraps its label in `.value`; a constant is the label.
        label = getattr(value, "value", value)
        if not isinstance(label, str) or _IDENTIFIER.fullmatch(f"`{label}`") is None:
            raise ValueError(f"{module}.{constant} is not a version label")
        labels.append(label)
    return tuple(labels)


def reconcile(text: str, labels: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """The page with each stale identifier made current, and what cannot be.

    A stale identifier takes the one current version of its document that the
    page does not yet name. A document with two current versions (cover
    selections) is matched as a set, so either may move on its own.
    """

    by_document: dict[str, list[str]] = {}
    for label in labels:
        by_document.setdefault(_document(label), []).append(label)
    problems: list[str] = []

    def current(match: re.Match[str]) -> str:
        label = f"{match.group(1)}-v{match.group(2)}"
        versions = by_document.get(match.group(1))
        if versions is None:
            problems.append(
                f"{PAGE}: `{label}` is not a version this build defines; "
                "add its constant to CURRENT_VERSIONS in "
                "scripts/compatibility_versions.py"
            )
            return match.group(0)
        if label in versions:
            return match.group(0)
        unnamed = [version for version in versions if f"`{version}`" not in text]
        if len(unnamed) != 1:
            problems.append(
                f"{PAGE}: `{label}` is stale and matches no single current "
                f"version of {match.group(1)} ({', '.join(versions)})"
            )
            return match.group(0)
        return f"`{unnamed[0]}`"

    updated = _IDENTIFIER.sub(current, text)
    problems.extend(
        f"{PAGE}: `{label}` is current but the page does not name it"
        for label in labels
        if f"`{label}`" not in updated
    )
    return updated, tuple(problems)


def version_findings(root: Path) -> tuple[str, ...]:
    """Every way the page differs from the code; empty when it is current."""

    text = (root / PAGE).read_text(encoding="utf-8")
    updated, problems = reconcile(text, current_versions())
    stale = (
        ()
        if updated == text
        else (
            f"{PAGE}: version identifiers are stale; run "
            "`uv run python scripts/compatibility_versions.py --write`",
        )
    )
    return (*stale, *problems)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help="rewrite stale identifiers in place"
    )
    parser.add_argument("--root", type=Path, default=_CHECKOUT)
    arguments = parser.parse_args(argv)
    path = arguments.root / PAGE
    text = path.read_text(encoding="utf-8")
    updated, problems = reconcile(text, current_versions())
    for problem in problems:
        print(problem, file=sys.stderr)
    if updated == text:
        return 1 if problems else 0
    if not arguments.write:
        print(
            f"{PAGE}: version identifiers are stale; run with --write", file=sys.stderr
        )
        return 1
    path.write_text(updated, encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
