"""The one bounded, redacted result convention for source-only scenarios."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_MAX_RESULT_BYTES = 16_384


@dataclass(frozen=True)
class WorkbenchResult:
    name: str
    summary: str
    data: dict[str, str]
    artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.name) is None
                or not self.summary or len(self.summary) > 512):
            raise ValueError("invalid workbench result")
        if (set(self.data) - {"artifact_bytes", "artifact_sha256", "status"}
                or self.data.get("status") != "completed"
                or re.fullmatch(r"[0-9]+", self.data.get("artifact_bytes", "")) is None
                or re.fullmatch(r"[0-9a-f]{64}", self.data.get("artifact_sha256", "")) is None
                or any(re.fullmatch(r"workbench_artifact_[0-9a-f]{64}", value) is None
                       for value in self.artifacts)):
            raise ValueError("workbench result is not a bounded projection")
        if any(len(value) > 128 for value in (*self.artifacts, *self.data.values())):
            raise ValueError("workbench result exposes an unbounded value")
        if len(self.to_json().encode()) > _MAX_RESULT_BYTES:
            raise ValueError("workbench result is too large")

    def to_json(self) -> str:
        return json.dumps(
            {
                "artifacts": list(self.artifacts),
                "data": self.data,
                "name": self.name,
                "schema_version": "workbench-result-v1",
                "summary": self.summary,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def render(self, format: str) -> str:
        if format == "json":
            return self.to_json()
        if format == "summary":
            return self.summary
        raise ValueError("invalid workbench format")
