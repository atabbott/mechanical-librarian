"""Direct unit tests for each EPUB package extraction stage.

These cases reach the stages that ``_package_report`` composes without going
through an archive, so a stage's rejection behaviour is provable on its own
rather than only observable as a whole-report outcome.
"""

from __future__ import annotations

import hashlib
import re
import time
import zipfile
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    _archive_shape_warnings,
    _bounded_report,
    _calibre_series_observations,
    _check_section_sequence,
    _consistency_warnings,
    _container_declarations,
    _cover_observations,
    _declared_resource_identity,
    _dublin_core_observations,
    _dublin_core_value,
    _encryption_warnings,
    _Entry,
    _guide_declarations,
    _has_compatible_spine_target,
    _identifier_registry,
    _InvalidReferenceSyntax,
    _Manifest,
    _manifest_declarations,
    _manifest_integrity,
    _manifest_item,
    _meta_observations,
    _metadata_field_observations,
    _metadata_refinements,
    _ncx_declaration,
    _Node,
    _package_frame,
    _package_identity_observations,
    _PackageFrame,
    _parse_xml,
    _prefix_vocabulary,
    _refinement_cycle_warnings,
    _Refinements,
    _Rejected,
    _required_field_warnings,
    _selected_cover,
    _spine_declarations,
    _title_refinements,
    _tours_warnings,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageDeclaration,
    EpubPackageDeclarationKind,
    EpubPackageField,
    EpubPackageLimits,
    EpubPackageObservation,
    EpubPackageRequest,
    EpubPackageSource,
    EpubPackageUsage,
    EpubPackageWarning,
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

_OPF = "http://www.idpf.org/2007/opf"
_PACKAGE = "OEBPS/content.opf"


def _request(limits: EpubPackageLimits | None = None) -> EpubPackageRequest:
    value = b"synthetic-package-source"
    return EpubPackageRequest(
        TargetId(
            AdapterId("synthetic"),
            ConnectionId("local"),
            LibraryId("library"),
            ExternalItemId("book"),
        ),
        FileReference("books", "generated/book.epub"),
        EpubPackageSource(len(value), lambda offset, size: value[offset : offset + size]),
        Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()),
        EpubPackageCapability.PACKAGE,
        EpubPackageLimits() if limits is None else limits,
        datetime.now(timezone.utc) + timedelta(seconds=30),
        time.monotonic() + 30.0,
        EpubPackageCancellation("stage-request", lambda: False),
    )


def _parse(xml: bytes, request: EpubPackageRequest | None = None) -> _Node:
    active = _request() if request is None else request
    root, _usage = _parse_xml(xml, active.limits, active)
    return root


def _entries(*names: str) -> dict[str, _Entry]:
    return {
        name: _Entry(name, name.encode(), 0x0808, zipfile.ZIP_DEFLATED, 0, 1, 1, 0, 0, 0, 0)
        for name in names
    }


def _item(attributes: str) -> _Node:
    return _parse(f'<item xmlns="{_OPF}" {attributes}/>'.encode())


def _no_refinements() -> _Refinements:
    return _Refinements(
        False, False, {"calibre:series": [], "calibre:series_index": []}, {}, {}
    )


def _package(
    *,
    version: str = "3.0",
    metadata: str = "",
    manifest: str = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    spine: str = '<itemref idref="nav"/>',
    spine_attributes: str = "",
    trailing: str = "",
    attributes: str = "",
) -> bytes:
    return (
        f'<package xmlns="{_OPF}" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        f'version="{version}"{attributes}>'
        f"<metadata>{metadata}</metadata>"
        f"<manifest>{manifest}</manifest>"
        f"<spine{spine_attributes}>{spine}</spine>{trailing}</package>"
    ).encode()


def _codes(warnings: Sequence[EpubPackageWarning]) -> list[str]:
    return [warning.code for warning in warnings]


# Stage: container/rootfile-resolved package shell.
def test_unsupported_root_element_is_rejected() -> None:
    root = _parse(b'<container xmlns="urn:x"/>')
    with pytest.raises(_Rejected) as raised:
        _package_frame(_request(), root)
    assert raised.value.outcome is EpubInspectionOutcome.UNSUPPORTED_FORMAT


def test_version_family_is_resolved_or_rejected() -> None:
    for declared, expected in (("1.0", "2.0"), ("2.0", "2.0"), ("2.0.1", "2.0"), ("3.0", "3.0"), ("3.3", "3.0")):
        frame = _package_frame(_request(), _parse(_package(version=declared)))
        assert (frame.version, frame.declared_version) == (expected, declared)
    for declared in ("", "4.0", "2", "3", "3.x", " 3.0", "03.0"):
        with pytest.raises(_Rejected) as raised:
            _package_frame(_request(), _parse(_package(version=declared)))
        assert raised.value.outcome is EpubInspectionOutcome.UNSUPPORTED_VERSION


def test_prefix_vocabulary_rejects_every_malformed_declaration() -> None:
    for prefix in (
        "foo:",
        "foo: bar: https://example.invalid/a",
        "foo: not-an-iri",
        "1bad: https://example.invalid/a",
        "foo https://example.invalid/a",
        "foo: https://example.invalid/a foo: https://example.invalid/b",
    ):
        root = _parse(_package(attributes=f' prefix="{prefix}"'))
        with pytest.raises(_Rejected) as raised:
            _prefix_vocabulary(_request(), root)
        assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_prefix_vocabulary_rejects_rather_than_truncating_over_the_limit() -> None:
    limits = EpubPackageLimits(prefix_mappings=1)
    root = _parse(
        _package(
            attributes=' prefix="a: https://example.invalid/a b: https://example.invalid/b"'
        )
    )
    with pytest.raises(_Rejected) as raised:
        _prefix_vocabulary(_request(limits), root)
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED
    assert len(_prefix_vocabulary(_request(), root)) == 2


