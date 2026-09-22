from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mechanical_librarian"
WORKBENCH_ROOT = (
    Path(__file__).parents[2] / "workbench" / "mechanical_librarian_workbench"
)
LAYERS = {
    "interfaces",
    "application",
    "domain",
    "adapters",
    "infrastructure",
    "bootstrap",
}
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
    return (
        parts[1]
        if len(parts) > 1 and parts[0] == "mechanical_librarian" and parts[1] in LAYERS
        else None
    )


def test_production_source_obeys_dependency_direction() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        origin = _module(path)
        origin_layer = _layer(origin)
        for target in _imports(path):
            target_layer = _layer(target)
            if target.startswith("tests") or (
                origin_layer
                and target_layer
                and target_layer not in ALLOWED[origin_layer]
            ):
                violations.append(f"{origin} imports {target}")
    assert violations == []


def test_source_only_workbench_does_not_import_production_adapters() -> None:
    violations: list[str] = []
    for path in sorted(WORKBENCH_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [value.name for value in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            else:
                continue
            if any(
                target == "mechanical_librarian.adapters"
                or target.startswith("mechanical_librarian.adapters.")
                for target in targets
            ):
                violations.append(str(path.relative_to(WORKBENCH_ROOT)))
    assert violations == []


def test_epub_child_keeps_one_private_archive_session() -> None:
    child = (PACKAGE_ROOT / "bootstrap" / "epub_child.py").read_text(encoding="utf-8")
    assert child.count("zipfile.ZipFile(") == 1
    assert "EpubPackageExtractor().extract(package_request, archive)" in child
    assert "EpubOpeningExtractor().extract_with_selection(" in child
    assert "_candidate_graph(" not in child
    assert "package_report.to_json" not in child


def test_superseded_runtime_and_central_ports_are_absent() -> None:
    absent = (
        "application/ports.py",
        "application/live_title_proposals.py",
        "application/hardcover_identity.py",
        "application/inventory_polling.py",
        "application/job_heartbeat.py",
        "application/contained_reviewer.py",
        "application/shadow_reviews.py",
        "adapters/reviewers/codex_shadow.py",
        "adapters/persistence/jobs.py",
        "adapters/persistence/title_proposals.py",
        "infrastructure/persistence/sqlite.py",
        "domain/inventory.py",
        "domain/review.py",
        "domain/workflow.py",
    )
    assert [value for value in absent if (PACKAGE_ROOT / value).exists()] == []


def test_http_interface_receives_only_composed_review_application() -> None:
    interface = (PACKAGE_ROOT / "interfaces" / "web" / "app.py").read_text(
        encoding="utf-8"
    )
    application = (PACKAGE_ROOT / "application" / "review_application.py").read_text(
        encoding="utf-8"
    )
    review_services = (PACKAGE_ROOT / "application" / "review_web.py").read_text(
        encoding="utf-8"
    )

    assert "ReviewWebRepository" not in interface
    assert "_SerializedReviewRepository" not in interface
    assert "repository." not in interface
    assert "application: ReviewApplication" in interface
    assert "class ReviewApplication" in application
    assert "class ReviewWebRepository" not in review_services


def _registered_commands(path: Path) -> list[str]:
    """Every command name passed to `add_parser`, read from the syntax tree.

    A text search for `add_parser("name"` finds a registration only while it
    stays on one physical line, so it silently stops finding one the moment a
    formatter wraps the call -- which is how a command could be re-added
    without this proof noticing. The names are read as literals instead, so the
    proof holds whatever shape the source takes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def test_runtime_has_only_the_closed_installed_command_set() -> None:
    commands = _registered_commands(PACKAGE_ROOT / "interfaces" / "cli" / "__init__.py")
    assert sorted(commands) == sorted(
        (
            "review-serve",
            "policy-preview",
            "cover-review",
            "review-activity",
            "provider-recovery",
            "qualification-state-bridge",
        )
    )
    for unsupported in (
        "metadata-report",
        "proposal-set",
        "review-case",
        "catalogue",
        "batch",
        "shadow-review",
        "reviewer-auth",
    ):
        assert not [name for name in commands if name.startswith(unsupported)]
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in PACKAGE_ROOT.rglob("*.py")
    )
    assert "DELETE FROM metadata_reports" not in source
    assert "UPDATE metadata_reports" not in source
