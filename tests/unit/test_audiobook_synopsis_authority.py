"""Closed synopsis comparison through real provider, persistence and owner seams."""

import json
from contextlib import closing
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    _recognized_synopsis_placeholder,
    project_audiobook_automatic_decision_value,
)
from tests.support.audiobook_reports import publish_audiobook_preview
from tests.support.audiobook_synopsis import synopsis_report
from tests.support.field_effects import effects as effects


@pytest.mark.parametrize(
    "value",
    [
        "Chapter 1",
        " TRACK 002. ",
        "No description",
        "No description available.",
        "Description unavailable",
        "No synopsis",
        "No synopsis available.",
        "Synopsis unavailable.",
        "CHAPTER\n1.",
    ],
)
def test_closed_placeholder_vocabulary(value):
    assert _recognized_synopsis_placeholder(value)


@pytest.mark.parametrize(
    "value",
    [
        "Chapter 1: A voyage begins",
        "Track 1 takes us through a city",
        "No description can capture this journey",
        "Chapter I",
        "Chapter one",
        "Chapter ١",
        "n/a",
        "Short.",
        "One paragraph.\n\nAnother paragraph.",
        "Chapter 1..",
    ],
)
def test_near_misses_are_not_placeholders(value):
    assert not _recognized_synopsis_placeholder(value)


@pytest.mark.parametrize("option", ["safe_plain_text", "replace_safe_plain_text"])
def test_useful_synopsis_is_durable_no_change_before_effect_guards(effects, option):
    incumbent = "A librarian follows a lost expedition.\n\nThe archive holds a secret."
    report = synopsis_report(incumbent)
    case_id, _ = publish_audiobook_preview(
        effects.repository,
        report,
        ("description_fill",),
        selected_options={"description_fill": option},
    )
    effects.repository.persist_shadow_outcome(case_id, 4)
    documents = effects.repository.get_review_case_decisions(case_id)
    decision = next(
        AutomaticDecision.from_json(doc)
        for doc in documents
        if json.loads(doc)["destination"] == "description"
    )
    assert decision.outcome == "keep_current"
    assert decision.policy_option == f"{option}:audiobook_description_preserve"
    authority = PersistedFieldEffectAuthority(effects.repository)
    lane = ContinuousGuardedApplication(
        effects.repository,
        {("primary", "audio"): effects.service},
        {("primary", "audio"): effects.capabilities},
        authority,
        (("primary", "audio"),),
        effects.clock,
        lambda *_: None,
    )
    with patch.object(
        authority, "resolve", side_effect=AssertionError("no-change reached guards")
    ):
        assert lane.schedule() == lane.schedule() == 0
    rows = effects.repository._connection.execute(
        "SELECT decision_id,destination,event_id FROM field_effect_no_changes"
    ).fetchall()
    assert len(rows) == 1 and rows[0][:2] == (decision.decision_id, "description")
    assert effects.repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)
    effects.repository.close()
    effects.repository = open_repository(effects.database_path)
    assert effects.repository.get_review_case_decisions(case_id) == documents
    inspector = InspectCurrentReviewCase(effects.repository)
    field = next(
        f for f in inspector.run(case_id)["fields"] if f["destination"] == "description"
    )
    assert not field["needs_attention"]
    assert field["current"]["value"] == incumbent
    assert any(
        item.get("decision_id") == decision.decision_id
        for item in inspector.history(case_id)["items"]
    )
    assert (
        effects.repository._connection.execute(
            "SELECT decision_id,destination,event_id FROM field_effect_no_changes"
        ).fetchall()
        == rows
    )


@pytest.mark.parametrize("current", [None, "Chapter 1", "No synopsis available."])
def test_actual_provider_elects_useful_original_text_and_reopens(tmp_path, current):
    report = synopsis_report(current, ("Track 2", "A voyage reveals a hidden city."))
    database = tmp_path / "synopsis.sqlite"
    with closing(open_repository(database)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("description_fill",),
            selected_options={"description_fill": "replace_safe_plain_text"},
        )
        repository.persist_shadow_outcome(case_id, 4)
    with closing(open_repository(database)) as repository:
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "description"
        )
        assert field["automatic_decision"]["selected_value"] == {
            "kind": "text",
            "value": "A voyage reveals a hidden city.",
        }
        assert not field["needs_attention"]


