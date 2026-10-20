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
from mechanical_librarian.domain.field_value_comparison import (
    recognized_synopsis_placeholder,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    project_audiobook_automatic_decision_value,
)
from tests.support.audiobook_reports import publish_audiobook_preview
from tests.support.audiobook_synopsis import (
    recording_synopsis_report as synopsis_report,
)
from tests.support.field_effects import effects as effects
from tests.support.genre_reports import audiobook_genre_report


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
        "n/a",
    ],
)
def test_closed_placeholder_vocabulary(value):
    assert recognized_synopsis_placeholder(value)


@pytest.mark.parametrize(
    "value",
    [
        "Chapter 1: A voyage begins",
        "Track 1 takes us through a city",
        "No description can capture this journey",
        "Chapter I",
        "Chapter one",
        "Chapter ١",
        "Short.",
        "One paragraph.\n\nAnother paragraph.",
        "Chapter 1..",
    ],
)
def test_near_misses_are_not_placeholders(value):
    assert not recognized_synopsis_placeholder(value)


_CREDIT_LINES = (
    "Narrated by Example Narrator",
    "Narrated by Example Narrator.",
    "Read by J. R. Example",
    "Written and narrated by Ada Example",
    "Narrated by Ann Example, Bea Example and Cy Example",
    "Narrator: Example Narrator",
    "Narrators: Ann Example & Bea Example",
    "Cast: Ann Example, Bea Example, Cy Example",
    "Unabridged - Narrated by Example Narrator",
    "Narrated by Example Narrator; Unabridged.",
    "Translated by Ann Example | Read by Bea O'Example-Smith",
    "Written by Ann O’Example. Read by Bea Example.",
)
_SHORT_FRAGMENTS = (
    "Unabridged",
    "(Unabridged)",
    "Abridged.",
    "Audiobook",
    "Audiobooks",
    "Audio Book",
    "Audio CD",
    "MP3 CD",
    "Unabridged Audiobook",
    "Audible Audiobook",
    "Part 2",
    "Disc 1 of 3",
    "CD 4",
    "Side 2",
    "Track 3 of 12",
    "N/A",
    "None",
    "Unknown",
    "TBD.",
)
# Closed shapes the retained-library recount found still missed (#1250).
_CREDIT_LINE_EXTENSIONS = (
    "Read by Example Narrator; Unabridged; Length:33h 50s;"
    " Publisher: Example Audio, March 27, 2012",
    "Narrated by Example Narrator. Length: 10 hrs and 3 mins",
    "Narrated by Example Narrator; Publisher: Example Audio, Inc.",
    "Read by Example Narrator; Released: 2012-03-27",
    "Read by Example Narrator; Release date: 27 March 2012",
    "Read by Example Narrator; Unabridged; Tag by A_S",
    "Ripped and tagged by user42",
    "Encoded by Example_Ripper",
    "Created by Example Narrator",
    "Read by Example Narrator; Unabr",
    "Unab, read by Example Narrator",
    "Unabridge",
    "Read by",
    "narrated by:",
    "Narrated by; Unabridged",
    "<p></p>",
    "<p><br/></p>",
    '<p class="synopsis"></p>',
    "<!-- no description -->",
    "Section 3",
    "Section 2 of 9.",
    "Audiobook created by Markable",
    "Unabridged created by Markable",
    "fre:ac - free audio converter",
)
_STILL_SYNOPSES = (
    "Narrated by the ghost of her sister, this novel follows…",
    "Narrated by the ghost of her sister, this novel follows her home",
    "Written by a master of suspense, this thriller…",
    "Read by millions…",
    "Narrated by " + ", ".join(f"Name{chr(97 + index)} Example" for index in range(7)),
    "Narrated by " + " and ".join("Example Narrator" for _ in range(20)),
    "Synthetic Journey",
    "Synthetic Journey (Unabridged)",
    "A boy discovers he is a wizard.",
    "Two rivals cross the sea.",
    "Audiobook of the year",
    "Narrated by Example Narrator in 2020",
    "Part one",
    "None of them survived the winter.",
    "Fantasy (Unabridged)",
    "DARK MATTER",
    "Length: 33h 50s",
    "Publisher: Example Audio",
    "Released: 2012-03-27",
    "Narrated by Example Narrator; Length: a long time",
    "Narrated by Example Narrator; Released: someday soon",
    "Tag by A_S and then she left",
    "Read by Example_Narrator",
    "Unable to sleep, she wanders the city.",
    "Una",
    "Unabridgedly told",
    "Section one",
    "Example created by Markable",
    "fre:ac",
    "<p>A story of loss.</p>",
    "< A story >",
    "<1984: a dystopia>",
    "<A thrilling story of love and loss>",
    # Each published bound decides one row: over 240 characters with four
    # five-or-fewer-token names, seven segments, and a six-token name.
    "Narrated by " + " and ".join(f"{'Long' * 12} Example" for _ in range(4)),
    "Narrated by Ann Example; Unabridged; Abridged; Audiobook; Audio CD; MP3 CD;"
    " Audiobooks",
    "Narrated by Ann Bea Cy Dee Eve Example",
    "Narrated by Ann Example, Bob loses everything tonight",
    "Narrated by Example Narrator. Set in 1920, a voyage reveals a hidden city"
    " beneath the ice, and an archivist must decide what to keep.",
)