def test_section_sequence_rejects_unsupported_top_level_shapes() -> None:
    reordered = (
        f'<package xmlns="{_OPF}" version="3.0"><manifest/><metadata/><spine/></package>'
    ).encode()
    with pytest.raises(_Rejected) as raised:
        _check_section_sequence(_parse(reordered), "3.0")
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED
    epub2 = _parse(_package(version="2.0", trailing="<bindings/>"))
    with pytest.raises(_Rejected):
        _check_section_sequence(epub2, "2.0")
    _check_section_sequence(_parse(_package(trailing="<guide/><bindings/><collection/>")), "3.0")


def test_repeated_section_group_is_rejected() -> None:
    duplicated = (
        f'<package xmlns="{_OPF}" version="3.0"><metadata/><manifest/>'
        "<spine/><metadata/></package>"
    ).encode()
    with pytest.raises(_Rejected) as raised:
        _package_frame(_request(), _parse(duplicated))
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_empty_manifest_or_spine_is_rejected() -> None:
    for manifest, spine in ((" ", '<itemref idref="nav"/>'), ('<item id="a" href="a" media-type="x/y"/>', " ")):
        with pytest.raises(_Rejected) as raised:
            _package_frame(_request(), _parse(_package(manifest=manifest, spine=spine)))
        assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_section_bounds_reject_rather_than_truncate() -> None:
    metadata = "".join(f"<dc:title>t{index}</dc:title>" for index in range(4))
    root = _parse(_package(metadata=metadata))
    assert len(_package_frame(_request(), root).metadata_nodes) == 4
    with pytest.raises(_Rejected) as raised:
        _package_frame(_request(EpubPackageLimits(metadata_nodes=3)), root)
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT
    wide = _parse(
        _package(
            manifest=(
                '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>'
            ),
            spine='<itemref idref="a"/><itemref idref="b"/>',
        )
    )
    with pytest.raises(_Rejected) as raised:
        _package_frame(_request(EpubPackageLimits(manifest_items=1)), wide)
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT
    with pytest.raises(_Rejected) as raised:
        _package_frame(_request(EpubPackageLimits(spine_items=1)), wide)
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT


def test_epub2_wrapper_elements_are_flattened_into_metadata_nodes() -> None:
    frame = _package_frame(
        _request(),
        _parse(
            _package(
                version="2.0",
                metadata="<dc-metadata><dc:title>Wrapped</dc:title></dc-metadata>",
                spine_attributes=' toc="ncx"',
            )
        ),
    )
    assert [node.text for node in frame.metadata_nodes] == ["Wrapped"]


def test_malformed_xml_never_reaches_the_frame_stage() -> None:
    for payload in (b"<package", b"<!DOCTYPE p><package/>", b"<a/><b/>", b""):
        with pytest.raises(_Rejected):
            _parse(payload)


# Stage: archive-level warnings that never change acceptance.
def test_noncanonical_inputs_are_reported_not_rejected() -> None:
    frame = _package_frame(_request(), _parse(_package(version="3.3", trailing="<tours/>")))
    entries = _entries("mimetype")
    entries["mimetype"] = _Entry("mimetype", b"mimetype", 0x0001, zipfile.ZIP_STORED, 0, 1, 1, 0, 0, 0, 0)
    assert sorted(_codes(_archive_shape_warnings(entries, frame, True))) == ["deprecated_tours", "noncanonical_mimetype", "noncanonical_package_version", "noncanonical_zip_flags"]


def test_canonical_archive_reports_nothing() -> None:
    frame = _package_frame(_request(), _parse(_package()))
    assert _archive_shape_warnings(_entries("mimetype"), frame, False) == []


# Stage: rootfile, package and cipher-reference declarations.
def test_every_opened_control_document_is_declared() -> None:
    root = _parse(_package())
    rootfile = _parse(b'<rootfile full-path="OEBPS/content.opf"/>')
    cipher = _parse(b'<CipherReference URI="OEBPS/secret.xhtml"/>')
    declarations = _container_declarations(
        _request(), _PACKAGE, root, rootfile, [("OEBPS/secret.xhtml", cipher)]
    )
    assert [declaration.kind for declaration in declarations] == [
            EpubPackageDeclarationKind.ROOTFILE,
            EpubPackageDeclarationKind.PACKAGE,
            EpubPackageDeclarationKind.ENCRYPTION,
        ]


# Stage: the package's unambiguous id map.
def test_duplicate_ids_are_withdrawn_rather_than_resolved() -> None:
    root = _parse(
        _package(
            metadata='<dc:title id="dup">A</dc:title><dc:creator id="dup">B</dc:creator>',
        )
    )
    registry, warnings = _identifier_registry(_request(), _PACKAGE, root)
    assert "dup" not in registry
    assert "ambiguous_package_id" in _codes(warnings)


def test_invalid_id_syntax_is_reported_and_dropped() -> None:
    root = _parse(_package(metadata='<dc:title id="1bad">A</dc:title>'))
    registry, warnings = _identifier_registry(_request(), _PACKAGE, root)
    assert "1bad" not in registry
    assert _codes(warnings) == ["invalid_package_id"]


def test_third_claim_on_a_withdrawn_id_stays_withdrawn() -> None:
    root = _parse(
        _package(
            metadata=(
                '<dc:title id="dup">A</dc:title><dc:creator id="dup">B</dc:creator>'
                '<dc:publisher id="dup">C</dc:publisher>'
            )
        )
    )
    registry, warnings = _identifier_registry(_request(), _PACKAGE, root)
    assert "dup" not in registry
    assert _codes(warnings).count("ambiguous_package_id") == 2


# Stage: metadata refinement resolution.
def _frame_and_registry(
    metadata: str
) -> tuple[_PackageFrame, dict[str, _Node]]:
    root = _parse(_package(metadata=metadata))
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    return _package_frame(_request(), root), registry