@pytest.mark.parametrize(
    "current,candidate,option",
    [
        (
            "Useful existing prose.",
            "Different useful prose.",
            "replace_safe_plain_text:audiobook_description_replace",
        ),
        (
            "Chapter 1",
            "Track 2",
            "replace_safe_plain_text:audiobook_description_replace",
        ),
        ("Chapter 1", "Useful prose.", "safe_plain_text:audiobook_description_replace"),
        (
            "<p>Unknown</p>",
            "Useful prose.",
            "replace_safe_plain_text:audiobook_description_replace",
        ),
        (
            "Cut off...",
            "Useful prose.",
            "replace_safe_plain_text:audiobook_description_replace",
        ),
    ],
)
def test_persisted_value_projection_refuses_replacement_substitutions(
    current, candidate, option
):
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            option,
            {"kind": "text", "value": candidate},
            current,
        )


@pytest.mark.parametrize(
    "mode,choice",
    [
        ("automatic", 0),
        ("automatic", 1),
        ("automatic", None),
        ("ask", 0),
        ("ask", 1),
        ("ask", None),
        ("ignore", None),
    ],
)
def test_owner_routing_alternatives_and_finality(tmp_path, mode, choice):
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        RecordHumanDecision,
        ReviewWebError,
    )

    path = tmp_path / "owner.sqlite"
    with closing(open_repository(path)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            synopsis_report("Chapter 1"),
            ("tags",),
            selected_options={
                "description_fill": "replace_safe_plain_text",
                "tags": "fill_empty",
            },
            selected_destinations={"description_fill": mode},
        )
        repository.persist_shadow_outcome(case_id, 4)
        inspector = InspectCurrentReviewCase(repository)
        field = next(
            f
            for f in inspector.run(case_id)["fields"]
            if f["destination"] == "description"
        )
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        assert any(i["destination"] == "description" for i in attention["items"]) is (
            mode == "ask"
        )
        if mode == "ignore":
            assert not field["editable"] and field["decision_actions"] == []
            assert all(
                json.loads(d)["destination"] != "description"
                for d in repository.pending_field_effect_decision_documents(100)
            )
            return
        alternatives = field["proposed_values"]
        assert {v["value"] for v in alternatives} == {
            "A voyage reveals a hidden city.",
            "An explorer crosses the sea and discovers an ancient city.",
        }
        command = RecordHumanDecision(repository, clock=lambda: 10)
        args = dict(
            proposal_id=field["proposal_id"],
            target_fingerprint=field["target_fingerprint"],
            destination="description",
            actor="owner",
            outcome="accept",
            selected_value=alternatives[0],
            idempotency_key="synopsis-forged",
        )
        for changes in (
            {"target_fingerprint": "f" * 64},
            {"selected_value": {"kind": "text", "value": "Forged"}},
        ):
            with pytest.raises(ReviewWebError):
                command.run(case_id, **(args | changes))
        command.run(
            case_id,
            **(
                args
                | {
                    "outcome": "keep_current" if choice is None else "accept",
                    "selected_value": None if choice is None else alternatives[choice],
                    "idempotency_key": "synopsis-owner-final",
                }
            ),
        )
        repository.persist_shadow_outcome(case_id, 11)
    with closing(open_repository(path)) as repository:
        repository.persist_shadow_outcome(case_id, 12)
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "description"
        )
        assert field["classification"] == "human_recorded"
        assert field["human_decision"]["outcome"] == (
            "keep_current" if choice is None else "accept"
        )