@pytest.mark.parametrize(
    "value", _CREDIT_LINES + _SHORT_FRAGMENTS + _CREDIT_LINE_EXTENSIONS
)
def test_credit_lines_and_short_fragments_are_placeholders(value):
    """Structured credits and format fragments describe nothing (#1250)."""
    assert recognized_synopsis_placeholder(value)


@pytest.mark.parametrize("value", _STILL_SYNOPSES)
def test_prose_that_merely_resembles_a_credit_stays_a_synopsis(value):
    assert not recognized_synopsis_placeholder(value)


@pytest.mark.parametrize(
    "current", _CREDIT_LINES + _SHORT_FRAGMENTS + _CREDIT_LINE_EXTENSIONS
)
def test_persisted_reconstruction_replaces_what_the_floor_recognizes(current):
    """The persisted projection reads the same floor the preview does."""
    candidate = {"kind": "text", "value": "A voyage reveals a hidden city."}
    assert (
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "replace_safe_plain_text:audiobook_description_replace",
            candidate,
            current,
        )
        == candidate
    )
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "safe_plain_text:audiobook_description_fill",
            {"kind": "text", "value": current},
            None,
        )


@pytest.mark.parametrize("current", _STILL_SYNOPSES)
def test_persisted_reconstruction_preserves_what_stays_a_synopsis(current):
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "replace_safe_plain_text:audiobook_description_replace",
            {"kind": "text", "value": "A voyage reveals a hidden city."},
            current,
        )


@pytest.mark.parametrize(
    "current",
    [
        "Narrated by Example Narrator",
        "Unabridged",
        "Read by Example Narrator; Unabr",
        "<p></p>",
    ],
)
def test_a_recognized_incumbent_is_replaced_through_the_persisted_decision(
    tmp_path, current
):
    report = synopsis_report(current, ("A voyage reveals a hidden city.",))
    database = tmp_path / "floor.sqlite"
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
        decision = next(
            AutomaticDecision.from_json(document)
            for document in repository.get_review_case_decisions(case_id)
            if json.loads(document)["destination"] == "description"
        )
        assert decision.policy_option == (
            "replace_safe_plain_text:audiobook_description_replace"
        )


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
    from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
    from mechanical_librarian.domain.rich_metadata_preview import (
        compile_rich_metadata_preview,
    )
    from mechanical_librarian.domain.shadow_decision import ShadowDecisionSet
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
    from tests.support.metadata_report_schemas import retained_report_as_current

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
        retained_report_as_current(
            (root / f"{scenario}-report.json").read_text().removesuffix("\n")
        )
        with pytest.raises(ValueError, match="proposal document is invalid"):
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
    report = retained_report_as_current(
        (root / "ebook-report.json").read_text().removesuffix("\n")
    )
    with pytest.raises(ValueError, match="proposal document is invalid"):
        ProposalSet.from_json(
            (root / "ebook-proposal.json").read_text().removesuffix("\n")
        )
    policy = ResolvedMetadataPolicy.from_data(
        json.loads((root / "ebook-policy.json").read_text().removesuffix("\n"))
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    expected = json.loads((root / "ebook-preview.json").read_text().removesuffix("\n"))
    expected["proposal_set_id"] = proposal.proposal_set_id
    expected["report_id"] = report.report_id
    assert (
        json.loads(
            json.dumps(
                compile_rich_metadata_preview(policy, report, proposal).to_data()
            )
        )
        == expected
    )


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
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        evaluate_report,
    )

    assert audiobook_review_scope(report) is None
    with closing(open_repository(tmp_path / "markers.sqlite")) as repository:
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        # The witnessed title only restates the current one (#1253).
        assert all(
            alternative.assessment.value == "preserves_target"
            for result in proposal.results
            if result.destination.value == "title"
            for alternative in result.alternatives
        )
        assert not any(
            result.alternatives
            for result in proposal.results
            if result.destination.value != "title"
        )
        repository.publish_proposal(proposal)
        assert json.loads(repository.admit_review_case(proposal))["status"] == "no_case"


