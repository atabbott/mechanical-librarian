from __future__ import annotations

from pathlib import Path

import pytest
from scripts import verify_dependency_authority as checker

ROOT = Path(__file__).resolve().parents[2]

_MANIFEST = """\
[build-system]
requires = ["setuptools==83.0.0"]

[project]
dependencies = [
  "httpx==0.28.1",
  "Pillow==12.3.0",
]

[dependency-groups]
dev = [
  "pytest==9.1.1",
  "ruff==0.16.3",
  "setuptools==83.0.0",
]
"""

_DOCUMENT = """\
# Dependencies

## Review criteria

- **Least authority.** The graph receives nothing beyond the grants below.

## Granted runtime authority

Prose that opens the section and grants nothing.

- **HTTPX and Pillow** carry traffic and decode images, and hold **no write
  authority** of their own.

## Granted development-only authority

- `ruff`, `pytest` and `setuptools` read this repository's own source.
  `setuptools` is the only one of the three that produces a shipped artifact.

## Frontend build graph

Nothing here is a Python dependency.
"""


def build_authority(root: Path, *, manifest: str = _MANIFEST) -> None:
    """Write the smallest pair that reconciles, so a finding is the one defect.

    Every test starts from a tree the checker passes, which is what makes a
    finding attributable to the single change that test introduced rather than
    to a gap in the fixture.
    """

    (root / "docs" / "development").mkdir(parents=True)
    (root / "pyproject.toml").write_text(manifest, encoding="utf-8")
    (root / "docs" / "development" / "dependencies.md").write_text(
        _DOCUMENT, encoding="utf-8"
    )


def test_the_fixture_reconciles_before_any_test_breaks_it(tmp_path: Path) -> None:
    build_authority(tmp_path)
    assert checker.authority_findings(tmp_path) == ()
    assert set(checker.declared_dependencies(tmp_path)) == {
        "httpx",
        "pillow",
        "pytest",
        "ruff",
        "setuptools",
    }


def test_a_dependency_added_without_a_grant_is_a_finding(tmp_path: Path) -> None:
    """The exact omission this check exists for.

    #737 added `coverage` and `mutmut` to the development group and left the
    authority document describing neither. Every gate passed and a reviewer
    caught it by eye, which is the failure mode being removed here (#769).
    """

    build_authority(
        tmp_path,
        manifest=_MANIFEST.replace(
            '  "ruff==0.16.3",', '  "coverage==7.13.0",\n  "ruff==0.16.3",'
        ),
    )
    assert checker.authority_findings(tmp_path) == (
        "pyproject.toml declares coverage, which has no grant in"
        " docs/development/dependencies.md",
    )


def test_a_runtime_dependency_and_the_build_backend_both_count(
    tmp_path: Path,
) -> None:
    """A document claiming completeness cannot carry an exception.

    The build backend is declared under `build-system` rather than in a group,
    and it is what produces the shipped wheel; skipping it would make the
    completeness claim untrue in the one place it matters most.
    """

    build_authority(
        tmp_path,
        manifest=_MANIFEST.replace('requires = ["setuptools==83.0.0"]', "requires = []")
        .replace('  "setuptools==83.0.0",\n', "")
        .replace(" and `setuptools`", "")
        .replace("one of the three", "one of the two"),
    )
    findings = checker.authority_findings(tmp_path)
    assert findings == (
        "docs/development/dependencies.md grants authority to setuptools,"
        " which pyproject.toml does not declare",
    )


def test_a_grant_that_outlived_its_dependency_is_a_finding(tmp_path: Path) -> None:
    build_authority(tmp_path, manifest=_MANIFEST.replace('  "Pillow==12.3.0",\n', ""))
    assert checker.authority_findings(tmp_path) == (
        "docs/development/dependencies.md grants authority to Pillow,"
        " which pyproject.toml does not declare",
    )


def test_names_reconcile_in_their_normalised_form(tmp_path: Path) -> None:
    """`Pillow`, `pillow` and `argon2_cffi` are one name, not two.

    The manifest and the document are written by different hands for different
    readers, so the reconciliation is on PEP 503 names rather than on spelling.
    """

    build_authority(
        tmp_path, manifest=_MANIFEST.replace('"Pillow==12.3.0"', '"pillow==12.3.0"')
    )
    assert checker.authority_findings(tmp_path) == ()
    assert checker.normalised("Argon2_CFFI") == "argon2-cffi"


