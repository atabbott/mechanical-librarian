"""The taste judge end to end, on either engine (#1372 PR 4, A11, A12).

Fields are prepared by the real producers, the engine is selected through
the real reviewer access operations, and the profile's second version is an
owner edit over HTTP. Since PR 4b a `choose` verdict is a suggestion that
To decide lists, and the owner's answer counts toward Confirmed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.operational_evidence import SupportBundle
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewerError,
    canonical_json,
)
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_judge import CoverTasteJudgeService
from mechanical_librarian.application.cover_taste_judge_contract import (
    AdmittedTasteJudgeRequest,
    TasteJudgeReply,
)
from mechanical_librarian.application.operations import support_preparation_incidents
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.application.reviewer_access import (
    OpenRouterFactory,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_PROFILE_SCHEMA,
    TASTE_JUDGE_SETTLE_SECONDS,
)
from tests.integration.test_cover_taste_build import _Built, _client
from tests.unit.test_cover_taste_judge import WORKS, _Library, _library
from tests.unit.test_cover_taste_suggestions import _choose
from tests.unit.test_reviewer_access_application import _Auth

MODEL = "example/vision"
NOW = 30 * 86_400
CODEX = ("gpt-5.6-sol", b"synthetic-contained-profile")
SENTINEL = "Drawn to JUDGEQUILL painted art."


def _never(*_args: object) -> str:
    raise AssertionError("cover review and description advice never run here")


def _answer(request: AdmittedTasteJudgeRequest) -> str:
    """keep_current when a current cover was sent, else choose the first."""
    if request.current is not None:
        return canonical_json(
            {
                "candidate": None,
                "confidence": 92,
                "decision": "keep_current",
                "rules": [1],
            }
        )
    return canonical_json(
        {
            "candidate": request.images[0][0],
            "confidence": 64,
            "decision": "choose",
            "rules": [1],
        }
    )


@dataclass
class _JudgeOpenRouter:
    key: str
    model: str
    requests: list[AdmittedTasteJudgeRequest] = field(default_factory=list)
    answer: Callable[[AdmittedTasteJudgeRequest], object] = _answer

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

    def run_taste_judge(
        self, _profile: bytes, request: AdmittedTasteJudgeRequest
    ) -> TasteJudgeReply:
        self.requests.append(request)
        answer = self.answer(request)
        if isinstance(answer, BaseException):
            raise answer
        return TasteJudgeReply(cast(str, answer), True)

    def close(self) -> None:
        return None


@dataclass
class _Judged:
    library: _Library
    access: ReviewerAccessOperations
    service: CoverTasteJudgeService
    clients: list[_JudgeOpenRouter]
    clock: list[int]

    def run(self, now: int) -> None:
        self.clock[0] = now
        while self.service.wake(now):
            pass

    def requests(self) -> list[AdmittedTasteJudgeRequest]:
        return [request for client in self.clients for request in client.requests]

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        return self.library.repository._connection.execute(sql).fetchall()


def _first_version(library: _Library, rule: str) -> None:
    """A profile version as an owner edit keeps a build's facts (#1372 PR 2)."""
    library.repository._connection.execute(
        "INSERT INTO cover_taste_profile_versions VALUES(1,NULL,0,?)",
        (
            canonical_json(
                {
                    "build_id": None,
                    "built_at": 0,
                    "input": {
                        "corrections": 0,
                        "dropped": 0,
                        "eligible": 25,
                        "images": 50,
                        "included": 25,
                        "input_sha256": "0" * 64,
                        "truncated": False,
                    },
                    "left_out": 0,
                    "model": MODEL,
                    "removed": [],
                    "rules": [
                        {
                            "number": 1,
                            "provenance": "owner",
                            "section": "drawn_to",
                            "text": rule,
                        }
                    ],
                    "schema_version": COVER_TASTE_PROFILE_SCHEMA,
                    "source": "edit",
                    "version": 1,
                }
            ),
        ),
    )


@contextmanager
def _judged(
    root: Path,
    *,
    codex: Callable[[bytes, object], str] | None = None,
    rule: str = "Calm colour.",
) -> Iterator[_Judged]:
    library = _library(
        root, {name: WORKS[name] for name in ("keep", "wrong_shape", "unreadable")}
    )
    clients: list[_JudgeOpenRouter] = []

    def factory(key: str, model: str) -> _JudgeOpenRouter:
        client = _JudgeOpenRouter(key, model)
        if clients:
            client.answer = clients[0].answer
        clients.append(client)
        return client

    access = ReviewerAccessOperations(
        library.repository.reviewer_access_store(),
        codex_auth=_Auth("usable"),
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(True, None),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=lambda: CODEX,
        codex_binding=lambda: (*CODEX, _never),
        codex_description_binding=lambda: (*CODEX, _never),
        codex_taste_binding=lambda: (*CODEX, codex or _never),
        openrouter_factory=cast("OpenRouterFactory", factory),
    )
    try:
        revision = access.read().revision
        if codex is not None:
            access.select_codex(expected_revision=revision, task="cover_taste_judge")
        else:
            access.test_taste_model("synthetic-key", MODEL, "cover_taste_judge")
            access.configure_openrouter(
                expected_revision=revision,
                api_key="synthetic-key",
                model=MODEL,
                task="cover_taste_judge",
            )
        clients.clear()
        _first_version(library, rule)
        clock = [NOW]
        store = library.repository.cover_taste_judge_store()
        service = CoverTasteJudgeService(
            lambda: store,
            access,
            library.artifacts.read_preview_digest,
            daily_limit=50,
            clock=lambda: clock[0],
        )
        yield _Judged(library, access, service, clients, clock)
    finally:
        library.repository.close()


