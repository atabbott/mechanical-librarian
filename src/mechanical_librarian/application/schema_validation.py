"""Project-owned JSON Schema vocabulary extensions."""

from __future__ import annotations

from collections.abc import Mapping


UTF8_BYTE_LENGTH_VOCABULARY = (
    "https://mechanical-librarian.invalid/schema/vocabulary/utf8-byte-length-v1"
)
UTF8_MAX_BYTES_KEYWORD = "mlUtf8MaxBytes"


def validates_utf8_byte_limits(
    schema: Mapping[str, object], instance: object
) -> bool:
    """Execute the required UTF-8 byte-limit vocabulary over an instance."""

    vocabulary = schema.get("$vocabulary")
    if (
        not isinstance(vocabulary, Mapping)
        or vocabulary.get(UTF8_BYTE_LENGTH_VOCABULARY) is not True
    ):
        raise ValueError("required UTF-8 byte-length vocabulary is absent")
    return _validates_node(schema, instance)


def _validates_node(schema: Mapping[str, object], instance: object) -> bool:
    maximum = schema.get(UTF8_MAX_BYTES_KEYWORD)
    if maximum is not None:
        if type(maximum) is not int or maximum < 0:
            raise ValueError("UTF-8 byte-length keyword is invalid")
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