def test_actual_predecessor_artifacts_and_ebook_comparison():
    import hashlib
    from pathlib import Path

    from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
    from mechanical_librarian.domain.metadata_report import MetadataReport
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from mechanical_librarian.domain.rich_metadata_preview import (
        compile_rich_metadata_preview,
    )
    from mechanical_librarian.domain.shadow_decision import ShadowDecisionSet
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

    root = Path(__file__).parents[1] / "fixtures/audiobook-synopsis-predecessor"
    provenance = json.loads((root / "provenance.json").read_text().removesuffix("\n"))
    assert provenance["source_sha"] == "79dbbf4c3eb58365e266655c32a0f6de3c784e51"
    # Sidecars retain archived capture entries recoverable from Git history.
    names = [
        f"{scenario}-{kind}.json"
        for scenario in ("useful", "marker")
        for kind in (
            "report",
            "proposal",
            "shadow-decision",
            "outcome",
            "automatic-0",
            "automatic-1",
        )
    ] + [f"ebook-{kind}.json" for kind in ("report", "proposal", "policy", "preview")]
    names.append("useful-store.sql")
    for name in names:
        assert (
            hashlib.sha256((root / name).read_bytes()).hexdigest()
            == provenance["files"][name]
        )
    for scenario in ("useful", "marker"):
        MetadataReport.from_json(
            (root / f"{scenario}-report.json").read_text().removesuffix("\n")
        )
        ProposalSet.from_json(
            (root / f"{scenario}-proposal.json").read_text().removesuffix("\n")
        )
        for kind, parser in [
            ("shadow-decision", ShadowDecisionSet),
            ("outcome", ShadowOutcomeSet),
        ]:
            with pytest.raises(ValueError):
                parser.from_json(
                    (root / f"{scenario}-{kind}.json").read_text().removesuffix("\n")
                )
        from mechanical_librarian.domain.automatic_decision import (
            qualification_manifest_is_current,
        )

        for index in range(2):
            file = root / f"{scenario}-automatic-{index}.json"
            decision = AutomaticDecision.from_json(file.read_text().removesuffix("\n"))
            from mechanical_librarian.application.field_effects import (
                authority_from_decision,
            )

            assert authority_from_decision(decision) is None
            assert not qualification_manifest_is_current(
                decision.policy_category,
                decision.policy_option,
                decision.qualification_manifest_sha256,
            )
    report = MetadataReport.from_json(
        (root / "ebook-report.json").read_text().removesuffix("\n")
    )
    proposal = ProposalSet.from_json(
        (root / "ebook-proposal.json").read_text().removesuffix("\n")
    )
    policy = ResolvedMetadataPolicy.from_data(
        json.loads((root / "ebook-policy.json").read_text().removesuffix("\n"))
    )
    assert json.loads(
        json.dumps(compile_rich_metadata_preview(policy, report, proposal).to_data())
    ) == json.loads((root / "ebook-preview.json").read_text().removesuffix("\n"))


def test_genuine_predecessor_store_refuses_before_row_validation(tmp_path):
    import sqlite3
    from pathlib import Path
    from unittest.mock import patch

    from mechanical_librarian.adapters.persistence import schema
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        MetadataReportError,
    )

    source = (
        Path(__file__).parents[1]
        / "fixtures/audiobook-synopsis-predecessor/useful-store.sql"
    )
    database = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(source.read_text())
        assert connection.execute(
            "SELECT count(*) FROM shadow_decision_outcomes"
        ).fetchone() == (1,)
    database.chmod(0o600)
    before = database.read_bytes()
    with patch.object(
        schema, "_validate_schema_contents", wraps=schema._validate_schema_contents
    ) as validate_rows:
        with pytest.raises(MetadataReportError):
            open_repository(database, create=False)
        validate_rows.assert_not_called()
        assert database.read_bytes() == before
        current = tmp_path / "current.sqlite"
        with closing(open_repository(current)):
            pass
        validate_rows.reset_mock()
        with closing(open_repository(current, create=False)):
            pass
        validate_rows.assert_called()


