import hashlib
import json
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.evidence_extractors import epub_cover
from mechanical_librarian.adapters.evidence_extractors.epub_cover import (
    read_declared_cover,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceState
from tests.support.epub_packages import EPUB2_PACKAGE, EPUB3_PACKAGE, epub_bytes


class _TimeoutProcess:
    pid = 999_999
    stdin = None
    stdout = None
    stderr = None
    returncode: int | None = None

    def communicate(
        self,
        _context: bytes,
        *,
        timeout: float,
    ) -> tuple[bytes, bytes]:
        raise subprocess.TimeoutExpired("epub-child", timeout)


def test_reads_only_declared_cover_member() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "book.epub"
        path.write_bytes(epub_bytes())
        descriptor = os.open(path, os.O_RDONLY)
        try:
            result = read_declared_cover(
                descriptor, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        finally:
            os.close(descriptor)
        assert result.state is None
        assert b"TRAP: cover bytes must remain opaque" == result.data


def test_absent_cover_is_not_heuristically_selected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "book.epub"
        package = EPUB3_PACKAGE.replace(b' properties="cover-image"', b"")
        path.write_bytes(epub_bytes(package=package))
        descriptor = os.open(path, os.O_RDONLY)
        try:
            result = read_declared_cover(
                descriptor, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        finally:
            os.close(descriptor)
        assert CoverSourceState.ABSENT == result.state


def test_exact_eight_mebibyte_cover_uses_bounded_binary_pipe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "book.epub"
        cover = b"x" * (8 * 1024 * 1024)
        path.write_bytes(
            epub_bytes(
                include_default_content=False,
                extra_entries=(
                    ("EPUB/nav.xhtml", b"nav", zipfile.ZIP_DEFLATED),
                    ("EPUB/chapter.xhtml", b"chapter", zipfile.ZIP_DEFLATED),
                    ("EPUB/cover.jpg", cover, zipfile.ZIP_STORED),
                ),
            )
        )
        descriptor = os.open(path, os.O_RDONLY)
        try:
            result = read_declared_cover(
                descriptor, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        finally:
            os.close(descriptor)
        assert result.state is None
        assert cover == result.data


def test_repeated_child_timeouts_close_every_parent_pipe() -> None:
    before = len(os.listdir("/dev/fd"))
    process = _TimeoutProcess()
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=process),
        patch.object(epub_cover, "_stop") as stop,
    ):
        for _ in range(20):
            result = read_declared_cover(-1, "0" * 64)
            assert CoverSourceState.TIMEOUT == result.state
    assert 20 == stop.call_count
    assert before == len(os.listdir("/dev/fd"))


@pytest.mark.parametrize(
    "name,data,expected",
    (
        ("EPUB/", b"", None),
        ("../", b"", CoverSourceState.INVALID_DECLARATION),
        ("../cover.jpg", b"", CoverSourceState.INVALID_DECLARATION),
        ("EPUB/", b"not empty", CoverSourceState.INVALID_DECLARATION),
        ("EPUB/cover.jpg", b"duplicate", CoverSourceState.INVALID_DECLARATION),
    ),
)
def test_directory_entries_preserve_validated_single_file_selection(
    tmp_path: Path, name: str, data: bytes, expected: CoverSourceState | None
) -> None:
    path = tmp_path / "book.epub"
    path.write_bytes(epub_bytes(extra_entries=((name, data, zipfile.ZIP_STORED),)))
    with path.open("rb") as source:
        result = read_declared_cover(
            source.fileno(), hashlib.file_digest(source, "sha256").hexdigest()
        )
    assert result.state is expected
    if expected is None:
        assert result.data == b"TRAP: cover bytes must remain opaque"


def test_child_cancellation_closes_parent_pipes_without_reclassification() -> None:
    before = len(os.listdir("/dev/fd"))
    interruption = KeyboardInterrupt()
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=_TimeoutProcess()),
        patch.object(_TimeoutProcess, "communicate", side_effect=interruption),
        patch.object(epub_cover, "_stop") as stop,
        pytest.raises(KeyboardInterrupt) as raised,
    ):
        read_declared_cover(-1, "0" * 64)
    assert raised.value is interruption
    stop.assert_called_once()
    assert before == len(os.listdir("/dev/fd"))


