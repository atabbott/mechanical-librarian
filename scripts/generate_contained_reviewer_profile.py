#!/usr/bin/env python3
"""Author the supported image profile from its verified reviewer artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    load_contained_reviewer_profile_bytes,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    COVER_MEMORY_BYTES,
    COVER_MODEL,
    COVER_PROCESS_LIMIT,
    canonical_json,
)


def generate_profile(artifacts: Path) -> bytes:
    """Use build metadata; never execute or authenticate the reviewer here."""
    installed = "/opt/mechanical-librarian/reviewer"
    version = json.loads((artifacts / "metadata/codex-package.json").read_bytes())[
        "version"
    ]
    payload = canonical_json(
        {
            "schema_version": "contained-reviewer-profile-v1",
            "codex": {
                "path": installed + "/bin/codex",
                "sha256": hashlib.sha256(
                    (artifacts / "bin/codex").read_bytes()
                ).hexdigest(),
                "version": "codex-cli " + version,
            },
            "sandbox": {
                "path": installed + "/bin/bwrap",
                "sha256": hashlib.sha256(
                    (artifacts / "bin/bwrap").read_bytes()
                ).hexdigest(),
                "version": (artifacts / "metadata/bubblewrap-version.txt")
                .read_text()
                .strip(),
            },
            "auth_store_path": "/data/reviewer/auth",
            "job_root_path": "/data/reviewer/jobs",
            "model": COVER_MODEL,
            "reasoning": "medium",
            "limits": {
                "timeout_seconds": 180,
                "output_bytes": 65_536,
                "file_bytes": 1_048_576,
                "memory_bytes": COVER_MEMORY_BYTES,
                "processes": COVER_PROCESS_LIMIT,
            },
        }
    ).encode()
    load_contained_reviewer_profile_bytes(payload)
    return payload


if __name__ == "__main__":
    artifacts = Path(sys.argv[1])
    (artifacts / "profile.json").write_bytes(generate_profile(artifacts))
