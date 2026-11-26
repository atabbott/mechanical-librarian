"""The frozen synthetic ledger fixture (ADR-0108, "Ledger format and migrations").

`generate` builds one store through the product's own writers with fixed
clocks (and a fixed taste build id), so the ledger holds a row of every kind
the shipped build can write:

- store-mode owner settings from a first run, with synthetic secrets only;
- one work prepared under a qualified Automatic contributor-names policy;
- the owner's answers of each outcome, one carried to a later case and
  carried `settled` once written, through a write a targeted read confirms;
- Resume, a suspended destination, Automatic's write and the no-change row
  of its next decision, with the value it kept;
- owner cover choices, one written (its pointer and cleanup), and the taste
  profile built from them;
- an identifiers file import with its link, and an authored description;
- a write waiting to be sent.

It then runs the real `regenerate-derived --retire-pending` over the store,
which retires the waiting write and the unwritten cover choices' pointers and
leaves the ledger at epoch 1 beside an empty derived partition.

`tests/fixtures/ledger/v<LEDGER_FORMAT_VERSION>.sqlite3` is that store, frozen.
A ledger change after the cutover is a migration from this file (C6), so the
file never changes for its format version. A later generator run writes other
ledger ids wherever derived content moved (a decision's id names its case), so
what it must still equal is `ledger_projection`: each ledger table's row count
and each document's schema label and kind, which no derived-only change moves.
To write it for a new format version:
`uv run python -m tests.support.ledger_fixture`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from mechanical_librarian.adapters.persistence import cover_taste as cover_taste_store
from mechanical_librarian.adapters.persistence import (
    description_advice as description_advice_store,
)
from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
)
from mechanical_librarian.adapters.persistence.schema import LEDGER_FORMAT_VERSION
from mechanical_librarian.adapters.persistence.setup_state import SetupStateStore
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    canonical_json,
)
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_contract import (
    AdmittedTasteBuildRequest,
    TasteBuildReply,
)
from mechanical_librarian.application.description_advice import DescriptionAdviceService
from mechanical_librarian.application.identifier_import import preview_digest
from mechanical_librarian.application.reviewer_access import (
    OpenRouterFactory,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.cover_taste import TASTE_FIRST_BUILD_DECISIONS
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceBinding,
    DescriptionAdviceSettings,
    description_digest,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.identifier_file import classify_identifier_file
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.owner_link import OwnerLinkScope, OwnerLinkTarget
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
from tests.support.description_advice import _preserved_description_report
from tests.support.ledger_documents import DOCUMENTS
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
from tests.unit.test_reviewer_access_application import _Auth

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "ledger"
    / f"v{LEDGER_FORMAT_VERSION}.sqlite3"
)
# The clock of each later step, after the cover choices (200 onwards); the
# run comes after every write, so its retirements and revocations follow what
# they close.
TASTE_BUILT_AT = 500
IMPORTED_AT = 600
ADVISED_AT = 700
LINKED_AT = 800
REGENERATED_AT = 1_000
# A taste build's and an advice attempt's ids are random; the fixture's are
# fixed, as its clocks are.
_TASTE_BUILD_ID = uuid.UUID(int=1)
_ATTEMPT_ID = uuid.UUID(int=2)
_AUTHORED = "Ōwner-chosen synthetic description."
# One more ebook of the library, which only the identifiers file names.
_CATALOGUE_FINGERPRINT = "f" * 64
_LINKED_ISBN = "9780306406157"
_LINKED_ENTRY = CatalogueEntry(
    "primary",
    "ebooks",
    "lib-1",
    "item-90",
    1,
    "Linked Title",
    ("Linked Author",),
    True,
    False,
    "singular-supported",
    "9" * 64,
    "book.epub",
    "epub",
    "ebooks",
    item_path="Shelf/Linked Title",
)
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


def _write_pending(
    library: Library,
    root: Path,
    at: int,
    *,
    send: bool,
    ambiguous: bool = False,
    work: str = "item-1",
) -> object:
    """Plan what is pending, and with `send` apply every planned plan for
    ``work`` and return the last value written. With `ambiguous` the
    service's answer leaves each write unknown, so a targeted read settles
    it."""
    scheduler, service, target = applier(library, root)
    library.now[0] = at
    scheduler.schedule()
    written: object = None
    if not send:
        return written
    for operation in planned(library).values():
        if (
            operation.plan.work_id == work
            and library.root.get_field_effect(operation.plan.plan_id).state == "planned"
        ):
            target.seed(operation.plan)
            if ambiguous:
                target.ambiguous_next = "desired"
            assert service.apply(operation.plan.plan_id).state == "verified"
            written = operation.plan.desired_value
    return written


class _TasteBuilder:
    """OpenRouter for the taste build: it reports a vision model and answers
    one fixed profile."""

    def __init__(self, _key: str, model: str) -> None:
        self.model = model

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
        self, _profile: bytes, _request: AdmittedTasteBuildRequest
    ) -> TasteBuildReply:
        sections = {"drawn_to": ["Calm colour."], "avoid": ["Busy layouts."]}
        return TasteBuildReply(
            {"sections": {"keep_or_switch": [], "trade_offs": [], **sections}}, True
        )

    def close(self) -> None:
        return None


def _unused(*_args: object) -> object:
    raise AssertionError("the taste build uses OpenRouter only")


def _build_taste_profile(
    repository: SQLiteMetadataReportRepository, covers: Path
) -> None:
    """Select OpenRouter for the taste build and run it through the real
    service over the owner's cover choices."""
    unused = cast(Any, _unused)
    access = ReviewerAccessOperations(
        repository.reviewer_access_store(),
        codex_auth=cast(Any, SimpleNamespace()),
        codex_host=unused,
        # This container provisions no contained reviewer.
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
            False, "profile_not_provisioned"
        ),
        codex_binding=unused,
        openrouter_factory=cast(OpenRouterFactory, _TasteBuilder),
    )
    access.test_taste_model(_REVIEWER_KEY, "example/vision")
    access.configure_openrouter(
        expected_revision=access.read().revision,
        api_key=_REVIEWER_KEY,
        model="example/vision",
        task="cover_taste_build",
    )
    service = CoverTasteBuildService(
        repository.cover_taste_store,
        access,
        CoverArtifactStore(covers / "artifacts", create=False).read_preview_digest,
        daily_limit=8,
        clock=lambda: TASTE_BUILT_AT,
    )
    with patch.object(cover_taste_store.uuid, "uuid4", lambda: _TASTE_BUILD_ID):
        assert service.wake(TASTE_BUILT_AT)