@pytest.mark.parametrize(
    "package,expected",
    (
        (
            EPUB3_PACKAGE.replace(
                b'media-type="image/jpeg"', b'media-type="image/svg+xml"'
            ),
            CoverSourceState.UNSUPPORTED_FORMAT,
        ),
        (
            EPUB3_PACKAGE.replace(
                b"</manifest>",
                b'<item id="other-cover" href="other.jpg" media-type="image/jpeg" properties="cover-image"/></manifest>',
            ),
            CoverSourceState.ABSENT,
        ),
    ),
)
def test_unsupported_and_ambiguous_cover_declarations_are_not_decoded(
    tmp_path, package, expected
):
    path = tmp_path / "book.epub"
    path.write_bytes(
        epub_bytes(
            package=package,
            extra_entries=(
                ("EPUB/", b"", zipfile.ZIP_STORED),
                ("EPUB/other.jpg", b"other", zipfile.ZIP_STORED),
            ),
        )
    )
    with path.open("rb") as source:
        result = read_declared_cover(
            source.fileno(), hashlib.file_digest(source, "sha256").hexdigest()
        )
    assert result.state is expected
    assert result.data is None


def test_malformed_child_protocol_remains_invalid_declaration():
    process = _TimeoutProcess()
    process.returncode = 0
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=process),
        patch.object(
            _TimeoutProcess, "communicate", return_value=(b'{"state":"made-up"}', b"")
        ),
        patch.object(epub_cover, "_stop") as stop,
    ):
        result = read_declared_cover(-1, "0" * 64)
    assert result.state is CoverSourceState.INVALID_DECLARATION
    stop.assert_called_once()


_NO_COVER = EPUB3_PACKAGE.replace(b' properties="cover-image"', b"")
_WRAP = b'<item id="wrap" href="wrap.xhtml" media-type="application/xhtml+xml"/>'
_WRAP_COVER = (
    b'<item id="wrap" href="wrap.xhtml" media-type="application/xhtml+xml" '
    b'properties="cover-image"/>'
)
_WRAP2_COVER = (
    b'<item id="wrap2" href="wrap2.xhtml" media-type="application/xhtml+xml" '
    b'properties="cover-image"/>'
)
_OTHER = b'<item id="other" href="other.jpg" media-type="image/jpeg"/>'
_PDF = b'<item id="pdf" href="cover.pdf" media-type="application/pdf"/>'
_META = b'<meta name="cover" content="wrap"/>'
_TRAP_TEXT = ("wrap.", "cover.", "other.jpg", "xhtml", "EPUB/", "https:", "data:", "#")


def _cover_package(
    items: bytes = b"", metadata: bytes = b"", guide: bytes = b""
) -> bytes:
    package = _NO_COVER.replace(b"</manifest>", items + b"</manifest>").replace(
        b"</metadata>", metadata + b"</metadata>"
    )
    if guide:
        package = package.replace(b"</spine>", b"</spine><guide>" + guide + b"</guide>")
    return package


def _guide_cover(href: bytes) -> bytes:
    return b'<reference type="cover" href="' + href + b'"/>'


def _wrapper(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><body>'
        f"{body}</body></html>"
    ).encode()


_COVER_IMG = _wrapper('<img src="cover.jpg" alt=""/>')


