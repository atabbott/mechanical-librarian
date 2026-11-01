"""Report the span, decisions and nested defs of every bootstrap def and class.

`tests/architecture/test_bootstrap_ratchet.py` pins spans and sizes but not
decisions, so a split or a residual review reads them here. A split keeps the
decision and nested-def sums of the pieces that stay RESIDUAL keys at or below
the sums of the key it replaces. Pass the outermost keys with `--keys` to get
the sums; a nested key's lines are already inside its outermost key.

The span is the def's physical lines from its first decorator to its last
line, which is what the ratchet pins. A decision is an `if`, `try`, `for`,
`while` or `match` statement anywhere inside the def; a nested def is a def or
class anywhere inside it. Entry-point modules are measured too.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "src" / "mechanical_librarian" / "bootstrap"
DECISIONS = (ast.If, ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While, ast.Match)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def measure(source: str, module: str) -> dict[str, dict[str, int]]:
    """`module::qualname` -> span, decisions and nested defs, for every def."""

    shapes: dict[str, dict[str, int]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, DEFS):
                inside = [part for part in ast.walk(child) if part is not child]
                first = min([d.lineno for d in child.decorator_list] + [child.lineno])
                assert child.end_lineno is not None
                shapes[f"{module}::{prefix}{child.name}"] = {
                    "span": child.end_lineno - first + 1,
                    "decisions": sum(isinstance(part, DECISIONS) for part in inside),
                    "nested_defs": sum(isinstance(part, DEFS) for part in inside),
                }
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(ast.parse(source), "")
    return shapes


def measure_tree(root: Path = BOOTSTRAP) -> dict[str, dict[str, int]]:
    shapes: dict[str, dict[str, int]] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        shapes.update(measure(path.read_text(encoding="utf-8"), module))
    return shapes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=BOOTSTRAP)
    parser.add_argument(
        "--keys",
        default="",
        help="comma-separated module::qualname keys to list and sum",
    )
    arguments = parser.parse_args()
    shapes = measure_tree(arguments.root)
    if arguments.keys:
        keys = [key.strip() for key in arguments.keys.split(",") if key.strip()]
        missing = [key for key in keys if key not in shapes]
        if missing:
            print(f"no such def: {', '.join(missing)}", file=sys.stderr)
            return 2
        shapes = {key: shapes[key] for key in keys}
    total = {
        field: sum(shape[field] for shape in shapes.values())
        for field in ("span", "decisions", "nested_defs")
    }
    print(json.dumps({"defs": shapes, "total": total}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