def _verdicts(judged: _Judged) -> list[dict[str, object]]:
    return [
        json.loads(cast(str, document))
        for (document,) in judged.rows(
            "SELECT document FROM cover_taste_verdicts ORDER BY rowid"
        )
    ]


def test_fields_are_judged_once_and_a_new_version_rejudges_only_suggestions(
    tmp_path: Path,
) -> None:
    with _judged(tmp_path) as judged:
        unchanged = (
            "SELECT (SELECT count(*) FROM cover_selection_authorities),"
            "(SELECT count(*) FROM human_decisions),"
            "(SELECT group_concat(currentness_document) FROM cover_considerations)"
        )
        before = judged.rows(unchanged)
        judged.run(NOW)
        assert len(judged.requests()) == 3
        verdicts = _verdicts(judged)
        assert sorted(str(verdict["decision"]) for verdict in verdicts) == [
            "choose",
            "keep_current",
            "keep_current",
        ]
        for verdict in verdicts:
            assert (verdict["engine"], verdict["model"], verdict["version"]) == (
                "openrouter",
                MODEL,
                1,
            )
            assert verdict["current_marker"] is True
            assert not str(verdict["candidate_reference"] or "").startswith(
                "taste_choice_"
            )
        assert judged.rows(
            "SELECT requests,stopped FROM reviewer_task_requests "
            "WHERE task='cover_taste_judge'"
        ) == [(3, None)]
        # It influences nothing yet.
        assert judged.rows(unchanged) == before

        # The owner edits the profile: version 2 settles, then only the
        # field whose verdict chose another cover is judged again.
        store = judged.library.repository.cover_taste_store()
        built = CoverTasteBuildService(
            lambda: store,
            judged.access,
            judged.library.artifacts.read_preview_digest,
            daily_limit=1,
            clock=lambda: NOW + 300,
        )
        with _client(
            _Built(tmp_path, judged.library.repository, judged.access, built, [])
        ) as (http, headers):
            response = http.post(
                "/api/v1/covers/taste/rules",
                json={"version": 1, "action": "edit", "number": 1, "text": "Bold art."},
                headers=headers,
            )
            assert response.status_code == 200, response.text
        edited_at = judged.rows(
            "SELECT created_at FROM cover_taste_profile_versions WHERE version=2"
        )[0][0]
        judged.clients.clear()
        judged.run(cast(int, edited_at) + TASTE_JUDGE_SETTLE_SECONDS - 1)
        assert judged.requests() == []
        judged.run(cast(int, edited_at) + TASTE_JUDGE_SETTLE_SECONDS)
        [request] = judged.requests()
        assert request.current is None and request.version == 2
        [*_, latest] = _verdicts(judged)
        assert (latest["version"], latest["decision"]) == (2, "choose")


def test_a_suggestion_is_listed_shown_confirmed_and_learned_from(
    tmp_path: Path,
) -> None:
    """#1372 PR 4b end to end: a `choose` verdict is listed under To decide
    and shown on its work; the owner's pick of that card is the ordinary
    accept, counts as confirmed, and the next build reads it as chosen."""
    with _judged(tmp_path) as judged:
        judged.run(NOW)
        [(item, reference)] = [
            (
                next(
                    name
                    for name, consideration in judged.library.considerations.items()
                    if consideration["consideration_id"] == verdict["consideration_id"]
                ),
                cast(str, verdict["candidate_reference"]),
            )
            for verdict in _verdicts(judged)
            if verdict["decision"] == "choose"
        ]
        case_id = judged.library.cases[item]
        store = judged.library.repository.cover_taste_store()
        built = CoverTasteBuildService(
            lambda: store,
            judged.access,
            judged.library.artifacts.read_preview_digest,
            daily_limit=1,
            clock=lambda: NOW + 60,
        )
        with _client(
            _Built(tmp_path, judged.library.repository, judged.access, built, [])
        ) as (http, _headers):
            decide = http.get("/api/v1/covers", params={"view": "decide"}).json()
            [row] = [row for row in decide["items"] if row["review_case_id"] == case_id]
            assert row["group"] == "proposal"
            # Confidence 64 is below the Strong threshold of 90.
            assert row["suggestion"]["taste"] == {"strength": "likely"}
            # The work's detail, as the composed application reads it: the
            # transport test app has no cover comparison reader.
            detail = InspectCurrentReviewCase(
                judged.library.repository, judged.library.service
            ).run(case_id)
            [cover] = [
                field
                for field in cast("list[dict[str, object]]", detail["fields"])
                if field["destination"] == "cover"
            ]
            suggestion = cast("dict[str, object]", cover["cover_taste_suggestion"])
            assert suggestion["candidate_reference"] == reference
            assert (suggestion["version"], suggestion["strength"]) == (1, "likely")
            assert suggestion["rules"] == [{"number": 1, "text": "Calm colour."}]
            taste = http.get("/api/v1/covers/taste").json()
            assert taste["judge"]["configured"] is True
            assert taste["judge"]["failed"] == 0
            assert taste["confirmed"] == {"confirmed": 0, "suggested": 0}

            _choose(judged.library, item, reference, NOW + 30)

            decide = http.get("/api/v1/covers", params={"view": "decide"}).json()
            assert case_id not in {row["review_case_id"] for row in decide["items"]}
            taste = http.get("/api/v1/covers/taste").json()
            assert taste["confirmed"] == {"confirmed": 1, "suggested": 1}
        decisions = store.decisions()
        assert [
            (decision.chosen_reference, decision.correction)
            for decision in decisions
            if decision.chosen_reference == reference
        ] == [(reference, False)]