def _import_identifiers(repository: SQLiteMetadataReportRepository, path: Path) -> None:
    """Import a one-row identifiers file over a catalogue that lists one
    more ebook: the import, its event and the file link it records."""
    catalogue = open_catalogue(path, create=True)
    try:
        catalogue.acquire_refresh()
        catalogue.publish(_CATALOGUE_FINGERPRINT, (_LINKED_ENTRY,))
    finally:
        catalogue.close()
    repository.bind_library_catalogue(path, _CATALOGUE_FINGERPRINT)
    read = repository.identifier_candidates("primary", "ebooks")
    rows = classify_identifier_file(
        f"item\tidentifier\n{_LINKED_ENTRY.item_id}\t{_LINKED_ISBN}\n".encode(),
        read.candidates,
        read.linked,
    )
    record, replayed = repository.import_identifiers(
        "primary",
        "ebooks",
        preview_digest=preview_digest("ledger-fixture-preview"),
        catalogue_digest=read.digest,
        rows=rows.rows,
        counts=rows.counts,
        links=rows.will_link,
        actor="owner",
        occurred_at=IMPORTED_AT,
        request_id="ledger-fixture-import-request",
        request_sha256=hashlib.sha256(b"ledger-fixture-import").hexdigest(),
    )
    assert record.linked == 1 and not replayed


_ADVISER = ("gpt-5.6-sol", b"synthetic-contained-profile")


def _advise(
    _profile: bytes, instruction: bytes, inputs: bytes, schema: bytes, _images: object
) -> str:
    """The contained adviser: one ready description for the request."""
    request = ContainedReviewRequest(
        instruction.decode(), inputs.decode(), schema.decode()
    )
    return canonical_json(
        {
            "request_sha256": request.digest,
            "result": {"description": _AUTHORED, "disposition": "ready"},
        }
    )