def test_unusable_refinement_edges_are_reported() -> None:
    frame, registry = _frame_and_registry(
        '<meta id="a" refines="missing" property="role">aut</meta>'
        '<meta id="b" refines="#absent" property="role">aut</meta>'
    )
    codes = _codes(_refinement_cycle_warnings(frame.metadata_nodes, registry))
    assert sorted(codes) == ["invalid_metadata_refinement", "unresolved_metadata_refinement"]


def test_refinement_cycle_terminates_and_is_reported() -> None:
    frame, registry = _frame_and_registry(
        '<meta id="a" refines="#b" property="role">aut</meta>'
        '<meta id="b" refines="#a" property="role">aut</meta>'
    )
    assert "cyclic_metadata_refinement" in _codes(_refinement_cycle_warnings(frame.metadata_nodes, registry))


def test_title_refinement_to_a_non_title_is_reported_and_dropped() -> None:
    frame, registry = _frame_and_registry(
        '<dc:creator id="c">Author</dc:creator>'
        '<meta refines="#c" property="title-type">main</meta>'
    )
    types, sequences, warnings = _title_refinements(frame.metadata_nodes, {}, registry)
    assert (types, sequences) == ({}, {})
    assert _codes(warnings) == ["inapplicable_metadata_refinement"]


def test_competing_title_refinements_are_withdrawn() -> None:
    frame, registry = _frame_and_registry(
        '<dc:title id="t">Book</dc:title>'
        '<meta refines="#t" property="title-type">main</meta>'
        '<meta refines="#t" property="title-type">subtitle</meta>'
    )
    types, _sequences, warnings = _title_refinements(frame.metadata_nodes, {}, registry)
    assert types == {}
    assert _codes(warnings) == ["ambiguous_metadata_refinement"]


def test_display_sequence_outside_its_range_is_reported_and_dropped() -> None:
    for value in ("-1", "1000001", "not-a-number", "1e3"):
        frame, registry = _frame_and_registry(
            '<dc:title id="t">Book</dc:title>'
            f'<meta refines="#t" property="display-seq">{value}</meta>'
        )
        _types, sequences, warnings = _title_refinements(frame.metadata_nodes, {}, registry)
        assert sequences == {}
        assert _codes(warnings) == ["invalid_metadata_refinement"]


def test_unsupported_title_type_is_reported_and_dropped() -> None:
    frame, registry = _frame_and_registry(
        '<dc:title id="t">Book</dc:title>'
        '<meta refines="#t" property="title-type">vendor</meta>'
    )
    types, _sequences, warnings = _title_refinements(frame.metadata_nodes, {}, registry)
    assert types == {}
    assert _codes(warnings) == ["unsupported_vendor_metadata"]


def test_calibre_pairs_are_collected_without_projection() -> None:
    frame, registry = _frame_and_registry(
        '<meta name="calibre:series" content="Saga"/>'
        '<meta name="calibre:series_index" content="2"/>'
    )
    refinements, warnings = _metadata_refinements(frame, registry)
    assert [value for value, _node in refinements.calibre["calibre:series"]] == ["Saga"]
    assert warnings == []


# Stage: Dublin Core and ``<meta>`` field projection.
def _project(
    metadata: str
) -> tuple[list[EpubPackageObservation], list[EpubPackageWarning]]:
    root = _parse(_package(metadata=metadata))
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    frame = _package_frame(_request(), root)
    refinements, _refinement_warnings = _metadata_refinements(frame, registry)
    return _metadata_field_observations(
        _request(), _PACKAGE, frame, registry, {}, refinements
    )


def test_invalid_date_is_preserved_verbatim_and_marked_inapplicable() -> None:
    observations, warnings = _project("<dc:date>1999-13-45</dc:date>")
    assert _codes(warnings) == ["invalid_date"]
    assert observations[0].raw_value == "1999-13-45"
    assert observations[0].normalized_value is None
    assert observations[0].applicability is EpubObservationApplicability.INAPPLICABLE


def test_invalid_language_is_preserved_verbatim_and_marked_inapplicable() -> None:
    observations, warnings = _project("<dc:language>english!</dc:language>")
    assert _codes(warnings) == ["invalid_language"]
    assert observations[0].applicability is EpubObservationApplicability.INAPPLICABLE


def test_invalid_identifier_is_reported_without_an_inferred_scheme() -> None:
    observations, warnings = _project(
        "<dc:identifier>ISBN 978-0-000-00000-1</dc:identifier>"
    )
    assert "invalid_identifier" in _codes(warnings)
    assert EpubPackageField.IDENTIFIER_SCHEME not in {observation.field for observation in observations}


def test_empty_dublin_core_element_contributes_nothing() -> None:
    assert _project("<dc:title>   </dc:title>") == ([], [])


def test_unknown_meta_property_is_reported_and_never_projected() -> None:
    observations, warnings = _project('<meta property="vendor:secret">x</meta>')
    assert observations == []
    assert _codes(warnings) == ["unsupported_vendor_metadata"]


def test_refinement_to_an_inapplicable_target_projects_nothing() -> None:
    observations, warnings = _project(
        '<dc:title id="t">Book</dc:title><meta refines="#t" property="role">aut</meta>'
    )
    assert [item.field for item in observations] == [EpubPackageField.TITLE]
    assert "inapplicable_metadata_refinement" in _codes(warnings)


def test_invalid_group_position_is_reported_and_never_projected() -> None:
    root = _parse(_package(metadata='<meta property="group-position">0</meta>'))
    frame = _package_frame(_request(), root)
    observations, warnings = _meta_observations(
        _request(), _PACKAGE, frame, frame.metadata_nodes[0], {}
    )
    assert observations == []
    assert _codes(warnings) == ["invalid_metadata_refinement"]


