"""Browse reuses what exact document bytes validated as, across requests (#1226).

Library, search, the attention lens and Attention re-parse the same immutable
case and report documents on every request. A process-lifetime cache keyed by
those exact bytes now hands each request what the bytes validated as. These
tests hold every page and every refusal to the uncached code's, with the cache
cold, warm, evicted and shared between threads, and hold the cache to its bound
and to never sharing a mutable object or a refusal.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mechanical_librarian.application import review_web
from mechanical_librarian.application.review_web import (
    BROWSE_FACTS_REUSE,
    BROWSE_SCOPE_REUSE,
    REVIEW_CASE_REUSE,
    BrowseReviewLibrary,
    DocumentReuse,
    ValidatedDocumentCache,
)
from mechanical_librarian.domain.human_decision import DecisionScope
from mechanical_librarian.domain.review_browse import browse_case_with_id
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    _FrozenJsonDict,
    _FrozenJsonList,
)
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.unit.test_decision_scope import _MUTATIONS, _PINNED, _decided, _outcome

_REUSE: dict[str, DocumentReuse[Any]] = {
    reuse.kind: reuse
    for reuse in (REVIEW_CASE_REUSE, BROWSE_FACTS_REUSE, BROWSE_SCOPE_REUSE)
}
# Key documents are about 12 KB for a case and 23 KB for a case and report pair.
_BOUNDS: dict[str, Callable[[], ValidatedDocumentCache]] = {
    "default": ValidatedDocumentCache,
    "one entry": lambda: ValidatedDocumentCache(1),
    "one pair of bytes": lambda: ValidatedDocumentCache(512, 40_000),
    "nothing fits": lambda: ValidatedDocumentCache(512, 1_000),
}


def _routes(fixture: OwnerResponsivenessFixture) -> dict[str, Callable[[], object]]:
    application = fixture.application
    case = fixture.cases[3]

    def library(lens: str, search: str | None = None, limit: int = 50):
        return lambda: application.library(
            lens=lens, search=search, limit=limit, cursor=None
        )

    position = next(
        index
        for index, item in enumerate(library("all")().items)
        if item["review_case_id"] == case.review_case_id
    )
    assert position > 0
    return {
        "library": library("all"),
        "lens": library("needs_attention"),
        "search": library("all", "0003"),
        "read-ahead": library("all", limit=position),
        "attention": lambda: application.attention(search=None, limit=50, cursor=None),
        "attention search": lambda: application.attention(
            search="0003", limit=50, cursor=None
        ),
    }


def _read(
    fixture: OwnerResponsivenessFixture,
    routes: dict[str, Callable[[], object]],
    validated: ValidatedDocumentCache | None,
) -> dict[str, object]:
    """Every route's page digest or refusal chain, through this cache or none."""
    fixture.application._browse = BrowseReviewLibrary(fixture.browse, validated)
    return {name: _outcome(call) for name, call in routes.items()}


@contextmanager
def _counted() -> Iterator[list[str]]:
    """Record each browse parse the cache exists to skip."""
    calls: list[str] = []
    pair = review_web._case_and_report

    def counting(case_document: str, report_document: str) -> object:
        calls.append("pair")
        return pair(case_document, report_document)

    with patch.object(review_web, "_case_and_report", counting):
        yield calls


def _check_bound(validated: ValidatedDocumentCache) -> None:
    sizes = [size for _value, size in validated._entries.values()]
    assert len(sizes) <= validated._max_entries
    assert validated._document_bytes == sum(sizes) <= validated._max_document_bytes


@pytest.mark.parametrize("bound", _BOUNDS)
def test_every_page_equals_the_uncached_page_cold_warm_and_evicted(
    tmp_path: Path, bound: str
) -> None:
    with _decided(tmp_path) as fixture:
        routes = _routes(fixture)
        with _counted() as uncached_parses:
            expected = _read(fixture, routes, None)
        validated = _BOUNDS[bound]()
        rounds = []
        for _round in range(3):
            with _counted() as parses:
                assert _read(fixture, routes, validated) == expected
            rounds.append(len(parses))
            _check_bound(validated)
    assert all(type(value) is str for value in expected.values()), expected
    # Cold, routes already share the rows an earlier route read.
    assert 0 < rounds[0] <= len(uncached_parses)
    if bound == "default":
        # Warm, every page reuses what its documents validated as.
        assert rounds[1:] == [0, 0]
    if bound == "nothing fits":
        assert rounds == [len(uncached_parses)] * 3
        assert not validated._entries


