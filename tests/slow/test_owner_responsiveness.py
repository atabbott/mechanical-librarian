"""Complete-cover synthetic owner HTTP budget under real preparation actors."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from mechanical_librarian.adapters.persistence import catalogue as catalogue_adapter
from mechanical_librarian.application.batch import catalogue_fingerprint
from mechanical_librarian.application.catalogue import CatalogueEntry
from tests.support.owner_responsiveness import (
    measure_owner_routes,
    running_owner_fixture,
)


def test_complete_cover_owner_routes_at_2300_works(tmp_path: Path) -> None:
    with running_owner_fixture(tmp_path, 2300, prepare=True) as (fixture, origin):
        assert len(fixture.reports) == 2300
        assert len(fixture.cases) == 2299
        assert len({report.target.library for report in fixture.reports.values()}) == 5
        # Populate the actual optional store before its first lazy admission.
        # The existing 2300 reports/2299 cases remain; one pre-report failure
        # exercises the terminal join rather than merely attaching an empty DB.
        path = tmp_path / "catalogue.sqlite"
        fingerprint = catalogue_fingerprint(fixture.configuration)
        entries = tuple(
            CatalogueEntry(
                "primary",
                report.target.library,
                report.target.library,
                report.target.item,
                1,
                "Retained " + report.target.item,
                ("Retained author",),
                True,
                False,
                "singular-supported",
                report.file.identity,
                "/synthetic.epub",
                "epub",
                "books",
            )
            for report in fixture.reports.values()
        )
        terminal = replace(
            entries[0],
            item_id="terminal-before-report",
            title="Retained terminal catalogue work",
            authors=("Terminal writer",),
        )
        catalogue = catalogue_adapter.open_catalogue(path, create=True)
        try:
            catalogue.acquire_refresh()
            generation = catalogue.publish(fingerprint, (*entries, terminal))
            fixture.repository.bind_library_catalogue(path, fingerprint)
            fixture.repository.request_review_scan(
                "primary", terminal.library, terminal.library_id, "f" * 64, 10
            )
            action = fixture.repository.claim_review_pipeline_action(11)
            assert action is not None
            fixture.repository.complete_review_discovery(
                action, generation, fingerprint, (terminal,), 12
            )
            action = fixture.repository.claim_review_pipeline_action(13)
            assert action is not None and action.item_id == terminal.item_id
            fixture.repository.fail_review_pipeline_action(
                action, "unreadable_media", False, 14
            )
            with (
                patch.object(
                    catalogue_adapter,
                    "_validate_schema",
                    wraps=catalogue_adapter._validate_schema,
                ) as validation,
                httpx.Client(
                    base_url=origin, headers={"Origin": origin}, timeout=30
                ) as client,
            ):
                assert (
                    client.post(
                        "/api/v1/session/login", json={"password": "synthetic-owner"}
                    ).status_code
                    == 200
                )
                assert validation.call_count == 0
                started = time.perf_counter()
                first = client.get("/api/v1/library?lens=all&limit=25")
                admission_ms = (time.perf_counter() - started) * 1000
                assert first.status_code == 200
                assert first.json()["counts"]["all"] == 2301
                assert admission_ms <= 2000
                assert validation.call_count == 1
                named = client.get("/api/v1/library?lens=all&q=Terminal%20writer")
                assert named.status_code == 200
                assert named.json()["counts"]["all"] == 1
                assert named.json()["items"][0]["title"] == terminal.title
                assert named.json()["items"][0]["authors"] == ["Terminal writer"]
                started = time.perf_counter()
                labelled = client.get(
                    "/api/v1/library",
                    params={
                        "lens": "all",
                        "q": "Owner room " + terminal.library,
                        "limit": 25,
                    },
                )
                label_search_ms = (time.perf_counter() - started) * 1000
                assert labelled.status_code == 200
                assert labelled.json()["counts"]["all"] == 1 + sum(
                    report.target.library == terminal.library
                    for report in fixture.reports.values()
                )
                assert all(
                    item["library_display_name"] == "Owner room " + terminal.library
                    for item in labelled.json()["items"]
                )
                assert label_search_ms <= 2000
                receipt = measure_owner_routes(fixture, origin, 30)
                receipt["active_label_search_ms"] = label_search_ms
                assert validation.call_count == 1
                receipt["catalogue_admission_ms"] = admission_ms
        finally:
            catalogue.close()
        print(json.dumps(receipt, sort_keys=True))
        assert all(row["status"] == 200 for row in receipt["misses"])
        progress = receipt["preparation_progress"]
        assert progress[-1]["checked"] > progress[0]["checked"]
        assert any(row["active"] for row in progress)
