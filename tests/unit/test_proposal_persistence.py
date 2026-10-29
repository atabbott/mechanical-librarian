import json
import sqlite3
import tempfile
from itertools import groupby
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.proposal_sets import (
    GetProposalSet,
    ProposalMissingInputError,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    default_target_fields,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import _claim, _fields, _provider
from tests.support.proposal_evidence import _report as _proposal_report


def _report(title: str, digest: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, digest),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )


def test_placeholder_report_and_nonmutating_proposal_replay_exactly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        fields = {field.destination: field for field in default_target_fields("Title")}
        fields[TargetDestination.AUTHORS] = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        claim = LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed="UNKNOWN",
            canonical="UNKNOWN",
            excerpt="UNKNOWN",
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            state=FieldState.VALID,
            role="author",
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                tuple(fields[destination] for destination in TargetDestination),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(claim,),
            local_reasons=("package_metadata_creator",),
            providers=(),
        )
        repository = open_repository(root / "reports.sqlite")
        assert report.to_json() == repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        assert POLICY_VERSION == proposal.policy_version
        authors = next(
            result
            for result in proposal.results
            if result.destination is TargetDestination.AUTHORS
        )
        assert ProposalOutcome.INELIGIBLE == authors.outcome
        assert () == authors.alternatives
        stored = repository.publish_proposal(proposal)
        assert proposal.to_json() == stored
        assert proposal == ProposalSet.from_json(
            repository.get_proposal(proposal.proposal_set_id)
        )
        assert report.to_json() == repository.get(report.report_id)
        repository.close()


def test_publish_is_idempotent_and_reopens_exact_document() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        repository = open_repository(root / "reports.sqlite")
        report = _report("Title", "b" * 64)
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        expected = proposal_set.to_json()
        assert expected == repository.publish_proposal(proposal_set)
        assert expected == repository.publish_proposal(proposal_set)
        assert expected == repository.get_proposal(proposal_set.proposal_set_id)
        columns = repository._connection.execute(
            "PRAGMA table_info(proposal_sets)"
        ).fetchall()
        assert [
            "proposal_set_id",
            "report_id",
            "policy_version",
            "document",
        ] == [column[1] for column in columns]
        policy = repository._connection.execute(
            "SELECT policy_version FROM proposal_sets"
        ).fetchone()
        assert (POLICY_VERSION,) == policy
        repository.close()


def test_missing_proposal_is_a_typed_absence_and_does_not_publish() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        with pytest.raises(ProposalMissingInputError):
            GetProposalSet(repository).run("proposal_set_" + "0" * 64)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM proposal_sets"
        ).fetchone() == (0,)
        repository.close()


def test_generic_provider_subtitle_persists_as_a_nonactionable_replay() -> None:
    subtitle = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.SUBTITLE,
        "A Novel",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    report = _proposal_report(
        fields=_fields(
            subtitle=TargetField(TargetDestination.SUBTITLE, TargetFieldState.MISSING)
        ),
        providers=(_provider("catalogue", "edition-1", (subtitle,)),),
    )
    proposal = evaluate_report(report)

    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        repository.publish(report, lambda: True)
        assert proposal.to_json() == repository.publish_proposal(proposal)
        first = repository.admit_review_case(proposal)
        assert first == repository.admit_review_case(proposal)
        assert {
            "review_case_id": None,
            "schema_version": "review-admission-v1",
            "status": "no_case",
        } == json.loads(first)
        assert (1, 0) == repository.count_current_review_cases(None)
        assert 0 == repository.count_current_attention_fields(None)
        assert 0 == repository.read_review_attention(None, 10, 0).total
        repository.close()


def test_prior_policy_store_schema_is_rejected_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.close()

        connection = sqlite3.connect(path)
        connection.execute("ALTER TABLE proposal_sets DROP COLUMN policy_version")
        connection.commit()
        connection.close()
        before = path.read_bytes()

        with pytest.raises(MetadataReportError):
            open_repository(path)

        assert before == path.read_bytes()


@pytest.mark.parametrize(
    "prior_policy",
    (
        "fixed-conservative-v20",
        "fixed-conservative-v22",
        "fixed-conservative-v23",
        "fixed-conservative-v31",
    ),
)
@pytest.mark.parametrize("populated", (False, True))
def test_persisted_prior_proposal_state_is_rejected_during_bootstrap(
    prior_policy,
    populated,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        report = _report("Title", "b" * 64)
        if populated:
            repository.publish(report, lambda: True)
            repository.publish_proposal(evaluate_report(report))
        repository.close()

        connection = sqlite3.connect(path)
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=replace(sql, ?, ?) WHERE name='proposal_sets'",
            (POLICY_VERSION, prior_policy),
        )
        connection.commit()
        connection.close()

        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER proposal_sets_no_update")
        connection.execute(
            "UPDATE proposal_sets SET policy_version=?,document=replace(document, ?, ?)",
            (prior_policy, POLICY_VERSION, prior_policy),
        )
        connection.execute(
            "CREATE TRIGGER proposal_sets_no_update BEFORE UPDATE ON proposal_sets "
            "BEGIN SELECT RAISE(ABORT, 'proposal sets are append-only'); END"
        )
        connection.commit()
        connection.execute("PRAGMA writable_schema=OFF")
        connection.close()
        before = path.read_bytes()

        with pytest.raises(MetadataReportError):
            open_repository(path)

        assert before == path.read_bytes()


