"""The description advice label sample and score (ADR-0099).

Every store is product-built through the description advice fixtures, closed,
copied, and read by the script from the copy. A sentinel sits in every
description the store holds, and no output may carry it.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import scripts.admission_rule_ledger as ledger
import scripts.description_advice_labels as labels
import scripts.hardcover_series_sample as series_sample
import scripts.relaxation_label_sample as relaxation_sample

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.application.configuration import CONFIG_SCHEMA
from mechanical_librarian.application.contained_reviewer_contract import (
    canonical_json,
)
from mechanical_librarian.application.description_advice import (
    DescriptionAdviceService,
)
from mechanical_librarian.application.review_web import RecordHumanDecision
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceOutput,
    DescriptionPreferences,
    description_digest,
    description_qualification_manifest,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    MetadataReport,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.description_advice import (
    _description_candidate,
    _prepared,
    qualify_description_advice,
)
from tests.support.proposal_evidence import (
    _report,
    _target_work_candidate,
    _target_work_report,
)
from tests.unit.test_owner_settings_store import _document

SENTINEL = "Quillwort Vantablack"
SOURCE = (
    f"This complete provider description of {SENTINEL} follows a synthetic "
    "journey through an old library and the people who preserve its stories."
)
LIBRARY = f"The library's own description of {SENTINEL}."
ADVICE = (
    f"A cleaned description of {SENTINEL}: a synthetic journey through an old "
    "library and the people who keep its stories."
)
RETRIED = (
    f"A newer description of {SENTINEL}: an old library and the people who "
    "keep its stories, told again."
)
_LINK = re.compile(
    r"^http://localhost:8080/library\?lens=all&preview=review_case_[0-9a-f]{64}"
    r"&field=description$"
)
_ASK_ONLY = {
    "automatic": False,
    "ask_only_policy": True,
    "automatic_policy": ("cover_order", "fixed_source_order"),
}


def _description_report(library: str | None, source: str = SOURCE):
    """One work whose one source offers `source`, over the library's
    `library` description (None: missing)."""
    base = _target_work_report(
        (
            (
                "catalogue",
                "hardcover",
                (_description_candidate("catalogue", "edition-1", "work-1", source),),
                True,
            ),
        )
    )
    return _report(
        fields=tuple(
            (
                TargetField(
                    value.destination, TargetFieldState.VALUE, library, restorable=True
                )
                if library is not None
                else TargetField(value.destination, TargetFieldState.MISSING)
            )
            if value.destination.value == "description"
            else value
            for value in base.target.fields
        ),
        local_claims=base.local_claims,
        providers=base.providers,
    )


@dataclass
class Store:
    tmp_path: Path
    # The untyped fixture's repository, store, binding and access.
    repository: Any
    store: Any
    binding: Any
    candidate: DescriptionAdviceCandidate
    clock: int = 100
    access: Any = None

    @property
    def case(self) -> str:
        return self.binding.review_case_id

    def tick(self) -> int:
        self.clock += 1
        return self.clock

    def settings(self, **description_advice: bool) -> None:
        """Save owner settings in the app, as setup does."""
        value = _document().model_dump()
        value["description_advice"] = {
            **value["description_advice"],
            **description_advice,
        }
        connection = self.repository._connection
        connection.execute("DELETE FROM owner_settings")
        connection.execute(
            "INSERT INTO owner_settings VALUES(1,1,?)", (_canonical_json(value),)
        )
        connection.commit()

    def choose(self, candidate: DescriptionAdviceCandidate | None = None) -> None:
        """The owner chooses the adviser's text."""
        chosen = candidate or self.candidate
        self.store.admit(
            chosen.candidate_id,
            self.binding,
            "human",
            hashlib.sha256(f"{chosen.candidate_id}{self.clock}".encode()).hexdigest(),
            self.tick(),
        )

    def decide(self, outcome: str, case: str | None = None, binding=None) -> None:
        """The owner records a human decision on the description."""
        case = case or self.case
        binding = binding or self.binding
        (document,) = self.repository._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?", (case,)
        ).fetchone()
        proposal_id = next(
            str(value["proposal_id"])
            for value in ReviewCase.from_json(document).proposals
            if value["destination"] == "description"
        )
        selected: dict[str, object] | None = None
        if outcome in {"accept", "reject"}:
            selected = {"kind": "text", "value": SOURCE}
        at = self.tick()
        RecordHumanDecision(self.repository, clock=lambda: at).run(
            case,
            proposal_id=proposal_id,
            target_fingerprint=binding.target_fingerprint,
            destination="description",
            actor="owner",
            outcome=outcome,
            selected_value=selected,
            idempotency_key=f"description-{outcome}-{at}",
        )

    def retry(
        self,
        text: str = RETRIED,
        disposition: Literal["ready", "needs_review"] = "ready",
    ) -> DescriptionAdviceCandidate:
        """The owner asks for new advice and the adviser answers."""
        store, binding = self.store, self.binding
        store.request_retry(self.case, self.tick())
        attempt = store.begin_attempt(
            self.candidate.request_identity,
            binding,
            self.candidate.engine,
            self.candidate.model,
            self.tick(),
            lambda: store.binding_is_current(binding),
            retry=True,
        )
        assert attempt is not None
        assert store.start(attempt[0], self.tick(), lambda: True)
        at = self.tick()
        latest = DescriptionAdviceCandidate.create(
            **{
                **self.candidate.model_dump(exclude={"candidate_id", "output"}),
                "output": DescriptionAdviceOutput(
                    description=text, disposition=disposition
                ),
                "attempt_id": attempt[0],
                "created_at": at,
            }
        )
        assert store.finish(
            attempt[0], latest, at, lambda: store.binding_is_current(binding)
        )
        return latest

    def copy(self) -> Path:
        self.repository.close()
        target = self.tmp_path / "copy" / "application.sqlite3"
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(self.tmp_path / "reports.sqlite", target)
        return target


