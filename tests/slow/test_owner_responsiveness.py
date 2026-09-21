"""Complete-cover synthetic owner HTTP budget under real preparation actors."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.owner_responsiveness import (
    measure_owner_routes,
    running_owner_fixture,
)


def test_complete_cover_owner_routes_at_2300_works(tmp_path: Path) -> None:
    with running_owner_fixture(tmp_path, 2300, prepare=True) as (fixture, origin):
        assert len(fixture.reports) == 2300
        assert len(fixture.cases) == 2299
        assert len({report.target.library for report in fixture.reports.values()}) == 5
        receipt = measure_owner_routes(fixture, origin, 30)
        print(json.dumps(receipt, sort_keys=True))
        assert receipt["misses"] == []
