"""The frozen synthetic ledger fixture (ADR-0108, "Ledger format and migrations").

`generate` builds one store through the product's own writers with fixed
clocks: one work prepared under a qualified Automatic contributor-names
policy, the owner's answers of each outcome, an answer carried to a later case
and then carried `settled` once written, a write verified, a write waiting to
be sent, Resume, owner cover choices, and store-mode owner settings with
synthetic secrets only. It then runs the real `regenerate-derived
--retire-pending` over the store, which retires the waiting write and leaves
the ledger at epoch 1 beside an empty derived partition.

`tests/fixtures/ledger/v<LEDGER_FORMAT_VERSION>.sqlite3` is that store, frozen.
A ledger change after the cutover is a migration from this file (C6), so the
file never changes for its format version; `ledger_dump` is what a later
generator run must still equal. To write it for a new format version:
`uv run python -m tests.support.ledger_fixture`.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
)
from mechanical_librarian.adapters.persistence.schema import LEDGER_FORMAT_VERSION
from mechanical_librarian.adapters.persistence.setup_state import SetupStateStore
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.reviewer_access import (
    REVIEWER_ACCESS_SCHEMA,
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)
from mechanical_librarian.domain.setup_state import (
    SetupStateDocument,
    claim_token_digest,
)
from tests.support.cover_taste import record_owner_cover_decisions
from tests.support.ledger_partition import regenerate
from tests.support.owner_field_record import assert_record_recomputes, resume
from tests.support.prepared_library import (
    PRODUCER,
    Library,
    applier,
    current_proposal,
    planned,
    prepared,
)

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "ledger"
    / f"v{LEDGER_FORMAT_VERSION}.sqlite3"
)
# After every write below, so the run's retirements and revocations follow
# what they close.
REGENERATED_AT = 1_000
# Synthetic setup secrets: never a real verifier or token.
_CLAIM_TOKEN = "synthetic-ledger-fixture-claim-token-0000000000"
_OWNER_VERIFIER = "$argon2id$v=19$m=65536,t=3,p=4$c3ludGhldGlj$synthetic"
_BOOTSTRAP_VERIFIER = "$argon2id$v=19$m=65536,t=3,p=4$Ym9vdHN0cmFw$synthetic"
_CONNECTION_KEY = "synthetic-audiobookshelf-key-0000"
_REVIEWER_KEY = "synthetic-openrouter-key-0000"
_ENTERED = ["Ōwner Named Author"]
# Facts of the build that generated the store rather than of its ledger: the
# derived partition it names, the build that ran the regeneration, the bytes
# of its restore point and how many derived objects it dropped and made.
_BUILD_FACTS = {
    "ledger_meta": ("derived_version", "derived_schema_digest"),
    "derived_regenerations": (
        "derived_schema_digest_before",
        "derived_schema_digest_after",
        "derived_version_after",
        "build_version",
        "backup_sha256",
    ),
}
_BUILD_COUNTS = ("derived_objects_dropped", "derived_objects_recreated")


def _decide(
    library: Library,
    destination: str,
    outcome: str,
    key: str,
    at: int,
    selected: dict[str, object] | None = None,
) -> HumanDecision:
    """Record the owner's answer on the field of the current case through the
    real command."""
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == destination
    )
    if selected is None and outcome == "accept":
        result = proposal["result"]
        assert type(result) is dict
        selected = result["alternatives"][0]["value"]
    decision = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=destination,
        actor="owner",
        outcome=outcome,
        selected_value=selected,
        idempotency_key=key,
        occurred_at=at,
    )
    library.root.record_human_decision(decision)
    return decision


def _reread(library: Library, at: int) -> None:
    """A later scan reads the work with an unrelated field changed, so the
    work has a new case and the question check runs on it."""
    held = library.held
    assert held is not None
    held.write(TargetDestination.PUBLISHER, f"Publisher read at {at}")
    held.revision = at
    library.root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, at)
    library.now[0] = at + 1
    library.drain()


def _write_pending(library: Library, root: Path, at: int, *, send: bool) -> None:
    """Plan what is pending, and with `send` apply every planned plan."""
    scheduler, service, target = applier(library, root)
    library.now[0] = at
    scheduler.schedule()
    if not send:
        return
    for operation in planned(library).values():
        if library.root.get_field_effect(operation.plan.plan_id).state == "planned":
            target.seed(operation.plan)
            service.apply(operation.plan.plan_id)


def _next(state: SetupStateDocument, **changes: object) -> SetupStateDocument:
    return SetupStateDocument.model_validate(
        {**state.model_dump(), "revision": state.revision + 1, **changes}
    )


def _owner_settings(database: Path) -> None:
    """First run on the new store: claimed, connected, the library and its
    folder found, providers left as they are, a reviewer configured, then
    promoted to the owner's settings. Every secret is synthetic."""
    repository = open_repository(database)
    try:
        connection = repository._connection
        store = SetupStateStore(connection)
        state = store.initialize(claim_token_digest(_CLAIM_TOKEN))
        state = store.claim_owner(
            expected_revision=state.revision,
            owner_verifier=_OWNER_VERIFIER,
            claim_token=_CLAIM_TOKEN,
        )
        state = store.write(
            _next(
                state,
                completed_steps=["claim", "connect"],
                connection={
                    "name": "primary",
                    "endpoint": "https://abs.example.invalid",
                },
            ),
            state.revision,
            _BOOTSTRAP_VERIFIER,
            {"connection.primary.key": _CONNECTION_KEY},
        )
        library = {
            "name": "ebooks",
            "connection": "primary",
            "library_id": "lib-1",
            "media_kind": "ebook",
            "mount": "ebooks",
        }
        for steps, changes in (
            (["libraries"], {"libraries": [library]}),
            (["mounts"], {"mounts": [{"library": "ebooks", "state": "matched"}]}),
            (["providers"], {}),
        ):
            state = store.write(
                _next(
                    state, completed_steps=[*state.completed_steps, *steps], **changes
                ),
                state.revision,
            )
        reviewer, _ = ReviewerAccessStore(connection).replace(
            ReviewerAccessDocument(
                schema_version=REVIEWER_ACCESS_SCHEMA,
                revision=2,
                cover_observation=OpenRouterTaskSelection(
                    engine="openrouter", model="provider/model"
                ),
            ),
            1,
            openrouter_key=_REVIEWER_KEY,
        )
        state = store.transition_reviewer(
            _next(
                state,
                completed_steps=[*state.completed_steps, "reviewer"],
                reviewer_setup="configured",
            ),
            state.revision,
            reviewer.revision,
            "configured",
        )
        store.promote(_BOOTSTRAP_VERIFIER, _OWNER_VERIFIER, state.revision)
    finally:
        repository.close()


