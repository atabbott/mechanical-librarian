#!/usr/bin/env python3
"""Verify every direct dependency carries an authority grant.

`docs/development/dependencies.md` is the single dependency authority created
by #684 and states completeness over direct dependencies. Nothing enforced it.
The completeness check run during that delivery was a one-off performed by
hand, and the document went stale inside a day: #737 added `coverage` and
`mutmut` to the development group with no grants and every gate passed, the
omission being caught by eye during review (#769). That is the same failure
`scripts/verify_documentation_integrity.py` was written to remove one layer up,
and this is its sibling.

Three checks, in both directions plus the arithmetic:

1. every direct dependency `pyproject.toml` declares -- runtime, every
   dependency group, and the build backend -- carries a grant;
2. every grant names a dependency `pyproject.toml` still declares;
3. every bare cardinal a grant states about its own subjects is the number of
   subjects that grant has. A count repeated in prose is arithmetic that goes
   stale, which is the same defect in a smaller form.

Grant *quality* is deliberately out of scope: that a grant exists is
machine-checkable, that it is true is not. So are licences and transitive
packages -- the document is explicitly about direct dependencies. Nothing here
opens a socket or resolves anything; the manifest and the document are the only
inputs.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

DEPENDENCY_AUTHORITY = Path("docs") / "development" / "dependencies.md"
MANIFEST = Path("pyproject.toml")

# The two sections that grant authority. The review criteria above them use the
# same bullet shape for `- **Least authority.**` and its siblings, which name
# no dependency, so the sections are addressed by heading rather than the whole
# document being scanned for bullets.
GRANT_SECTIONS = (
    "Granted runtime authority",
    "Granted development-only authority",
)

# A grant opens by naming its subjects and then writes prose: `- **Pillow**
# (MIT-CMU, ...`, `- **HTTPX and HTTPcore** carry ...`, `- `ruff`, `mypy`,
# `pytest` and `setuptools` read ...`. Only the leading run is read, so the
# emphasis a grant uses later for `**production writes**` or `**patch**` is
# never mistaken for a second subject.
_SPAN = re.compile(r"\*\*([^*]+)\*\*|`([^`]+)`")
_CONNECTIVE = re.compile(r"\s*(?:,|and|/|&)\s*")
_NAME_SEPARATOR = re.compile(r",| and ")
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_CARDINALS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
# A cardinal standing on its own -- "the only one of the four that produces a
# shipped artifact" -- is a pronoun for the grant's subjects and is checked
# against them. A cardinal that quantifies its own noun -- "one of the five
# permitted read paths" -- counts something this checker knows nothing about
# and is left alone, which is why the lookahead requires a relative pronoun or
# punctuation rather than any word at all.
_BARE_CARDINAL = re.compile(
    r"\bthe (" + "|".join(_CARDINALS) + r")\b"
    r"(?=\s*(?:that\b|which\b|who\b|whose\b|[,.;:]|$))"
)


class DependencyAuthorityError(RuntimeError):
    """The manifest and the dependency authority cannot be reconciled."""


def normalised(name: str) -> str:
    """The PEP 503 form, so `Argon2-cffi` and `argon2_cffi` are one name."""

    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def declared_dependencies(root: Path) -> dict[str, str]:
    """Every direct dependency the manifest declares, normalised to as written.

    The build backend counts. `pyproject.toml` names it under `build-system`
    rather than under a group, but it is pinned exactly, it produces the
    shipped wheel, and a document claiming completeness over direct
    dependencies that skipped it would be claiming something untrue.
    """

    manifest = root / MANIFEST
    if not manifest.is_file():
        raise DependencyAuthorityError(f"{MANIFEST.as_posix()} is missing")
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    project = data.get("project", {})
    groups = data.get("dependency-groups", {})
    build = data.get("build-system", {})
    specifications: list[str] = [
        *project.get("dependencies", []),
        *build.get("requires", []),
    ]
    for entries in groups.values():
        # A group may include another group as `{include-group = "name"}`,
        # which declares no distribution of its own.
        specifications.extend(entry for entry in entries if isinstance(entry, str))
    declared: dict[str, str] = {}
    for specification in specifications:
        match = _REQUIREMENT_NAME.match(specification.strip())
        if match is None:
            raise DependencyAuthorityError(
                f"{MANIFEST.as_posix()} declares an unreadable requirement"
                f" {specification!r}"
            )
        declared.setdefault(normalised(match.group(0)), match.group(0))
    return declared


def _section_lines(text: str, heading: str) -> tuple[str, ...]:
    lines = text.splitlines()
    for position, line in enumerate(lines):
        if line.strip() != f"## {heading}":
            continue
        body: list[str] = []
        for following in lines[position + 1 :]:
            if following.startswith("## "):
                break
            body.append(following)
        return tuple(body)
    raise DependencyAuthorityError(
        f"{DEPENDENCY_AUTHORITY.as_posix()} has no '## {heading}' section"
    )


def grant_items(text: str) -> tuple[str, ...]:
    """Each grant in the two granted-authority sections, continuations joined.

    A grant is a top-level list item plus every indented line under it, so a
    claim written in a grant's second paragraph still belongs to that grant.
    The sections' opening prose is not a grant and names no subject, so it is
    not read as one.
    """

    items: list[list[str]] = []
    for heading in GRANT_SECTIONS:
        collecting = False
        for line in _section_lines(text, heading):
            if line.startswith("- "):
                items.append([line.removeprefix("- ")])
                collecting = True
                continue
            if not collecting:
                continue
            if not line.strip() or line.startswith("  "):
                items[-1].append(line.strip())
                continue
            collecting = False
    return tuple(" ".join(part for part in item if part) for item in items)


def grant_subjects(item: str) -> tuple[str, ...]:
    """The dependency names one grant is about, in the order it names them."""

    names: list[str] = []
    rest = item
    while (span := _SPAN.match(rest)) is not None:
        text = span.group(1) if span.group(1) is not None else span.group(2)
        names.extend(
            part.strip() for part in _NAME_SEPARATOR.split(text) if part.strip()
        )
        rest = rest[span.end() :]
        connective = _CONNECTIVE.match(rest)
        if connective is None:
            break
        rest = rest[connective.end() :]
    return tuple(names)


def granted_dependencies(root: Path) -> dict[str, str]:
    """Every dependency the authority grants, normalised to as written."""

    document = root / DEPENDENCY_AUTHORITY
    if not document.is_file():
        raise DependencyAuthorityError(
            f"the dependency authority {DEPENDENCY_AUTHORITY.as_posix()} is missing"
        )
    text = document.read_text(encoding="utf-8")
    granted: dict[str, str] = {}
    for item in grant_items(text):
        for name in grant_subjects(item):
            granted.setdefault(normalised(name), name)
    return granted


def coverage_findings(root: Path) -> tuple[str, ...]:
    declared = declared_dependencies(root)
    granted = granted_dependencies(root)
    findings = [
        f"{MANIFEST.as_posix()} declares {declared[name]},"
        f" which has no grant in {DEPENDENCY_AUTHORITY.as_posix()}"
        for name in sorted(declared.keys() - granted.keys())
    ]
    findings.extend(
        f"{DEPENDENCY_AUTHORITY.as_posix()} grants authority to {granted[name]},"
        f" which {MANIFEST.as_posix()} does not declare"
        for name in sorted(granted.keys() - declared.keys())
    )
    return tuple(findings)


def stated_count_findings(root: Path) -> tuple[str, ...]:
    """Every grant whose own arithmetic no longer matches its subjects."""

    document = root / DEPENDENCY_AUTHORITY
    findings: list[str] = []
    for item in grant_items(document.read_text(encoding="utf-8")):
        subjects = grant_subjects(item)
        findings.extend(
            f"{DEPENDENCY_AUTHORITY.as_posix()} says 'the {word}' of a grant"
            f" covering {len(subjects)}: {', '.join(subjects)}"
            for word in _BARE_CARDINAL.findall(item)
            if _CARDINALS[word] != len(subjects)
        )
    return tuple(findings)


def authority_findings(root: Path) -> tuple[str, ...]:
    return (*coverage_findings(root), *stated_count_findings(root))


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
        findings = authority_findings(root)
        covered = len(declared_dependencies(root))
    except DependencyAuthorityError as error:
        print(f"dependency authority: {error}", file=sys.stderr)
        return 1
    if findings:
        print(f"dependency authority: {len(findings)} finding(s)", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print(f"dependency authority: {covered} direct dependencies, all granted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
