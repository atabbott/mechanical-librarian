from __future__ import annotations

from pathlib import Path

import pytest
from scripts import verify_documentation_integrity as checker

ROOT = Path(__file__).resolve().parents[2]


def build_corpus(root: Path) -> None:
    """Write the smallest corpus that satisfies all four checks.

    Every test below starts from a tree that passes, so a finding it then
    asserts is caused by the one defect that test introduced and not by an
    incidental gap in the fixture.
    """

    (root / "docs" / "architecture" / "decisions").mkdir(parents=True)
    (root / "AGENTS.md").write_text(
        "# Agents\n\n- [Operations](docs/operations.md)\n", encoding="utf-8"
    )
    (root / "docs" / "README.md").write_text(
        "# Documentation\n\n"
        "- [Agents](../AGENTS.md)\n"
        "- [Decisions](architecture/decisions/README.md)\n",
        encoding="utf-8",
    )
    (root / "docs" / "operations.md").write_text("# Operations\n", encoding="utf-8")
    (root / "docs" / "architecture" / "decisions" / "README.md").write_text(
        "# Decisions\n\n"
        "| ADR | Status | Decision |\n"
        "| --- | --- | --- |\n"
        "| [0001](0001-first.md) | accepted | The first decision |\n"
        "| [0003](0003-third.md) | accepted | The third decision |\n\n"
        "Use [the template](template.md) for new decisions.\n",
        encoding="utf-8",
    )
    (root / "docs" / "architecture" / "decisions" / "template.md").write_text(
        "# Template\n", encoding="utf-8"
    )
    for name in ("0001-first.md", "0003-third.md"):
        (root / "docs" / "architecture" / "decisions" / name).write_text(
            f"# {name}\n", encoding="utf-8"
        )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_corpus(tmp_path)
    return tmp_path


def test_a_well_formed_corpus_reports_nothing(corpus: Path) -> None:
    assert checker.integrity_findings(corpus) == ()


def test_the_repository_corpus_itself_is_intact() -> None:
    """The guarantee the probes proved, kept after the probes were removed.

    Binding the checker to this repository's own tree is what makes the fast
    tier fail on an orphan or a broken link, rather than only the gate step
    that runs the script.
    """

    assert checker.integrity_findings(ROOT) == ()


def test_an_orphaned_document_is_reported(corpus: Path) -> None:
    (corpus / "docs" / "stranded.md").write_text("# Stranded\n", encoding="utf-8")
    assert checker.integrity_findings(corpus) == (
        "docs/stranded.md is not reachable from docs/README.md",
    )


def test_a_broken_relative_link_is_reported(corpus: Path) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[missing](./no-such-document.md)\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/operations.md links to missing ./no-such-document.md",
    )


def test_reachability_is_transitive_and_runs_through_root_documents(
    corpus: Path,
) -> None:
    """`docs/README.md` reaches `AGENTS.md`, which reaches `docs/operations.md`.

    Only `AGENTS.md` links the operations document, so a checker that walked
    `docs/` alone would call it an orphan.
    """

    (corpus / "docs" / "README.md").write_text(
        "# Documentation\n\n"
        "- [Agents](../AGENTS.md)\n"
        "- [Decisions](architecture/decisions/README.md)\n",
        encoding="utf-8",
    )
    reached = checker.reachable_documents(corpus)
    assert Path("docs/operations.md") in reached
    assert checker.integrity_findings(corpus) == ()


def test_a_document_reached_only_through_two_hops_is_not_an_orphan(
    corpus: Path,
) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[Deeper](deeper.md)\n", encoding="utf-8"
    )
    (corpus / "docs" / "deeper.md").write_text("# Deeper\n", encoding="utf-8")
    assert checker.integrity_findings(corpus) == ()


def test_a_fragment_resolves_against_the_document_it_names(corpus: Path) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[Agents](../AGENTS.md#agents)\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == ()


def test_code_is_quoted_rather_than_linked(corpus: Path) -> None:
    """A path shown as an example is neither followed nor required to exist."""

    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n"
        "Inline `[example](./absent.md)` is a quotation.\n\n"
        "```markdown\n[fenced](./also-absent.md)\n```\n",
        encoding="utf-8",
    )
    assert (
        checker.relative_targets(
            (corpus / "docs" / "operations.md").read_text(encoding="utf-8")
        )
        == ()
    )
    assert checker.integrity_findings(corpus) == ()