def _store(
    tmp_path: Path,
    library: str | None = LIBRARY,
    source: str = SOURCE,
    *,
    settings: bool = True,
    **options,
) -> Store:
    captured = {}

    def probe(repository, report, deterministic, access, calls):
        captured["access"] = access

    keywords = {**_ASK_ONLY, "description": ADVICE, **options}
    repository, store, binding, candidate, _ = _prepared(
        tmp_path,
        report_override=_description_report(library, source),
        pipeline_probe=probe,
        **keywords,
    )
    prepared = Store(tmp_path, repository, store, binding, candidate)
    prepared.access = captured["access"]
    if settings:
        prepared.settings()
    return prepared


def _admit(prepared: Store, report, profile_name: str | None = None) -> SimpleNamespace:
    """Admit `report`'s work as a current case under the active policy, or
    under a new ask-only policy named `profile_name` (a successor)."""
    repository = prepared.repository
    connection = repository._connection
    active, generation = connection.execute(
        "SELECT active_policy_id,generation FROM metadata_policy_state"
    ).fetchone()
    if profile_name is None:
        resolved = repository.metadata_policy_resolution_for_preview(
            report.target.library
        )
    else:
        options = dict(DEFAULT_OPTIONS)
        options["description_fill"] = next(
            name
            for name in OPTION_CATALOGUE["description_fill"]
            if name != options["description_fill"]
        )
        profile = MetadataPolicyProfile.create(
            profile_name,
            options,
            {
                name: "unsupported" if name == "explicit" else "ask"
                for name in OPTION_CATALOGUE
            },
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            active,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            prepared.tick(),
            expected_active_generation=generation,
        )
    proposal_set = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    return SimpleNamespace(
        review_case_id=case_id,
        report=report,
        proposal_set_id=proposal_set.proposal_set_id,
    )


def _advise(prepared: Store, admitted: SimpleNamespace) -> DescriptionAdviceBinding:
    """Prepare advice for an admitted case through the product service."""
    store = prepared.store
    report = admitted.report
    binding = DescriptionAdviceBinding(
        review_case_id=admitted.review_case_id,
        report_id=report.report_id,
        proposal_set_id=admitted.proposal_set_id,
        target_fingerprint=report.target.fingerprint,
        policy_generation=prepared.repository._connection.execute(
            "SELECT generation FROM metadata_policy_state"
        ).fetchone()[0],
        access_revision=prepared.binding.access_revision,
        preferences_digest=description_digest(store.current_settings().model_dump()),
    )
    service = DescriptionAdviceService(
        store, prepared.access, store.current_settings, lambda: prepared.tick()
    )
    assert (
        service.prepare(report, binding, lambda: store.binding_is_current(binding))
        is not None
    )
    return binding


