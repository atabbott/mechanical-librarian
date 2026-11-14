"""The metadata-report schema version the tests use, in one place (#1796).

The current version is the product's `REPORT_SCHEMA`; its literal is pinned
once, in `tests/architecture/test_document_versions.py`, so a changed constant
still fails a test. The previous version is the one before it.
"""

from __future__ import annotations

from mechanical_librarian.domain.metadata_report import REPORT_SCHEMA

CURRENT_REPORT_SCHEMA = REPORT_SCHEMA
_PREFIX, _NUMBER = REPORT_SCHEMA.rsplit("-v", 1)
PREVIOUS_REPORT_SCHEMA = f"{_PREFIX}-v{int(_NUMBER) - 1}"
