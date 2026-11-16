"""Session memory holds one value per session for a bounded time (#1477 S13)."""

from __future__ import annotations

import pytest

from mechanical_librarian.application.session_memory import SessionMemory


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_a_value_reads_back_until_its_time_to_live_passes() -> None:
    clock = _Clock()
    memory: SessionMemory[str] = SessionMemory(900, clock)
    memory.put("token-a", "first")
    clock.now += 900
    assert memory.get("token-a") == "first"
    clock.now += 0.001
    assert memory.get("token-a") is None


def test_a_put_replaces_the_session_value_and_leaves_others() -> None:
    memory: SessionMemory[str] = SessionMemory(900, _Clock())
    memory.put("token-a", "first")
    memory.put("token-b", "other")
    memory.put("token-a", "second")
    assert memory.get("token-a") == "second"
    assert memory.get("token-b") == "other"


def test_discard_forgets_only_that_session() -> None:
    memory: SessionMemory[str] = SessionMemory(900, _Clock())
    memory.put("token-a", "first")
    memory.put("token-b", "other")
    memory.discard("token-a")
    memory.discard("token-never-held")
    assert memory.get("token-a") is None
    assert memory.get("token-b") == "other"


def test_the_oldest_session_is_evicted_at_the_bound() -> None:
    clock = _Clock()
    memory: SessionMemory[int] = SessionMemory(900, clock, maximum_sessions=2)
    memory.put("token-a", 1)
    clock.now += 1
    memory.put("token-b", 2)
    clock.now += 1
    memory.put("token-c", 3)
    assert memory.get("token-a") is None
    assert (memory.get("token-b"), memory.get("token-c")) == (2, 3)


def test_the_token_itself_is_never_held() -> None:
    memory: SessionMemory[str] = SessionMemory(900, _Clock())
    memory.put("secret-session-token", "value")
    assert "secret-session-token" not in repr(vars(memory))


@pytest.mark.parametrize("token", ["", 7, None], ids=["empty", "integer", "none"])
def test_an_invalid_token_is_refused(token: object) -> None:
    memory: SessionMemory[str] = SessionMemory(900, _Clock())
    with pytest.raises(ValueError):
        memory.put(token, "value")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ttl", "maximum"),
    [(0, 1), (86_401, 1), (900, 0)],
    ids=["zero-ttl", "ttl-over-a-day", "no-sessions"],
)
def test_bounds_outside_the_contract_are_refused(ttl: float, maximum: int) -> None:
    with pytest.raises(ValueError):
        SessionMemory(ttl, _Clock(), maximum_sessions=maximum)
