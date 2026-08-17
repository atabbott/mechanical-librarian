#!/usr/bin/env python3
"""Verify the live documentation corpus is reachable, linked, and indexed.

Four checks, and deliberately only four. `docs/README.md` claims to list every
live document, and `docs/architecture/decisions/README.md` claims to index
every decision; both claims were true only for as long as somebody re-read the
tree by hand. The 2026-08-15 audit found ten orphaned documents and four
missing index rows, so the claims are enforced here instead:

1. every live document under `docs/` is reachable from `docs/README.md`,
   directly or through documents that are themselves reachable;
2. every relative link in a live document resolves to a path that exists, and
   every link is written in a form this checker can actually read;
3. every ADR file has an index row, every index row names a file, and every
   row's declared number is the number of the file it links to;
4. no two ADRs share a number.

The corpus is `docs/**/*.md` plus the repository-root `*.md` files, which is
exactly what `docs/README.md` says it lists. Files under `.github/` and
`.impeccable/` are templates for tooling rather than documents that corpus
claims, so they are outside it.

Nothing here opens a socket. An `http`, `https`, or `mailto` target is skipped
by construction rather than fetched, so the check is offline and stays that way
whatever a document links to.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ADR_DIRECTORY = Path("docs") / "architecture" / "decisions"
ADR_INDEX = ADR_DIRECTORY / "README.md"
CORPUS_ROOT = Path("docs")
CORPUS_ENTRY_POINT = CORPUS_ROOT / "README.md"

# A decision file is `NNNN-slug.md`. The index and the template describe the
# decisions rather than being decisions, so they are named out explicitly.
_ADR_FILENAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
_ADR_INDEX_ROW = re.compile(r"^\|\s*\[(\d+)\]\(([^)]+)\)\s*\|")
_NOT_A_DECISION = frozenset({"README.md", "template.md"})

# `[text](target)` and the `![alt](target)` image form, with the optional
# Markdown title in any of its three spellings. The title is matched so that a
# titled link is read rather than skipped: an earlier revision required the
# target to run to the closing parenthesis, which made `[a](b.md "why")` match
# nothing at all, so a broken titled target passed silently.
# `_unsupported_link_forms` below is the guard that stops that class of hole
# recurring, for the forms this pattern still does not read.
_INLINE_LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*([^()\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)"
)
# Reference-style links -- `[text][label]` or `[text][]` -- and the definitions
# they resolve through. This corpus writes every target inline, and these are
# reported rather than resolved: resolving them means implementing enough of
# the Markdown link algorithm to tell a shortcut reference `[label]` from
# ordinary bracketed prose, which is a parser rather than a checker. Reporting
# the definition is what closes the shortcut form too, because without a
# definition a shortcut is not a link at all.
_REFERENCE_LINK = re.compile(r"!?\[[^\]]*\]\[[^\]]*\]")
_REFERENCE_DEFINITION = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*\S")
_LINK_OPENING = re.compile(r"\]\(")
_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`]*`")

# Targets that name something outside the filesystem. A bare `#anchor` points
# within its own document, and this checker deliberately does not verify
# anchors -- that is a heading check, not a link check.
_NON_FILE_PREFIXES = ("http://", "https://", "mailto:", "#")


class DocumentationIntegrityError(RuntimeError):
    """The corpus contradicts what its own index documents claim."""


def live_documents(root: Path) -> tuple[Path, ...]:
    """Every document the corpus claims, as paths relative to `root`."""

    documents = {path for path in root.glob("*.md")}
    documents.update((root / CORPUS_ROOT).rglob("*.md"))
    return tuple(sorted(path.relative_to(root) for path in documents))


def _strip_code(text: str) -> str:
    """Drop fenced blocks and inline spans, which quote rather than link.

    A path inside a code span is an example being shown, not a reference being
    followed. Counting one would make an accurate document fail, and following
    one would let an inaccurate document pass by hiding its links in backticks.
    """

    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        opening = _FENCE.match(line)
        if fence is None:
            if opening is not None:
                fence = opening.group(1)
                continue
            lines.append(_INLINE_CODE.sub("``", line))
            continue
        if opening is not None and opening.group(1) == fence:
            fence = None
    return "\n".join(lines)


def relative_targets(text: str) -> tuple[str, ...]:
    """Every path-naming target `_INLINE_LINK` reads from `text`, in order.

    This is what the pattern reads, which is not the same claim as every link
    the document contains. `_unsupported_link_forms` reports the difference, so
    a target missing from here is a finding somewhere rather than a silence.
    """

    targets = [
        target
        for target in _INLINE_LINK.findall(_strip_code(text))
        if not target.startswith(_NON_FILE_PREFIXES)
    ]
    # Duplicates carry no further information: the same target either resolves
    # or does not, and reporting it once per occurrence buries the other finds.
    return tuple(dict.fromkeys(targets))


def _resolved(root: Path, document: Path, target: str) -> Path:
    """The path `target` names, read from the document that links to it."""

    path, _, _ = unquote(target).partition("#")
    return (root / document).parent / path


def _excerpt(text: str, position: int) -> str:
    """The line `position` falls on, trimmed to something a finding can carry."""

    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    line = text[start : end if end != -1 else len(text)].strip()
    return line if len(line) <= 72 else f"{line[:69]}..."


def _unsupported_link_forms(text: str) -> tuple[str, ...]:
    """Every link this checker will not resolve, so none is silently skipped.

    A checker that quietly ignores a syntax reports green for reach it does not
    have, which is the failure this whole script exists to stop. Two named
    forms are reported by name, and the residue rule behind them catches the
    unnamed ones: any `](` the inline pattern did not consume is a link written
    in a shape the pattern cannot read, whatever that shape turns out to be.
    """

    stripped = _strip_code(text)
    findings = [
        f"uses a reference-style link {match.group(0)}; write the target inline"
        for match in _REFERENCE_LINK.finditer(stripped)
    ]
    findings.extend(
        f"defines the link reference [{match.group(1)}]; write the target inline"
        for line in stripped.splitlines()
        for match in [_REFERENCE_DEFINITION.match(line)]
        if match is not None
    )
    spans = [match.span() for match in _INLINE_LINK.finditer(stripped)]
    findings.extend(
        f"has a link this checker cannot read: {_excerpt(stripped, match.start())}"
        for match in _LINK_OPENING.finditer(stripped)
        if not any(start <= match.start() < end for start, end in spans)
    )
    return tuple(findings)


def link_findings(root: Path) -> tuple[str, ...]:
    """Report every relative link that does not resolve, and every one that
    cannot be read.

    The second half is what makes the first half's guarantee true. `Every
    relative link resolves` is only worth as much as the set of links the
    extractor sees, so a form it cannot parse is a finding rather than a skip.
    """

    findings: list[str] = []
    for document in live_documents(root):
        text = (root / document).read_text(encoding="utf-8")
        findings.extend(
            f"{document.as_posix()} {finding}"
            for finding in _unsupported_link_forms(text)
        )
        for target in relative_targets(text):
            if not _resolved(root, document, target).exists():
                findings.append(f"{document.as_posix()} links to missing {target}")
    return tuple(findings)


def reachable_documents(root: Path) -> frozenset[Path]:
    """Every live document reachable from the corpus entry point.

    Reachability is transitive and runs through the whole live corpus, not just
    `docs/`: the entry point lists `AGENTS.md`, so a document that only
    `AGENTS.md` links to is reached, and is listed where its reader will be.
    """

    corpus = set(live_documents(root))
    if CORPUS_ENTRY_POINT not in corpus:
        raise DocumentationIntegrityError(
            f"the corpus entry point {CORPUS_ENTRY_POINT.as_posix()} is missing"
        )
    reached = {CORPUS_ENTRY_POINT}
    pending = [CORPUS_ENTRY_POINT]
    while pending:
        document = pending.pop()
        text = (root / document).read_text(encoding="utf-8")
        for target in relative_targets(text):
            resolved = _resolved(root, document, target)
            if not resolved.exists():
                continue
            linked = resolved.resolve().relative_to(root.resolve())
            if linked in corpus and linked not in reached:
                reached.add(linked)
                pending.append(linked)
    return frozenset(reached)


def orphaned_documents(root: Path) -> tuple[str, ...]:
    reached = reachable_documents(root)
    return tuple(
        f"{document.as_posix()} is not reachable from {CORPUS_ENTRY_POINT.as_posix()}"
        for document in live_documents(root)
        if document.parts[0] == CORPUS_ROOT.name and document not in reached
    )


def _indexed_adrs(root: Path) -> tuple[tuple[str, str], ...]:
    index = root / ADR_INDEX
    if not index.is_file():
        raise DocumentationIntegrityError(
            f"the ADR index {ADR_INDEX.as_posix()} is missing"
        )
    rows = (
        _ADR_INDEX_ROW.match(line)
        for line in index.read_text(encoding="utf-8").splitlines()
    )
    return tuple((row.group(1), row.group(2)) for row in rows if row is not None)


def adr_index_findings(root: Path) -> tuple[str, ...]:
    """Reconcile decision files against index rows, in both directions.

    The reconciliation is by filename, never by arithmetic over the numbers: a
    gap in the sequence is a decision that was never written, which is a
    historical fact and not a defect this can repair by inventing a document.

    A row's own declared number is checked against the number in the file it
    links to, which is a separate claim from either direction of the filename
    reconciliation. A row reading `[0002](0003-third.md)` links to a file that
    exists and covers a file that has a row, so both directions pass while the
    index presents the decision under the wrong number. This milestone
    renumbered a duplicate ADR-0050 and added four missing rows by hand, which
    is exactly the operation that produces this shape of defect.
    """

    directory = root / ADR_DIRECTORY
    files = {
        path.name for path in directory.glob("*.md") if path.name not in _NOT_A_DECISION
    }
    indexed = _indexed_adrs(root)
    linked = {target for _, target in indexed}
    findings = [
        f"{(ADR_DIRECTORY / name).as_posix()} has no row in {ADR_INDEX.as_posix()}"
        for name in sorted(files - linked)
    ]
    findings.extend(
        f"{ADR_INDEX.as_posix()} row {number} names missing {target}"
        for number, target in indexed
        if not (directory / target).is_file()
    )
    for number, target in indexed:
        named = _ADR_FILENAME.match(target)
        if named is not None and named.group(1) != number:
            findings.append(
                f"{ADR_INDEX.as_posix()} row {number} names {target},"
                f" which is ADR {named.group(1)}"
            )
    return tuple(findings)


def duplicate_adr_numbers(root: Path) -> tuple[str, ...]:
    """A number two decisions both claim identifies neither of them."""

    directory = root / ADR_DIRECTORY
    numbered: dict[str, list[str]] = {}
    unnumbered: list[str] = []
    for path in sorted(directory.glob("*.md")):
        if path.name in _NOT_A_DECISION:
            continue
        match = _ADR_FILENAME.match(path.name)
        if match is None:
            unnumbered.append(path.name)
            continue
        numbered.setdefault(match.group(1), []).append(path.name)
    findings = [
        f"{ADR_DIRECTORY.as_posix()} holds {name}, which carries no ADR number"
        for name in unnumbered
    ]
    findings.extend(
        f"ADR number {number} is claimed by {', '.join(names)}"
        for number, names in sorted(numbered.items())
        if len(names) > 1
    )
    rows: dict[str, int] = {}
    for number, _ in _indexed_adrs(root):
        rows[number] = rows.get(number, 0) + 1
    findings.extend(
        f"{ADR_INDEX.as_posix()} holds {count} rows for ADR number {number}"
        for number, count in sorted(rows.items())
        if count > 1
    )
    return tuple(findings)


def integrity_findings(root: Path) -> tuple[str, ...]:
    """Every finding, links first: an orphan is often a broken link upstream."""

    return (
        *link_findings(root),
        *orphaned_documents(root),
        *adr_index_findings(root),
        *duplicate_adr_numbers(root),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to verify (default: this checkout)",
    )
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    try:
        findings = integrity_findings(root)
    except DocumentationIntegrityError as error:
        print(f"documentation integrity: {error}", file=sys.stderr)
        return 1
    if findings:
        print(f"documentation integrity: {len(findings)} finding(s)", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    documents = len(live_documents(root))
    print(f"documentation integrity: {documents} live documents, no findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
