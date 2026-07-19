"""Project-owned JSON Schema vocabulary resolution and execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


DRAFT_2020_12_META_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
UTF8_BYTE_LENGTH_META_SCHEMA = (
    "https://mechanical-librarian.github.io/schemas/utf8-byte-length-meta-v1"
)
UTF8_BYTE_LENGTH_VOCABULARY = (
    "https://mechanical-librarian.github.io/schemas/utf8-byte-length-v1"
)
UTF8_MAX_BYTES_KEYWORD = "mlUtf8MaxBytes"


class SchemaResourceResolver(Protocol):
    """Resolve an exact public schema identity without network access."""

    def resolve(self, uri: str) -> Mapping[str, object]: ...


class SchemaResourceRegistry:
    """Closed project-owned registry for immutable schema resources."""

    def __init__(self, resources: tuple[Mapping[str, object], ...]) -> None:
        resolved: dict[str, Mapping[str, object]] = {}
        for resource in resources:
            identity = resource.get("$id")
            if type(identity) is not str or not identity or identity in resolved:
                raise ValueError("schema resource registry is invalid")
            resolved[identity] = resource
        self._resources = resolved

    def resolve(self, uri: str) -> Mapping[str, object]:
        if type(uri) is not str or uri not in self._resources:
            raise ValueError("schema resource is unavailable")
        return self._resources[uri]


def meta_schema_validates(
    schema: Mapping[str, object], resolver: SchemaResourceResolver
) -> bool:
    """Validate this extension's executable portion through its meta-schema."""

    _required_utf8_vocabulary(schema, resolver)
    return _keyword_declarations_valid(schema)


def validates_utf8_byte_limits(
    schema: Mapping[str, object],
    instance: object,
    resolver: SchemaResourceResolver,
) -> bool:
    """Resolve and execute the required UTF-8 byte-limit vocabulary."""

    _required_utf8_vocabulary(schema, resolver)
    if not _keyword_declarations_valid(schema):
        raise ValueError("UTF-8 byte-length keyword is invalid")
    return _validates_node(schema, instance)


def _required_utf8_vocabulary(
    schema: Mapping[str, object], resolver: SchemaResourceResolver
) -> Mapping[str, object]:
    meta_uri = schema.get("$schema")
    if meta_uri != UTF8_BYTE_LENGTH_META_SCHEMA:
        raise ValueError("required UTF-8 byte-length meta-schema is absent")
    meta = resolver.resolve(meta_uri)
    vocabulary = meta.get("$vocabulary")
    properties = meta.get("properties")
    keyword = (
        properties.get(UTF8_MAX_BYTES_KEYWORD)
        if isinstance(properties, Mapping)
        else None
    )
    if (
        meta.get("$id") != UTF8_BYTE_LENGTH_META_SCHEMA
        or meta.get("$schema") != DRAFT_2020_12_META_SCHEMA
        or meta.get("$dynamicAnchor") != "meta"
        or not isinstance(vocabulary, Mapping)
        or vocabulary.get(UTF8_BYTE_LENGTH_VOCABULARY) is not True
        or keyword != {"minimum": 0, "type": "integer"}
    ):
        raise ValueError("required UTF-8 byte-length vocabulary is absent")
    return meta


def _keyword_declarations_valid(schema: object) -> bool:
    if isinstance(schema, Mapping):
        maximum = schema.get(UTF8_MAX_BYTES_KEYWORD)
        if maximum is not None and (type(maximum) is not int or maximum < 0):
            return False
        return all(_keyword_declarations_valid(value) for value in schema.values())
    if type(schema) is list:
        return all(_keyword_declarations_valid(value) for value in schema)
    return True


def _validates_node(schema: Mapping[str, object], instance: object) -> bool:
    maximum = schema.get(UTF8_MAX_BYTES_KEYWORD)
    if maximum is not None:
        if type(instance) is not str:
            return False
        try:
            if len(instance.encode("utf-8", "strict")) > maximum:
                return False
        except UnicodeError:
            return False

    properties = schema.get("properties")
    if isinstance(properties, Mapping) and isinstance(instance, Mapping):
        for name, child_schema in properties.items():
            if name in instance and isinstance(child_schema, Mapping):
                if not _validates_node(child_schema, instance[name]):
                    return False

    items = schema.get("items")
    if isinstance(items, Mapping) and type(instance) is list:
        for item in instance:
            if not _validates_node(items, item):
                return False
    return True