def test_only_the_leading_run_of_a_grant_names_its_subjects() -> None:
    """Emphasis inside a grant's prose is not a second dependency.

    A grant emphasises what it is granting -- `**production writes**`,
    `**patch**` -- for the reader's benefit, and reading those as subjects
    would report a finding for every well-written grant in the document.
    """

    assert checker.grant_subjects(
        "**HTTPX and HTTPcore** carry traffic, including **production writes**"
    ) == ("HTTPX", "HTTPcore")
    assert checker.grant_subjects("**Pillow** (MIT-CMU, compatible) decodes") == (
        "Pillow",
    )
    assert checker.grant_subjects("`ruff`, `mypy` and `pytest` read and analyse") == (
        "ruff",
        "mypy",
        "pytest",
    )
    # The review criteria use the same bullet shape and name no dependency,
    # which is why the sections are addressed by heading rather than the
    # document being scanned for bullets.
    assert "Least authority." not in checker.granted_dependencies(ROOT)


def test_a_grants_own_arithmetic_is_checked_against_its_subjects(
    tmp_path: Path,
) -> None:
    """Stale arithmetic in a completeness document is the defect in miniature."""

    build_authority(tmp_path)
    document = tmp_path / "docs" / "development" / "dependencies.md"
    document.write_text(
        _DOCUMENT.replace("one of the three", "one of the four"), encoding="utf-8"
    )
    assert checker.authority_findings(tmp_path) == (
        "docs/development/dependencies.md says 'the four' of a grant covering 3:"
        " ruff, pytest, setuptools",
    )
    # And the count follows the subjects rather than being a constant: dropping
    # one from both sides leaves the arithmetic wrong even though the two
    # directions above now reconcile.
    document.write_text(
        _DOCUMENT.replace("`ruff`, `pytest` and ", "`ruff` and "), encoding="utf-8"
    )
    assert checker.authority_findings(tmp_path) == (
        "pyproject.toml declares pytest, which has no grant in"
        " docs/development/dependencies.md",
        "docs/development/dependencies.md says 'the three' of a grant covering 2:"
        " ruff, setuptools",
    )


def test_a_cardinal_that_quantifies_its_own_noun_is_left_alone(
    tmp_path: Path,
) -> None:
    """`one of the five permitted read paths` counts paths, not dependencies.

    The HTTPX grant states that count today. A rule that read every cardinal
    as a claim about the grant's subjects would fail the gate on an accurate
    document, which is the one outcome a completeness check cannot afford.
    """

    build_authority(tmp_path)
    document = tmp_path / "docs" / "development" / "dependencies.md"
    document.write_text(
        _DOCUMENT.replace(
            "carry traffic",
            "carry traffic on one of the five permitted read paths",
        ),
        encoding="utf-8",
    )
    assert checker.authority_findings(tmp_path) == ()


@pytest.mark.parametrize(
    ("removed", "message"),
    [
        ("pyproject.toml", "pyproject.toml is missing"),
        (
            "docs/development/dependencies.md",
            "the dependency authority docs/development/dependencies.md is missing",
        ),
    ],
    ids=("manifest", "document"),
)
def test_a_missing_input_fails_closed(
    tmp_path: Path, removed: str, message: str
) -> None:
    build_authority(tmp_path)
    (tmp_path / removed).unlink()
    with pytest.raises(checker.DependencyAuthorityError, match=message):
        checker.authority_findings(tmp_path)


def test_a_restructured_document_fails_rather_than_reporting_nothing(
    tmp_path: Path,
) -> None:
    """A section renamed away must not silently take its grants with it.

    A checker that reads no grants reports no findings, which looks exactly
    like a document in perfect order.
    """

    build_authority(tmp_path)
    document = tmp_path / "docs" / "development" / "dependencies.md"
    document.write_text(
        _DOCUMENT.replace("## Granted runtime authority", "## Runtime authority"),
        encoding="utf-8",
    )
    with pytest.raises(checker.DependencyAuthorityError, match="Granted runtime"):
        checker.authority_findings(tmp_path)


def test_this_repository_reconciles() -> None:
    """The check the gate runs, run against the tree it runs against."""

    assert checker.authority_findings(ROOT) == ()
