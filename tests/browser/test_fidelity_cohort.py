from __future__ import annotations

import unittest

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.browser.fidelity_cohort import (
    LIBRARY_WORKS,
    RichFidelityApiMiddleware,
)


_INSTALLED_CASE_ID = "review_case_" + "e" * 64


class RichFidelityCohortTests(unittest.TestCase):
    def test_case_aliases_retain_real_authentication_and_currentness_gate(self) -> None:
        observed_paths: list[str] = []

        async def installed_case(request: Request) -> JSONResponse:
            observed_paths.append(
                request.url.path
                + (f"?{request.url.query}" if request.url.query else "")
            )
            if request.headers.get("x-synthetic-owner") != "authenticated":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            if request.path_params["case_id"] != _INSTALLED_CASE_ID:
                return JSONResponse({"error": "not_found"}, status_code=404)
            if request.query_params.get("cursor") is not None:
                return JSONResponse({"error": "invalid_cursor"}, status_code=422)
            return JSONResponse({"schema_version": "installed-case-v1"})

        inner = Starlette(
            routes=[
                Route(
                    "/api/v1/review-cases/{case_id}",
                    installed_case,
                    methods=["GET"],
                ),
                Route(
                    "/api/v1/review-cases/{case_id}/{subview}",
                    installed_case,
                    methods=["GET"],
                ),
            ]
        )
        app = RichFidelityApiMiddleware(
            inner, review_case_id=_INSTALLED_CASE_ID
        )

        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/review-cases/case-1")
            self.assertEqual(unauthorized.status_code, 401)
            unauthorized_page_2 = client.get(
                "/api/v1/review-cases/case-2/evidence"
                "?limit=50&cursor=evidence-page-2"
            )
            self.assertEqual(unauthorized_page_2.status_code, 401)

            linked_works = [
                work for work in LIBRARY_WORKS if work["review_case_id"] is not None
            ]
            for work in linked_works:
                case_id = str(work["review_case_id"])
                response = client.get(
                    f"/api/v1/review-cases/{case_id}",
                    headers={"x-synthetic-owner": "authenticated"},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["review_case_id"], case_id)
                self.assertEqual(payload["target"]["item"], work["title"])

            evidence = client.get(
                "/api/v1/review-cases/case-2/evidence",
                headers={"x-synthetic-owner": "authenticated"},
            )
            self.assertEqual(evidence.status_code, 200)
            self.assertEqual(evidence.json()["review_case_id"], "case-2")

            evidence_page_2 = client.get(
                "/api/v1/review-cases/case-2/evidence"
                "?limit=50&cursor=evidence-page-2",
                headers={"x-synthetic-owner": "authenticated"},
            )
            self.assertEqual(evidence_page_2.status_code, 200)
            page_2_payload = evidence_page_2.json()
            self.assertEqual(page_2_payload["review_case_id"], "case-2")
            self.assertEqual(len(page_2_payload["items"]), 3)
            self.assertTrue(
                all(
                    str(item["reference"]).startswith("evidence-2-")
                    for item in page_2_payload["items"]
                )
            )
            self.assertIsNone(page_2_payload["next_cursor"])

        self.assertTrue(observed_paths)
        self.assertEqual(
            set(observed_paths),
            {
                f"/api/v1/review-cases/{_INSTALLED_CASE_ID}",
                f"/api/v1/review-cases/{_INSTALLED_CASE_ID}/evidence",
                f"/api/v1/review-cases/{_INSTALLED_CASE_ID}/evidence?limit=50",
            },
        )


if __name__ == "__main__":
    unittest.main()
