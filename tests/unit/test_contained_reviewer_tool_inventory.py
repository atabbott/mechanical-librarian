from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.verify_contained_reviewer_tool_inventory import (
    _probe_version,
    _validate_requests,
)

from mechanical_librarian.application.contained_reviewer_contract import COVER_MODEL


def test_version_probe_is_bounded_and_redacts_failure() -> None:
    with patch(
        "scripts.verify_contained_reviewer_tool_inventory.subprocess.run",
        side_effect=subprocess.TimeoutExpired(("secret-path",), 10),
    ) as run:
        with pytest.raises(
            RuntimeError, match="^contained Codex version probe failed$"
        ):
            _probe_version(Path("/private/codex"), "/private/home")
    assert run.call_args.kwargs["timeout"] == 10


_REQUEST: dict[str, object] = {"model": COVER_MODEL, "input": "fixture", "tools": []}


@pytest.mark.parametrize(
    ("requests", "failure"),
    (
        ([_REQUEST], None),
        # The binary may retry after the fixture's 503 before it is stopped.
        ([_REQUEST, dict(_REQUEST), dict(_REQUEST)], None),
        ([_REQUEST, _REQUEST | {"input": "other"}], "an ambiguous fixture request"),
        (
            [_REQUEST, _REQUEST | {"tools": [{"type": "shell"}]}],
            "a model-callable tool",
        ),
        ([_REQUEST | {"model": "other"}] * 2, "a model-callable tool"),
        ([], "no fixture request"),
    ),
    ids=("one", "identical-retries", "differing", "tool-retry", "wrong-model", "none"),
)
def test_every_captured_request_is_judged_and_retries_are_accepted(
    requests: list[dict[str, object]], failure: str | None
) -> None:
    if failure is None:
        _validate_requests(requests)
        return
    with pytest.raises(
        RuntimeError, match=f"^contained Codex (emitted|exposed) {failure}$"
    ):
        _validate_requests(requests)
