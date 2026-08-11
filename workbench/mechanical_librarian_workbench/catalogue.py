"""Closed, typed contributor scenarios and inspectors, never a plugin registry."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    kind: str
    operation: str
    alternate_operations: tuple[str, ...] = ()

    @property
    def operations(self) -> tuple[str, ...]:
        return (self.operation, *self.alternate_operations)

    def to_json(self) -> str:
        return json.dumps(
            {"kind": self.kind, "name": self.name, "operations": list(self.operations)},
            separators=(",", ":"), sort_keys=True,
        )


CAPABILITIES = (
    Capability("report-observe", "scenario", "run_metadata_report"),
    Capability(
        "report-inspect", "inspector", "reopen_metadata_report",
        ("reopen_current_metadata_report",),
    ),
    Capability("cover-inspect", "inspector", "reopen_cover_preview"),
    Capability("proposal-create", "scenario", "create_proposal_set"),
    Capability("proposal-inspect", "inspector", "reopen_proposal_set"),
    Capability("case-admit", "scenario", "admit_review_case"),
    Capability("case-inspect", "inspector", "reopen_review_case"),
    Capability("catalogue-refresh", "scenario", "run_catalogue"),
    Capability("catalogue-list", "inspector", "run_catalogue"),
    Capability("catalogue-get", "inspector", "run_catalogue"),
    Capability("batch-preview", "scenario", "preview_batch"),
    Capability("batch-inspect", "inspector", "inspect_batch"),
    Capability("batch-run", "scenario", "run_batch"),
    Capability("decision-preview", "inspector", "preview_shadow_decision_case"),
    Capability("contained-reviewer", "scenario", "run_contained_reviewer"),
    Capability(
        "cover-packet-review", "scenario", "run_cover_packet_reviewer",
        ("reopen_metadata_report", "reopen_cover_preview"),
    ),
    Capability(
        "cover-identity-report", "inspector", "reopen_metadata_report",
        ("reopen_cover_preview",),
    ),
    Capability(
        "cover-observation-report", "scenario", "run_cover_packet_reviewer",
        ("reopen_metadata_report", "reopen_cover_preview"),
    ),
)
