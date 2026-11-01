"""The browser decoders know every field a review response may leave out.

Rule P in `frontend/scripts/generate-decoders.mjs` makes every property of a
response object required on the wire except the pairs in
`RESPONSE_OMISSIONS`. These proofs keep that list equal to what the server
really omits, so a new `exclude_if` or serializer cannot ship a response the
browser's decoders would refuse (#1576).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, get_args

from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from mechanical_librarian.domain.owner_settings import LibrarySettings
from tests.support.review_web_doubles import FakeReviewRepository, review_web_context

GENERATOR = Path("frontend/scripts/generate-decoders.mjs")
SOURCE = Path("src/mechanical_librarian")
# Constructs outside any response model, named by symbol rather than line.
ALLOWED = {("domain/setup_state.py", "SetupLibraryCandidate", "service_paths")}
SERIALIZERS = {
    "model_serializer",
    "field_serializer",
    "PlainSerializer",
    "WrapSerializer",
}


def _declared() -> set[tuple[str, str]]:
    block = re.search(
        r"^const RESPONSE_OMISSIONS = \[\n((?:  \['\w+', '\w+'\],\n)*)\]$",
        GENERATOR.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert block is not None, "RESPONSE_OMISSIONS lost its parseable form"
    return {
        (component, field)
        for component, field in re.findall(r"\['(\w+)', '(\w+)'\]", block[1])
    }


def _omitted(annotation: Any, seen: set[type], found: set[tuple[str, str]]) -> None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for name, field in annotation.model_fields.items():
            if field.exclude is True or field.exclude_if is not None:
                found.add((annotation.__name__, name))
            _omitted(field.annotation, seen, found)
        return
    for argument in get_args(annotation):
        _omitted(argument, seen, found)


def _route_omissions(models: Iterator[Any]) -> set[tuple[str, str]]:
    seen: set[type] = set()
    found: set[tuple[str, str]] = set()
    for model in models:
        _omitted(model, seen, found)
    return found


def _response_models() -> Iterator[Any]:
    for context in review_web_context(FakeReviewRepository):
        for route in context.client.app.routes:
            if isinstance(route, APIRoute) and route.path.startswith("/api/v1/"):
                yield route.response_model


def test_the_declared_omissions_are_exactly_what_responses_omit() -> None:
    routes = list(_response_models())
    assert len(routes) > 60
    assert _route_omissions(iter(routes)) == _declared()


def test_the_walker_reports_an_omission_it_was_not_told_about() -> None:
    class _Nested(BaseModel):
        hidden: str | None = Field(default=None, exclude_if=lambda value: not value)

    class _Response(BaseModel):
        items: list[Annotated[_Nested, Field(max_length=2)]]
        settings: LibrarySettings | None

    assert _route_omissions(iter([_Response])) == _declared() | {("_Nested", "hidden")}


def test_each_omission_predicate_omits_only_its_default() -> None:
    fields = LibrarySettings.model_fields
    display, languages = fields["display_name"], fields["languages"]
    assert display.exclude_if is not None and languages.exclude_if is not None
    assert display.exclude_if(None) is True
    assert display.exclude_if("Reading Room") is False
    assert languages.exclude_if([]) is True
    assert languages.exclude_if(["en"]) is False
    base = {
        "name": "ebooks",
        "connection": "primary",
        "library_id": "library-1",
        "media_kind": "ebook",
    }
    bare = LibrarySettings.model_validate(base).model_dump(mode="json")
    assert "display_name" not in bare and "languages" not in bare
    named = LibrarySettings.model_validate(
        {**base, "display_name": "Reading Room", "languages": ["en"]}
    ).model_dump(mode="json")
    assert named["display_name"] == "Reading Room"
    assert named["languages"] == ["en"]


def _constructs(path: Path) -> Iterator[tuple[str, str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owners: dict[int, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    for inner in ast.walk(statement):
                        owners[id(inner)] = (node.name, statement.target.id)
    relative = path.relative_to(SOURCE).as_posix()
    for node in ast.walk(tree):
        names = {
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else node.name
            if isinstance(node, ast.alias)
            else ""
        }
        if names & SERIALIZERS:
            yield relative, "serializer", next(iter(names))
        if isinstance(node, ast.keyword) and node.arg is not None:
            omits = node.arg == "exclude_if" or (
                node.arg == "exclude"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            )
            if omits or node.arg.startswith("response_model_exclude"):
                owner = owners.get(id(node), ("module", node.arg))
                yield relative, *owner


def test_no_other_omission_or_serializer_construct_exists() -> None:
    found = {hit for path in sorted(SOURCE.rglob("*.py")) for hit in _constructs(path)}
    declared = {
        ("domain/owner_settings.py", component, field)
        for component, field in _declared()
    }
    assert found == declared | ALLOWED
