from __future__ import annotations

import hashlib
import html
import json
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import mechanical_librarian.adapters.evidence_extractors.epub_opening as opening
from mechanical_librarian.adapters.evidence_extractors.epub_opening import (
    EpubOpeningExtractor,
    _looks_wide_encoded,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageRequest,
    EpubPackageSource,
    epub_file_lineage,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.epub_opening import (
    EPUB_OPENING_EXTRACTOR_VERSION,
    EpubOpeningLimits,
    EpubOpeningReport,
    OpeningField,
    OpeningParseMode,
    OpeningResourceDisposition,
)
from mechanical_librarian.domain.epub_source import FileReference
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
from tests.support.epub_packages import (
    MATERIALIZED_RECIPES,
    _opening_epub,
    _opening_package,
    _xhtml,
    opening_active_content_inert,
    opening_claim_families,
    opening_claims_complete,
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


def _single(body: bytes, *, media_type: str = "application/xhtml+xml") -> bytes:
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

_MATERIALIZED_OPENING_RECIPES = tuple(
    name for name in MATERIALIZED_RECIPES if name.startswith("opening_")
)


@pytest.mark.parametrize("name", _MATERIALIZED_OPENING_RECIPES)
def test_materialized_opening_registry_has_declared_top_level_outcomes(
    name: str,
) -> None:
    report = _extract(MATERIALIZED_RECIPES[name]())
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
    assert report.outcome == expected
    if name in {"opening_epub3_landmarks", "opening_epub2_guide"}:
        assert OpeningField.TITLE in {claim.field for claim in report.claims}
        assert OpeningField.IDENTIFIER not in {claim.field for claim in report.claims}
    if name == "opening_invalid_fragment_fallback":
        assert report.resources[1].selection_path == ("epub2_guide", "title_page")
        assert report.usage.navigation_links == 2_048
    if name in {
        "opening_empty_namespace_authority",
        "opening_implicit_foreign_content",
        "opening_xml_declaration_lookalike",
    }:
        assert len(
            [
                claim
                for claim in report.claims
                if claim.field is OpeningField.TITLE
            ]
        ) == (2 if name == "opening_empty_namespace_authority" else 1)
    if name == "opening_implicit_foreign_content":
        assert report.usage.navigation_links == 1
        assert report.resources[1].selection_path == ("epub3_landmark", "titlepage")


def test_citations_are_exact_versioned_path_free_and_round_trip_canonically() -> None:
    report = _extract(opening_claims_complete())
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert {claim.field for claim in report.claims} == {
        OpeningField.TITLE,
        OpeningField.CREATOR,
        OpeningField.CREATOR_ROLE,
        OpeningField.EDITION,
        OpeningField.IMPRINT,
        OpeningField.IDENTIFIER,
    }
    for claim in report.claims:
        citation = claim.citation
        assert citation.projection_end - citation.projection_start == len(citation.exact_excerpt)
        assert citation.excerpt_digest == hashlib.sha256(citation.exact_excerpt.encode("utf-8")).hexdigest()
        assert ".xhtml" not in report.to_json()
        assert "/Users/" not in report.to_json()
        assert citation.same_file_lineage == epub_file_lineage(report.source_fingerprint)
    assert report.same_file_lineage == epub_file_lineage(report.source_fingerprint)
    assert EpubOpeningReport.from_json(report.to_json()) == report


def test_plain_chapter_heading_never_becomes_a_book_title() -> None:
    report = _extract(_single(_xhtml("<h1>Chapter One</h1><p>Story prose.</p>")))
    assert report.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert not report.claims


def test_landmark_terms_require_landmarks_ancestry_and_epub_namespace() -> None:
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Real Title"]


def test_projection_preserves_nested_text_void_html_and_body_boundary() -> None:
    nested = _extract(
        _single(_xhtml('<h1 epub:type="fulltitle">The <em>Nested</em> Clock</h1>'))
    )
    assert next(claim for claim in nested.claims if claim.field is OpeningField.TITLE).raw_value == "The Nested Clock"
    recovered = _extract(
        _single(
            b'<!doctype html><html><body><input type="hidden"><br><meta charset="utf-8"><h1 epub:type="fulltitle">Visible Title</h1></body></html>',
            media_type="text/html",
        )
    )
    assert "Visible Title" in {claim.raw_value for claim in recovered.claims}
    bounded_voids = _extract(
        _single(
            ("<!doctype html><html><body>" + "<br>" * 100 + '<h1 epub:type="fulltitle">After Voids</h1></body></html>').encode(),
            media_type="text/html",
        )
    )
    assert bounded_voids.outcome == EpubInspectionOutcome.SUCCESS
    body = _extract(
        _single(
            _xhtml('<h1 epub:type="fulltitle">Front Title</h1><main epub:type="bodymatter"><p>eBook ISBN 9780306406157</p></main>')
        )
    )
    assert {claim.field for claim in body.claims} == {OpeningField.TITLE}
    structural_break = _extract(
        _single(_xhtml('<h1 epub:type="fulltitle">The<br/>Clock</h1>'))
    )
    assert "The Clock" in {claim.raw_value for claim in structural_break.claims}
    empty_body = _extract(
        _single(
            _xhtml('<h1 epub:type="fulltitle">Front</h1><hr epub:type="bodymatter"/><p>eBook ISBN 9780306406157</p>')
        )
    )
    assert {claim.field for claim in empty_body.claims} == {OpeningField.TITLE}
    nested_body = _extract(
        _single(
            _xhtml('<h1 epub:type="fulltitle">Front<span epub:type="bodymatter">Story</span></h1>')
        )
    )
    assert nested_body.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE

    assert "body_transition_reached" in nested_body.warnings
    implied = _extract(
        _single(
            ("<!doctype html><html><body>" + "<p><span>x" * 40 + '<h1 epub:type="fulltitle">Implied Close Title</h1></body></html>').encode(),
            media_type="text/html",
        )
    )
    assert implied.outcome == EpubInspectionOutcome.SUCCESS
    assert "Implied Close Title" in {claim.raw_value for claim in implied.claims}


def test_foreign_inclusion_is_rejected_without_losing_a_safe_sibling() -> None:
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
    assert "Injected" not in report.to_json()
    assert "Safe Sibling" in {claim.raw_value for claim in report.claims}
    assert OpeningResourceDisposition.MALFORMED in {item.disposition for item in report.resources}


@pytest.mark.parametrize(
    "injected",
    (
        b'''<?xml version="1.0"?><include xmlns="http://www.w3.org/2001/XInclude"><fallback><h1 xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" epub:type="fulltitle">Default Injected</h1></fallback></include>''',
        b'''<!doctype html><html xmlns:q="http://www.w3.org/2001/XInclude"><body><q:include><q:fallback><h1 epub:type="fulltitle">Alias Injected</h1></q:fallback></q:include></body></html>''',
        b'''<!doctype html><html xmlns:t="http://www.w3.org/1999/XSL/Transform"><body><t:stylesheet><h1 epub:type="fulltitle">Transform Injected</h1></t:stylesheet></body></html>''',
        b'''<!doctype html><html xmlns:x="http://www.w3.org/1999/xhtml"><body><x:script><h1 epub:type="fulltitle">Script Injected</h1></x:script><h1 epub:type="fulltitle">Visible Alias Safe</h1></body></html>''',
    ),
    ids=("default_namespace", "alias_prefix", "xsl_transform", "aliased_script"),
)
def test_foreign_inclusion_guarded_variants_are_rejected(injected: bytes) -> None:
    guarded = _extract(_single(injected, media_type="text/html"))
    assert "Injected" not in guarded.to_json()


@pytest.mark.parametrize(
    "media_type",
    ("application/xhtml+xml", "text/html"),
    ids=("xhtml", "html"),
)
def test_recovery_resolves_qualified_active_start_and_end_tags(
    media_type: str,
) -> None:
    content = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:x="http://www.w3.org/1999/xhtml" xmlns:s="http://www.w3.org/2000/svg" xmlns:epub="http://www.idpf.org/2007/ops"><body><s:script><x:h1 epub:type="fulltitle">Injected SVG Script</x:h1></s:script><x:h1 epub:type="fulltitle">Safe QName Sibling</x:h1></body>'''
    report = _extract(_single(content, media_type=media_type))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Safe QName Sibling"]


def test_extension_vocabulary_terms_never_gain_controlled_semantics() -> None:
    prefixed = _extract(
        _single(
            _xhtml('<h1 epub:type="vendor:fulltitle">Vendor False Title</h1>')
        )
    )
    assert prefixed.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE

    boundary = _extract(
        _single(
            _xhtml('<h1 epub:type="fulltitle">Front<span epub:type="vendor:bodymatter">Story</span></h1><p epub:type="vendor:preview">eBook ISBN 9780306406157</p>')
        )
    )
    assert "FrontStory" in {claim.raw_value for claim in boundary.claims}
    assert OpeningField.IDENTIFIER in {claim.field for claim in boundary.claims}

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
    assert "Vendor Selected" not in selected.to_json()
    assert "Spine Selected" in {claim.raw_value for claim in selected.claims}


def test_same_element_isbn_labels_are_scoped_per_match() -> None:
    report = _extract(
        _single(
            _xhtml('<section epub:type="copyright-page"><p>eBook ISBN 9780306406157; Paperback ISBN 9781861972712</p></section>')
        )
    )
    identifiers = [claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER]
    assert [claim.applicability_label for claim in identifiers] == ["ebook", "paperback"]
    assert "eBook" in identifiers[0].citation.exact_excerpt
    assert "Paperback" in identifiers[1].citation.exact_excerpt
    suffixes = _extract(
        _single(
            _xhtml('<section epub:type="copyright-page"><p>ISBN 9780306406157 (eBook); ISBN 9781861972712 (Paperback)</p></section>')
        )
    )
    assert [
        claim.applicability_label
        for claim in suffixes.claims
        if claim.field is OpeningField.IDENTIFIER
    ] == ["ebook", "paperback"]
    preceding = _extract(
        _single(
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
    assert preceding_identifier.applicability_label == "ebook"
    assert "Electronic edition" in preceding_identifier.citation.exact_excerpt

    assert preceding_identifier.raw_value in preceding_identifier.citation.exact_excerpt

    following = _extract(
        _single(
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
    assert following_identifier.applicability_label == "ebook"
    assert "Electronic edition" in following_identifier.citation.exact_excerpt

    assert following_identifier.raw_value in following_identifier.citation.exact_excerpt

    conflicting = _extract(
        _single(
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
    assert conflicting_identifier.applicability_label == "ambiguous"
    assert conflicting_identifier.applicability is EpubObservationApplicability.UNKNOWN
    assert "Electronic paperback" in conflicting_identifier.citation.exact_excerpt


@pytest.mark.parametrize(
    "separator",
    (", ", " / ", " • "),
    ids=("comma", "slash", "bullet"),
)
def test_same_element_isbn_labels_survive_each_separator(separator: str) -> None:
    separated = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page"><p>'
                f'ISBN 9780306406157 (eBook){separator}'
                'ISBN 9781861972712 (Paperback)</p></section>'
            )
        )
    )
    assert [
        claim.applicability_label
        for claim in separated.claims
        if claim.field is OpeningField.IDENTIFIER
    ] == ["ebook", "paperback"]


@pytest.mark.parametrize("false_label", ("Notebook", "digitalis", "hardcovering"))
def test_same_element_isbn_false_labels_stay_unlabelled(false_label: str) -> None:
    unlabelled = _extract(
        _single(
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
    assert identifier.applicability_label == "unlabelled"
    assert identifier.applicability is EpubObservationApplicability.UNKNOWN


@pytest.mark.parametrize("semantic", ("bodymatter", "sample"))
def test_same_element_isbn_labels_are_scoped_out_of_later_semantics(
    semantic: str,
) -> None:
    scoped = _extract(
        _single(
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
    assert scoped_identifier.applicability == EpubObservationApplicability.UNKNOWN
    assert scoped_identifier.applicability_label == "unlabelled"
    assert "Electronic edition" not in scoped_identifier.citation.exact_excerpt


def test_isbn_recognition_is_occurrence_local_and_rejects_owned_uri_digits() -> None:
    report = _extract(
        _single(
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
    assert [(claim.normalized_value, claim.applicability_label) for claim in identifiers] == [("9781861972712", "unlabelled"), ("9780306406157", "ebook")]
    forward = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page"><p>'
                'ISBN 9781861972712 eBook ISBN 9780306406157'
                '</p></section>'
            )
        )
    )
    assert [
        claim.applicability_label
        for claim in forward.claims
        if claim.field is OpeningField.IDENTIFIER
    ] == ["unlabelled", "ebook"]
    backward = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page"><p>'
                'eBook ISBN 9781861972712 ISBN 9780306406157'
                '</p></section>'
            )
        )
    )
    assert [
        claim.applicability_label
        for claim in backward.claims
        if claim.field is OpeningField.IDENTIFIER
    ] == ["ebook", "unlabelled"]


def test_colon_delimited_media_labels_and_uri_owners_are_local() -> None:
    report = _extract(
        _single(
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
    assert [
        (claim.normalized_value, claim.applicability_label)
        for claim in report.claims
        if claim.field is OpeningField.IDENTIFIER
    ] == [
        ("9781861972712", "paperback"),
        ("9780306406157", "hardcover"),
        ("9780140328721", "ebook"),
    ]


def test_identifier_owner_spans_are_computed_once_per_element() -> None:
    values = " ".join("9780306406157" for _ in range(12))
    source = _single(
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
    assert spans.call_count == 1
    assert sum(claim.field is OpeningField.IDENTIFIER for claim in report.claims) == 12


def test_canonical_envelope_rejects_forged_versions_lineage_and_usage() -> None:
    report = _extract(opening_claims_complete())
    assert EPUB_OPENING_EXTRACTOR_VERSION == "epub-opening-v3"
    assert all(
        claim.citation.extractor_version
        == EPUB_OPENING_EXTRACTOR_VERSION
        for claim in report.claims
    )

    for mutate in (
        lambda value: value["claims"][0]["citation"].__setitem__(
            "extractor_version",
            "epub-opening-v2",
        ),
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
        with pytest.raises(EvidenceValueError):
            EpubOpeningReport.from_json(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )

    recovered = json.loads(_extract(opening_recovered_html()).to_json())
    recovered["claims"][0]["citation"]["recovery_warnings"] = []
    with pytest.raises(EvidenceValueError):
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
    with pytest.raises(EvidenceValueError):
        EpubOpeningReport.from_json(
            json.dumps(duplicate, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )

    failed = json.loads(_extract(opening_resource_limit()).to_json())
    failed["usage"]["opened_resources"] = 1
    with pytest.raises(EvidenceValueError):
        EpubOpeningReport.from_json(
            json.dumps(failed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )

    no_claims = json.loads(
        _extract(_single(_xhtml("<p>ordinary front matter</p>"))).to_json()
    )
    assert no_claims["outcome"] == "no_applicable_evidence"
    no_claims["usage"]["strict_documents"] = 0
    with pytest.raises(EvidenceValueError):
        EpubOpeningReport.from_json(
            json.dumps(no_claims, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )


def test_excerpt_profile_and_recovery_warning_are_enforced() -> None:
    limited = _extract(opening_claims_complete(), EpubOpeningLimits(excerpt_characters=1))
    assert limited.claims
    assert all(len(claim.raw_value) == 1 for claim in limited.claims)
    recovered = _extract(opening_recovered_html())
    assert all(claim.citation.recovery_warnings == ("recovered_markup",) for claim in recovered.claims)


def test_every_controlled_claim_family_and_contributor_role_is_recognized() -> None:
    report = _extract(opening_claim_families())
    assert {claim.field for claim in report.claims} == set(OpeningField)
    assert {
        claim.normalized_value
        for claim in report.claims
        if claim.field in {OpeningField.CONTRIBUTOR_ROLE, OpeningField.CREATOR_ROLE}
    } == {"author", "editor", "translator", "illustrator"}
    identifier = next(
        claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER
    )
    assert identifier.identifier_type == "isbn_13"
    assert identifier.applicability_label == "ebook"

    roles = _extract(
        _single(
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
    assert {
        claim.normalized_value
        for claim in roles.claims
        if claim.field is OpeningField.CONTRIBUTOR_ROLE
    } == {"adapter"}
    assert {
        claim.normalized_value
        for claim in roles.claims
        if claim.field is OpeningField.CONTRIBUTOR
    } == {
        "Casey Designer",
        "Alex Adapter",
    }
    assert [claim.normalized_value for claim in roles.claims if claim.field is OpeningField.CREATOR] == ["Chris Creator"]


def test_indicia_roles_dates_and_production_credits_remain_typed_and_separate() -> None:
    report = _extract(
        _single(
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

    assert [
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CREATOR
    ] == [("Ada Author", "author")]
    assert {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTRIBUTOR
    } == {
        ("Bea Artist", "translator+illustrator"),
        ("Cara Editor", "editor"),
        ("Dev Writer", "foreword"),
        ("Eli Designer", "designer"),
        ("Fran Lens", "photographer"),
        ("Gia Mapper", "cartographer"),
        ("Hal Adapter", "adapter"),
    }
    contextual = {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTEXTUAL_CREDIT
    }
    assert contextual == {
        ("Tool House", "conversion"),
        ("Archive Bot", "scanner"),
        ("Calibre", "software"),
        ("Example Retailer", "retailer"),
    }
    assert not (
        contextual
        & {
            (claim.normalized_value, claim.role)
            for claim in report.claims
            if claim.field is OpeningField.CONTRIBUTOR
        }
    )
    assert [
        (
            claim.field,
            claim.normalized_value,
            claim.date_event,
            claim.date_precision,
        )
        for claim in report.claims
        if claim.field
        in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
    ] == [
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
    ]
    assert EpubOpeningReport.from_json(report.to_json()) == report


def test_unlabelled_print_and_invalid_dates_never_become_manifestation_dates() -> None:
    report = _extract(
        _single(
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
    assert not any(
        claim.field is OpeningField.PUBLICATION_DATE
        for claim in report.claims
    )

    assert [
        (claim.normalized_value, claim.date_event)
        for claim in report.claims
        if claim.field is OpeningField.DATE_EVENT
    ] == [("1999", DateEvent.WORK_FIRST_PUBLICATION)]


def test_ambiguous_date_spans_fail_closed_without_truncation_or_event_guessing() -> None:
    report = _extract(
        _single(
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

    assert not any(
        claim.field
        in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
        for claim in report.claims
    )

    assert "ambiguous_date_event" in report.warnings
    assert opening._date_precision("2024-03-05T10:30:00") is None

    independent = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page">'
                "<p>First published 1998. "
                "This ebook edition published 2024</p>"
                "</section>"
            )
        )
    )
    assert [
        (claim.normalized_value, claim.date_event)
        for claim in independent.claims
        if claim.field
        in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
    ] == [
        ("1998", DateEvent.WORK_FIRST_PUBLICATION),
        ("2024", DateEvent.MANIFESTATION_PUBLICATION),
    ]
    assert "ambiguous_date_event" not in independent.warnings
    assert [
        (lexical, event)
        for lexical, event, _precision
        in opening._indicia_dates("First published 1998")
    ] == [("1998", DateEvent.WORK_FIRST_PUBLICATION)]
    assert opening._mixed_edition_date_spans("This ebook edition") == ()


@pytest.mark.parametrize(
    "separator",
    (". ", "; ", "|", "\n"),
    ids=("period", "semicolon", "pipe", "newline"),
)
def test_independent_date_spans_survive_each_separator(separator: str) -> None:
    text = (
        f"First published 1998{separator}"
        "This ebook edition published 2024"
    )
    assert [
        (lexical, event)
        for lexical, event, _precision
        in opening._indicia_dates(text)
    ] == [
        ("1998", DateEvent.WORK_FIRST_PUBLICATION),
        ("2024", DateEvent.MANIFESTATION_PUBLICATION),
    ]
    assert opening._mixed_edition_date_spans(text) == ()


def test_indicia_dates_require_whole_tokens_and_exact_offset_boundary() -> None:
    valid = (
        "EPUB publication date: 2024-03-05T10:30:00+14:00. "
        "Copyright 2023"
    )
    assert [
        (value, event, precision)
        for value, event, precision in opening._indicia_dates(valid)
    ] == [
        (
            "2024-03-05T10:30:00+14:00",
            DateEvent.MANIFESTATION_PUBLICATION,
            DatePrecision.TIMESTAMP,
        ),
        ("2023", DateEvent.COPYRIGHT, DatePrecision.YEAR),
    ]


@pytest.mark.parametrize(
    "malformed",
    (
        "2024-03-05T10:30:00+14:01",
        "2024-03-05T10:30:00-14:59",
        "2024/03/05",
        "2024abc",
        "2024-03-05Z",
    ),
)
def test_indicia_dates_reject_malformed_tokens(malformed: str) -> None:
    assert opening._indicia_dates(
        f"EPUB publication date: {malformed}"
    ) == ()
    assert opening._date_precision(malformed) is None


def test_adjacent_explicit_credits_recover_independent_people_and_roles() -> None:
    report = _extract(
        _single(
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

    assert {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTRIBUTOR
    } == {
        ("Alice", "translator"),
        ("Bob", "illustrator"),
        ("Carol", "translator"),
        ("Dana", "illustrator"),
        ("A. B. Carter", "translator"),
        ("Eve", "translator"),
        ("Bea Artist", "translator+illustrator"),
    }
    assert ("Tool House", "conversion") in {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTEXTUAL_CREDIT
    }


def test_adjacent_contextual_credits_stop_at_each_controlled_cue() -> None:
    report = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page">'
                "<p>EPUB conversion by Tool House and "
                "Scanned by Archive Bot</p>"
                "</section>"
            )
        )
    )
    assert {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTEXTUAL_CREDIT
    } == {
        ("Tool House", "conversion"),
        ("Archive Bot", "scanner"),
    }


def test_one_date_occurrence_has_one_event_and_repeated_literals_cite_exactly() -> None:
    assert opening._indicia_dates("Copyright notice last modified 2024") == ()
    report = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page">'
                "<p>First published 1998. Copyright 1998</p>"
                "</section>"
            )
        )
    )
    dated = [
        claim
        for claim in report.claims
        if claim.date_event
        in {DateEvent.WORK_FIRST_PUBLICATION, DateEvent.COPYRIGHT}
    ]
    assert [
        (
            claim.normalized_value,
            claim.date_event,
            claim.citation.exact_excerpt,
        )
        for claim in dated
    ] == [
        ("1998", DateEvent.WORK_FIRST_PUBLICATION, "1998"),
        ("1998", DateEvent.COPYRIGHT, "1998"),
    ]
    assert dated[0].citation.projection_start != dated[1].citation.projection_start
    copyright_shadow = next(
        claim
        for claim in report.claims
        if claim.field is OpeningField.COPYRIGHT_DATE
    )
    assert copyright_shadow.citation.projection_start == dated[1].citation.projection_start


def test_promotion_word_inside_semantic_title_does_not_suppress_claim() -> None:
    report = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page">'
                '<h1 epub:type="fulltitle">A Sample of Life</h1>'
                "<p>Copyright 2024</p>"
                "</section>"
            )
        )
    )
    assert (OpeningField.TITLE, "A Sample of Life") in {(claim.field, claim.normalized_value) for claim in report.claims}


def test_promotion_notice_suppresses_only_later_claims_in_its_front_scope() -> None:
    report = _extract(
        _single(
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
    assert {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTRIBUTOR
    } == {
        ("Earlier Editor", "editor"),
        ("Sibling Editor", "editor"),
    }
    assert {
        claim.normalized_value
        for claim in report.claims
        if claim.date_event is DateEvent.COPYRIGHT
    } == {"2023"}
    assert (OpeningField.TITLE, "A Sample of Life") in {(claim.field, claim.normalized_value) for claim in report.claims}


def test_uncontained_promotion_run_stops_at_later_target_section() -> None:
    report = _extract(
        _single(
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
    assert {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTRIBUTOR
    } == {
        ("Earlier Editor", "editor"),
        ("Target Editor", "editor"),
    }
    assert {
        claim.normalized_value
        for claim in report.claims
        if claim.date_event is DateEvent.COPYRIGHT
    } == {"2023"}
    assert (OpeningField.TITLE, "A Sample of Life") in {(claim.field, claim.normalized_value) for claim in report.claims}


def test_promotion_descendant_suppresses_its_claim_ancestor_only() -> None:
    report = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page">'
                "<p>Edited by Earlier Editor</p>"
                "<p>Legal notice <span>Also by</span> "
                "<span>First published 1999</span></p>"
                "<p>Copyright 2023</p>"
                "<p>Edited by Later Editor</p>"
                "</section>"
            )
        )
    )
    assert {
        (claim.normalized_value, claim.role)
        for claim in report.claims
        if claim.field is OpeningField.CONTRIBUTOR
    } == {
        ("Earlier Editor", "editor"),
        ("Later Editor", "editor"),
    }
    assert {
        (claim.normalized_value, claim.date_event)
        for claim in report.claims
        if claim.date_event is not None
    } == {("2023", DateEvent.COPYRIGHT)}


def test_synthetic_indicia_aggregate_has_exact_role_and_date_recovery() -> None:
    role_cases: tuple[tuple[str, set[tuple[str, str]]], ...] = (
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
    date_cases: tuple[tuple[str, set[tuple[str, DatePrecision]]], ...] = (
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
    assert {"contributor_role": role_aggregate, "manifestation_date": date_aggregate} == {
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
    }


def test_fact_limit_retains_already_admitted_opening_evidence() -> None:
    report = _extract(
        _single(
            _xhtml(
                '<section epub:type="copyright-page">'
                "<p>Translated by First Contributor</p>"
                "<p>Illustrated by Second Contributor</p>"
                "</section>"
            )
        ),
        EpubOpeningLimits(facts=1),
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert len(report.claims) == 1
    assert "opening_fact_limit" in report.warnings


def test_fact_limit_marker_is_reserved_inside_a_full_warning_budget() -> None:
    report = _extract(
        _single(
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
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert len(report.claims) == 1
    assert report.warnings == ("ambiguous_date_event", "opening_fact_limit")
    assert EpubOpeningReport.from_json(report.to_json()) == report


def test_trailing_series_cue_without_positive_structure_is_rejected() -> None:
    report = _extract(
        _single(_xhtml("<p>Book 2.5 of the Clockwork Archive</p>"))
    )
    assert [
        (claim.field, claim.normalized_value)
        for claim in report.claims
        if claim.field in {OpeningField.SERIES, OpeningField.SERIES_ORDER}
    ] == [
        (OpeningField.SERIES_ORDER, "2.5"),
        (OpeningField.SERIES, "Clockwork Archive"),
    ]


@pytest.mark.parametrize(
    "markup",
    (
        "<p>Clockwork Archive Book 2.5</p>",
        "<p>Read Clockwork Archive Book 2.5</p>",
        "<p>Discover Clockwork Archive Volume 2.5</p>",
        "<p>Clockwork Archive Book II</p>",
        "<p>Clockwork Archive Book Two</p>",
        "<p>Clockwork Archive Book 2.5 revised</p>",
        "<p>Read Clockwork Archive Book 2.5 today</p>",
        "<h2>Clockwork Archive Book 2.5</h2>",
        "<p>One Two Three Four Five Six Seven Book 2.5</p>",
    ),
    ids=(
        "trailing_order",
        "read_prefix",
        "discover_volume",
        "roman_order",
        "word_order",
        "revised_tail",
        "promotional_tail",
        "heading_element",
        "long_lead_in",
    ),
)
def test_trailing_series_cue_variants_are_rejected(markup: str) -> None:
    claims = _extract(_single(_xhtml(markup))).claims
    assert not (
        {
            claim.field
            for claim in claims
        }
        & {OpeningField.SERIES, OpeningField.SERIES_ORDER}
    )


def test_byline_is_a_creator_with_author_role_and_source_values_are_redacted() -> None:
    report = _extract(_single(_xhtml('<p epub:type="creator">By Ada Example</p><p epub:type="contributor">Bea Editor</p>')))
    creators = [claim for claim in report.claims if claim.field is OpeningField.CREATOR]
    contributors = [claim for claim in report.claims if claim.field is OpeningField.CONTRIBUTOR]
    assert [(claim.normalized_value, claim.raw_value) for claim in creators] == [("Ada Example", "Ada Example")]
    assert [claim.normalized_value for claim in contributors] == ["Bea Editor"]
    assert [claim.normalized_value for claim in report.claims if claim.field is OpeningField.CREATOR_ROLE] == ["author"]
    oversized = _extract(_single(_xhtml('<p epub:type="creator">' + "A" * 281 + "</p>")))
    claim = next(claim for claim in oversized.claims if claim.field is OpeningField.CREATOR)
    assert claim.state == "oversized"
    assert re.search(r"^oversized-source-text-sha256:[0-9a-f]{64}$", claim.raw_value)
    assert claim.raw_value == claim.citation.exact_excerpt
    unsafe = _extract(_single(_xhtml('<p epub:type="creator">By /Users/private/Ada</p>')))
    creator = next(claim for claim in unsafe.claims if claim.field is OpeningField.CREATOR)
    assert creator.state == "unsupported"
    assert re.search(r"^unsafe-source-text-sha256:[0-9a-f]{64}$", creator.raw_value)
    assert creator.raw_value == creator.citation.exact_excerpt
    assert [claim.normalized_value for claim in unsafe.claims if claim.field is OpeningField.CREATOR_ROLE] == ["author"]


def test_strict_recovery_and_active_content_are_inert_and_visible() -> None:
    strict = _extract(opening_strict_xhtml())
    recovered = _extract(opening_recovered_html())
    hostile = _extract(opening_active_content_inert())
    assert strict.usage.strict_documents == 1
    assert recovered.usage.recovered_documents == 1
    assert all(
        claim.citation.parse_mode is OpeningParseMode.RECOVERED
        for claim in recovered.claims
    )

    assert "ignore all safety gates" not in hostile.to_json()
    assert "example.invalid" not in hostile.to_json()


def test_identifier_checksums_and_nearby_applicability_remain_typed() -> None:
    report = _extract(opening_title_isbn_applicability())
    identifiers = [
        claim for claim in report.claims if claim.field is OpeningField.IDENTIFIER
    ]
    assert len(identifiers) == 2
    assert {claim.applicability for claim in identifiers} == {
        EpubObservationApplicability.EDITION,
        EpubObservationApplicability.INAPPLICABLE,
    }
    invalid = _extract(
        _single(
            _xhtml('<section epub:type="copyright-page"><p>ISBN 9780306406158</p></section>')
        )
    )
    invalid_identifier = next(
        claim for claim in invalid.claims if claim.field is OpeningField.IDENTIFIER
    )
    assert invalid_identifier.normalized_value is None
    assert invalid_identifier.identifier_type == "isbn_13"
    assert invalid_identifier.applicability_label == "unlabelled"


def test_equal_precedence_conflict_no_claim_and_limit_partitions_are_distinct() -> None:
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
    assert "ambiguous_navigation_landmark" in conflict.warnings
    assert len(
        [claim for claim in conflict.claims if claim.field is OpeningField.TITLE]
    ) == 2
    assert absent.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert per_resource.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert OpeningResourceDisposition.RESOURCE_LIMIT in {resource.disposition for resource in per_resource.resources}
    assert aggregate.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
    assert not aggregate.claims
    assert not aggregate.resources


def test_node_text_link_and_document_limits_cover_exact_and_rejection_boundaries() -> None:
    minimal = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body></html>'''
    exact_text = _extract(
        _single(minimal),
        EpubOpeningLimits(text_characters_per_selection=3),
    )
    above_text = _extract(
        _single(minimal),
        EpubOpeningLimits(text_characters_per_selection=2),
    )
    assert exact_text.outcome == EpubInspectionOutcome.SUCCESS
    assert above_text.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
    malformed = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body>'''
    cumulative = _extract(
        _single(malformed),
        EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=6),
    )
    assert cumulative.outcome == EpubInspectionOutcome.SUCCESS
    assert cumulative.usage.nodes == 6
    assert cumulative.usage.text_characters == 6
    assert _extract(
        _single(malformed),
        EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=5),
    ).outcome == EpubInspectionOutcome.RESOURCE_LIMIT
    assert _extract(
        _single(minimal),
        EpubOpeningLimits(nodes_per_resource=3, nodes_per_selection=3),
    ).outcome == EpubInspectionOutcome.SUCCESS

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
        _single(minimal),
        EpubOpeningLimits(nodes_per_resource=10, nodes_per_selection=2),
    )
    assert local_nodes.outcome == EpubInspectionOutcome.SUCCESS
    assert OpeningResourceDisposition.RESOURCE_LIMIT in {item.disposition for item in local_nodes.resources}
    assert aggregate_nodes.outcome == EpubInspectionOutcome.RESOURCE_LIMIT

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
    assert charged_navigation.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
    assert not charged_navigation.resources
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
    assert _extract(
        unsafe_navigation,
        EpubOpeningLimits(nodes_per_resource=5, nodes_per_selection=5),
    ).outcome == EpubInspectionOutcome.SUCCESS
    assert _extract(
        unsafe_navigation,
        EpubOpeningLimits(nodes_per_resource=5, nodes_per_selection=4),
    ).outcome == EpubInspectionOutcome.RESOURCE_LIMIT

    nav_package = _opening_package(
        items=(("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'), ("safe", "safe.xhtml", "application/xhtml+xml", "")),
        spine=("safe",),
    )
    nav = _xhtml('<nav epub:type="landmarks"><a epub:type="titlepage" href="safe.xhtml">one</a><a epub:type="copyright-page" href="safe.xhtml">two</a></nav>')
    nav_value = _opening_epub(
        package=nav_package,
        entries=(("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED), ("EPUB/safe.xhtml", minimal, zipfile.ZIP_DEFLATED)),
    )
    assert _extract(nav_value, EpubOpeningLimits(navigation_links=2)).outcome == EpubInspectionOutcome.SUCCESS
    assert _extract(nav_value, EpubOpeningLimits(navigation_links=1)).outcome == EpubInspectionOutcome.RESOURCE_LIMIT


def test_navigation_only_and_unknown_encoding_preserve_typed_outcomes() -> None:
    public_doctype = b'''<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Recovered Doctype</h1></body></html>'''
    recovered_doctype = _extract(_single(public_doctype))
    assert recovered_doctype.outcome == EpubInspectionOutcome.SUCCESS
    assert recovered_doctype.claims[0].raw_value == "Recovered Doctype"
    assert recovered_doctype.resources[0].parse_mode == OpeningParseMode.RECOVERED

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
    assert report.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert report.usage.selected_documents == 0
    assert len(report.resources) == 1
    assert report.resources[0].selection_path == ("epub3_navigation",)

    nav_in_spine = _extract(
        _opening_epub(
            package=_opening_package(
                items=(("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),),
                spine=("nav",),
            ),
            entries=(("EPUB/nav.xhtml", _xhtml("<nav><ol/></nav>"), zipfile.ZIP_DEFLATED),),
        )
    )
    assert nav_in_spine.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE

    assert nav_in_spine.usage.selected_documents == 1
    assert [resource.selection_path for resource in nav_in_spine.resources] == [("epub3_navigation",), ("linear_spine_fallback",)]
    assert len({resource.resource_identity for resource in nav_in_spine.resources}) == 1

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
    assert content.outcome == EpubInspectionOutcome.SUCCESS
    assert OpeningResourceDisposition.UNSUPPORTED_ENCODING in {resource.disposition for resource in content.resources}
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
    assert navigation.outcome == EpubInspectionOutcome.SUCCESS
    assert "unusable_navigation_document" in navigation.warnings

    minimal = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">abc</h1></body></html>'''
    items = tuple((f"item{index}", f"item{index}.xhtml", "application/xhtml+xml", "") for index in range(6))
    documents = _opening_epub(
        package=_opening_package(items=items, spine=tuple(item[0] for item in items)),
        entries=tuple((f"EPUB/item{index}.xhtml", minimal.replace(b">abc<", f">title{index}<".encode()), zipfile.ZIP_DEFLATED) for index in range(6)),
    )
    selected = _extract(documents)
    assert selected.usage.selected_documents == 5
    assert "selected_document_limit_reached" in selected.warnings
    one_selected = _extract(
        documents,
        EpubOpeningLimits(selected_documents=1),
    )
    assert one_selected.usage.selected_documents == 1
    assert "selected_document_limit_reached" in one_selected.warnings


@pytest.mark.parametrize(
    "media_type",
    ("application/xhtml+xml", "text/html"),
    ids=("xhtml", "html"),
)
def test_quoted_bracket_doctype_is_inert(media_type: str) -> None:
    quoted_bracket_doctype = b'''<!DOCTYPE html SYSTEM "https://example.invalid/a[b].dtd"><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Quoted Bracket Safe</h1></body></html>'''
    inert_doctype = _extract(
        _single(
            quoted_bracket_doctype,
            media_type=media_type,
        )
    )
    assert inert_doctype.outcome == EpubInspectionOutcome.SUCCESS
    assert inert_doctype.resources[0].parse_mode == OpeningParseMode.RECOVERED


@pytest.mark.parametrize(
    "media_type",
    ("application/xhtml+xml", "text/html"),
    ids=("xhtml", "html"),
)
@pytest.mark.parametrize(
    "unsafe",
    (
        b'''<!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        b'''<!DOCTYPE html SYSTEM "x>y" [<!ELEMENT html ANY>]><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        b'''<?bogus><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        b'''<?bogus><!DOCTYPE html [<!ELEMENT html ANY>]><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        b'''<!-- inert --!><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        b'''<!--><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
        b'''<!---><!ENTITY injected "unsafe"><html><body><h1 epub:type="fulltitle">Injected</h1></body></html>''',
    ),
    ids=(
        "bare_entity",
        "system_subset",
        "bogus_pi_entity",
        "bogus_pi_subset",
        "abrupt_comment_close",
        "empty_comment",
        "short_comment",
    ),
)
def test_unsafe_declarations_are_rejected(unsafe: bytes, media_type: str) -> None:
    rejected = _extract(
        _single(unsafe, media_type=media_type)
    )
    assert rejected.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert rejected.resources[0].disposition == OpeningResourceDisposition.MALFORMED
    assert "unsafe_construct" in rejected.resources[0].warnings
    assert "Injected" not in rejected.to_json()


@pytest.mark.parametrize(
    "media_type",
    ("application/xhtml+xml", "text/html"),
    ids=("xhtml", "html"),
)
def test_declaration_inside_a_comment_is_inert(media_type: str) -> None:
    comment = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><!-- <!ENTITY inert "text"> --><h1 epub:type="fulltitle">Comment Safe</h1></body></html>'''
    inert_comment = _extract(
        _single(comment, media_type=media_type)
    )
    assert inert_comment.outcome == EpubInspectionOutcome.SUCCESS
    assert inert_comment.resources[0].parse_mode == (
        OpeningParseMode.STRICT
        if media_type == "application/xhtml+xml"
        else OpeningParseMode.RECOVERED
    )


@pytest.mark.parametrize(
    "media_type",
    ("application/xhtml+xml", "text/html"),
    ids=("xhtml", "html"),
)
@pytest.mark.parametrize(
    "body",
    (
        b'''<![CDATA[<!DOCTYPE unsafe [<!ENTITY inert "text">]>]]><h1 epub:type="fulltitle">CDATA Safe</h1>''',
        b'''<svg xmlns="http://www.w3.org/2000/svg"><![CDATA[<!ENTITY inert "text">]]></svg><h1 epub:type="fulltitle">Foreign CDATA Safe</h1>''',
    ),
    ids=("html_cdata", "foreign_cdata"),
)
def test_declaration_inside_cdata_is_inert(body: bytes, media_type: str) -> None:
    cdata = (
        b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'''
        + body
        + b"</body></html>"
    )
    inert_cdata = _extract(
        _single(cdata, media_type=media_type)
    )
    assert inert_cdata.outcome == EpubInspectionOutcome.SUCCESS
    assert inert_cdata.resources[0].parse_mode == (
        OpeningParseMode.STRICT
        if media_type == "application/xhtml+xml"
        else OpeningParseMode.RECOVERED
    )


@pytest.mark.parametrize(
    "media_type",
    ("application/xhtml+xml", "text/html"),
    ids=("xhtml", "html"),
)
@pytest.mark.parametrize(
    ("tag", "prefix"),
    (("script", "scripture"), ("style", "stylex")),
    ids=("script", "style"),
)
def test_declaration_inside_raw_text_is_inert(
    tag: str, prefix: str, media_type: str
) -> None:
    raw_text = (
        f'<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops"><body><{tag}>'
        f'inert</{prefix}><!ENTITY inert "text"></{tag}>'
        '<h1 epub:type="fulltitle">Raw Text Safe</h1></body></html>'
    ).encode()
    inert_raw_text = _extract(
        _single(raw_text, media_type=media_type)
    )
    assert inert_raw_text.outcome == EpubInspectionOutcome.SUCCESS
    assert inert_raw_text.resources[0].parse_mode == OpeningParseMode.RECOVERED


_FRAGMENT_BOUNDARY_CONTENT = _xhtml(
    '<section id="title"><h1>Fragment Boundary Title</h1></section>'
    '<section id="sample"><p>eBook ISBN 9780306406157</p></section>'
    '<main id="body"><p>Body</p></main>'
)


@pytest.mark.parametrize("order", ("opening-first", "boundaries-first"))
def test_same_resource_fragment_boundaries_preserve_epub3_landmark_order(
    order: str,
) -> None:
    content = _FRAGMENT_BOUNDARY_CONTENT
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("opening", "opening.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("opening",),
    )
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
    report = _extract(
        _opening_epub(
            package=package,
            entries=(
                ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
            ),
        )
    )
    assert "Fragment Boundary Title" in {claim.raw_value for claim in report.claims}


def test_same_resource_fragment_boundaries_preserve_safe_opening_roles() -> None:
    content = _FRAGMENT_BOUNDARY_CONTENT
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("opening", "opening.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("opening",),
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
    assert "Fragment Boundary Title" in {claim.raw_value for claim in legacy.claims}

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
    assert not excluded.claims
    assert "explicitly_excluded_resource" in excluded.warnings

_FRAGMENT_TARGET_PACKAGE = _opening_package(
    items=(
        ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
        ("opening", "opening.xhtml", "application/xhtml+xml", ""),
    ),
    spine=("opening",),
)
_FRAGMENT_TARGET_STRICT = _xhtml(
    '<section xml:id="title-page"><h1>Resolved Fragment Title</h1></section>'
    '<section id="excluded"><p>eBook ISBN 9780306406157</p></section>'
    '<main id="body"><p>Body</p></main>'
)


@pytest.mark.parametrize(
    "content",
    (_FRAGMENT_TARGET_STRICT, _FRAGMENT_TARGET_STRICT.replace(b"</body>", b"")),
    ids=("strict", "recovered"),
)
def test_fragment_targets_resolve_uniquely_in_strict_and_recovered_content(
    content: bytes,
) -> None:
    package = _FRAGMENT_TARGET_PACKAGE
    nav = _xhtml(
        '<nav epub:type="landmarks">'
        '<a epub:type="titlepage" href="opening.xhtml#title%2Dpage">Title</a>'
        '<a epub:type="preview" href="opening.xhtml#excluded">Preview</a>'
        '<a epub:type="bodymatter" href="opening.xhtml#body">Body</a>'
        "</nav>"
    )
    report = _extract(
        _opening_epub(
            package=package,
            entries=(
                ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
            ),
        )
    )
    assert {claim.field for claim in report.claims} == {OpeningField.TITLE}
    assert report.resources[1].selection_path == ("epub3_landmark", "titlepage")
    assert report.usage.selected_documents == 1
    assert len(
        {
            resource.resource_identity
            for resource in report.resources[1:]
        }
    ) == 1


@pytest.mark.parametrize(
    "href",
    ("opening.xhtml", "opening.xhtml#"),
    ids=("no_fragment", "empty_fragment"),
)
def test_empty_fragment_hrefs_are_equivalent_to_no_fragment(href: str) -> None:
    empty_nav = _xhtml(
        '<nav epub:type="landmarks">'
        f'<a epub:type="titlepage" href="{href}">Title</a>'
        "</nav>"
    )
    equivalent = _extract(
        _opening_epub(
            package=_FRAGMENT_TARGET_PACKAGE,
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
    assert "Empty Fragment Equivalent" in {claim.raw_value for claim in equivalent.claims}


@pytest.mark.parametrize(
    "invalid",
    (
        _xhtml("<h1>Missing Target</h1>"),
        _xhtml(
            '<section id="target"><h1>Duplicate One</h1></section>'
            '<section id="target"><h1>Duplicate Two</h1></section>'
        ),
    ),
    ids=("missing_target", "duplicate_target"),
)
def test_invalid_fragment_roles_are_omitted_and_safe_siblings_continue(
    invalid: bytes,
) -> None:
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Safe Sibling"]
    assert "invalid_fragment_target" in report.warnings


def test_body_target_removes_title_role_but_preserves_same_tier_dedication() -> None:
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
    assert report.resources[1].selection_path == ("epub3_landmark", "dedication")
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Surviving Front Title"]


@pytest.mark.parametrize(
    "invalid_markup",
    (
        '<section id="guide"><h1>Guide Fallback</h1></section>',
        '<section id="missing"></section><section id="missing"></section><section id="guide"><h1>Guide Fallback</h1></section>',
    ),
    ids=("missing_target", "duplicate_target"),
)
@pytest.mark.parametrize("recovered", (False, True), ids=("strict", "recovered"))
def test_invalid_high_tier_fragments_fall_back_to_the_guide(
    invalid_markup: str, recovered: bool
) -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("opening", "opening.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("opening",),
        guide=(("title-page", "opening.xhtml#guide"),),
    )
    nav = _xhtml(
        '<nav epub:type="landmarks"><a epub:type="titlepage" '
        'href="opening.xhtml#missing">Invalid</a></nav>'
    )
    strict = _xhtml(invalid_markup)
    content = strict.replace(b"</body>", b"") if recovered else strict
    report = _extract(
        _opening_epub(
            package=package,
            entries=(
                ("EPUB/nav.xhtml", nav, zipfile.ZIP_DEFLATED),
                ("EPUB/opening.xhtml", content, zipfile.ZIP_DEFLATED),
            ),
        )
    )
    assert report.resources[1].selection_path == ("epub2_guide", "title_page")
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Guide Fallback"]
    assert "invalid_fragment_target" in report.warnings


def test_invalid_high_tier_fragments_fall_back_with_canonical_provenance() -> None:
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
    assert invalid_both.resources[1].selection_path == ("linear_spine_fallback",)
    assert "Spine Semantic Fallback" in {claim.raw_value for claim in invalid_both.claims}

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
    assert legacy.resources[0].selection_path == ("linear_spine_fallback",)


def test_valid_high_tier_role_behind_body_never_falls_through() -> None:
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
    assert not report.claims
    assert report.resources[1].selection_path == ("epub3_landmark", "titlepage")


@pytest.mark.parametrize(
    "markup",
    (
        '<section><h1 epub:type="fulltitle">Missing Target Claim</h1></section>',
        '<section id="target"></section><section id="target"><h1 epub:type="fulltitle">Duplicate Target Claim</h1></section>',
    ),
    ids=("missing_target", "duplicate_target"),
)
@pytest.mark.parametrize("recovered", (False, True), ids=("strict", "recovered"))
def test_invalid_only_optional_roles_never_authorize_claims(
    markup: str, recovered: bool
) -> None:
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
    strict = _xhtml(markup)
    content = strict.replace(b"</body>", b"") if recovered else strict
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
        assert not report.claims
        assert "invalid_fragment_target" in report.warnings
        assert report.resources[-1].selection_path == expected_path


@pytest.mark.parametrize(
    "roles",
    (
        ("dedication", "titlepage"),
        ("titlepage", "dedication"),
        ("copyright-page", "titlepage"),
        ("titlepage", "copyright-page"),
    ),
    ids=(
        "dedication_then_titlepage",
        "titlepage_then_dedication",
        "copyright_then_titlepage",
        "titlepage_then_copyright",
    ),
)
def test_same_resource_role_dedup_unions_title_context_canonically(
    roles: tuple[str, ...],
) -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("opening", "opening.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("opening",),
    )
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
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert report.resources[1].selection_path == ("epub3_landmark", "titlepage")
    assert report.usage.selected_documents == 1
    assert "Role Union Title" in {claim.raw_value for claim in report.claims}


def test_same_resource_role_dedup_prefers_the_epub3_landmark_over_the_guide() -> None:
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
    assert mixed.resources[1].selection_path == ("epub3_landmark", "dedication")
    assert "Cross Precedence Title" not in {claim.raw_value for claim in mixed.claims}


@pytest.mark.parametrize(
    "body",
    (
        b'<!-- <meta charset="shift_jis"> --><h1 epub:type="fulltitle">Comment Safe</h1>',
        b'<script>"<meta charset=shift_jis>"</script><h1 epub:type="fulltitle">Script Safe</h1>',
        b'<p data-charset="shift_jis">prose charset=shift_jis</p><h1 epub:type="fulltitle">Attribute Safe</h1>',
    ),
    ids=("comment", "script", "attribute"),
)
def test_recovery_encoding_ignores_declaration_lookalikes(body: bytes) -> None:
    report = _extract(_single(body, media_type="text/html"))
    assert report.outcome == EpubInspectionOutcome.SUCCESS


@pytest.mark.parametrize(
    "body",
    (
        b'<meta charset="shift_jis"><h1 epub:type="fulltitle">Unsupported</h1>',
        b'<meta charset="utf-8"><meta charset="windows-1252"><h1 epub:type="fulltitle">Conflict</h1>',
        b'\xef\xbb\xbf<meta charset="windows-1252"><h1 epub:type="fulltitle">BOM Conflict</h1>',
    ),
    ids=("unsupported", "conflicting", "bom_conflict"),
)
def test_recovery_encoding_rejects_unusable_declarations(body: bytes) -> None:
    report = _extract(_single(body, media_type="text/html"))
    assert report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING


def test_recovery_encoding_uses_only_real_supported_declarations() -> None:
    cp1252 = (
        b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
        b'<h1 epub:type="fulltitle">Publisher\x92s Clock</h1>'
    )
    recovered = _extract(_single(cp1252, media_type="text/html"))
    assert recovered.outcome == EpubInspectionOutcome.SUCCESS
    assert "Publisher’s Clock" in {claim.raw_value for claim in recovered.claims}

    xml_declared = b'''<?xml version="1.0" encoding="shift_jis"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body>'''
    xml_report = _extract(_single(xml_declared))
    assert xml_report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING

_WIDE_TAIL = "<html><body><h1>Late wide</h1></body></html>".encode("utf-16-le")


@pytest.mark.parametrize(
    "body",
    (
        b'<textarea><meta charset="shift_jis"></textarea><h1>Textarea Safe</h1>',
        b'<title><meta charset="shift_jis"></title><h1>Title Safe</h1>',
        b'<template><meta charset="shift_jis"></template><h1>Template Safe</h1>',
        b'<f:meta xmlns:f="urn:foreign" charset="shift_jis"></f:meta><h1>Foreign Meta Safe</h1>',
    ),
    ids=("textarea", "title", "template", "foreign_meta"),
)
def test_inactive_encoding_declarations_are_inert(body: bytes) -> None:
    report = _extract(_single(body, media_type="text/html"))
    assert report.outcome == EpubInspectionOutcome.SUCCESS


@pytest.mark.parametrize(
    "body",
    (
        b'<meta charset="utf-8" charset="utf-8"><h1>Duplicate Charset</h1>',
        b'<meta http-equiv="content-type" http-equiv="content-type" content="text/html; charset=utf-8"><h1>Duplicate Http Equiv</h1>',
        b'<?xml version="1.0" encoding="utf-8" encoding="utf-8"?><html><body><h1>Duplicate XML</h1></body>',
    ),
    ids=("duplicate_charset", "duplicate_http_equiv", "duplicate_xml"),
)
def test_duplicate_encoding_declarations_are_unsupported(body: bytes) -> None:
    media_type = (
        "application/xhtml+xml" if body.startswith(b"<?xml") else "text/html"
    )
    report = _extract(_single(body, media_type=media_type))
    assert report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING


@pytest.mark.parametrize(
    "body",
    (
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
    ),
    ids=(
        "utf16le_declaration",
        "utf16be_declaration",
        "utf32le_declaration",
        "utf32be_declaration",
        "utf16le_html",
        "utf16be_html",
        "utf16le_doctype",
        "utf16be_leading_space",
        "utf16le_disrupted",
        "utf16be_disrupted",
        "utf16le_disrupted_doctype",
        "utf16be_disrupted_space",
    ),
)
def test_wide_signatures_are_unsupported(body: bytes) -> None:
    report = _extract(_single(body, media_type="text/html"))
    assert report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING


@pytest.mark.parametrize(
    "body",
    (
        b"A" * 2_100 + _WIDE_TAIL,
        b"\xc3\xa9" * 1_100
        + "<html><body>Late BE</body></html>".encode("utf-16-be"),
    ),
    ids=("late_le", "late_be"),
)
def test_late_wide_signatures_are_unsupported(body: bytes) -> None:
    report = _extract(_single(body, media_type="text/html"))
    assert report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING


def test_encoding_declaration_activity_duplicates_and_wide_signatures() -> None:
    unclosed_foreign = _extract(
        _single(
            b'<f:section xmlns:f="urn:foreign"><h1 epub:type="fulltitle">Foreign Child</h1>',
            media_type="text/html",
        )
    )
    assert unclosed_foreign.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert not unclosed_foreign.claims

    equivalent = _extract(
        _single(
            b'\xef\xbb\xbf<meta charset="utf8"><meta charset="UTF-8"><h1>Equivalent Declarations</h1>',
            media_type="text/html",
        )
    )
    assert equivalent.outcome == EpubInspectionOutcome.SUCCESS

    strict_wide = _extract(
        _single(
            '<?xml version="1.0" encoding="utf-16"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">'
            '<body><h1 epub:type="fulltitle">Wide Strict</h1></body></html>'.encode(
                "utf-16"
            )
        )
    )
    assert strict_wide.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING

    near_limit = b"x" * (1_048_000 - len(_WIDE_TAIL)) + _WIDE_TAIL
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
    assert near_report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING

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
        assert not _looks_wide_encoded(sample)
    dispositions = []
    for body in supported_without_nul:
        report = _extract(_single(body, media_type="text/html"))
        dispositions.append(report.resources[0].disposition)
    assert dispositions == [
        OpeningResourceDisposition.SELECTED,
        OpeningResourceDisposition.SELECTED,
        OpeningResourceDisposition.SELECTED,
        OpeningResourceDisposition.UNSUPPORTED_ENCODING,
    ]


@pytest.mark.parametrize(
    "body",
    (
        b'''<?xml version="1.0" encoding="utf-8" standalone="yes"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Double Quote Actual</h1></body>''',
        b'''<?xml version='1.1' encoding='utf8' standalone='no'?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 epub:type="fulltitle">Single Quote Actual</h1></body>''',
    ),
    ids=("double_quoted", "single_quoted"),
)
def test_xml_declaration_lexer_accepts_real_declarations(body: bytes) -> None:
    report = _extract(_single(body))
    assert report.outcome == EpubInspectionOutcome.SUCCESS


@pytest.mark.parametrize(
    "body",
    (
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
    ),
    ids=(
        "quoted_lookalike_note",
        "encoding_before_version",
        "unsupported_version",
        "quoted_lookalike_standalone",
        "invalid_standalone",
        "unknown_attribute",
        "missing_version",
        "unseparated_attributes",
        "unquoted_encoding",
        "unterminated_quote",
        "repeated_encoding",
    ),
)
def test_xml_declaration_lexer_ignores_quoted_lookalikes(body: bytes) -> None:
    report = _extract(_single(body))
    assert report.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING


def test_xml_declaration_lexer_rejects_unsupported_declared_encodings() -> None:
    unsupported = _extract(
        _single(
            b'''<?xml version='1.0' encoding='shift_jis'?><html><body>x</body>'''
        )
    )
    assert unsupported.resources[0].disposition == OpeningResourceDisposition.UNSUPPORTED_ENCODING


def test_raw_spine_annotations_are_partitioned_by_f06_acceptance() -> None:
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Accepted Safe"]
    assert report.usage.selected_documents == 1
    assert "invalid_spine_item" in report.warnings

_LINEAR_BASE_PACKAGE = _opening_package(
    items=(
        ("first", "first.xhtml", "application/xhtml+xml", ""),
        ("safe", "safe.xhtml", "application/xhtml+xml", ""),
    ),
    spine=("first", "safe"),
)
_LINEAR_ENTRIES = (
    ("EPUB/first.xhtml", _xhtml('<h1 epub:type="fulltitle">First</h1>'), zipfile.ZIP_DEFLATED),
    ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe</h1>'), zipfile.ZIP_DEFLATED),
)


@pytest.mark.parametrize(
    ("linear", "expected", "warned"),
    (
        (None, ["First", "Safe"], False),
        ("yes", ["First", "Safe"], False),
        ("no", ["Safe"], False),
        ("", ["Safe"], True),
        (" NO ", ["Safe"], True),
        ("NO", ["Safe"], True),
    ),
    ids=(
        "absent",
        "yes",
        "no",
        "empty",
        "padded_uppercase",
        "uppercase",
    ),
)
def test_raw_linear_values_are_exact_and_all_no_stays_empty(
    linear: str | None, expected: list[str], warned: bool
) -> None:
    base = _LINEAR_BASE_PACKAGE
    entries = _LINEAR_ENTRIES
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == expected
    assert ("unusable_spine_relationship" in report.warnings) == warned


def test_raw_all_no_spine_stays_empty() -> None:
    base = _LINEAR_BASE_PACKAGE
    entries = _LINEAR_ENTRIES
    all_no = base.replace(
        b'<itemref idref="first"/>',
        b'<itemref idref="first" linear="no"/>',
    ).replace(
        b'<itemref idref="safe"/>',
        b'<itemref idref="safe" linear="no"/>',
    )
    empty = _extract(_opening_epub(package=all_no, entries=entries))
    assert empty.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert not empty.resources
    assert empty.usage.selected_documents == 0


@pytest.mark.parametrize(
    "removed",
    (
        ("source_fingerprint",),
        ("same_file_lineage", "selection_digest"),
        ("source_fingerprint", "same_file_lineage", "selection_digest"),
    ),
    ids=("fingerprint", "lineage_and_digest", "all_three"),
)
def test_post_open_reports_reject_a_stripped_provenance_field(
    removed: tuple[str, ...],
) -> None:
    report = _extract(_single(_xhtml("<p>No claims here.</p>")))
    data = report.to_data()
    for field in removed:
        data[field] = None
    serialized = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(EvidenceValueError):
        EpubOpeningReport.from_json(serialized)


def test_post_open_reports_require_the_complete_bound_provenance_set() -> None:
    report = _extract(_single(_xhtml("<p>No claims here.</p>")))
    assert report.outcome == EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert report.resources
    assert report.usage.opened_resources > 0
    pristine = EpubOpeningReport(
        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
        EpubOpeningLimits(),
        report.source_fingerprint,
    )
    assert EpubOpeningReport.from_json(pristine.to_json()) == pristine


_FOREIGN_COLLISION_STRICT = b'''<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:f="urn:foreign" xmlns:s="http://www.w3.org/2000/svg"><body><f:h1 epub:type="fulltitle">Foreign Title</f:h1><f:section epub:type="bodymatter"><f:p epub:type="preview">Foreign Boundary</f:p></f:section><s:text>Renderable SVG Text</s:text><h1 epub:type="fulltitle">Safe HTML Title</h1></body></html>'''
_FOREIGN_COLLISION_NAV = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:f="urn:foreign"><body><f:nav epub:type="landmarks"><f:a epub:type="titlepage" href="fake.xhtml">Fake</f:a></f:nav></body></html>'''


@pytest.mark.parametrize(
    ("media_type", "body"),
    (
        ("application/xhtml+xml", _FOREIGN_COLLISION_STRICT),
        ("text/html", _FOREIGN_COLLISION_STRICT.replace(b"</body>", b"")),
    ),
    ids=("strict", "recovered"),
)
def test_foreign_local_name_collisions_are_semantically_inert(
    media_type: str, body: bytes
) -> None:
    report = _extract(_single(body, media_type=media_type))
    titles = {
        claim.raw_value
        for claim in report.claims
        if claim.field is OpeningField.TITLE
    }
    assert titles == {"Safe HTML Title"}
    assert "explicitly_excluded_resource" not in report.warnings
    assert "body_transition_reached" not in report.warnings


@pytest.mark.parametrize(
    "nav_body",
    (
        _FOREIGN_COLLISION_NAV,
        _FOREIGN_COLLISION_NAV.replace(b"</body>", b""),
    ),
    ids=("strict", "recovered"),
)
def test_foreign_local_name_collisions_never_authorize_navigation(
    nav_body: bytes,
) -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("fake", "fake.xhtml", "application/xhtml+xml", ""),
            ("safe", "safe.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("safe",),
    )
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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["Spine Safe"]


def test_recovery_undeclared_qnames_never_fall_back_to_html_identity() -> None:
    content = b'''<!doctype html><html xmlns:epub="http://www.idpf.org/2007/ops"><body><f:h1 epub:type="fulltitle">Undeclared Foreign Title</f:h1><f:section epub:type="bodymatter"><f:p epub:type="preview">Foreign Boundary</f:p></f:section><h1>Recovered HTML Safe</h1></body></html>'''
    report = _extract(_single(content, media_type="text/html"))
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Recovered HTML Safe"]
    assert "body_transition_reached" not in report.warnings
    assert "explicitly_excluded_resource" not in report.warnings

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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["QName Safe"]

    declared_alias = b'''<!doctype html><html xmlns:x="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><x:h1 epub:type="fulltitle">Declared Alias Title</x:h1></body></html>'''
    alias_report = _extract(
        _single(declared_alias, media_type="text/html")
    )
    assert "Declared Alias Title" in {claim.raw_value for claim in alias_report.claims}


def test_empty_prefix_bindings_are_unknown_across_recovery_consumers() -> None:
    body = b'''<!doctype html><html xmlns:f="" xmlns:epub="" xmlns:e="http://www.idpf.org/2007/ops"><body><f:h1 e:type="fulltitle">Empty Element Prefix</f:h1><h1 epub:type="fulltitle">Empty Attribute Prefix</h1><f:section e:type="bodymatter"><f:p e:type="preview">False Boundary</f:p></f:section><f:meta charset="shift_jis"></f:meta><h1 e:type="fulltitle">Empty Binding Safe</h1></body></html>'''
    report = _extract(_single(body))
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Empty Binding Safe"]
    assert "body_transition_reached" not in report.warnings
    assert "explicitly_excluded_resource" not in report.warnings

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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["Empty Nav Safe"]


def test_empty_default_namespace_resets_reenter_recovery_html() -> None:
    root = b'''<!doctype html><html xmlns="" xmlns:e="http://www.idpf.org/2007/ops"><head><meta charset="windows-1252"></head><body><p e:type="publisher">Root Reset Publisher\x92s</p><h1 e:type="fulltitle">Root Reset Title</h1><section e:type="bodymatter"><h1 e:type="fulltitle">Root Body Fake</h1></section></body></html>'''
    root_report = _extract(_single(root))
    assert root_report.outcome == EpubInspectionOutcome.SUCCESS
    assert [(claim.field, claim.raw_value) for claim in root_report.claims] == [
        (OpeningField.PUBLISHER, "Root Reset Publisher’s"),
        (OpeningField.TITLE, "Root Reset Title"),
    ]
    assert "body_transition_reached" in root_report.warnings

    subtree = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:outer-foreign"><section xmlns=""><meta charset="iso-8859-1"><p e:type="publisher">Subtree Caf\xe9 Publisher</p><svg><h1 e:type="fulltitle">Reset SVG Fake</h1></svg><math><h1 e:type="fulltitle">Reset Math Fake</h1></math><h1 e:type="fulltitle">Subtree Reset Title</h1></section><h1 e:type="fulltitle">Later Foreign Fake</h1></svg><h1 e:type="fulltitle">Later Safe Sibling</h1></body></html>'''
    subtree_report = _extract(_single(subtree))
    assert subtree_report.outcome == EpubInspectionOutcome.SUCCESS
    assert [(claim.field, claim.raw_value) for claim in subtree_report.claims] == [
        (OpeningField.PUBLISHER, "Subtree Café Publisher"),
        (OpeningField.TITLE, "Subtree Reset Title"),
        (OpeningField.TITLE, "Later Safe Sibling"),
    ]


@pytest.mark.parametrize(
    "navigation",
    (
        b'''<!doctype html><html xmlns="" xmlns:e="http://www.idpf.org/2007/ops"><body><nav e:type="landmarks"><a e:type="titlepage" href="safe.xhtml">Root reset</a></nav></body></html>''',
        b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:outer-foreign"><nav xmlns="" e:type="landmarks"><a e:type="titlepage" href="safe.xhtml">Subtree reset</a></nav><nav e:type="landmarks"><a e:type="titlepage" href="fake.xhtml">Later foreign</a></nav></svg></body></html>''',
    ),
    ids=("root_reset", "subtree_reset"),
)
def test_empty_default_namespace_resets_authorize_navigation(
    navigation: bytes,
) -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("fake", "fake.xhtml", "application/xhtml+xml", ""),
            ("safe", "safe.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("safe",),
    )
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Empty Reset Nav Safe"]
    assert report.usage.navigation_links == 1
    assert report.resources[1].selection_path == ("epub3_landmark", "titlepage")


@pytest.mark.parametrize(
    "body",
    (
        b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns=""><head><meta charset="windows-1252"></head><body><h1>Duplicate Default \x92</h1></html>''',
        b'''<!doctype html><html xmlns="" xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="iso-8859-1"></head><body><h1>Duplicate Default Reverse \xe9</h1></html>''',
        b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:e="urn:wrong"><body><h1>Duplicate Prefix \x92</h1></html>''',
        b'''<!doctype html><html xmlns:e="urn:wrong" xmlns:e="http://www.idpf.org/2007/ops"><head><f:meta xmlns:f="urn:foreign" charset="shift_jis"></f:meta><meta charset="iso-8859-1"></head><body><h1>Duplicate Prefix Reverse \xe9</h1></html>''',
        b'''<!doctype html><meta charset="windows-1252"><html xmlns="urn:unique-foreign" xmlns:e="http://www.idpf.org/2007/ops" xmlns:e="urn:wrong" xmlns:f="urn:other"><f:meta charset="shift_jis"></f:meta><meta charset="shift_jis"><h1>Mixed Unique Foreign \x92</h1></html><meta charset="cp1252">''',
        b'''<!doctype html><meta charset="iso-8859-1"><html xmlns="urn:unique-foreign" xmlns:e="urn:wrong" xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:other"><meta charset="shift_jis"><f:meta charset="shift_jis"></f:meta><h1>Mixed Reverse \xe9</h1></html><meta charset="latin-1">''',
    ),
    ids=(
        "duplicate_default",
        "duplicate_default_reverse",
        "duplicate_prefix",
        "duplicate_prefix_reverse",
        "mixed_unique_foreign",
        "mixed_unique_foreign_reverse",
    ),
)
def test_duplicate_recovery_authority_is_order_independent_and_closed(
    body: bytes,
) -> None:
    report = _extract(_single(body))
    assert report.resources[0].disposition == OpeningResourceDisposition.MALFORMED
    assert "malformed_selected_resource" in report.warnings


@pytest.mark.parametrize(
    "bindings",
    (
        'xmlns:e="http://www.idpf.org/2007/ops" xmlns:e="urn:wrong"',
        'xmlns:e="urn:wrong" xmlns:e="http://www.idpf.org/2007/ops"',
    ),
    ids=("controlled_first", "controlled_last"),
)
def test_duplicate_prefix_bindings_never_authorize_navigation(bindings: str) -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("fake", "fake.xhtml", "application/xhtml+xml", ""),
            ("safe", "safe.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("safe",),
    )
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
    assert report.resources[0].disposition == OpeningResourceDisposition.MALFORMED
    assert "unusable_navigation_document" in report.warnings
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Namespace Nav Safe"]


@pytest.mark.parametrize(
    "body",
    (
        b'''<!doctype html><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="http://www.idpf.org/2007/ops"><body><p e:type="publisher" f:type="publisher">Forged Publisher</p><section hidden hidden><h1 e:type="fulltitle">Hidden Forged</h1></section><h1 e:type="fulltitle">Duplicate Authority Safe</h1></html>''',
        b'''<!doctype html><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="http://www.idpf.org/2007/ops"><body><p f:type="publisher" e:type="publisher">Forged Publisher</p><section aria-hidden="false" aria-hidden="true"><h1 e:type="fulltitle">ARIA Forged</h1></section><h1 e:type="fulltitle">Duplicate Authority Safe</h1></html>''',
    ),
    ids=("hidden_attribute", "aria_hidden_attribute"),
)
def test_duplicate_recovery_semantics_never_forge_a_claim(body: bytes) -> None:
    report = _extract(_single(body))
    values = {claim.raw_value for claim in report.claims}
    assert values == {"Duplicate Authority Safe"}


@pytest.mark.parametrize(
    "hrefs",
    (
        'href="fake.xhtml" href="safe.xhtml"',
        'href="safe.xhtml" href="fake.xhtml"',
    ),
    ids=("fake_first", "safe_first"),
)
def test_duplicate_recovery_relationship_and_identity_use_safe_fallback(
    hrefs: str,
) -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("fake", "fake.xhtml", "application/xhtml+xml", ""),
            ("safe", "safe.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("safe",),
    )
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Relationship Fallback Safe"]
    assert "invalid_navigation_target" in report.warnings
    assert report.resources[1].selection_path == ("linear_spine_fallback",)


@pytest.mark.parametrize(
    "identities",
    (
        'id="target" xml:id="other"',
        'xml:id="other" id="target"',
        'id="target" id="target"',
    ),
    ids=("id_then_xml_id", "xml_id_then_id", "repeated_id"),
)
def test_duplicate_recovery_identities_use_safe_fallback(identities: str) -> None:
    identity_package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("opening", "opening.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("opening",),
    )
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
    assert "invalid_fragment_target" in report.warnings
    assert report.resources[1].selection_path == ("linear_spine_fallback",)
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Identity Fallback Safe"]

_DUPLICATE_MANIFEST_NAV = _xhtml(
    '<nav epub:type="landmarks"><a epub:type="titlepage" '
    'href="opening.xhtml">Opening</a></nav>'
)
_DUPLICATE_MANIFEST_CONTENT = _xhtml(
    '<h1 epub:type="fulltitle">Duplicate Manifest Safe</h1>'
)
_DUPLICATE_MANIFEST_ITEMS = (
    ("content", "opening.xhtml", "application/xhtml+xml", ""),
    ("alias", "opening.xhtml", "image/png", ""),
)


@pytest.mark.parametrize(
    "declarations",
    (_DUPLICATE_MANIFEST_ITEMS, tuple(reversed(_DUPLICATE_MANIFEST_ITEMS))),
    ids=("content_first", "alias_first"),
)
def test_duplicate_manifest_media_is_order_independent(
    declarations: tuple[tuple[str, str, str, str], ...],
) -> None:
    navigation = _DUPLICATE_MANIFEST_NAV
    content = _DUPLICATE_MANIFEST_CONTENT
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
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [
        claim.raw_value
        for claim in report.claims
        if claim.field is OpeningField.TITLE
    ] == ["Duplicate Manifest Safe"]
    assert report.resources[-1].selection_path == ("linear_spine_fallback",)


def test_duplicate_manifest_media_and_valid_fallback_are_order_independent() -> None:
    content = _DUPLICATE_MANIFEST_CONTENT
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
    assert fallback.outcome == EpubInspectionOutcome.SUCCESS
    assert [
        claim.raw_value
        for claim in fallback.claims
        if claim.field is OpeningField.TITLE
    ] == ["Duplicate Manifest Safe"]
    assert fallback.resources[0].selection_path == ("linear_spine_fallback",)


def _duplicate_href_publication(count: int, reverse: bool = False) -> bytes:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("fake", "fake.xhtml", "application/xhtml+xml", ""),
            ("safe", "safe.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("safe",),
    )
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


@pytest.mark.parametrize(
    ("count", "reverse"),
    ((1, False), (2, False), (2, True)),
    ids=("one_link", "two_links", "two_links_reversed"),
)
def test_duplicate_navigation_hrefs_count_before_validation(
    count: int, reverse: bool
) -> None:
    report = _extract(
        _duplicate_href_publication(count, reverse),
        EpubOpeningLimits(navigation_links=2),
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert report.usage.navigation_links == count
    assert "invalid_navigation_target" in report.warnings
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Budget Safe"]


def test_duplicate_navigation_hrefs_above_the_link_budget_are_refused() -> None:
    above = _extract(
        _duplicate_href_publication(3), EpubOpeningLimits(navigation_links=2)
    )
    assert above.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
    assert not above.claims
    assert not above.resources


def test_implicit_svg_and_math_subtrees_remain_foreign_in_recovery() -> None:
    body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg><meta charset="shift_jis"><h1 id="fake" e:type="fulltitle">SVG Fake</h1><section e:type="bodymatter"><p e:type="preview"><math><span e:type="publisher">Math Publisher Fake</span></math></p></section></svg><math><svg><h1 e:type="fulltitle">Nested Foreign Fake</h1></svg></math><h1 id="safe" e:type="fulltitle">Foreign Content Safe</h1></html>'''
    report = _extract(_single(body))
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Foreign Content Safe"]
    assert "body_transition_reached" not in report.warnings
    assert "explicitly_excluded_resource" not in report.warnings

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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["Foreign Nav Safe"]
    assert nav_report.resources[1].selection_path == ("epub3_landmark", "titlepage")


def test_recovery_stack_never_applies_html_closing_rules_to_foreign_nodes() -> None:
    body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg><p><li><h1 e:type="fulltitle">Foreign P LI Fake</h1></li></p><math><p><span e:type="publisher">Nested Math Fake</span></p></math></svg></svg></math><h1 e:type="fulltitle">Stack Safe Title</h1><p e:type="publisher">Stack Safe Publisher</p><section e:type="bodymatter">Body</section></html>'''
    report = _extract(_single(body))
    values = {(claim.field, claim.raw_value) for claim in report.claims}
    assert (OpeningField.TITLE, "Stack Safe Title") in values
    assert (OpeningField.PUBLISHER, "Stack Safe Publisher") in values
    assert "Foreign P LI Fake" not in {value for _field, value in values}
    assert "Nested Math Fake" not in {value for _field, value in values}
    assert "body_transition_reached" in report.warnings

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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["Stack Nav Safe"]
    assert nav_report.usage.navigation_links == 1


def test_unqualified_foreign_end_tags_use_the_open_element_identity() -> None:
    body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops"><body><svg xmlns="urn:explicit-svg"><meta charset="shift_jis"><h1 e:type="fulltitle">Explicit SVG Fake</h1></svg><math xmlns="urn:explicit-math"><h1 e:type="fulltitle">Explicit Math Fake</h1></math><meta charset="windows-1252"><h1 e:type="fulltitle">Publisher\x92s Explicit Close Safe</h1></html>'''
    report = _extract(_single(body))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Publisher’s Explicit Close Safe"]

    qualified = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:qualified"><body><f:svg><f:h1 e:type="fulltitle">Qualified Fake</f:h1></f:svg><h1 e:type="fulltitle">Qualified Close Safe</h1></html>'''
    qualified_report = _extract(_single(qualified))
    assert [claim.raw_value for claim in qualified_report.claims if claim.field is OpeningField.TITLE] == ["Qualified Close Safe"]

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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["Explicit Nav Safe"]
    assert nav_report.resources[1].selection_path == ("epub3_landmark", "titlepage")


def test_unqualified_end_tags_resolve_the_current_default_namespace() -> None:
    body = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:s="urn:foreign"><body><s:template><meta charset="shift_jis"></template><meta charset="shift_jis"><h1 e:type="fulltitle">Wrong Default Fake</h1></s:template><meta charset="windows-1252"><h1 e:type="fulltitle">Publisher\x92s Default Safe</h1></html>'''
    report = _extract(_single(body))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Publisher’s Default Safe"]

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
    assert [claim.raw_value for claim in nav_report.claims if claim.field is OpeningField.TITLE] == ["Default Namespace Safe"]
    assert nav_report.usage.navigation_links == 1


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        (
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops"><body><opaquealpha:template></opaquebeta:template><meta charset="shift_jis"><h1 e:type="fulltitle">Missing Prefix Fake</h1></opaquealpha:template><h1 e:type="fulltitle">Missing Prefix Safe</h1></body></html>''',
            "Missing Prefix Safe",
        ),
        (
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="" xmlns:g=""><body><f:template></g:template><meta charset="shift_jis"><h1 e:type="fulltitle">Empty Prefix Fake</h1></f:template><h1 e:type="fulltitle">Empty Prefix Safe</h1></body></html>''',
            "Empty Prefix Safe",
        ),
        (
            b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f=""><body><f:template></g:template><meta charset="shift_jis"><h1 e:type="fulltitle">Mixed Prefix Fake</h1></f:template><h1 e:type="fulltitle">Mixed Prefix Safe</h1></body></html>''',
            "Mixed Prefix Safe",
        ),
    ),
    ids=("missing_prefix", "empty_prefix", "mixed_prefix"),
)
def test_unknown_prefix_identities_are_prefix_specific(
    body: bytes, expected: str
) -> None:
    report = _extract(_single(body))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == [expected]
    assert "opaquealpha" not in report.to_json()
    assert "opaquebeta" not in report.to_json()


def test_duplicate_prefix_bindings_are_malformed() -> None:
    duplicate = b'''<!doctype html><meta charset="windows-1252"><html xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:first" xmlns:f="urn:second"><body><f:template><f:meta charset="shift_jis"></f:meta></g:template><meta charset="shift_jis"></f:template><h1 e:type="fulltitle">Duplicate Prefix Content</h1></body></html>'''
    duplicate_report = _extract(_single(duplicate))
    assert duplicate_report.resources[0].disposition == OpeningResourceDisposition.MALFORMED
    assert "malformed_selected_resource" in duplicate_report.warnings


@pytest.mark.parametrize(
    "name",
    (
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ),
)
def test_self_closing_recovery_tags_only_close_the_start_they_pushed(
    name: str,
) -> None:
    body = (
        '<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:e="http://www.idpf.org/2007/ops"><body>'
        f'<{name} xmlns="urn:foreign" hidden><{name} xmlns="http://www.w3.org/1999/xhtml"/>'
        '<h1 xmlns="http://www.w3.org/1999/xhtml" e:type="fulltitle">Void Fake</h1>'
        f'</{name}><h1 e:type="fulltitle">Void {name} Safe</h1></body></html>'
    ).encode()
    report = _extract(_single(body))
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == [f"Void {name} Safe"]


def test_nonvoid_self_closing_recovery_tags_close_their_own_start() -> None:
    nonvoid = b'''<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:e="http://www.idpf.org/2007/ops" xmlns:f="urn:foreign"><body><template/><f:template/><section xmlns="urn:foreign"/><h1 e:type="fulltitle">Nonvoid Self Close Safe</h1></body></html>'''
    nonvoid_report = _extract(_single(nonvoid))
    assert [claim.raw_value for claim in nonvoid_report.claims if claim.field is OpeningField.TITLE] == ["Nonvoid Self Close Safe"]


@pytest.mark.parametrize(
    ("outer", "inner"),
    (("p", "p"), ("li", "li"), ("dt", "dd"), ("dd", "dt")),
    ids=("p_p", "li_li", "dt_dd", "dd_dt"),
)
def test_html_implied_closes_stop_at_each_foreign_boundary(
    outer: str, inner: str
) -> None:
    body = (
        '<!doctype html><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:e="http://www.idpf.org/2007/ops"><body>'
        f'<{outer} e:type="publisher">Outer Before '
        '<svg><math><div xmlns="http://www.w3.org/1999/xhtml">'
        f'</{outer}><{inner}>Inner Reentry</{inner}></div></math></svg> Outer Tail'
        f'</{outer}><h1 e:type="fulltitle">Boundary Safe Title</h1></html>'
    ).encode()
    report = _extract(_single(body))
    publishers = [
        claim.raw_value
        for claim in report.claims
        if claim.field is OpeningField.PUBLISHER
    ]
    assert len(publishers) == 1
    assert "Outer Tail" in publishers[0]
    assert "Boundary Safe Title" in {claim.raw_value for claim in report.claims}


def test_cross_foreign_misnested_end_cannot_authorize_navigation() -> None:
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
    assert [claim.raw_value for claim in report.claims if claim.field is OpeningField.TITLE] == ["Cross Boundary Safe"]
    assert report.resources[1].selection_path == ("epub3_landmark", "titlepage")
    assert report.usage.navigation_links == 1


@settings(max_examples=30, deadline=None)
@given(st.text(max_size=120))
def test_recovered_html_text_is_bounded_inert_data(value: str) -> None:
    content = (
        '<!doctype html><html><body><h1 epub:type="fulltitle">'
        + html.escape(value)
        + "</h1><script>fetch('https://example.invalid')</script></body></html>"
    ).encode("utf-8")
    report = _extract(_single(content, media_type="text/html"))
    assert report.outcome in {EpubInspectionOutcome.SUCCESS, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE}
    assert "example.invalid" not in report.to_json()
