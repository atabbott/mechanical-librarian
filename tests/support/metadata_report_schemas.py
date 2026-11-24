"""Shared metadata-report persistence fixtures.

These build the legacy and prior on-disk schemas, the synthetic
report published against them, and the hot journal a rejected
database must be preserved beside.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import mechanical_librarian.adapters.persistence.review_sql as review_sql
import mechanical_librarian.adapters.persistence.schema as persistence_schema
from mechanical_librarian.adapters.persistence.review_sql import (
    _episode_decision_sql,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverFinalHost,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import (
    COMPARISON_VERSION,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderLaneDispatch,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    _provider_lane_from_data,
    canonical_json,
    default_target_fields,
    lane_dispatch_outcome,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    admit_provider_work_identity,
    work_query_ladder,
)
from tests.support.report_schema_version import CURRENT_REPORT_SCHEMA


def synthetic_report(title: str = "Current title") -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )


def synthetic_latin_work_report() -> MetadataReport:
    """One qualified report whose dispatch and admission lineages differ."""
    local_title = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    local_author = "Jos\N{LATIN SMALL LETTER E WITH ACUTE}"
    provider_title = "Cafe"
    provider_author = "Jose"
    candidate = ProviderCandidate(
        "edition-1",
        "work-1",
        (
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.TITLE,
                value=provider_title,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.CREATOR,
                value=provider_author,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=1,
            ),
        ),
    )
    admission = admit_provider_work_identity(
        local_title=local_title,
        local_authors=(local_author,),
        provider_title=provider_title,
        provider_authors=(provider_author,),
        provider_work_identity="work-1",
    )
    assert admission.transformation is not None
    fields = tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            (local_author,),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(local_title)
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("target_metadata",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (candidate,),
                ProviderQueryKind.WORK,
                dispatch_lineage=WorkDispatchLineage.from_query(
                    work_query_ladder(local_title, (local_author,))[-1]
                ),
                transformation_lineage=admission.transformation,
            ),
        ),
    )


def synthetic_reordered_work_report() -> MetadataReport:
    """One WORK replay whose complete closed author multiset is reordered."""
    local_title = "Plain Work"
    local_authors = ("Ada Example", "Bea Example")
    provider_authors = tuple(reversed(local_authors))
    candidate = ProviderCandidate(
        "edition-1",
        "work-1",
        (
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.TITLE,
                value=local_title,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            *(
                ProviderClaim.create(
                    provider="catalogue",
                    candidate="edition-1",
                    kind=ClaimKind.CREATOR,
                    value=author,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    role="author",
                    raw_order=index,
                )
                for index, author in enumerate(provider_authors, start=1)
            ),
        ),
    )
    fields = tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, local_authors)
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(local_title)
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("target_metadata",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (candidate,),
                ProviderQueryKind.WORK,
                dispatch_lineage=WorkDispatchLineage.from_query(
                    work_query_ladder(local_title, local_authors)[0]
                ),
            ),
        ),
    )


def forged_latin_work_report_json() -> tuple[str, str]:
    """A self-consistent report envelope missing required Latin evidence."""
    data = synthetic_latin_work_report().to_data()
    provider = cast(dict[str, object], cast(list[object], data["providers"])[0])
    lineage = cast(dict[str, object], provider["transformation_lineage"])
    applications = cast(list[object], lineage["applications"])
    applications.pop()
    identity_data = dict(data)
    identity_data.pop("report_id")
    report_id = (
        "report_" + hashlib.sha256(canonical_json(identity_data).encode()).hexdigest()
    )
    data["report_id"] = report_id
    return report_id, canonical_json(data)


def retained_report_as_current(document: str) -> MetadataReport:
    """Read one retained v49 report as an ephemeral current-schema report.

    The retained bytes and their digest pins stay frozen, and current code
    refuses them. For a test whose downstream semantics are current ones this
    relabels an in-memory copy, gives each lane the empty exhausted-dispatch
    record v50 added, the empty recording-lookup-hint record v53 added, and
    any audio section the empty discovery-title record v51 added (the only
    ones a v49 producer could state), and readdresses it. v52 changed what an
    Audible overflow or empty rung means, v54 what an incumbent description
    admits and v55 what an audio description, language or ASIN claim means;
    none added a field. v56 added each lane's dispatch record: null on an
    ebook lane, and on an audiobook lane the outcome its status and ladder
    allow, never starved, with no ladder depth or near-miss, since a v49
    producer kept no cause or depth. v57 changed which audiobook products a
    band-corroborated lane admits and added no field. v58 changed what an
    Audiobookshelf incumbent description admits, the visible text rather than
    the stored markup, and added each description's `restorable` flag: false
    here, so no relabelled copy can authorise a restore it was never read for.
    v60 added the `prior_admission` field-result source: an earlier producer
    carried no admission, so it reads `absent` for an ASIN and `unsupported`
    for every other field, as `derive_field_results` states it. v61 added each
    provider's `refused_products`: empty here, since an earlier producer cut
    every refused product to its identifier and kept nothing else of it, and
    moved comparison to `closed-title-creator-publication-work-identity-v6`,
    whose only change, a lone apostrophe between letters, no relabelled
    fixture states. v62 added the report's `library_languages`: none here,
    since an earlier producer read no library language. Nothing is migrated:
    the copy is never written back. v63 added the `companion` ebook section,
    null here since an earlier producer prepared no item beside an EPUB as an
    audiobook. v64 added each provider's `audio_editions`: empty here, since
    an earlier producer fetched no Hardcover audio edition. v65 added the
    `adapter_fault` diagnostic, which an earlier producer never wrote. v66
    added the EPUB `local.cover_recovery`: null here, since an earlier producer
    tried no cover fallback. v67 added the `not_needed` provider status and the
    `region_unavailable` diagnostic, which an earlier producer never wrote.
    v68 added Hardcover's audiobook work first-publication claim, which an
    earlier producer never stated and a v49 report holds no Hardcover work for.
    v69 added the audiobook companion EPUB's ISBN lane and discovery titles,
    which a v49 report, holding no companion, never planned. v70 added each
    lane candidate's `admission_basis`: null here, since an earlier producer
    said nothing of how a lane kept a recording. v71 added each cover source's
    closed `final_host`: an earlier producer followed no Open Library redirect,
    so a retrieved Open Library cover there was served by
    `covers.openlibrary.org` itself, and every other source states none. v72
    made a Hardcover WORK lane's candidates books and planned an ebook lane
    over its EPUB creators, neither of which a v49 report holds. v73
    added each cover artifact's same-art `measures`: null here, since an
    earlier producer measured no preview. v74 added no key: it plans iTunes' one
    cover search for an audiobook (#1779), which a v49 report, being an ebook's,
    never needed.
    v75 gave an audiobook's sidecar manifest entries their classification,
    and v76 their byte-order-mark record (#1796), and v77 the
    `initials_spelling` refusal (#1780); a v49 report holds no audio
    section. v78 added no key: it widens the closed cover designation and
    truncation sets and their bounds for Hardcover's edition covers (#1778).
    v79 reports each cover artifact's width and height as oriented and adds
    the `retained` cover source, neither of which a v49 report, holding no
    automatic switch, could carry (#1363). v80 records the digest of
    Audiobookshelf cover bytes the decoder refused: null here, since a v49
    report's sources hold none (#1853). v81 added each provider's
    `identity_refused_candidates` and `identity_refused_truncated` and each
    lane's `identity_refused_new`: empty and zero here, since an earlier
    producer kept nothing of a refused WORK answer (#1355). v82 added the
    report's `owner_link`: null here, since a v49 item had no owner link
    (#1477). v83 added each description's `formatted` copy: null here, since
    a v49 report kept none (#1995).
    """
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(document)
    data = json.loads(document)
    assert data["schema_version"] == "metadata-report-v49"
    data["schema_version"] = CURRENT_REPORT_SCHEMA
    data["comparison_version"] = COMPARISON_VERSION
    data["library_languages"] = []
    data["companion"] = None
    data["owner_link"] = None
    data["local"]["cover_recovery"] = None
    for cover in data["covers"]:
        cover["final_host"] = (
            CoverFinalHost.COVERS_OPEN_LIBRARY.value
            if cover["kind"] == CoverSourceKind.OPEN_LIBRARY.value
            and cover["state"] == CoverSourceState.RETRIEVED.value
            else None
        )
        cover["original_digest"] = None
        if cover["artifact"] is not None:
            cover["artifact"]["measures"] = None
    for provider in data["providers"]:
        provider["refused_products"] = []
        provider["audio_editions"] = []
        provider["identity_refused_candidates"] = []
        provider["identity_refused_truncated"] = 0
        for lane in provider["lanes"]:
            for member in lane["candidates"]:
                member["admission_basis"] = None
            lane["exhausted_dispatch"] = []
            lane["identity_refused_new"] = []
            lane["recording_lookup_hints"] = []
            lane["dispatch"] = None
            if data.get("audio") is not None:
                outcome = lane_dispatch_outcome(
                    _provider_lane_from_data(lane), starved=False
                )
                assert outcome is not None
                lane["dispatch"] = ProviderLaneDispatch(outcome).to_data()
    for field in data["target"]["fields"]:
        if field["destination"] == "description":
            field["restorable"] = False
            field["formatted"] = None

    # Every provider description claim, in any candidate list a provider
    # keeps (live, refused, recording lookup), gains the null copy.
    def described(value: object) -> None:
        if isinstance(value, dict):
            claims = value.get("claims")
            if isinstance(claims, list):
                for claim in claims:
                    if isinstance(claim, dict) and claim.get("kind") == "description":
                        claim["formatted"] = None
            for item in value.values():
                described(item)
        elif isinstance(value, list):
            for item in value:
                described(item)

    described(data["providers"])
    if data.get("audio") is not None:
        data["audio"]["discovery_titles"] = []
    for result in data["field_results"]:
        if result["sources"]:
            result["sources"].append(
                {
                    "source": "prior_admission",
                    "state": "absent" if result["kind"] == "asin" else "unsupported",
                }
            )
    data.pop("report_id")
    data["report_id"] = report_content_address(data)
    return MetadataReport.from_json(canonical_json(data))


# Every producer before #1310 queued a choice its policy settles and a
# single-source choice, so its review-case field-fact trigger lacks both clauses.
PRE_1310_REVIEW_CASE_FIELD_FACTS_INSERT_SQL = (
    persistence_schema._REVIEW_CASE_FIELD_FACTS_INSERT_SQL.replace(
        "AND NOT "
        + persistence_schema._policy_settles_choice_sql("NEW.proposal_set_id")
        + " ",
        "",
    ).replace("AND NOT " + persistence_schema._SINGLE_SOURCE_CHOICE_SQL + " ", "")
)
assert (
    persistence_schema._SINGLE_SOURCE_CHOICE_SQL
    in persistence_schema._REVIEW_CASE_FIELD_FACTS_INSERT_SQL
)
assert (
    persistence_schema._SINGLE_SOURCE_CHOICE_SQL
    not in PRE_1310_REVIEW_CASE_FIELD_FACTS_INSERT_SQL
)
assert (
    persistence_schema._policy_settles_choice_sql("NEW.proposal_set_id")
    not in PRE_1310_REVIEW_CASE_FIELD_FACTS_INSERT_SQL
)


# Every producer before #1253 counted an immaterial work alternative as an
# unresolved choice, and before #1310 a verified current cover's choice too, so
# its own review-case field-fact trigger lacks the clause.
PRIOR_REVIEW_CASE_FIELD_FACTS_INSERT_SQL = (
    PRE_1310_REVIEW_CASE_FIELD_FACTS_INSERT_SQL.replace(
        "AND NOT EXISTS (SELECT 1 FROM json_each(result.value,'$.reasons') reason "
        "WHERE reason.value='work_alternative_immaterial' OR ("
        "json_extract(result.value,'$.destination')='cover' "
        "AND reason.value='current_cover_verified')) ",
        "",
    )
)
assert (
    PRIOR_REVIEW_CASE_FIELD_FACTS_INSERT_SQL
    != persistence_schema._REVIEW_CASE_FIELD_FACTS_INSERT_SQL
)


# Every producer before #2013 recorded no unread item as a failed work, and
# kept no reason a library's discovery stopped.
WITHOUT_UNREAD_REVIEW_WORK_SQL = persistence_schema._REVIEW_WORK_SQL.replace(
    "'item_no_media','item_unreadable','item_oversized',", ""
)
assert WITHOUT_UNREAD_REVIEW_WORK_SQL.count("item_no_media") == 0
PRIOR_REVIEW_SCANS_SQL = (
    persistence_schema._REVIEW_SCANS_SQL[
        : persistence_schema._REVIEW_SCANS_SQL.index(", discovery_failure ")
    ]
    + ")"
)
assert PRIOR_REVIEW_SCANS_SQL.endswith("rescan_requested IN (0,1)))")


# Every producer before #1451 kept no re-search mark on a preparation work,
# and every one before #1363 no refresh request.
PRIOR_REVIEW_WORK_SQL = WITHOUT_UNREAD_REVIEW_WORK_SQL.replace(
    "research_not_before INTEGER CHECK(research_not_before IS NULL OR "
    "research_not_before>=0), ",
    "",
).replace(
    "refresh_requested INTEGER NOT NULL DEFAULT 0 CHECK(refresh_requested IN (0,1)), ",
    "",
)
assert PRIOR_REVIEW_WORK_SQL != persistence_schema._REVIEW_WORK_SQL


# Every producer before #1351 keyed a current package by its work alone, with
# no arrival position that SQLite never gives out twice.
PRIOR_CURRENT_REVIEW_PACKAGES_SQL = (
    persistence_schema._CURRENT_REVIEW_PACKAGES_SQL.replace(
        "package_position INTEGER PRIMARY KEY AUTOINCREMENT, ", ""
    ).replace(
        "UNIQUE(connection_name,library_name,item_id))",
        "PRIMARY KEY(connection_name,library_name,item_id))",
    )
)
assert (
    PRIOR_CURRENT_REVIEW_PACKAGES_SQL != persistence_schema._CURRENT_REVIEW_PACKAGES_SQL
)


# Every producer before #2038 held its owner decisions, their events and its
# no-change rows by foreign key to their case and report, and kept no item key
# or display copies on them or on its write plans (ADR-0108). These undo
# exactly that change, so each older form below derives from them.
_CASE_REFERENCE = (
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
)
_CASE_SHAPE = (
    "review_case_id TEXT NOT NULL CHECK(review_case_id GLOB 'review_case_[0-9a-f]*' "
    "AND length(review_case_id)=76), "
)
_ITEM_KEY = (
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL"
)
_DISPLAY_TITLE = (
    "display_title TEXT CHECK(display_title IS NULL OR typeof(display_title)='text')"
)


def _undone(current: str, *steps: tuple[str, str]) -> str:
    """The current DDL with each (now, then) step undone, every one applying."""
    for now, then in steps:
        assert current.count(now) == 1, now
        current = current.replace(now, then)
    return current


PRE_2038_HUMAN_DECISIONS_SQL = _undone(
    persistence_schema._HUMAN_DECISIONS_SQL,
    (_CASE_SHAPE, _CASE_REFERENCE),
    (
        _ITEM_KEY
        + ", "
        + _DISPLAY_TITLE
        + ", target_field_document TEXT NOT NULL CHECK(typeof(target_field_document)='text' "
        "AND json_valid(target_field_document) AND json_type(target_field_document) IN "
        "('object','null')), selected_form TEXT CHECK(selected_form IS NULL OR "
        "(typeof(selected_form)='text' AND destination='description' AND outcome='accept')), ",
        "",
    ),
)
PRE_2038_HUMAN_DECISION_EVENTS_SQL = _undone(
    persistence_schema._HUMAN_DECISION_EVENTS_SQL,
    (_CASE_SHAPE, _CASE_REFERENCE),
    (", " + _ITEM_KEY + ")", ")"),
)
PRE_2038_FIELD_EFFECT_NO_CHANGES_SQL = _undone(
    persistence_schema._FIELD_EFFECT_NO_CHANGES_SQL,
    (
        _CASE_SHAPE
        + "report_id TEXT NOT NULL CHECK(report_id GLOB 'report_[0-9a-f]*' AND length(report_id)=71), ",
        _CASE_REFERENCE
        + "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), ",
    ),
    (", " + _DISPLAY_TITLE + ") WITHOUT ROWID", ") WITHOUT ROWID"),
)
PRE_2038_FIELD_EFFECTS_SQL = _undone(
    persistence_schema._FIELD_EFFECTS_SQL, (", " + _DISPLAY_TITLE + ")", ")")
)
PRE_2038_METADATA_POLICY_TRANSITIONS_SQL = _undone(
    persistence_schema._METADATA_POLICY_TRANSITIONS_SQL,
    (
        ", completed INTEGER NOT NULL DEFAULT 0 CHECK(completed>=0), "
        "failed INTEGER NOT NULL DEFAULT 0 CHECK(failed>=0), "
        "CHECK(completed+failed<=total))",
        ")",
    ),
)
# What a reconstruction of any earlier producer sets first, before its own
# older forms (#2038).
PRE_2038_SCHEMA = {
    "_HUMAN_DECISIONS_SQL": PRE_2038_HUMAN_DECISIONS_SQL,
    "_HUMAN_DECISION_EVENTS_SQL": PRE_2038_HUMAN_DECISION_EVENTS_SQL,
    "_FIELD_EFFECT_NO_CHANGES_SQL": PRE_2038_FIELD_EFFECT_NO_CHANGES_SQL,
    "_FIELD_EFFECTS_SQL": PRE_2038_FIELD_EFFECTS_SQL,
    "_METADATA_POLICY_TRANSITIONS_SQL": PRE_2038_METADATA_POLICY_TRANSITIONS_SQL,
}


def set_pre_2038_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo #2038's schema change for a reconstructed earlier producer."""
    for name, sql in PRE_2038_SCHEMA.items():
        monkeypatch.setattr(persistence_schema, name, sql)


# Nor did it keep the owner's preparation controls apart from scheduling: one
# row held both, the Book matching level last (#2038).
PRE_2038_REVIEW_CONTROL_SQL = _undone(
    persistence_schema._REVIEW_CONTROL_SQL,
    (
        "CHECK(singleton=1), claim_sequence",
        "CHECK(singleton=1), paused INTEGER NOT NULL CHECK(paused IN (0,1)), "
        "claim_sequence",
    ),
    (
        "CHECK(claim_sequence>=0), last_library",
        "CHECK(claim_sequence>=0), first_boot_acknowledged INTEGER NOT NULL "
        "CHECK(first_boot_acknowledged IN (0,1)), last_library",
    ),
    (
        "CHECK(updated_at>=0))",
        "CHECK(updated_at>=0), work_identity_level TEXT NOT NULL DEFAULT "
        "'identifier' CHECK(work_identity_level IN "
        "('identifier','title_and_creator_exact','title_and_creator')))",
    ),
)


# Nor did it keep the owner's authored descriptions apart from Automatic's:
# one table held both, and the case's chosen one named it by foreign key.
_AUTOMATIC_DESCRIPTION_DECISIONS_SQL = persistence_schema._DESCRIPTION_ADVICE_TABLES[
    "automatic_description_decisions"
]
PRE_2038_DESCRIPTION_DECISIONS_SQL = _undone(
    _AUTOMATIC_DESCRIPTION_DECISIONS_SQL,
    (
        "CREATE TABLE automatic_description_decisions (decision_id TEXT PRIMARY KEY "
        "NOT NULL CHECK(decision_id GLOB 'automatic_decision_[0-9a-f]*' AND "
        "length(decision_id)=83), ",
        "CREATE TABLE description_decisions (decision_id TEXT PRIMARY KEY NOT NULL, ",
    ),
)
PRE_2038_CURRENT_DESCRIPTION_DECISIONS_SQL = _undone(
    persistence_schema._DESCRIPTION_ADVICE_TABLES["current_description_decisions"],
    (
        "decision_id TEXT NOT NULL)",
        "decision_id TEXT NOT NULL REFERENCES description_decisions(decision_id))",
    ),
)


def _renamed(current: str, now: str, then: str) -> str:
    """An object of the one table, named and placed on it as before."""
    assert current.count(now) == 2, now
    return current.replace(now, then)


PRE_2038_DESCRIPTION_DECISION_TRIGGERS = tuple(
    _renamed(
        persistence_schema._DESCRIPTION_ADVICE_TRIGGERS[
            f"automatic_description_decisions_no_{operation}"
        ],
        "automatic_description_decisions",
        "description_decisions",
    )
    for operation in ("update", "delete")
)
PRE_2038_DESCRIPTION_DECISIONS_CASE_INDEX_SQL = _renamed(
    persistence_schema._DESCRIPTION_DECISION_INDEXES[
        "automatic_description_decisions_case"
    ],
    "automatic_description_decisions",
    "description_decisions",
)


def _undo_description_split(connection: sqlite3.Connection) -> None:
    """One description decisions table again, keeping every row and every
    trigger a predecessor kept on the case's chosen one."""
    kept = [
        sql
        for (sql,) in connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' "
            "AND tbl_name='current_description_decisions' "
            "AND name NOT LIKE 'current_description_decisions_validate_%' ORDER BY rowid"
        )
    ]
    connection.executescript(
        "CREATE TEMP TABLE pre_2038_description AS SELECT decision_id,review_case_id,"
        "candidate_id,idempotency_digest,document FROM owner_authored_descriptions "
        "UNION ALL SELECT decision_id,review_case_id,candidate_id,idempotency_digest,"
        "document FROM automatic_description_decisions;"
        "CREATE TEMP TABLE pre_2038_current_description AS "
        "SELECT * FROM current_description_decisions;"
        "DROP TABLE current_description_decisions;"
        "DROP TABLE owner_authored_descriptions;"
        "DROP TABLE automatic_description_decisions;"
        + PRE_2038_DESCRIPTION_DECISIONS_SQL
        + ";"
        + PRE_2038_CURRENT_DESCRIPTION_DECISIONS_SQL
        + ";INSERT INTO description_decisions SELECT * FROM pre_2038_description;"
        "INSERT INTO current_description_decisions "
        "SELECT * FROM pre_2038_current_description;"
        + "".join(sql + ";" for sql in PRE_2038_DESCRIPTION_DECISION_TRIGGERS)
        + PRE_2038_DESCRIPTION_DECISIONS_CASE_INDEX_SQL
        + ";"
        + "".join(sql + ";" for sql in kept)
        + "DROP TABLE temp.pre_2038_description;"
        "DROP TABLE temp.pre_2038_current_description;"
    )


def undo_2038_tables(connection: sqlite3.Connection) -> None:
    """Undo #2038's table splits as every producer before it kept them:
    the two preparation control rows merged back into one row, and the
    description decisions back in one table, keeping their values."""
    connection.executescript(
        "CREATE TEMP TABLE pre_2038_control AS SELECT 1 AS singleton,"
        "owner.paused,scheduling.claim_sequence,owner.first_boot_acknowledged,"
        "scheduling.last_library,scheduling.circuit_failures,"
        "scheduling.circuit_open_until,scheduling.updated_at,"
        "owner.work_identity_level FROM review_preparation_control scheduling,"
        "review_preparation_owner_control owner;"
        "DROP TABLE review_preparation_owner_control;"
        "DROP TABLE review_preparation_control;"
        + PRE_2038_REVIEW_CONTROL_SQL
        + ";INSERT INTO review_preparation_control SELECT * FROM pre_2038_control;"
        "DROP TABLE temp.pre_2038_control;"
    )
    _undo_description_split(connection)


# Every producer before #1285 admitted only the three original human outcomes.
# Every producer before #1523 kept no check on the selected value's kind.
WITHOUT_SELECTED_KIND_HUMAN_DECISIONS_SQL = (
    PRE_2038_HUMAN_DECISIONS_SQL[
        : PRE_2038_HUMAN_DECISIONS_SQL.index(", CHECK(selected_value_document='null'")
    ]
    + ")"
)
PRIOR_HUMAN_DECISIONS_SQL = WITHOUT_SELECTED_KIND_HUMAN_DECISIONS_SQL.replace(
    "'reject','owner_entered','withdraw','pin','unpin')", "'reject')"
)
assert PRIOR_HUMAN_DECISIONS_SQL != WITHOUT_SELECTED_KIND_HUMAN_DECISIONS_SQL
# Every producer before #1973 floored an episode by field effect row and
# admitted `follows` on a keep only.
PRIOR_REVIEW_CASE_EPISODES_SQL = persistence_schema._REVIEW_CASE_EPISODES_SQL.replace(
    "event_floor", "effect_floor"
)
assert PRIOR_REVIEW_CASE_EPISODES_SQL != persistence_schema._REVIEW_CASE_EPISODES_SQL
PRIOR_FOLLOWS_HUMAN_DECISIONS_SQL = PRE_2038_HUMAN_DECISIONS_SQL.replace(
    "CHECK(json_valid(document)=0 OR json_type(document,'$.follows') IN ('null','text')))",
    "CHECK(json_valid(document)=0 OR json_type(document,'$.follows')='null' "
    "OR (outcome='keep_current' AND json_type(document,'$.follows')='text')))",
)
assert PRIOR_FOLLOWS_HUMAN_DECISIONS_SQL != PRE_2038_HUMAN_DECISIONS_SQL
# Every producer before #1953 recorded no unidentified retirement.
WITHOUT_UNIDENTIFIED_REVIEW_TRANSITIONS_SQL = (
    persistence_schema._REVIEW_TRANSITIONS_SQL.replace(
        "'ineligible','unidentified',", "'ineligible',"
    ).replace("reason IN ('ineligible','unidentified')", "reason='ineligible'")
)
assert WITHOUT_UNIDENTIFIED_REVIEW_TRANSITIONS_SQL.count("unidentified") == 0
# Nor did any before #1523 record a recent_write transition.
PRIOR_REVIEW_TRANSITIONS_SQL = WITHOUT_UNIDENTIFIED_REVIEW_TRANSITIONS_SQL.replace(
    "'actionable','recent_write','nonactionable'", "'actionable','nonactionable'"
).replace("reason IN ('actionable','recent_write')", "reason='actionable'")
assert PRIOR_REVIEW_TRANSITIONS_SQL.count("recent_write") == 0


# Every producer before #1346 kept no pin: a pin in force settled nothing, a
# changed pin asked nothing, and a pin or unpin row was no outcome. These undo
# exactly the edits #1346 made to the attention source, so the oracles
# below keep describing the predecessors they name.
_PIN_CHANGED_SQL = review_sql._PIN_CHANGED_SQL
_PIN_IN_FORCE_SQL = review_sql._pin_hold_sql(
    "rc.review_case_id", "result.destination", True
)
_PINNED_REASON_HEAD = "(result.has_owner_decision_actions=1 AND ((result."
_PINNED_REASON_TAIL = " OR " + _PIN_CHANGED_SQL + ")"
assert review_sql._REVIEW_FIELD_ATTENTION_REASON_SQL.startswith(_PINNED_REASON_HEAD)
assert review_sql._REVIEW_FIELD_ATTENTION_REASON_SQL.endswith(_PINNED_REASON_TAIL)
PRE_1346_REVIEW_FIELD_ATTENTION_REASON_SQL = (
    review_sql._REVIEW_FIELD_ATTENTION_REASON_SQL[1 : -len(_PINNED_REASON_TAIL)]
)


def without_pins(sql: str) -> str:
    """The attention SQL as every producer before #1346 wrote it."""
    unpinned = (
        sql.replace(_PINNED_REASON_HEAD, _PINNED_REASON_HEAD[1:])
        .replace(_PINNED_REASON_TAIL, "")
        .replace("AND decision.outcome NOT IN ('pin','unpin') ", "")
        .replace(
            "),'withdraw')='withdraw' AND NOT " + _PIN_IN_FORCE_SQL + " ",
            "),'withdraw')='withdraw' ",
        )
        .replace(" AND NOT " + _PIN_CHANGED_SQL + ")", ")")
    )
    assert "pin_hold" not in unpinned and "'pin'" not in unpinned
    return unpinned


PRE_1346_ATTENTION_FACT_SOURCE_SQL = without_pins(review_sql._ATTENTION_FACT_SOURCE_SQL)
assert PRE_1346_ATTENTION_FACT_SOURCE_SQL != review_sql._ATTENTION_FACT_SOURCE_SQL
# Before #1285 any owner decision settled a field; now only its latest one of
# the case's episode does (#1358), unless it withdrew the field.
HUMAN_DECIDED_SQL = (
    " AND COALESCE((SELECT decision.outcome FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination AND "
    + _episode_decision_sql("decision")
    + " ORDER BY decision.ordinal DESC LIMIT 1),'withdraw')='withdraw' "
)
PRIOR_HUMAN_DECIDED_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination) "
)
# Every producer before #1358 kept no case episodes.
# Nor, before #1310, did their cover branch keep a verified current cover.
_KEPT_COVER_SQL = " AND NOT " + review_sql._VERIFIED_CURRENT_COVER_SQL
# Nor did they split a cover the measured rules hold or suggest (#1363): a
# manually ready comparison asked unless its current cover was kept.
MEASURED_COVER_ATTENTION_SQL = PRE_1346_REVIEW_FIELD_ATTENTION_REASON_SQL.partition(
    "OR " + review_sql._REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
)[0]
MANUAL_READY_COVER_ATTENTION_SQL = (
    "result.has_owner_decision_actions=1 AND (result.unresolved_choice=1 "
    "OR (result.destination='cover' AND EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current_cover.consideration_id "
    "WHERE current_cover.review_case_id=rc.review_case_id "
    "AND json_extract(consideration.currentness_document,'$.manual_ready')=1)"
    + _KEPT_COVER_SQL
    + ") "
)
assert MEASURED_COVER_ATTENTION_SQL != MANUAL_READY_COVER_ATTENTION_SQL
# Nor did they admit a refreshing cover by its raw outcome.
_RAW_REFRESH_SQL = "THEN " + review_sql._COVER_UNRESOLVED_OUTCOME_SQL + " END"

# Every producer before #1573 let an automatic decision of any generation
# settle its field, so it kept no policy-state attention input either.
AUTOMATIC_DECIDED_SQL = (
    "AND decision.destination=result.destination AND "
    + review_sql._active_generation_automatic_sql("decision")
    + ") "
)
PRIOR_AUTOMATIC_DECIDED_SQL = "AND decision.destination=result.destination ) "

PRIOR_ATTENTION_FACT_OBJECTS = tuple(
    (
        kind,
        name,
        without_pins(sql)
        .replace(MEASURED_COVER_ATTENTION_SQL, MANUAL_READY_COVER_ATTENTION_SQL)
        .replace(HUMAN_DECIDED_SQL, PRIOR_HUMAN_DECIDED_SQL)
        .replace(AUTOMATIC_DECIDED_SQL, PRIOR_AUTOMATIC_DECIDED_SQL)
        .replace(_KEPT_COVER_SQL, "")
        .replace(_RAW_REFRESH_SQL, "THEN result.unresolved_choice END"),
    )
    for kind, name, sql in persistence_schema._ATTENTION_FACT_OBJECTS
    if not name.startswith(
        (
            "review_case_episodes_",
            "metadata_policy_state_",
            "field_effect_manual_holds_",
        )
    )
)

assert any(
    PRIOR_AUTOMATIC_DECIDED_SQL in sql for _, _, sql in PRIOR_ATTENTION_FACT_OBJECTS
)
# Open checks the stored facts against this source, so a store holding the
# prior automatic decisions table is checked by the prior source.
PRIOR_ATTENTION_FACT_SOURCE_SQL = PRE_1346_ATTENTION_FACT_SOURCE_SQL.replace(
    AUTOMATIC_DECIDED_SQL, PRIOR_AUTOMATIC_DECIDED_SQL
)
assert PRIOR_ATTENTION_FACT_SOURCE_SQL != review_sql._ATTENTION_FACT_SOURCE_SQL

# Every producer before #1573 kept one automatic decision per case and
# destination.
PRIOR_AUTOMATIC_DECISIONS_SQL = persistence_schema._AUTOMATIC_DECISIONS_SQL.replace(
    "policy_generation INTEGER NOT NULL CHECK(typeof(policy_generation)='integer' "
    "AND policy_generation>=1), "
    "CHECK((CASE WHEN json_valid(document) THEN json_extract(document,"
    "'$.policy_generation') IS policy_generation ELSE 0 END) IS 1), "
    "UNIQUE(review_case_id,destination,policy_generation))",
    "UNIQUE(review_case_id,destination))",
)
assert PRIOR_AUTOMATIC_DECISIONS_SQL != persistence_schema._AUTOMATIC_DECISIONS_SQL


# Every producer before #1573 had four preparation triggers; confirming
# Automatic reopened preparation under title recovery, and neither Book
# matching (#1355) nor a book link (#1477) had a trigger of its own.
PRIOR_REVIEW_PREPARATION_PRESENTATION_SQL = (
    persistence_schema._REVIEW_PREPARATION_PRESENTATION_SQL.replace(
        "'title_recovery','automatic_confirmed','matching_changed','book_linked')",
        "'title_recovery')",
    ).replace("trigger_ordinal BETWEEN 0 AND 6", "trigger_ordinal BETWEEN 0 AND 3")
)
assert PRIOR_REVIEW_PREPARATION_PRESENTATION_SQL.count("'title_recovery')") == 1
assert PRIOR_REVIEW_PREPARATION_PRESENTATION_SQL.count("BETWEEN 0 AND 3") == 1


# Every producer before #1407 derived no provider match state for a report.
PRIOR_REPORT_BROWSE_FACTS_SQL = persistence_schema._REPORT_BROWSE_FACTS_SQL.replace(
    ", provider_match_state TEXT NOT NULL CHECK(provider_match_state IN "
    "('matched','refused','unavailable','adapter_fault','no_match'))",
    "",
)


def _without_match_state(sql: str, document: str) -> str:
    value = persistence_schema._report_browse_fact_values_sql(document).partition(
        " THEN 1 ELSE 0 END,"
    )[2]
    return sql.replace(" THEN 1 ELSE 0 END," + value, " THEN 1 ELSE 0 END")


# Nor did they exclude #1498's adapter-faulted lanes from the limited flag.
_FAULT_EXCLUSION_SQL = " AND NOT " + persistence_schema._LANE_ADAPTER_FAULT_SQL


# Nor did they ignore #1779's cover-only lanes on an audio report.
def _cover_only_exclusion_sql(document: str) -> str:
    return "NOT " + persistence_schema._cover_only_provider_sql(document) + " AND "


PRIOR_REPORT_BROWSE_FACTS_INSERT_SQL = (
    _without_match_state(
        persistence_schema._REPORT_BROWSE_FACTS_INSERT_SQL.replace(
            ",provider_limited,provider_match_state)", ",provider_limited)"
        ),
        "NEW.document",
    )
    .replace(_FAULT_EXCLUSION_SQL, "")
    .replace(_cover_only_exclusion_sql("NEW.document"), "")
    # Nor did they derive #1334's value flags.
    .replace(
        "; INSERT INTO metadata_report_value_flags(report_id,destination,rule) "
        "SELECT NEW.report_id,"
        + persistence_schema._report_value_flag_values_sql("", "NEW.document"),
        "",
    )
)
assert "metadata_report_value_flags" not in PRIOR_REPORT_BROWSE_FACTS_INSERT_SQL


_CURRENT_REPORT_BROWSE_FACT_PAIRS = persistence_schema._report_browse_fact_pairs


def prior_report_browse_fact_pairs(key: str) -> tuple[tuple[str, str], ...]:
    """The open-time fact comparison those producers' stores answer."""
    (derived, stored), *unchanged = _CURRENT_REPORT_BROWSE_FACT_PAIRS(key)
    return (
        (
            _without_match_state(derived, "mr.document")
            .replace(_FAULT_EXCLUSION_SQL, "")
            .replace(_cover_only_exclusion_sql("mr.document"), ""),
            stored.replace(",provider_match_state FROM", " FROM"),
        ),
        *unchanged,
    )


def create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        persistence_schema._REPORTS_SQL.replace(
            f"= '{CURRENT_REPORT_SCHEMA}'", "= 'metadata-report-v1'"
        )
    )
    assert (
        "metadata-report-v1"
        in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'metadata_reports'"
        ).fetchone()[0]
    )
    connection.execute(persistence_schema._CURRENT_SQL)
    connection.execute(persistence_schema._NO_UPDATE_SQL)
    connection.execute(persistence_schema._NO_DELETE_SQL)


def create_prior_schema(
    connection: sqlite3.Connection,
    version: str = "metadata-report-v9",
    proposal_policy: str = "fixed-conservative-v6",
) -> None:
    assert f"= '{CURRENT_REPORT_SCHEMA}'" in persistence_schema._REPORTS_SQL
    statements = (
        persistence_schema._REPORTS_SQL.replace(
            f"= '{CURRENT_REPORT_SCHEMA}'", f"= '{version}'"
        ),
        persistence_schema._CURRENT_SQL,
        persistence_schema._CURRENT_LINEAGE_SQL,
        persistence_schema._COVER_REFERENCES_SQL,
        persistence_schema._COVER_ARTIFACT_INDEX_SQL,
        persistence_schema._COVER_PREVIEW_INDEX_SQL,
        persistence_schema._PROPOSAL_SETS_SQL.replace(
            f"= '{POLICY_VERSION}'",
            f"= '{proposal_policy}'",
        ),
        persistence_schema._REVIEW_CASES_SQL,
        persistence_schema._REVIEW_POINTERS_SQL,
        persistence_schema._REVIEW_TRANSITIONS_SQL,
        persistence_schema._NO_UPDATE_SQL,
        persistence_schema._COVER_NO_UPDATE_SQL,
        persistence_schema._COVER_NO_DELETE_SQL,
        persistence_schema._PROPOSAL_NO_UPDATE_SQL,
        persistence_schema._PROPOSAL_NO_DELETE_SQL,
        persistence_schema._REVIEW_NO_UPDATE_SQL,
        persistence_schema._REVIEW_NO_DELETE_SQL,
        persistence_schema._REVIEW_TRANSITION_NO_UPDATE_SQL,
        persistence_schema._REVIEW_TRANSITION_NO_DELETE_SQL,
        persistence_schema._NO_DELETE_SQL,
    )
    for statement in statements:
        connection.execute(statement)
    assert (
        f"= '{version}'"
        in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'metadata_reports'"
        ).fetchone()[0]
    )


def leave_hot_journal(path: Path) -> Path:
    child = (
        "import os,sqlite3,sys\n"
        "connection=sqlite3.connect(sys.argv[1])\n"
        "connection.execute('PRAGMA journal_mode=DELETE')\n"
        "connection.execute('PRAGMA synchronous=FULL')\n"
        "connection.execute('BEGIN IMMEDIATE')\n"
        "connection.execute('PRAGMA user_version=1')\n"
        "os._exit(0)\n"
    )
    subprocess.run(
        [sys.executable, "-c", child, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    journal = Path(f"{path}-journal")
    if not journal.is_file() or journal.stat().st_size <= 512:
        raise AssertionError("synthetic hot journal was not created")
    return journal


def create_pre_currentness_schema(connection: sqlite3.Connection) -> None:
    """Build the exact #1072 predecessor, with no derived cover facts column."""
    persistence_schema._create_schema(connection)
    # #1232's attention facts read the dropped column, so they go first.
    connection.executescript(
        "".join(
            f"DROP {kind.upper()} {name};"
            for kind, name, _ in reversed(persistence_schema._ATTENTION_FACT_OBJECTS)
        )
    )
    connection.execute(
        "ALTER TABLE cover_considerations DROP COLUMN currentness_document"
    )
    connection.commit()
