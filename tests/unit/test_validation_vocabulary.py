from __future__ import annotations

import unittest

from mechanical_librarian.domain.validation import (
    boolean,
    bounded_integer,
    closed_document,
    exact_type,
    literal,
    matching_text,
    member_of,
    non_empty_text,
    non_negative_integer,
    text,
)


class _Text(str):
    """A ``str`` subclass, which exact-type checks must reject."""


class _Integer(int):
    """An ``int`` subclass, which exact-type checks must reject."""


class _Mapping(dict[str, object]):
    """A ``dict`` subclass, which closed-document checks must reject."""


class ExactTypeTests(unittest.TestCase):
    def test_exact_type_accepts_only_that_type(self) -> None:
        check = exact_type(str)
        self.assertTrue(check("value"))
        self.assertFalse(check(b"value"))
        self.assertFalse(check(None))

    def test_exact_type_rejects_a_subclass_instance(self) -> None:
        self.assertFalse(exact_type(str)(_Text("value")))
        self.assertFalse(exact_type(int)(_Integer(1)))
        self.assertFalse(exact_type(dict)(_Mapping()))

    def test_boolean_rejects_the_integers_that_equal_it(self) -> None:
        self.assertTrue(boolean(True))
        self.assertTrue(boolean(False))
        self.assertFalse(boolean(1))
        self.assertFalse(boolean(0))

    def test_text_rejects_a_string_subclass(self) -> None:
        self.assertTrue(text(""))
        self.assertFalse(text(_Text("value")))


class BoundedIntegerTests(unittest.TestCase):
    def test_bounds_are_inclusive_at_both_ends(self) -> None:
        check = bounded_integer(0, 3)
        self.assertEqual([check(value) for value in (-1, 0, 1, 3, 4)],
                         [False, True, True, True, False])

    def test_an_absent_maximum_bounds_only_from_below(self) -> None:
        check = bounded_integer(1)
        self.assertFalse(check(0))
        self.assertTrue(check(1))
        self.assertTrue(check(2**80))

    def test_a_bool_never_satisfies_an_integer_bound(self) -> None:
        # bool is an int subclass, so True would otherwise pass 0 <= x <= 3.
        self.assertFalse(bounded_integer(0, 3)(True))
        self.assertFalse(bounded_integer(0, 3)(False))
        self.assertFalse(non_negative_integer(True))

    def test_an_integer_subclass_is_rejected(self) -> None:
        self.assertFalse(bounded_integer(0, 3)(_Integer(2)))

    def test_non_integer_values_are_rejected_without_raising(self) -> None:
        for value in (1.0, "1", None, [], {}, object()):
            self.assertFalse(non_negative_integer(value))

    def test_non_negative_integer_starts_at_zero(self) -> None:
        self.assertTrue(non_negative_integer(0))
        self.assertFalse(non_negative_integer(-1))


class TextCheckTests(unittest.TestCase):
    def test_non_empty_text_rejects_the_empty_string(self) -> None:
        self.assertTrue(non_empty_text("a"))
        self.assertFalse(non_empty_text(""))

    def test_non_empty_text_rejects_a_non_empty_subclass_instance(self) -> None:
        self.assertFalse(non_empty_text(_Text("a")))

    def test_non_empty_text_rejects_other_truthy_values(self) -> None:
        for value in (1, True, ["a"], {"a": 1}, None):
            self.assertFalse(non_empty_text(value))

    def test_matching_text_anchors_the_whole_value(self) -> None:
        check = matching_text(r"human_decision_[0-9a-f]{4}")
        self.assertTrue(check("human_decision_0a1b"))
        self.assertFalse(check("human_decision_0a1bc"))
        self.assertFalse(check(" human_decision_0a1b"))
        self.assertFalse(check("human_decision_0A1B"))

    def test_matching_text_rejects_non_text_without_raising(self) -> None:
        check = matching_text(r"[a-z]+")
        self.assertFalse(check(None))
        self.assertFalse(check(b"abc"))
        self.assertFalse(check(_Text("abc")))