def test_transaction_rechecks_current_report_before_insert() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        repository = open_repository(root / "reports.sqlite")
        original = _report("Original", "b" * 64)
        repository.publish(original, lambda: True)
        proposal_set = evaluate_report(original)
        successor = _report("Successor", "c" * 64)
        repository.publish(
            successor,
            lambda: True,
            expected_current=original.report_id,
        )
        with pytest.raises(MetadataReportError):
            repository.publish_proposal(proposal_set)
        count = repository._connection.execute(
            "SELECT count(*) FROM proposal_sets"
        ).fetchone()
        assert (0,) == count
        repository.close()


def test_report_and_pointer_recheck_are_the_final_queries_before_insert() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        repository = open_repository(root / "reports.sqlite")
        report = _report("Title", "b" * 64)
        repository.publish(report, lambda: True)
        statements: list[str] = []
        repository._connection.set_trace_callback(statements.append)
        repository.publish_proposal(evaluate_report(report))
        # The browse-fact trigger (#1199) retraces its INSERT's text once per
        # trigger step; consecutive repeats are one statement.
        relevant = tuple(
            statement
            for statement, _ in groupby(
                statement
                for statement in statements
                if statement.startswith(
                    "SELECT report_id,connection_name,library_name,item_id,"
                )
                or statement.startswith(
                    "SELECT report_id FROM current_metadata_reports"
                )
                or statement.startswith("INSERT INTO proposal_sets")
            )
        )
        assert 3 == len(relevant)
        assert relevant[0].startswith("SELECT report_id,")
        assert relevant[1].startswith("SELECT report_id FROM current_metadata_reports")
        assert relevant[2].startswith("INSERT INTO proposal_sets")
        repository.close()


@pytest.mark.parametrize("populated", (False, True))
def test_actual_v32_baseline_table_refuses_before_rows_without_mutation(
    tmp_path, monkeypatch, populated
):
    import hashlib

    capsule = json.loads(
        (
            Path(__file__).parents[1]
            / "support/proposal_predecessors/fixed-conservative-v32.json"
        ).read_text()
    )
    path = tmp_path / "baseline-table.sqlite"
    repository = open_repository(path)
    report = _report("Title", "b" * 64)
    # The capsule's producer wrote metadata-report-v49, which differs from this
    # provider-less current report only in its schema label (#1245), in the
    # description's restore flag v58 added (#1424), in the field-result
    # source v60 added (#1316), in the comparison version v61 moved
    # (#1307), in the library languages v62 added (#1315) and in the companion
    # section v63 added (#1319).
    produced = report.to_data()
    produced.pop("library_languages")
    produced.pop("companion")
    produced["schema_version"] = "metadata-report-v49"
    produced["comparison_version"] = "closed-title-creator-publication-work-identity-v5"
    for field in produced["target"]["fields"]:
        field.pop("restorable", None)
    for result in produced["field_results"]:
        result["sources"] = [
            source
            for source in result["sources"]
            if source["source"] != "prior_admission"
        ]
    produced.pop("report_id")
    produced["report_id"] = report_content_address(produced)
    assert produced["report_id"] == capsule["report_id"]
    assert (
        hashlib.sha256(canonical_json(produced).encode()).hexdigest()
        == capsule["report_sha256"]
    )
    repository.publish(report, lambda: True)
    repository.close()
    with sqlite3.connect(path) as connection:
        # Reconstruct only the actual predecessor table over unchanged scaffolding.
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE name='proposal_sets'",
            (capsule["proposal_sets_ddl"],),
        )
        connection.execute("PRAGMA writable_schema=OFF")
    with sqlite3.connect(path) as connection:
        if populated:
            document = capsule["proposal_document"]
            proposal = json.loads(document)
            connection.execute(
                "INSERT INTO proposal_sets VALUES (?,?,?,?)",
                (
                    "proposal_set_" + capsule["proposal_sha256"],
                    report.report_id,
                    proposal["policy_version"],
                    document,
                ),
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    before = path.read_bytes()

    def reject_row_parse(*args, **kwargs):
        pytest.fail("baseline schema must refuse before proposal row validation")

    monkeypatch.setattr(ProposalSet, "from_json", reject_row_parse)
    with pytest.raises(MetadataReportError):
        open_repository(path)
    assert path.read_bytes() == before
