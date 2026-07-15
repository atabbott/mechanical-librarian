from __future__ import annotations

import hashlib
import html
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
import unittest

from hypothesis import given, settings, strategies as st

from mechanical_librarian.adapters.evidence_extractors.epub_opening import EpubOpeningExtractor
from mechanical_librarian.adapters.evidence_extractors.epub_package import EpubPackageExtractor
from mechanical_librarian.domain.epub_opening import (
    EpubOpeningLimits,
    EpubOpeningReport,
    OpeningField,
    OpeningParseMode,
    OpeningResourceDisposition,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
)
from mechanical_librarian.domain.inventory import FileReference
from mechanical_librarian.domain.local_evidence import (
    LocalEvidenceCancellation,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceRequest,
    LocalEvidenceSource,
    ObservationApplicability,
    local_file_lineage,
)
from tests.support.epub_packages import (
    MATERIALIZED_RECIPES,
    _opening_epub,
    _opening_package,
    _xhtml,
    epub_bytes,
    opening_active_content_inert,
    opening_claims_complete,
    opening_claim_families,
    opening_epub3_landmark_conflict,
    opening_no_applicable,
    opening_recovered_html,
    opening_resource_limit,
    opening_strict_xhtml,
    opening_title_isbn_applicability,
)


def _request(value: bytes, limits: EpubOpeningLimits | None = None) -> LocalEvidenceRequest:
    selected = EpubOpeningLimits() if limits is None else limits
    source = Fingerprint(
        "sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()
    )
    return LocalEvidenceRequest(
        TargetId(
            AdapterId("local-epub"),
            ConnectionId("local"),
            LibraryId("local"),
            ExternalItemId("0" * 64),
        ),
        FileReference("local", "0.epub"),
        LocalEvidenceSource(
            len(value), lambda offset, size: value[offset : offset + size]
        ),
        source,
        LocalEvidenceCapability.OPENING_MATTER,
        selected.package,
        datetime.now(timezone.utc) + timedelta(seconds=30),
        time.monotonic() + 30.0,
        LocalEvidenceCancellation("opening-test", lambda: False),
    )


def _extract(
    value: bytes, limits: EpubOpeningLimits | None = None
) -> EpubOpeningReport:
    selected = EpubOpeningLimits() if limits is None else limits
    request = _request(value, selected)
    package_request = LocalEvidenceRequest(
        request.target,
        request.file,
        request.source,
        request.expected_source,
        LocalEvidenceCapability.PACKAGE,
        request.limits,
        request.deadline,
        request.monotonic_deadline,
        request.cancellation,
    )
    package = EpubPackageExtractor().extract(package_request)
    return EpubOpeningExtractor().extract(request, selected, package)