class ClosedSetTests(unittest.TestCase):
    def test_literal_accepts_only_the_frozen_constant(self) -> None:
        check = literal("field-effect-event-v1")
        self.assertTrue(check("field-effect-event-v1"))
        self.assertFalse(check("field-effect-event-v2"))
        self.assertFalse(check(None))

    def test_member_of_accepts_only_declared_members(self) -> None:
        check = member_of(frozenset({None, "ambiguous", "consent"}))
        self.assertTrue(check(None))
        self.assertTrue(check("ambiguous"))
        self.assertFalse(check("unknown"))

    def test_member_of_propagates_a_set_containers_hashing_requirement(self) -> None:
        # Load-bearing, not incidental: converting a longhand
        # `value not in FROZEN_SET` into member_of(FROZEN_SET) is only
        # byte-compatible because both raise on an unhashable decoded value
        # rather than quietly rejecting it. literal() is the contrast — it
        # compares with == and answers False for the very same input.
        check = member_of(frozenset({"ambiguous", "consent"}))
        unhashable: tuple[object, ...] = ([], {}, ["ambiguous"])
        for value in unhashable:
            with self.assertRaises(TypeError):
                check(value)
        self.assertFalse(literal("ambiguous")([]))

    def test_member_of_over_a_tuple_cannot_raise(self) -> None:
        # The guarded alternative the member_of docstring points callers to.
        self.assertFalse(member_of(("ambiguous", "consent"))([]))
        self.assertTrue(member_of(("ambiguous", "consent"))("consent"))

    def test_closed_document_propagates_a_field_checks_failure(self) -> None:
        check = closed_document(failure=member_of(frozenset({None, "consent"})))
        self.assertTrue(check({"failure": "consent"}))
        with self.assertRaises(TypeError):
            check({"failure": []})


class ClosedDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.check = closed_document(
            "plan_id", schema_version=literal("field-effect-cleanup-v1"),
            attempt=bounded_integer(0, 3), updated_at=non_negative_integer, state=text)
        self.valid = {
            "plan_id": "effect_plan_1", "schema_version": "field-effect-cleanup-v1",
            "attempt": 0, "updated_at": 17, "state": "pending",
        }

    def test_the_canonical_document_is_accepted(self) -> None:
        self.assertTrue(self.check(self.valid))

    def test_an_unknown_field_is_rejected(self) -> None:
        self.assertFalse(self.check({**self.valid, "extra": 1}))

    def test_a_missing_field_is_rejected(self) -> None:
        self.assertFalse(self.check({key: value for key, value in self.valid.items()
                                     if key != "state"}))

    def test_an_unconstrained_field_is_required_but_unchecked(self) -> None:
        self.assertTrue(self.check({**self.valid, "plan_id": 42}))
        self.assertFalse(self.check({key: value for key, value in self.valid.items()
                                     if key != "plan_id"}))

    def test_each_declared_check_is_applied(self) -> None:
        self.assertFalse(self.check({**self.valid, "schema_version": "v2"}))
        self.assertFalse(self.check({**self.valid, "attempt": 4}))
        self.assertFalse(self.check({**self.valid, "attempt": True}))
        self.assertFalse(self.check({**self.valid, "updated_at": -1}))
        self.assertFalse(self.check({**self.valid, "state": 1}))

    def test_a_non_dict_is_rejected_without_raising(self) -> None:
        for value in (None, [], "document", 1, object()):
            self.assertFalse(self.check(value))

    def test_a_dict_subclass_is_rejected(self) -> None:
        self.assertFalse(self.check(_Mapping(self.valid)))

    def test_checks_run_in_declaration_order(self) -> None:
        order: list[str] = []

        def record(name: str) -> object:
            def check(value: object) -> bool:
                order.append(name)
                return True

            return check

        closed_document(first=record("first"), second=record("second"),
                        third=record("third"))({"first": 1, "second": 2, "third": 3})
        self.assertEqual(order, ["first", "second", "third"])

    def test_a_failing_check_stops_the_remaining_checks(self) -> None:
        reached: list[str] = []

        def watch(value: object) -> bool:
            reached.append("second")
            return True

        closed_document(first=literal("no"), second=watch)({"first": "yes",
                                                            "second": 1})
        self.assertEqual(reached, [])

    def test_nested_documents_close_their_own_field_set(self) -> None:
        check = closed_document(pending_return=closed_document(
            requested_at=non_negative_integer, scan_id=non_empty_text))
        self.assertTrue(check({"pending_return": {"requested_at": 0, "scan_id": "s"}}))
        self.assertFalse(check({"pending_return": {"requested_at": 0, "scan_id": ""}}))
        self.assertFalse(check({"pending_return": {"requested_at": 0}}))
        self.assertFalse(check({"pending_return": None}))


if __name__ == "__main__":
    unittest.main()