@pytest.mark.parametrize(
    "guard",
    (
        None,
        "backup",
        "target",
        "readback",
        "mismatch",
        "consent",
        "authority",
        "missing_consent",
    ),
)
def test_synopsis_replacement_uses_persisted_guarded_effect(
    effects,
    guard: str | None,
) -> None:
    from mechanical_librarian.adapters.field_effect_runtime import (
        PersistedEffectConsent,
    )
    from mechanical_librarian.application.field_effects import GuardedFieldApplication
    from tests.support.field_effects import CAPABILITY

    case_id, _ = publish_audiobook_preview(
        effects.repository,
        synopsis_report("Chapter 1"),
        ("description_fill",),
        selected_options={"description_fill": "replace_safe_plain_text"},
    )
    effects.repository.persist_shadow_outcome(case_id, 4)
    document = effects.repository._connection.execute(
        "SELECT document FROM automatic_decisions WHERE destination='description'"
    ).fetchone()[0]
    decision = AutomaticDecision.from_json(document)
    authority = PersistedFieldEffectAuthority(effects.repository)
    snapshot = authority.resolve(decision)
    if guard != "missing_consent":
        effects.repository.grant_field_effect_consents(
            snapshot.connection, (snapshot.library,), ("description",), 5
        )
    service = GuardedFieldApplication(
        effects.repository,
        effects.target,
        effects.backup,
        effects.capabilities,
        authority,
        effects.dependencies,
        PersistedEffectConsent(effects.repository),
        effects.recovery,
        effects.reevaluation,
        effects.clock,
        effects.preclaim,
        effects.claim_hook,
        effects.recovery_hook,
    )

    def plan():
        return service.plan(
            decision,
            connection=snapshot.connection,
            library=snapshot.library,
            work_id=snapshot.work_id,
            destination="description",
            pre_value=snapshot.pre_value,
            desired_value=snapshot.decision_value,
            target_fingerprint=snapshot.target_fingerprint,
            target_version=snapshot.target_version,
            dependency_digests=snapshot.dependency_digests,
            capability_digest=CAPABILITY,
        )

    if guard == "missing_consent":
        from mechanical_librarian.application.field_effects import (
            GuardedApplicationError,
        )

        with pytest.raises(GuardedApplicationError, match="consent"):
            plan()
        assert effects.repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        return
    result = plan()
    assert result is not None
    assert result.plan.authority.origin == "automatic_policy"
    effects.target.seed(result.plan)
    if guard == "backup":
        effects.backup.ready_value = False
    elif guard == "target":
        effects.target.drift(result.plan, "Owner changed title")
    elif guard == "readback":
        effects.target.fail_read_after_apply_for.add(result.plan.plan_id)
    elif guard == "mismatch":
        effects.target.after_apply = lambda plan: effects.target.drift(
            plan, "Unexpected synopsis"
        )
    elif guard == "consent":
        effects.repository.revoke_field_effect_consent(
            result.plan.consent.consent_id, effects.clock()
        )
    elif guard == "authority":
        effects.repository.revoke_field_effect_authority(
            decision.decision_id, effects.clock()
        )
    before = dict(effects.target._values)
    applied = service.apply(result.plan.plan_id)
    if guard in {"consent", "authority"}:
        assert (applied.state, applied.failure) == (
            "superseded",
            "consent_revoked" if guard == "consent" else "authority_superseded",
        )
        assert effects.target._values == before
        return
    if guard in {"backup", "target"}:
        assert (applied.state, applied.failure) == (
            "stale",
            "target_changed" if guard == "target" else guard,
        )
        assert effects.target._values == before
        return
    if guard == "mismatch":
        assert applied.state != "verified"
        assert effects.target.read(result.plan).value == "Unexpected synopsis"
        return
    if guard == "readback":
        assert (applied.state, applied.failure) == ("reconciling", "target_failure")
        assert effects.target._values != before
        return
    assert applied.state == "verified"
    assert service.apply(result.plan.plan_id).state == "verified"
    changed = {
        key for key, value in effects.target._values.items() if before.get(key) != value
    }
    assert changed == {
        (snapshot.connection, snapshot.library, snapshot.work_id, "description")
    }
    assert (
        effects.target._values[next(iter(changed))] == "A voyage reveals a hidden city."
    )