class EpubOpeningTests(unittest.TestCase):
    def _single(self, body: bytes, *, media_type: str = "application/xhtml+xml") -> bytes:
        package = _opening_package(
            items=(("opening", "opening.xhtml", media_type, ""),),
            spine=("opening",),
        )
        return _opening_epub(
            package=package,
            entries=(("EPUB/opening.xhtml", body, zipfile.ZIP_DEFLATED),),
        )

    def test_materialized_opening_registry_has_declared_top_level_outcomes(self) -> None:
        for name, recipe in MATERIALIZED_RECIPES.items():
            if not name.startswith("opening_"):
                continue
            with self.subTest(recipe=name):
                report = _extract(recipe())
                expected = (
                    LocalEvidenceOutcome.RESOURCE_LIMIT
                    if name == "opening_resource_limit"
                    else LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE
                    if name in {"opening_no_applicable", "opening_invalid_encoding"}
                    else LocalEvidenceOutcome.SUCCESS
                )
                self.assertEqual(report.outcome, expected)

    def test_citations_are_exact_versioned_path_free_and_round_trip_canonically(self) -> None:
        report = _extract(opening_claims_complete())
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertEqual(
            {claim.field for claim in report.claims},
            {
                OpeningField.TITLE,
                OpeningField.CONTRIBUTOR,
                OpeningField.CONTRIBUTOR_ROLE,
                OpeningField.EDITION,
                OpeningField.IMPRINT,
                OpeningField.IDENTIFIER,
            },
        )
        for claim in report.claims:
            citation = claim.citation
            self.assertEqual(
                citation.projection_end - citation.projection_start,
                len(citation.exact_excerpt),
            )
            self.assertEqual(
                citation.excerpt_digest,
                hashlib.sha256(citation.exact_excerpt.encode("utf-8")).hexdigest(),
            )
            self.assertNotIn(".xhtml", report.to_json())
            self.assertNotIn("/Users/", report.to_json())
            self.assertEqual(
                citation.same_file_lineage,
                local_file_lineage(report.source_fingerprint),
            )
        self.assertEqual(
            report.same_file_lineage,
            local_file_lineage(report.source_fingerprint),
        )
        self.assertEqual(EpubOpeningReport.from_json(report.to_json()), report)

    def test_plain_chapter_heading_never_becomes_a_book_title(self) -> None:
        report = _extract(self._single(_xhtml("<h1>Chapter One</h1><p>Story prose.</p>")))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertFalse(report.claims)

    def test_landmark_terms_require_landmarks_ancestry_and_epub_namespace(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("real", "real.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("real",),
        )
        nav = _xhtml(
            '<nav epub:type="toc"><a epub:type="titlepage" href="fake.xhtml">Fake</a></nav>'
            '<nav epub:type="landmarks"><a type="titlepage" role="titlepage" itemprop="titlepage" href="fake.xhtml">Still fake</a></nav>'
        )
        value = _opening_epub(
            package=package,
            entries=(
                ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">False Title</h1>'), zipfile.ZIP_DEFLATED),
                ("EPUB/real.xhtml", _xhtml('<section epub:type="titlepage"><h1>Real Title</h1></section>'), zipfile.ZIP_DEFLATED),
            ),
        )
        report = _extract(value)
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Real Title"],
        )

    def test_projection_preserves_nested_text_void_html_and_body_boundary(self) -> None:
        nested = _extract(
            self._single(_xhtml('<h1 epub:type="fulltitle">The <em>Nested</em> Clock</h1>'))
        )
        self.assertEqual(
            next(claim for claim in nested.claims if claim.field is OpeningField.TITLE).raw_value,
            "The Nested Clock",
        )
        recovered = _extract(
            self._single(
                b'<!doctype html><html><body><input type="hidden"><br><meta charset="utf-8"><h1 epub:type="fulltitle">Visible Title</h1></body></html>',
                media_type="text/html",
            )
        )
        self.assertIn("Visible Title", {claim.raw_value for claim in recovered.claims})
        bounded_voids = _extract(
            self._single(
                ("<!doctype html><html><body>" + "<br>" * 100 + '<h1 epub:type="fulltitle">After Voids</h1></body></html>').encode(),
                media_type="text/html",
            )
        )
        self.assertEqual(bounded_voids.outcome, LocalEvidenceOutcome.SUCCESS)
        body = _extract(
            self._single(
                _xhtml('<h1 epub:type="fulltitle">Front Title</h1><main epub:type="bodymatter"><p>eBook ISBN 9780306406157</p></main>')
            )
        )
        self.assertEqual({claim.field for claim in body.claims}, {OpeningField.TITLE})
        structural_break = _extract(
            self._single(_xhtml('<h1 epub:type="fulltitle">The<br/>Clock</h1>'))
        )
        self.assertIn("The Clock", {claim.raw_value for claim in structural_break.claims})
        empty_body = _extract(
            self._single(
                _xhtml('<h1 epub:type="fulltitle">Front</h1><hr epub:type="bodymatter"/><p>eBook ISBN 9780306406157</p>')
            )
        )
        self.assertEqual({claim.field for claim in empty_body.claims}, {OpeningField.TITLE})
        nested_body = _extract(
            self._single(
                _xhtml('<h1 epub:type="fulltitle">Front<span epub:type="bodymatter">Story</span></h1>')
            )
        )
        self.assertEqual(
            nested_body.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE
        )
        self.assertIn("body_transition_reached", nested_body.warnings)
        implied = _extract(
            self._single(
                ("<!doctype html><html><body>" + "<p><span>x" * 40 + '<h1 epub:type="fulltitle">Implied Close Title</h1></body></html>').encode(),
                media_type="text/html",
            )
        )
        self.assertEqual(implied.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn("Implied Close Title", {claim.raw_value for claim in implied.claims})

    def test_foreign_inclusion_is_rejected_without_losing_a_safe_sibling(self) -> None:
        package = _opening_package(
            items=(
                ("hostile", "hostile.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("hostile", "safe"),
        )
        hostile = b'''<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:xi="http://www.w3.org/2001/XInclude"><body><xi:include href="https://example.invalid"><xi:fallback><h1 epub:type="fulltitle">Injected</h1></xi:fallback></xi:include></body></html>'''
        value = _opening_epub(
            package=package,
            entries=(
                ("EPUB/hostile.xhtml", hostile, zipfile.ZIP_DEFLATED),
                ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe Sibling</h1>'), zipfile.ZIP_DEFLATED),
            ),
        )
        report = _extract(value)
        self.assertNotIn("Injected", report.to_json())
        self.assertIn("Safe Sibling", {claim.raw_value for claim in report.claims})
        self.assertIn(OpeningResourceDisposition.MALFORMED, {item.disposition for item in report.resources})

        for injected in (
            b'''<?xml version="1.0"?><include xmlns="http://www.w3.org/2001/XInclude"><fallback><h1 xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" epub:type="fulltitle">Default Injected</h1></fallback></include>''',
            b'''<!doctype html><html xmlns:q="http://www.w3.org/2001/XInclude"><body><q:include><q:fallback><h1 epub:type="fulltitle">Alias Injected</h1></q:fallback></q:include></body></html>''',
            b'''<!doctype html><html xmlns:t="http://www.w3.org/1999/XSL/Transform"><body><t:stylesheet><h1 epub:type="fulltitle">Transform Injected</h1></t:stylesheet></body></html>''',
            b'''<!doctype html><html xmlns:x="http://www.w3.org/1999/xhtml"><body><x:script><h1 epub:type="fulltitle">Script Injected</h1></x:script><h1 epub:type="fulltitle">Visible Alias Safe</h1></body></html>''',
        ):
            with self.subTest(injected=injected[:40]):
                guarded = _extract(self._single(injected, media_type="text/html"))
                self.assertNotIn("Injected", guarded.to_json())

    def test_recovery_resolves_qualified_active_start_and_end_tags(self) -> None:
        content = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:x="http://www.w3.org/1999/xhtml" xmlns:s="http://www.w3.org/2000/svg" xmlns:epub="http://www.idpf.org/2007/ops"><body><s:script><x:h1 epub:type="fulltitle">Injected SVG Script</x:h1></s:script><x:h1 epub:type="fulltitle">Safe QName Sibling</x:h1></body>'''
        for media_type in ("application/xhtml+xml", "text/html"):
            with self.subTest(media_type=media_type):
                report = _extract(self._single(content, media_type=media_type))
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Safe QName Sibling"],
                )

    def test_extension_vocabulary_terms_never_gain_controlled_semantics(self) -> None:
        prefixed = _extract(
            self._single(
                _xhtml('<h1 epub:type="vendor:fulltitle">Vendor False Title</h1>')
            )
        )
        self.assertEqual(prefixed.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE)

        boundary = _extract(
            self._single(
                _xhtml('<h1 epub:type="fulltitle">Front<span epub:type="vendor:bodymatter">Story</span></h1><p epub:type="vendor:preview">eBook ISBN 9780306406157</p>')
            )
        )
        self.assertIn("FrontStory", {claim.raw_value for claim in boundary.claims})
        self.assertIn(OpeningField.IDENTIFIER, {claim.field for claim in boundary.claims})

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("real", "real.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("real",),
        )
        navigation = _xhtml('<nav epub:type="vendor:landmarks"><a epub:type="titlepage" href="fake.xhtml">False</a></nav>')
        selected = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Vendor Selected</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/real.xhtml", _xhtml('<h1 epub:type="fulltitle">Spine Selected</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertNotIn("Vendor Selected", selected.to_json())
        self.assertIn("Spine Selected", {claim.raw_value for claim in selected.claims})

    def test_same_element_isbn_labels_are_scoped_per_match(self) -> None:
        report = _extract(
            self._single(
                _xhtml('<section epub:type="copyright-page"><p>eBook ISBN 9780306406157; Paperback ISBN 9781861972712</p></section>')
            )
        )
        identifiers = [claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER]
        self.assertEqual([claim.applicability_label for claim in identifiers], ["ebook", "paperback"])
        self.assertIn("eBook", identifiers[0].citation.exact_excerpt)
        self.assertIn("Paperback", identifiers[1].citation.exact_excerpt)
        suffixes = _extract(
            self._single(
                _xhtml('<section epub:type="copyright-page"><p>ISBN 9780306406157 (eBook); ISBN 9781861972712 (Paperback)</p></section>')
            )
        )
        self.assertEqual(
            [
                claim.applicability_label
                for claim in suffixes.claims
                if claim.field is OpeningField.IDENTIFIER
            ],
            ["ebook", "paperback"],
        )
        for separator in (", ", " / ", " • "):
            with self.subTest(separator=separator):
                separated = _extract(
                    self._single(
                        _xhtml(
                            '<section epub:type="copyright-page"><p>'
                            f'ISBN 9780306406157 (eBook){separator}'
                            'ISBN 9781861972712 (Paperback)</p></section>'
                        )
                    )
                )
                self.assertEqual(
                    [
                        claim.applicability_label
                        for claim in separated.claims
                        if claim.field is OpeningField.IDENTIFIER
                    ],
                    ["ebook", "paperback"],
                )
        for false_label in ("Notebook", "digitalis", "hardcovering"):
            with self.subTest(false_label=false_label):
                unlabelled = _extract(
                    self._single(
                        _xhtml(
                            '<section epub:type="copyright-page"><p>'
                            f'{false_label} ISBN 9780306406157</p></section>'
                        )
                    )
                )
                identifier = next(
                    claim
                    for claim in unlabelled.claims
                    if claim.field is OpeningField.IDENTIFIER
                )
                self.assertEqual(identifier.applicability_label, "unlabelled")
                self.assertIs(identifier.applicability, ObservationApplicability.UNKNOWN)

        preceding = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    '<p>Electronic edition</p><p>ISBN 9780306406157</p>'
                    '</section>'
                )
            )
        )
        preceding_identifier = next(
            claim
            for claim in preceding.claims
            if claim.field is OpeningField.IDENTIFIER
        )
        self.assertEqual(preceding_identifier.applicability_label, "ebook")
        self.assertIn(
            "Electronic edition", preceding_identifier.citation.exact_excerpt
        )
        self.assertIn(
            preceding_identifier.raw_value,
            preceding_identifier.citation.exact_excerpt,
        )

        conflicting = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    '<p>Electronic paperback edition</p>'
                    '<p>ISBN 9780306406157</p>'
                    '</section>'
                )
            )
        )
        conflicting_identifier = next(
            claim
            for claim in conflicting.claims
            if claim.field is OpeningField.IDENTIFIER
        )
        self.assertEqual(conflicting_identifier.applicability_label, "unlabelled")
        self.assertIs(
            conflicting_identifier.applicability,
            ObservationApplicability.UNKNOWN,
        )
        self.assertIn(
            "Electronic paperback",
            conflicting_identifier.citation.exact_excerpt,
        )

    def test_canonical_envelope_rejects_forged_versions_lineage_and_usage(self) -> None:
        report = _extract(opening_claims_complete())
        for mutate in (
            lambda value: value["claims"][0]["citation"].__setitem__("extractor_version", "forged-v1"),
            lambda value: value["limits"].__setitem__("excerpt_characters", 1),
            lambda value: value.__setitem__("same_file_lineage", "0" * 64),
            lambda value: (
                value.__setitem__("same_file_lineage", "0" * 64),
                [
                    claim["citation"].__setitem__("same_file_lineage", "0" * 64)
                    for claim in value["claims"]
                ],
            ),
            lambda value: value["usage"].__setitem__("selected_documents", 999),
            lambda value: value["usage"].__setitem__("opened_resources", 6),
            lambda value: value["resources"][0].update(
                {"disposition": "unsupported_media", "opened": True, "parse_mode": "strict"}
            ),
            lambda value: value["resources"][0].__setitem__(
                "selection_path", ["epub3_navigation"]
            ),
            lambda value: (
                value["resources"][0].__setitem__(
                    "selection_path", ["epub3_landmark", "invented_term"]
                ),
                [
                    claim["citation"].__setitem__(
                        "selection_path", ["epub3_landmark", "invented_term"]
                    )
                    for claim in value["claims"]
                    if claim["citation"]["resource_identity"]
                    == value["resources"][0]["resource_identity"]
                ],
            ),
            lambda value: value["claims"][0]["citation"].__setitem__(
                "selection_path", ["epub2_guide", "title_page"]
            ),
            lambda value: value["usage"].update({name: 0 for name in value["usage"]}),
            lambda value: (value.__setitem__("claims", []), value.__setitem__("resources", []), value.__setitem__("same_file_lineage", None), value["usage"].update({name: 0 for name in value["usage"]})),
            lambda value: value["claims"][0]["citation"].__setitem__("recovery_warnings", ["recovered_markup"]),
        ):
            value = json.loads(report.to_json())
            mutate(value)
            with self.assertRaises(EvidenceValueError):
                EpubOpeningReport.from_json(
                    json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                )

        recovered = json.loads(_extract(opening_recovered_html()).to_json())
        recovered["claims"][0]["citation"]["recovery_warnings"] = []
        with self.assertRaises(EvidenceValueError):
            EpubOpeningReport.from_json(
                json.dumps(recovered, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )

        duplicate = json.loads(report.to_json())
        duplicate["resources"].append(dict(duplicate["resources"][0]))
        duplicate["usage"]["selected_documents"] += 1
        duplicate["usage"]["opened_resources"] += int(
            duplicate["resources"][0]["opened"]
        )
        duplicate["usage"][f'{duplicate["resources"][0]["parse_mode"]}_documents'] += 1
        with self.assertRaises(EvidenceValueError):
            EpubOpeningReport.from_json(
                json.dumps(duplicate, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )

        failed = json.loads(_extract(opening_resource_limit()).to_json())
        failed["usage"]["opened_resources"] = 1
        with self.assertRaises(EvidenceValueError):
            EpubOpeningReport.from_json(
                json.dumps(failed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )

        no_claims = json.loads(
            _extract(self._single(_xhtml("<p>ordinary front matter</p>"))).to_json()
        )
        self.assertEqual(no_claims["outcome"], "no_applicable_evidence")
        no_claims["usage"]["strict_documents"] = 0
        with self.assertRaises(EvidenceValueError):
            EpubOpeningReport.from_json(
                json.dumps(no_claims, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )

    def test_excerpt_profile_and_recovery_warning_are_enforced(self) -> None:
        limited = _extract(opening_claims_complete(), EpubOpeningLimits(excerpt_characters=1))
        self.assertTrue(limited.claims)
        self.assertTrue(all(len(claim.raw_value) == 1 for claim in limited.claims))
        recovered = _extract(opening_recovered_html())
        self.assertTrue(
            all(claim.citation.recovery_warnings == ("recovered_markup",) for claim in recovered.claims)
        )

    def test_every_controlled_claim_family_and_contributor_role_is_recognized(self) -> None:
        report = _extract(opening_claim_families())
        self.assertEqual({claim.field for claim in report.claims}, set(OpeningField))
        self.assertEqual(
            {
                claim.normalized_value
                for claim in report.claims
                if claim.field is OpeningField.CONTRIBUTOR_ROLE
            },
            {"author", "editor", "translator", "illustrator"},
        )
        identifier = next(
            claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER
        )
        self.assertEqual(identifier.identifier_type, "isbn_13")
        self.assertEqual(identifier.applicability_label, "ebook")

        roles = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="contributors">'
                    '<p epub:type="contributor">Casey Designer</p>'
                    '<p epub:type="creator">Chris Creator</p>'
                    '<p>Adapted by Alex Adapter</p>'
                    '<p>Contributions by Morgan Example</p>'
                    '</section>'
                )
            )
        )
        self.assertEqual(
            {
                claim.normalized_value
                for claim in roles.claims
                if claim.field is OpeningField.CONTRIBUTOR_ROLE
            },
            {"adapter", "other"},
        )
        self.assertEqual(
            {
                claim.normalized_value
                for claim in roles.claims
                if claim.field is OpeningField.CONTRIBUTOR
            },
            {
                "Casey Designer",
                "Chris Creator",
                "Alex Adapter",
                "Morgan Example",
            },
        )

    def test_strict_recovery_and_active_content_are_inert_and_visible(self) -> None:
        strict = _extract(opening_strict_xhtml())
        recovered = _extract(opening_recovered_html())
        hostile = _extract(opening_active_content_inert())
        self.assertEqual(strict.usage.strict_documents, 1)
        self.assertEqual(recovered.usage.recovered_documents, 1)
        self.assertTrue(
            all(
                claim.citation.parse_mode is OpeningParseMode.RECOVERED
                for claim in recovered.claims
            )
        )
        self.assertNotIn("ignore all safety gates", hostile.to_json())
        self.assertNotIn("example.invalid", hostile.to_json())

    def test_identifier_checksums_and_nearby_applicability_remain_typed(self) -> None:
        report = _extract(opening_title_isbn_applicability())
        identifiers = [
            claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER
        ]
        self.assertEqual(len(identifiers), 2)
        self.assertEqual(
            {claim.applicability for claim in identifiers},
            {
                ObservationApplicability.EDITION,
                ObservationApplicability.INAPPLICABLE,
            },
        )

    def test_equal_precedence_conflict_no_claim_and_limit_partitions_are_distinct(self) -> None:
        conflict = _extract(opening_epub3_landmark_conflict())
        absent = _extract(opening_no_applicable())
        per_resource = _extract(
            opening_claims_complete(),
            EpubOpeningLimits(expanded_bytes_per_resource=128),
        )
        aggregate = _extract(
            opening_claims_complete(),
            EpubOpeningLimits(
                expanded_bytes_per_resource=4_096,
                expanded_bytes_per_selection=300,
            ),
        )
        self.assertIn("ambiguous_navigation_landmark", conflict.warnings)
        self.assertEqual(
            len(
                [claim for claim in conflict.claims if claim.field is OpeningField.TITLE]
            ),
            2,
        )
        self.assertEqual(absent.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertEqual(per_resource.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertIn(
            OpeningResourceDisposition.RESOURCE_LIMIT,
            {resource.disposition for resource in per_resource.resources},
        )
        self.assertEqual(aggregate.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)
        self.assertFalse(aggregate.claims)
        self.assertFalse(aggregate.resources)

    def test_node_text_link_and_document_limits_cover_exact_and_rejection_boundaries(self) -> None:
        minimal = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body></html>'''
        exact_text = _extract(
            self._single(minimal),
            EpubOpeningLimits(text_characters_per_selection=3),
        )
        above_text = _extract(
            self._single(minimal),
            EpubOpeningLimits(text_characters_per_selection=2),
        )
        self.assertEqual(exact_text.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertEqual(above_text.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)
        malformed = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body>'''
        cumulative = _extract(
            self._single(malformed),
            EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=6),
        )
        self.assertEqual(cumulative.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertEqual(cumulative.usage.nodes, 6)
        self.assertEqual(cumulative.usage.text_characters, 6)
        self.assertEqual(
            _extract(
                self._single(malformed),
                EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=5),
            ).outcome,
            LocalEvidenceOutcome.RESOURCE_LIMIT,
        )
        self.assertEqual(
            _extract(
                self._single(minimal),
                EpubOpeningLimits(nodes_per_resource=3, nodes_per_selection=3),
            ).outcome,
            LocalEvidenceOutcome.SUCCESS,
        )

        package = _opening_package(
            items=(("deep", "deep.xhtml", "application/xhtml+xml", ""), ("safe", "safe.xhtml", "application/xhtml+xml", "")),
            spine=("deep", "safe"),
        )
        node_partition = _opening_epub(
            package=package,
            entries=(("EPUB/deep.xhtml", b'''<html xmlns="http://www.w3.org/1999/xhtml"><body><div><p>x</p></div></body></html>''', zipfile.ZIP_DEFLATED), ("EPUB/safe.xhtml", minimal, zipfile.ZIP_DEFLATED)),
        )
        local_nodes = _extract(
            node_partition,
            EpubOpeningLimits(nodes_per_resource=3),
        )
        aggregate_nodes = _extract(
            self._single(minimal),
            EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=2),
        )
        self.assertEqual(local_nodes.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(OpeningResourceDisposition.RESOURCE_LIMIT, {item.disposition for item in local_nodes.resources})
        self.assertEqual(aggregate_nodes.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)

        navigation_nodes = _opening_epub(
            package=_opening_package(
                items=(
                    ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                    ("safe", "safe.xhtml", "application/xhtml+xml", ""),
                ),
                spine=("safe",),
            ),
            entries=(
                ("EPUB/nav.xhtml", _xhtml("<nav><ol><li><a href='safe.xhtml'>x</a></li></ol></nav>"), zipfile.ZIP_DEFLATED),
                ("EPUB/safe.xhtml", minimal, zipfile.ZIP_DEFLATED),
            ),
        )
        charged_navigation = _extract(
            navigation_nodes,
            EpubOpeningLimits(nodes_per_resource=5, nodes_per_selection=5),
        )
        self.assertEqual(charged_navigation.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)
        self.assertFalse(charged_navigation.resources)
        unsafe_navigation = _opening_epub(
            package=_opening_package(
                items=(
                    ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                    ("safe", "safe.xhtml", "application/xhtml+xml", ""),
                ),
                spine=("safe",),
            ),
            entries=(
                (
                    "EPUB/nav.xhtml",
                    b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:q="http://www.w3.org/2001/XInclude"><body><q:include/></body></html>''',
                    zipfile.ZIP_DEFLATED,
                ),
                ("EPUB/safe.xhtml", minimal, zipfile.ZIP_DEFLATED),
            ),
        )
        self.assertEqual(
            _extract(
                unsafe_navigation,
                EpubOpeningLimits(nodes_per_resource=5, nodes_per_selection=5),
            ).outcome,
            LocalEvidenceOutcome.SUCCESS,
        )
        self.assertEqual(
            _extract(
                unsafe_navigation,
                EpubOpeningLimits(nodes_per_resource=5, nodes_per_selection=4),
            ).outcome,
            LocalEvidenceOutcome.RESOURCE_LIMIT,
        )

        nav_package = _opening_package(
            items=(("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'), ("safe", "safe.xhtml", "application/xhtml+xml", "")),
            spine=("safe",),
        )
        nav = _xhtml('<nav epub:type="landmarks"><a epub:type="titlepage" href="safe.xhtml">one</a><a epub:type="copyright-page" href="safe.xhtml">two</a></nav>')
        nav_value = _opening_epub(
            package=nav_package,
            entries=(("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED), ("EPUB/safe.xhtml", minimal, zipfile.ZIP_DEFLATED)),
        )
        self.assertEqual(
            _extract(nav_value, EpubOpeningLimits(navigation_links=2)).outcome,
            LocalEvidenceOutcome.SUCCESS,
        )
        self.assertEqual(
            _extract(nav_value, EpubOpeningLimits(navigation_links=1)).outcome,
            LocalEvidenceOutcome.RESOURCE_LIMIT,
        )

    def test_navigation_only_and_unknown_encoding_preserve_typed_outcomes(self) -> None:
        public_doctype = b'''<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Recovered Doctype</h1></body></html>'''
        recovered_doctype = _extract(self._single(public_doctype))
        self.assertEqual(recovered_doctype.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertEqual(recovered_doctype.claims[0].raw_value, "Recovered Doctype")
        self.assertEqual(
            recovered_doctype.resources[0].parse_mode, OpeningParseMode.RECOVERED
        )

        quoted_bracket_doctype = b'''<!DOCTYPE html SYSTEM "https://example.invalid/a[b].dtd"><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Quoted Bracket Safe</h1></body></html>'''
        for media_type in ("application/xhtml+xml", "text/html"):
            with self.subTest(media_type=media_type, inert="quoted-bracket"):
                inert_doctype = _extract(
                    self._single(
                        quoted_bracket_doctype,
                        media_type=media_type,
                    )
                )
                self.assertEqual(
                    inert_doctype.outcome, LocalEvidenceOutcome.SUCCESS
                )
                self.assertEqual(
                    inert_doctype.resources[0].parse_mode,
                    OpeningParseMode.RECOVERED,
                )

        unsafe_declarations = (
            b'''<!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
            b'''<!DOCTYPE html SYSTEM "x>y" [<!ELEMENT html ANY>]><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
            b'''<?bogus><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
            b'''<?bogus><!DOCTYPE html [<!ELEMENT html ANY>]><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
            b'''<!-- inert --!><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
            b'''<!--><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
            b'''<!---><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        )
        for media_type in ("application/xhtml+xml", "text/html"):
            for unsafe in unsafe_declarations:
                with self.subTest(media_type=media_type, unsafe=unsafe[:24]):
                    rejected = _extract(
                        self._single(unsafe, media_type=media_type)
                    )
                    self.assertEqual(
                        rejected.outcome,
                        LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE,
                    )
                    self.assertEqual(
                        rejected.resources[0].disposition,
                        OpeningResourceDisposition.MALFORMED,
                    )
                    self.assertIn(
                        "unsafe_construct", rejected.resources[0].warnings
                    )
                    self.assertNotIn("Injected", rejected.to_json())

        comment = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><!-- <!ENTITY inert "text"> --><h1 epub:type="fulltitle">Comment Safe</h1></body></html>'''
        for media_type in ("application/xhtml+xml", "text/html"):
            with self.subTest(media_type=media_type, inert="comment"):
                inert_comment = _extract(
                    self._single(comment, media_type=media_type)
                )
                self.assertEqual(inert_comment.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertEqual(
                    inert_comment.resources[0].parse_mode,
                    OpeningParseMode.STRICT
                    if media_type == "application/xhtml+xml"
                    else OpeningParseMode.RECOVERED,
                )

        for body in (
            b'''<![CDATA[<!DOCTYPE unsafe [<!ENTITY inert "text">]>]]><h1 epub:type="fulltitle">CDATA Safe</h1>''',
            b'''<svg xmlns="http://www.w3.org/2000/svg"><![CDATA[<!ENTITY inert "text">]]></svg><h1 epub:type="fulltitle">Foreign CDATA Safe</h1>''',
        ):
            cdata = (
                b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'''
                + body
                + b"</body></html>"
            )
            for media_type in ("application/xhtml+xml", "text/html"):
                with self.subTest(media_type=media_type, inert="cdata"):
                    inert_cdata = _extract(
                        self._single(cdata, media_type=media_type)
                    )
                    self.assertEqual(
                        inert_cdata.outcome, LocalEvidenceOutcome.SUCCESS
                    )
                    self.assertEqual(
                        inert_cdata.resources[0].parse_mode,
                        OpeningParseMode.STRICT
                        if media_type == "application/xhtml+xml"
                        else OpeningParseMode.RECOVERED,
                    )

        for tag, prefix in (("script", "scripture"), ("style", "stylex")):
            raw_text = (
                f'<html xmlns="http://www.w3.org/1999/xhtml" '
                f'xmlns:epub="http://www.idpf.org/2007/ops"><body><{tag}>'
                f'inert</{prefix}><!ENTITY inert "text"></{tag}>'
                '<h1 epub:type="fulltitle">Raw Text Safe</h1></body></html>'
            ).encode()
            for media_type in ("application/xhtml+xml", "text/html"):
                with self.subTest(media_type=media_type, raw_text=tag):
                    inert_raw_text = _extract(
                        self._single(raw_text, media_type=media_type)
                    )
                    self.assertEqual(
                        inert_raw_text.outcome, LocalEvidenceOutcome.SUCCESS
                    )
                    self.assertEqual(
                        inert_raw_text.resources[0].parse_mode,
                        OpeningParseMode.RECOVERED,
                    )

        nav_only = _opening_epub(
            package=_opening_package(
                items=(
                    ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                    ("aux", "aux.xhtml", "application/xhtml+xml", ""),
                ),
                spine=(),
            ).replace(b"</spine>", b'<itemref idref="aux" linear="no"/></spine>'),
            entries=(
                ("EPUB/nav.xhtml", _xhtml("<nav><ol/></nav>"), zipfile.ZIP_DEFLATED),
                ("EPUB/aux.xhtml", _xhtml("<p>auxiliary</p>"), zipfile.ZIP_DEFLATED),
            ),
        )
        report = _extract(nav_only)
        self.assertEqual(report.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertEqual(report.usage.selected_documents, 0)
        self.assertEqual(len(report.resources), 1)
        self.assertEqual(report.resources[0].selection_path, ("epub3_navigation",))

        nav_in_spine = _extract(
            _opening_epub(
                package=_opening_package(
                    items=(("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),),
                    spine=("nav",),
                ),
                entries=(("EPUB/nav.xhtml", _xhtml("<nav><ol/></nav>"), zipfile.ZIP_DEFLATED),),
            )
        )
        self.assertEqual(
            nav_in_spine.outcome, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE
        )
        self.assertEqual(nav_in_spine.usage.selected_documents, 1)
        self.assertEqual(
            [resource.selection_path for resource in nav_in_spine.resources],
            [("epub3_navigation",), ("linear_spine_fallback",)],
        )
        self.assertEqual(
            len({resource.resource_identity for resource in nav_in_spine.resources}),
            1,
        )

        unsupported = b'''<?xml version="1.0" encoding="x-unsupported"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>'''
        package = _opening_package(
            items=(
                ("bad", "bad.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("bad", "safe"),
        )
        content = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/bad.xhtml", unsupported, zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(content.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            OpeningResourceDisposition.UNSUPPORTED_ENCODING,
            {resource.disposition for resource in content.resources},
        )
        navigation = _extract(
            _opening_epub(
                package=_opening_package(
                    items=(
                        ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                        ("safe", "safe.xhtml", "application/xhtml+xml", ""),
                    ),
                    spine=("safe",),
                ),
                entries=(
                    ("EPUB/nav.xhtml", unsupported, zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(navigation.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn("unusable_navigation_document", navigation.warnings)

        minimal = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body></html>'''
        items = tuple((f"item{index}", f"item{index}.xhtml", "application/xhtml+xml", "") for index in range(6))
        documents = _opening_epub(
            package=_opening_package(items=items, spine=tuple(item[0] for item in items)),
            entries=tuple((f"EPUB/item{index}.xhtml", minimal.replace(b">abc<", f">title{index}<".encode()), zipfile.ZIP_DEFLATED) for index in range(6)),
        )
        selected = _extract(documents)
        self.assertEqual(selected.usage.selected_documents, 5)
        self.assertIn("selected_document_limit_reached", selected.warnings)
        one_selected = _extract(
            documents,
            EpubOpeningLimits(selected_documents=1),
        )
        self.assertEqual(one_selected.usage.selected_documents, 1)
        self.assertIn("selected_document_limit_reached", one_selected.warnings)

    @settings(max_examples=30, deadline=None)
    @given(st.text(max_size=120))
    def test_recovered_html_text_is_bounded_inert_data(self, value: str) -> None:
        package = b'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:uuid:00000000-0000-4000-8000-000000000008</dc:identifier><dc:title>Property Fixture</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-01-13T00:00:00Z</meta></metadata>
<manifest><item id="opening" href="opening.html" media-type="text/html"/></manifest><spine><itemref idref="opening"/></spine></package>'''
        content = (
            "<!doctype html><html><body><h1>"
            + html.escape(value)
            + "</h1><script>fetch('https://example.invalid')</script></body></html>"
        ).encode("utf-8")
        source = epub_bytes(
            package=package,
            include_default_content=False,
            extra_entries=(("EPUB/opening.html", content, zipfile.ZIP_DEFLATED),),
        )
        report = _extract(source)
        self.assertIn(
            report.outcome,
            {LocalEvidenceOutcome.SUCCESS, LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE},
        )
        self.assertNotIn("example.invalid", report.to_json())


if __name__ == "__main__":
    unittest.main()
