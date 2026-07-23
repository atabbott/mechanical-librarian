from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import threading
import unittest
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import mechanical_librarian.bootstrap.epub_child as child
from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
    _Rejected,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCapability,
    EpubPackageLimits,
    EpubPackageReport,
    EpubPackageSourceError,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.epub_inspection import EpubInspectionOutcome
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_source import FileReference
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageRequest,
    EpubPackageSource,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId, ConnectionId, ExternalItemId, LibraryId, TargetId,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind, FieldState, FileFacts, LocalClaim, MetadataReport, TargetFacts,
)
from tests.support.epub_packages import EPUB2_PACKAGE, EPUB3_PACKAGE, _opening_epub, _opening_package, _xhtml, epub_bytes


class EpubChildCompositionTests(unittest.TestCase):
    @staticmethod
    def _opening_context(digest: str) -> dict[str, object]:
        return {
            "child_seconds": 4.5,
            "expected_source": {
                "algorithm": "sha256",
                "digest": digest,
                "schema": "local-file-v1",
            },
            "limits": EpubOpeningLimits().to_data(),
            "logical_identity": digest,
        }

    def test_snapshot_digest_mismatch_rejects_before_any_parser_dispatch(self) -> None:
        expected = hashlib.sha256(b"original").hexdigest()
        written: list[EpubOpeningReport] = []
        with (
            patch.object(child, "_install_parent_guard"),
            patch.object(child, "_apply_limits"),
            patch.object(child, "_read_context", return_value=self._opening_context(expected)),
            patch.object(child, "_snapshot_source", return_value=b"changed then restored"),
            patch.object(child, "_write_report", side_effect=written.append),
            patch.object(child, "_inspect_opening") as inspect,
            patch.object(child.signal, "signal"),
            patch.object(child.os, "close"),
        ):
            self.assertEqual(child.main(["10", "11", "123"]), 0)
        inspect.assert_not_called()
        self.assertEqual([value.outcome for value in written], [EpubInspectionOutcome.INTEGRITY_FAILURE])

    def test_matching_snapshot_is_the_immutable_source_used_for_all_parser_reads(self) -> None:
        snapshot = bytearray(b"one bounded immutable snapshot")
        expected = hashlib.sha256(snapshot).hexdigest()
        observed: list[bytes] = []

        def inspect(request, package_request, limits):
            snapshot[:] = b"x" * len(snapshot)
            observed.append(request.source.read_at(0, request.source.length))
            observed.append(package_request.source.read_at(0, package_request.source.length))
            return (
                EpubPackageReport(EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE, EpubPackageCapability.PACKAGE, limits.package),
                EpubOpeningReport(EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE, limits),
            )

        written: list[EpubOpeningReport] = []
        with (
            patch.object(child, "_install_parent_guard"),
            patch.object(child, "_apply_limits"),
            patch.object(child, "_read_context", return_value=self._opening_context(expected)),
            patch.object(child, "_snapshot_source", return_value=bytes(snapshot)),
            patch.object(child, "_write_report", side_effect=written.append),
            patch.object(child, "_inspect_bundle", side_effect=inspect),
            patch.object(child.signal, "signal"),
            patch.object(child.os, "close"),
        ):
            self.assertEqual(child.main(["10", "11", "123"]), 0)
        self.assertEqual(observed, [b"one bounded immutable snapshot"] * 2)
        self.assertEqual([value.outcome for value in written], [EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE])

    def test_unknown_capability_is_rejected_without_package_dispatch(self) -> None:
        context = {
            "capability": "unknown",
            "child_seconds": 4.5,
            "expected_source": {
                "algorithm": "sha256",
                "digest": "a" * 64,
                "schema": "source-v1",
            },
            "limits": EpubPackageLimits().to_data(),
            "logical_identity": "b" * 64,
        }
        written: list[EpubPackageReport] = []
        with (
            patch.object(child, "_install_parent_guard"),
            patch.object(child, "_apply_limits"),
            patch.object(child, "_read_context", return_value=context),
            patch.object(child, "_write_report", side_effect=written.append),
            patch.object(child.signal, "signal"),
            patch.object(child.os, "close"),
            patch.object(EpubPackageExtractor, "extract") as extract,
        ):
            self.assertEqual(child.main(["10", "11", "123"]), 0)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0].outcome, EpubInspectionOutcome.INTERNAL_FAILURE)
        extract.assert_not_called()

    def test_entrypoint_import_does_not_load_network_database_or_secret_implementations(
        self,
    ) -> None:
        source = Path(__file__).parents[2] / "src"
        program = """
import json
import sys
import mechanical_librarian.bootstrap.epub_child
forbidden = (
    "httpx",
    "pydantic",
    "sqlite3",
    "mechanical_librarian.adapters.library_services",
    "mechanical_librarian.adapters.persistence",
    "mechanical_librarian.infrastructure.security.target_secrets",
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(forbidden))))
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", program],
            cwd="/",
            env={"PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout), [])

    def test_linux_guard_rejects_a_child_already_reparented_before_installation(
        self,
    ) -> None:
        with (
            patch.object(child.sys, "platform", "linux"),
            patch.object(child.os, "getppid", return_value=1),
            patch.object(child.os, "_exit", side_effect=SystemExit(70)) as exit_process,
        ):
            with self.assertRaises(SystemExit):
                child._install_parent_guard(10, threading.Event(), 999)
        exit_process.assert_called_once_with(70)


class EpubChildArchiveConstructionTests(unittest.TestCase):
    def test_constructor_timeout_preserves_typed_outcome(self) -> None:
        self.assertEqual(
            self._constructed_outcome(_Rejected(EpubInspectionOutcome.TIMEOUT)),
            EpubInspectionOutcome.TIMEOUT,
        )

    def test_constructor_source_failure_remains_unavailable(self) -> None:
        self.assertEqual(
            self._constructed_outcome(EpubPackageSourceError("read failed")),
            EpubInspectionOutcome.UNAVAILABLE_SOURCE,
        )

    @staticmethod
    def _constructed_outcome(error: Exception) -> EpubInspectionOutcome:
        extractor = Mock()
        extractor.preflight.return_value = None
        with (
            patch.object(child, "EpubPackageExtractor", return_value=extractor),
            patch.object(child, "_SourceIO", return_value=object()),
            patch.object(child.zipfile, "ZipFile", side_effect=error),
        ):
            return child._inspect_opening(object(), object(), EpubOpeningLimits()).outcome


class EpubChildRearSpineTests(unittest.TestCase):
    @staticmethod
    def _request(value: bytes, *, cancelled=lambda: False, deadline_seconds: float = 30.0):
        source = Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest())
        limits = EpubOpeningLimits()
        return EpubPackageRequest(
            TargetId(AdapterId("local-epub"), ConnectionId("local"), LibraryId("local"), ExternalItemId("0" * 64)),
            FileReference("local", "fixture.epub"),
            EpubPackageSource(len(value), lambda offset, size: value[offset : offset + size]),
            source, EpubPackageCapability.OPENING_MATTER, limits.package,
            datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
            time.monotonic() + deadline_seconds,
            EpubPackageCancellation("rear-test", cancelled),
        )

    def _bundle(self, tail: bytes):
        items = tuple((f"slot{index}", f"slot{index}.xhtml", "application/xhtml+xml", "") for index in range(9))
        package = _opening_package(items=items, spine=tuple(identity for identity, *_ in items))
        value = _opening_epub(
            package=package,
            entries=tuple(
                (f"EPUB/slot{index}.xhtml", tail if index == 8 else _xhtml("<p>ordinary synthetic prose</p>"), 8)
                for index in range(9)
            ),
        )
        request = self._request(value)
        package_request = EpubPackageRequest(request.target, request.file, request.source, request.expected_source, EpubPackageCapability.PACKAGE, request.limits, request.deadline, request.monotonic_deadline, request.cancellation)
        return request, child._inspect_bundle(request, package_request, EpubOpeningLimits())

    def _package_facts(self, package: bytes):
        value = epub_bytes(package=package)
        request = self._request(value)
        package_request = EpubPackageRequest(request.target, request.file, request.source, request.expected_source, EpubPackageCapability.PACKAGE, request.limits, request.deadline, request.monotonic_deadline, request.cancellation)
        report = EpubPackageExtractor().extract(package_request)
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIsNotNone(report.source_fingerprint)
        return child._package_facts(report, report.source_fingerprint)

    def test_package_facts_projects_only_recognized_valid_isbns_and_normalizes_titles(self) -> None:
        package = EPUB3_PACKAGE.replace(
            b'<dc:title id="title">A Synthetic Book</dc:title>',
            "<dc:title id=\"main\">  Cafe\u0301   Registry  </dc:title>".encode(),
        ).replace(
            b'<dc:identifier id="pub-id">9780306406157</dc:identifier>',
            b'<dc:identifier id="pub-id">urn:uuid:00000000-0000-4000-8000-000000000007</dc:identifier>',
        ).replace(
            b"</metadata>",
            b'''<dc:title id="subtitle">  The   Sequel  </dc:title>
    <meta refines="#subtitle" property="title-type">subtitle</meta>
    <dc:identifier>10.1234/synthetic.1</dc:identifier>
    <dc:identifier>ASIN:B012345678</dc:identifier>
    <dc:identifier>urn:synthetic:alternate</dc:identifier>
    <dc:identifier>9780306406157</dc:identifier></metadata>''',
        )
        facts = self._package_facts(package)
        self.assertEqual(
            [(fact.field.value, fact.canonical_value) for fact in facts if fact.field.value in {"title", "subtitle", "isbn"}],
            [("title", "Caf\u00e9 Registry"), ("subtitle", "The Sequel"), ("isbn", "9780306406157")],
        )

    def test_package_facts_mark_missing_invalid_date_and_language_canonicals_malformed(self) -> None:
        package = EPUB3_PACKAGE.replace(
            b"<dc:language>en-GB</dc:language>", b"<dc:language>not a language!</dc:language>"
        ).replace(
            b"</metadata>", b"<dc:date>2026-02-30</dc:date></metadata>"
        )
        facts = self._package_facts(package)
        states = {fact.field.value: fact.state for fact in facts}
        self.assertEqual(states["publication_date"], "malformed")
        self.assertEqual(states["language"], "malformed")

    def test_package_oversized_sentinel_reaches_report_as_noncanonical_oversized_evidence(self) -> None:
        package = EPUB3_PACKAGE.replace(b"A Synthetic Book", b"x" * 4_097)
        facts = self._package_facts(package)
        title = next(fact for fact in facts if fact.field.value == "title")
        self.assertTrue(title.observed_value.startswith("oversized-source-text-sha256:"))
        self.assertIsNone(title.canonical_value)
        self.assertEqual(title.state, "oversized")
        claim = LocalClaim.create(
            kind=ClaimKind(title.field.value), observed=title.observed_value,
            canonical=title.canonical_value, excerpt=title.supporting_context,
            applicability=title.media, checksum=title.checksum,
            applicability_basis=title.classification_basis, basis=title.basis,
            state=FieldState(title.state),
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(claim,),
            local_reasons=("package_metadata_title",), providers=(),
        )
        self.assertEqual(report.field_results[0].state, FieldState.OVERSIZED)

    def test_package_sentinels_and_explicit_invalid_isbns_preserve_only_the_admitted_forms(self) -> None:
        exact = "unsafe-source-text-sha256:" + "a" * 64
        nonexact = "unsafe-source-text-sha256:" + "A" * 64
        for value, expected_state, expected_canonical in ((exact, "unsupported", None), (nonexact, "valid", nonexact)):
            with self.subTest(value=value):
                facts = self._package_facts(EPUB3_PACKAGE.replace(b"A Synthetic Book", value.encode()))
                title = next(fact for fact in facts if fact.field.value == "title")
                self.assertEqual((title.state, title.canonical_value), (expected_state, expected_canonical))
        epub2 = EPUB2_PACKAGE.replace(b"0-306-40615-2", b"0-306-40615-3")
        urn = EPUB3_PACKAGE.replace(b"</metadata>", b"<dc:identifier>urn:isbn:9780306406158</dc:identifier></metadata>")
        for package in (epub2, urn):
            with self.subTest(package=package[:32]):
                invalid = [fact for fact in self._package_facts(package) if fact.field.value == "isbn" and fact.canonical_value is None]
                self.assertEqual([(fact.checksum, fact.state) for fact in invalid], [("invalid", "malformed")])
        non_isbn = EPUB3_PACKAGE.replace(b'<dc:identifier id="pub-id">9780306406157</dc:identifier>', b'<dc:identifier id="pub-id">urn:uuid:00000000-0000-4000-8000-000000000007</dc:identifier>')
        self.assertEqual([fact for fact in self._package_facts(non_isbn) if fact.field.value == "isbn"], [])

    def test_package_role_projection_bounds_unsafe_and_oversized_values_for_report_replay(self) -> None:
        unsafe = "unsafe-source-text-sha256:" + "a" * 64
        oversized = "oversized-source-text-sha256:" + "a" * 64
        for role, sentinel_prefix in (("author", None), ("y" * 64, None), (unsafe, None), (oversized, None), ("/Users/private/role", "unsafe"), ("x" * 65, "oversized")):
            with self.subTest(role=role):
                facts = self._package_facts(EPUB2_PACKAGE.replace(b'opf:role="aut"', f'opf:role="{role}"'.encode()))
                creator = next(fact for fact in facts if fact.field.value == "creator")
                if sentinel_prefix is None:
                    self.assertEqual(creator.role, role)
                else:
                    self.assertRegex(creator.role or "", rf"^{sentinel_prefix}-source-text-sha256:[0-9a-f]{{64}}$")
                claim = LocalClaim.create(
                    kind=ClaimKind.CREATOR, observed=creator.observed_value,
                    canonical=creator.canonical_value, excerpt=creator.supporting_context,
                    applicability=creator.media, checksum=creator.checksum,
                    applicability_basis=creator.classification_basis, basis=creator.basis,
                    state=FieldState(creator.state), role=creator.role,
                )
                report = MetadataReport.create(
                    target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
                    file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(claim,),
                    local_reasons=("package_metadata_creator",), providers=(),
                )
                self.assertEqual(MetadataReport.from_json(report.to_json()).local_claims[0].role, creator.role)

    def test_rear_pool_reads_only_tail_evidence_with_marker_and_explicit_cues(self) -> None:
        request, (package, opening, _) = self._bundle(_xhtml('<p>Copyright 2026</p><h1 epub:type="fulltitle">Rear Clock</h1><p>by Ada Example</p><p>ISBN 9780306406157</p>'))
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            facts = child._rear_facts(request, package, opening, archive)
        self.assertEqual([(fact.field.value, fact.canonical_value) for fact in facts], [("title", "Rear Clock"), ("creator", "Ada Example"), ("isbn", "9780306406157")])

    def test_rear_pool_does_not_replace_a_tail_slot_without_marker(self) -> None:
        request, (package, opening, _) = self._bundle(_xhtml('<h1 epub:type="fulltitle">Unmarked Tail</h1><p>ISBN 9780306406157</p>'))
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            self.assertEqual(child._rear_facts(request, package, opening, archive), ())

    def test_rear_pool_suppresses_unsafe_cues_without_losing_safe_facts(self) -> None:
        for unsafe in ("/Users/private/title", "Authorization: Bearer abcdefgh"):
            with self.subTest(unsafe=unsafe):
                request, (package, opening, _) = self._bundle(_xhtml(f'<p>Copyright 2026</p><h1 epub:type="fulltitle">{unsafe}</h1><p>by Ada Example</p>'))
                with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
                    facts = child._rear_facts(request, package, opening, archive)
                self.assertEqual([(fact.field.value, fact.canonical_value) for fact in facts], [("creator", "Ada Example")])

    def test_rear_pool_caps_combined_navigation_opening_and_rear_resources_at_nine(self) -> None:
        items = (("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),) + tuple(
            (f"slot{index}", f"slot{index}.xhtml", "application/xhtml+xml", "") for index in range(9)
        )
        package = _opening_package(items=items, spine=tuple(f"slot{index}" for index in range(9)))
        value = _opening_epub(
            package=package,
            entries=(("EPUB/nav.xhtml", _xhtml("<nav epub:type=\"toc\"></nav>"), 8),) + tuple(
                (f"EPUB/slot{index}.xhtml", _xhtml(f'<p>Copyright 2026</p><h1 epub:type="fulltitle">Slot {index}</h1>'), 8)
                for index in range(9)
            ),
        )
        request = self._request(value)
        package_request = EpubPackageRequest(request.target, request.file, request.source, request.expected_source, EpubPackageCapability.PACKAGE, request.limits, request.deadline, request.monotonic_deadline, request.cancellation)
        package_report, opening_report, _ = child._inspect_bundle(request, package_request, EpubOpeningLimits())
        self.assertEqual(sum(resource.opened for resource in opening_report.resources), 6)
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            facts = child._rear_facts(request, package_report, opening_report, archive)
        self.assertEqual([(fact.field.value, fact.canonical_value) for fact in facts], [("title", "Slot 8"), ("title", "Slot 7"), ("title", "Slot 6")])
        self.assertEqual(sum(resource.opened for resource in opening_report.resources) + len(facts), 9)

    def test_rear_pool_counts_duplicate_navigation_open_operations_against_budget(self) -> None:
        items = (("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),) + tuple(
            (f"slot{index}", f"slot{index}.xhtml", "application/xhtml+xml", "") for index in range(9)
        )
        package = _opening_package(items=items, spine=("nav",) + tuple(f"slot{index}" for index in range(9)))
        value = _opening_epub(
            package=package,
            entries=(("EPUB/nav.xhtml", _xhtml('<nav epub:type="toc"></nav>'), 8),) + tuple(
                (f"EPUB/slot{index}.xhtml", _xhtml(f'<p>Copyright 2026</p><h1 epub:type="fulltitle">Slot {index}</h1>'), 8)
                for index in range(9)
            ),
        )
        request = self._request(value)
        package_request = EpubPackageRequest(request.target, request.file, request.source, request.expected_source, EpubPackageCapability.PACKAGE, request.limits, request.deadline, request.monotonic_deadline, request.cancellation)
        package_report, opening_report, _ = child._inspect_bundle(request, package_request, EpubOpeningLimits())
        self.assertEqual(opening_report.usage.opened_resources, 6)
        self.assertEqual(len({resource.resource_identity for resource in opening_report.resources if resource.opened}), 5)
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            facts = child._rear_facts(request, package_report, opening_report, archive)
        self.assertEqual([(fact.field.value, fact.canonical_value) for fact in facts], [("title", "Slot 8"), ("title", "Slot 7"), ("title", "Slot 6")])
        self.assertLessEqual(opening_report.usage.opened_resources + len(facts), 9)

    def test_rear_pool_preserves_cancellation_terminal(self) -> None:
        value = _opening_epub(
            package=_opening_package(items=(("tail", "tail.xhtml", "application/xhtml+xml", ""),), spine=("tail",)),
            entries=(("EPUB/tail.xhtml", _xhtml('<p>Copyright 2026</p>'), 8),),
        )
        initial = self._request(value)
        package_request = EpubPackageRequest(initial.target, initial.file, initial.source, initial.expected_source, EpubPackageCapability.PACKAGE, initial.limits, initial.deadline, initial.monotonic_deadline, initial.cancellation)
        package, opening, _ = child._inspect_bundle(initial, package_request, EpubOpeningLimits())
        request = self._request(value, cancelled=lambda: True)
        with self.assertRaises(child.opening_parser._AggregateRejected) as raised:
            with zipfile.ZipFile(child._SourceIO(initial), "r") as archive:
                child._rear_facts(request, package, opening, archive)
        self.assertEqual(raised.exception.outcome, EpubInspectionOutcome.CANCELLATION)


if __name__ == "__main__":
    unittest.main()