def generate(root: Path) -> Path:
    """Build the fixture's store under ``root``, which must be a private
    folder, and return its database."""
    _owner_settings(root / "application.sqlite3")
    with prepared(root, automatic=True, library_model="publication") as library:
        entered: dict[str, object] = {"kind": "list", "value": _ENTERED}
        _decide(
            library, "authors", "owner_entered", "ledger-fixture-authors", 11, entered
        )
        # Carried to the next case, then written once, as the carried row.
        _reread(library, 20)
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("authors",), 21
        )
        _write_pending(library, root / "writes", 22, send=True)
        # The library holds the value written; the next case carries the
        # answer `settled`.
        assert library.held is not None
        library.held.write(TargetDestination.AUTHORS, tuple(_ENTERED))
        _reread(library, 30)
        _decide(library, "language", "reject", "ledger-fixture-language-reject", 31)
        _decide(library, "language", "keep_current", "ledger-fixture-language-keep", 32)
        # Resume over the settled answer, completed by its scan.
        assert resume(library.root, library.case_id(), "authors", 33)
        # Writes to narrators are suspended on this connection.
        library.root.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="narrators",
            reason="field_unavailable",
            changed_at=33,
        )
        # A write waiting to be sent, which the run retires.
        _decide(library, "title", "accept", "ledger-fixture-title-accept", 34)
        library.root.grant_field_effect_consents("primary", ("ebooks",), ("title",), 35)
        _write_pending(library, root / "waiting", 36, send=False)
        (root / "covers").mkdir(mode=0o700)
        record_owner_cover_decisions(
            root / "covers",
            library.root,
            2,
            start=10,
            propose=lambda report: current_proposal(library.root, report),
        )
        assert_record_recomputes(library.root._connection)
        database = library.database
    assert database is not None
    regenerate(database, retire_pending=True, at=REGENERATED_AT)
    # The owner dismisses the run's upgrade notice.
    repository = open_repository(database)
    try:
        notices = repository.regeneration_notice_store()
        assert notices.dismiss_regeneration_notice(1, REGENERATED_AT + 1)
    finally:
        repository.close()
    return database


def ledger_dump(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every ledger table's rows in stored order (rowid, or the key of a
    table without one), the generating build's own facts set aside."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        dump: dict[str, list[tuple[object, ...]]] = {}
        for table in sorted(schema.LEDGER_TABLES):
            info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = [
                f'"{row[1]}"'
                for row in info
                if row[1] not in _BUILD_FACTS.get(table, ())
            ]
            (sql,) = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            keys = sorted((row for row in info if row[5]), key=lambda row: row[5])
            order = (
                ",".join(f'"{row[1]}"' for row in keys)
                if "WITHOUT ROWID" in sql.upper()
                else "rowid"
            )
            dump[table] = [
                _without_build_counts(table, columns, row)
                for row in connection.execute(
                    f'SELECT {",".join(columns)} FROM "{table}" ORDER BY {order}'
                )
            ]
        return dump
    finally:
        connection.close()


def _without_build_counts(
    table: str, columns: list[str], row: tuple[object, ...]
) -> tuple[object, ...]:
    if table != "derived_regenerations":
        return row
    values = dict(zip(columns, row, strict=True))
    counts = json.loads(str(values['"counts_document"']))
    for key in _BUILD_COUNTS:
        del counts[key]
    values['"counts_document"'] = counts
    return tuple(values.values())


def write_fixture() -> None:
    """Generate the store and freeze it as this format version's fixture."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "store"
        root.mkdir(mode=0o700)
        database = generate(root)
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(database, FIXTURE)


if __name__ == "__main__":
    write_fixture()