@pytest.mark.parametrize("mutation", _PINNED)
def test_each_decision_refusal_is_the_uncached_refusal_however_warm(
    tmp_path: Path, mutation: str
) -> None:
    with _decided(tmp_path) as fixture:
        routes = _routes(fixture)
        warm = ValidatedDocumentCache()
        evicted = ValidatedDocumentCache(1)
        _read(fixture, routes, warm)
        _read(fixture, routes, evicted)
        _MUTATIONS[mutation](fixture)
        expected = _read(fixture, routes, None)
        outcomes = {
            "warm": _read(fixture, routes, warm),
            "warm again": _read(fixture, routes, warm),
            "cold": _read(fixture, routes, ValidatedDocumentCache()),
            "evicted": _read(fixture, routes, evicted),
        }
    # A decision is never reused: each is validated in its retained scope.
    assert expected["library"] == _PINNED[mutation]["library"]
    assert outcomes == dict.fromkeys(outcomes, expected)


def _documents(fixture: OwnerResponsivenessFixture) -> tuple[str, str, str]:
    case = fixture.cases[3]
    connection = fixture.repository._connection
    case_document = connection.execute(
        "SELECT document FROM review_cases WHERE review_case_id=?",
        (case.review_case_id,),
    ).fetchone()[0]
    report_document = connection.execute(
        "SELECT document FROM metadata_reports WHERE report_id=?", (case.report_id,)
    ).fetchone()[0]
    return case.review_case_id, case_document, report_document


def _store(fixture: OwnerResponsivenessFixture, table: str, document: str) -> None:
    """Replace the decided case's stored case or report bytes in place."""
    review_case_id, _case_document, _report_document = _documents(fixture)
    connection = fixture.repository._connection
    connection.commit()
    connection.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
    if table == "review_cases":
        connection.execute(
            "UPDATE review_cases SET document=? WHERE review_case_id=?",
            (document, review_case_id),
        )
    else:
        connection.execute(
            "UPDATE metadata_reports SET document=? WHERE report_id=?",
            (document, fixture.cases[3].report_id),
        )
    connection.commit()


def _more_requests(report_document: str) -> str:
    """The same claimed identity and lineage, with one provider lane's count raised."""
    report = json.loads(report_document)
    accounting = report["providers"][0]["request_accounting"]
    accounting["logical_requests"] += 1
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


_TAMPERS: dict[str, tuple[str, Callable[[str], str]]] = {
    # Bytes that name the same case content but are not its canonical form.
    "case not canonical": ("review_cases", lambda document: document + " "),
    # A report keeps its claimed identity and lineage while its content changes,
    # so only its bytes tell the pair apart: the case alone does not name them.
    "report content changed": ("metadata_reports", _more_requests),
    "report not canonical": (
        "metadata_reports",
        lambda document: json.dumps(json.loads(document), indent=1),
    ),
    "report invalid": ("metadata_reports", lambda document: "{}"),
}


@pytest.mark.parametrize("tamper", _TAMPERS)
def test_changed_bytes_read_exactly_as_uncached_and_no_refusal_is_retained(
    tmp_path: Path, tamper: str
) -> None:
    table, change = _TAMPERS[tamper]
    with _decided(tmp_path) as fixture:
        routes = _routes(fixture)
        original = _read(fixture, routes, None)
        _review_case_id, case_document, report_document = _documents(fixture)
        document = case_document if table == "review_cases" else report_document
        warm = ValidatedDocumentCache()
        evicted = ValidatedDocumentCache(1)
        _read(fixture, routes, warm)
        _read(fixture, routes, evicted)
        changed = change(document)
        _store(fixture, table, changed)
        expected = _read(fixture, routes, None)
        outcomes = {
            "warm": _read(fixture, routes, warm),
            "warm again": _read(fixture, routes, warm),
            "cold": _read(fixture, routes, ValidatedDocumentCache()),
            "evicted": _read(fixture, routes, evicted),
        }
        refused = [name for name, value in expected.items() if type(value) is not str]
        retained = [key for key in warm._entries if changed in key]
        _store(fixture, table, document)
        restored = _read(fixture, routes, warm)
    assert outcomes == dict.fromkeys(outcomes, expected)
    if tamper != "report not canonical":
        assert refused and not retained
    if tamper == "report content changed":
        # The store reads it as a valid envelope; only the shown rows, which
        # read its content, refuse it.
        assert type(expected["read-ahead"]) is str
    assert restored == original


