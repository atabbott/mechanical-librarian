from __future__ import annotations

import ast
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mechanical_librarian"
LAYERS = {"interfaces", "application", "domain", "adapters", "infrastructure", "bootstrap"}
ALLOWED = {
    "domain": {"domain"},
    "application": {"application", "domain"},
    "adapters": {"adapters", "application", "domain"},
    "infrastructure": {"infrastructure", "application", "domain"},
    "interfaces": {"interfaces", "application", "domain"},
    "bootstrap": LAYERS,
}


def _module(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("mechanical_librarian", *parts))


def _imports(path: Path) -> list[str]:
    origin = _module(path).split(".")
    is_package = path.name == "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(value.name for value in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = origin if is_package else origin[:-1]
                base = package[: len(package) - node.level + 1]
                target = ".".join((*base, *(node.module or "").split(".")))
            else:
                target = node.module or ""
            result.append(target)
    return result


def _layer(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "mechanical_librarian" and parts[1] in LAYERS else None


class ImportBoundaryTests(unittest.TestCase):
    def test_production_source_obeys_dependency_direction(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            origin = _module(path)
            origin_layer = _layer(origin)
            for target in _imports(path):
                target_layer = _layer(target)
                if target.startswith("tests") or (origin_layer and target_layer and target_layer not in ALLOWED[origin_layer]):
                    violations.append(f"{origin} imports {target}")
        self.assertEqual(violations, [])

    def test_epub_child_keeps_one_private_archive_session(self) -> None:
        child = (PACKAGE_ROOT / "bootstrap" / "epub_child.py").read_text(encoding="utf-8")
        self.assertEqual(child.count("zipfile.ZipFile("), 1)
        self.assertIn("EpubPackageExtractor().extract(package_request, archive)", child)
        self.assertIn("EpubOpeningExtractor().extract(", child)
        self.assertNotIn("package_report.to_json", child)

    def test_superseded_runtime_and_central_ports_are_absent(self) -> None:
        absent = (
            "application/ports.py",
            "application/live_title_proposals.py",
            "application/hardcover_identity.py",
            "application/inventory_polling.py",
            "application/job_heartbeat.py",
            "application/contained_reviewer.py",
            "application/shadow_reviews.py",
            "adapters/reviewers/codex_shadow.py",
            "adapters/reviewers/contained_reviewer.py",
            "adapters/persistence/jobs.py",
            "adapters/persistence/title_proposals.py",
            "infrastructure/persistence/sqlite.py",
            "domain/inventory.py",
            "domain/review.py",
            "domain/workflow.py",
        )
        self.assertEqual([value for value in absent if (PACKAGE_ROOT / value).exists()], [])

    def test_runtime_has_one_cli_command_and_no_write_authority(self) -> None:
        cli = (PACKAGE_ROOT / "interfaces" / "cli" / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(cli.count('add_parser("review-serve"'), 1)
        for unsupported in ('add_parser("metadata-report"', 'add_parser("proposal-set', 'add_parser("review-case', 'add_parser("catalogue"', 'add_parser("batch"', 'add_parser("shadow-review', 'add_parser("reviewer-auth'):
            self.assertNotIn(unsupported, cli)
        source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_ROOT.rglob("*.py"))
        self.assertNotIn("DELETE FROM metadata_reports", source)
        self.assertNotIn("UPDATE metadata_reports", source)


if __name__ == "__main__":
    unittest.main()
