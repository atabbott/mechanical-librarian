from __future__ import annotations

import ast
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mechanical_librarian"
PRODUCTION_LAYERS = {
    "domain",
    "application",
    "adapters",
    "infrastructure",
    "interfaces",
    "bootstrap",
}
ROOT_MODULES = {"__init__", "__main__", "_version"}
ALLOWED_LAYER_IMPORTS = {
    "domain": {"domain"},
    "application": {"application", "domain"},
    "adapters": {"adapters", "application", "domain"},
    "infrastructure": {"infrastructure", "domain"},
    "interfaces": {"interfaces", "application", "domain"},
    "bootstrap": PRODUCTION_LAYERS,
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["mechanical_librarian", *parts])


def _resolve_import(
    module: str,
    imported: ast.ImportFrom,
    *,
    is_package: bool,
) -> str:
    if imported.level == 0:
        return imported.module or ""

    module_parts = module.split(".")
    package_parts = module_parts if is_package else module_parts[:-1]
    keep = len(package_parts) - imported.level + 1
    base = package_parts[: max(keep, 0)]
    suffix = imported.module.split(".") if imported.module else []
    return ".".join([*base, *suffix])


def _layer(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "mechanical_librarian":
        return None
    return parts[1] if parts[1] in PRODUCTION_LAYERS else None


def violates_boundary(origin: str, target: str) -> bool:
    target_parts = target.split(".")
    if target_parts[0] in {"tests", "scripts"} or "tests" in target_parts:
        return True

    origin_layer = _layer(origin)
    target_layer = _layer(target)
    if origin_layer is None or target_layer is None:
        return False
    if target_layer not in ALLOWED_LAYER_IMPORTS[origin_layer]:
        return True

    if origin_layer == target_layer == "adapters":
        origin_parts = origin.split(".")
        if len(origin_parts) < 3 or len(target_parts) < 3:
            return False
        return origin_parts[2] != target_parts[2]

    return False


def _imports(path: Path) -> list[str]:
    module = _module_name(path)
    is_package = path.name == "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import(module, node, is_package=is_package)
            imported_modules.append(base)
            imported_modules.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return imported_modules


class ImportBoundaryTests(unittest.TestCase):
    def test_production_source_obeys_dependency_direction(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            origin = _module_name(path)
            for target in _imports(path):
                if violates_boundary(origin, target):
                    violations.append(f"{origin} imports {target}")

        self.assertEqual(violations, [])

    def test_every_top_level_package_is_classified(self) -> None:
        unclassified = sorted(
            path.name
            for path in PACKAGE_ROOT.iterdir()
            if path.is_dir()
            and (path / "__init__.py").exists()
            and path.name not in PRODUCTION_LAYERS
        )
        root_modules = sorted(
            path.stem
            for path in PACKAGE_ROOT.glob("*.py")
            if path.stem not in ROOT_MODULES
        )

        self.assertEqual(unclassified, [])
        self.assertEqual(root_modules, [])

    def test_domain_cannot_import_application(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.domain.entity",
                "mechanical_librarian.application.service",
            )
        )

    def test_application_cannot_import_infrastructure(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.application.service",
                "mechanical_librarian.infrastructure.runtime",
            )
        )

    def test_adapter_cannot_import_sibling_adapter(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.adapters.providers.catalogue",
                "mechanical_librarian.adapters.reviewers.backend",
            )
        )

    def test_adapter_cannot_import_infrastructure(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.adapters.persistence.sqlite",
                "mechanical_librarian.infrastructure.configuration",
            )
        )

    def test_persistence_adapter_can_implement_application_repository_port(self) -> None:
        self.assertFalse(
            violates_boundary(
                "mechanical_librarian.adapters.persistence.audit",
                "mechanical_librarian.application.audit",
            )
        )

    def test_task_execution_adapter_can_implement_application_port(self) -> None:
        self.assertFalse(
            violates_boundary(
                "mechanical_librarian.adapters.task_execution.worker",
                "mechanical_librarian.application.ports",
            )
        )

    def test_infrastructure_cannot_implement_persistence_port(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.infrastructure.persistence.audit",
                "mechanical_librarian.application.audit",
            )
        )

    def test_infrastructure_cannot_implement_task_execution_port(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.infrastructure.jobs.worker",
                "mechanical_librarian.application.ports",
            )
        )

    def test_interface_cannot_import_concrete_persistence(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.interfaces.http.routes",
                "mechanical_librarian.adapters.persistence.sqlite",
            )
        )

    def test_ordinary_layer_cannot_import_bootstrap(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.interfaces.cli",
                "mechanical_librarian.bootstrap",
            )
        )

    def test_bootstrap_can_import_concrete_layers(self) -> None:
        self.assertFalse(
            violates_boundary(
                "mechanical_librarian.bootstrap",
                "mechanical_librarian.adapters.persistence.sqlite",
            )
        )

    def test_production_cannot_import_test_fixtures(self) -> None:
        self.assertTrue(
            violates_boundary(
                "mechanical_librarian.application.service",
                "tests.fixtures.items",
            )
        )


if __name__ == "__main__":
    unittest.main()