@pytest.mark.parametrize(
    "member,value",
    [
        ("decision_policy_version", None),
        ("decision_policy_version", 17),
        ("decision_policy_version", "deterministic-shadow-decision-policy-v16"),
        ("decision_policy_version", "deterministic-shadow-decision-policy-v17"),
        ("decision_policy_sha256", None),
        ("decision_policy_sha256", 17),
        ("decision_policy_sha256", "0" * 64),
    ],
)
def test_sql_refuses_missing_or_substituted_semantic_bindings(tmp_path, member, value):
    import sqlite3

    from tests.unit.test_review_read_indexes import _representative_database

    connection = _representative_database(tmp_path / "state.sqlite", works=8)
    try:
        row = connection.execute(
            "SELECT review_case_id,report_id,proposal_set_id,resolved_policy_id,source_decision_set_id,evaluated_at,document FROM shadow_decision_outcomes LIMIT 1"
        ).fetchone()
        document = json.loads(row[6])
        if value is None:
            document.pop(member)
        else:
            document[member] = value
        identifier = "shadow_outcome_set_" + "a" * 64
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO shadow_decision_outcomes(outcome_set_id,review_case_id,report_id,proposal_set_id,resolved_policy_id,source_decision_set_id,evaluated_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (identifier, *row[:6], json.dumps(document)),
            )
        for table in ("shadow_decision_outcomes", "shadow_outcome_field_facts"):
            assert connection.execute(
                f"SELECT count(*) FROM {table} WHERE outcome_set_id=?", (identifier,)
            ).fetchone() == (0,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "summaries",
    [("Chapter 1", "Track 2."), ("No synopsis", "No description available.")],
)
def test_placeholder_only_sources_do_not_create_description_authority(
    tmp_path, summaries
):
    with closing(open_repository(tmp_path / "markers.sqlite")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            synopsis_report("Chapter 3", summaries),
            ("tags", "description_fill"),
            selected_options={
                "tags": "fill_empty",
                "description_fill": "replace_safe_plain_text",
            },
        )
        repository.persist_shadow_outcome(case_id, 4)
        assert all(
            json.loads(doc)["destination"] != "description"
            for doc in repository.get_review_case_decisions(case_id)
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "state,value", [("missing", None), ("null", None), ("empty", None)]
)
def test_replacement_option_fills_each_empty_target_state(tmp_path, state, value):
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_report import (
        MetadataReport,
        TargetField,
        TargetFieldState,
    )

    report = synopsis_report()
    report = MetadataReport.create(
        target=replace(
            report.target,
            fields=tuple(
                TargetField(field.destination, TargetFieldState(state), value)
                if field.destination.value == "description"
                else field
                for field in report.target.fields
            ),
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
    )
    with closing(open_repository(tmp_path / "empty.sqlite")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("description_fill",),
            selected_options={"description_fill": "replace_safe_plain_text"},
        )
        repository.persist_shadow_outcome(case_id, 4)
        decision = next(
            json.loads(doc)
            for doc in repository.get_review_case_decisions(case_id)
            if json.loads(doc)["destination"] == "description"
        )
        assert (
            decision["policy_option"]
            == "replace_safe_plain_text:audiobook_description_fill"
        )
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "description"
        )
        assert (
            field["automatic_decision"]["selected_value"]["value"]
            == "A voyage reveals a hidden city."
        )


