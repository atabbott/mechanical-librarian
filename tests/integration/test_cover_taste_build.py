"""A taste profile written from recorded owner cover choices, end to end (#1372).

Decisions go through the real decision command; the builder answers through a
fake OpenRouter client behind the real reviewer access, so every seam from the
eligible-decision SQL to the read route is the shipped one.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.operational_evidence import SupportBundle
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_contract import (
    AdmittedTasteBuildRequest,
    TasteBuildReply,
)
from mechanical_librarian.application.operations import support_preparation_incidents
from mechanical_librarian.application.reviewer_access import (
    OpenRouterFactory,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from tests.support.cover_taste import record_owner_cover_decisions
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from tests.unit.test_reviewer_access_application import _Auth

ORIGIN = "https://review.test"
MODEL = "example/vision"
NOW = 20 * 86_400 + 600
SENTINEL = "Drawn to SENTINELQUILL illustrated art."
CODEX = ("gpt-5.6-sol", b"synthetic-contained-profile")


def _never(*_args: object) -> str:
    raise AssertionError("the taste profile never runs on Codex")


@dataclass
class _TasteOpenRouter:
    """OpenRouter for one model: it records each request and answers a profile."""

    key: str
    model: str
    requests: list[AdmittedTasteBuildRequest] = field(default_factory=list)
    answer: object = None

    def probe(self) -> OpenRouterAccountStatus:
        return OpenRouterAccountStatus(
            tier="paid",
            model=self.model,
            input_modalities=("text", "image"),
            output_modalities=("text",),
            structured_outputs=True,
            observed_at=1,
            prompt_price="0.000003",
            completion_price="0.000015",
            image_price="0.0048",
        )

    def run_taste_build(
        self, _profile: bytes, request: AdmittedTasteBuildRequest
    ) -> TasteBuildReply:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return TasteBuildReply(
            {
                "sections": {
                    "drawn_to": [SENTINEL],
                    "avoid": [],
                    "trade_offs": ["Prefers a sharper copy of the same art."],
                    "keep_or_switch": [],
                }
            },
            True,
        )

    def close(self) -> None:
        return None


@dataclass
class _Built:
    root: Path
    repository: SQLiteMetadataReportRepository
    access: ReviewerAccessOperations
    service: CoverTasteBuildService
    openrouter: list[_TasteOpenRouter]


@contextmanager
def _built(root: Path, decisions: int = 25) -> Iterator[_Built]:
    repository = open_repository(root / "reports.sqlite", cross_thread=True)
    clients: list[_TasteOpenRouter] = []

    def factory(key: str, model: str) -> _TasteOpenRouter:
        client = _TasteOpenRouter(key, model)
        if clients and clients[0].answer is not None:
            client.answer = clients[0].answer
        clients.append(client)
        return client

    access = ReviewerAccessOperations(
        repository.reviewer_access_store(),
        codex_auth=_Auth("usable"),
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(True, None),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=lambda: CODEX,
        codex_binding=lambda: (*CODEX, _never),
        codex_description_binding=lambda: (*CODEX, _never),
        # A taste build uses only `run_taste_build` and `close`.
        openrouter_factory=cast("OpenRouterFactory", factory),
    )
    try:
        record_owner_cover_decisions(root, repository, decisions)
        revision = access.read().revision
        access.test_taste_model("synthetic-key", MODEL)
        access.configure_openrouter(
            expected_revision=revision,
            api_key="synthetic-key",
            model=MODEL,
            task="cover_taste_build",
        )
        clients.clear()
        service = CoverTasteBuildService(
            repository.cover_taste_store,
            access,
            CoverArtifactStore(root / "artifacts", create=False).read_preview_digest,
            daily_limit=8,
            clock=lambda: NOW,
        )
        yield _Built(root, repository, access, service, clients)
    finally:
        repository.close()


def test_a_profile_is_written_from_owner_choices_and_shown_read_only(tmp_path) -> None:
    with _built(tmp_path) as built:
        before = built.repository._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone()
        built.service.wake(NOW)
        [client] = built.openrouter
        [request] = client.requests
        packet = json.loads(request.packet_document)
        assert len(packet["decisions"]) == 25
        assert {
            image["role"] for d in packet["decisions"] for image in d["images"]
        } == {
            "chosen",
            "passed_over",
        }
        # The original's decoded size, never the preview's.
        assert {
            image["width"] for d in packet["decisions"] for image in d["images"]
        } == {
            301,
            1200,
        }
        assert len(request.images) == 50
        state = built.service.read()
        assert state["configured"] is True
        assert state["eligible"] == 25
        assert state["build"] is None
        [(version, created, document)] = cast(
            "list[tuple[int, int, dict[str, Any]]]", state["versions"]
        )
        assert (version, created) == (1, NOW)
        assert document["rules"][0]["text"] == SENTINEL
        assert document["input"]["included"] == 25
        assert document["model"] == MODEL
        # It influences nothing: no selection, stage or decision moved.
        assert (
            built.repository._connection.execute(
                "SELECT count(*) FROM cover_selection_authorities"
            ).fetchone()
            == before
        )
        with _client(built) as (client_, headers):
            response = client_.get("/api/v1/covers/taste", headers=headers)
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
            body = response.json()
            assert body["schema_version"] == "review-taste-profile-v1"
            assert body["chosen"] == 25
            assert body["profile"]["version"] == 1
            assert body["profile"]["rules"][0] == {
                "number": 1,
                "section": "drawn_to",
                "text": SENTINEL,
            }
            assert body["versions"] == [
                {"version": 1, "built_at": NOW, "decisions": 25}
            ]


def test_a_failed_build_is_retried_once_by_the_owner(tmp_path) -> None:
    with _built(tmp_path) as built:
        built.openrouter.append(
            _TasteOpenRouter("k", MODEL, answer=RuntimeError("private"))
        )
        built.service.wake(NOW)
        state = built.service.read()
        build = state["build"]
        assert isinstance(build, dict) and build["failure"] == "unavailable"
        with _client(built) as (client, headers):
            refused = client.post(
                "/api/v1/covers/taste/retry",
                json={"build_id": build["build_id"]},
                headers={
                    key: value
                    for key, value in headers.items()
                    if key != "X-CSRF-Token"
                },
            )
            assert refused.status_code in {403, 422}
            stale = client.post(
                "/api/v1/covers/taste/retry",
                json={"build_id": "taste_build_" + "0" * 32},
                headers=headers,
            )
            assert stale.status_code == 409
            first = client.post(
                "/api/v1/covers/taste/retry",
                json={"build_id": build["build_id"]},
                headers=headers,
            )
            again = client.post(
                "/api/v1/covers/taste/retry",
                json={"build_id": build["build_id"]},
                headers=headers,
            )
            assert first.status_code == again.status_code == 200
            assert first.json()["build"]["state"] == "building"
            assert (
                first.json()["build"]["build_id"] == again.json()["build"]["build_id"]
            )
        built.openrouter.clear()
        built.service.wake(NOW + 1)
        assert [row[0] for row in built.service.read()["versions"]] == [1]


def test_profile_content_never_reaches_operational_output(
    tmp_path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with _built(tmp_path) as built:
        built.service.wake(NOW)
        [request] = built.openrouter[0].requests
        references = [reference for reference, _jpeg in request.images]
        # A failed build's history carries a closed code only.
        built.openrouter.clear()
        built.openrouter.append(
            _TasteOpenRouter("k", MODEL, answer=RuntimeError(f"echo {SENTINEL}"))
        )
        record_owner_cover_decisions(tmp_path, built.repository, 10, start=25)
        built.service.wake(NOW + 120)
        bundle = (
            SupportBundle(
                (
                    (
                        "operational_incidents",
                        lambda: support_preparation_incidents(
                            built.repository.operational_snapshot()
                        ),
                    ),
                )
            )
            .export()
            .decode()
        )
        failures = json.dumps(
            built.repository._connection.execute(
                "SELECT * FROM cover_taste_builds"
            ).fetchall()
        )
        status = json.dumps(built.repository.operational_snapshot(), default=str)
    captured = capsys.readouterr()
    outputs = {
        "stdout": captured.out,
        "stderr": captured.err,
        "log": caplog.text,
        "support bundle": bundle,
        "failure history": failures,
        "status": status,
    }
    for name, text in outputs.items():
        assert "SENTINELQUILL" not in text, name
        assert "taste_image_" not in text, name
        for reference in references:
            assert reference not in text, name
        assert "data:image" not in text, name
    assert '"unavailable"' in failures


@contextmanager
def _client(built: _Built) -> Iterator[tuple[TestClient, dict[str, str]]]:
    password = built.root / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        built.repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=ORIGIN,
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=lambda: NOW,
        reviewer_access=built.access,
        cover_taste=built.service,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200, login.text
        yield (
            client,
            {
                "Origin": ORIGIN,
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
        )
