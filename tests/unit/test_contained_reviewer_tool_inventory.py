from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.verify_contained_reviewer_tool_inventory import _probe_version


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