def _other_work(library: str | None = LIBRARY, source: str | None = SOURCE):
    """A second library item; with no `source`, no source has a description."""
    report = _description_report(library, SOURCE if source is None else source)
    providers = report.providers
    if source is None:
        providers = _target_work_report(
            (
                (
                    "catalogue",
                    "hardcover",
                    (
                        replace(
                            _target_work_candidate("catalogue", "edition-1", "work-1"),
                            claims=tuple(
                                claim
                                for claim in _target_work_candidate(
                                    "catalogue", "edition-1", "work-1"
                                ).claims
                                if claim.kind is not ClaimKind.DESCRIPTION
                            ),
                        ),
                    ),
                    True,
                ),
            )
        ).providers
    return MetadataReport.create(
        target=TargetFacts(
            report.target.connection,
            report.target.library,
            "item-2",
            report.target.fields,
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=providers,
        cover_sources=report.cover_sources,
    )


def _run(*argv: str) -> str:
    output = io.StringIO()
    assert labels.main(list(argv), output) == 0
    text = output.getvalue()
    assert SENTINEL not in text
    return text


def _sample(database: Path, *options: str) -> list[str]:
    return _run("sample", "--database", str(database), *options).splitlines()


def _score(database: Path) -> dict:
    return json.loads(_run("score", "--database", str(database), "--format", "json"))


def _refusal(*argv: str) -> str:
    with pytest.raises(SystemExit) as raised:
        labels.main(list(argv), io.StringIO())
    return str(raised.value.code)


def _counts(lines: list[str]) -> list[str]:
    return [line for line in lines if not _LINK.match(line)]


def _sample_lines(
    *,
    adviser: str = "Codex, gpt-5.6-sol",
    replacing: int = 0,
    filling: int = 0,
    **counts: int,
) -> list[str]:
    value = {
        "decided": 0,
        "cannot": 0,
        "needs": 0,
        "below": 0,
        "same": 0,
        "out_of_date": 0,
        "waiting": 0,
        "failed": 0,
        "no_source": 0,
        "labels": 0,
        "right": 0,
        "needed": 35,
        **counts,
    }
    return [
        f"Description adviser: {adviser}.",
        f"To decide: {replacing + filling}, {replacing} replacing a description "
        f"and {filling} filling an empty one.",
        f"Already decided: {value['decided']}.",
        f"Advice you cannot choose on the work's page: {value['cannot']}.",
        f"Advice the adviser marked as needing you: {value['needs']}.",
        f"Advice below the description floor: {value['below']}.",
        f"Advice the same as the current description: {value['same']}.",
        f"Advice out of date: {value['out_of_date']}.",
        f"Waiting for advice: {value['waiting']}.",
        f"Advice request failed: {value['failed']}.",
        f"No source description to rewrite: {value['no_source']}.",
        f"Labels for this adviser: {value['labels']}, {value['right']} choosing "
        "its description.",
        f"Labels still needed for the bar: {value['needed']}, if each is right.",
    ]


def test_positive_fixture_lists_its_one_link(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    lines = _sample(prepared.copy())
    assert lines == [
        *_sample_lines(replacing=1),
        f"http://localhost:8080/library?lens=all&preview={prepared.case}"
        "&field=description",
    ]


def _link(case: str, origin: str = "http://localhost:8080") -> str:
    return f"{origin}/library?lens=all&preview={case}&field=description"


# Refusals (S3, S8).


def test_missing_file_is_refused(tmp_path: Path) -> None:
    missing = str(tmp_path / "application.sqlite3")
    assert _refusal("sample", "--database", missing) == series_sample._MISSING
    assert _refusal("score", "--database", missing) == series_sample._MISSING


def test_write_ahead_log_outranks_an_unreadable_file(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    database.write_bytes(b"not a database")
    database.with_name("application.sqlite3-wal").write_bytes(b"pending")
    assert _refusal("sample", "--database", str(database)) == relaxation_sample._WAL
    assert _refusal("score", "--database", str(database)) == relaxation_sample._WAL


def test_garbage_file_is_unreadable(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    database.write_bytes(b"not a database")
    database.with_name("application.sqlite3-wal").write_bytes(b"")
    for command in ("sample", "score"):
        assert (
            _refusal(command, "--database", str(database)) == series_sample._UNREADABLE
        )


def test_copy_without_saved_settings_is_refused(tmp_path: Path) -> None:
    prepared = _store(tmp_path, settings=False)
    database = prepared.copy()
    assert _refusal("sample", "--database", str(database)) == labels._NO_SETTINGS
    # score reads labels without settings.
    assert _score(database)["identities"]


# An install whose settings come from config.json (#1898).


def _config(
    tmp_path: Path, review: dict[str, object] | None = None, **choices: bool
) -> Path:
    """A copied config.json; `choices` are its description fields, `review`
    None leaves the review section out."""
    document: dict[str, object] = {
        "schema_version": CONFIG_SCHEMA,
        "provider_recovery_generation": 1,
        "connections": [
            {
                "name": "library",
                "adapter": "audiobookshelf",
                "endpoint": "https://library.invalid",
                "secret_file": "/synthetic/library.secret",
            }
        ],
        "libraries": [
            {
                "name": "books",
                "connection": "library",
                "library_id": "library-a",
                "mount": "books",
                "provider_connections": [],
            }
        ],
        "mounts": [
            {
                "name": "books",
                "container_path": "/media/books",
                "service_path": "/books",
                "read_only": True,
            }
        ],
        "providers": [],
    }
    if review is not None:
        document["review"] = {
            **review,
            **{f"description_{name}": value for name, value in choices.items()},
        }
    path = tmp_path / "copy" / "config.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document))
    return path


_REVIEW: dict[str, object] = {
    "canonical_origin": "https://review.example.invalid",
    "owner_password_hash_file": "/synthetic/owner.hash",
    "tls_certificate_file": "/synthetic/review.crt",
    "tls_private_key_file": "/synthetic/review.key",
}


@pytest.mark.parametrize("review", [_REVIEW, None], ids=["review", "no_review"])
def test_a_file_mode_copy_is_read_with_its_configuration(
    tmp_path: Path, review: dict[str, object] | None
) -> None:
    prepared = _store(tmp_path, settings=False)
    database = prepared.copy()
    config = str(_config(tmp_path, review))
    assert _sample(database, "--config", config) == [
        *_sample_lines(replacing=1),
        _link(prepared.case),
    ]
    score = json.loads(
        _run(
            "score", "--database", str(database), "--config", config, "--format", "json"
        )
    )
    assert _only(score)["not_counted"]["undecided"] == 1


def test_cleanup_choices_in_the_configuration_are_bound(tmp_path: Path) -> None:
    """Advice made under the default choices is out of date under the file's."""
    database = _store(tmp_path, settings=False).copy()
    config = str(_config(tmp_path, _REVIEW, remove_praise=False))
    assert _sample(database, "--config", config) == _sample_lines(out_of_date=1)
    score = json.loads(
        _run(
            "score", "--database", str(database), "--config", config, "--format", "json"
        )
    )
    assert score["identities"] == []
    # Without the file, score assumes the default choices.
    assert _only(_score(database))["not_counted"]["undecided"] == 1


@pytest.mark.parametrize(
    "off", ["remove_marketing", "remove_sales_boasts", "remove_praise"]
)
def test_each_cleanup_choice_in_the_configuration_reaches_its_field(
    tmp_path: Path, off: str
) -> None:
    """Advice made under the file's choices is current under them."""
    prepared = _store(tmp_path)
    prepared.settings(**{off: False})
    second = _admit(prepared, _other_work())
    _advise(prepared, second)
    connection = prepared.repository._connection
    connection.execute("DELETE FROM owner_settings")
    connection.commit()
    database = prepared.copy()
    config = str(_config(tmp_path, _REVIEW, **{off: False}))
    assert _sample(database, "--config", config) == [
        *_sample_lines(replacing=1, out_of_date=1),
        _link(second.review_case_id),
    ]


def test_automatic_on_in_the_configuration_is_refused(tmp_path: Path) -> None:
    database = _store(tmp_path, settings=False).copy()
    config = str(_config(tmp_path, _REVIEW, automatic_application=True))
    assert (
        _refusal("sample", "--database", str(database), "--config", config)
        == labels._AUTOMATIC_ON_IN_FILE
    )


@pytest.mark.parametrize("command", ["sample", "score"])
@pytest.mark.parametrize("present", [True, False], ids=["file", "no_file"])
def test_a_configuration_for_a_copy_with_saved_settings_is_refused(
    tmp_path: Path, command: str, present: bool
) -> None:
    """Saved settings are named first, even before a file that is not there."""
    database = _store(tmp_path).copy()
    config = _config(tmp_path, _REVIEW)
    if not present:
        config.unlink()
    assert (
        _refusal(command, "--database", str(database), "--config", str(config))
        == labels._CONFIG_NOT_NEEDED
    )


def test_a_missing_database_is_named_before_the_configuration(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "copy" / "application.sqlite3"
    config = str(tmp_path / "copy" / "config.json")
    assert (
        _refusal("sample", "--database", str(missing), "--config", config)
        == series_sample._MISSING
    )


@pytest.mark.parametrize("command", ["sample", "score"])
@pytest.mark.parametrize("damage", ["missing", "not_json", "unknown_field"])
def test_an_unreadable_configuration_is_refused(
    tmp_path: Path, command: str, damage: str
) -> None:
    database = _store(tmp_path, settings=False).copy()
    config = _config(tmp_path, _REVIEW)
    if damage == "missing":
        config.unlink()
    elif damage == "not_json":
        config.write_text("{")
    else:
        config.write_text(json.dumps({**json.loads(config.read_text()), "x": 1}))
    assert (
        _refusal(command, "--database", str(database), "--config", str(config))
        == labels._CONFIG_UNREADABLE
    )


def test_advice_from_another_checkout_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _store(tmp_path).copy()
    monkeypatch.setattr(labels, "CHECKOUT_SCHEMA_SHA256", "0" * 64)
    assert _refusal("sample", "--database", str(database)) == relaxation_sample._ENGINE


def test_copy_without_an_adviser_is_refused(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    access_store = prepared.repository.reviewer_access_store()
    document, _ = access_store.read()
    access_store.replace(
        document.model_copy(
            update={"revision": document.revision + 1, "description_rewrite": None}
        ),
        document.revision,
    )
    database = prepared.copy()
    assert _refusal("sample", "--database", str(database)) == labels._NO_ADVISER


@pytest.mark.parametrize(
    "settings,checkout,adviser,expected",
    [
        (False, False, True, labels._NO_SETTINGS),
        (True, False, True, relaxation_sample._ENGINE),
        (True, True, False, labels._NO_ADVISER),
        (True, True, True, labels._AUTOMATIC_ON),
    ],
    ids=["no_settings", "other_checkout", "no_adviser", "automatic_on"],
)
def test_sample_refusals_keep_their_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: bool,
    checkout: bool,
    adviser: bool,
    expected: str,
) -> None:
    """Each later condition holds too, so the first that fails is named."""
    prepared = _store(tmp_path, settings=False)
    if settings:
        prepared.settings(automatic_application=True)
    if not adviser:
        access_store = prepared.repository.reviewer_access_store()
        document, _ = access_store.read()
        access_store.replace(
            document.model_copy(
                update={
                    "revision": document.revision + 1,
                    "description_rewrite": None,
                }
            ),
            document.revision,
        )
    database = prepared.copy()
    if not checkout:
        monkeypatch.setattr(labels, "CHECKOUT_INSTRUCTION_SHA256", "0" * 64)
    assert _refusal("sample", "--database", str(database)) == expected
    # score never refuses on these.
    _score(database)


# sample (S9-S11).


def test_advice_on_a_description_every_source_repeats_is_listed(
    tmp_path: Path,
) -> None:
    """Every source repeats the library's description: the advice is ready
    and differs, and the work's page offers it beside Keep current (#1905)."""
    prepared = _store(tmp_path, library=LIBRARY, source=LIBRARY)
    assert _sample(prepared.copy()) == [
        *_sample_lines(replacing=1),
        _link(prepared.case),
    ]


def test_unchoosable_advice_is_counted_not_listed(tmp_path: Path) -> None:
    """The library refuses writes to the description: the advice is ready
    and differs, but the work's page offers no choice (finding 6)."""
    prepared = _store(tmp_path)
    (connection,) = prepared.repository._connection.execute(
        "SELECT connection_name FROM current_review_case_lineages "
        "WHERE review_case_id=?",
        (prepared.case,),
    ).fetchone()
    prepared.repository.suspend_field_effect_capability(
        scope="destination",
        connection=connection,
        destination="description",
        reason="field_unavailable",
        changed_at=prepared.tick(),
    )
    assert _sample(prepared.copy()) == _sample_lines(cannot=1)


@pytest.mark.parametrize(
    "options,expected",
    [
        ({"disposition": "needs_review"}, {"needs": 1}),
        ({"description": f"<p>{ADVICE}</p>"}, {"below": 1}),
        ({"same_current": True}, {"same": 1}),
        (
            {"output_transform": lambda _response: "not json"},
            {"failed": 1, "adviser": "Codex"},
        ),
    ],
    ids=["needs_review", "below_floor", "same_as_current", "failed"],
)
def test_advice_that_automatic_would_not_write_is_counted(
    tmp_path: Path, options: dict, expected: dict
) -> None:
    database = _store(tmp_path, **options).copy()
    assert _sample(database) == _sample_lines(**expected)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("requested", {"waiting": 1}),
        ("no_row", {"waiting": 1}),
        ("no_source", {"no_source": 1}),
    ],
    ids=["requested", "no_advice_row", "no_source_description"],
)
def test_works_without_advice_are_counted(
    tmp_path: Path, case: str, expected: dict
) -> None:
    prepared = _store(tmp_path)
    prepared.choose()
    if case == "requested":
        prepared.store.request_retry(prepared.case, prepared.tick())
        assert prepared.store.begin_attempt(
            prepared.candidate.request_identity,
            prepared.binding,
            prepared.candidate.engine,
            prepared.candidate.model,
            prepared.tick(),
            lambda: True,
            retry=True,
        )
    else:
        _admit(prepared, _other_work(source=None if case == "no_source" else SOURCE))
    lines = _sample(prepared.copy())
    decided = 0 if case == "requested" else 1
    assert lines == _sample_lines(
        decided=decided, labels=1, right=1, needed=34, **expected
    )


def test_changed_cleanup_choices_leave_the_advice_out_of_date(
    tmp_path: Path,
) -> None:
    prepared = _store(tmp_path)
    prepared.settings(remove_praise=False)
    assert _sample(prepared.copy()) == _sample_lines(out_of_date=1)


def test_chosen_advice_is_already_decided(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    prepared.choose()
    assert _sample(prepared.copy()) == _sample_lines(
        decided=1, labels=1, right=1, needed=34
    )


def test_decision_before_any_advice_is_listed_again(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    prepared.clock = 0
    prepared.decide("keep_current")
    database = prepared.copy()
    assert _sample(database) == [*_sample_lines(replacing=1), _link(prepared.case)]
    assert _score(database)["not_counted"]["decided_without_advice"] == 1


def _two_listed(tmp_path: Path) -> Store:
    prepared = _store(tmp_path)
    _advise(prepared, _admit(prepared, _other_work(library=None)))
    return prepared


def test_limit_defaults_to_the_labels_still_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _two_listed(tmp_path).copy()
    lines = _sample(database, "--origin", "https://ml.example.org")
    links = [line for line in lines if line.startswith("https://ml.example.org/")]
    assert len(links) == 2
    assert links == sorted(links)
    assert _counts(lines)[1] == (
        "To decide: 2, 1 replacing a description and 1 filling an empty one."
    )
    one = _sample(database, "--limit", "1")
    assert [line for line in one if _LINK.match(line)] == [
        _link(links[0].split("preview=")[1].split("&")[0])
    ]
    assert one[-1] == "Showing 1 of 2."
    monkeypatch.setattr(labels, "labels_needed", lambda right, total: 1)
    default = _sample(database)
    assert default[-2:] == [default[-2], "Showing 1 of 2."]
    assert _LINK.match(default[-2])


def test_met_bar_prints_no_links_unless_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _two_listed(tmp_path)
    prepared.choose()
    database = prepared.copy()
    monkeypatch.setattr(labels, "LOWER_LIMIT", 0.2)
    lines = _sample(database)
    assert lines[-2:] == [
        "Labels for this adviser: 1, 1 choosing its description.",
        "Your labels meet the bar. Score a new copy.",
    ]
    asked = _sample(database, "--limit", "5")
    assert len([line for line in asked if _LINK.match(line)]) == 1
    assert asked[-2] == "Your labels meet the bar. Score a new copy."


def test_a_work_whose_page_cannot_be_read_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def __init__(self, _repository: object) -> None:
            pass

        def run(self, _case: str) -> dict:
            raise RuntimeError(SENTINEL)

    database = _store(tmp_path).copy()
    monkeypatch.setattr(labels, "InspectCurrentReviewCase", Broken)
    assert _sample(database) == [
        *_sample_lines(),
        "1 work could not be checked and is left out.",
    ]


def test_output_never_holds_library_text(tmp_path: Path) -> None:
    prepared = _two_listed(tmp_path)
    prepared.decide("keep_current")
    database = prepared.copy()
    _run("sample", "--database", str(database), "--limit", "500")
    _run("score", "--database", str(database))
    _run("score", "--database", str(database), "--format", "json")
    assert SENTINEL.encode() in database.read_bytes()


# score (S4, S5, S7, S12).


def _only(document: dict) -> dict:
    (identity,) = document["identities"]
    return identity


def _wrong(**counts: int) -> dict:
    return {
        "kept_current": 0,
        "chose_source": 0,
        "declined": 0,
        "entered": 0,
        "asked_again": 0,
        **counts,
    }


@pytest.mark.parametrize(
    "library,stratum",
    [(LIBRARY, "replaces"), (None, "fills")],
    ids=["replaces", "fills"],
)
def test_choosing_the_advice_is_right(
    tmp_path: Path, library: str | None, stratum: str
) -> None:
    prepared = _store(tmp_path, library=library)
    prepared.choose()
    group = _only(_score(prepared.copy()))
    assert (group["labels"], group["right"], group["wrong"]) == (1, 1, _wrong())
    assert group["strata"][stratum] == {
        "right": 1,
        "wrong": 0,
        "wilson_lower_limit": round(ledger.wilson_lower_bound(1, 1) or 0.0, 4),
    }
    other = "fills" if stratum == "replaces" else "replaces"
    assert group["strata"][other] == {
        "right": 0,
        "wrong": 0,
        "wilson_lower_limit": None,
    }


@pytest.mark.parametrize(
    "outcome,label",
    [("keep_current", "kept_current"), ("accept", "chose_source")],
    ids=["keep_current", "accept"],
)
def test_other_choices_after_the_advice_are_wrong(
    tmp_path: Path, outcome: str, label: str
) -> None:
    prepared = _store(tmp_path)
    prepared.decide(outcome)
    group = _only(_score(prepared.copy()))
    assert (group["labels"], group["right"]) == (1, 0)
    assert group["wrong"] == _wrong(**{label: 1})
    assert group["strata"]["replaces"]["wrong"] == 1


def test_choosing_retried_advice_is_asked_again(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    prepared.choose(prepared.retry())
    group = _only(_score(prepared.copy()))
    assert (group["right"], group["wrong"]) == (0, _wrong(asked_again=1))


def test_advice_that_exists_only_after_a_retry_is_not_eligible(
    tmp_path: Path,
) -> None:
    """The first answer needed the owner, so Automatic would never have
    written the retried one unasked."""
    prepared = _store(tmp_path, disposition="needs_review")
    prepared.choose(prepared.retry())
    group = _only(_score(prepared.copy()))
    assert (group["labels"], group["not_counted"]["not_eligible"]) == (0, 1)


def _readmit(prepared: Store, report) -> str:
    """Rescan the item to other content and back, so its case returns as a
    new episode."""
    repository = prepared.repository
    target = report.target
    for scanned in (_description_report("Another library description."), report):
        (current,) = repository._connection.execute(
            "SELECT report_id FROM current_metadata_report_lineages "
            "WHERE connection_name=? AND library_name=? AND item_id=?",
            (target.connection, target.library, target.item),
        ).fetchone()
        resolved = repository.metadata_policy_resolution_for_preview(target.library)
        proposal_set = evaluate_report(
            scanned, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(
            scanned,
            lambda: True,
            current if current != scanned.report_id else None,
        )
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
    return str(case_id)


@pytest.mark.parametrize("across_cases", [False, True], ids=["same_case", "successor"])
def test_a_new_episode_keeps_the_latest_decision(
    tmp_path: Path, across_cases: bool
) -> None:
    """Choosing the advice, then keeping the current description, stays wrong
    after the deciding case returns as a new episode."""
    prepared = _store(tmp_path)
    prepared.choose()
    report = prepared_report(prepared)
    case, binding = prepared.case, prepared.binding
    if across_cases:
        successor = _admit(prepared, report, "Ask only again")
        case, binding = successor.review_case_id, _advise(prepared, successor)
    prepared.decide("keep_current", case=case, binding=binding)
    assert _readmit(prepared, report) == case
    assert prepared.repository._connection.execute(
        "SELECT count(*) FROM review_case_episodes WHERE review_case_id=?", (case,)
    ).fetchone() == (1,)
    group = _only(_score(prepared.copy()))
    assert (group["labels"], group["right"]) == (1, 0)
    assert group["wrong"] == _wrong(kept_current=1)


@pytest.mark.parametrize(
    "first,then,right",
    [("choose", "keep_current", 0), ("keep_current", "choose", 1)],
    ids=["choose_then_keep_on_successor", "keep_then_choose_on_successor"],
)
def test_the_latest_decision_across_cases_is_the_label(
    tmp_path: Path, first: str, then: str, right: int
) -> None:
    prepared = _store(tmp_path)
    if first == "choose":
        prepared.choose()
    else:
        prepared.decide(first)
    successor = _admit(prepared, prepared_report(prepared), "Ask only again")
    binding = _advise(prepared, successor)
    if then == "choose":
        prepared.binding = binding
        prepared.choose()
    else:
        prepared.decide(then, case=successor.review_case_id, binding=binding)
    group = _only(_score(prepared.copy()))
    assert (group["labels"], group["right"]) == (1, right)
    if not right:
        assert group["wrong"] == _wrong(kept_current=1)


def test_anything_unreadable_after_opening_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _store(tmp_path).copy()

    def broken(_copy: object) -> None:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(labels, "read_labels", broken)
    for command in ("sample", "score"):
        refusal = _refusal(command, "--database", str(database))
        assert refusal == series_sample._UNREADABLE


@pytest.mark.parametrize(
    "order,right",
    [(("choose", "keep_current"), 0), (("keep_current", "choose"), 1)],
    ids=["choose_then_keep", "keep_then_choose"],
)
def test_the_latest_decision_is_the_label(
    tmp_path: Path, order: tuple[str, str], right: int
) -> None:
    prepared = _store(tmp_path)
    for step in order:
        if step == "choose":
            prepared.choose()
        else:
            prepared.decide(step)
    group = _only(_score(prepared.copy()))
    assert (group["labels"], group["right"]) == (1, right)


def test_a_successor_keeps_its_items_one_label(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    prepared.choose()
    successor = _admit(prepared, prepared_report(prepared), "Ask only again")
    assert successor.review_case_id != prepared.case
    _advise(prepared, successor)
    database = prepared.copy()
    assert _only(_score(database))["labels"] == 1
    assert _sample(database) == _sample_lines(decided=1, labels=1, right=1, needed=34)


def prepared_report(prepared: Store):
    return MetadataReport.from_json(prepared.repository.get(prepared.binding.report_id))


def test_fields_that_are_not_labels_are_counted_apart(tmp_path: Path) -> None:
    """A decision on advice Automatic would not write is not eligible, and
    advice it would not write is not undecided either."""
    prepared = _store(tmp_path, disposition="needs_review")
    prepared.decide("keep_current")
    _advise(prepared, _admit(prepared, _other_work()))
    group = _only(_score(prepared.copy()))
    assert group["labels"] == 0
    assert group["not_counted"] == {
        "not_eligible": 1,
        "decided_by_automatic": 0,
        "undecided": 0,
    }


def test_undecided_counts_current_eligible_advice(tmp_path: Path) -> None:
    group = _only(_score(_store(tmp_path).copy()))
    assert group["labels"] == 0
    assert group["not_counted"] == {
        "not_eligible": 0,
        "decided_by_automatic": 0,
        "undecided": 1,
    }


def test_automatic_decisions_are_not_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qualify_description_advice(monkeypatch)
    from mechanical_librarian.application.description_advice import (
        admit_staged_description,
    )

    prepared = _store(tmp_path)
    prepared.settings(automatic_application=True)
    # Advice prepared under the earlier settings is out of date; prepare it
    # again under the current ones, then let Automatic admit it.
    admitted = SimpleNamespace(
        review_case_id=prepared.case,
        report=prepared_report(prepared),
        proposal_set_id=prepared.binding.proposal_set_id,
    )
    binding = _advise(prepared, admitted)
    action: Any = SimpleNamespace(
        review_case_id=binding.review_case_id, report_id=binding.report_id
    )
    admit_staged_description(prepared.repository, action)
    assert prepared.repository._connection.execute(
        "SELECT count(*) FROM current_description_decisions"
    ).fetchone() == (1,)
    group = _only(_score(prepared.copy()))
    assert group["labels"] == 0
    assert group["not_counted"]["decided_by_automatic"] == 1


def test_withdrawal_is_not_a_label(tmp_path: Path) -> None:
    """A withdrawal on a description cannot be recorded; the mapper still
    counts none."""
    assert labels.outcome_label("withdraw") is None


def test_each_cleanup_combination_is_its_own_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _store(tmp_path)
    prepared.choose()
    prepared.settings(remove_praise=False)
    second = _admit(prepared, _other_work(library=None))
    binding = _advise(prepared, second)
    advice = prepared.store.read(second.review_case_id)
    later = DescriptionAdviceCandidate.model_validate(advice["candidate"])
    decision = prepared.store.admit(
        later.candidate_id, binding, "human", "f" * 64, prepared.tick()
    )
    database = prepared.copy()
    document = _score(database)
    first, other = document["identities"]
    narrower = DescriptionPreferences(remove_praise=False)
    assert first["preferences"] == DescriptionPreferences().model_dump()
    assert other["preferences"] == narrower.model_dump()
    assert other["qualification_manifest_sha256"] == (
        description_qualification_manifest(narrower)
    )
    assert other["qualification_manifest_sha256"] == (
        decision.qualification_manifest_sha256
    )
    assert first["qualification_manifest_sha256"] == (
        description_qualification_manifest(DescriptionPreferences())
    )
    assert [group["labels"] for group in document["identities"]] == [1, 1]
    text = _run("score", "--database", str(database))
    assert "Cleanup removes marketing, sales boasts and praise." in text
    assert "Cleanup removes marketing and sales boasts." in text
    monkeypatch.setattr(labels, "CHECKOUT_INSTRUCTION_SHA256", "0" * 64)
    earlier = _score(database)
    assert [group["this_checkout"] for group in earlier["identities"]] == [
        False,
        False,
    ]
    assert [
        group["qualification_manifest_sha256"] for group in earlier["identities"]
    ] == [None, None]
    assert "with earlier instructions." in _run("score", "--database", str(database))


def test_labels_needed_and_the_wilson_bar() -> None:
    assert labels.labels_needed(0, 0) == 35
    assert ledger.wilson_lower_bound(0, 0) is None
    assert labels.labels_needed(35, 35) == 0
    assert (ledger.wilson_lower_bound(35, 35) or 0.0) >= labels.LOWER_LIMIT
    assert labels.labels_needed(34, 34) == 1
    assert (ledger.wilson_lower_bound(34, 34) or 0.0) < labels.LOWER_LIMIT
    assert labels.labels_needed(0, 1) + 1 == 53
    assert labels.labels_needed(0, 2) + 2 == 69


@pytest.mark.parametrize(
    "kind,label",
    [
        ("adviser", "right"),
        ("keep_current", "kept_current"),
        ("accept", "chose_source"),
        ("reject", "declined"),
        ("owner_entered", "entered"),
        ("withdraw", None),
    ],
    ids=["adviser", "keep_current", "accept", "reject", "owner_entered", "withdraw"],
)
def test_outcome_mapper(kind: str, label: str | None) -> None:
    assert labels.outcome_label(kind) == label


def test_score_json_is_canonical_and_text_is_exact(tmp_path: Path) -> None:
    prepared = _store(tmp_path)
    prepared.choose()
    database = prepared.copy()
    raw = _run("score", "--database", str(database), "--format", "json")
    document = json.loads(raw)
    assert raw == canonical_json(document) + "\n"
    assert document["schema_version"] == labels.SCORE_SCHEMA
    assert document["bar"] == {
        "wilson_lower_limit": 0.9,
        "confidence": 0.95,
        "set_by": "owner",
        "decision": "ADR-0099",
    }
    group = _only(document)
    assert group["engine"] == "codex"
    assert group["model"] == "gpt-5.6-sol"
    assert group["profile_digests"] == [prepared.candidate.profile_digest]
    assert group["this_checkout"] is True
    assert group["meets_bar"] is False
    assert group["labels_needed"] == 34
    lower = ledger.wilson_lower_bound(1, 1)
    assert _run("score", "--database", str(database)).splitlines() == [
        "Description adviser: Codex, gpt-5.6-sol, with this checkout's instructions.",
        "Cleanup removes marketing, sales boasts and praise.",
        "Labels: 1, 1 right and 0 wrong.",
        "Wrong labels: 0 kept the current description, 0 chose a source's "
        "description, 0 declined the change, 0 entered your own, 0 asked for "
        "new advice.",
        "Replacing a description: 1 right and 0 wrong. Filling an empty one: "
        "0 right and 0 wrong.",
        f"Wilson 95% lower limit: {lower:.4f}.",
        "Labels still needed for the bar: 34, if each is right.",
        "Not counted: 0 not eligible, 0 decided by Automatic, 0 undecided.",
        "",
        "Also not counted: 0 withdrawn, 0 decided before any advice.",
    ]


def test_score_without_labels(tmp_path: Path) -> None:
    prepared = _store(tmp_path, disposition="needs_review")
    prepared.clock = 0
    prepared.decide("keep_current")
    database = prepared.copy()
    assert _run("score", "--database", str(database)).splitlines() == [
        "No labels yet. Decide the descriptions sample lists, then score a new copy.",
        "",
        "Also not counted: 0 withdrawn, 1 decided before any advice.",
    ]


def test_a_decision_on_reused_advice_survives_new_cleanup_choices(
    tmp_path: Path,
) -> None:
    """A case that reused another case's advice links to it only through its
    current pointer; preparing it again under new cleanup choices overwrites
    that pointer, and the decision still judges the advice it was made on."""
    prepared = _store(tmp_path)
    other = _admit(prepared, _other_work())
    binding = _advise(prepared, other)
    assert prepared.store.read(other.review_case_id)["candidate"]["candidate_id"] == (
        prepared.candidate.candidate_id
    )
    prepared.decide("keep_current", case=other.review_case_id, binding=binding)
    prepared.settings(remove_praise=False)
    _advise(prepared, other)
    assert prepared.store.read(other.review_case_id)["candidate"]["candidate_id"] != (
        prepared.candidate.candidate_id
    )
    document = _score(prepared.copy())
    assert document["not_counted"]["decided_without_advice"] == 0
    (judged,) = [
        group
        for group in document["identities"]
        if group["preferences"] == DescriptionPreferences().model_dump()
    ]
    assert (judged["labels"], judged["wrong"]) == (1, _wrong(kept_current=1))
