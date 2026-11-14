"""The metadata-report schema version the tests pin, in one place (#1796).

Kept apart from the product's `REPORT_SCHEMA` on purpose: a test that reads the
version from the code it checks could never notice the code changing it.  A
version collision at merge changes this pin and the product constant.
"""

from __future__ import annotations

CURRENT_REPORT_SCHEMA = "metadata-report-v80"
PREVIOUS_REPORT_SCHEMA = "metadata-report-v79"