def _immutable(value: object, path: str = "value") -> None:
    if value is None or type(value) in {str, int, float, bool}:
        return
    if isinstance(value, enum.Enum):
        return
    if type(value) is _FrozenJsonDict:
        for key, item in value.items():
            _immutable(item, f"{path}[{key!r}]")
        return
    if isinstance(value, (_FrozenJsonList, tuple, frozenset)):
        assert type(value) in {_FrozenJsonList, tuple, frozenset}, path
        for index, item in enumerate(value):
            _immutable(item, f"{path}[{index}]")
        return
    assert dataclasses.is_dataclass(value), (path, type(value))
    assert type(value).__dataclass_params__.frozen, path  # type: ignore[union-attr]
    for field in dataclasses.fields(value):
        _immutable(getattr(value, field.name), f"{path}.{field.name}")


def _exact(left: object, right: object) -> bool:
    """Equal, with every nested value of exactly the same type."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _exact(item, right[key]) for key, item in left.items()
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(map(_exact, left, right))
    if dataclasses.is_dataclass(left) and not isinstance(left, type):
        return all(
            _exact(getattr(left, field.name), getattr(right, field.name))
            for field in dataclasses.fields(left)
        )
    return left == right


def _deface(value: object) -> None:
    """Empty every mutable container a caller was handed."""
    if type(value) is dict:
        for item in value.values():
            _deface(item)
        value.clear()
    elif type(value) is list:
        for item in value:
            _deface(item)
        value.clear()
    elif type(value) is tuple:
        for item in value:
            _deface(item)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            _deface(getattr(value, field.name))


def test_a_retained_object_is_frozen_and_every_caller_gets_a_detached_copy(
    tmp_path: Path,
) -> None:
    with _decided(tmp_path) as fixture:
        routes = _routes(fixture)
        expected = _read(fixture, routes, None)
        validated = ValidatedDocumentCache()
        _read(fixture, routes, validated)
        entries = dict(validated._entries)
        assert {key[0] for key in entries} == set(_REUSE)
        handed = []
        for key, (stored, _size) in entries.items():
            _immutable(stored)
            parsed = _parsed(key)
            first = validated.reused(_REUSE[key[0]], *key[1:])
            # What a caller is handed is what parsing hands it, type for type.
            assert _exact(first, parsed), key[0]
            handed.append(first)
        for value in handed:
            _deface(value)
        assert _read(fixture, routes, validated) == expected


def _parsed(key: tuple[str, ...]) -> object:
    """What the uncached code parses these documents as."""
    if key[0] == "review_case":
        return ReviewCase.from_json(key[1])
    if key[0] == "browse_facts":
        return review_web._case_and_report(key[1], key[2])
    case, review_case_id = browse_case_with_id(key[1])
    return DecisionScope.of_browse_case(review_case_id, case)


_TEXT = DocumentReuse[str]("text", str.upper, str.lower)


def test_the_cache_holds_its_entry_and_byte_bounds_least_recently_used_first() -> None:
    validated = ValidatedDocumentCache(2, 10)
    validated.retain(_TEXT, "a", "aaa")
    validated.retain(_TEXT, "b", "bbb")
    assert validated.reused(_TEXT, "aaa") == "a"
    validated.retain(_TEXT, "c", "ccc")
    # The least recently used entry goes first, by entry count.
    assert validated.reused(_TEXT, "bbb") is None
    assert [key[1] for key in validated._entries] == ["aaa", "ccc"]
    # And by byte count, with the bytes UTF-8 measures.
    validated.retain(_TEXT, "d", "dddddd", "é")
    assert [key[1] for key in validated._entries] == ["dddddd"]
    assert validated._document_bytes == 8
    # One document larger than the whole bound is never held.
    validated.retain(_TEXT, "e", "e" * 11)
    assert validated.reused(_TEXT, "e" * 11) is None
    # Retaining what is already held changes nothing.
    validated.retain(_TEXT, "x", "dddddd", "é")
    assert validated.reused(_TEXT, "dddddd", "é") == "d"
    assert validated._document_bytes == 8
    # Only exact text is a key, and kinds never share an entry.
    validated.retain(_TEXT, "f", b"ff")
    assert validated.reused(_TEXT, b"ff") is None
    other = DocumentReuse[str]("other", str.upper, str.lower)
    assert validated.reused(other, "dddddd", "é") is None
    _check_bound(validated)


def test_threads_sharing_one_cache_keep_its_bound_and_its_values() -> None:
    validated = ValidatedDocumentCache(16, 400)
    failures: list[str] = []
    start = threading.Barrier(8)

    def churn(worker: int) -> None:
        start.wait()
        for step in range(2_000):
            document = f"{(worker * 7 + step) % 40:03d}" * (1 + step % 5)
            value = validated.reused(_TEXT, document)
            if value is None:
                validated.retain(_TEXT, document[::-1], document)
            elif value != document[::-1]:
                failures.append(document)

    threads = [threading.Thread(target=churn, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    _check_bound(validated)


class _Interleaved(OrderedDict[tuple[str, ...], tuple[object, int]]):
    """Entries that run `between` once, just after the first call `at` returns."""

    def __init__(
        self,
        entries: OrderedDict[tuple[str, ...], tuple[object, int]],
        at: str,
        between: Callable[[], None],
    ) -> None:
        self._at: str | None = at
        self._between = between
        super().__init__(entries)

    def _then(self, name: str, result: Any) -> Any:
        if self._at == name:
            self._at = None
            self._between()
        return result

    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self._then("get", super().get(*args, **kwargs))

    def __contains__(self, key: object) -> bool:
        return bool(self._then("contains", super().__contains__(key)))


def _race(
    validated: ValidatedDocumentCache,
    at: str,
    first: Callable[[], object],
    second: Callable[[], object],
) -> object:
    """Run `first`, letting `second` loose on another thread at `at` inside it.

    `first` resumes only once `second` has finished or has reached the cache's
    lock, so without the lock `second` always lands inside `first`, and with it
    never does. Nothing sleeps: events order the two threads exactly.
    """
    reached = threading.Event()
    lock = validated._lock
    failures: list[BaseException] = []

    class Announcing:
        def __enter__(self) -> None:
            if threading.current_thread() is racer:
                reached.set()
            lock.acquire()

        def __exit__(self, *exc_info: object) -> None:
            lock.release()

    def run_second() -> None:
        try:
            second()
        except BaseException as error:
            failures.append(error)
        finally:
            reached.set()

    def release() -> None:
        racer.start()
        assert reached.wait(timeout=30)

    racer = threading.Thread(target=run_second)
    validated._entries = _Interleaved(validated._entries, at, release)
    with patch.object(validated, "_lock", Announcing()):
        try:
            result = first()
        finally:
            if racer.ident is not None:
                racer.join(timeout=30)
    assert not racer.is_alive()
    assert failures == []
    return result


def test_the_lock_keeps_an_eviction_out_of_a_read() -> None:
    validated = ValidatedDocumentCache(1)
    validated.retain(_TEXT, "a", "aaa")
    # Unlocked, the eviction lands between the read's lookup and its reordering.
    read = partial(validated.reused, _TEXT, "aaa")
    evict = partial(validated.retain, _TEXT, "b", "bbb")
    assert _race(validated, "get", read, evict) == "a"
    assert [key[1] for key in validated._entries] == ["bbb"]
    _check_bound(validated)


def test_the_lock_keeps_a_duplicate_out_of_a_retain() -> None:
    validated = ValidatedDocumentCache()
    # Unlocked, both retains find the key absent and both count its bytes.
    retain = partial(validated.retain, _TEXT, "a", "aaa")
    _race(validated, "contains", retain, retain)
    assert [key[1] for key in validated._entries] == ["aaa"]
    _check_bound(validated)


@pytest.mark.parametrize("bound", ("default", "one entry"))
def test_concurrent_pages_through_one_cache_equal_the_uncached_pages(
    tmp_path: Path, bound: str
) -> None:
    def pages(browse: BrowseReviewLibrary) -> dict[str, str]:
        return {
            name: json.dumps(call(), sort_keys=True, default=str)
            for name, call in {
                "library": lambda: browse.library(
                    lens="all", search=None, limit=50, cursor=None
                ),
                "lens": lambda: browse.library(
                    lens="needs_attention", search=None, limit=50, cursor=None
                ),
                "search": lambda: browse.library(
                    lens="all", search="0003", limit=50, cursor=None
                ),
                "attention": lambda: browse.attention(
                    search=None, limit=50, cursor=None
                ),
            }.items()
        }

    with _decided(tmp_path) as fixture:
        expected = pages(BrowseReviewLibrary(fixture.browse))
        browse = BrowseReviewLibrary(fixture.browse, _BOUNDS[bound]())
        observed: list[dict[str, str]] = []
        failures: list[BaseException] = []
        start = threading.Barrier(6)

        def read() -> None:
            try:
                start.wait()
                for _ in range(4):
                    observed.append(pages(browse))
            except BaseException as error:
                failures.append(error)

        threads = [threading.Thread(target=read) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert failures == []
    assert len(observed) == 24
    assert all(page == expected for page in observed)
