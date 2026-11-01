"""Bootstrap holds wiring and entry points only, and cannot regrow unnoticed.

The composition root once shrank to about 2,000 lines (#660) and regrew past
5,000 because each feature put its behaviour there. This ratchet makes every
such growth an edit to this file in the same PR. It fails when a bootstrap
module's ncnc size passes its CEILING or sits more than SLACK under it, a
non-entry module's def count differs from DEF_COUNT, a non-entry module gains
a class with a method in any listed form or a def over the length bound
outside RESIDUAL, an outermost RESIDUAL key's span passes its pin or sits more
than SPAN_SLACK under it, a bootstrap module is added, a Python module sits
outside the layer packages, or composition passes REVIEWED_NCNC + REVIEW_BAND.

The test checks that these constants agree with the code, not the direction
of an edit. Which edits are acceptable is a review rule (AGENTS.md, "Code
economy"):
- lowering is always allowed;
- raising a span, CEILING or DEF_COUNT is allowed for straight-line wiring,
  stated in the PR body;
- adding a RESIDUAL key or a bootstrap module, adding decisions or closures
  where the layering allows them in application, or raising REVIEWED_NCNC or
  REVIEW_BAND needs the owner's instruction, quoted in the PR body;
- a split keeps only pieces over 60 lines as keys, and the outermost spans of
  the pieces that stay keys sum to at most the old span plus 25 per new key,
  with the decision and nested-def sums that `scripts/measure_bootstrap.py`
  reports not growing.

REVIEWED_NCNC is composition's size at the owner's last review. A stage of the
composition-root refactor lowers it by exactly that stage's own net change to
composition, so growth nobody reviewed carries over; only an owner review sets
it to the measured size.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

import pytest
from scripts.measure_bootstrap import measure_tree

from tests.architecture.test_import_boundaries import ALLOWED

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mechanical_librarian"
BOOTSTRAP_ROOT = PACKAGE_ROOT / "bootstrap"
ROOT_MODULES = {"__init__.py", "__main__.py", "_version.py"}
MAX_FUNCTION_LINES = 60
SPAN_SLACK = 2
SLACK = 10

# Child, probe and container entry points stay in bootstrap by design. They
# are exempt from the construct rules but pinned by ncnc size.
ENTRY_POINTS = {
    "audio_child.py": 264,
    "cover_child.py": 164,
    "epub_child.py": 660,
    "probe_shim.py": 66,
    "container.py": 77,
    "container_health.py": 110,
}
CEILING = {
    "__init__.py": 8,
    "composition.py": 4572,
    "contained_reviewer.py": 124,
    **ENTRY_POINTS,
}
DEF_COUNT = {
    "__init__.py": 1,
    "composition.py": 167,
    "contained_reviewer.py": 5,
}
# Set to the measured size only by an owner review; lowered by a refactor
# stage's own net change; raised only on the owner's instruction.
REVIEWED_NCNC = 4572
REVIEW_BAND = 300
REVIEWED_MODULE = "composition.py"
_C = "composition.py::"
# Outermost keys carry their span; keys nested inside another key carry None
# (their growth shows in the outermost span).
RESIDUAL: dict[str, int | None] = {
    _C + "_StoreRuntimeActivation": 118,
    _C + "compose_continuous_guarded_application": 84,
    _C + "_IsolatedEpubInspector": 163,
    _C + "_IsolatedEpubInspector.inspect": None,
    _C + "_IsolatedAudioInspector": 327,
    _C + "_IsolatedAudioInspector.inspect": None,
    _C + "_IsolatedAudioInspector._inspect_tracks": None,
    _C + "_IsolatedAudioInspector.unchanged": None,
    _C + "_open_audio_sidecars": 64,
    _C + "_CoverEvidenceObserver": 343,
    _C + "_CoverEvidenceObserver.observe_current": None,
    _C + "_CoverEvidenceObserver._admit_provider_descriptors": None,
    _C + "_CoverEvidenceObserver._remote": None,
    _C + "_NonClosingRepository": 13,
    _C + "run_metadata_report_loaded": 263,
    _C + "run_metadata_report_loaded.factory": None,
    _C + "_ProviderPacers": 29,
    _C + "_retry_provider": 98,
    _C + "create_review_pipeline": 457,
    _C + "run_catalogue": 70,
    _C + "run_batch": 125,
    _C + "compose_store_runtime_generation": 237,
    _C + "_compose_setup_runtime_generation": 145,
    _C + "run_review_server": 350,
    _C + "_UnavailableCodexAuth": 18,
    _C + "_LazyCodexAuth": 119,
    _C + "_CodexAuthLeaseCoordinator": 90,
    _C + "_compose_reviewer_access": 167,
    _C + "_compose_cover_considerations": 74,
    _C + "_OfflineSessionStore": 40,
    _C + "run_cover_review": 104,
}

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_BINDERS = {
    "property",
    "classmethod",
    "staticmethod",
    "cached_property",
    "partialmethod",
    "partial",
    "MethodType",
}
_PERMITTED_DUNDERS = {"__slots__", "__match_args__"}
_LAYOUT = {
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
}


# Measurement ---------------------------------------------------------------


def ncnc(source: str) -> int:
    """Lines covered by a non-layout token's start-to-end span, blank ones out.

    A multi-line string covers every line it spans; whitespace-only lines
    inside it do not count. Docstrings count.
    """
    lines = source.splitlines()
    covered: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in _LAYOUT:
            covered.update(range(token.start[0], token.end[0] + 1))
    return sum(1 for number in covered if lines[number - 1].strip())


def _lambda_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        if any(isinstance(node, ast.Lambda) for node in ast.walk(value)):
            for target in targets:
                names.update(
                    node.id for node in ast.walk(target) if isinstance(node, ast.Name)
                )
    return names


def def_count(source: str) -> int:
    tree = ast.parse(source)
    defs = sum(isinstance(node, _FUNCTIONS) for node in ast.walk(tree))
    return defs + len(_lambda_names(tree))


def _module_functions(tree: ast.Module) -> set[str]:
    defined = {node.name for node in tree.body if isinstance(node, _FUNCTIONS)}
    return defined | _lambda_names(tree)


def _span(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    first = min([value.lineno for value in node.decorator_list] + [node.lineno])
    assert node.end_lineno is not None
    return node.end_lineno - first + 1


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _is_docstring_or_pass(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Pass) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_stub(statement: ast.stmt) -> bool:
    if not isinstance(statement, _FUNCTIONS):
        return False
    body = list(statement.body)
    if body and _is_docstring_or_pass(body[0]) and isinstance(body[0], ast.Expr):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    only = body[0]
    return (
        isinstance(only, ast.Pass)
        or (
            isinstance(only, ast.Expr)
            and isinstance(only.value, ast.Constant)
            and only.value.value is Ellipsis
        )
        or (
            isinstance(only, ast.Raise)
            and only.exc is not None
            and _name(only.exc) == "NotImplementedError"
        )
    )


def _value_nodes(value: ast.AST) -> Iterator[ast.AST]:
    """Every node in a value, skipping `default_factory=` inside `field(...)`."""
    stack = [value]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.Call) and _name(node.func) == "field":
            stack.append(node.func)
            stack.extend(node.args)
            stack.extend(
                keyword.value
                for keyword in node.keywords
                if keyword.arg != "default_factory"
            )
        else:
            stack.extend(ast.iter_child_nodes(node))


def _carries_behaviour(value: ast.expr | None, callables: set[str]) -> bool:
    """A class attribute whose value is, or builds, a callable member."""
    if value is None:
        return False
    for node in _value_nodes(value):
        if isinstance(node, ast.Lambda):
            return True
        if isinstance(node, ast.Name) and node.id in callables:
            return True
        if isinstance(node, ast.Call) and _name(node.func) in _BINDERS:
            return True
    return False


def _forbidden_target(target: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Name)
        and node.id.startswith("__")
        and node.id.endswith("__")
        and node.id not in _PERMITTED_DUNDERS
        for node in ast.walk(target)
    )


def _init_binds_behaviour(init: ast.FunctionDef, callables: set[str]) -> bool:
    for node in ast.walk(init):
        if node is init:
            continue
        if isinstance(node, (*_DEFS, ast.Lambda)):
            return True
        if isinstance(node, ast.Name) and node.id in callables:
            return True
        if isinstance(node, ast.Call) and _name(node.func) == "MethodType":
            return True
    return False


def _permitted_class(node: ast.ClassDef, functions: set[str]) -> bool:
    if any(_name(decorator) != "dataclass" for decorator in node.decorator_list):
        return False
    protocol = any(_name(base) == "Protocol" for base in node.bases)
    # A Name of a def in this class body (an `__init__` or a Protocol stub) is
    # as much a method alias as a module function.
    callables = functions | {
        statement.name for statement in node.body if isinstance(statement, _FUNCTIONS)
    }
    inits = 0
    for statement in node.body:
        if _is_docstring_or_pass(statement):
            continue
        if isinstance(statement, ast.AnnAssign):
            if _forbidden_target(statement.target) or _carries_behaviour(
                statement.value, callables
            ):
                return False
            continue
        if isinstance(statement, ast.Assign):
            if any(_forbidden_target(target) for target in statement.targets):
                return False
            if _carries_behaviour(statement.value, callables):
                return False
            continue
        if isinstance(statement, ast.FunctionDef) and statement.name == "__init__":
            inits += 1
            if statement.decorator_list or _init_binds_behaviour(statement, callables):
                return False
            continue
        if protocol and _is_stub(statement):
            continue
        return False
    return inits <= 1


def _module_level_bindings(tree: ast.Module, module: str) -> dict[str, int]:
    """`A.run = ...`, `setattr(A, ...)` on a module class, and 3-argument type()."""
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            for part in ast.walk(target):
                if (
                    isinstance(part, ast.Attribute)
                    and isinstance(part.value, ast.Name)
                    and part.value.id in classes
                ):
                    found[f"{module}::<{part.value.id}.{part.attr} =>"] = 1
        if isinstance(node, ast.Call):
            callee = _name(node.func)
            if (
                callee == "setattr"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in classes
            ):
                found[f"{module}::<setattr {node.args[0].id}>"] = 1
            if (
                isinstance(node.func, ast.Name)
                and callee == "type"
                and len(node.args) == 3
            ):
                found[f"{module}::<type() at {node.lineno}>"] = 1
    return found


def violations(source: str, module: str) -> dict[str, int]:
    """Every behaviour-bearing class and over-long def: qualname -> span."""
    tree = ast.parse(source)
    functions = _module_functions(tree)
    found: dict[str, int] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNCTIONS):
                qualname = prefix + child.name
                if _span(child) > MAX_FUNCTION_LINES:
                    found[f"{module}::{qualname}"] = _span(child)
                visit(child, qualname + ".")
            elif isinstance(child, ast.ClassDef):
                qualname = prefix + child.name
                if not _permitted_class(child, functions):
                    found[f"{module}::{qualname}"] = _span(child)
                visit(child, qualname + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    found.update(_module_level_bindings(tree, module))
    return found


def is_nested(key: str, keys: Iterable[str]) -> bool:
    """Whether another key is an enclosing qualname of this one."""
    module, _, qualname = key.partition("::")
    parts = qualname.split(".")
    enclosing = {f"{module}::{'.'.join(parts[:n])}" for n in range(1, len(parts))}
    return bool(enclosing & set(keys))


# Rules, as pure functions so the controls exercise the same code ------------


def ceiling_problems(sizes: Mapping[str, int], ceiling: Mapping[str, int]) -> list[str]:
    problems = []
    for module, size in sorted(sizes.items()):
        allowed = ceiling.get(module, 0)
        if size > allowed:
            problems.append(f"{module}: {size} ncnc > CEILING {allowed}")
        elif allowed - size > SLACK:
            problems.append(f"{module}: CEILING {allowed} stale ({size} ncnc)")
    return problems


def def_count_problems(
    measured: Mapping[str, int], pinned: Mapping[str, int]
) -> list[str]:
    return [
        f"{module}: {count} defs != DEF_COUNT {pinned.get(module, 0)}"
        for module, count in sorted(measured.items())
        if count != pinned.get(module, 0)
    ]


def residual_problems(
    found: Mapping[str, int], residual: Mapping[str, int | None]
) -> list[str]:
    problems = [
        f"unlisted: {key} (span {found[key]})"
        for key in sorted(found.keys() - residual.keys())
    ]
    problems += [
        f"gone, delete it: {key}" for key in sorted(residual.keys() - found.keys())
    ]
    for key in sorted(found.keys() & residual.keys()):
        pin, span = residual[key], found[key]
        if is_nested(key, found):
            if pin is not None:
                problems.append(f"nested: {key} pin must be None")
        elif pin is None:
            problems.append(f"outermost: {key} needs its span ({span})")
        elif span > pin:
            problems.append(f"grew: {key} {span} > {pin}")
        elif pin - span > SPAN_SLACK:
            problems.append(f"stale: {key} pin {pin}, measured {span}")
    return problems


def review_band_problems(size: int, reviewed: int, band: int) -> list[str]:
    if size > reviewed + band:
        return [
            f"{REVIEWED_MODULE}: {size} ncnc > REVIEWED_NCNC {reviewed} + "
            f"REVIEW_BAND {band}: owner review required; do not raise without "
            "the owner's instruction"
        ]
    return []


def layout_problems(paths: Iterable[str]) -> list[str]:
    problems = []
    for path in sorted(paths):
        parts = path.split("/")
        if len(parts) == 1:
            if path not in ROOT_MODULES:
                problems.append(f"{path}: module outside the layer packages")
        elif parts[0] not in ALLOWED:
            problems.append(f"{path}: module outside the layer packages")
    return problems


# The tree ------------------------------------------------------------------


def _modules() -> dict[str, str]:
    return {
        path.relative_to(BOOTSTRAP_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(BOOTSTRAP_ROOT.rglob("*.py"))
    }


def test_every_package_module_is_under_a_layer() -> None:
    paths = [
        path.relative_to(PACKAGE_ROOT).as_posix() for path in PACKAGE_ROOT.rglob("*.py")
    ]
    assert layout_problems(paths) == []


def test_bootstrap_module_set_is_closed() -> None:
    modules = sorted(_modules())
    assert modules == sorted(CEILING)
    assert sorted(DEF_COUNT) == [m for m in modules if m not in ENTRY_POINTS]


def test_bootstrap_sizes_hold_their_ncnc_ceilings() -> None:
    sizes = {module: ncnc(source) for module, source in _modules().items()}
    assert ceiling_problems(sizes, CEILING) == []


def test_bootstrap_def_counts_are_exact() -> None:
    measured = {
        module: def_count(source)
        for module, source in _modules().items()
        if module not in ENTRY_POINTS
    }
    assert def_count_problems(measured, DEF_COUNT) == []


def test_bootstrap_gains_no_behaviour_and_residual_holds_its_spans() -> None:
    found: dict[str, int] = {}
    for module, source in _modules().items():
        if module not in ENTRY_POINTS:
            found.update(violations(source, module))
    assert residual_problems(found, RESIDUAL) == []


def test_composition_is_within_its_review_band() -> None:
    size = ncnc((BOOTSTRAP_ROOT / REVIEWED_MODULE).read_text(encoding="utf-8"))
    assert review_band_problems(size, REVIEWED_NCNC, REVIEW_BAND) == []


def test_measure_bootstrap_agrees_with_the_ratchet_spans() -> None:
    """The split rule reads `scripts/measure_bootstrap.py`, so it may not drift."""
    shapes = measure_tree(BOOTSTRAP_ROOT)
    for module, source in _modules().items():
        for key, span in violations(source, module).items():
            if "::<" not in key:
                assert shapes[key]["span"] == span, key


# Controls ------------------------------------------------------------------


def _lines(count: int, indent: str = "    ") -> str:
    return "".join(f"{indent}value_{index} = {index}\n" for index in range(count))


def _flagged(source: str) -> set[str]:
    return {key.split("::", 1)[1] for key in violations(source, "probe.py")}


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        # From the stage-1 probe.
        pytest.param("class A:\n    def run(self):\n        pass\n", True, id="method"),
        pytest.param(
            "from dataclasses import dataclass\n"
            "@dataclass\nclass A:\n    '''Doc.'''\n    x: int\n    y: str = 'a'\n",
            False,
            id="dataclass-fields",
        ),
        pytest.param(
            "@dataclass\nclass A:\n    x: int\n    def __init__(self, x):\n"
            "        self.x = x\n",
            False,
            id="init-only",
        ),
        pytest.param(
            "@dataclass\nclass A:\n    x: int\n    @classmethod\n"
            "    def build(cls):\n        return cls(1)\n",
            True,
            id="dataclass-classmethod",
        ),
        pytest.param(
            "class A:\n    @staticmethod\n    def __init__():\n        pass\n",
            True,
            id="decorated-init",
        ),
        pytest.param(
            "class P(Protocol):\n    def run(self) -> None: ...\n"
            "    def stop(self) -> None:\n        '''Doc.'''\n"
            "        raise NotImplementedError\n",
            False,
            id="protocol-stub",
        ),
        pytest.param(
            "class P(typing.Protocol):\n    def run(self) -> int:\n        return 1\n",
            True,
            id="protocol-body",
        ),
        pytest.param(
            "class T(TypedDict, total=False):\n    a: int\n    b: str\n",
            False,
            id="typeddict",
        ),
        pytest.param(
            "class T(TypedDict):\n    a: int\n    def m(self):\n        return 1\n",
            True,
            id="typeddict-with-method",
        ),
        pytest.param("class E(Exception):\n    pass\n", False, id="bodyless-exception"),
        pytest.param(
            "class S(ctypes.Structure):\n    _fields_ = [('a', ctypes.c_int)]\n",
            False,
            id="ctypes-structure",
        ),
        pytest.param(
            "class A:\n    def __init__(self):\n        pass\n"
            "    start = status = __init__\n",
            True,
            id="alias-in-class",
        ),
        pytest.param(
            "def helper(self):\n    return 1\nclass A:\n    run = helper\n",
            True,
            id="alias-module-function",
        ),
        pytest.param(
            "class A:\n    run = property(lambda self: 1)\n", True, id="lambda-in-call"
        ),
        pytest.param(
            "class A:\n    run: Callable[[], int] = lambda self: 1\n",
            True,
            id="annotated-lambda",
        ),
        pytest.param(
            "class A:\n    run = staticmethod(print)\n", True, id="descriptor-factory"
        ),
        pytest.param(
            "class A:\n    class B:\n        x: int\n", True, id="nested-class"
        ),
        pytest.param(
            "def f():\n    class A:\n        def run(self):\n            pass\n",
            True,
            id="class-in-function",
        ),
        pytest.param("def f():\n" + _lines(59), False, id="def-60-lines"),
        pytest.param("@decorator\ndef f():\n" + _lines(59), True, id="decorated-61"),
        # Revision 3: new evasion forms.
        pytest.param(
            "class A:\n    def __init__(self):\n        def run():\n"
            "            return 1\n        self.run = run\n",
            True,
            id="def-in-init",
        ),
        pytest.param(
            "class A:\n    def __init__(self):\n        self.run = lambda: 1\n",
            True,
            id="lambda-in-init",
        ),
        pytest.param(
            "import types\nclass A:\n    def __init__(self, f):\n"
            "        self.run = types.MethodType(f, self)\n",
            True,
            id="methodtype-in-init",
        ),
        pytest.param(
            "def helper():\n    return 1\nclass A:\n    def __init__(self):\n"
            "        self.run = helper\n",
            True,
            id="module-function-in-init",
        ),
        pytest.param(
            "class A:\n    def __init__(self):\n        pass\n"
            "    def __init__(self, x):\n        self.x = x\n",
            True,
            id="duplicate-init",
        ),
        pytest.param(
            "@functools.total_ordering\nclass A:\n    x: int\n",
            True,
            id="class-decorator",
        ),
        pytest.param(
            "def f(self):\n    return 1\nclass A:\n    x: int\nA.run = f\n",
            True,
            id="assign-class-attribute",
        ),
        pytest.param(
            "def f(self):\n    return 1\nclass A:\n    x: int\nsetattr(A, 'run', f)\n",
            True,
            id="setattr-class",
        ),
        pytest.param("B = type('A', (), {'run': print})\n", True, id="three-arg-type"),
        pytest.param("class A:\n    __call__ = print\n", True, id="dunder-assign"),
        pytest.param(
            "h = lambda self: 1\nclass A:\n    run = h\n",
            True,
            id="module-lambda-alias",
        ),
        # Revision 3: permitted forms.
        pytest.param(
            "def _make():\n    return []\n@dataclass(frozen=True)\nclass A:\n"
            "    x: list[int] = field(default_factory=_make)\n",
            False,
            id="default-factory",
        ),
        pytest.param(
            "from x import imported_fn\nclass A:\n    run = imported_fn\n",
            False,
            id="imported-callable-is-a-stated-limit",
        ),
        pytest.param(
            "class A:\n    __slots__ = ('x',)\n    __match_args__ = ('x',)\n",
            False,
            id="slots-and-match-args",
        ),
    ],
)
def test_construct_rules_flag_behaviour_and_long_functions(
    source: str, flagged: bool
) -> None:
    assert bool(violations(source, "probe.py")) is flagged


def test_a_field_default_named_like_a_method_or_closure_is_permitted() -> None:
    source = (
        "class B:\n    def timeout(self):\n        return 1\n"
        "def outer(timeout):\n    def retry():\n        return 2\n"
        "    @dataclass\n    class A:\n        timeout: float = timeout\n"
        "        retry: int = 3\n"
    )
    assert _flagged(source) == {"B"}


def test_ncnc_counts_code_and_docstrings_not_comments_or_blanks() -> None:
    source = (
        '"""Doc.\n\n    \nMore."""\n\n# comment\nx = 1  # trailing\n\ny = (\n    2\n)\n'
    )
    assert ncnc(source) == 6


_ENTRY = "def wiring(a):\n    if a:\n        x = 1\n" + _lines(58)
_NESTED = "def wiring(a):\n    def inner():\n" + _lines(60, "        ")


def _found(source: str) -> dict[str, int]:
    return violations(source, "m.py")


def test_an_exact_span_holds_and_a_nested_key_is_none() -> None:
    assert _found(_ENTRY) == {"m.py::wiring": 61}
    assert residual_problems(_found(_ENTRY), {"m.py::wiring": 61}) == []
    assert _found(_NESTED) == {"m.py::wiring": 62, "m.py::wiring.inner": 61}
    pins = {"m.py::wiring": 62, "m.py::wiring.inner": None}
    assert residual_problems(_found(_NESTED), pins) == []


def test_span_growth_by_one_and_stale_by_three_fail() -> None:
    assert residual_problems(_found(_ENTRY + "    y = 2\n"), {"m.py::wiring": 61}) == [
        "grew: m.py::wiring 62 > 61"
    ]
    assert residual_problems(_found(_ENTRY), {"m.py::wiring": 63}) == []
    assert residual_problems(_found(_ENTRY), {"m.py::wiring": 64}) == [
        "stale: m.py::wiring pin 64, measured 61"
    ]


def test_nested_growth_shows_in_the_outermost_span() -> None:
    grown = _NESTED + "        extra = 1\n"
    pins = {"m.py::wiring": 62, "m.py::wiring.inner": None}
    assert residual_problems(_found(grown), pins) == ["grew: m.py::wiring 63 > 62"]


def test_nested_and_outermost_keys_need_the_right_pin_kind() -> None:
    assert residual_problems(
        _found(_NESTED), {"m.py::wiring": 62, "m.py::wiring.inner": 61}
    ) == ["nested: m.py::wiring.inner pin must be None"]
    assert residual_problems(
        _found(_NESTED), {"m.py::wiring": None, "m.py::wiring.inner": None}
    ) == ["outermost: m.py::wiring needs its span (62)"]


def test_a_new_nested_violator_without_a_key_fails() -> None:
    grown = _ENTRY + "    class Helper:\n        def run(self):\n            pass\n"
    assert residual_problems(_found(grown), {"m.py::wiring": 64}) == [
        "unlisted: m.py::wiring.Helper (span 3)"
    ]


def test_a_new_long_def_is_unlisted() -> None:
    grown = _ENTRY + "def other():\n" + _lines(60)
    assert residual_problems(_found(grown), {"m.py::wiring": 61}) == [
        "unlisted: m.py::other (span 61)"
    ]
    assert residual_problems({}, {"m.py::wiring": 61}) == [
        "gone, delete it: m.py::wiring"
    ]


def test_ncnc_growth_by_one_and_stale_beyond_slack_fail() -> None:
    assert ceiling_problems({"m.py": 101}, {"m.py": 100}) == [
        "m.py: 101 ncnc > CEILING 100"
    ]
    assert ceiling_problems({"m.py": 90}, {"m.py": 100}) == []
    assert ceiling_problems({"m.py": 89}, {"m.py": 100}) == [
        "m.py: CEILING 100 stale (89 ncnc)"
    ]
    assert ceiling_problems({"x.py": 1}, {}) == ["x.py: 1 ncnc > CEILING 0"]


def test_def_counts_are_exact_both_ways() -> None:
    assert def_count_problems({"m.py": 5}, {"m.py": 5}) == []
    assert def_count_problems({"m.py": 6}, {"m.py": 5}) == [
        "m.py: 6 defs != DEF_COUNT 5"
    ]
    assert def_count_problems({"m.py": 4}, {"m.py": 5}) == [
        "m.py: 4 defs != DEF_COUNT 5"
    ]


def test_passing_the_review_band_fails_until_the_owner_resets_it() -> None:
    assert review_band_problems(4871, 4571, 300) == []
    (problem,) = review_band_problems(4872, 4571, 300)
    assert "owner review required" in problem


def test_a_module_outside_the_layers_fails() -> None:
    assert layout_problems(["__init__.py", "bootstrap/x.py", "_version.py"]) == []
    assert layout_problems(["helpers.py", "resources/x.py"]) == [
        "helpers.py: module outside the layer packages",
        "resources/x.py: module outside the layer packages",
    ]