def test_oversize_value_is_replaced_by_a_digest_marker_not_truncated() -> None:
    value = "A" * (EpubPackageLimits.MAX_RAW_VALUE_CHARACTERS + 1)
    root = _parse(_package(metadata=f"<dc:title>{value}</dc:title>"))
    frame = _package_frame(_request(), root)
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    refinements, _refinement_warnings = _metadata_refinements(frame, registry)
    observations, _observation_warnings = _dublin_core_observations(
        _request(), _PACKAGE, frame, frame.metadata_nodes[0], "title", registry, {}, refinements
    )
    assert observations[0].raw_value.startswith("oversized-source-text-sha256:")
    assert "A" * 64 not in observations[0].raw_value


def test_required_fields_are_measured_only_over_metadata_observations() -> None:
    observations, _warnings = _project("<dc:title>Book</dc:title>")
    assert _codes(_required_field_warnings(observations, "3.0")) == ["incomplete_required_metadata", "missing_modification_date"]


def test_package_identity_infers_a_lone_identifier_and_reports_it() -> None:
    root = _parse(_package(metadata='<dc:identifier id="pub">urn:uuid:not-a-uuid</dc:identifier>'))
    frame = _package_frame(_request(), root)
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    refinements, _refinement_warnings = _metadata_refinements(frame, registry)
    identifiers: dict[str, _Node] = {}
    _metadata_field_observations(_request(), _PACKAGE, frame, registry, identifiers, refinements)
    observations, warnings = _package_identity_observations(
        _request(), _PACKAGE, frame, root, identifiers
    )
    assert _codes(warnings) == ["inferred_package_identifier"]
    assert [item.field for item in observations] == [EpubPackageField.PACKAGE_IDENTIFIER, EpubPackageField.PACKAGE_VERSION]


def test_two_candidate_identifiers_are_never_guessed() -> None:
    root = _parse(_package())
    frame = _package_frame(_request(), root)
    node = _parse(b"<dc:identifier xmlns:dc='http://purl.org/dc/elements/1.1/'>a</dc:identifier>")
    observations, warnings = _package_identity_observations(
        _request(), _PACKAGE, frame, root, {"a": node, "b": node}
    )
    assert _codes(warnings) == ["missing_package_identifier"]
    assert [item.field for item in observations] == [EpubPackageField.PACKAGE_VERSION]


# Stage: manifest item acceptance and cross-item integrity.
def _manifest(
    manifest: str, entries: dict[str, _Entry] | None = None
) -> tuple[_Manifest, list[EpubPackageDeclaration], list[EpubPackageWarning]]:
    root = _parse(
        _package(version="2.0", manifest=manifest, spine='<itemref idref="a"/>')
    )
    frame = _package_frame(_request(), root)
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    return _manifest_declarations(
        _request(),
        _PACKAGE,
        frame,
        _entries("OEBPS/a.xhtml", "OEBPS/b.xhtml") if entries is None else entries,
        registry,
    )


def test_foreign_manifest_element_rejects_the_package() -> None:
    with pytest.raises(_Rejected) as raised:
        _manifest('<foreign id="a" href="a.xhtml" media-type="application/xhtml+xml"/>')
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_path_escape_rejects_the_package() -> None:
    for href in ("../../etc/passwd", "..%2f..%2fetc", "OEBPS/..\\..\\x", "/absolute"):
        with pytest.raises(_Rejected) as raised:
            _manifest(
                f'<item id="a" href="{href}" media-type="application/xhtml+xml"/>'
            )
        assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_oversize_reference_is_rejected_rather_than_truncated() -> None:
    href = "a" * (EpubPackageLimits.MEMBER_NAME_UTF8_BYTES_HARD_CAP + 1)
    with pytest.raises(_Rejected) as raised:
        _manifest(f'<item id="a" href="{href}" media-type="application/xhtml+xml"/>')
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT


def test_duplicate_manifest_id_drops_both_items() -> None:
    manifest, declarations, warnings = _manifest(
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="a" href="b.xhtml" media-type="application/xhtml+xml"/>'
    )
    assert manifest.ids == {}
    assert declarations == []
    assert _codes(warnings).count("invalid_manifest_item") == 2


def test_duplicate_resource_is_reported_but_still_declared() -> None:
    manifest, declarations, warnings = _manifest(
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="b" href="a.xhtml" media-type="application/xhtml+xml"/>'
    )
    assert len(declarations) == 2
    assert _codes(warnings) == ["duplicate_manifest_resource"]
    assert set(manifest.ids) == {"a", "b"}


def test_missing_local_resource_is_dropped_whole() -> None:
    manifest, declarations, warnings = _manifest(
        '<item id="a" href="absent.xhtml" media-type="application/xhtml+xml"/>'
    )
    assert (manifest.ids, declarations) == ({}, [])
    assert _codes(warnings) == ["missing_manifest_resource"]


def test_unsafe_property_text_is_reported_and_dropped() -> None:
    manifest, _declarations, warnings = _manifest(
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" '
        'properties="a\u202eb"/>'
    )
    assert manifest.ids == {}
    assert _codes(warnings) == ["unsafe_manifest_item"]


def test_ambiguous_manifest_identifier_never_resolves() -> None:
    manifest, _declarations, warnings = _manifest(
        '<item id="a\u202eb" href="a.xhtml" media-type="application/xhtml+xml"/>'
    )
    assert manifest.ids == {}
    assert _codes(warnings) == ["invalid_manifest_item"]


def test_oversize_property_list_is_rejected_per_item_not_trimmed() -> None:
    properties = " ".join(f"p{index}" for index in range(65))
    manifest, declarations, warnings = _manifest(
        f'<item id="a" href="a.xhtml" media-type="application/xhtml+xml" properties="{properties}"/>'
    )
    assert (manifest.ids, declarations) == ({}, [])
    assert _codes(warnings) == ["invalid_manifest_item"]


