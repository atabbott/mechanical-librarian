"""Guarded cover writes as the field-effect store records them (#1363).

A Put back proof needs a verified automatic switch, and sometimes the owner's
Put back write, already in the store. These build the real operation document
the case and Covers reads parse, without driving a guarded application.
"""

from __future__ import annotations

import sqlite3

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.metadata_report import TargetFacts


def cover_operation(
    target: TargetFacts,
    *,
    decision_id: str,
    review_case_id: str,
    pre_value: str,
    desired_value: str,
    state: str,
    at: int,
    failure: str | None = None,
) -> FieldEffectOperation:
    """One cover write for ``target``, automatic or the owner's by its decision."""
    automatic = decision_id.startswith("automatic_decision_")
    plan = FieldEffectPlan.create(
        connection=target.connection,
        library=target.library,
        work_id=target.item,
        destination="cover",
        pre_value=pre_value,
        desired_value=desired_value,
        target_fingerprint="e" * 64,
        target_version="version-1",
        dependency_digests=("a" * 64,),
        authority=EffectAuthority(
            decision_id,
            review_case_id,
            "e" * 64,
            "persisted_review_case",
            "automatic_policy" if automatic else "human",
            "candidate",
            "synthetic-policy" if automatic else None,
            "f" * 64 if automatic else None,
        ),
        consent=EffectConsent.create(
            connection=target.connection,
            library=target.library,
            destination="cover",
            envelope_identity="d" * 64,
            granted_at=at,
        ),
        capability_digest="c" * 64,
        created_at=at,
    )
    return FieldEffectOperation(plan, state, 1, at, failure)


def record_cover_operation(
    connection: sqlite3.Connection, operation: FieldEffectOperation
) -> None:
    plan = operation.plan
    connection.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,?,?,?,'cover',?,?,?,?)",
        (
            plan.plan_id,
            plan.authority.decision_id,
            plan.connection,
            plan.library,
            plan.work_id,
            operation.state,
            operation.attempt,
            operation.updated_at,
            _canonical_json(operation.to_data()),
        ),
    )


def automatic_switch(
    target: TargetFacts, original: str, current: str, *, at: int = 900
) -> FieldEffectOperation:
    """The verified automatic switch that replaced ``original`` with ``current``.

    It was decided under an earlier case of the work, so its plan, and the
    retained source report assembly names after it, exist before this case.
    """
    return cover_operation(
        target,
        decision_id="automatic_decision_" + "c" * 64,
        review_case_id="review_case_" + "a" * 64,
        pre_value=original,
        desired_value=current,
        state="verified",
        at=at,
    )


def retained_source(plan_id: str, artifact: CoverArtifact) -> CoverSource:
    """The original a switch replaced, as report assembly offers it."""
    return CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, plan_id),
        CoverSourceKind.RETAINED,
        CoverSourceState.RETRIEVED,
        artifact,
    )


def list_switch(
    connection: sqlite3.Connection,
    switch: FieldEffectOperation,
    original_digest: str,
    byte_count: int,
) -> None:
    """Record the switch with its original bound and listed, as verification does."""
    plan = switch.plan
    record_cover_operation(connection, switch)
    connection.execute(
        "INSERT INTO retained_cover_bindings(plan_id,decision_id,connection_name,"
        "library_name,work_id,original_digest,byte_count,retained_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            plan.plan_id,
            plan.authority.decision_id,
            plan.connection,
            plan.library,
            plan.work_id,
            original_digest,
            byte_count,
            switch.updated_at,
        ),
    )
    connection.execute(
        "INSERT INTO retained_cover_listings(plan_id,sequence) VALUES(?,1)",
        (plan.plan_id,),
    )
    connection.commit()