def test_historical_automatic_queue_cannot_reach_scheduler_authority(effects):
    from pathlib import Path

    root = Path(__file__).parents[1] / "fixtures/audiobook-synopsis-predecessor"
    documents = tuple(
        p.read_text().removesuffix("\n")
        for p in sorted(root.glob("useful-automatic-*.json"))
    )
    assert len(documents) == 2
    authority = PersistedFieldEffectAuthority(effects.repository)
    lane = ContinuousGuardedApplication(
        effects.repository,
        {("primary", "audio"): effects.service},
        {("primary", "audio"): effects.capabilities},
        authority,
        (("primary", "audio"),),
        effects.clock,
        lambda *_: None,
    )
    with (
        patch.object(
            effects.repository,
            "pending_field_effect_decision_documents",
            return_value=documents,
        ),
        patch.object(
            authority,
            "resolve",
            side_effect=AssertionError("historical decision reached authority"),
        ),
    ):
        assert lane.schedule() == 0
    assert effects.repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)


@pytest.mark.parametrize("substitution", ["incumbent", "option"])
def test_resealed_incumbent_and_option_refuse_persisted_authority(
    tmp_path, substitution
):
    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        _automatic_decision_value,
    )
    from mechanical_librarian.domain.field_effect import FieldEffectError
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from mechanical_librarian.domain.review_case import ReviewCase
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

    report = synopsis_report("Chapter 1")
    with closing(open_repository(tmp_path / "substitution.sqlite")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("description_fill",),
            selected_options={"description_fill": "replace_safe_plain_text"},
        )
        repository.persist_shadow_outcome(case_id, 4)
        decision = next(
            AutomaticDecision.from_json(document)
            for document in repository.get_review_case_decisions(case_id)
            if json.loads(document)["destination"] == "description"
        )
        connection = repository._connection
        case = ReviewCase.from_json(
            connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (case_id,),
            ).fetchone()[0]
        )
        proposal = ProposalSet.from_json(
            connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (decision.proposal_set_id,),
            ).fetchone()[0]
        )
        outcome = ShadowOutcomeSet.from_json(
            connection.execute(
                "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? "
                "ORDER BY evaluated_at DESC LIMIT 1",
                (case_id,),
            ).fetchone()[0]
        )
        assert _automatic_decision_value(decision, case, report, proposal, outcome)[
            "value"
        ]
        if substitution == "incumbent":
            report = synopsis_report("Useful existing synopsis.")
        else:
            arguments = {
                key: value
                for key, value in json.loads(decision.to_json()).items()
                if key
                not in {"decision_id", "idempotency_digest", "origin", "schema_version"}
            }
            arguments["policy_option"] = (
                "safe_plain_text:audiobook_description_preserve"
            )
            decision = AutomaticDecision.from_json(
                AutomaticDecision.create(**arguments).to_json()
            )
        with pytest.raises(FieldEffectError, match="authority_binding"):
            _automatic_decision_value(decision, case, report, proposal, outcome)


@pytest.mark.parametrize("summary", [{}, "<script>bad()</script>"])
def test_current_detail_does_not_offer_unadmitted_audiobook_synopsis(tmp_path, summary):
    from tests.support.audiobook_reports import (
        synthetic_audiobook_product_lookup_report,
    )

    report = synthetic_audiobook_product_lookup_report(
        (
            {
                "asin": "B000000001",
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
                "narrators": [{"name": "Example Narrator"}],
                "summary": summary,
                "genres": [{"type": "tag", "name": "Exploration"}],
            },
        )
    )
    path = tmp_path / "markers.sqlite"
    with closing(open_repository(path)) as repository:
        case, _ = publish_audiobook_preview(
            repository,
            report,
            ("tags", "description_fill"),
            selected_options={
                "tags": "fill_empty",
                "description_fill": "safe_plain_text",
            },
        )
        repository.persist_shadow_outcome(case, 4)
    with closing(open_repository(path)) as repository:
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case)["fields"]
            if f["destination"] == "description"
        )
        assert field["decision_actions"] == []
        assert field["proposed_values"] == []
        assert field["editable"] is False
        assert isinstance(field["automatic_withheld_reason"], str)
        assert field["automatic_withheld_reason"]