def _oversized_wrapper() -> bytes:
    # Incompressible padding, so the archive's compression-ratio bound passes
    # and the wrapper's own expanded-byte bound is what refuses.
    padding = b"".join(
        hashlib.sha256(str(index).encode()).hexdigest().encode()
        for index in range(1_048_576 // 64 + 1)
    )
    return _COVER_IMG.replace(b"</body>", b"<!--" + padding + b"--></body>")


def _read_cover(
    tmp_path: Path,
    package: bytes,
    entries: tuple[tuple[str, bytes], ...] = (),
    encryption: bytes | None = None,
) -> epub_cover.DeclaredEpubCover:
    path = tmp_path / "book.epub"
    path.write_bytes(
        epub_bytes(
            package=package,
            encryption=encryption,
            extra_entries=(
                ("EPUB/other.jpg", b"TRAP: other bytes", zipfile.ZIP_STORED),
                ("EPUB/unlisted.jpg", b"TRAP: unlisted bytes", zipfile.ZIP_STORED),
                ("EPUB/cover.pdf", b"TRAP: pdf bytes", zipfile.ZIP_STORED),
                *((name, data, zipfile.ZIP_DEFLATED) for name, data in entries),
            ),
        )
    )
    with path.open("rb") as source:
        return read_declared_cover(
            source.fileno(), hashlib.file_digest(source, "sha256").hexdigest()
        )


_META_FALLBACK = "epub2_meta_cover_item"
_GUIDE_FALLBACK = "guide_cover_reference"
_WRAPPER_FALLBACK = "cover_image_wrapper"


@pytest.mark.parametrize(
    "package,entries,fallbacks,refusal",
    (
        (
            _cover_package(_WRAP, _META),
            (("EPUB/wrap.xhtml", _COVER_IMG),),
            [_META_FALLBACK],
            None,
        ),
        (_cover_package(guide=_guide_cover(b"cover.jpg")), (), [_GUIDE_FALLBACK], None),
        (
            _cover_package(_WRAP, guide=_guide_cover(b"wrap.xhtml#start")),
            (("EPUB/wrap.xhtml", _COVER_IMG),),
            [_GUIDE_FALLBACK],
            None,
        ),
        (
            _cover_package(_WRAP_COVER),
            (("EPUB/wrap.xhtml", _COVER_IMG),),
            [_WRAPPER_FALLBACK],
            None,
        ),
        (
            _cover_package(_WRAP_COVER, guide=_guide_cover(b"cover.jpg")),
            (("EPUB/wrap.xhtml", _COVER_IMG),),
            [_GUIDE_FALLBACK, _WRAPPER_FALLBACK],
            None,
        ),
        (
            _cover_package(_WRAP_COVER, _META, _guide_cover(b"wrap.xhtml")),
            (("EPUB/wrap.xhtml", _COVER_IMG),),
            [_META_FALLBACK, _GUIDE_FALLBACK, _WRAPPER_FALLBACK],
            None,
        ),
        (
            _cover_package(
                metadata=b'<meta name="cover" content="missing"/>',
                guide=_guide_cover(b"cover.jpg"),
            ),
            (),
            [_GUIDE_FALLBACK],
            None,
        ),
        (
            _cover_package(_WRAP_COVER),
            (
                (
                    "EPUB/wrap.xhtml",
                    _wrapper(
                        '<img src="cover.jpg"/><img src="./cov%65r.jpg"/>'
                        '<svg xmlns="http://www.w3.org/2000/svg">'
                        '<image xlink:href="cover.jpg"/></svg>'
                    ),
                ),
            ),
            [_WRAPPER_FALLBACK],
            None,
        ),
        (
            _cover_package(
                b'<item id="wrap" href="wrap.html" media-type="text/html" '
                b'properties="cover-image"/>'
            ),
            (
                (
                    "EPUB/wrap.html",
                    b"<html><body><svg><image xlink:href='cover.jpg'></svg>",
                ),
            ),
            [_WRAPPER_FALLBACK],
            None,
        ),
        (
            _cover_package(
                metadata=b'<meta name="cover" content="missing"/>',
                guide=_guide_cover(b"toc.ncx"),
            ),
            (),
            [],
            "cover_recovery_unresolved",
        ),
        (
            _cover_package(_PDF, guide=_guide_cover(b"cover.pdf")),
            (),
            [_GUIDE_FALLBACK],
            "cover_recovery_unsupported_target",
        ),
        (
            _cover_package(_WRAP + _WRAP2_COVER, _META),
            (("EPUB/wrap.xhtml", _COVER_IMG), ("EPUB/wrap2.xhtml", _COVER_IMG)),
            [_META_FALLBACK, _WRAPPER_FALLBACK],
            "cover_recovery_multiple_wrappers",
        ),
        (
            _cover_package(_WRAP_COVER + _OTHER, guide=_guide_cover(b"other.jpg")),
            (("EPUB/wrap.xhtml", _COVER_IMG),),
            [_GUIDE_FALLBACK, _WRAPPER_FALLBACK],
            "contradictory_cover_declaration",
        ),
        (
            _cover_package(_WRAP_COVER),
            (("EPUB/wrap.xhtml", _oversized_wrapper()),),
            [_WRAPPER_FALLBACK],
            "cover_wrapper_resource_limit",
        ),
        (
            _cover_package(_WRAP_COVER),
            (
                (
                    "EPUB/wrap.xhtml",
                    b'<!DOCTYPE html [<!ENTITY c "cover.jpg">]>' + _COVER_IMG,
                ),
            ),
            [_WRAPPER_FALLBACK],
            "cover_wrapper_unreadable",
        ),
    ),
    ids=(
        "meta_names_wrapper",
        "guide_names_image",
        "guide_names_wrapper",
        "cover_image_on_wrapper",
        "guide_and_wrapper_agree",
        "all_three_agree",
        "missing_meta_beside_resolving_guide",
        "same_image_named_three_ways",
        "recovered_html_wrapper",
        "nothing_resolves",
        "unsupported_target",
        "two_wrappers",
        "fallbacks_disagree",
        "wrapper_over_byte_bound",
        "wrapper_doctype_subset",
    ),
)
def test_malformed_cover_declarations_yield_shadow_recovery_only(
    tmp_path: Path,
    package: bytes,
    entries: tuple[tuple[str, bytes], ...],
    fallbacks: list[str],
    refusal: str | None,
) -> None:
    result = _read_cover(tmp_path, package, entries)
    assert result.state is CoverSourceState.ABSENT
    assert result.data is None and result.media_type is None
    assert result.recovery is not None
    data = result.recovery.to_data()
    assert data == {"authority": "shadow", "fallbacks": fallbacks, "refusal": refusal}
    assert not any(text in json.dumps(data) for text in _TRAP_TEXT)


@pytest.mark.parametrize(
    "body,refusal",
    (
        ('<img src="https://example.invalid/cover.jpg"/>', "external_reference"),
        ('<img src="data:image/png;base64,AAAA"/>', "external_reference"),
        (
            '<svg xmlns="http://www.w3.org/2000/svg"><image href="#c"/></svg>',
            "external_reference",
        ),
        ('<img src="../../cover.jpg"/>', "outside_package"),
        ('<img src="%2e%2e/%2e%2e/cover.jpg"/>', "outside_package"),
        ('<img src="/EPUB/cover.jpg"/>', "outside_package"),
        ('<img src="images/"/>', "outside_package"),
        ('<img src="cover.jpg?size=1"/>', "outside_package"),
        ('<img src="wrap.xhtml"/>', "nested"),
        ('<img src="chapter.xhtml"/>', "nested"),
        ('<img src="unlisted.jpg"/>', "not_manifest_image"),
        ('<img src="cover.pdf"/>', "not_manifest_image"),
        ('<img src="cover.jpg"/><img hidden="" src="other.jpg"/>', "multiple_images"),
        ("<p>No image</p>", "no_image"),
        (
            '<img src="https://example.invalid/x"/><img src="../../x"/>',
            "external_reference",
        ),
        ('<img src="other.jpg" src="cover.jpg"/>', "unreadable"),
        (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<image href="https://example.invalid/x.png" href="cover.jpg"/></svg>',
            "unreadable",
        ),
    ),
    ids=(
        "https_scheme",
        "data_uri",
        "fragment_only",
        "dot_dot_traversal",
        "percent_encoded_traversal",
        "absolute_path",
        "directory",
        "query",
        "self_reference",
        "wrapper_to_wrapper",
        "not_in_manifest",
        "not_an_image",
        "two_images_one_hidden",
        "no_image",
        "first_failure_wins",
        "duplicate_src",
        "duplicate_svg_href",
    ),
)
def test_cover_wrapper_references_refuse_with_closed_codes(
    tmp_path: Path, body: str, refusal: str
) -> None:
    result = _read_cover(
        tmp_path,
        _cover_package(_WRAP_COVER + _OTHER + _PDF),
        (("EPUB/wrap.xhtml", _wrapper(body)),),
    )
    assert result.state is CoverSourceState.ABSENT
    assert result.data is None
    assert result.recovery is not None
    assert result.recovery.to_data() == {
        "authority": "shadow",
        "fallbacks": [_WRAPPER_FALLBACK],
        "refusal": f"cover_wrapper_{refusal}",
    }


@pytest.mark.parametrize(
    "package,state",
    (
        (
            EPUB3_PACKAGE.replace(b"</manifest>", _WRAP + b"</manifest>")
            .replace(b"</metadata>", _META + b"</metadata>")
            .replace(
                b"</spine>",
                b"</spine><guide>" + _guide_cover(b"wrap.xhtml") + b"</guide>",
            ),
            None,
        ),
        (
            EPUB2_PACKAGE.replace(b"</manifest>", _WRAP + b"</manifest>").replace(
                b'type="text" title="Start" href="chapter.xhtml#start"',
                b'type="cover" href="wrap.xhtml"',
            ),
            None,
        ),
        (
            _cover_package(guide=_guide_cover(b"../../cover.jpg")),
            CoverSourceState.INVALID_DECLARATION,
        ),
        (_NO_COVER, CoverSourceState.ABSENT),
    ),
    ids=(
        "valid_current_wins",
        "valid_legacy_wins",
        "guide_outside_package",
        "no_declaration",
    ),
)
def test_fallbacks_never_override_a_valid_or_rejected_declaration(
    tmp_path: Path, package: bytes, state: CoverSourceState | None
) -> None:
    result = _read_cover(
        tmp_path, package, (("EPUB/wrap.xhtml", _wrapper('<img src="other.jpg"/>')),)
    )
    assert result.state is state
    assert result.recovery is None
    if state is None:
        assert result.data == b"TRAP: cover bytes must remain opaque"


@pytest.mark.parametrize(
    "payload",
    (
        b'{"state":"invalid_declaration","recovery":{"authority":"shadow","fallbacks":[],"refusal":"cover_recovery_unresolved"}}',
        b'{"state":"absent","recovery":null}',
        b'{"state":"absent","recovery":{"authority":"verified","fallbacks":[],"refusal":"cover_recovery_unresolved"}}',
        b'{"state":"absent","recovery":{"authority":"shadow","fallbacks":["first_image_in_spine"],"refusal":null}}',
        b'{"state":"absent","recovery":{"authority":"shadow","fallbacks":["guide_cover_reference"],"refusal":"made-up"}}',
        b'{"state":"absent","recovery":{"authority":"shadow","fallbacks":[],"refusal":null}}',
        b'{"state":"absent","recovery":{"authority":"shadow","fallbacks":["guide_cover_reference"],"refusal":null,"href":"cover.jpg"}}',
        b'{"state":"absent","identity":"cover.jpg"}',
    ),
    ids=(
        "recovery_beside_other_state",
        "null_recovery",
        "wrong_authority",
        "unknown_fallback",
        "unknown_refusal",
        "recovered_without_fallback",
        "extra_recovery_key",
        "extra_absent_key",
    ),
)
def test_malformed_recovery_protocol_is_an_invalid_declaration(payload: bytes) -> None:
    process = _TimeoutProcess()
    process.returncode = 0
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=process),
        patch.object(_TimeoutProcess, "communicate", return_value=(payload, b"")),
        patch.object(epub_cover, "_stop"),
    ):
        result = read_declared_cover(-1, "0" * 64)
    assert result.state is CoverSourceState.INVALID_DECLARATION
    assert result.recovery is None