def test_a_link_hidden_in_a_code_span_cannot_rescue_an_orphan(corpus: Path) -> None:
    """The other direction of the same rule, which is the one that matters."""

    (corpus / "docs" / "stranded.md").write_text("# Stranded\n", encoding="utf-8")
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\nSee `[Stranded](stranded.md)`.\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/stranded.md is not reachable from docs/README.md",
    )


@pytest.mark.parametrize(
    "target",
    ("https://example.invalid/page", "http://example.invalid/", "mailto:a@b.invalid"),
    ids=("https", "http", "mailto"),
)
def test_no_target_outside_the_filesystem_is_ever_resolved(
    corpus: Path, target: str
) -> None:
    """The check is offline: a network target is skipped, never fetched."""

    (corpus / "docs" / "operations.md").write_text(
        f"# Operations\n\n[out]({target})\n",
        encoding="utf-8",
    )
    assert (
        checker.relative_targets(
            (corpus / "docs" / "operations.md").read_text(encoding="utf-8")
        )
        == ()
    )
    assert checker.integrity_findings(corpus) == ()


def test_an_own_document_anchor_is_not_a_path(corpus: Path) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[section](#operations)\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == ()


def test_an_adr_file_without_an_index_row_is_reported(corpus: Path) -> None:
    decisions = corpus / "docs" / "architecture" / "decisions"
    (decisions / "0004-fourth.md").write_text("# Fourth\n", encoding="utf-8")
    assert checker.adr_index_findings(corpus) == (
        "docs/architecture/decisions/0004-fourth.md has no row in "
        "docs/architecture/decisions/README.md",
    )


def test_an_index_row_without_a_file_is_reported(corpus: Path) -> None:
    (corpus / "docs" / "architecture" / "decisions" / "0003-third.md").unlink()
    assert checker.adr_index_findings(corpus) == (
        "docs/architecture/decisions/README.md row 0003 names missing 0003-third.md",
    )


def test_a_gap_in_the_adr_sequence_is_history_rather_than_a_defect(
    corpus: Path,
) -> None:
    """0002 is absent from the fixture exactly as 0023 is absent upstream.

    The reconciliation is by filename in both directions, so a decision that
    was never written stays never written instead of being demanded into
    existence.
    """

    assert not (
        corpus / "docs" / "architecture" / "decisions" / "0002-second.md"
    ).exists()
    assert checker.integrity_findings(corpus) == ()


def test_two_files_claiming_one_adr_number_are_reported(corpus: Path) -> None:
    decisions = corpus / "docs" / "architecture" / "decisions"
    (decisions / "0003-third-again.md").write_text("# Again\n", encoding="utf-8")
    assert (
        "ADR number 0003 is claimed by 0003-third-again.md, 0003-third.md"
        in checker.duplicate_adr_numbers(corpus)
    )


def test_two_index_rows_claiming_one_adr_number_are_reported(corpus: Path) -> None:
    index = corpus / "docs" / "architecture" / "decisions" / "README.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "| [0003](0003-third.md) | accepted | The third decision |\n",
            "| [0003](0003-third.md) | accepted | The third decision |\n"
            "| [0003](0003-third.md) | accepted | Claimed twice |\n",
        ),
        encoding="utf-8",
    )
    assert checker.duplicate_adr_numbers(corpus) == (
        "docs/architecture/decisions/README.md holds 2 rows for ADR number 0003",
    )


def test_a_decision_file_carrying_no_number_is_reported(corpus: Path) -> None:
    decisions = corpus / "docs" / "architecture" / "decisions"
    (decisions / "notes.md").write_text("# Notes\n", encoding="utf-8")
    assert (
        "docs/architecture/decisions holds notes.md, which carries no ADR number"
        in checker.duplicate_adr_numbers(corpus)
    )


