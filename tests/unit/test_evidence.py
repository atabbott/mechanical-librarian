from __future__ import annotations

from dataclasses import replace
import json
import unittest

from mechanical_librarian.domain.evidence import (
    Candidate,
    ConfidenceFact,
    CrossTargetPolicyDecision,
    EvidenceDetail,
    EvidenceReadiness,
    EvidenceReadinessFact,
    EvidenceReadinessState,
    EvidenceRecord,
    EvidenceReference,
    EvidenceRelationship,
    EvidenceSummary,
    EvidenceValueError,
    FieldProposal,
    FieldValue,
    ProposalKind,
    ReviewerEvidencePacket,
    ReviewNotice as EvidenceNotice,
    Risk,
    RiskSeverity,
    SourceApplicability,
)
from mechanical_librarian.domain.evidence_bounds import (
    MAX_EVIDENCE_REFERENCES,
    MAX_EXCERPT_LENGTH,
    MAX_FACTS,
    MAX_FACT_VALUE_LENGTH,
    MAX_FIELD_VALUE_LENGTH,
    MAX_FIELD_VALUES,
    MAX_PACKET_RECORDS,
    MAX_RISKS,
    MAX_SERIALIZED_BYTES,
    MAX_SHORT_TEXT_LENGTH,
    MAX_WARNINGS,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    EvidenceId,
    ExternalItemId,
    ExtractorVersion,
    Fingerprint,
    LibraryId,
    PolicyVersion,
    ProposalId,
    SchemaVersion,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import FieldName


class EvidenceTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = self._target("primary", "book-1")
        self.other = self._target("secondary", "book-1")
        self.snapshot = SnapshotId("snapshot-one")
        self.snapshot_fingerprint = Fingerprint.from_canonical_value(
            {"title": "Example"}, schema=SchemaVersion("snapshot-v1")
        )
        self.reference = EvidenceReference(
            EvidenceId("evidence-one"),
            self.target,
            self.target,
            EvidenceRelationship.SAME_TARGET,
        )

    def test_field_values_enforce_text_count_uniqueness_and_immutability(self) -> None:
        self.assertEqual(
            len(FieldValue(("x" * MAX_FIELD_VALUE_LENGTH,)).values[0]),
            MAX_FIELD_VALUE_LENGTH,
        )
        invalid = (
            ("x" * (MAX_FIELD_VALUE_LENGTH + 1),),
            tuple("value" for _ in range(MAX_FIELD_VALUES + 1)),
            ("duplicate", "duplicate"),
            ("control\x00",),
            ("bidi\u202e",),
            ("surrogate\ud800",),
        )
        for values in invalid:
            with self.subTest(values=repr(values[:2])):
                with self.assertRaises(EvidenceValueError):
                    FieldValue(values)
        with self.assertRaises(EvidenceValueError):
            FieldValue(["mutable"])  # type: ignore[arg-type]
        self.assertNotIn("private-title", repr(FieldValue(("private-title",))))

    def test_facts_and_records_reject_hostile_or_duplicate_values(self) -> None:
        with self.assertRaises(EvidenceValueError):
            ConfidenceFact("code", "x" * (MAX_FACT_VALUE_LENGTH + 1))
        with self.assertRaises(EvidenceValueError):
            ConfidenceFact("bad code", "value")
        with self.assertRaises(EvidenceValueError):
            ConfidenceFact("code", "\x01")
        fact = ConfidenceFact("title", "Example")
        with self.assertRaisesRegex(EvidenceValueError, "facts"):
            self._record(facts=(fact, fact))
        with self.assertRaises(EvidenceValueError):
            self._record(
                facts=tuple(
                    ConfidenceFact(f"f{index}", "v")
                    for index in range(MAX_FACTS + 1)
                )
            )
        with self.assertRaises(EvidenceValueError):
            self._record(warnings=("warning", "warning"))
        with self.assertRaises(EvidenceValueError):
            self._record(lineage=" ")
        self.assertNotIn("private-lineage", repr(self._record(lineage="private-lineage")))

    def test_persisted_facts_reject_credentials_without_rejecting_private_paths(self) -> None:
        for credential in (
            "Authorization: Bearer private-token",
            "Bearer private-token-123",
            "Bearer abcdefgh1234",
            "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "token=private-token",
            "secret=private-secret",
            "password=private-password",
            "API-Key: private-key",
        ):
            with self.subTest(credential=credential):
                with self.assertRaisesRegex(EvidenceValueError, "credential-shaped"):
                    ConfidenceFact("title", credential)

        for ordinary_value in (
            "Bearer of Bad News",
            "Bearer Certificate",
            "Bearer Authentication",
            "Bearer Williams",
            "Bearer Moonlight",
            "The Secret: A Novel",
            "Passwordless: Modern Authentication",
            "A key contribution",
        ):
            with self.subTest(ordinary_value=ordinary_value):
                self.assertEqual(ConfidenceFact("title", ordinary_value).value, ordinary_value)

        private_path = ConfidenceFact("path", "/private/library/book.epub")
        persisted = self._record(facts=(private_path,)).to_persistence_json()
        self.assertIn("/private/library/book.epub", persisted)

        unsafe_persisted = json.loads(
            self._record(facts=(ConfidenceFact("title", "Example"),)).to_persistence_json()
        )
        unsafe_persisted["facts"][0]["value"] = "token=private-token"
        with self.assertRaisesRegex(EvidenceValueError, "credential-shaped"):
            EvidenceRecord.from_persistence_json(_canonical(unsafe_persisted))

    def test_cross_target_references_require_versioned_policy(self) -> None:
        with self.assertRaises(EvidenceValueError):
            EvidenceReference(
                EvidenceId("evidence-one"),
                self.target,
                self.other,
                EvidenceRelationship.EXPLICIT_CROSS_TARGET,
            )
        decision = CrossTargetPolicyDecision(PolicyVersion("policy-v1"), "same-work")
        reference = EvidenceReference(
            EvidenceId("evidence-one"),
            self.target,
            self.other,
            EvidenceRelationship.EXPLICIT_CROSS_TARGET,
            decision,
        )
        self.assertEqual(reference.policy_decision, decision)
        with self.assertRaises(EvidenceValueError):
            replace(self.reference, policy_decision=decision)
        with self.assertRaises(EvidenceValueError):
            CrossTargetPolicyDecision("policy-v1", "same-work")  # type: ignore[arg-type]

    def test_proposals_are_target_checked_reference_only_and_unique(self) -> None:
        proposal = self._proposal()
        self.assertEqual(proposal.field, FieldName.TITLE)
        self.assertEqual(proposal.supporting_evidence, (self.reference,))
        with self.assertRaises(EvidenceValueError):
            replace(proposal, supporting_evidence=(self.reference, self.reference))
        with self.assertRaises(EvidenceValueError):
            replace(
                proposal,
                supporting_evidence=(replace(self.reference, consuming_target=self.other),),
            )
        risk = Risk("identity", RiskSeverity.HIGH, True, (self.reference,))
        with self.assertRaisesRegex(EvidenceValueError, "proposal evidence"):
            replace(proposal, contradicting_evidence=(self.reference,), risks=(risk,))
        with self.assertRaises(EvidenceValueError):
            replace(proposal, risks=(risk, replace(risk)))
        with self.assertRaises(EvidenceValueError):
            replace(proposal, kind=ProposalKind.FILL_EMPTY, current_value=FieldValue(("old",)))

    def test_candidates_risks_and_notices_are_bounded_reference_values(self) -> None:
        candidate = Candidate(
            self.target,
            "provider-record",
            FieldValue(("Example",)),
            SourceApplicability.EDITION,
            "provider-family",
            "response-family",
            (self.reference,),
        )
        self.assertNotIn("provider-family", repr(candidate))
        with self.assertRaises(EvidenceValueError):
            replace(candidate, source_id="\x00")
        with self.assertRaises(EvidenceValueError):
            replace(candidate, contradicts=(self.reference, self.reference))
        with self.assertRaises(EvidenceValueError):
            replace(candidate, value="Example")  # type: ignore[arg-type]

        risk = Risk("identity", RiskSeverity.HIGH, True, (self.reference,))
        with self.assertRaises(EvidenceValueError):
            replace(risk, evidence=(self.reference, self.reference))
        other_reference = EvidenceReference(
            EvidenceId("evidence-two"),
            self.other,
            self.other,
            EvidenceRelationship.SAME_TARGET,
        )
        with self.assertRaisesRegex(EvidenceValueError, "one consuming target"):
            replace(risk, evidence=(self.reference, other_reference))
        duplicate_id_reference = EvidenceReference(
            self.reference.evidence,
            self.other,
            self.target,
            EvidenceRelationship.EXPLICIT_CROSS_TARGET,
            CrossTargetPolicyDecision(PolicyVersion("policy-v1"), "same-work"),
        )
        with self.assertRaisesRegex(EvidenceValueError, "IDs"):
            replace(risk, evidence=(self.reference, duplicate_id_reference))
        notice = EvidenceNotice("verify-source", True, (self.reference,), "Check source")
        self.assertNotIn("Check source", repr(notice))
        with self.assertRaises(EvidenceValueError):
            replace(notice, required_action="x" * 257)
        with self.assertRaises(EvidenceValueError):
            replace(notice, evidence=[self.reference])  # type: ignore[arg-type]
        with self.assertRaisesRegex(EvidenceValueError, "one consuming target"):
            replace(notice, evidence=(self.reference, other_reference))
        with self.assertRaisesRegex(EvidenceValueError, "IDs"):
            replace(notice, evidence=(self.reference, duplicate_id_reference))

    def test_persistence_serialization_is_canonical_strict_and_round_trips(self) -> None:
        record = self._record(
            facts=(ConfidenceFact("title", "Example"),),
            warnings=("ambiguous-title",),
            lineage="provider-family",
        )
        serialized = record.to_persistence_json()
        self.assertEqual(EvidenceRecord.from_persistence_json(serialized), record)
        self.assertEqual(serialized, record.to_persistence_json())
        self.assertLessEqual(len(serialized.encode()), MAX_SERIALIZED_BYTES)

        unknown = json.loads(serialized)
        unknown["unknown"] = True
        with self.assertRaises(EvidenceValueError):
            EvidenceRecord.from_persistence_json(_canonical(unknown))
        incompatible = json.loads(serialized)
        incompatible["schema"] = "evidence-persistence-v2"
        with self.assertRaises(EvidenceValueError):
            EvidenceRecord.from_persistence_json(_canonical(incompatible))
        with self.assertRaises(EvidenceValueError):
            EvidenceRecord.from_persistence_json("x" * (MAX_SERIALIZED_BYTES + 1))

    def test_persisted_lineage_rejects_credentials_but_retains_paths(self) -> None:
        with self.assertRaisesRegex(EvidenceValueError, "credential-shaped"):
            self._record(lineage="token=private-token")

        private_path_record = self._record(lineage="/private/library/provider-record")
        self.assertEqual(
            EvidenceRecord.from_persistence_json(
                private_path_record.to_persistence_json()
            ),
            private_path_record,
        )

        persisted = json.loads(self._record(lineage="provider-family").to_persistence_json())
        persisted["lineage"] = "token=private-token"
        with self.assertRaisesRegex(EvidenceValueError, "credential-shaped"):
            EvidenceRecord.from_persistence_json(_canonical(persisted))

    def test_api_and_reviewer_projections_are_bounded_and_privacy_allowlisted(self) -> None:
        private = ConfidenceFact("path", "/private/library/book.epub")
        public = ConfidenceFact("title", "Example")
        record = self._record(facts=(private, public), warnings=("path-redacted",))
        detail = EvidenceDetail.from_record(record)
        self.assertEqual(detail.facts, (public,))
        self.assertNotIn("/private", detail.to_api_json())
        self.assertNotIn("/private", repr(detail))
        with self.assertRaises(EvidenceValueError):
            EvidenceDetail(
                self.reference,
                self.snapshot,
                self.snapshot_fingerprint,
                "epub",
                (ConfidenceFact("title", "/private/library/book.epub"),),
                (),
            )
        for private_value in (
            "C:/private/library/book.epub",
            "..\\private\\book.epub",
            "~/Library/Books/private.epub",
            "Path: /srv/media/private.epub",
            "Location=C:\\private\\book.epub",
            "smb://nas/private/library/book.epub",
            "nfs://nas/private/library/book.epub",
            "afp://nas/private/library/book.epub",
            "Authorization: Bearer sk-private-token",
            "Authorization: Basic dXNlcjpwYXNz",
            "API-Key: sk-private-token",
            "X_API_KEY = private-token",
            "password: private-secret",
            "secret=private-secret",
            "token: private-token",
            "https://user:password@example.com",
            "sk-proj-private-token-123456",
            "client_secret=private-secret",
            "refresh_token=private-token",
            "AWS_SECRET_ACCESS_KEY=private-secret",
            "clientSecret: private-secret",
            "refreshToken = private-token",
            '{"client_secret":"private-secret"}',
            '{"refresh_token": "private-token"}',
            "clientAPIKey=private-key",
            "openAIAPIKey: private-key",
            "AWSAccessKeyId=private-key",
            "https://example.com?token=private-token",
            "user=alice password=private-secret",
            "credentials: client_secret=private-secret",
            "note=ok;password=private-secret",
            '{"note":"ok","client_secret":"private-secret"}',
            "prefix password: private-secret",
            "client secret: private-secret",
            "consumer secret=private-secret",
            "secret key: private-key",
            "AWS Secret Access Key: private-secret",
            "API Key: private-key",
            "access token: private-token",
            "private key: private-key",
        ):
            with self.subTest(private_value=private_value):
                with self.assertRaises(EvidenceValueError):
                    EvidenceDetail(
                        self.reference,
                        self.snapshot,
                        self.snapshot_fingerprint,
                        "epub",
                        (ConfidenceFact("title", private_value),),
                        (),
                    )

        for ordinary_title in (
            "Basic Instinct",
            "Bearer of Bad News",
            "The Secret: A Novel",
            "Secretary: A Biography",
            "Secretariat: The Making of a Champion",
            "Tokenization: A Practical Guide",
            "Passwordless: Modern Authentication",
            "Love / Hate",
            "https://example.com/books/title",
        ):
            with self.subTest(ordinary_title=ordinary_title):
                detail_with_title = EvidenceDetail(
                    self.reference,
                    self.snapshot,
                    self.snapshot_fingerprint,
                    "epub",
                    (ConfidenceFact("title", ordinary_title),),
                    (),
                )
                self.assertIn(ordinary_title, detail_with_title.to_api_json())

        summary = EvidenceSummary(record.id, "epub", 2, 1, "Bounded title fact")
        self.assertLessEqual(len(summary.to_api_json().encode()), MAX_SERIALIZED_BYTES)
        with self.assertRaises(EvidenceValueError):
            replace(summary, excerpt="/private/library/book.epub")
        with self.assertRaises(EvidenceValueError):
            replace(summary, excerpt="control\x00")

        packet = ReviewerEvidencePacket(
            self.target,
            self.snapshot,
            self.snapshot_fingerprint,
            (detail,),
        )
        self.assertEqual(packet.to_json(), packet.to_json())
        self.assertNotIn("private-lineage", packet.to_json())
        self.assertNotIn("book-1", packet.to_json())
        with self.assertRaises(EvidenceValueError):
            replace(packet, details=(detail, detail))

        with self.assertRaises(EvidenceValueError):
            EvidenceDetail.from_record(record, consuming_target=self.other)
        cross_target_detail = EvidenceDetail.from_record(
            record,
            consuming_target=self.other,
            policy_decision=CrossTargetPolicyDecision(
                PolicyVersion("policy-v1"), "same-work"
            ),
        )
        cross_target_packet = ReviewerEvidencePacket(
            self.other,
            self.snapshot,
            self.snapshot_fingerprint,
            (cross_target_detail,),
        )
        cross_target_json = cross_target_packet.to_json()
        self.assertIn("explicit_cross_target", cross_target_json)
        self.assertIn("policy-v1", cross_target_json)
        self.assertIn("same-work", cross_target_json)

    def test_reviewer_packets_require_matching_snapshot_provenance(self) -> None:
        record = self._record(facts=(ConfidenceFact("title", "Example"),))
        detail = EvidenceDetail.from_record(record)
        packet = ReviewerEvidencePacket(
            self.target,
            record.snapshot,
            record.snapshot_fingerprint,
            (detail,),
        )
        self.assertEqual(packet.details, (detail,))
        detail_projection = json.loads(detail.to_api_json())
        self.assertEqual(detail_projection["snapshot"], record.snapshot.value)
        self.assertEqual(
            detail_projection["fingerprint"]["digest"],
            record.snapshot_fingerprint.digest,
        )

        mismatches = (
            replace(detail, snapshot=SnapshotId("snapshot-two")),
            replace(
                detail,
                snapshot_fingerprint=Fingerprint.from_canonical_value(
                    {"title": "Different"}, schema=SchemaVersion("snapshot-v1")
                ),
            ),
        )
        for mismatched_detail in mismatches:
            with self.subTest(detail=mismatched_detail):
                with self.assertRaisesRegex(EvidenceValueError, "snapshot and fingerprint"):
                    replace(packet, details=(mismatched_detail,))

        cross_target_detail = EvidenceDetail.from_record(
            record,
            consuming_target=self.other,
            policy_decision=CrossTargetPolicyDecision(
                PolicyVersion("policy-v1"), "same-work"
            ),
        )
        consuming_snapshot = SnapshotId("snapshot-two")
        consuming_fingerprint = Fingerprint.from_canonical_value(
            {"title": "Consuming target"}, schema=SchemaVersion("snapshot-v1")
        )
        cross_target_packet = ReviewerEvidencePacket(
            self.other,
            consuming_snapshot,
            consuming_fingerprint,
            (cross_target_detail,),
        )
        self.assertEqual(cross_target_packet.details, (cross_target_detail,))
        cross_target_projection = json.loads(cross_target_packet.to_json())
        self.assertEqual(cross_target_projection["snapshot"], consuming_snapshot.value)
        self.assertEqual(
            cross_target_projection["fingerprint"]["digest"],
            consuming_fingerprint.digest,
        )
        self.assertEqual(
            cross_target_projection["details"][0]["snapshot"], record.snapshot.value
        )
        self.assertEqual(
            cross_target_projection["details"][0]["fingerprint"]["digest"],
            record.snapshot_fingerprint.digest,
        )

    def test_readiness_preserves_states_and_counts_unique_records_and_targets(self) -> None:
        second_reference = replace(self.reference, evidence=EvidenceId("evidence-two"))
        facts = (
            EvidenceReadinessFact(
                self.target,
                EvidenceReadinessState.CURRENT,
                (self.reference, second_reference),
            ),
            EvidenceReadinessFact(self.other, EvidenceReadinessState.MISSING),
            EvidenceReadinessFact(
                self._target("stale", "book-4"),
                EvidenceReadinessState.STALE,
                (
                    replace(
                        self.reference,
                        source_target=self._target("stale", "book-4"),
                        consuming_target=self._target("stale", "book-4"),
                    ),
                ),
            ),
            EvidenceReadinessFact(
                self._target("third", "book-2"), EvidenceReadinessState.UNSUPPORTED
            ),
            EvidenceReadinessFact(
                self._target("fourth", "book-3"),
                EvidenceReadinessState.APPROVED_SKIP,
                skip_policy=CrossTargetPolicyDecision(
                    PolicyVersion("policy-v1"), "approved-skip"
                ),
            ),
        )
        readiness = EvidenceReadiness(facts)
        self.assertEqual(readiness.evidence_count, 2)
        self.assertEqual(readiness.target_count, 5)
        projection = readiness.to_api_json()
        self.assertNotIn("book-1", projection)
        for state in ("current", "missing", "stale", "unsupported", "approved_skip"):
            self.assertIn(state, projection)
        self.assertIn("policy-v1", projection)
        self.assertIn("approved-skip", projection)
        with self.assertRaises(EvidenceValueError):
            EvidenceReadiness((facts[0], facts[0]))
        with self.assertRaises(EvidenceValueError):
            EvidenceReadinessFact(self.target, EvidenceReadinessState.STALE)
        with self.assertRaises(EvidenceValueError):
            EvidenceReadinessFact(self.target, EvidenceReadinessState.APPROVED_SKIP)
        duplicate_id = replace(
            self.reference,
            source_target=self.other,
            relationship=EvidenceRelationship.EXPLICIT_CROSS_TARGET,
            policy_decision=CrossTargetPolicyDecision(
                PolicyVersion("policy-v1"), "same-work"
            ),
        )
        with self.assertRaisesRegex(EvidenceValueError, "IDs"):
            EvidenceReadinessFact(
                self.target,
                EvidenceReadinessState.CURRENT,
                (self.reference, duplicate_id),
            )

    def test_all_documented_exact_limits_accept(self) -> None:
        field_values = tuple(f"value-{index}" for index in range(MAX_FIELD_VALUES))
        self.assertEqual(len(FieldValue(field_values).values), MAX_FIELD_VALUES)

        facts = tuple(
            ConfidenceFact(f"f{index}", "x" * MAX_FACT_VALUE_LENGTH)
            for index in range(MAX_FACTS)
        )
        warnings = tuple(f"w{index}" for index in range(MAX_WARNINGS))
        record = self._record(
            facts=facts,
            warnings=warnings,
            lineage="x" * MAX_SHORT_TEXT_LENGTH,
        )
        self.assertLessEqual(len(record.to_persistence_json().encode()), MAX_SERIALIZED_BYTES)

        summary = EvidenceSummary(
            EvidenceId("evidence-summary"),
            "s" * 64,
            MAX_FACTS,
            MAX_WARNINGS,
            "x" * MAX_EXCERPT_LENGTH,
        )
        self.assertLessEqual(len(summary.to_api_json().encode()), MAX_SERIALIZED_BYTES)

        candidate = Candidate(
            self.target,
            "s" * MAX_SHORT_TEXT_LENGTH,
            FieldValue(("value",)),
            SourceApplicability.WORK,
            "l" * MAX_SHORT_TEXT_LENGTH,
            "c" * MAX_SHORT_TEXT_LENGTH,
        )
        self.assertEqual(len(candidate.source_id), MAX_SHORT_TEXT_LENGTH)

        notice = EvidenceNotice(
            "n" * 64,
            False,
            (self.reference,),
            "a" * MAX_SHORT_TEXT_LENGTH,
        )
        self.assertEqual(len(notice.required_action), MAX_SHORT_TEXT_LENGTH)

        risks = tuple(
            Risk(f"r{index}", RiskSeverity.LOW, False, (self.reference,))
            for index in range(MAX_RISKS)
        )
        self.assertEqual(len(replace(self._proposal(), risks=risks).risks), MAX_RISKS)

    def test_collection_and_projection_size_limits_are_enforced(self) -> None:
        references = tuple(
            replace(self.reference, evidence=EvidenceId(f"evidence-{index}"))
            for index in range(MAX_EVIDENCE_REFERENCES)
        )
        self.assertEqual(len(references), MAX_EVIDENCE_REFERENCES)
        self.assertEqual(
            len(replace(self._proposal(), supporting_evidence=references).supporting_evidence),
            MAX_EVIDENCE_REFERENCES,
        )
        with self.assertRaises(EvidenceValueError):
            replace(self._proposal(), supporting_evidence=references + (self.reference,))

        safe_codes = (
            "author",
            "chapter-count",
            "cover-dimensions",
            "duration",
            "identifier",
            "language",
            "narrator",
            "published-date",
            "publisher",
            "series",
            "series-sequence",
            "title",
        )
        large_detail = EvidenceDetail(
            replace(self.reference, evidence=EvidenceId("evidence-large")),
            self.snapshot,
            self.snapshot_fingerprint,
            "epub",
            tuple(
                ConfidenceFact(code, "x" * MAX_FACT_VALUE_LENGTH)
                for code in safe_codes
            ),
            (),
        )
        packet = ReviewerEvidencePacket(
            self.target,
            self.snapshot,
            self.snapshot_fingerprint,
            tuple(
                replace(
                    large_detail,
                    reference=replace(
                        large_detail.reference,
                        evidence=EvidenceId(f"evidence-{index}"),
                    ),
                )
                for index in range(MAX_PACKET_RECORDS)
            ),
        )
        serialized = packet.to_json()
        self.assertEqual(serialized, packet.to_json())
        self.assertLessEqual(len(serialized.encode()), MAX_SERIALIZED_BYTES)

    def _proposal(self) -> FieldProposal:
        return FieldProposal(
            ProposalId("proposal-one"),
            self.target,
            SnapshotId("snapshot-one"),
            FieldName.TITLE,
            None,
            FieldValue(("Example",)),
            ProposalKind.FILL_EMPTY,
            (self.reference,),
        )

    def _record(
        self,
        *,
        facts: tuple[ConfidenceFact, ...] = (),
        warnings: tuple[str, ...] = (),
        lineage: str | None = None,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            EvidenceId("evidence-one"),
            self.target,
            self.snapshot,
            self.snapshot_fingerprint,
            ExtractorVersion("extractor-v1"),
            SchemaVersion("evidence-v1"),
            "epub",
            facts,
            warnings,
            lineage,
        )

    @staticmethod
    def _target(connection: str, item: str) -> TargetId:
        return TargetId(
            AdapterId("abs"),
            ConnectionId(connection),
            LibraryId("books"),
            ExternalItemId(item),
        )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    unittest.main()