@pytest.mark.parametrize("destination", ("description", "genres", "series", "title"))
@pytest.mark.parametrize(
    "guard", (None, "missing_consent", "consent", "target", "source", "readback")
)
def test_work_owner_choice_uses_persisted_guarded_effect(effects, destination, guard):
    from mechanical_librarian.application.field_effects import GuardedApplicationError
    from mechanical_librarian.application.review_web import RecordHumanDecision
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )
    from mechanical_librarian.domain.human_decision import (
        HumanDecision,
        decision_actions,
    )
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        evaluate_report,
    )
    from tests.support.audiobook_reports import (
        synthetic_audiobook_product_lookup_report,
    )
    from tests.support.field_effects import CAPABILITY

    report = synthetic_audiobook_product_lookup_report(
        (
            {
                "asin": "B000000001",
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
                "summary": "A voyage reveals a hidden city.",
                "genres": [{"type": "genre", "name": "Adventure"}],
                "seriesPrimary": {"name": "Example Saga", "position": "2"},
            },
        ),
        target_title="Synthetic Journey (Unabridged)",
    )
    assert not admitted_audiobook_identity(report)
    assert audiobook_review_scope(report) == "work"
    proposal = evaluate_report(report)
    assert {r.destination.value for r in proposal.results if r.alternatives} == {
        "description",
        "genres",
        "series",
        "title",
    }
    assert {
        r.destination.value
        for r in proposal.results
        if decision_actions({"result": r.to_data()})
    } == {"description", "genres", "series", "title"}
    repository = effects.repository
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    field = next(
        f
        for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        if f["destination"] == destination
    )
    RecordHumanDecision(repository, clock=lambda: 10).run(
        case_id,
        proposal_id=field["proposal_id"],
        target_fingerprint=field["target_fingerprint"],
        destination=destination,
        actor="owner",
        outcome="accept",
        selected_value=field["proposed_values"][0],
        idempotency_key="work-review-effect",
    )
    decision = HumanDecision.from_json(
        repository._connection.execute(
            "SELECT document FROM human_decisions WHERE destination=?", (destination,)
        ).fetchone()[0]
    )
    assert decision.application == "apply"
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    if guard != "missing_consent":
        repository.grant_field_effect_consents(
            snapshot.connection, (snapshot.library,), (destination,), 11
        )
    service = effects.persisted_service(repository, effects.target, authority)
    arguments = dict(
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination=destination,
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    if guard == "missing_consent":
        with pytest.raises(GuardedApplicationError, match="consent"):
            service.plan(decision, **arguments)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        return
    planned = service.plan(decision, **arguments)
    assert planned is not None
    assert planned.plan.authority.origin == "human"
    assert (
        planned.plan.desired_value
        == {
            "description": "A voyage reveals a hidden city.",
            "genres": ["Adventure"],
            "series": [{"name": "Example Saga", "sequence": "2"}],
            "title": "Synthetic Journey",
        }[destination]
    )
    # Title and series each name the other as a dependency (#1253).
    assert [
        value.value
        for value in next(
            r for r in proposal.results if r.destination.value == destination
        ).dependencies
    ] == {"series": ["title"], "title": ["subtitle", "series"]}.get(destination, [])
    assert snapshot.dependency_digests == (report.target.fingerprint,)
    effects.target.seed(planned.plan)
    if guard == "target":
        effects.target.drift(
            planned.plan,
            {
                "description": "Changed by owner",
                "genres": ["Different genre"],
                "series": [{"name": "Other Saga", "sequence": "9"}],
                "title": "Changed by owner",
            }[destination],
        )
    elif guard == "consent":
        repository.revoke_field_effect_consent(
            planned.plan.consent.consent_id, effects.clock()
        )
    elif guard == "source":
        from dataclasses import replace

        from mechanical_librarian.domain.metadata_report import (
            MetadataReport,
            TargetDestination,
            TargetField,
            TargetFieldState,
        )

        successor = MetadataReport.create(
            target=replace(
                report.target,
                fields=tuple(
                    TargetField(
                        field.destination, TargetFieldState.VALUE, "New source snapshot"
                    )
                    if field.destination is TargetDestination.DESCRIPTION
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
        repository.publish(successor, lambda: True, expected_current=report.report_id)
    elif guard == "readback":
        effects.target.fail_read_after_apply_for.add(planned.plan.plan_id)
    before = dict(effects.target._values)
    applied = service.apply(planned.plan.plan_id)
    if guard == "target":
        assert (applied.state, applied.failure) == ("stale", "target_changed")
        assert effects.target._values == before
    elif guard == "consent":
        assert (applied.state, applied.failure) == ("superseded", "consent_revoked")
        assert effects.target._values == before
    elif guard == "source":
        assert (applied.state, applied.failure) == ("stale", "authority")
        assert effects.target._values == before
    elif guard == "readback":
        assert (applied.state, applied.failure) == ("reconciling", "target_failure")
        effects.target.fail_read_after_apply_for.clear()
        effects.target.fail_reads_for.clear()
        assert service.apply(planned.plan.plan_id).state == "verified"
    else:
        assert applied.state == "verified"
        assert service.apply(planned.plan.plan_id).state == "verified"
        changed = {
            key
            for key, value in effects.target._values.items()
            if before.get(key) != value
        }
        assert changed == {
            (snapshot.connection, snapshot.library, snapshot.work_id, destination)
        }
    repository.close()
    effects.repository = open_repository(effects.database_path)
    assert decision.to_json() in effects.repository.get_review_case_decisions(case_id)
    assert effects.repository.get_field_effect(planned.plan.plan_id) is not None


@pytest.mark.parametrize(
    "identity",
    ("exact", "wrong_title", "wrong_author", "incomplete_authors", "variant"),
)
def test_work_source_cannot_borrow_another_candidates_identity(identity):
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.audiobook_reports import (
        synthetic_audiobook_product_lookup_report,
    )

    products: tuple[dict[str, object], ...] = (
        {
            "asin": "B000000001",
            "title": "Synthetic Journey",
            "authors": [{"name": "Example Author"}],
            "summary": "Supported synopsis.",
        },
        {
            "asin": "B000000002",
            "title": {
                "wrong_title": "Different Story",
                "variant": "Synthetic Journey (Abridged)",
            }.get(identity, "Synthetic Journey"),
            "authors": []
            if identity == "incomplete_authors"
            else [
                {
                    "name": "Other Author"
                    if identity == "wrong_author"
                    else "Example Author"
                }
            ],
            "summary": "Other source synopsis.",
            "genres": [{"type": "genre", "name": "Adventure"}],
        },
    )
    report = synthetic_audiobook_product_lookup_report(products)
    proposal = evaluate_report(report)
    fields = {result.destination.value: result for result in proposal.results}
    descriptions = {
        alternative.value.value for alternative in fields["description"].alternatives
    }
    assert "Supported synopsis." in descriptions
    assert ("Other source synopsis." in descriptions) is (identity == "exact")
    assert bool(fields["genres"].alternatives) is (identity == "exact")
    # Every witnessed title restates the current one (#1253).
    assert all(
        alternative.assessment.value == "preserves_target"
        for alternative in fields["title"].alternatives
    )
    assert all(
        not result.alternatives
        for result in proposal.results
        if result.destination.value not in {"description", "genres", "title"}
    )


@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
def test_work_policy_has_no_automatic_decisions_or_effects(tmp_path, destination):
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
    from tests.support.audiobook_synopsis import synopsis_report as work_report

    with closing(open_repository(tmp_path / "work.sqlite")) as repository:
        report = work_report()
        from mechanical_librarian.domain.proposal_set import (
            audiobook_review_scope,
            evaluate_report,
        )

        assert audiobook_review_scope(report) == "work"
        assert not next(
            r for r in evaluate_report(report).results if r.destination.value == "tags"
        ).alternatives
        case_id, policy = publish_audiobook_preview(
            repository,
            report,
            ("tags", "description_fill"),
            selected_options={
                "tags": "fill_empty",
                "description_fill": "safe_plain_text",
            },
            selected_destinations={"description_fill": destination},
        )
        assert dict(policy.destinations)["tags"] == "automatic"
        assert dict(policy.options)["tags"] == "fill_empty"
        if destination == "ignore":
            assert case_id is None
            stored = json.loads(
                repository._connection.execute(
                    "SELECT document FROM proposal_sets"
                ).fetchone()[0]
            )
            result = next(
                r for r in stored["results"] if r["destination"] == "description"
            )
            assert result["alternatives"] == []
            assert result["reasons"] == ["policy_ignored"]
        else:
            outcome = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(case_id, 4)
            )
            field = next(f for f in outcome.outcomes if f.destination == "description")
            assert field.policy_destination == "ask"
            assert field.disposition == "manual"
            assert repository.get_review_case_decisions(case_id) == ()
        assert repository.pending_field_effect_decision_documents(100) == ()
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("category", "destination"), (("series", "series"), ("title_layout", "title"))
)
def test_work_series_and_title_have_no_automatic_decisions_or_effects(
    tmp_path, category, destination
):
    """Built-in Automatic reads as Ask on the work route (#1253)."""
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        evaluate_report,
    )
    from mechanical_librarian.domain.rich_metadata_preview import (
        compile_rich_metadata_preview,
    )
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
    from tests.support.audiobook_reports import (
        synthetic_audiobook_product_lookup_report,
    )

    report = synthetic_audiobook_product_lookup_report(
        (
            {
                "asin": "B000000001",
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
                "seriesPrimary": {"name": "Example Saga", "position": "2"},
            },
        ),
        target_title="Synthetic Journey (Unabridged)",
    )
    assert audiobook_review_scope(report) == "work"
    builtin = MetadataPolicyProfile.builtin()
    assert dict(builtin.destinations)[category] == "automatic"
    with closing(open_repository(tmp_path / "work.sqlite")) as repository:
        case_id, policy = publish_audiobook_preview(
            repository,
            report,
            ("series", "title_layout"),
            selected_options=dict(builtin.options),
        )
        assert dict(policy.destinations)[category] == "automatic"
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        field = next(f for f in outcome.outcomes if f.destination == destination)
        assert (field.policy_destination, field.disposition) == ("ask", "manual")
        assert repository.get_review_case_decisions(case_id) == ()
        assert repository.pending_field_effect_decision_documents(100) == ()
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions"
        ).fetchone() == (0,)
    if category == "series":
        asked = resolve_policy(
            MetadataPolicyProfile.create(
                "Ask series",
                dict(builtin.options),
                {**dict(builtin.destinations), "series": "ask"},
            ),
            report.target.library,
        )
        previews = tuple(
            next(
                value
                for value in compile_rich_metadata_preview(
                    resolved, report, evaluate_report(report, resolved.resolved_id)
                ).fields
                if value.field == "series"
            )
            for resolved in (policy, asked)
        )
        assert previews[0] == previews[1]