def test_a_missing_entry_point_fails_rather_than_passing_vacuously(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    with pytest.raises(checker.DocumentationIntegrityError, match="docs/README.md"):
        checker.reachable_documents(tmp_path)


def test_a_missing_adr_index_fails_rather_than_passing_vacuously(
    corpus: Path,
) -> None:
    (corpus / "docs" / "architecture" / "decisions" / "README.md").unlink()
    with pytest.raises(checker.DocumentationIntegrityError, match="ADR index"):
        checker.adr_index_findings(corpus)


def test_the_command_reports_its_findings_and_fails(
    corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (corpus / "docs" / "stranded.md").write_text("# Stranded\n", encoding="utf-8")
    assert checker.main(["--root", str(corpus)]) == 1
    captured = capsys.readouterr()
    assert "1 finding(s)" in captured.err
    assert "docs/stranded.md is not reachable" in captured.err


def test_the_command_succeeds_on_an_intact_corpus(
    corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checker.main(["--root", str(corpus)]) == 0
    assert "no findings" in capsys.readouterr().out


@pytest.mark.parametrize(
    "link",
    (
        '[guide](./absent.md "details")',
        "[guide](./absent.md 'details')",
        "[guide](./absent.md (details))",
    ),
    ids=("double_quoted", "single_quoted", "parenthesised"),
)
def test_a_titled_link_to_a_missing_target_is_reported(corpus: Path, link: str) -> None:
    """A title is ordinary Markdown, so a titled target is read, not skipped.

    The regression this pins: an earlier target pattern had to run to the
    closing parenthesis, so a titled link matched nothing at all and a broken
    one passed in silence.
    """

    (corpus / "docs" / "operations.md").write_text(
        f"# Operations\n\n{link}\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/operations.md links to missing ./absent.md",
    )


def test_a_titled_link_reaches_the_document_it_names(corpus: Path) -> None:
    (corpus / "docs" / "operations.md").write_text(
        '# Operations\n\n[Deeper](deeper.md "why it exists")\n', encoding="utf-8"
    )
    (corpus / "docs" / "deeper.md").write_text("# Deeper\n", encoding="utf-8")
    assert checker.relative_targets(
        (corpus / "docs" / "operations.md").read_text(encoding="utf-8")
    ) == ("deeper.md",)
    assert checker.integrity_findings(corpus) == ()


def test_a_reference_style_link_is_reported_rather_than_ignored(corpus: Path) -> None:
    """Not resolved, but never skipped: the corpus writes targets inline."""

    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[guide][manual]\n\n[manual]: ./absent.md\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/operations.md uses a reference-style link [guide][manual];"
        " write the target inline",
        "docs/operations.md defines the link reference [manual];"
        " write the target inline",
    )


def test_a_collapsed_reference_link_is_reported_too(corpus: Path) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[manual][]\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/operations.md uses a reference-style link [manual][];"
        " write the target inline",
    )


def test_a_link_the_checker_cannot_read_is_reported_rather_than_skipped(
    corpus: Path,
) -> None:
    """The residue rule, which covers the forms nobody thought to name.

    Any `](` the inline pattern did not consume is a link written in a shape
    the pattern cannot read. Reporting it is what keeps `every relative link
    resolves` worth what it claims.
    """

    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[a link](two words.md)\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/operations.md has a link this checker cannot read:"
        " [a link](two words.md)",
    )


def test_a_parenthesised_target_is_reported_rather_than_half_read(
    corpus: Path,
) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n[a link](nested(parens).md)\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == (
        "docs/operations.md has a link this checker cannot read:"
        " [a link](nested(parens).md)",
    )


def test_an_unreadable_link_inside_code_stays_a_quotation(corpus: Path) -> None:
    (corpus / "docs" / "operations.md").write_text(
        "# Operations\n\n```markdown\n[a link](two words.md)\n```\n", encoding="utf-8"
    )
    assert checker.integrity_findings(corpus) == ()


def test_an_index_row_naming_the_wrong_decision_number_is_reported(
    corpus: Path,
) -> None:
    """A row can link the right file under the wrong number and pass both
    directions of the filename reconciliation.

    Renumbering a duplicate and adding missing rows by hand -- both done in
    this milestone -- is exactly the operation that produces this.
    """

    index = corpus / "docs" / "architecture" / "decisions" / "README.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "| [0003](0003-third.md) |", "| [0002](0003-third.md) |"
        ),
        encoding="utf-8",
    )
    assert checker.adr_index_findings(corpus) == (
        "docs/architecture/decisions/README.md row 0002 names 0003-third.md,"
        " which is ADR 0003",
    )
