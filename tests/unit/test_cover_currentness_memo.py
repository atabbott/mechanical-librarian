"""Browse reuses what a cover currentness document validated as (#1237).

`ml_cover_consideration_current` re-validated each row's currentness document
on every call of every browse statement. The validated facts are a function of
the document's bytes alone, so a bounded memo keyed by those exact bytes now
hands them back; provider fetching, the bound replay reference and whether a
browse observation is installed are still read on every call. These tests hold
every answer, page, count and refusal to the uncached code's across each input
change, and hold the memo to its bound and to never retaining a failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from functools import partial
from itertools import repeat
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.persistence import metadata_reports
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    cover_currentness_cache,
    open_repository,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationCurrentness,
)
from mechanical_librarian.application.cover_packet_semantics import (
    canonical_packet_json,
)
from mechanical_librarian.application.review_web import (
    CoverReadObservation,
    ValidatedDocumentCache,
)
from mechanical_librarian.bootstrap import composition
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.unit.test_browse_attention_statements import (
    _LABELS,
    _SEARCHES,
    _case_ids,
    _consideration,
    _cover_selection,
    _hex,
    _human_decision,
    _library,
    _outcome,
)

_REPLAY = "cover_replay_" + "1" * 64
_OTHER_REPLAY = "cover_replay_" + "2" * 64
_ANSWER = {"inactive": 0, "current": 1, "refresh_required": 2}
Fetching = Callable[[str, str, str, str], bool]


def _facts(item: str, fetching: bool = False, replay: str = _REPLAY) -> str:
    return canonical_packet_json(
        {
            "connection": "primary",
            "library": "books",
            "item": item,
            "report_id": "report-" + item,
            "provider_fetching": fetching,
            "manual_ready": True,
            "replay_reference": replay,
        }
    )


_VALID = tuple(
    _facts(item, fetching, replay)
    for item in ("a", "b")
    for fetching in (False, True)
    for replay in (_REPLAY, _OTHER_REPLAY)
)
# Documents that grant nothing, each refused by a different check.
_REFUSED = (
    "null",
    "not-json",
    "[]",
    "{}",
    json.dumps(json.loads(_facts("a")), indent=1),
    _facts("a").replace('"item":"a"', '"item":""'),
    _facts("a").replace(_REPLAY, "cover_replay_short"),
    _facts("a").replace('"manual_ready":true', '"manual_ready":1'),
    _facts("a")[:-1] + ',"extra":1}',
)


# The oracle is the implementation as it stood before #1237, so a change to
# the candidate's own derivation cannot also change what it is compared with.
def _pre_1237_currentness(
    document: str,
    replay_reference: str,
    provider_fetching: Callable[[str, str, str, str], bool],
) -> CoverConsiderationCurrentness:
    """`cover_currentness_from_facts` at 96deda67, verbatim but for its name."""
    if (
        type(document) is not str
        or type(replay_reference) is not str
        or re.fullmatch(r"cover_replay_[0-9a-f]{64}", replay_reference) is None
    ):
        return "inactive"
    try:
        value = json.loads(document)
        if (
            type(value) is not dict
            or set(value)
            != {
                "connection",
                "library",
                "item",
                "report_id",
                "provider_fetching",
                "manual_ready",
                "replay_reference",
            }
            or any(
                type(value[key]) is not str or not value[key]
                for key in (
                    "connection",
                    "library",
                    "item",
                    "report_id",
                    "replay_reference",
                )
            )
            or type(value["manual_ready"]) is not bool
            or type(value["provider_fetching"]) is not bool
            or re.fullmatch(r"cover_replay_[0-9a-f]{64}", value["replay_reference"])
            is None
            or canonical_packet_json(value) != document
        ):
            return "inactive"
        fetching = provider_fetching(
            value["connection"], value["library"], value["item"], value["report_id"]
        )
        if type(fetching) is not bool or fetching != value["provider_fetching"]:
            return "inactive"
        return (
            "current"
            if value["replay_reference"] == replay_reference
            else "refresh_required"
        )
    except Exception:
        return "inactive"


def _uncached(document: str, replay: str, fetching: Fetching) -> int:
    """The answer the function gave before #1237, through no memo at all."""
    return _ANSWER[_pre_1237_currentness(document, replay, fetching)]


def _ask(repository: SQLiteMetadataReportRepository, document: str, replay: str):
    return repository._connection.execute(
        "SELECT ml_cover_consideration_current(?,?)", (document, replay)
    ).fetchone()[0]


def _held(memo: ValidatedDocumentCache) -> int:
    return len(memo._entries)


@contextmanager
def _counted_parses() -> Iterator[list[str]]:
    real = metadata_reports.cover_currentness_facts
    parsed: list[str] = []

    def counted(document: str):
        parsed.append(document)
        return real(document)

    with patch.object(metadata_reports, "cover_currentness_facts", counted):
        yield parsed


