from __future__ import annotations

from collections.abc import Callable

import pytest

from mechanical_librarian.domain.validation import (
    boolean,
    bounded_integer,
    bounded_utf8_text,
    closed_document,
    exact_type,
    literal,
    non_negative_integer,
    nonempty_text,
    prefixed_hex_identity,
    text,
)


class _Text(str):
    """A ``str`` subclass, which exact-type checks must reject."""


class _Integer(int):
    """An ``int`` subclass, which exact-type checks must reject."""


class _Mapping(dict[str, object]):
    """A ``dict`` subclass, which closed-document checks must reject."""


def test_exact_type_accepts_only_that_type() -> None:
    check = exact_type(str)
    assert check("value")
    assert not check(b"value")
    assert not check(None)


def test_exact_type_rejects_a_subclass_instance() -> None:
    assert not exact_type(str)(_Text("value"))
    assert not exact_type(int)(_Integer(1))
    assert not exact_type(dict)(_Mapping())


def test_boolean_rejects_the_integers_that_equal_it() -> None:
    assert boolean(True)
    assert boolean(False)
    assert not boolean(1)
    assert not boolean(0)


def test_text_rejects_a_string_subclass() -> None:
    assert text("")
    assert not text(_Text("value"))


def test_nonempty_text_requires_exact_text_with_content() -> None:
    assert nonempty_text("value")
    assert not nonempty_text("")
    assert not nonempty_text(_Text("value"))
    assert not nonempty_text(None)


def test_bounds_are_inclusive_at_both_ends() -> None:
    check = bounded_integer(0, 3)
    assert [check(value) for value in (-1, 0, 1, 3, 4)] == [
        False,
        True,
        True,
        True,
        False,
    ]


def test_an_absent_maximum_bounds_only_from_below() -> None:
    check = bounded_integer(1)
    assert not check(0)
    assert check(1)
    assert check(2**80)


def test_a_bool_never_satisfies_an_integer_bound() -> None:
    # bool is an int subclass, so True would otherwise pass 0 <= x <= 3.
    assert not bounded_integer(0, 3)(True)
    assert not bounded_integer(0, 3)(False)
    assert not non_negative_integer(True)


def test_an_integer_subclass_is_rejected() -> None:
    assert not bounded_integer(0, 3)(_Integer(2))


@pytest.mark.parametrize(
    "value",
    (
        pytest.param(1.0, id="float"),
        pytest.param("1", id="text"),
        pytest.param(None, id="none"),
        pytest.param([], id="list"),
        pytest.param({}, id="mapping"),
        pytest.param(object(), id="object"),
    ),
)
def test_non_integer_values_are_rejected_without_raising(value: object) -> None:
    assert not non_negative_integer(value)


def test_non_negative_integer_starts_at_zero() -> None:
    assert non_negative_integer(0)
    assert not non_negative_integer(-1)


def test_bounded_utf8_text_uses_exact_type_and_encoded_bytes() -> None:
    check = bounded_utf8_text(6)
    assert check("ééé")
    assert not check("")
    assert not check("éééé")
    assert not check(_Text("value"))
    assert not check("\ud800")


def test_prefixed_hex_identity_requires_exact_lowercase_64_hex_suffix() -> None:
    check = prefixed_hex_identity("record_")
    assert check("record_" + "a" * 64)
    assert not check("record_" + "A" * 64)
    assert not check("record_" + "a" * 63)
    assert not check(_Text("record_" + "a" * 64))


def test_literal_accepts_only_the_frozen_constant() -> None:
    check = literal("field-effect-event-v1")
    assert check("field-effect-event-v1")
    assert not check("field-effect-event-v2")
    assert not check(None)


def test_literal_rejects_a_bool_that_equals_an_integer_constant() -> None:
    # `True == 1` and `False == 0`, so equality alone would accept a bool
    # where the schema pins an integer. The exact-type check closes that.
    assert literal(1)(1)
    assert not literal(1)(True)
    assert literal(0)(0)
    assert not literal(0)(False)


def test_literal_rejects_an_integer_that_equals_a_bool_constant() -> None:
    assert literal(True)(True)
    assert not literal(True)(1)
    assert literal(False)(False)
    assert not literal(False)(0)


def test_literal_rejects_a_float_that_equals_an_integer_constant() -> None:
    assert not literal(1)(1.0)


