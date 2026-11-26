"""A taste profile written from recorded owner cover choices, end to end (#1372).

Decisions go through the real decision command; the builder answers through a
fake OpenRouter client, or a fake contained Codex runner, behind the real
reviewer access, so every seam from the eligible-decision SQL to the read
route is the shipped one.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.operational_evidence import SupportBundle
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.cover_taste import _ELIGIBLE, _USABLE
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewerError,
    canonical_json,
)
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_contract import (
    AdmittedTasteBuildRequest,
    TasteBuildReply,
)
from mechanical_librarian.application.cover_taste_inputs import assemble_taste_inputs
from mechanical_librarian.application.operations import support_preparation_incidents
from mechanical_librarian.application.reviewer_access import (
    OpenRouterFactory,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.cover_taste import TASTE_MIN_ARTS, TasteProfile
from mechanical_librarian.interfaces.web.app import REVIEW_TASTE_PROFILE_SCHEMA
from tests.support.cover_selections import ALL_COVER_SELECTIONS
from tests.support.cover_taste import record_owner_cover_decisions
from tests.support.ledger_copies import assert_ledger_copies
from tests.support.ledger_partition import (
    copied_store,
    regenerate,
    reopen_without_derived_rows,
)
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
    raise AssertionError("cover review and description advice never run here")


_CODEX_PROFILE = canonical_json(
    {
        "sections": {
            "avoid": [],
            "drawn_to": [SENTINEL],
            "keep_or_switch": [],
            "trade_offs": [],
        }
    }
)


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
def _built(
    root: Path,
    decisions: int = 25,
    *,
    codex: Callable[[bytes, Any], str] | None = None,
    daily_limit: int = 8,
) -> Iterator[_Built]:
    """Select OpenRouter for the taste build, or Codex answering through `codex`."""
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
        codex_taste_binding=lambda: (*CODEX, codex or _never),
        # A taste build uses only `run_taste_build` and `close`.
        openrouter_factory=cast("OpenRouterFactory", factory),
    )
    try:
        record_owner_cover_decisions(root, repository, decisions)
        revision = access.read().revision
        if codex is not None:
            access.select_codex(expected_revision=revision, task="cover_taste_build")
        else:
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
            daily_limit=daily_limit,
            clock=lambda: NOW,
        )
        yield _Built(root, repository, access, service, clients)
    finally:
        repository.close()


def test_a_profile_is_written_from_owner_choices_and_shown(tmp_path) -> None:
    with _built(tmp_path) as built:
        before = built.repository._connection.execute(
            "SELECT count(*) FROM " + ALL_COVER_SELECTIONS
        ).fetchone()
        # Each owner choice the build sends carries the facts it is counted
        # and sent by (ADR-0108).
        checked = assert_ledger_copies(built.repository)
        assert checked["taste_decision"] > 0
        assert checked["taste_eligible"] > 0
        # Cover choices not yet written need the owner's go-ahead (ADR-0108
        # step 4); the choices themselves stand.
        assert reopen_without_derived_rows(
            built.repository, tmp_path / "regenerated", retire_pending=True
        )["owner_cover_selections.transport_sha256"]
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
                "SELECT count(*) FROM " + ALL_COVER_SELECTIONS
            ).fetchone()
            == before
        )
        with _client(built) as (client_, headers):
            response = client_.get("/api/v1/covers/taste", headers=headers)
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
            body = response.json()
            assert body["schema_version"] == REVIEW_TASTE_PROFILE_SCHEMA
            assert body["chosen"] == 25
            assert body["profile"]["version"] == 1
            assert body["profile"]["kind"] == "built"
            assert body["profile"]["rules"][0] == {
                "number": 1,
                "section": "drawn_to",
                "text": SENTINEL,
                "provenance": "model",
            }
            assert body["versions"] == [
                {"version": 1, "kind": "built", "created_at": NOW, "decisions": 25}
            ]


def test_a_profile_is_written_on_codex_with_the_contained_model(tmp_path) -> None:
    """Codex receives the same admitted request and its model is stored (#1372 C4)."""
    runs: list[tuple[bytes, AdmittedTasteBuildRequest]] = []

    def codex(profile: bytes, request: AdmittedTasteBuildRequest) -> str:
        runs.append((profile, request))
        return _CODEX_PROFILE

    with _built(tmp_path, codex=codex) as built:
        assert built.service.read()["configured"] is True
        built.service.wake(NOW)
        [(profile, request)] = runs
        assert profile == CODEX[1]
        assert type(request) is AdmittedTasteBuildRequest
        assert len(json.loads(request.packet_document)["decisions"]) == 25
        assert len(request.images) == 50
        assert built.openrouter == []
        state = built.service.read()
        assert state["build"] is None
        [(version, _created, document)] = cast(
            "list[tuple[int, int, dict[str, Any]]]", state["versions"]
        )
        assert version == 1
        assert document["model"] == CODEX[0]
        assert document["rules"][0]["text"] == SENTINEL
        assert built.repository._connection.execute(
            "SELECT model FROM cover_taste_builds"
        ).fetchall() == [(CODEX[0],)]


def test_codex_failures_end_closed_without_stopping_the_day_and_count(
    tmp_path,
) -> None:
    """A Codex failure is `unavailable`, a bad reply `invalid_output`; both count."""
    answers: list[object] = [
        ContainedReviewerError("timeout"),
        ContainedReviewerError("malformed_output"),
        canonical_json({"sections": {"drawn_to": ["x" * 401]}}),
    ]

    def codex(_profile: bytes, _request: AdmittedTasteBuildRequest) -> str:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return cast("str", answer)

    def requests_today(built: _Built) -> list[tuple[int, str | None]]:
        return cast(
            "list[tuple[int, str | None]]",
            built.repository._connection.execute(
                "SELECT requests,stopped FROM reviewer_task_requests"
            ).fetchall(),
        )

    with _built(tmp_path, codex=codex, daily_limit=3) as built:
        failures = []
        for offset in range(3):
            built.service.wake(NOW + offset)
            build = cast("dict[str, Any]", built.service.read()["build"])
            failures.append(build["failure"])
            assert requests_today(built) == [(offset + 1, None)]
            built.service.retry(build["build_id"])
        assert failures == ["unavailable", "invalid_output", "invalid_output"]
        assert answers == []
        # The third build used the day's last request: the next is refused.
        built.service.wake(NOW + 3)
        build = cast("dict[str, Any]", built.service.read()["build"])
        assert build["failure"] == "ceiling"
        assert requests_today(built) == [(3, None)]
        assert built.service.read()["versions"] == []


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


OWNER_TEXT = "Drawn to OWNERQUILL painted art."
REPEATED = "Prefers a sharper copy of the same art."


def _owner_edits(built: _Built) -> None:
    """Edit rule 1, remove rule 2 and add a rule, each over HTTP."""
    edits: tuple[dict[str, object], ...] = (
        {"action": "edit", "number": 1, "text": OWNER_TEXT},
        {"action": "remove", "number": 2},
        {"action": "add", "section": "keep_or_switch", "text": "Keeps sharp art."},
    )
    with _client(built) as (client, headers):
        for version, body in enumerate(edits, start=1):
            response = client.post(
                "/api/v1/covers/taste/rules",
                json={"version": version, **body},
                headers=headers,
            )
            assert response.status_code == 200, response.text
        stale = client.post(
            "/api/v1/covers/taste/rules",
            json={"version": 1, "action": "remove", "number": 1},
            headers=headers,
        )
        assert (stale.status_code, stale.json()["error"]["code"]) == (409, "stale")
        assert OWNER_TEXT not in stale.text


def test_owner_edits_survive_a_rebuild_and_removed_rules_stay_out(tmp_path) -> None:
    """#1372 PR 2 E13: the builder is sent the kept rules and removed texts,
    and the rebuild keeps the owner's rules word for word and leaves out the
    model's repeats of a removed or replaced text."""
    with _built(tmp_path) as built:
        built.service.wake(NOW)
        _owner_edits(built)
        record_owner_cover_decisions(tmp_path, built.repository, 10, start=25)
        built.openrouter.clear()
        built.service.wake(NOW + 120)
        [client] = built.openrouter
        [request] = client.requests
        packet = json.loads(request.packet_document)
        assert packet["kept"] == [
            {"section": "drawn_to", "text": OWNER_TEXT},
            {"section": "keep_or_switch", "text": "Keeps sharp art."},
        ]
        assert packet["left_out"] == [REPEATED, SENTINEL]
        assert packet["budget"] == {"characters": 8_144, "rules": 38, "words": 1_192}
        with _client(built) as (http, headers):
            body = http.get("/api/v1/covers/taste", headers=headers).json()
        assert body["profile"]["kind"] == "built"
        assert body["profile"]["rules"] == [
            {
                "number": 1,
                "section": "drawn_to",
                "text": OWNER_TEXT,
                "provenance": "edited",
            },
            {
                "number": 2,
                "section": "keep_or_switch",
                "text": "Keeps sharp art.",
                "provenance": "owner",
            },
        ]
        assert [(row["version"], row["kind"]) for row in body["versions"]] == [
            (5, "built"),
            (4, "edited"),
            (3, "edited"),
            (2, "edited"),
            (1, "built"),
        ]
        versions = cast(
            "list[tuple[int, int, dict[str, Any]]]", built.service.read()["versions"]
        )
        # The newest version is first.
        document = versions[0][2]
        assert document["left_out"] == 2


def test_owner_rule_text_never_reaches_operational_output(
    tmp_path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with _built(tmp_path) as built:
        built.service.wake(NOW)
        _owner_edits(built)
        built.openrouter.clear()
        built.openrouter.append(
            _TasteOpenRouter("k", MODEL, answer=RuntimeError(f"echo {OWNER_TEXT}"))
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
    for name, text in {
        "stdout": captured.out,
        "stderr": captured.err,
        "log": caplog.text,
        "support bundle": bundle,
        "failure history": failures,
        "status": status,
    }.items():
        assert "OWNERQUILL" not in text, name
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


def _taste_reading(path: Path, watermark: int) -> tuple[object, ...]:
    """Which choices were made, then those a build can use: the count, the
    count shown, those since `watermark`, and whether a cadence build is then
    due."""
    repository = open_repository(path, create=False)
    try:
        store = repository.cover_taste_store()
        made = [
            row[0]
            for row in repository._connection.execute(
                f"SELECT selection.selection_id {_ELIGIBLE} ORDER BY selection.rowid",
                (TASTE_MIN_ARTS,),
            )
        ]
        new = repository._connection.execute(
            f"SELECT count(*) {_USABLE} AND selection.rowid>?",
            (TASTE_MIN_ARTS, watermark),
        ).fetchone()[0]
        return (
            made,
            store.eligible_count(),
            store.read()["eligible"],
            new,
            store.schedule(NOW + 86_400, True, 8),
        )
    finally:
        repository.close()


def _decide_then_regenerate(
    tmp_path: Path, count: int, at_25: Callable[[Any], None]
) -> tuple[Path, Path]:
    """`count` owner keeps, calling `at_25` with the store once 25 are made,
    and a copy of the store after the real `regenerate-derived`. Keeps: a
    regeneration keeps the owner's pointer to a keep, but repoints or clears
    one to a choice never written."""
    path = tmp_path / "reports.sqlite"
    repository = open_repository(path, cross_thread=True)
    try:
        store = repository.cover_taste_store()

        def first_build(_case_id: str) -> None:
            if store.eligible_count() == 25 and not store.read()["versions"]:
                assert store.schedule(NOW, True, 8)
                at_25(store)

        record_owner_cover_decisions(
            tmp_path, repository, count, keep=True, before_decision=first_build
        )
        regenerated = copied_store(repository, tmp_path / "regenerated")
    finally:
        repository.close()
    regenerate(regenerated)
    return path, regenerated


def test_a_build_waits_for_choices_it_can_use_after_a_regeneration(
    tmp_path,
) -> None:
    """ADR-0108: after a regeneration a new profile is built from choices
    made since. The copies still name every earlier choice, but the count
    shown, the thresholds and Try again read only choices a build can use:
    no build is due and no failure is shown, as before 25 picks, until 25 new
    choices make one due with inputs to send."""
    failed: list[str] = []

    def fail(store: Any) -> None:
        # The first build fails, so the next one is due 10 choices later.
        build_id = store.pending()
        store.fail(build_id, NOW, "unavailable")
        failed.append(build_id)

    path, regenerated = _decide_then_regenerate(tmp_path, 35, fail)
    (build_id,) = failed
    repository = open_repository(path, create=False)
    try:
        (watermark,) = repository._connection.execute(
            "SELECT selection_watermark FROM cover_taste_builds"
        ).fetchone()
    finally:
        repository.close()
    before = _taste_reading(path, watermark)
    assert before[1:] == (35, 35, 10, True)
    assert _taste_reading(regenerated, watermark) == (before[0], 0, 0, 0, False)

    repository = open_repository(regenerated, create=False, cross_thread=True)
    try:
        store = repository.cover_taste_store()
        shown = store.read()
        # Today's state before 25 picks: no profile, no build, no failure.
        assert (shown["eligible"], shown["build"], shown["versions"]) == (0, None, [])
        assert store.request_retry(build_id, NOW + 86_400, 8) is None
        assert store.decisions() == ()
        record_owner_cover_decisions(tmp_path, repository, 24, start=35, keep=True)
        assert (store.eligible_count(), store.schedule(NOW + 86_400, True, 8)) == (
            24,
            False,
        )
        record_owner_cover_decisions(tmp_path, repository, 1, start=59, keep=True)
        assert store.schedule(NOW + 86_400, True, 8)
        inputs = assemble_taste_inputs(
            store.decisions(),
            CoverArtifactStore(
                tmp_path / "artifacts", create=False
            ).read_preview_digest,
            kept=(),
            left_out=(),
        )
        assert inputs is not None
        assert (inputs.record["eligible"], inputs.record["included"]) == (25, 25)
    finally:
        repository.close()


def test_a_profile_is_kept_after_a_regeneration(tmp_path) -> None:
    """A store with a profile keeps it after a regeneration, shows no
    failure, and rebuilds once it has 25 new choices a build can use."""

    def succeed(store: Any) -> None:
        build_id = store.pending()
        assert store.claim(build_id, NOW, MODEL, 8) is None
        assert (
            store.succeed(
                build_id,
                NOW,
                TasteProfile.from_output(
                    {
                        "sections": {
                            "drawn_to": ["Calm colour."],
                            "avoid": [],
                            "trade_offs": [],
                            "keep_or_switch": [],
                        }
                    }
                ),
                {
                    "corrections": 0,
                    "dropped": 0,
                    "eligible": 25,
                    "images": 50,
                    "included": 25,
                    "input_sha256": "0" * 64,
                    "truncated": False,
                },
                failure="invalid_output",
            )
            == 1
        )

    _path, regenerated = _decide_then_regenerate(tmp_path, 26, succeed)
    repository = open_repository(regenerated, create=False, cross_thread=True)
    try:
        store = repository.cover_taste_store()
        shown = store.read()
        assert [version for version, *_ in shown["versions"]] == [1]
        assert (shown["eligible"], shown["build"]) == (0, None)
        assert not store.schedule(NOW + 86_400, True, 8)
        # As before a first profile, a build is due on 25 it can use.
        record_owner_cover_decisions(tmp_path, repository, 24, start=26, keep=True)
        assert not store.schedule(NOW + 86_400, True, 8)
        record_owner_cover_decisions(tmp_path, repository, 1, start=50, keep=True)
        assert store.schedule(NOW + 86_400, True, 8)
        assert [version for version, *_ in store.read()["versions"]] == [1]
    finally:
        repository.close()
