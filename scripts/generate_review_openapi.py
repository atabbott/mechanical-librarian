#!/usr/bin/env python3
"""Generate the deterministic authenticated review API contract."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from argon2 import PasswordHasher

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.interfaces.web import create_review_app


class _SchemaRepository:
    def __getattr__(self, name: str):
        raise RuntimeError(f"schema generation called repository method {name}")


def generate() -> str:
    with tempfile.TemporaryDirectory() as directory:
        password_hash = Path(directory) / "synthetic-owner.hash"
        password_hash.write_text(
            PasswordHasher().hash("synthetic-schema-only-password"),
            encoding="utf-8",
        )
        os.chmod(password_hash, 0o600)
        app = create_review_app(
            _SchemaRepository(),  # type: ignore[arg-type]
            ReviewWebConfiguration(
                canonical_origin="https://review.invalid",
                owner_password_hash_file=str(password_hash),
            ),
        )
        return (
            json.dumps(
                app.openapi(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/reference/review-openapi-v1.json"),
    )
    arguments = parser.parse_args()
    arguments.output.write_text(generate(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