def test_work_scope_blocks_secondary_inputs_before_collaborators(tmp_path):
    from mechanical_librarian.application.cover_packets import prepare_cover_packet
    from mechanical_librarian.domain.description_advice import (
        DescriptionAdviceInput,
        DescriptionPreferences,
    )
    from tests.support.audiobook_synopsis import synopsis_report as work_report

    report = work_report()
    with pytest.raises(ValueError, match="recording admission"):
        DescriptionAdviceInput.from_report(report, DescriptionPreferences())
    with pytest.raises(ValueError, match="recording admission"):
        prepare_cover_packet(
            report.to_json(), lambda *_: pytest.fail("work-only cover read")
        )


def test_work_scope_wire_contract_and_direct_retry_refusal(effects, monkeypatch):
    from pydantic import ValidationError

    from mechanical_librarian.application.review_web import _report_detail
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from mechanical_librarian.interfaces.web.app import (
        _CaseDetailResponse,
        _ReportDetailResponse,
    )
    from tests.support.audiobook_synopsis import synopsis_report as work_report

    report = work_report()
    repository = effects.repository
    proposal = evaluate_report(report)
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    case = InspectCurrentReviewCase(repository).run(case_id)
    # Transport enrichment when no field application is configured.
    for field in case["fields"]:
        field["effect_capability"] = "unavailable"
    case["writes_consented"] = False
    recorded = _report_detail(report.to_data(), report)
    for model, value in (
        (_CaseDetailResponse, case),
        (_ReportDetailResponse, recorded),
    ):
        assert model.model_validate(value).audiobook_review_scope == "work"
        assert value["recording_identity_basis"] is None
        missing = dict(value)
        del missing["audiobook_review_scope"]
        with pytest.raises(ValidationError):
            model.model_validate(missing)
        malformed_values: tuple[object, ...] = ("edition", True, {}, 1)
        for malformed in malformed_values:
            with pytest.raises(ValidationError):
                model.model_validate({**value, "audiobook_review_scope": malformed})
        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    **value,
                    "recording_identity_basis": {
                        "basis": "chapter_runtime_and_structure",
                        "provider": "source",
                        "candidate": "recording",
                    },
                }
            )
        ebook = dict(value)
        if model is _CaseDetailResponse:
            target = ebook["target"]
            assert isinstance(target, dict)
            ebook["target"] = {**target, "media_kind": "epub"}
        else:
            ebook["media_kind"] = "epub"
        with pytest.raises(ValidationError):
            model.model_validate(ebook)
    from unittest.mock import Mock

    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        _automatic_decision_value,
        _validate_automatic_decision_authority,
    )
    from mechanical_librarian.application.cover_consideration import (
        CoverConsiderationService,
    )
    from mechanical_librarian.application.review_application import (
        ReviewApplicationError,
        prepare_cover_consideration,
    )
    from mechanical_librarian.domain.field_effect import FieldEffectError

    cover = Mock(spec=CoverConsiderationService)
    cover.current.side_effect = AssertionError("work-only cover lookup")
    for retry in (False, True):
        with pytest.raises(ReviewApplicationError, match="unavailable"):
            prepare_cover_consideration(
                InspectCurrentReviewCase(repository),
                cover,
                case_id,
                serialized=lambda call: call(),
                retry=retry,
            )
    cover.current.assert_not_called()
    # Neither reconstruction entry point may inspect a forged authority before
    # refusing the report's work-only scope.
    forged = Mock()
    type(forged).target = property(lambda _: pytest.fail("forged authority read"))
    for reconstruct in (
        _automatic_decision_value,
        _validate_automatic_decision_authority,
    ):
        with pytest.raises(FieldEffectError, match="authority_binding"):
            reconstruct(forged, forged, report, proposal, forged)
    store = repository.description_advice_store()
    monkeypatch.setattr(store, "_task_configured", lambda: True)
    with pytest.raises(ValueError, match="description retry unavailable"):
        store.request_retry(case_id, 11)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_preparation_pending"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("current", "outcome", "option"),
    (
        (("Audiobook",), "candidate", "fill_empty:audiobook_genres_fill"),
        (
            ("Audiobook", "Fantasy"),
            "keep_current",
            "fill_empty:audiobook_genres_preserve",
        ),
    ),
)
def test_a_format_only_genre_list_is_filled_through_the_persisted_decision(
    tmp_path, current, outcome, option
):
    """The genre half of the floor, through the same persisted seam (#1250)."""
    with closing(open_repository(tmp_path / "genres.sqlite")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            audiobook_genre_report(current, ("Fantasy",)),
            ("genres",),
            selected_options={"genres": "fill_empty"},
        )
        repository.persist_shadow_outcome(case_id, 4)
        decision = next(
            AutomaticDecision.from_json(document)
            for document in repository.get_review_case_decisions(case_id)
            if json.loads(document)["destination"] == "genres"
        )
    assert (decision.outcome, decision.policy_option) == (outcome, option)