def test_the_judge_runs_on_codex_with_the_contained_model(tmp_path: Path) -> None:
    runs: list[object] = []

    def codex(_profile: bytes, request: object) -> str:
        runs.append(request)
        return _answer(cast(AdmittedTasteJudgeRequest, request))

    with _judged(tmp_path, codex=codex) as judged:
        judged.run(NOW)
        assert len(runs) == 3
        assert all(type(run) is AdmittedTasteJudgeRequest for run in runs)
        assert {
            (verdict["engine"], verdict["model"]) for verdict in _verdicts(judged)
        } == {("codex", CODEX[0])}
        assert judged.clients == []


def test_a_codex_failure_and_a_refused_verdict_store_nothing(tmp_path: Path) -> None:
    def codex(_profile: bytes, _request: object) -> str:
        raise ContainedReviewerError("timeout")

    with _judged(tmp_path, codex=codex) as judged:
        judged.run(NOW)
        assert _verdicts(judged) == []
        assert judged.rows("SELECT state,failure FROM cover_taste_judgements") == [
            ("failed", "unavailable")
        ]
        assert judged.rows("SELECT requests,stopped FROM reviewer_task_requests") == [
            (1, None)
        ]
    (tmp_path / "refused").mkdir(mode=0o700)
    with _judged(tmp_path / "refused") as judged:
        # Never one of the references sent: refused, never repaired.
        judged.clients.append(_JudgeOpenRouter("k", MODEL))
        judged.clients[0].answer = lambda _request: canonical_json(
            {
                "candidate": "taste_choice_" + "0" * 64,
                "confidence": 99,
                "decision": "choose",
                "rules": [1],
            }
        )
        judged.run(NOW)
        assert _verdicts(judged) == []
        assert {
            row
            for row in judged.rows("SELECT state,failure FROM cover_taste_judgements")
        } == {("failed", "invalid_output")}


def test_judge_content_never_reaches_operational_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with _judged(tmp_path, rule=SENTINEL) as judged:
        answers = iter(
            (
                RuntimeError(f"echo {SENTINEL}"),
                "not json " + SENTINEL,
            )
        )
        judged.clients.append(_JudgeOpenRouter("k", MODEL))
        judged.clients[0].answer = lambda request: (
            next(answers, None) or _answer(request)
        )
        judged.run(NOW)
        judged.run(NOW + 600)
        requests = judged.requests()
        assert len(requests) == 3
        references = {
            reference
            for request in requests
            for pair in request.references
            for reference in pair
        }
        verdicts = [
            cast(str, document)
            for (document,) in judged.rows("SELECT document FROM cover_taste_verdicts")
        ]
        assert verdicts
        bundle = (
            SupportBundle(
                (
                    (
                        "operational_incidents",
                        lambda: support_preparation_incidents(
                            judged.library.repository.operational_snapshot()
                        ),
                    ),
                )
            )
            .export()
            .decode()
        )
        failures = json.dumps(judged.rows("SELECT * FROM cover_taste_judgements"))
        status = json.dumps(
            judged.library.repository.operational_snapshot(), default=str
        )
    captured = capsys.readouterr()
    for name, text in {
        "stdout": captured.out,
        "stderr": captured.err,
        "log": caplog.text,
        "support bundle": bundle,
        "failure history": failures,
        "status": status,
    }.items():
        assert "JUDGEQUILL" not in text, name
        assert "taste_choice_" not in text, name
        assert "data:image" not in text, name
        for reference in references:
            assert reference not in text, name
        for verdict in verdicts:
            assert verdict not in text, name
    assert '"unavailable"' in failures and '"invalid_output"' in failures