def _author_description(library: Library) -> None:
    """Prepare description advice for one more work, whose sources hold a
    description, through the real service on a selected contained adviser,
    and record the owner's choice of it through the real command."""
    repository = library.root
    unused = cast(Any, _unused)
    access = ReviewerAccessOperations(
        repository.reviewer_access_store(),
        codex_auth=_Auth("usable"),
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
            True, None, model=_ADVISER[0]
        ),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=lambda: _ADVISER,
        codex_binding=unused,
        codex_description_binding=lambda: (*_ADVISER, _advise),
        openrouter_factory=unused,
    )
    view = access.select_codex(
        expected_revision=access.read().revision, task="description_rewrite"
    )
    settings = DescriptionAdviceSettings(automatic_application=False)
    store = repository.description_advice_store()
    store.bind_settings(settings)
    store.bind_identity(
        lambda engine, _model: (
            (_ADVISER[0], hashlib.sha256(_ADVISER[1]).hexdigest())
            if engine == "codex"
            else None
        )
    )
    base = _preserved_description_report()
    report = MetadataReport.create(
        target=replace(base.target, item="item-70"),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        cover_sources=base.cover_sources,
    )
    repository.publish(report, lambda: True)
    proposal = current_proposal(repository, report)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    binding = DescriptionAdviceBinding(
        review_case_id=case_id,
        report_id=report.report_id,
        proposal_set_id=proposal.proposal_set_id,
        target_fingerprint=report.target.fingerprint,
        policy_generation=library.generation(),
        access_revision=view.revision,
        preferences_digest=description_digest(settings.model_dump()),
    )
    with patch.object(description_advice_store.uuid, "uuid4", lambda: _ATTEMPT_ID):
        candidate = DescriptionAdviceService(
            store, access, store.current_settings, lambda: ADVISED_AT
        ).prepare(report, binding, lambda: store.binding_is_current(binding))
    assert candidate is not None
    repository.record_authored_description_decision(
        candidate.candidate_id,
        binding,
        description_digest({"ledger-fixture": "authored-description"}),
        ADVISED_AT + 1,
    )


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
        # Carried to the next case, then written once, as the carried row,
        # through an answer that leaves the write's outcome unknown until a
        # targeted read confirms it.
        _reread(library, 20)
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("authors",), 21
        )
        _write_pending(library, root / "writes", 22, send=True, ambiguous=True)
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
        # Back under Automatic, its authors write lands. The owner presses
        # Automatic's card for the written value: an answer with nothing to
        # write, whose no-change row keeps the work's authors.
        written = _write_pending(library, root / "automatic", 34, send=True)
        library.held.write(TargetDestination.AUTHORS, tuple(cast(list[str], written)))
        _decide(library, "authors", "accept", "ledger-fixture-authors-kept", 35)
        # Resumed, the next read's Automatic decision of the same value needs
        # no write either: Automatic's no-change row, with the value it kept.
        assert resume(library.root, library.case_id(), "authors", 36)
        _reread(library, 37)
        _write_pending(library, root / "automatic", 39, send=False)
        # Owner cover choices: the first is written, so its pointer and the
        # write's cleanup stay; the run retires the others' pointers and
        # their planned writes.
        (root / "covers").mkdir(mode=0o700)
        record_owner_cover_decisions(
            root / "covers",
            library.root,
            TASTE_FIRST_BUILD_DECISIONS,
            start=10,
            propose=lambda report: current_proposal(library.root, report),
        )
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("cover",), 240
        )
        _write_pending(library, root / "cover", 241, send=True, work="item-10")
        # Those choices are enough for a taste profile: one build, its
        # version and the day's request count.
        _build_taste_profile(library.root, root / "covers")
        # An identifiers file links one more work of the library.
        _import_identifiers(library.root, root / "catalogue.sqlite3")
        # The owner uses the adviser's description, and it is written.
        _author_description(library)
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("description",), ADVISED_AT + 2
        )
        _write_pending(
            library, root / "description", ADVISED_AT + 3, send=True, work="item-70"
        )
        # Find this book links that work to a record the owner chose.
        library.root.choose_owner_link(
            "primary",
            "ebooks",
            "item-70",
            medium="ebook",
            expected_current=None,
            target=OwnerLinkTarget(
                OwnerLinkScope.EDITION,
                "google_books",
                _LINKED_ISBN,
                "Real Tigers",
                ("Mick Herron",),
            ),
            actor="owner",
            occurred_at=LINKED_AT,
            request_id="ledger-fixture-link-request",
            request_sha256=hashlib.sha256(b"ledger-fixture-link").hexdigest(),
        )
        # A write waiting to be sent, which the run retires.
        _decide(library, "title", "accept", "ledger-fixture-title-accept", 900)
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("title",), 901
        )
        _write_pending(library, root / "waiting", 902, send=False)
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


def ledger_projection(path: Path) -> dict[str, object]:
    """What the ledger at ``path`` holds, apart from its content: each ledger
    table's row count, and for each registered document column how many
    documents carry each JSON type, `schema_version` and `kind`."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in sorted(schema.LEDGER_TABLES)
        }
        documents: dict[str, list[tuple[object, ...]]] = {}
        for table, column in sorted(DOCUMENTS):
            shapes: Counter[tuple[str, object, object]] = Counter()
            for (stored,) in connection.execute(
                f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
            ):
                value = json.loads(stored)
                shapes[
                    (
                        type(value).__name__,
                        value.get("schema_version"),
                        value.get("kind"),
                    )
                    if type(value) is dict
                    else (type(value).__name__, None, None)
                ] += 1
            documents[f"{table}.{column}"] = sorted(
                (*shape, count) for shape, count in shapes.items()
            )
        return {"rows": rows, "documents": documents}
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
        FIXTURE.unlink(missing_ok=True)
        # Packed, so the frozen file holds no free pages.
        connection = sqlite3.connect(database)
        try:
            connection.execute("VACUUM INTO ?", (str(FIXTURE),))
        finally:
            connection.close()


if __name__ == "__main__":
    write_fixture()