def test_a_frozenset_membership_check_raises_where_literal_answers() -> None:
    # Load-bearing, not incidental: converting a longhand
    # `value not in FROZEN_SET` into that set's own __contains__ as a field
    # check is only byte-compatible because both raise on an unhashable
    # decoded value rather than quietly rejecting it. literal() is the
    # contrast — it answers False for the very same input.
    check = frozenset({"ambiguous", "consent"}).__contains__
    assert check("consent")
    assert not check("unknown")
    unhashable: tuple[object, ...] = ([], {}, ["ambiguous"])
    for value in unhashable:
        with pytest.raises(TypeError):
            check(value)
    assert not literal("ambiguous")([])


def test_closed_document_propagates_a_field_checks_failure() -> None:
    # The shape the converted field-effect event schema uses for `failure`.
    check = closed_document(failure=frozenset({None, "consent"}).__contains__)
    assert check({"failure": "consent"})
    assert check({"failure": None})
    assert not check({"failure": "unknown"})
    with pytest.raises(TypeError):
        check({"failure": []})


@pytest.fixture
def check() -> Callable[[object], bool]:
    return closed_document(
        "plan_id",
        schema_version=literal("field-effect-cleanup-v1"),
        attempt=bounded_integer(0, 3),
        updated_at=non_negative_integer,
        state=text,
    )


@pytest.fixture
def valid() -> dict[str, object]:
    return {
        "plan_id": "effect_plan_1",
        "schema_version": "field-effect-cleanup-v1",
        "attempt": 0,
        "updated_at": 17,
        "state": "pending",
    }


def test_the_canonical_document_is_accepted(
    check: Callable[[object], bool], valid: dict[str, object]
) -> None:
    assert check(valid)


def test_an_unknown_field_is_rejected(
    check: Callable[[object], bool], valid: dict[str, object]
) -> None:
    assert not check({**valid, "extra": 1})


def test_a_missing_field_is_rejected(
    check: Callable[[object], bool], valid: dict[str, object]
) -> None:
    assert not check({key: value for key, value in valid.items() if key != "state"})


def test_an_unconstrained_field_is_required_but_unchecked(
    check: Callable[[object], bool], valid: dict[str, object]
) -> None:
    assert check({**valid, "plan_id": 42})
    assert not check({key: value for key, value in valid.items() if key != "plan_id"})


def test_each_declared_check_is_applied(
    check: Callable[[object], bool], valid: dict[str, object]
) -> None:
    assert not check({**valid, "schema_version": "v2"})
    assert not check({**valid, "attempt": 4})
    assert not check({**valid, "attempt": True})
    assert not check({**valid, "updated_at": -1})
    assert not check({**valid, "state": 1})


@pytest.mark.parametrize(
    "value",
    (
        pytest.param(None, id="none"),
        pytest.param([], id="list"),
        pytest.param("document", id="text"),
        pytest.param(1, id="integer"),
        pytest.param(object(), id="object"),
    ),
)
def test_a_non_dict_is_rejected_without_raising(
    check: Callable[[object], bool], value: object
) -> None:
    assert not check(value)


def test_a_dict_subclass_is_rejected(
    check: Callable[[object], bool], valid: dict[str, object]
) -> None:
    assert not check(_Mapping(valid))


def test_checks_run_in_declaration_order() -> None:
    order: list[str] = []

    def record(name: str) -> object:
        def check(value: object) -> bool:
            order.append(name)
            return True

        return check

    closed_document(
        first=record("first"), second=record("second"), third=record("third")
    )({"first": 1, "second": 2, "third": 3})
    assert order == ["first", "second", "third"]


def test_a_failing_check_stops_the_remaining_checks() -> None:
    reached: list[str] = []

    def watch(value: object) -> bool:
        reached.append("second")
        return True

    closed_document(first=literal("no"), second=watch)({"first": "yes", "second": 1})
    assert reached == []


def test_nested_documents_close_their_own_field_set() -> None:
    check = closed_document(
        pending_return=closed_document(requested_at=non_negative_integer, scan_id=text)
    )
    assert check({"pending_return": {"requested_at": 0, "scan_id": "s"}})
    assert not check({"pending_return": {"requested_at": -1, "scan_id": "s"}})
    assert not check({"pending_return": {"requested_at": 0, "scan_id": 1}})
    assert not check({"pending_return": {"requested_at": 0}})
    assert not check({"pending_return": {"requested_at": 0, "scan_id": "s", "x": 1}})
    assert not check({"pending_return": None})
