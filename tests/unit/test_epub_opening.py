from __future__ import annotations

import hashlib
import html
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

import mechanical_librarian.adapters.evidence_extractors.epub_opening as opening

from mechanical_librarian.adapters.evidence_extractors.epub_opening import (
    EpubOpeningExtractor,
    _looks_wide_encoded,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package import EpubPackageExtractor
from mechanical_librarian.domain.epub_opening import (
    EpubOpeningLimits,
    EpubOpeningReport,
    OpeningField,
    OpeningParseMode,
    OpeningResourceDisposition,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
)
from mechanical_librarian.domain.epub_source import FileReference
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubInspectionOutcome,
    EpubPackageRequest,
    EpubPackageSource,
    EpubObservationApplicability,
    epub_file_lineage,
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


def _request(value: bytes, limits: EpubOpeningLimits | None = None) -> EpubPackageRequest:
    selected = EpubOpeningLimits() if limits is None else limits
    source = Fingerprint(
        "sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()
    )
    return EpubPackageRequest(
        TargetId(
            AdapterId("local-epub"),
            ConnectionId("local"),
            LibraryId("local"),
            ExternalItemId("0" * 64),
        ),
        FileReference("local", "0.epub"),
        EpubPackageSource(
            len(value), lambda offset, size: value[offset : offset + size]
        ),
        source,
        EpubPackageCapability.OPENING_MATTER,
        selected.package,
        datetime.now(timezone.utc) + timedelta(seconds=30),
        time.monotonic() + 30.0,
        EpubPackageCancellation("opening-test", lambda: False),
    )


def _extract(
    value: bytes, limits: EpubOpeningLimits | None = None
) -> EpubOpeningReport:
    selected = EpubOpeningLimits() if limits is None else limits
    request = _request(value, selected)
    package_request = EpubPackageRequest(
        request.target,
        request.file,
        request.source,
        request.expected_source,
        EpubPackageCapability.PACKAGE,
        request.limits,
        request.deadline,
        request.monotonic_deadline,
        request.cancellation,
    )
    package = EpubPackageExtractor().extract(package_request)
    return EpubOpeningExtractor().extract(request, selected, package)


class EpubOpeningTests(unittest.TestCase):
    def _single(self, body: bytes, *, media_type: str = "application/xhtml+xml") -> bytes:
        recovered_html = media_type == "text/html"
        package = _opening_package(
            version=2 if recovered_html else 3,
            items=(("opening", "opening.xhtml", media_type, ""),),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml"),) if recovered_html else (),
        )
        return _opening_epub(
            package=package,
            entries=(("EPUB/opening.xhtml", body, zipfile.ZIP_DEFLATED),),
            version=2 if recovered_html else 3,
        )

    def test_materialized_opening_registry_has_declared_top_level_outcomes(self) -> None:
        for name, recipe in MATERIALIZED_RECIPES.items():
            if not name.startswith("opening_"):
                continue
            with self.subTest(recipe=name):
                report = _extract(recipe())
                expected = (
                    EpubInspectionOutcome.RESOURCE_LIMIT
                    if name == "opening_resource_limit"
                    else EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
                    if name in {
                        "opening_no_applicable",
                        "opening_invalid_encoding",
                        "opening_utf16le_unsupported",
                        "opening_utf16be_unsupported",
                    }
                    else EpubInspectionOutcome.SUCCESS
                )
                self.assertEqual(report.outcome, expected)
                if name in {"opening_epub3_landmarks", "opening_epub2_guide"}:
                    self.assertIn(
                        OpeningField.TITLE,
                        {claim.field for claim in report.claims},
                    )
                    self.assertNotIn(
                        OpeningField.IDENTIFIER,
                        {claim.field for claim in report.claims},
                    )
                if name == "opening_invalid_fragment_fallback":
                    self.assertEqual(
                        report.resources[1].selection_path,
                        ("epub2_guide", "title_page"),
                    )
                    self.assertEqual(report.usage.navigation_links, 2_048)
                if name in {
                    "opening_empty_namespace_authority",
                    "opening_implicit_foreign_content",
                    "opening_xml_declaration_lookalike",
                }:
                    self.assertEqual(
                        len(
                            [
                                claim
                                for claim in report.claims
                                if claim.field is OpeningField.TITLE
                            ]
                        ),
                        2 if name == "opening_empty_namespace_authority" else 1,
                    )
                if name == "opening_implicit_foreign_content":
                    self.assertEqual(report.usage.navigation_links, 1)
                    self.assertEqual(
                        report.resources[1].selection_path,
                        ("epub3_landmark", "titlepage"),
                    )

    def test_citations_are_exact_versioned_path_free_and_round_trip_canonically(self) -> None:
        report = _extract(opening_claims_complete())
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            {claim.field for claim in report.claims},
            {
                OpeningField.TITLE,
                OpeningField.CREATOR,
                OpeningField.CREATOR_ROLE,
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
                epub_file_lineage(report.source_fingerprint),
            )
        self.assertEqual(
            report.same_file_lineage,
            epub_file_lineage(report.source_fingerprint),
        )
        self.assertEqual(EpubOpeningReport.from_json(report.to_json()), report)

    def test_plain_chapter_heading_never_becomes_a_book_title(self) -> None:
        report = _extract(self._single(_xhtml("<h1>Chapter One</h1><p>Story prose.</p>")))
        self.assertEqual(report.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)
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
        self.assertEqual(bounded_voids.outcome, EpubInspectionOutcome.SUCCESS)
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
            nested_body.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
        )
        self.assertIn("body_transition_reached", nested_body.warnings)
        implied = _extract(
            self._single(
                ("<!doctype html><html><body>" + "<p><span>x" * 40 + '<h1 epub:type="fulltitle">Implied Close Title</h1></body></html>').encode(),
                media_type="text/html",
            )
        )
        self.assertEqual(implied.outcome, EpubInspectionOutcome.SUCCESS)
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
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
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
        self.assertEqual(prefixed.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)

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
                self.assertIs(identifier.applicability, EpubObservationApplicability.UNKNOWN)

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

        following = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    '<p>ISBN 9780306406157</p><p>Electronic edition</p>'
                    '</section>'
                )
            )
        )
        following_identifier = next(
            claim
            for claim in following.claims
            if claim.field is OpeningField.IDENTIFIER
        )
        self.assertEqual(following_identifier.applicability_label, "ebook")
        self.assertIn(
            "Electronic edition", following_identifier.citation.exact_excerpt
        )
        self.assertIn(
            following_identifier.raw_value,
            following_identifier.citation.exact_excerpt,
        )

        for semantic in ("bodymatter", "sample"):
            scoped = _extract(
                self._single(
                    _xhtml(
                        '<p>ISBN 9780306406157</p>'
                        f'<section epub:type="{semantic}"><p>Electronic edition</p></section>'
                    )
                )
            )
            scoped_identifier = next(
                claim
                for claim in scoped.claims
                if claim.field is OpeningField.IDENTIFIER
            )
            with self.subTest(semantic=semantic):
                self.assertEqual(
                    scoped_identifier.applicability,
                    EpubObservationApplicability.UNKNOWN,
                )
                self.assertEqual(
                    scoped_identifier.applicability_label,
                    "unlabelled",
                )
                self.assertNotIn(
                    "Electronic edition",
                    scoped_identifier.citation.exact_excerpt,
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
        self.assertEqual(conflicting_identifier.applicability_label, "ambiguous")
        self.assertIs(
            conflicting_identifier.applicability,
            EpubObservationApplicability.UNKNOWN,
        )
        self.assertIn(
            "Electronic paperback",
            conflicting_identifier.citation.exact_excerpt,
        )

    def test_isbn_recognition_is_occurrence_local_and_rejects_owned_uri_digits(self) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page"><p>'
                    'ISBN 9781861972712; eBook ISBN 9780306406157; '
                    'LCCN: 9780306406157; '
                    'LCCN https://lccn.loc.gov/9780306406157'
                    '</p></section>'
                )
            )
        )
        identifiers = [
            claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER
        ]
        self.assertEqual(
            [(claim.normalized_value, claim.applicability_label) for claim in identifiers],
            [("9781861972712", "unlabelled"), ("9780306406157", "ebook")],
        )
        forward = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page"><p>'
                    'ISBN 9781861972712 eBook ISBN 9780306406157'
                    '</p></section>'
                )
            )
        )
        self.assertEqual(
            [
                claim.applicability_label
                for claim in forward.claims
                if claim.field is OpeningField.IDENTIFIER
            ],
            ["unlabelled", "ebook"],
        )
        backward = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page"><p>'
                    'eBook ISBN 9781861972712 ISBN 9780306406157'
                    '</p></section>'
                )
            )
        )
        self.assertEqual(
            [
                claim.applicability_label
                for claim in backward.claims
                if claim.field is OpeningField.IDENTIFIER
            ],
            ["ebook", "unlabelled"],
        )

    def test_colon_delimited_media_labels_and_uri_owners_are_local(self) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page"><p>'
                    'Paperback: 9781861972712; Hardcover: 9780306406157; '
                    'eBook: 9780140328721; x:9780306406157, '
                    'https://lccn.loc.gov/9780306406157; '
                    'https://example.invalid/item;id=9780306406157'
                    '</p></section>'
                )
            )
        )
        self.assertEqual(
            [
                (claim.normalized_value, claim.applicability_label)
                for claim in report.claims
                if claim.field is OpeningField.IDENTIFIER
            ],
            [
                ("9781861972712", "paperback"),
                ("9780306406157", "hardcover"),
                ("9780140328721", "ebook"),
            ],
        )

    def test_identifier_owner_spans_are_computed_once_per_element(self) -> None:
        values = " ".join("9780306406157" for _ in range(12))
        source = self._single(
            _xhtml(
                '<section epub:type="copyright-page"><p>'
                + values
                + '</p></section>'
            )
        )
        with patch.object(
            opening,
            "_non_isbn_identifier_spans",
            wraps=opening._non_isbn_identifier_spans,
        ) as spans:
            report = _extract(source)
        self.assertEqual(spans.call_count, 1)
        self.assertEqual(
            sum(claim.field is OpeningField.IDENTIFIER for claim in report.claims),
            12,
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
            lambda value: value["claims"][0]["citation"].__setitem__(
                "resource_digest", "0" * 64
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
        self.assertEqual(
            {claim.field for claim in report.claims},
            set(OpeningField),
        )
        self.assertEqual(
            {
                claim.normalized_value
                for claim in report.claims
                if claim.field in {OpeningField.CONTRIBUTOR_ROLE, OpeningField.CREATOR_ROLE}
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
            {"adapter"},
        )
        self.assertEqual(
            {
                claim.normalized_value
                for claim in roles.claims
                if claim.field is OpeningField.CONTRIBUTOR
            },
            {
                "Casey Designer",
                "Alex Adapter",
            },
        )
        self.assertEqual(
            [claim.normalized_value for claim in roles.claims if claim.field is OpeningField.CREATOR],
            ["Chris Creator"],
        )

    def test_indicia_roles_dates_and_production_credits_remain_typed_and_separate(
        self,
    ) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>by Ada Author</p>"
                    "<p>Translated and illustrated by Bea Artist</p>"
                    "<p>Edited by Cara Editor; Foreword by Dev Writer</p>"
                    "<p>Book design by Eli Designer; Photography by Fran Lens; "
                    "Cartography by Gia Mapper; Adapted by Hal Adapter</p>"
                    "<p>EPUB conversion by Tool House; Scanned by Archive Bot; "
                    "Produced with Calibre; Distributed by Example Retailer</p>"
                    "<p>First published 1998</p>"
                    "<p>This ebook edition published 2024-03</p>"
                    "<p>Copyright 2024-03-05</p>"
                    "<p>Last modified 2024-03-05T10:30:00+01:00</p>"
                    "</section>"
                )
            )
        )

        self.assertEqual(
            [
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CREATOR
            ],
            [("Ada Author", "author")],
        )
        self.assertEqual(
            {
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CONTRIBUTOR
            },
            {
                ("Bea Artist", "translator+illustrator"),
                ("Cara Editor", "editor"),
                ("Dev Writer", "foreword"),
                ("Eli Designer", "designer"),
                ("Fran Lens", "photographer"),
                ("Gia Mapper", "cartographer"),
                ("Hal Adapter", "adapter"),
            },
        )
        contextual = {
            (claim.normalized_value, claim.role)
            for claim in report.claims
            if claim.field is OpeningField.CONTEXTUAL_CREDIT
        }
        self.assertEqual(
            contextual,
            {
                ("Tool House", "conversion"),
                ("Archive Bot", "scanner"),
                ("Calibre", "software"),
                ("Example Retailer", "retailer"),
            },
        )
        self.assertFalse(
            contextual
            & {
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CONTRIBUTOR
            }
        )
        self.assertEqual(
            [
                (
                    claim.field,
                    claim.normalized_value,
                    claim.date_event,
                    claim.date_precision,
                )
                for claim in report.claims
                if claim.field
                in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
            ],
            [
                (
                    OpeningField.DATE_EVENT,
                    "1998",
                    DateEvent.WORK_FIRST_PUBLICATION,
                    DatePrecision.YEAR,
                ),
                (
                    OpeningField.PUBLICATION_DATE,
                    "2024-03",
                    DateEvent.MANIFESTATION_PUBLICATION,
                    DatePrecision.MONTH,
                ),
                (
                    OpeningField.DATE_EVENT,
                    "2024-03-05",
                    DateEvent.COPYRIGHT,
                    DatePrecision.DAY,
                ),
                (
                    OpeningField.DATE_EVENT,
                    "2024-03-05T10:30:00+01:00",
                    DateEvent.MODIFICATION,
                    DatePrecision.TIMESTAMP,
                ),
            ],
        )
        self.assertEqual(EpubOpeningReport.from_json(report.to_json()), report)

    def test_unlabelled_print_and_invalid_dates_never_become_manifestation_dates(
        self,
    ) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>Published 2024</p>"
                    "<p>Paperback edition published 2023-02-01</p>"
                    "<p>This ebook edition published 2024-02-31</p>"
                    "<p>First published 1999</p>"
                    "</section>"
                )
            )
        )
        self.assertFalse(
            any(
                claim.field is OpeningField.PUBLICATION_DATE
                for claim in report.claims
            )
        )
        self.assertEqual(
            [
                (claim.normalized_value, claim.date_event)
                for claim in report.claims
                if claim.field is OpeningField.DATE_EVENT
            ],
            [("1999", DateEvent.WORK_FIRST_PUBLICATION)],
        )

    def test_ambiguous_date_spans_fail_closed_without_truncation_or_event_guessing(
        self,
    ) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>EPUB publication date: 2024-03-05T10:30:00</p>"
                    "<p>First published as this ebook edition 2024</p>"
                    "<p>THIS EBOOK EDITION: FIRST PUBLISHED 2025</p>"
                    "<p>Digital edition, originally published 2026</p>"
                    "</section>"
                )
            )
        )

        self.assertFalse(
            any(
                claim.field
                in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
                for claim in report.claims
            )
        )
        self.assertIn("ambiguous_date_event", report.warnings)
        self.assertIsNone(
            opening._date_precision("2024-03-05T10:30:00")
        )

        independent = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>First published 1998. "
                    "This ebook edition published 2024</p>"
                    "</section>"
                )
            )
        )
        self.assertEqual(
            [
                (claim.normalized_value, claim.date_event)
                for claim in independent.claims
                if claim.field
                in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
            ],
            [
                ("1998", DateEvent.WORK_FIRST_PUBLICATION),
                ("2024", DateEvent.MANIFESTATION_PUBLICATION),
            ],
        )
        self.assertNotIn("ambiguous_date_event", independent.warnings)
        for separator in (". ", "; ", "|", "\n"):
            with self.subTest(separator=separator):
                text = (
                    f"First published 1998{separator}"
                    "This ebook edition published 2024"
                )
                self.assertEqual(
                    [
                        (lexical, event)
                        for lexical, event, _precision
                        in opening._indicia_dates(text)
                    ],
                    [
                        ("1998", DateEvent.WORK_FIRST_PUBLICATION),
                        ("2024", DateEvent.MANIFESTATION_PUBLICATION),
                    ],
                )
                self.assertEqual(
                    opening._mixed_edition_date_spans(text),
                    (),
                )
        self.assertEqual(
            [
                (lexical, event)
                for lexical, event, _precision
                in opening._indicia_dates("First published 1998")
            ],
            [("1998", DateEvent.WORK_FIRST_PUBLICATION)],
        )
        self.assertEqual(
            opening._mixed_edition_date_spans("This ebook edition"),
            (),
        )

    def test_indicia_dates_require_whole_tokens_and_exact_offset_boundary(
        self,
    ) -> None:
        valid = (
            "EPUB publication date: 2024-03-05T10:30:00+14:00. "
            "Copyright 2023"
        )
        self.assertEqual(
            [
                (value, event, precision)
                for value, event, precision in opening._indicia_dates(valid)
            ],
            [
                (
                    "2024-03-05T10:30:00+14:00",
                    DateEvent.MANIFESTATION_PUBLICATION,
                    DatePrecision.TIMESTAMP,
                ),
                ("2023", DateEvent.COPYRIGHT, DatePrecision.YEAR),
            ],
        )
        for malformed in (
            "2024-03-05T10:30:00+14:01",
            "2024-03-05T10:30:00-14:59",
            "2024/03/05",
            "2024abc",
            "2024-03-05Z",
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    opening._indicia_dates(
                        f"EPUB publication date: {malformed}"
                    ),
                    (),
                )
                self.assertIsNone(opening._date_precision(malformed))

    def test_adjacent_explicit_credits_recover_independent_people_and_roles(
        self,
    ) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>Translated by Alice. Illustrated by Bob</p>"
                    "<p>Translated by Carol and illustrated by Dana</p>"
                    "<p>Translated by A. B. Carter</p>"
                    "<p>Translated by Eve. Copyright 2024</p>"
                    "<p>EPUB conversion by Tool House. Copyright 2024</p>"
                    "<p>Translated and illustrated by Bea Artist</p>"
                    "</section>"
                )
            )
        )

        self.assertEqual(
            {
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CONTRIBUTOR
            },
            {
                ("Alice", "translator"),
                ("Bob", "illustrator"),
                ("Carol", "translator"),
                ("Dana", "illustrator"),
                ("A. B. Carter", "translator"),
                ("Eve", "translator"),
                ("Bea Artist", "translator+illustrator"),
            },
        )
        self.assertIn(
            ("Tool House", "conversion"),
            {
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CONTEXTUAL_CREDIT
            },
        )

    def test_promotion_word_inside_semantic_title_does_not_suppress_claim(
        self,
    ) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    '<h1 epub:type="fulltitle">A Sample of Life</h1>'
                    "<p>Copyright 2024</p>"
                    "</section>"
                )
            )
        )
        self.assertIn(
            (OpeningField.TITLE, "A Sample of Life"),
            {(claim.field, claim.normalized_value) for claim in report.claims},
        )

    def test_promotion_notice_suppresses_only_later_claims_in_its_front_scope(
        self,
    ) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>Edited by Earlier Editor</p>"
                    '<section epub:type="preview">'
                    "<p>Copyright 2022</p>"
                    "<p>Edited by Semantic Promo Editor</p>"
                    "</section>"
                    "<h2>Preview of Another Work</h2>"
                    "<p>Copyright 2024</p>"
                    "<p>Edited by Promo Editor</p>"
                    "</section>"
                    '<section epub:type="copyright-page">'
                    '<h1 epub:type="fulltitle">A Sample of Life</h1>'
                    "<p>Copyright 2023</p>"
                    "<p>Edited by Sibling Editor</p>"
                    "</section>"
                )
            )
        )
        self.assertEqual(
            {
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CONTRIBUTOR
            },
            {
                ("Earlier Editor", "editor"),
                ("Sibling Editor", "editor"),
            },
        )
        self.assertEqual(
            {
                claim.normalized_value
                for claim in report.claims
                if claim.date_event is DateEvent.COPYRIGHT
            },
            {"2023"},
        )
        self.assertIn(
            (OpeningField.TITLE, "A Sample of Life"),
            {(claim.field, claim.normalized_value) for claim in report.claims},
        )

    def test_uncontained_promotion_run_stops_at_later_target_section(self) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    "<p>Edited by Earlier Editor</p>"
                    "<h2>Preview of Another Work</h2>"
                    "<p>Copyright 2024</p>"
                    "<p>Edited by Promo Editor</p>"
                    '<section epub:type="copyright-page">'
                    '<h1 epub:type="fulltitle">A Sample of Life</h1>'
                    "<p>Copyright 2023</p>"
                    "<p>Edited by Target Editor</p>"
                    "</section>"
                )
            )
        )
        self.assertEqual(
            {
                (claim.normalized_value, claim.role)
                for claim in report.claims
                if claim.field is OpeningField.CONTRIBUTOR
            },
            {
                ("Earlier Editor", "editor"),
                ("Target Editor", "editor"),
            },
        )
        self.assertEqual(
            {
                claim.normalized_value
                for claim in report.claims
                if claim.date_event is DateEvent.COPYRIGHT
            },
            {"2023"},
        )
        self.assertIn(
            (OpeningField.TITLE, "A Sample of Life"),
            {(claim.field, claim.normalized_value) for claim in report.claims},
        )

    def test_synthetic_indicia_aggregate_has_exact_role_and_date_recovery(
        self,
    ) -> None:
        role_cases = (
            ("Translated by A Translator", {("A Translator", "translator")}),
            ("Illustrations by B Artist", {("B Artist", "illustrator")}),
            ("Edited by C Editor", {("C Editor", "editor")}),
            ("Foreword by D Writer", {("D Writer", "foreword")}),
            ("Cover design by E Designer", {("E Designer", "designer")}),
            ("Photography by F Lens", {("F Lens", "photographer")}),
            ("Maps by G Mapper", {("G Mapper", "cartographer")}),
            ("Adapted by H Adapter", {("H Adapter", "adapter")}),
            (
                "Translated and illustrated by I Artist",
                {("I Artist", "translator+illustrator")},
            ),
            ("EPUB conversion by Tool House", set()),
            ("Scanned by Archive Bot", set()),
            ("Contributions by Ambiguous Person", set()),
            ("Retailer: Example Shop", set()),
            ("ordinary prose by nobody in particular", set()),
        )
        date_cases = (
            (
                "This ebook edition published 2024",
                {("2024", DatePrecision.YEAR)},
            ),
            (
                "Digital edition published 2024-03",
                {("2024-03", DatePrecision.MONTH)},
            ),
            (
                "This edition published 2024-03-05",
                {("2024-03-05", DatePrecision.DAY)},
            ),
            (
                "EPUB publication date: 2024-03-05T10:30:00-04:00",
                {("2024-03-05T10:30:00-04:00", DatePrecision.TIMESTAMP)},
            ),
            ("Published 2024", set()),
            ("Paperback edition published 2024-03-05", set()),
            ("First published 1998", set()),
            ("Copyright 2024", set()),
            ("Last modified 2024-03-05", set()),
            ("This ebook edition published 2024-02-31", set()),
        )

        def aggregate(cases, recover):
            true_positive = false_positive = false_negative = true_negative = 0
            for text, expected in cases:
                actual = recover(text)
                true_positive += len(actual & expected)
                false_positive += len(actual - expected)
                false_negative += len(expected - actual)
                true_negative += int(not expected and not actual)
            return {
                "explicit_negative_precision": (
                    true_negative / (true_negative + false_positive)
                ),
                "false_positive": false_positive,
                "recall": true_positive / (true_positive + false_negative),
            }

        role_aggregate = aggregate(
            role_cases,
            lambda text: {
                (name, role)
                for field, name, role, _applicability in opening._indicia_credits(
                    text
                )
                if field is OpeningField.CONTRIBUTOR
            },
        )
        date_aggregate = aggregate(
            date_cases,
            lambda text: {
                (lexical, precision)
                for lexical, event, precision in opening._indicia_dates(text)
                if event is DateEvent.MANIFESTATION_PUBLICATION
            },
        )
        self.assertEqual(
            {"contributor_role": role_aggregate, "manifestation_date": date_aggregate},
            {
                "contributor_role": {
                    "explicit_negative_precision": 1.0,
                    "false_positive": 0,
                    "recall": 1.0,
                },
                "manifestation_date": {
                    "explicit_negative_precision": 1.0,
                    "false_positive": 0,
                    "recall": 1.0,
                },
            },
        )

    def test_fact_limit_retains_already_admitted_opening_evidence(self) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>Translated by First Contributor</p>"
                    "<p>Illustrated by Second Contributor</p>"
                    "</section>"
                )
            ),
            EpubOpeningLimits(facts=1),
        )
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(len(report.claims), 1)
        self.assertIn("opening_fact_limit", report.warnings)

    def test_fact_limit_marker_is_reserved_inside_a_full_warning_budget(self) -> None:
        report = _extract(
            self._single(
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>First published as this ebook edition 2024</p>"
                    "<p>Translated by First Contributor</p>"
                    "<p>Illustrated by Second Contributor</p>"
                    "</section>"
                )
            ),
            EpubOpeningLimits(facts=1, warnings=2),
        )
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(len(report.claims), 1)
        self.assertEqual(
            report.warnings,
            ("ambiguous_date_event", "opening_fact_limit"),
        )
        self.assertEqual(EpubOpeningReport.from_json(report.to_json()), report)

    def test_trailing_series_cue_without_positive_structure_is_rejected(
        self,
    ) -> None:
        report = _extract(
            self._single(_xhtml("<p>Book 2.5 of the Clockwork Archive</p>"))
        )
        self.assertEqual(
            [
                (claim.field, claim.normalized_value)
                for claim in report.claims
                if claim.field in {OpeningField.SERIES, OpeningField.SERIES_ORDER}
            ],
            [
                (OpeningField.SERIES_ORDER, "2.5"),
                (OpeningField.SERIES, "Clockwork Archive"),
            ],
        )

        rejected = (
            "<p>Clockwork Archive Book 2.5</p>",
            "<p>Read Clockwork Archive Book 2.5</p>",
            "<p>Discover Clockwork Archive Volume 2.5</p>",
            "<p>Clockwork Archive Book II</p>",
            "<p>Clockwork Archive Book Two</p>",
            "<p>Clockwork Archive Book 2.5 revised</p>",
            "<p>Read Clockwork Archive Book 2.5 today</p>",
            "<h2>Clockwork Archive Book 2.5</h2>",
            "<p>One Two Three Four Five Six Seven Book 2.5</p>",
        )
        for markup in rejected:
            with self.subTest(markup=markup):
                claims = _extract(self._single(_xhtml(markup))).claims
                self.assertFalse(
                    {
                        claim.field
                        for claim in claims
                    }
                    & {OpeningField.SERIES, OpeningField.SERIES_ORDER}
                )

    def test_byline_is_a_creator_with_author_role_and_source_values_are_redacted(self) -> None:
        report = _extract(self._single(_xhtml('<p epub:type="creator">By Ada Example</p><p epub:type="contributor">Bea Editor</p>')))
        creators = [claim for claim in report.claims if claim.field is OpeningField.CREATOR]
        contributors = [claim for claim in report.claims if claim.field is OpeningField.CONTRIBUTOR]
        self.assertEqual([(claim.normalized_value, claim.raw_value) for claim in creators], [("Ada Example", "Ada Example")])
        self.assertEqual([claim.normalized_value for claim in contributors], ["Bea Editor"])
        self.assertEqual(
            [claim.normalized_value for claim in report.claims if claim.field is OpeningField.CREATOR_ROLE],
            ["author"],
        )
        oversized = _extract(self._single(_xhtml('<p epub:type="creator">' + "A" * 281 + "</p>")))
        claim = next(claim for claim in oversized.claims if claim.field is OpeningField.CREATOR)
        self.assertEqual(claim.state, "oversized")
        self.assertRegex(claim.raw_value, r"^oversized-source-text-sha256:[0-9a-f]{64}$")
        self.assertEqual(claim.raw_value, claim.citation.exact_excerpt)
        unsafe = _extract(self._single(_xhtml('<p epub:type="creator">By /Users/private/Ada</p>')))
        creator = next(claim for claim in unsafe.claims if claim.field is OpeningField.CREATOR)
        self.assertEqual(creator.state, "unsupported")
        self.assertRegex(creator.raw_value, r"^unsafe-source-text-sha256:[0-9a-f]{64}$")
        self.assertEqual(creator.raw_value, creator.citation.exact_excerpt)
        self.assertEqual(
            [claim.normalized_value for claim in unsafe.claims if claim.field is OpeningField.CREATOR_ROLE],
            ["author"],
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
                EpubObservationApplicability.EDITION,
                EpubObservationApplicability.INAPPLICABLE,
            },
        )
        invalid = _extract(
            self._single(
                _xhtml('<section epub:type="copyright-page"><p>ISBN 9780306406158</p></section>')
            )
        )
        invalid_identifier = next(
            claim for claim in invalid.claims if claim.field is OpeningField.IDENTIFIER
        )
        self.assertIsNone(invalid_identifier.normalized_value)
        self.assertEqual(invalid_identifier.identifier_type, "isbn_13")
        self.assertEqual(invalid_identifier.applicability_label, "unlabelled")

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
        self.assertEqual(absent.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertEqual(per_resource.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertIn(
            OpeningResourceDisposition.RESOURCE_LIMIT,
            {resource.disposition for resource in per_resource.resources},
        )
        self.assertEqual(aggregate.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)
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
        self.assertEqual(exact_text.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(above_text.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)
        malformed = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body>'''
        cumulative = _extract(
            self._single(malformed),
            EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=6),
        )
        self.assertEqual(cumulative.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(cumulative.usage.nodes, 6)
        self.assertEqual(cumulative.usage.text_characters, 6)
        self.assertEqual(
            _extract(
                self._single(malformed),
                EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=5),
            ).outcome,
            EpubInspectionOutcome.RESOURCE_LIMIT,
        )
        self.assertEqual(
            _extract(
                self._single(minimal),
                EpubOpeningLimits(nodes_per_resource=3, nodes_per_selection=3),
            ).outcome,
            EpubInspectionOutcome.SUCCESS,
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
        self.assertEqual(local_nodes.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(OpeningResourceDisposition.RESOURCE_LIMIT, {item.disposition for item in local_nodes.resources})
        self.assertEqual(aggregate_nodes.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)

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
        self.assertEqual(charged_navigation.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)
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
            EpubInspectionOutcome.SUCCESS,
        )
        self.assertEqual(
            _extract(
                unsafe_navigation,
                EpubOpeningLimits(nodes_per_resource=5, nodes_per_selection=4),
            ).outcome,
            EpubInspectionOutcome.RESOURCE_LIMIT,
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
            EpubInspectionOutcome.SUCCESS,
        )
        self.assertEqual(
            _extract(nav_value, EpubOpeningLimits(navigation_links=1)).outcome,
            EpubInspectionOutcome.RESOURCE_LIMIT,
        )

    def test_navigation_only_and_unknown_encoding_preserve_typed_outcomes(self) -> None:
        public_doctype = b'''<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Recovered Doctype</h1></body></html>'''
        recovered_doctype = _extract(self._single(public_doctype))
        self.assertEqual(recovered_doctype.outcome, EpubInspectionOutcome.SUCCESS)
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
                    inert_doctype.outcome, EpubInspectionOutcome.SUCCESS
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
                        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
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
                self.assertEqual(inert_comment.outcome, EpubInspectionOutcome.SUCCESS)
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
                        inert_cdata.outcome, EpubInspectionOutcome.SUCCESS
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
                        inert_raw_text.outcome, EpubInspectionOutcome.SUCCESS
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
        self.assertEqual(report.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)
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
            nav_in_spine.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
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
        self.assertEqual(content.outcome, EpubInspectionOutcome.SUCCESS)
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
        self.assertEqual(navigation.outcome, EpubInspectionOutcome.SUCCESS)
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

    def test_same_resource_fragment_boundaries_preserve_safe_opening_roles(self) -> None:
        content = _xhtml(
            '<section id="title"><h1>Fragment Boundary Title</h1></section>'
            '<section id="sample"><p>eBook ISBN 9780306406157</p></section>'
            '<main id="body"><p>Body</p></main>'
        )
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
        )
        for order in ("opening-first", "boundaries-first"):
            links = (
                '<a epub:type="titlepage" href="opening.xhtml#title">Title</a>',
                '<a epub:type="bodymatter" href="opening.xhtml#body">Body</a>',
                '<a epub:type="preview" href="opening.xhtml#sample">Sample</a>',
            )
            if order == "boundaries-first":
                links = links[1:] + links[:1]
            nav = _xhtml(
                '<nav epub:type="landmarks">' + "".join(links) + "</nav>"
            )
            with self.subTest(family="epub3", order=order):
                report = _extract(
                    _opening_epub(
                        package=package,
                        entries=(
                            ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                            ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                        ),
                    )
                )
                self.assertIn(
                    "Fragment Boundary Title",
                    {claim.raw_value for claim in report.claims},
                )

        guide_package = _opening_package(
            version=2,
            items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
            spine=("opening",),
            guide=(
                ("text", "opening.xhtml#body"),
                ("preview", "opening.xhtml#sample"),
                ("title-page", "opening.xhtml#title"),
            ),
        )
        legacy = _extract(
            _opening_epub(
                version=2,
                package=guide_package,
                entries=(("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),),
            )
        )
        self.assertIn(
            "Fragment Boundary Title", {claim.raw_value for claim in legacy.claims}
        )

        excluded_nav = _xhtml(
            '<nav epub:type="landmarks">'
            '<a epub:type="titlepage" href="opening.xhtml#title">Title</a>'
            '<a epub:type="preview" href="opening.xhtml#title">Preview</a>'
            "</nav>"
        )
        excluded = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", excluded_nav, zipfile.ZIP_DEFLATED),
                    ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertFalse(excluded.claims)
        self.assertIn("explicitly_excluded_resource", excluded.warnings)

    def test_fragment_targets_resolve_uniquely_in_strict_and_recovered_content(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
        )
        nav = _xhtml(
            '<nav epub:type="landmarks">'
            '<a epub:type="titlepage" href="opening.xhtml#title%2Dpage">Title</a>'
            '<a epub:type="preview" href="opening.xhtml#excluded">Preview</a>'
            '<a epub:type="bodymatter" href="opening.xhtml#body">Body</a>'
            "</nav>"
        )
        strict = _xhtml(
            '<section xml:id="title-page"><h1>Resolved Fragment Title</h1></section>'
            '<section id="excluded"><p>eBook ISBN 9780306406157</p></section>'
            '<main id="body"><p>Body</p></main>'
        )
        recovered = strict.replace(b"</body>", b"")
        for content in (strict, recovered):
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                        ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(mode=report.resources[1].parse_mode):
                self.assertEqual(
                    {claim.field for claim in report.claims}, {OpeningField.TITLE}
                )
                self.assertEqual(
                    report.resources[1].selection_path,
                    ("epub3_landmark", "titlepage"),
                )
                self.assertEqual(report.usage.selected_documents, 1)
                self.assertEqual(
                    len(
                        {
                            resource.resource_identity
                            for resource in report.resources[1:]
                        }
                    ),
                    1,
                )

        for href in ("opening.xhtml", "opening.xhtml#"):
            empty_nav = _xhtml(
                '<nav epub:type="landmarks">'
                f'<a epub:type="titlepage" href="{href}">Title</a>'
                "</nav>"
            )
            equivalent = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", empty_nav, zipfile.ZIP_DEFLATED),
                        (
                            "EPUB/opening.xhtml",
                            _xhtml("<h1>Empty Fragment Equivalent</h1>"),
                            zipfile.ZIP_DEFLATED,
                        ),
                    ),
                )
            )
            with self.subTest(href=href):
                self.assertIn(
                    "Empty Fragment Equivalent",
                    {claim.raw_value for claim in equivalent.claims},
                )

    def test_invalid_fragment_roles_are_omitted_and_safe_siblings_continue(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("invalid", "invalid.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("invalid", "safe"),
        )
        nav = _xhtml(
            '<nav epub:type="landmarks"><a epub:type="titlepage" '
            'href="invalid.xhtml#target">Invalid target</a></nav>'
        )
        invalid_documents = (
            _xhtml("<h1>Missing Target</h1>"),
            _xhtml(
                '<section id="target"><h1>Duplicate One</h1></section>'
                '<section id="target"><h1>Duplicate Two</h1></section>'
            ),
        )
        for invalid in invalid_documents:
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                        ("EPUB/invalid.xhtml", invalid, zipfile.ZIP_DEFLATED),
                        ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe Sibling</h1>'), zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(document=hashlib.sha256(invalid).hexdigest()):
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Safe Sibling"],
                )
                self.assertIn("invalid_fragment_target", report.warnings)

    def test_body_target_removes_title_role_but_preserves_same_tier_dedication(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
        )
        nav = _xhtml(
            '<nav epub:type="landmarks">'
            '<a epub:type="titlepage" href="opening.xhtml#body">Title</a>'
            '<a epub:type="dedication" href="opening.xhtml#front">Dedication</a>'
            '<a epub:type="bodymatter" href="opening.xhtml#body">Body</a>'
            "</nav>"
        )
        report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                    (
                        "EPUB/opening.xhtml",
                        _xhtml(
                            '<section id="front"><h1 epub:type="fulltitle">Surviving Front Title</h1></section>'
                            '<main id="body"><h1>Body</h1></main>'
                        ),
                        zipfile.ZIP_DEFLATED,
                    ),
                ),
            )
        )
        self.assertEqual(
            report.resources[1].selection_path,
            ("epub3_landmark", "dedication"),
        )
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Surviving Front Title"],
        )

    def test_invalid_high_tier_fragments_fall_back_with_canonical_provenance(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml#guide"),),
        )
        for invalid_markup in (
            '<section id="guide"><h1>Guide Fallback</h1></section>',
            '<section id="missing"></section><section id="missing"></section><section id="guide"><h1>Guide Fallback</h1></section>',
        ):
            nav = _xhtml(
                '<nav epub:type="landmarks"><a epub:type="titlepage" '
                'href="opening.xhtml#missing">Invalid</a></nav>'
            )
            strict = _xhtml(invalid_markup)
            for content in (strict, strict.replace(b"</body>", b"")):
                report = _extract(
                    _opening_epub(
                        package=package,
                        entries=(
                            ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                            ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                        ),
                    )
                )
                with self.subTest(
                    invalid=hashlib.sha256(invalid_markup.encode()).hexdigest(),
                    mode=report.resources[1].parse_mode,
                ):
                    self.assertEqual(
                        report.resources[1].selection_path,
                        ("epub2_guide", "title_page"),
                    )
                    self.assertEqual(
                        [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                        ["Guide Fallback"],
                    )
                    self.assertIn("invalid_fragment_target", report.warnings)

        invalid_both_package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml#also-missing"),),
        )
        invalid_both = _extract(
            _opening_epub(
                package=invalid_both_package,
                entries=(
                    (
                        "EPUB/nav.xhtml",
                        _xhtml('<nav epub:type="landmarks"><a epub:type="titlepage" href="opening.xhtml#missing">Invalid</a></nav>'),
                        zipfile.ZIP_DEFLATED,
                    ),
                    ("EPUB/opening.xhtml", _xhtml('<h1 epub:type="fulltitle">Spine Semantic Fallback</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            invalid_both.resources[1].selection_path,
            ("linear_spine_fallback",),
        )
        self.assertIn(
            "Spine Semantic Fallback",
            {claim.raw_value for claim in invalid_both.claims},
        )

        legacy_package = _opening_package(
            version=2,
            items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml#missing"),),
        )
        legacy = _extract(
            _opening_epub(
                version=2,
                package=legacy_package,
                entries=(("EPUB/opening.xhtml", _xhtml('<h1 epub:type="fulltitle">Legacy Spine Fallback</h1>'), zipfile.ZIP_DEFLATED),),
            )
        )
        self.assertEqual(
            legacy.resources[0].selection_path, ("linear_spine_fallback",)
        )

    def test_valid_high_tier_role_behind_body_never_falls_through(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml#front"),),
        )
        nav = _xhtml(
            '<nav epub:type="landmarks">'
            '<a epub:type="titlepage" href="opening.xhtml#body">Blocked title</a>'
            '<a epub:type="bodymatter" href="opening.xhtml#body">Body</a>'
            "</nav>"
        )
        report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                    (
                        "EPUB/opening.xhtml",
                        _xhtml('<section id="front"><h1>Lower Guide Must Not Authorize</h1></section><main id="body"><p>Body</p></main>'),
                        zipfile.ZIP_DEFLATED,
                    ),
                ),
            )
        )
        self.assertFalse(report.claims)
        self.assertEqual(
            report.resources[1].selection_path,
            ("epub3_landmark", "titlepage"),
        )

    def test_invalid_only_optional_roles_never_authorize_claims(self) -> None:
        nav_package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
        ).replace(
            b'<itemref idref="opening"/>',
            b'<itemref idref="opening" linear="no"/>',
        )
        navigation = _xhtml(
            '<nav epub:type="landmarks"><a epub:type="titlepage" href="opening.xhtml#target">Target</a></nav>'
        )
        guide_package = _opening_package(
            version=2,
            items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml#target"),),
        ).replace(
            b'<itemref idref="opening"/>',
            b'<itemref idref="opening" linear="no"/>',
        )
        fragments = (
            '<section><h1 epub:type="fulltitle">Missing Target Claim</h1></section>',
            '<section id="target"></section><section id="target"><h1 epub:type="fulltitle">Duplicate Target Claim</h1></section>',
        )
        for markup in fragments:
            strict = _xhtml(markup)
            for content in (strict, strict.replace(b"</body>", b"")):
                nav_report = _extract(
                    _opening_epub(
                        package=nav_package,
                        entries=(
                            ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                            ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                        ),
                    )
                )
                guide_report = _extract(
                    _opening_epub(
                        version=2,
                        package=guide_package,
                        entries=(("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),),
                    )
                )
                for report, expected_path in (
                    (nav_report, ("epub3_landmark", "titlepage")),
                    (guide_report, ("epub2_guide", "title_page")),
                ):
                    with self.subTest(
                        duplicate=markup.count('id="target"') == 2,
                        mode=report.resources[-1].parse_mode,
                        path=expected_path,
                    ):
                        self.assertFalse(report.claims)
                        self.assertIn("invalid_fragment_target", report.warnings)
                        self.assertEqual(
                            report.resources[-1].selection_path, expected_path
                        )

    def test_same_resource_role_dedup_unions_title_context_canonically(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
        )
        for roles in (
            ("dedication", "titlepage"),
            ("titlepage", "dedication"),
            ("copyright-page", "titlepage"),
            ("titlepage", "copyright-page"),
        ):
            nav = _xhtml(
                '<nav epub:type="landmarks">'
                + "".join(
                    f'<a epub:type="{role}" href="opening.xhtml#front">{role}</a>'
                    for role in roles
                )
                + "</nav>"
            )
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                        (
                            "EPUB/opening.xhtml",
                            _xhtml('<section id="front"><h1>Role Union Title</h1></section>'),
                            zipfile.ZIP_DEFLATED,
                        ),
                    ),
                )
            )
            with self.subTest(roles=roles):
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(
                    report.resources[1].selection_path,
                    ("epub3_landmark", "titlepage"),
                )
                self.assertEqual(report.usage.selected_documents, 1)
                self.assertIn(
                    "Role Union Title", {claim.raw_value for claim in report.claims}
                )

        mixed_package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
            guide=(("title-page", "opening.xhtml#front"),),
        )
        mixed = _extract(
            _opening_epub(
                package=mixed_package,
                entries=(
                    (
                        "EPUB/nav.xhtml",
                        _xhtml(
                            '<nav epub:type="landmarks"><a epub:type="dedication" href="opening.xhtml#front">Front</a></nav>'
                        ),
                        zipfile.ZIP_DEFLATED,
                    ),
                    (
                        "EPUB/opening.xhtml",
                        _xhtml('<section id="front"><h1>Cross Precedence Title</h1></section>'),
                        zipfile.ZIP_DEFLATED,
                    ),
                ),
            )
        )
        self.assertEqual(
            mixed.resources[1].selection_path,
            ("epub3_landmark", "dedication"),
        )
        self.assertNotIn(
            "Cross Precedence Title", {claim.raw_value for claim in mixed.claims}
        )

    def test_recovery_encoding_uses_only_real_supported_declarations(self) -> None:
        lookalikes = (
            b'<!-- <meta charset="shift_jis"> --><h1 epub:type="fulltitle">Comment Safe</h1>',
            b'<script>"<meta charset=shift_jis>"</script><h1 epub:type="fulltitle">Script Safe</h1>',
            b'<p data-charset="shift_jis">prose charset=shift_jis</p><h1 epub:type="fulltitle">Attribute Safe</h1>',
        )
        for body in lookalikes:
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                report = _extract(self._single(body, media_type="text/html"))
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)

        cp1252 = (
            b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
            b'<h1 epub:type="fulltitle">Publisher\x92s Clock</h1>'
        )
        recovered = _extract(self._single(cp1252, media_type="text/html"))
        self.assertEqual(recovered.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "Publisher’s Clock", {claim.raw_value for claim in recovered.claims}
        )

        rejected_bodies = (
            b'<meta charset="shift_jis"><h1 epub:type="fulltitle">Unsupported</h1>',
            b'<meta charset="utf-8"><meta charset="windows-1252"><h1 epub:type="fulltitle">Conflict</h1>',
            b'\xef\xbb\xbf<meta charset="windows-1252"><h1 epub:type="fulltitle">BOM Conflict</h1>',
        )
        for body in rejected_bodies:
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                report = _extract(self._single(body, media_type="text/html"))
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.UNSUPPORTED_ENCODING,
                )

        xml_declared = b'''<?xml version="1.0" encoding="shift_jis"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body>'''
        xml_report = _extract(self._single(xml_declared))
        self.assertEqual(
            xml_report.resources[0].disposition,
            OpeningResourceDisposition.UNSUPPORTED_ENCODING,
        )

    def test_encoding_declaration_activity_duplicates_and_wide_signatures(self) -> None:
        inert = (
            b'<textarea><meta charset="shift_jis"></textarea><h1>Textarea Safe</h1>',
            b'<title><meta charset="shift_jis"></title><h1>Title Safe</h1>',
            b'<template><meta charset="shift_jis"></template><h1>Template Safe</h1>',
            b'<f:meta xmlns:f="urn:foreign" charset="shift_jis"></f:meta><h1>Foreign Meta Safe</h1>',
        )
        for body in inert:
            report = _extract(self._single(body, media_type="text/html"))
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)

        unclosed_foreign = _extract(
            self._single(
                b'<f:section xmlns:f="urn:foreign"><h1 epub:type="fulltitle">Foreign Child</h1>',
                media_type="text/html",
            )
        )
        self.assertEqual(
            unclosed_foreign.outcome,
            EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
        )
        self.assertFalse(unclosed_foreign.claims)

        equivalent = _extract(
            self._single(
                b'\xef\xbb\xbf<meta charset="utf8"><meta charset="UTF-8"><h1>Equivalent Declarations</h1>',
                media_type="text/html",
            )
        )
        self.assertEqual(equivalent.outcome, EpubInspectionOutcome.SUCCESS)

        duplicates = (
            b'<meta charset="utf-8" charset="utf-8"><h1>Duplicate Charset</h1>',
            b'<meta http-equiv="content-type" http-equiv="content-type" content="text/html; charset=utf-8"><h1>Duplicate Http Equiv</h1>',
            b'<?xml version="1.0" encoding="utf-8" encoding="utf-8"?><html><body><h1>Duplicate XML</h1></body>',
        )
        for body in duplicates:
            media_type = (
                "application/xhtml+xml" if body.startswith(b"<?xml") else "text/html"
            )
            report = _extract(self._single(body, media_type=media_type))
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.UNSUPPORTED_ENCODING,
                )

        wide_signatures = (
            b"<\x00?\x00x\x00m\x00l\x00",
            b"\x00<\x00?\x00x\x00m\x00l",
            b"<\x00\x00\x00?\x00\x00\x00x\x00\x00\x00",
            b"\x00\x00\x00<\x00\x00\x00?\x00\x00\x00x",
            "<html><body><h1>Wide LE</h1></body></html>".encode("utf-16-le"),
            "<html><body><h1>Wide BE</h1></body></html>".encode("utf-16-be"),
            "<!doctype html><html><body>Wide</body></html>".encode("utf-16-le"),
            "  <html><body>Whitespace Wide</body></html>".encode("utf-16-be"),
            "é—<html><body>Disrupted LE</body></html>".encode("utf-16-le"),
            "漢é<html><body>Disrupted BE</body></html>".encode("utf-16-be"),
            "é<!doctype html><html><body>Doctype LE</body></html>".encode("utf-16-le"),
            "漢  <html><body>Whitespace BE</body></html>".encode("utf-16-be"),
        )
        for body in wide_signatures:
            report = _extract(self._single(body, media_type="text/html"))
            with self.subTest(signature=body[:8].hex()):
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.UNSUPPORTED_ENCODING,
                )

        strict_wide = _extract(
            self._single(
                '<?xml version="1.0" encoding="utf-16"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops">'
                '<body><h1 epub:type="fulltitle">Wide Strict</h1></body></html>'.encode(
                    "utf-16"
                )
            )
        )
        self.assertEqual(
            strict_wide.resources[0].disposition,
            OpeningResourceDisposition.UNSUPPORTED_ENCODING,
        )

        wide_tail = "<html><body><h1>Late wide</h1></body></html>".encode(
            "utf-16-le"
        )
        late_wide = (
            b"A" * 2_100 + wide_tail,
            b"\xc3\xa9" * 1_100
            + "<html><body>Late BE</body></html>".encode("utf-16-be"),
        )
        for body in late_wide:
            report = _extract(self._single(body, media_type="text/html"))
            with self.subTest(length=len(body)):
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.UNSUPPORTED_ENCODING,
                )

        near_limit = b"x" * (1_048_000 - len(wide_tail)) + wide_tail
        near_package = _opening_package(
            version=2,
            items=(("opening", "opening.html", "text/html", ""),),
            spine=("opening",),
            guide=(("title-page", "opening.html"),),
        )
        near_report = _extract(
            _opening_epub(
                version=2,
                package=near_package,
                entries=(("EPUB/opening.html", near_limit, zipfile.ZIP_STORED),),
            )
        )
        self.assertEqual(
            near_report.resources[0].disposition,
            OpeningResourceDisposition.UNSUPPORTED_ENCODING,
        )

        supported_without_nul = (
            b'<meta charset="utf-8"><h1>UTF-8 \xc3\xa9</h1>',
            b'<meta charset="windows-1252"><h1>CP1252 \x92</h1>',
            b'<meta charset="iso-8859-1"><h1>Latin-1 \xe9</h1>',
            b'<meta charset="shift_jis"><h1>Unsupported But Not Wide</h1>',
        )
        for sample in (
            "UTF-8 é".encode("utf-8"),
            "Windows ’".encode("windows-1252"),
            "Latin é".encode("iso-8859-1"),
            "日本語".encode("shift_jis"),
        ):
            self.assertFalse(_looks_wide_encoded(sample))
        dispositions = []
        for body in supported_without_nul:
            report = _extract(self._single(body, media_type="text/html"))
            dispositions.append(report.resources[0].disposition)
        self.assertEqual(
            dispositions,
            [
                OpeningResourceDisposition.SELECTED,
                OpeningResourceDisposition.SELECTED,
                OpeningResourceDisposition.SELECTED,
                OpeningResourceDisposition.UNSUPPORTED_ENCODING,
            ],
        )

    def test_xml_declaration_lexer_ignores_quoted_lookalikes(self) -> None:
        accepted = (
            b'''<?xml version="1.0" encoding="utf-8" standalone="yes"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Double Quote Actual</h1></body>''',
            b'''<?xml version='1.1' encoding='utf8' standalone='no'?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Single Quote Actual</h1></body>''',
        )
        for body in accepted:
            report = _extract(self._single(body))
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)

        malformed = (
            b'''<?xml note="encoding='shift_jis'" version="1.0"?><html><body>x</body>''',
            b'''<?xml encoding="utf-8" version="1.0"?><html><body>x</body>''',
            b'''<?xml version="2.0" encoding="utf-8"?><html><body>x</body>''',
            b'''<?xml version="1.0" standalone="encoding='shift_jis'"?><html><body>x</body>''',
            b'''<?xml version="1.0" standalone="maybe"?><html><body>x</body>''',
            b'''<?xml version="1.0" encoding="utf-8" note="x"?><html><body>x</body>''',
            b'''<?xml encoding="utf-8"?><html><body>x</body>''',
            b'''<?xml version="1.0" encoding="utf-8"encoding="utf-8"?><html><body>x</body>''',
            b'''<?xml version="1.0" encoding=utf-8?><html><body>x</body>''',
            b'''<?xml version="1.0" encoding="utf-8?><html><body>x</body>''',
            b'''<?xml version="1.0" encoding="utf-8" encoding='windows-1252'?><html><body>x</body>''',
        )
        for body in malformed:
            report = _extract(self._single(body))
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.UNSUPPORTED_ENCODING,
                )

        unsupported = _extract(
            self._single(
                b'''<?xml version='1.0' encoding='shift_jis'?><html><body>x</body>'''
            )
        )
        self.assertEqual(
            unsupported.resources[0].disposition,
            OpeningResourceDisposition.UNSUPPORTED_ENCODING,
        )

    def test_raw_spine_annotations_are_partitioned_by_f06_acceptance(self) -> None:
        package = b'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:bad="urn:bad" unique-identifier="id" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:uuid:00000000-0000-4000-8000-000000000009</dc:identifier><dc:title>Spine Partition</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-01-13T00:00:00Z</meta><itemref idref="trap"/></metadata>
<manifest><item id="skip" href="skip.xhtml" media-type="application/xhtml+xml"/><item id="trap" href="trap.xhtml" media-type="application/xhtml+xml"/><item id="safe" href="safe.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="skip" linear="no"/><itemref idref="skip"/><itemref idref="trap"><meta/></itemref><bad:itemref idref="trap"/><itemref idref="missing"/><itemref idref="safe"/></spine>
</package>'''
        report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/skip.xhtml", _xhtml('<h1 epub:type="fulltitle">Skipped</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/trap.xhtml", _xhtml('<h1 epub:type="fulltitle">Trap</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Accepted Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Accepted Safe"],
        )
        self.assertEqual(report.usage.selected_documents, 1)
        self.assertIn("invalid_spine_item", report.warnings)

    def test_raw_linear_values_are_exact_and_all_no_stays_empty(self) -> None:
        base = _opening_package(
            items=(
                ("first", "first.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("first", "safe"),
        )
        entries = (
            ("EPUB/first.xhtml", _xhtml('<h1 epub:type="fulltitle">First</h1>'), zipfile.ZIP_DEFLATED),
            ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe</h1>'), zipfile.ZIP_DEFLATED),
        )
        cases = (
            (None, ["First", "Safe"], False),
            ("yes", ["First", "Safe"], False),
            ("no", ["Safe"], False),
            ("", ["Safe"], True),
            (" NO ", ["Safe"], True),
            ("NO", ["Safe"], True),
        )
        for linear, expected, warned in cases:
            first = b'<itemref idref="first"/>'
            replacement = (
                first
                if linear is None
                else f'<itemref idref="first" linear="{linear}"/>'.encode()
            )
            report = _extract(
                _opening_epub(
                    package=base.replace(first, replacement), entries=entries
                )
            )
            with self.subTest(linear=linear):
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    expected,
                )
                self.assertEqual(
                    "unusable_spine_relationship" in report.warnings, warned
                )

        all_no = base.replace(
            b'<itemref idref="first"/>',
            b'<itemref idref="first" linear="no"/>',
        ).replace(
            b'<itemref idref="safe"/>',
            b'<itemref idref="safe" linear="no"/>',
        )
        empty = _extract(_opening_epub(package=all_no, entries=entries))
        self.assertEqual(empty.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertFalse(empty.resources)
        self.assertEqual(empty.usage.selected_documents, 0)

    def test_post_open_reports_require_the_complete_bound_provenance_set(self) -> None:
        report = _extract(self._single(_xhtml("<p>No claims here.</p>")))
        self.assertEqual(report.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE)
        self.assertTrue(report.resources)
        self.assertGreater(report.usage.opened_resources, 0)
        for removed in (
            ("source_fingerprint",),
            ("same_file_lineage", "selection_digest"),
            ("source_fingerprint", "same_file_lineage", "selection_digest"),
        ):
            data = report.to_data()
            for field in removed:
                data[field] = None
            serialized = json.dumps(
                data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            with self.subTest(removed=removed), self.assertRaises(EvidenceValueError):
                EpubOpeningReport.from_json(serialized)

        pristine = EpubOpeningReport(
            EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
            EpubOpeningLimits(),
            report.source_fingerprint,
        )
        self.assertEqual(
            EpubOpeningReport.from_json(pristine.to_json()), pristine
        )

    def test_foreign_local_name_collisions_are_semantically_inert(self) -> None:
        strict = b'''<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:f="urn:foreign" xmlns:s="http://www.w3.org/2000/svg"><body><f:h1 epub:type="fulltitle">Foreign Title</f:h1><f:section epub:type="bodymatter"><f:p epub:type="preview">Foreign Boundary</f:p></f:section><s:text>Renderable SVG Text</s:text><h1 epub:type="fulltitle">Safe HTML Title</h1></body></html>'''
        recovered = strict.replace(b"</body>", b"")
        for media_type, body in (
            ("application/xhtml+xml", strict),
            ("text/html", recovered),
        ):
            with self.subTest(media_type=media_type):
                report = _extract(self._single(body, media_type=media_type))
                titles = {
                    claim.raw_value
                    for claim in report.claims
                    if claim.field is OpeningField.TITLE
                }
                self.assertEqual(titles, {"Safe HTML Title"})
                self.assertNotIn("explicitly_excluded_resource", report.warnings)
                self.assertNotIn("body_transition_reached", report.warnings)

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:f="urn:foreign"><body><f:nav epub:type="landmarks"><f:a epub:type="titlepage" href="fake.xhtml">Fake</f:a></f:nav></body></html>'''
        for nav_body in (navigation, navigation.replace(b"</body>", b"")):
            nav_report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", nav_body, zipfile.ZIP_DEFLATED),
                        ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Foreign Selected</h1>'), zipfile.ZIP_DEFLATED),
                        ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Spine Safe</h1>'), zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(navigation=hashlib.sha256(nav_body).hexdigest()):
                self.assertEqual(
                    [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
                    ["Spine Safe"],
                )

    def test_recovery_undeclared_qnames_never_fall_back_to_html_identity(self) -> None:
        content = b'''<!doctype html><html xmlns:epub="http://www.idpf.org/2007/ops"><body><f:h1 epub:type="fulltitle">Undeclared Foreign Title</f:h1><f:section epub:type="bodymatter"><f:p epub:type="preview">Foreign Boundary</f:p></f:section><h1>Recovered HTML Safe</h1></body></html>'''
        report = _extract(self._single(content, media_type="text/html"))
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Recovered HTML Safe"],
        )
        self.assertNotIn("body_transition_reached", report.warnings)
        self.assertNotIn("explicitly_excluded_resource", report.warnings)

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns:epub="http://www.idpf.org/2007/ops"><body><f:nav epub:type="landmarks"><f:a epub:type="titlepage" href="fake.xhtml">Fake</f:a></f:nav></body></html>'''
        nav_report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">QName Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">QName Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
            ["QName Safe"],
        )

        declared_alias = b'''<!doctype html><html xmlns:x="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><x:h1 epub:type="fulltitle">Declared Alias Title</x:h1></body></html>'''
        alias_report = _extract(
            self._single(declared_alias, media_type="text/html")
        )
        self.assertIn(
            "Declared Alias Title", {claim.raw_value for claim in alias_report.claims}
        )

    def test_empty_prefix_bindings_are_unknown_across_recovery_consumers(self) -> None:
        body = b'''<!doctype html><html xmlns:f="" xmlns:epub="" xmlns:e="http://www.idpf.org/2007/ops"><body><f:h1 e:type="fulltitle">Empty Element Prefix</f:h1><h1 epub:type="fulltitle">Empty Attribute Prefix</h1><f:section e:type="bodymatter"><f:p e:type="preview">False Boundary</f:p></f:section><f:meta charset="shift_jis"></f:meta><h1 e:type="fulltitle">Empty Binding Safe</h1></body></html>'''
        report = _extract(self._single(body))
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Empty Binding Safe"],
        )
        self.assertNotIn("body_transition_reached", report.warnings)
        self.assertNotIn("explicitly_excluded_resource", report.warnings)

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns:f="" xmlns:epub="" xmlns:e="http://www.idpf.org/2007/ops"><body><f:nav e:type="landmarks"><f:a e:type="titlepage" href="fake.xhtml">Fake</f:a></f:nav><nav epub:type="landmarks"><a epub:type="titlepage" href="fake.xhtml">Also fake</a></nav></body></html>'''
        nav_report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Empty Nav Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Empty Nav Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
            ["Empty Nav Safe"],
        )

    def test_empty_default_namespace_resets_reenter_recovery_html(self) -> None:
        root = b'''<!doctype html><html xmlns="" xmlns:e="http://www.idpf.org/2007/ops"><head><meta charset="windows-1252"></head><body><p e:type="publisher">Root Reset Publisher\x92s</p><h1 e:type="fulltitle">Root Reset Title</h1><section e:type="bodymatter"><h1 e:type="fulltitle">Root Body Fake</h1></section></body></html>'''
        root_report = _extract(self._single(root))
        self.assertEqual(root_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            [(claim.field, claim.raw_value) for claim in root_report.claims],
            [
                (OpeningField.PUBLISHER, "Root Reset Publisher’s"),
                (OpeningField.TITLE, "Root Reset Title"),
            ],
        )
        self.assertIn("body_transition_reached", root_report.warnings)

        subtree = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:outer-foreign"><section xmlns=""><meta charset="iso-8859-1"><p e:type="publisher">Subtree Caf\xe9 Publisher</p><svg><h1 e:type="fulltitle">Reset SVG Fake</h1></svg><math><h1 e:type="fulltitle">Reset Math Fake</h1></math><h1 e:type="fulltitle">Subtree Reset Title</h1></section><h1 e:type="fulltitle">Later Foreign Fake</h1></svg><h1 e:type="fulltitle">Later Safe Sibling</h1></body></html>'''
        subtree_report = _extract(self._single(subtree))
        self.assertEqual(subtree_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            [(claim.field, claim.raw_value) for claim in subtree_report.claims],
            [
                (OpeningField.PUBLISHER, "Subtree Café Publisher"),
                (OpeningField.TITLE, "Subtree Reset Title"),
                (OpeningField.TITLE, "Later Safe Sibling"),
            ],
        )

    def test_empty_default_namespace_resets_authorize_navigation(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation_documents = (
            b'''<!doctype html><html xmlns="" xmlns:e="http://www.idpf.org/2007/ops"><body><nav e:type="landmarks"><a e:type="titlepage" href="safe.xhtml">Root reset</a></nav></body></html>''',
            b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:outer-foreign"><nav xmlns="" e:type="landmarks"><a e:type="titlepage" href="safe.xhtml">Subtree reset</a></nav><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">Later foreign</a></nav></svg></body></html>''',
        )
        for navigation in navigation_documents:
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                        ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Empty Reset Nav Fake</h1>'), zipfile.ZIP_DEFLATED),
                        ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Empty Reset Nav Safe</h1>'), zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(navigation=hashlib.sha256(navigation).hexdigest()):
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Empty Reset Nav Safe"],
                )
                self.assertEqual(report.usage.navigation_links, 1)
                self.assertEqual(
                    report.resources[1].selection_path,
                    ("epub3_landmark", "titlepage"),
                )

    def test_duplicate_recovery_authority_is_order_independent_and_closed(self) -> None:
        duplicate_namespaces = (
            b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns=""><head><meta charset="windows-1252"></head><body><h1>Duplicate Default \x92</h1></html>''',
            b'''<!doctype html><html xmlns="" xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="iso-8859-1"></head><body><h1>Duplicate Default Reverse \xe9</h1></html>''',
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:e="urn:wrong"><body><h1>Duplicate Prefix \x92</h1></html>''',
            b'''<!doctype html><html xmlns:e="urn:wrong" xmlns:e="http://www.idpf.org/2007/ops"><head><f:meta xmlns:f="urn:foreign" charset="shift_jis"></f:meta><meta charset="iso-8859-1"></head><body><h1>Duplicate Prefix Reverse \xe9</h1></html>''',
            b'''<!doctype html><meta charset="windows-1252"><html xmlns="urn:unique-foreign" xmlns:e="http://www.idpf.org/2007/ops" xmlns:e="urn:wrong" xmlns:f="urn:other"><f:meta charset="shift_jis"></f:meta><meta charset="shift_jis"><h1>Mixed Unique Foreign \x92</h1></html><meta charset="cp1252">''',
            b'''<!doctype html><meta charset="iso-8859-1"><html xmlns="urn:unique-foreign" xmlns:e="urn:wrong" xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:other"><meta charset="shift_jis"><f:meta charset="shift_jis"></f:meta><h1>Mixed Reverse \xe9</h1></html><meta charset="latin-1">''',
        )
        for body in duplicate_namespaces:
            report = _extract(self._single(body))
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.MALFORMED,
                )
                self.assertIn("malformed_selected_resource", report.warnings)

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        for bindings in (
            'xmlns:e="http://www.idpf.org/2007/ops" xmlns:e="urn:wrong"',
            'xmlns:e="urn:wrong" xmlns:e="http://www.idpf.org/2007/ops"',
        ):
            navigation = (
                '<!doctype html><html ' + bindings + '><body><nav e:type="landmarks">'
                '<a e:type="titlepage" href="fake.xhtml">bad</a></nav></html>'
            ).encode()
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                        ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Namespace Nav Fake</h1>'), zipfile.ZIP_DEFLATED),
                        ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Namespace Nav Safe</h1>'), zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(bindings=bindings):
                self.assertEqual(
                    report.resources[0].disposition,
                    OpeningResourceDisposition.MALFORMED,
                )
                self.assertIn("unusable_navigation_document", report.warnings)
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Namespace Nav Safe"],
                )

        duplicate_semantics = (
            b'''<!doctype html><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="http://www.idpf.org/2007/ops"><body><p e:type="publisher" f:type="publisher">Forged Publisher</p><section hidden hidden><h1 e:type="fulltitle">Hidden Forged</h1></section><h1 e:type="fulltitle">Duplicate Authority Safe</h1></html>''',
            b'''<!doctype html><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="http://www.idpf.org/2007/ops"><body><p f:type="publisher" e:type="publisher">Forged Publisher</p><section aria-hidden="false" aria-hidden="true"><h1 e:type="fulltitle">ARIA Forged</h1></section><h1 e:type="fulltitle">Duplicate Authority Safe</h1></html>''',
        )
        for body in duplicate_semantics:
            report = _extract(self._single(body))
            values = {claim.raw_value for claim in report.claims}
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(values, {"Duplicate Authority Safe"})

    def test_duplicate_recovery_relationship_and_identity_use_safe_fallback(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        for hrefs in (
            'href="fake.xhtml" href="safe.xhtml"',
            'href="safe.xhtml" href="fake.xhtml"',
        ):
            navigation = (
                '<!doctype html><html xmlns:e="http://www.idpf.org/2007/ops"><body>'
                '<nav e:type="landmarks"><a e:type="titlepage" '
                + hrefs
                + '>Duplicate href</a></nav></html>'
            ).encode()
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                        ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Forged Target</h1>'), zipfile.ZIP_DEFLATED),
                        ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Relationship Fallback Safe</h1>'), zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(hrefs=hrefs):
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Relationship Fallback Safe"],
                )
                self.assertIn("invalid_navigation_target", report.warnings)
                self.assertEqual(
                    report.resources[1].selection_path,
                    ("linear_spine_fallback",),
                )

        identity_package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("opening",),
        )
        for identities in (
            'id="target" xml:id="other"',
            'xml:id="other" id="target"',
            'id="target" id="target"',
        ):
            navigation = _xhtml(
                '<nav epub:type="landmarks"><a epub:type="titlepage" href="opening.xhtml#target">Target</a></nav>'
            )
            content = (
                '<!doctype html><html xmlns:epub="http://www.idpf.org/2007/ops"><body><section '
                + identities
                + '><h1 epub:type="fulltitle">Identity Fallback Safe</h1></section></html>'
            ).encode()
            report = _extract(
                _opening_epub(
                    package=identity_package,
                    entries=(
                        ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                        ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(identities=identities):
                self.assertIn("invalid_fragment_target", report.warnings)
                self.assertEqual(
                    report.resources[1].selection_path,
                    ("linear_spine_fallback",),
                )
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Identity Fallback Safe"],
                )

    def test_duplicate_manifest_media_and_valid_fallback_are_order_independent(self) -> None:
        navigation = _xhtml(
            '<nav epub:type="landmarks"><a epub:type="titlepage" '
            'href="opening.xhtml">Opening</a></nav>'
        )
        content = _xhtml(
            '<h1 epub:type="fulltitle">Duplicate Manifest Safe</h1>'
        )
        duplicate_items = (
            ("content", "opening.xhtml", "application/xhtml+xml", ""),
            ("alias", "opening.xhtml", "image/png", ""),
        )
        for declarations in (duplicate_items, tuple(reversed(duplicate_items))):
            package = _opening_package(
                items=(
                    ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                    *declarations,
                ),
                spine=("content",),
            )
            report = _extract(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                        ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                    ),
                )
            )
            with self.subTest(declarations=declarations):
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(
                    [
                        claim.raw_value
                        for claim in report.claims
                        if claim.field is OpeningField.TITLE
                    ],
                    ["Duplicate Manifest Safe"],
                )
                self.assertEqual(
                    report.resources[-1].selection_path,
                    ("linear_spine_fallback",),
                )

        fallback_package = _opening_package(
            items=(
                (
                    "source",
                    "source.bin",
                    "application/octet-stream",
                    ' fallback="content"',
                ),
                ("content", "opening.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("source",),
        )
        fallback = _extract(
            _opening_epub(
                package=fallback_package,
                entries=(
                    ("EPUB/source.bin", b"unsupported", zipfile.ZIP_STORED),
                    ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(fallback.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            [
                claim.raw_value
                for claim in fallback.claims
                if claim.field is OpeningField.TITLE
            ],
            ["Duplicate Manifest Safe"],
        )
        self.assertEqual(
            fallback.resources[0].selection_path,
            ("linear_spine_fallback",),
        )

    def test_duplicate_navigation_hrefs_count_before_validation(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )

        def publication(count: int, reverse: bool = False) -> bytes:
            duplicate = (
                'href="safe.xhtml" href="fake.xhtml"'
                if reverse
                else 'href="fake.xhtml" href="safe.xhtml"'
            )
            links = "".join(
                '<a epub:type="titlepage" ' + duplicate + '>bad</a>'
                for _index in range(count)
            )
            navigation = _xhtml(
                '<nav epub:type="landmarks">' + links + "</nav>"
            )
            return _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Budget Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Budget Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )

        for count, reverse in ((1, False), (2, False), (2, True)):
            report = _extract(
                publication(count, reverse), EpubOpeningLimits(navigation_links=2)
            )
            with self.subTest(count=count, reverse=reverse):
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(report.usage.navigation_links, count)
                self.assertIn("invalid_navigation_target", report.warnings)
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    ["Budget Safe"],
                )

        above = _extract(
            publication(3), EpubOpeningLimits(navigation_links=2)
        )
        self.assertEqual(above.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)
        self.assertFalse(above.claims)
        self.assertFalse(above.resources)

    def test_implicit_svg_and_math_subtrees_remain_foreign_in_recovery(self) -> None:
        body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg><meta charset="shift_jis"><h1 id="fake" e:type="fulltitle">SVG Fake</h1><section e:type="bodymatter"><p e:type="preview"><math><span e:type="publisher">Math Publisher Fake</span></math></p></section></svg><math><svg><h1 e:type="fulltitle">Nested Foreign Fake</h1></svg></math><h1 id="safe" e:type="fulltitle">Foreign Content Safe</h1></html>'''
        report = _extract(self._single(body))
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Foreign Content Safe"],
        )
        self.assertNotIn("body_transition_reached", report.warnings)
        self.assertNotIn("explicitly_excluded_resource", report.warnings)

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">SVG nav</a></nav><math><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">Nested nav</a></nav></math></svg><nav e:type="landmarks"><a e:type="titlepage" href="safe.xhtml#safe">Safe nav sibling</a></nav></html>'''
        nav_report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Foreign Nav Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 id="safe" epub:type="fulltitle">Foreign Nav Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
            ["Foreign Nav Safe"],
        )
        self.assertEqual(
            nav_report.resources[1].selection_path,
            ("epub3_landmark", "titlepage"),
        )

    def test_recovery_stack_never_applies_html_closing_rules_to_foreign_nodes(self) -> None:
        body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg><p><li><h1 e:type="fulltitle">Foreign P LI Fake</h1></li></p><math><p><span e:type="publisher">Nested Math Fake</span></p></math></svg></svg></math><h1 e:type="fulltitle">Stack Safe Title</h1><p e:type="publisher">Stack Safe Publisher</p><section e:type="bodymatter">Body</section></html>'''
        report = _extract(self._single(body))
        values = {(claim.field, claim.raw_value) for claim in report.claims}
        self.assertIn((OpeningField.TITLE, "Stack Safe Title"), values)
        self.assertIn((OpeningField.PUBLISHER, "Stack Safe Publisher"), values)
        self.assertNotIn("Foreign P LI Fake", {value for _field, value in values})
        self.assertNotIn("Nested Math Fake", {value for _field, value in values})
        self.assertIn("body_transition_reached", report.warnings)

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg><p><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">Foreign</a></nav></p><math><li><meta charset="shift_jis"></li></math></svg></math></svg><nav e:type="landmarks"><a e:type="titlepage" href="safe.xhtml">Safe</a></nav></html>'''
        nav_report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Stack Nav Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Stack Nav Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
            ["Stack Nav Safe"],
        )
        self.assertEqual(nav_report.usage.navigation_links, 1)

    def test_unqualified_foreign_end_tags_use_the_open_element_identity(self) -> None:
        body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:explicit-svg"><meta charset="shift_jis"><h1 e:type="fulltitle">Explicit SVG Fake</h1></svg><math xmlns="urn:explicit-math"><h1 e:type="fulltitle">Explicit Math Fake</h1></math><meta charset="windows-1252"><h1 e:type="fulltitle">Publisher\x92s Explicit Close Safe</h1></html>'''
        report = _extract(self._single(body))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Publisher’s Explicit Close Safe"],
        )

        qualified = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:qualified"><body><f:svg><f:h1 e:type="fulltitle">Qualified Fake</f:h1></f:svg><h1 e:type="fulltitle">Qualified Close Safe</h1></html>'''
        qualified_report = _extract(self._single(qualified))
        self.assertEqual(
            [claim.raw_value for claim in qualified_report.claims if claim.field is OpeningField.TITLE],
            ["Qualified Close Safe"],
        )

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:explicit-svg"><meta charset="shift_jis"><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">Fake</a></nav></svg><math xmlns="urn:explicit-math"><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">Fake</a></nav></math><meta charset="windows-1252"><nav e:type="landmarks"><a e:type="titlepage" href="safe.xhtml">Publisher\x92s Safe</a></nav></html>'''
        nav_report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Explicit Nav Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Explicit Nav Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
            ["Explicit Nav Safe"],
        )
        self.assertEqual(
            nav_report.resources[1].selection_path,
            ("epub3_landmark", "titlepage"),
        )

    def test_unqualified_end_tags_resolve_the_current_default_namespace(self) -> None:
        body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:s="urn:foreign"><body><s:template><meta charset="shift_jis"></template><meta charset="shift_jis"><h1 e:type="fulltitle">Wrong Default Fake</h1></s:template><meta charset="windows-1252"><h1 e:type="fulltitle">Publisher\x92s Default Safe</h1></html>'''
        report = _extract(self._single(body))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Publisher’s Default Safe"],
        )

        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:s="urn:foreign"><body><nav e:type="landmarks"><s:nav></nav><a e:type="titlepage" href="fake.xhtml">Wrong default</a></s:nav><a e:type="titlepage" href="safe.xhtml">Safe default</a></nav></html>'''
        nav_report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Default Namespace Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Default Namespace Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE],
            ["Default Namespace Safe"],
        )
        self.assertEqual(nav_report.usage.navigation_links, 1)

    def test_unknown_prefix_identities_are_prefix_specific(self) -> None:
        bodies = (
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops"><body><opaquealpha:template></opaquebeta:template><meta charset="shift_jis"><h1 e:type="fulltitle">Missing Prefix Fake</h1></opaquealpha:template><h1 e:type="fulltitle">Missing Prefix Safe</h1></body></html>''',
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="" xmlns:g=""><body><f:template></g:template><meta charset="shift_jis"><h1 e:type="fulltitle">Empty Prefix Fake</h1></f:template><h1 e:type="fulltitle">Empty Prefix Safe</h1></body></html>''',
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f=""><body><f:template></g:template><meta charset="shift_jis"><h1 e:type="fulltitle">Mixed Prefix Fake</h1></f:template><h1 e:type="fulltitle">Mixed Prefix Safe</h1></body></html>''',
        )
        for body, expected in zip(
            bodies,
            ("Missing Prefix Safe", "Empty Prefix Safe", "Mixed Prefix Safe"),
            strict=True,
        ):
            report = _extract(self._single(body))
            with self.subTest(body=hashlib.sha256(body).hexdigest()):
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    [expected],
                )
                self.assertNotIn("opaquealpha", report.to_json())
                self.assertNotIn("opaquebeta", report.to_json())

        duplicate = b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:first" xmlns:f="urn:second"><body><f:template><f:meta charset="shift_jis"></f:meta></g:template><meta charset="shift_jis"></f:template><h1 e:type="fulltitle">Duplicate Prefix Content</h1></body></html>'''
        duplicate_report = _extract(self._single(duplicate))
        self.assertEqual(
            duplicate_report.resources[0].disposition,
            OpeningResourceDisposition.MALFORMED,
        )
        self.assertIn("malformed_selected_resource", duplicate_report.warnings)

    def test_self_closing_recovery_tags_only_close_the_start_they_pushed(self) -> None:
        void_names = (
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        )
        for name in void_names:
            body = (
                '<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:e="http://www.idpf.org/2007/ops"><body>'
                f'<{name} xmlns="urn:foreign" hidden><{name} xmlns="http://www.w3.org/1999/xhtml"/>'
                '<h1 xmlns="http://www.w3.org/1999/xhtml" e:type="fulltitle">Void Fake</h1>'
                f'</{name}><h1 e:type="fulltitle">Void {name} Safe</h1></body></html>'
            ).encode()
            report = _extract(self._single(body))
            with self.subTest(name=name):
                self.assertEqual(
                    [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
                    [f"Void {name} Safe"],
                )

        nonvoid = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:foreign"><body><template/><f:template/><section xmlns="urn:foreign"/><h1 e:type="fulltitle">Nonvoid Self Close Safe</h1></body></html>'''
        nonvoid_report = _extract(self._single(nonvoid))
        self.assertEqual(
            [claim.raw_value for claim in nonvoid_report.claims if claim.field is OpeningField.TITLE],
            ["Nonvoid Self Close Safe"],
        )

    def test_html_implied_closes_stop_at_each_foreign_boundary(self) -> None:
        for outer, inner in (("p", "p"), ("li", "li"), ("dt", "dd"), ("dd", "dt")):
            body = (
                '<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:e="http://www.idpf.org/2007/ops"><body>'
                f'<{outer} e:type="publisher">Outer Before '
                '<svg><math><div xmlns="http://www.w3.org/1999/xhtml">'
                f'</{outer}><{inner}>Inner Reentry</{inner}></div></math></svg> Outer Tail'
                f'</{outer}><h1 e:type="fulltitle">Boundary Safe Title</h1></html>'
            ).encode()
            report = _extract(self._single(body))
            publishers = [
                claim.raw_value
                for claim in report.claims
                if claim.field is OpeningField.PUBLISHER
            ]
            with self.subTest(outer=outer, inner=inner):
                self.assertEqual(len(publishers), 1)
                self.assertIn("Outer Tail", publishers[0])
                self.assertIn(
                    "Boundary Safe Title",
                    {claim.raw_value for claim in report.claims},
                )

    def test_cross_foreign_misnested_end_cannot_authorize_navigation(self) -> None:
        package = _opening_package(
            items=(
                ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                ("fake", "fake.xhtml", "application/xhtml+xml", ""),
                ("safe", "safe.xhtml", "application/xhtml+xml", ""),
            ),
            spine=("safe",),
        )
        navigation = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><nav e:type="landmarks"><svg><math><div xmlns="http://www.w3.org/1999/xhtml"></nav><a e:type="titlepage" href="fake.xhtml">False cross-boundary authority</a></div></math></svg><a e:type="titlepage" href="safe.xhtml">Safe contiguous authority</a></nav></html>'''
        report = _extract(
            _opening_epub(
                package=package,
                entries=(
                    ("EPUB/nav.xhtml", navigation, zipfile.ZIP_DEFLATED),
                    ("EPUB/fake.xhtml", _xhtml('<h1 epub:type="fulltitle">Cross Boundary Fake</h1>'), zipfile.ZIP_DEFLATED),
                    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Cross Boundary Safe</h1>'), zipfile.ZIP_DEFLATED),
                ),
            )
        )
        self.assertEqual(
            [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE],
            ["Cross Boundary Safe"],
        )
        self.assertEqual(
            report.resources[1].selection_path,
            ("epub3_landmark", "titlepage"),
        )
        self.assertEqual(report.usage.navigation_links, 1)

    @settings(max_examples=30, deadline=None)
    @given(st.text(max_size=120))
    def test_recovered_html_text_is_bounded_inert_data(self, value: str) -> None:
        content = (
            '<!doctype html><html><body><h1 epub:type="fulltitle">'
            + html.escape(value)
            + "</h1><script>fetch('https://example.invalid')</script></body></html>"
        ).encode("utf-8")
        report = _extract(self._single(content, media_type="text/html"))
        self.assertIn(
            report.outcome,
            {EpubInspectionOutcome.SUCCESS, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE},
        )
        self.assertNotIn("example.invalid", report.to_json())


if __name__ == "__main__":
    unittest.main()