def test_duplicate_items_under_repeated_guide_covers_refuse_without_a_choice(
    tmp_path: Path,
) -> None:
    duplicates = b"".join(
        b'<item id="dup%d" href="cover.jpg" media-type="image/jpeg"/>' % index
        for index in range(3_000)
    )
    result = _read_cover(
        tmp_path,
        _cover_package(duplicates, guide=_guide_cover(b"cover.jpg") * 3),
    )
    assert result.state is CoverSourceState.ABSENT
    assert result.data is None
    assert result.recovery is not None
    assert result.recovery.to_data() == {
        "authority": "shadow",
        "fallbacks": [_GUIDE_FALLBACK],
        "refusal": "cover_recovery_unsupported_target",
    }


def test_many_members_and_references_resolve_within_a_bounded_time(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    result = _read_cover(
        tmp_path,
        _cover_package(_WRAP_COVER),
        (
            # Each element carries an incompressible label so the archive's
            # compression-ratio bound admits ten thousand identical references.
            (
                "EPUB/wrap.xhtml",
                _wrapper(
                    "".join(
                        f'<img alt="{hashlib.sha256(str(index).encode()).hexdigest()}"'
                        ' src="cover.jpg"/>'
                        for index in range(10_000)
                    )
                ),
            ),
            *((f"EPUB/filler/{index}.bin", b"") for index in range(2_000)),
        ),
    )
    assert time.monotonic() - started < 10.0
    assert result.state is CoverSourceState.ABSENT
    assert result.recovery is not None
    assert result.recovery.to_data() == {
        "authority": "shadow",
        "fallbacks": [_WRAPPER_FALLBACK],
        "refusal": None,
    }


def test_many_members_keep_the_declared_cover_selection(tmp_path: Path) -> None:
    result = _read_cover(
        tmp_path,
        EPUB3_PACKAGE,
        tuple((f"EPUB/filler/{index}.bin", b"") for index in range(2_000)),
    )
    assert result.state is None
    assert result.media_type == "image/jpeg"
    assert result.data == b"TRAP: cover bytes must remain opaque"
    assert result.recovery is None


def _encrypting(member: str) -> bytes:
    return (
        b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b'<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData>'
        b'<CipherReference URI="' + member.encode() + b'"/></CipherData>'
        b"</EncryptedData></encryption>"
    )


@pytest.mark.parametrize(
    "package,encrypted,fallbacks,refusal",
    (
        (
            _cover_package(_WRAP_COVER),
            "EPUB/wrap.xhtml",
            [_WRAPPER_FALLBACK],
            "cover_wrapper_unreadable",
        ),
        (
            _cover_package(guide=_guide_cover(b"cover.jpg")),
            "EPUB/cover.jpg",
            [_GUIDE_FALLBACK],
            "cover_recovery_unsupported_target",
        ),
        (
            _cover_package(_WRAP_COVER),
            "EPUB/cover.jpg",
            [_WRAPPER_FALLBACK],
            "cover_recovery_unsupported_target",
        ),
        (
            _cover_package(_WRAP_COVER, guide=_guide_cover(b"cover.jpg")),
            "EPUB/other.jpg",
            [_GUIDE_FALLBACK, _WRAPPER_FALLBACK],
            None,
        ),
    ),
    ids=(
        "encrypted_wrapper",
        "encrypted_direct_image",
        "encrypted_image_behind_wrapper",
        "other_resource_encrypted",
    ),
)
def test_encrypted_cover_fallback_resources_are_never_recovered(
    tmp_path: Path,
    package: bytes,
    encrypted: str,
    fallbacks: list[str],
    refusal: str | None,
) -> None:
    result = _read_cover(
        tmp_path,
        package,
        (("EPUB/wrap.xhtml", _COVER_IMG),),
        _encrypting(encrypted),
    )
    assert result.state is CoverSourceState.ABSENT
    assert result.data is None
    assert result.recovery is not None
    assert result.recovery.to_data() == {
        "authority": "shadow",
        "fallbacks": fallbacks,
        "refusal": refusal,
    }