def test_unresolvable_fallback_is_pruned_and_reported() -> None:
    manifest, _declarations, warnings = _manifest(
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" fallback="absent"/>'
        '<item id="b" href="b.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    )
    assert manifest.fallbacks == {}
    assert "invalid_manifest_fallback" in _codes(warnings)


def test_fallback_cycle_is_reported_and_never_followed_forever() -> None:
    manifest, _declarations, warnings = _manifest(
        '<item id="a" href="a.xhtml" media-type="x/one" fallback="b"/>'
        '<item id="b" href="b.xhtml" media-type="x/two" fallback="a"/>'
    )
    assert "cyclic_manifest_fallback" in _codes(warnings)
    assert not _has_compatible_spine_target("a", manifest, {"application/xhtml+xml"})


def test_media_overlay_must_be_a_local_smil_document() -> None:
    _manifest_state, _declarations, warnings = _manifest(
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" media-overlay="absent"/>'
        '<item id="b" href="b.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    )
    assert "invalid_media_overlay" in _codes(warnings)


def test_epub3_requires_exactly_one_navigation_document() -> None:
    state = _Manifest()
    assert _codes(_manifest_integrity(state, "3.0")) == ["invalid_navigation_declaration"]
    state.nav_count = 1
    assert _manifest_integrity(state, "3.0") == []
    assert _manifest_integrity(_Manifest(), "2.0") == []


def test_contradictory_cover_properties_are_reported() -> None:
    state = _Manifest(
        ids={"a": "d" * 64, "b": "e" * 64}, cover_ids=["a", "b"]
    )
    assert "contradictory_cover_declaration" in _codes(_manifest_integrity(state, "2.0"))


# Stage: spine slot and reading-order projection.
def _spine(
    spine: str, manifest_items: str, version: str = "3.0"
) -> tuple[list[EpubPackageDeclaration], list[EpubPackageWarning]]:
    root = _parse(_package(version=version, manifest=manifest_items, spine=spine))
    frame = _package_frame(_request(), root)
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    manifest, _declarations, _manifest_warnings = _manifest_declarations(
        _request(), _PACKAGE, frame, _entries("OEBPS/a.xhtml", "OEBPS/b.png"), registry
    )
    return _spine_declarations(_request(), _PACKAGE, frame, manifest)


def test_foreign_spine_element_is_dropped_not_rejected() -> None:
    declarations, warnings = _spine(
        "<foreign/>", '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    )
    assert declarations == []
    assert _codes(warnings) == ["invalid_spine_item"]


def test_itemref_with_content_is_dropped() -> None:
    declarations, warnings = _spine(
        '<itemref idref="a">text</itemref>',
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    )
    assert declarations == []
    assert _codes(warnings) == ["invalid_spine_item"]


def test_repeated_idref_occupies_a_slot_but_is_accepted_once() -> None:
    declarations, warnings = _spine(
        '<itemref idref="a"/><itemref idref="a"/>',
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    )
    kinds = [declaration.kind for declaration in declarations]
    assert kinds.count(EpubPackageDeclarationKind.SPINE_SLOT) == 2
    assert kinds.count(EpubPackageDeclarationKind.SPINE_ITEM) == 1
    assert "invalid_spine_item" in _codes(warnings)


def test_incompatible_media_type_occupies_a_slot_only() -> None:
    declarations, warnings = _spine(
        '<itemref idref="b"/>',
        '<item id="a" href="a.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="b" href="b.png" media-type="image/png"/>',
    )
    assert [declaration.kind for declaration in declarations] == [EpubPackageDeclarationKind.SPINE_SLOT]
    assert "invalid_spine_item" in _codes(warnings)


# Stages: NCX, deprecated tours and guide references.
def _frame(**kwargs: str) -> _PackageFrame:
    return _package_frame(_request(), _parse(_package(**kwargs)))


def test_epub2_without_a_toc_is_reported() -> None:
    frame = _frame(version="2.0")
    declarations, warnings = _ncx_declaration(_request(), _PACKAGE, frame, _Manifest())
    assert declarations == []
    assert _codes(warnings) == ["invalid_ncx_declaration"]


def test_toc_pointing_at_a_non_ncx_is_reported() -> None:
    frame = _frame(spine_attributes=' toc="a"')
    manifest = _Manifest(
        ids={"a": "d" * 64},
        nodes={"a": _parse(b'<item id="a"/>')},
        media_types={"a": "application/xhtml+xml"},
        local_ids={"a"},
    )
    declarations, warnings = _ncx_declaration(_request(), _PACKAGE, frame, manifest)
    assert declarations == []
    assert _codes(warnings) == ["invalid_ncx_declaration"]


def test_tours_in_epub3_are_reported_as_invalid() -> None:
    frame = _frame(trailing="<tours><tour title='t'><site title='s' href='a.xhtml'/></tour></tours>")
    warnings = _tours_warnings(_PACKAGE, frame, _entries("OEBPS/a.xhtml"), _Manifest())
    assert _codes(warnings) == ["invalid_deprecated_tours"]


def test_tour_site_escaping_the_container_rejects_the_package() -> None:
    frame = _frame(
        version="2.0",
        spine_attributes=' toc="a"',
        trailing="<tours><tour title='t'><site title='s' href='../../etc/passwd'/></tour></tours>",
    )
    with pytest.raises(_Rejected) as raised:
        _tours_warnings(_PACKAGE, frame, _entries("OEBPS/a.xhtml"), _Manifest())
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_guide_bound_rejects_rather_than_truncating() -> None:
    references = "".join(
        '<reference type="text" href="a.xhtml" />' for _ in range(3)
    )
    root = _parse(_package(trailing=f"<guide>{references}</guide>"))
    with pytest.raises(_Rejected) as raised:
        _guide_declarations(
            _request(EpubPackageLimits(guide_references=2)),
            _PACKAGE,
            root,
            _entries("OEBPS/a.xhtml"),
        )
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT


def test_guide_reference_escaping_the_container_rejects_the_package() -> None:
    root = _parse(
        _package(trailing='<guide><reference type="text" href="../../etc/passwd"/></guide>')
    )
    with pytest.raises(_Rejected) as raised:
        _guide_declarations(_request(), _PACKAGE, root, _entries("OEBPS/a.xhtml"))
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_unusable_guide_references_are_dropped_individually() -> None:
    root = _parse(
        _package(
            trailing=(
                "<guide>"
                '<foreign/>'
                '<reference href="a.xhtml"/>'
                '<reference type="1bad" href="a.xhtml"/>'
                '<reference type="text" href="absent.xhtml"/>'
                '<reference type="text" href="a.xhtml"/>'
                "</guide>"
            )
        )
    )
    declarations, warnings = _guide_declarations(
        _request(), _PACKAGE, root, _entries("OEBPS/a.xhtml")
    )
    assert len(declarations) == 1
    assert _codes(warnings) == ["invalid_guide_reference"] * 4


# Stage: encryption accounting.
def test_encrypted_control_document_rejects_the_package() -> None:
    for name in ("mimetype", "META-INF/container.xml", "META-INF/encryption.xml", _PACKAGE):
        with pytest.raises(_Rejected) as raised:
            _encryption_warnings(_PACKAGE, {name}, True)
        assert raised.value.outcome is EpubInspectionOutcome.ENCRYPTED


def test_encrypted_content_is_only_reported() -> None:
    assert _codes(_encryption_warnings(_PACKAGE, {"OEBPS/secret.xhtml"}, True)) == ["opaque_resource_encrypted", "encryption_declarations_present"]
    assert _encryption_warnings(_PACKAGE, set(), False) == []


# Stage: cover selection.
def test_contradictory_current_covers_select_nothing() -> None:
    manifest = _Manifest(ids={"a": "d" * 64, "b": "e" * 64}, cover_ids=["a", "b"])
    selected, node, warnings = _selected_cover(manifest, ())
    assert (selected, node) == (None, None)
    assert warnings == []


def test_legacy_cover_is_only_consulted_without_a_current_one() -> None:
    root = _parse(_package(metadata='<meta name="cover" content="img"/>'))
    frame = _package_frame(_request(), root)
    item = _parse(b'<item id="img"/>')
    manifest = _Manifest(
        ids={"img": "d" * 64},
        nodes={"img": item},
        media_types={"img": "image/png"},
        local_ids={"img"},
    )
    observations, declarations, warnings = _cover_observations(
        _request(), _PACKAGE, frame, manifest
    )
    assert [item.field for item in observations] == [EpubPackageField.COVER]
    assert [declaration.kind for declaration in declarations] == [EpubPackageDeclarationKind.COVER]
    assert warnings == []


def test_legacy_cover_naming_a_non_image_is_reported_and_dropped() -> None:
    root = _parse(_package(metadata='<meta name="cover" content="doc"/>'))
    frame = _package_frame(_request(), root)
    manifest = _Manifest(
        ids={"doc": "d" * 64},
        nodes={"doc": _parse(b'<item id="doc"/>')},
        media_types={"doc": "application/xhtml+xml"},
        local_ids={"doc"},
    )
    observations, declarations, warnings = _cover_observations(
        _request(), _PACKAGE, frame, manifest
    )
    assert (observations, declarations) == ([], [])
    assert _codes(warnings) == ["invalid_cover_declaration"]


# Stage: consistency warnings and final bounds accounting.
def _observations(values: list[str]) -> list[EpubPackageObservation]:
    root = _parse(
        _package(
            metadata="".join(f"<dc:title>{value}</dc:title>" for value in values)
        )
    )
    frame = _package_frame(_request(), root)
    registry, _warnings = _identifier_registry(_request(), _PACKAGE, root)
    refinements, _refinement_warnings = _metadata_refinements(frame, registry)
    observations, _observation_warnings = _metadata_field_observations(
        _request(), _PACKAGE, frame, registry, {}, refinements
    )
    return observations


def test_contradiction_is_reported_never_resolved() -> None:
    observations = _observations(["One", "Two"])
    assert len(observations) == 2
    assert _codes(_consistency_warnings(observations)) == ["ambiguous_title"]


def test_agreeing_values_are_not_a_contradiction() -> None:
    assert _consistency_warnings(_observations(["One", "One"])) == []


def test_observation_bound_rejects_rather_than_truncating() -> None:
    observations = _observations(["One"])
    limits = EpubPackageLimits()
    request = _request(limits)
    with pytest.raises(_Rejected) as raised:
        _bounded_report(
            request,
            observations * (limits.MAX_VALUES_PER_FIELD + 1),
            [],
            [],
            EpubPackageUsage(),
        )
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT


def test_repeated_warnings_collapse_and_a_flood_is_announced() -> None:
    request = _request()
    report = _bounded_report(
        request, [], [], [EpubPackageWarning("ambiguous_title")] * 5, EpubPackageUsage()
    )
    assert [warning.code for warning in report.warnings] == ["ambiguous_title"]
    flood = [
        EpubPackageWarning("ambiguous_title", observation.citation)
        for observation in _observations([f"T{index}" for index in range(40)])
    ]
    report = _bounded_report(request, [], [], flood, EpubPackageUsage())
    assert [warning.code for warning in report.warnings] == ["ambiguous_title", "warning_occurrences_aggregated"]


# Stages driven directly, with no parent stage between test and contract.
#
#     `_dublin_core_value`, `_calibre_series_observations` and `_manifest_item`
#     are the three largest stages whose contracts were otherwise only reachable
#     through a parent, so their rejection behaviour is proven here at the stage
#     boundary that owns it.
def test_dublin_core_value_classifies_a_bad_date_at_its_own_boundary() -> None:
    node = _parse(
        b'<date xmlns="http://purl.org/dc/elements/1.1/" '
        b'xmlns:opf="http://www.idpf.org/2007/opf" '
        b'opf:event="copyright">1999-13-45</date>'
    )
    value, warnings = _dublin_core_value(
        _request(), _PACKAGE, node, "date", None, {}, {}, _no_refinements()
    )
    assert value.target_field is EpubPackageField.COPYRIGHT_DATE
    assert value.applicability is EpubObservationApplicability.INAPPLICABLE
    assert value.preserve_only
    assert value.normalized == "1999-13-45"
    assert _codes(warnings) == ["invalid_date"]


def test_dublin_core_value_rejects_an_unsupported_date_event() -> None:
    node = _parse(
        b'<date xmlns="http://purl.org/dc/elements/1.1/" '
        b'xmlns:opf="http://www.idpf.org/2007/opf" '
        b'opf:event="vendor">2001-01-01</date>'
    )
    value, warnings = _dublin_core_value(
        _request(), _PACKAGE, node, "date", None, {}, {}, _no_refinements()
    )
    assert value.applicability is EpubObservationApplicability.INAPPLICABLE
    assert _codes(warnings) == ["invalid_date"]


def test_dublin_core_value_registers_only_a_resolvable_identifier() -> None:
    node = _parse(
        b'<identifier xmlns="http://purl.org/dc/elements/1.1/" id="pub">'
        b"urn:uuid:12345678-1234-1234-8234-123456789abc</identifier>"
    )
    registered: dict[str, _Node] = {}
    value, warnings = _dublin_core_value(
        _request(), _PACKAGE, node, "identifier", "pub", {"pub": node}, registered, _no_refinements()
    )
    assert registered == {"pub": node}
    assert value.inferred_scheme == "UUID"
    assert warnings == []
    withdrawn: dict[str, _Node] = {}
    _dublin_core_value(
        _request(), _PACKAGE, node, "identifier", "pub", {}, withdrawn, _no_refinements()
    )
    assert withdrawn == {}


def test_calibre_series_projects_only_paired_values() -> None:
    series = _Node(f"{{{_OPF}}}meta", {"name": "calibre:series"}, ordinal=3)
    first = _Node(f"{{{_OPF}}}meta", {"name": "calibre:series_index"}, ordinal=4)
    second = _Node(f"{{{_OPF}}}meta", {"name": "calibre:series_index"}, ordinal=5)
    refinements = _Refinements(
        False,
        False,
        {
            "calibre:series": [("Saga", series)],
            "calibre:series_index": [("2", first), ("3", second)],
        },
        {},
        {},
    )
    frame = _package_frame(_request(), _parse(_package()))
    observations, warnings = _calibre_series_observations(
        _request(), _PACKAGE, frame, refinements
    )
    assert [item.field for item in observations] == [EpubPackageField.COLLECTION, EpubPackageField.COLLECTION_POSITION]
    assert observations[1].citation.source_ordinal == 4
    assert _codes(warnings) == ["incomplete_calibre_series"]


def test_calibre_series_defers_to_the_standard_vocabulary() -> None:
    series = _Node(f"{{{_OPF}}}meta", {"name": "calibre:series"}, ordinal=3)
    position = _Node(f"{{{_OPF}}}meta", {"name": "calibre:series_index"}, ordinal=4)
    refinements = _Refinements(
        True,
        True,
        {
            "calibre:series": [("Saga", series)],
            "calibre:series_index": [("2", position)],
        },
        {},
        {},
    )
    frame = _package_frame(_request(), _parse(_package()))
    observations, warnings = _calibre_series_observations(
        _request(), _PACKAGE, frame, refinements
    )
    assert [item.normalized_value for item in observations] == [None, None]
    assert warnings == []


def test_manifest_item_rejects_a_traversal_at_its_own_boundary() -> None:
    for href in ("../../etc/passwd", "..%2f..%2fetc", "/absolute"):
        item = _item(f'id="a" href="{href}" media-type="application/xhtml+xml"')
        with pytest.raises(_Rejected) as raised:
            _manifest_item(
                _request(), _PACKAGE, _entries("OEBPS/a.xhtml"), item, {"a": item}, _Manifest()
            )
        assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_manifest_item_rejects_a_foreign_element_at_its_own_boundary() -> None:
    node = _parse(f'<foreign xmlns="{_OPF}" id="a"/>'.encode())
    with pytest.raises(_Rejected) as raised:
        _manifest_item(
            _request(), _PACKAGE, _entries("OEBPS/a.xhtml"), node, {"a": node}, _Manifest()
        )
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED


def test_manifest_item_accumulates_only_accepted_state() -> None:
    state = _Manifest()
    accepted = _item(
        'id="a" href="a.xhtml" media-type="application/xhtml+xml" properties="nav"'
    )
    declarations, warnings = _manifest_item(
        _request(), _PACKAGE, _entries("OEBPS/a.xhtml"), accepted, {"a": accepted}, state
    )
    assert warnings == []
    assert [declaration.kind for declaration in declarations] == [
            EpubPackageDeclarationKind.MANIFEST_ITEM,
            EpubPackageDeclarationKind.NAVIGATION,
        ]
    assert (set(state.ids), state.nav_count) == ({"a"}, 1)
    withdrawn = _item('id="b" href="a.xhtml" media-type="application/xhtml+xml"')
    declarations, warnings = _manifest_item(
        _request(), _PACKAGE, _entries("OEBPS/a.xhtml"), withdrawn, {}, state
    )
    assert declarations == []
    assert _codes(warnings) == ["invalid_manifest_item"]
    assert set(state.ids) == {"a"}


def test_manifest_item_reports_a_repeated_resource_without_dropping_it() -> None:
    state = _Manifest()
    entries = _entries("OEBPS/a.xhtml")
    first = _item('id="a" href="a.xhtml" media-type="application/xhtml+xml"')
    _manifest_item(_request(), _PACKAGE, entries, first, {"a": first}, state)
    second = _item('id="b" href="a.xhtml" media-type="application/xhtml+xml"')
    declarations, warnings = _manifest_item(
        _request(), _PACKAGE, entries, second, {"b": second}, state
    )
    assert len(declarations) == 1
    assert _codes(warnings) == ["duplicate_manifest_resource"]
    assert set(state.ids) == {"a", "b"}


# Free text almost never produces a traversal or a well-formed prefix mapping, so
# both strategies below are built from the tokens an attacker would actually use.
_SEGMENTS = st.sampled_from(
    ["..", ".", "%2e%2e", "%2E%2e", "a", "OEBPS", "", "%2f", "%5c", "~", "a b", "\u202e"]
)
_REFERENCES = st.one_of(
    st.lists(_SEGMENTS, max_size=6).map("/".join),
    st.lists(_SEGMENTS, max_size=6).map("\\".join),
    st.text(max_size=80),
)
_PREFIX_TOKENS = st.sampled_from(
    ["a:", "dcterms:", "1bad:", "a", ":", "", "https://example.invalid/v", "not-an-iri", "a b"]
)
_PREFIX_DECLARATIONS = st.one_of(
    st.lists(_PREFIX_TOKENS, max_size=6).map(" ".join),
    st.text(max_size=60),
)

# Inputs that pin each branch of each property. `@example` runs on every seed,
# before any generated input, so the branch is reached by construction. The
# strategies above widen the search; these decide whether the guard can pass.
_TRAVERSAL_REFERENCE = "../../etc/passwd"
_CONTAINED_REFERENCE = "a.xhtml"
_CONTAINED_MEMBER = "OEBPS/a.xhtml"
_MALFORMED_PREFIX = "dcterms:"
_VALID_PREFIX = "dcterms: http://purl.org/dc/terms/"


# Properties over hostile stage inputs, each with a deterministic guard.
#
#     Coverage of both branches is pinned by `@example`, never inferred from what
#     a seed happened to generate: asserting that a random strategy explored a
#     branch is a probabilistic assertion that passes on most seeds and fails on
#     the rest. Each guard names the exact forced input it expects to see
#     recorded, so it fails when the recording wiring breaks, when the explicit
#     examples stop running, or when the behaviour under test regresses -- and
#     for no other reason.
#
#     Evidence is local to the single test that generates it. Class or module
#     state would accumulate across repeated executions in one interpreter,
#     letting a narrowed strategy be validated by an earlier run's evidence.
def test_the_reference_guard_has_both_outcomes() -> None:
    entries = _entries(_CONTAINED_MEMBER)
    with pytest.raises(_Rejected) as raised:
        _declared_resource_identity(_PACKAGE, _TRAVERSAL_REFERENCE, entries)
    assert raised.value.outcome is EpubInspectionOutcome.MALFORMED
    _identity, resolved = _declared_resource_identity(
        _PACKAGE, _CONTAINED_REFERENCE, entries
    )
    assert resolved == _CONTAINED_MEMBER


def test_declared_reference_is_rejected_or_stays_inside_the_container() -> None:
    entries = _entries(_CONTAINED_MEMBER)
    rejected: set[str] = set()
    accepted: set[str] = set()

    @settings(max_examples=400, deadline=None)
    @given(_REFERENCES)
    @example(_TRAVERSAL_REFERENCE)
    @example(_CONTAINED_REFERENCE)
    def check(reference: str) -> None:
        try:
            identity, resolved = _declared_resource_identity(
                _PACKAGE, reference, entries
            )
        except _Rejected:
            rejected.add(reference)
            return
        except (_InvalidReferenceSyntax, EvidenceValueError):
            # `EvidenceValueError` escapes this helper for a reference whose
            # resolved member name fails the shared evidence-text bounds; the
            # extractor turns it into `MALFORMED` at its own boundary.
            # Recorded as a finding, not changed here: this unit is
            # behaviour-free.
            return
        assert re.search(r"^[0-9a-f]{64}$", identity), r"^[0-9a-f]{64}$"
        if resolved is not None:
            accepted.add(resolved)
            assert not resolved.startswith("/")
            assert not resolved.startswith("../")
            assert "\\" not in resolved
            assert "/../" not in resolved
            assert len(resolved.encode("utf-8")) <= EpubPackageLimits.MEMBER_NAME_UTF8_BYTES_HARD_CAP

    check()
    assert _TRAVERSAL_REFERENCE in rejected
    assert _CONTAINED_MEMBER in accepted


def test_prefix_vocabulary_is_rejected_or_fully_validated() -> None:
    request = _request()
    rejected: set[str] = set()
    accepted: list[dict[str, str]] = []

    @settings(max_examples=400, deadline=None)
    @given(_PREFIX_DECLARATIONS)
    @example(_MALFORMED_PREFIX)
    @example(_VALID_PREFIX)
    def check(declaration: str) -> None:
        node = _Node("{" + _OPF + "}package", {"prefix": declaration})
        try:
            prefixes = _prefix_vocabulary(request, node)
        except _Rejected as raised:
            assert raised.outcome is EpubInspectionOutcome.MALFORMED
            rejected.add(declaration)
            return
        accepted.append(prefixes)
        assert len(prefixes) <= request.limits.prefix_mappings
        for name, iri in prefixes.items():
            assert re.search(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$", name), r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$"
            assert " " not in iri

    check()
    assert _MALFORMED_PREFIX in rejected
    assert {"dcterms": "http://purl.org/dc/terms/"} in accepted