def test_memoised_answers_equal_the_uncached_answer_for_every_input(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    memo = cover_currentness_cache()
    fetching_items: set[tuple[str, str, str, str]] = set()

    def fetching(connection: str, library: str, item: str, report: str) -> bool:
        return (connection, library, item, report) in fetching_items

    a = ("primary", "books", "a", "report-a")
    b = ("primary", "books", "b", "report-b")
    # Live inputs: whether an observation is installed and what it holds, the
    # provider's fetching state otherwise, and the bound replay reference.
    observations: tuple[CoverReadObservation | None, ...] = (
        None,
        (("policy", _REPLAY), frozenset()),
        (("policy", _REPLAY), frozenset({a})),
        (("policy", _REPLAY), frozenset({a, b})),
    )
    answers = set()
    try:
        repository.bind_cover_consideration_currentness(fetching, memo)
        for live in (set(), {a}, {b}, {a, b}):
            fetching_items.clear()
            fetching_items.update(live)
            for observation in observations:
                held = (
                    nullcontext()
                    if observation is None
                    else repository.install_browse_observation(observation)
                )
                with held:
                    probe: Fetching = (
                        fetching
                        if observation is None
                        else lambda *identity, o=observation: identity in o[1]
                    )
                    for replay in (_REPLAY, _OTHER_REPLAY, "replay"):
                        for document in _VALID + _REFUSED:
                            expected = _uncached(document, replay, probe)
                            # Asked twice, so the second answer is a memo hit.
                            assert _ask(repository, document, replay) == expected
                            assert _ask(repository, document, replay) == expected
                            answers.add((document, expected))
        # Each valid document was answered more than one way, so no answer
        # could have been held for it.
        for document in _VALID:
            assert {answer for held, answer in answers if held == document} >= {0, 1}
        assert _held(memo) == len(_VALID)
    finally:
        repository.close()


def test_a_changed_input_is_never_served_from_the_memo(tmp_path: Path) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    memo = cover_currentness_cache()
    fetching = [False]
    document = _facts("a")
    try:
        repository.bind_cover_consideration_currentness(
            lambda *_identity: fetching[0], memo
        )
        with _counted_parses() as parsed:
            assert _ask(repository, document, _REPLAY) == 1
            assert _ask(repository, document, _REPLAY) == 1
            assert parsed == [document]
            # Another replay reference: a refresh, not the held answer.
            assert _ask(repository, document, _OTHER_REPLAY) == 2
            # The provider starts fetching: no authority.
            fetching[0] = True
            assert _ask(repository, document, _REPLAY) == 0
            fetching[0] = False
            # An observation that says it is fetching, then one that does not.
            identity = ("primary", "books", "a", "report-a")
            with repository.install_browse_observation(
                (("policy", _REPLAY), frozenset({identity}))
            ):
                assert _ask(repository, document, _REPLAY) == 0
            with repository.install_browse_observation(
                (("policy", _REPLAY), frozenset())
            ):
                assert _ask(repository, document, _REPLAY) == 1
            # Other bytes for the same target are validated afresh.
            superseding = _facts("a", replay=_OTHER_REPLAY)
            assert _ask(repository, superseding, _REPLAY) == 2
            assert parsed == [document, superseding]
            # A cutover binds a new generation's fetching reader to the same
            # memo; the facts are reused and the new reader is asked.
            repository.bind_cover_consideration_currentness(lambda *_: True, memo)
            assert _ask(repository, document, _REPLAY) == 0
            repository.bind_cover_consideration_currentness(lambda *_: False, memo)
            assert _ask(repository, document, _REPLAY) == 1
            assert parsed == [document, superseding]
    finally:
        repository.close()


def test_absolute_answers_need_a_valid_replay_and_a_bool_fetching_answer(
    tmp_path: Path,
) -> None:
    """Answers stated outright, independent of any oracle."""
    repository = open_repository(tmp_path / "application.sqlite3")
    answer: list[object] = [False]
    try:
        repository.bind_cover_consideration_currentness(
            lambda *_identity: answer[0],  # type: ignore[arg-type]
            cover_currentness_cache(),
        )
        idle, fetching = _facts("a"), _facts("b", fetching=True)
        for _attempt in range(2):
            assert _ask(repository, idle, _REPLAY) == 1
            assert _ask(repository, idle, _OTHER_REPLAY) == 2
            # A replay reference that is not one grants nothing, even where
            # the document's own reference has the same malformed text.
            for replay in ("replay", "cover_replay_short", "cover_replay_" + "1" * 63):
                assert _ask(repository, idle, replay) == 0
                assert _ask(repository, idle.replace(_REPLAY, replay), replay) == 0
            # Only a bool from the provider counts; 0 and 1 compare equal to
            # False and True but are not answers.
            for value, document in ((0, idle), (1, fetching), ("unknown", idle)):
                answer[0] = value
                assert _ask(repository, document, _REPLAY) == 0, value
            answer[0] = True
            assert _ask(repository, fetching, _REPLAY) == 1
            answer[0] = False
    finally:
        repository.close()


def test_no_failure_is_retained(tmp_path: Path) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    memo = cover_currentness_cache()
    failing = [True]

    def fetching(*_identity: str) -> bool:
        if failing[0]:
            raise RuntimeError("synthetic fetching failure")
        return False

    try:
        repository.bind_cover_consideration_currentness(fetching, memo)
        with _counted_parses() as parsed:
            for document in _REFUSED:
                for _attempt in range(2):
                    assert _ask(repository, document, _REPLAY) == 0
            # A refused document is validated again each time it is asked.
            assert parsed == [value for value in _REFUSED for _ in range(2)]
            assert _held(memo) == 0
            # A fetching failure answers 0 and is not held as the answer.
            assert _ask(repository, _facts("a"), _REPLAY) == 0
            failing[0] = False
            assert _ask(repository, _facts("a"), _REPLAY) == 1
            assert _held(memo) == 1
        # Neither is a failure of the validation itself.
        with patch.object(
            metadata_reports,
            "cover_currentness_facts",
            side_effect=RuntimeError("synthetic validation failure"),
        ):
            assert _ask(repository, _facts("b"), _REPLAY) == 0
        assert _held(memo) == 1
        assert _ask(repository, _facts("b"), _REPLAY) == 1
    finally:
        repository.close()


def test_the_memo_is_bounded(tmp_path: Path) -> None:
    memo = cover_currentness_cache()
    assert (memo._max_entries, memo._max_document_bytes) == (16_384, 8 * 1024 * 1024)
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        repository.bind_cover_consideration_currentness(lambda *_: False, memo)
        documents = [_facts(f"item-{index}") for index in range(16_384 + 64)]
        for document in documents:
            assert _ask(repository, document, _REPLAY) == 1
        assert _held(memo) == 16_384
        # The least recently used were evicted and are validated again.
        with _counted_parses() as parsed:
            assert _ask(repository, documents[0], _REPLAY) == 1
            assert _ask(repository, documents[-1], _REPLAY) == 1
        assert parsed == [documents[0]]
        small = ValidatedDocumentCache(3)
        repository.bind_cover_consideration_currentness(lambda *_: False, small)
        for document in documents[:10]:
            assert _ask(repository, document, _REPLAY) == 1
            assert _held(small) <= 3
    finally:
        repository.close()


def test_readers_share_one_memo_across_threads(tmp_path: Path) -> None:
    memo = cover_currentness_cache()
    documents = [_facts(f"item-{index}", index % 3 == 0) for index in range(200)]
    expected = [
        _uncached(document, _REPLAY, lambda *_: False) for document in documents
    ]
    errors: list[str] = []

    def read(index: int) -> None:
        repository = open_repository(tmp_path / f"reader-{index}.sqlite3")
        try:
            repository.bind_cover_consideration_currentness(lambda *_: False, memo)
            for _round in range(5):
                answers = [_ask(repository, value, _REPLAY) for value in documents]
                if answers != expected:
                    errors.append(f"reader {index} disagreed")
        finally:
            repository.close()

    readers = [threading.Thread(target=read, args=(index,)) for index in range(4)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(60)
    assert errors == []
    # Every document validated, fetching or not, so each is held once.
    assert _held(memo) == len(documents)


def _attention(
    repository: SQLiteMetadataReportRepository, search: str | None
) -> tuple[object, ...]:
    page = repository.read_current_attention_page(search, 50, 0, _LABELS)
    return page.rows, page.total


def _browse(repository: SQLiteMetadataReportRepository) -> tuple[object, ...]:
    """Every Library and Attention answer, refusals included."""
    attention: list[object] = []
    for search in _SEARCHES:
        attention.append(_outcome(partial(_attention, repository, search)))
        attention.append(
            _outcome(
                partial(repository.count_current_attention_fields, search, _LABELS)
            )
        )
    attentive = tuple(
        repository._field_effects._current_attention_review_case_ids(
            "primary", destination
        )
        for destination in (None, "cover")
    )
    return _outcome(partial(_library, repository)), tuple(attention), attentive


def _repoint(repository: SQLiteMetadataReportRepository, case_id: str) -> None:
    """Supersede a case's consideration with one whose currentness differs."""
    consideration_id, currentness = _consideration(repository, case_id)
    row = repository._connection.execute(
        "SELECT review_case_id,report_id,replay_digest,document_digest,document "
        "FROM cover_considerations WHERE consideration_id=?",
        (consideration_id,),
    ).fetchone()
    successor = "cover_consideration_" + _hex(f"successor:{consideration_id}")
    document = json.loads(row[4])
    document["consideration_id"] = successor
    currentness["provider_fetching"] = not currentness["provider_fetching"]
    repository._connection.execute(
        "INSERT INTO cover_considerations(consideration_id,review_case_id,"
        "report_id,replay_digest,document_digest,supersedes_id,occurred_at,"
        "document,currentness_document) VALUES(?,?,?,?,?,?,2,?,?)",
        (
            successor,
            row[0],
            row[1],
            row[2],
            hashlib.sha256(row[4].encode()).hexdigest(),
            consideration_id,
            json.dumps(document),
            canonical_packet_json(currentness),
        ),
    )
    repository._connection.execute(
        "UPDATE current_cover_considerations SET consideration_id=? "
        "WHERE review_case_id=?",
        (successor, case_id),
    )
    repository._connection.commit()


@pytest.fixture(scope="module")
def browse_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[OwnerResponsivenessFixture]:
    fixture = OwnerResponsivenessFixture(
        tmp_path_factory.mktemp("currentness"), 30, mixed=True
    )
    try:
        yield fixture
    finally:
        fixture.close()


def test_browse_with_a_warm_memo_equals_browse_without_one(
    browse_fixture: OwnerResponsivenessFixture,
) -> None:
    fixture = browse_fixture
    repository = fixture.repository
    connection = repository._connection
    memo = cover_currentness_cache()
    fetching_items: set[tuple[str, str, str, str]] = set()

    def fetching(connection: str, library: str, item: str, report: str) -> bool:
        return (connection, library, item, report) in fetching_items

    cases = _case_ids(repository)
    identities = [
        (
            str(value["connection"]),
            str(value["library"]),
            str(value["item"]),
            str(value["report_id"]),
        )
        for value in (_consideration(repository, case)[1] for case in cases)
    ]

    def observations() -> tuple[CoverReadObservation | None, ...]:
        policy, replay = repository._current_cover_authority
        assert policy is not None and replay is not None
        return (
            None,
            ((policy, replay), frozenset()),
            ((policy, replay), frozenset(identities[::3])),
            ((policy, f"cover_replay_{_hex('another replay')}"), frozenset()),
        )

    def compare() -> set[str]:
        """Browse with the shared memo, warm, and with no memo; they agree."""
        seen = set()
        for observation in observations():
            reads = []
            for held in (memo, memo, ValidatedDocumentCache(0)):
                repository.bind_cover_consideration_currentness(fetching, held)
                if observation is None:
                    reads.append(_browse(repository))
                    continue
                repository.bind_cover_read_observation(repeat(observation).__next__)
                with repository.browse_observation():
                    reads.append(_browse(repository))
            assert reads[0] == reads[1] == reads[2], observation
            seen.add(repr(reads[2]))
        repository.bind_cover_consideration_currentness(fetching, memo)
        return seen

    states: dict[str, set[str]] = {"fixture": compare()}
    assert _held(memo) >= len(cases)
    with _counted_parses() as parsed:
        for observation in observations():
            if observation is None:
                continue
            repository.bind_cover_read_observation(repeat(observation).__next__)
            with repository.browse_observation():
                _browse(repository)
        _browse(repository)
    # Every currentness document was already held, so a warm browse
    # validates none of them again.
    assert parsed == []
    # Provider fetching changes without any database write.
    fetching_items.update(identities[1::2])
    states["fetching"] = compare()
    fetching_items.clear()
    # New decisions and a cover selection.
    _human_decision(repository, cases[2], "cover")
    _cover_selection(repository, cases[6], "owner", cases[6])
    connection.commit()
    states["decided"] = compare()
    # A connection suspension and its recovery.
    connection.execute(
        "INSERT INTO field_effect_suspensions(scope,connection_name,destination,"
        "reason,updated_at,document) VALUES('connection','primary','',"
        "'field_unavailable',1,'{}')"
    )
    connection.commit()
    states["suspended"] = compare()
    connection.execute("DELETE FROM field_effect_suspensions")
    connection.commit()
    states["recovered"] = compare()
    # New considerations: other currentness bytes for held targets.
    for case in cases[:6]:
        _repoint(repository, case)
    states["superseded"] = compare()
    # A Settings cutover recomposes the cover authority on the shared memo.
    policy_before = repository._current_cover_authority[0]
    composition._compose_cover_considerations(
        repository,
        fixture.root / "artifacts",
        replace(fixture.review, cover_avoid_promotional_text=True),
        fixture.reviewer_access,
        fixture.recovery,
        memo,
    )
    assert repository._current_cover_authority[0] != policy_before
    states["cutover"] = compare()
    # Each state answered differently from the one before it, so the
    # comparison was not made only where every answer agrees.
    names = list(states)
    for before, after in zip(names, names[1:], strict=False):
        assert states[before] != states[after], (before, after)
