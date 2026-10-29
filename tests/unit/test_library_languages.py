"""A library's declared languages as a matching prior (#1315, #1305).

The owner may declare, per library, the ordered languages it holds. Matching
reads the list three ways: it orders the Audible marketplaces an audiobook is
searched and looked up in, it holds an audiobook product to the list when the
recording states no language of its own, and the stated-runtime path now runs
the recording-variant check at all. Every title, name and identifier here is
synthetic.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mechanical_librarian.application.batch import batch_fingerprint
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    admitted_audiobook_identity,
    audiobook_discovery_plan,
    qualify_provider_observation,
)
from mechanical_librarian.bootstrap.composition import _audiobook_marketplaces
from mechanical_librarian.domain.audiobook_corroboration import recording_admitted
from mechanical_librarian.domain.field_outcomes import library_languages_valid
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetFacts,
    canonical_json,
    finalize_candidate_comparisons,
    recording_variant_conflict,
)
from mechanical_librarian.infrastructure.configuration import (
    load_configuration_document,
)
from tests.support.audiobook_providers import audio_claim
from tests.support.audiobook_reports import (
    _audio_work_dispatch,
    audio_local_claims,
    audio_target_fields,
    audiobook_candidate,
    manual_audiobook_observation,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

# -- configuration -------------------------------------------------------------


def _library(**changes: object) -> LibraryConfiguration:
    values: dict[str, object] = {
        "name": "audio",
        "connection": "primary",
        "library_id": "library-1",
        "media_kind": "book",
        "mount": None,
        "provider_connections": (),
    }
    values.update(changes)
    return LibraryConfiguration(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "languages",
    ((), ("en",), ("de", "en"), ("fr", "en", "de"), ("zh-Hant", "zh-Hans"), ("eng",)),
)
def test_a_library_declares_an_ordered_language_list(
    languages: tuple[str, ...],
) -> None:
    assert languages == _library(languages=languages).languages


@pytest.mark.parametrize(
    "languages",
    (
        ["en"],
        ("english",),
        ("EN",),
        ("en", "eng"),
        ("en-GB", "en-US"),
        ("und",),
        ("mul",),
        ("zxx",),
        ("xx",),
        ("",),
        (1,),
        tuple(
            f"{code}" for code in ("en", "de", "fr", "it", "es", "ja", "nl", "sv", "da")
        ),
    ),
)
def test_an_invalid_language_list_is_refused(languages: object) -> None:
    assert not library_languages_valid(languages)
    with pytest.raises(ConfigurationError, match="library languages are invalid"):
        _library(languages=languages)


def _document(library: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "schema_version": "config-v9",
            "provider_recovery_generation": 1,
            "connections": [
                {
                    "name": "primary",
                    "adapter": "audiobookshelf",
                    "endpoint": "https://library.example.invalid",
                    "secret_file": "/run/secrets/token",
                }
            ],
            "libraries": [
                {
                    "name": "audio",
                    "connection": "primary",
                    "library_id": "library-1",
                    "mount": None,
                    "provider_connections": [],
                    **library,
                }
            ],
            "mounts": [],
            "providers": [],
        }
    ).encode()


def test_the_file_reads_languages_in_order_and_defaults_to_none() -> None:
    declared = load_configuration_document(_document({"languages": ["de", "en"]}))
    absent = load_configuration_document(_document({}))

    assert ("de", "en") == declared.libraries[0].languages
    assert () == absent.libraries[0].languages


@pytest.mark.parametrize(
    "languages", (["en", "en"], ["Deutsch"], "en", [None], ["en"] * 9)
)
def test_the_file_refuses_an_invalid_language_list(languages: object) -> None:
    with pytest.raises(ConfigurationError):
        load_configuration_document(_document({"languages": languages}))


def test_a_changed_language_list_is_new_report_authority() -> None:
    def configuration(languages: tuple[str, ...]) -> ApplicationConfiguration:
        return ApplicationConfiguration(
            (
                TargetConnectionConfiguration(
                    "primary", "https://library.example.invalid", None
                ),
            ),
            (_library(languages=languages),),
            (),
            (),
        )

    assert batch_fingerprint(configuration(("en",))) != batch_fingerprint(
        configuration(("de",))
    )
    assert batch_fingerprint(configuration(("de", "en"))) != batch_fingerprint(
        configuration(("en", "de"))
    )


# -- marketplace order ---------------------------------------------------------


def _audible(marketplaces: tuple[str, ...]) -> ProviderConnectionConfiguration:
    return ProviderConnectionConfiguration(
        "audible-main",
        True,
        False,
        None,
        "audible_catalog",
        marketplaces=marketplaces,
    )


_ALL = ("us", "uk", "de", "fr", "au", "in", "ca", "jp", "it", "es")


@pytest.mark.parametrize(
    ("languages", "expected"),
    (
        ((), _ALL),
        (("en",), ("us", "uk", "au", "in", "ca", "de", "fr", "jp", "it", "es")),
        (("de",), ("de", "us", "uk", "fr", "au", "in", "ca", "jp", "it", "es")),
        (("de", "en"), ("de", "us", "uk", "au", "in", "ca", "fr", "jp", "it", "es")),
        (("fr", "en"), ("fr", "ca", "us", "uk", "au", "in", "de", "jp", "it", "es")),
        (("ja",), ("jp", "us", "uk", "de", "fr", "au", "in", "ca", "it", "es")),
        # A language no marketplace serves moves nothing.
        (("nl",), _ALL),
    ),
)
def test_declared_languages_order_the_marketplaces(
    languages: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert expected == _audiobook_marketplaces((_audible(_ALL),), languages)


def test_the_order_never_adds_a_marketplace_the_owner_left_out() -> None:
    assert ("us", "uk") == _audiobook_marketplaces((_audible(("us", "uk")),), ("de",))
    # A region narrows nothing: English keeps the owner's own English order.
    assert ("us", "uk") == _audiobook_marketplaces(
        (_audible(("us", "uk")),), ("en-GB",)
    )
    assert ("us",) == _audiobook_marketplaces((), ("de",))


def test_marketplaces_in_one_language_keep_the_owners_order() -> None:
    # The title search asks only the first marketplace, so declaring English
    # must not move a UK owner's search to the US catalogue.
    assert ("uk", "us", "de") == _audiobook_marketplaces(
        (_audible(("de", "uk", "us")),), ("en",)
    )
    assert ("ca", "fr", "uk") == _audiobook_marketplaces(
        (_audible(("uk", "ca", "fr")),), ("fr", "en")
    )


# -- the recording-language prior ---------------------------------------------


def _with_language(
    candidate: ProviderCandidate, language: str, local: tuple[LocalClaim, ...]
) -> ProviderCandidate:
    claim = ProviderClaim.create(
        provider="catalogue",
        candidate=candidate.identity,
        kind=ClaimKind.LANGUAGE,
        value=language,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
        raw_order=len(candidate.claims),
    )
    return finalize_candidate_comparisons(
        replace(candidate, claims=(*candidate.claims, claim)), local, "catalogue"
    )


def _language_claims(language: str | None) -> tuple[LocalClaim, ...]:
    claims = audio_local_claims()
    if language is None:
        return claims
    return (*claims, audio_claim(ClaimKind.LANGUAGE, language, 3))


@pytest.mark.parametrize(
    ("local", "library", "product", "expected"),
    (
        # No declaration and no local language: nothing to compare.
        (None, (), "de", None),
        # One declared language is exactly a language claim.
        (None, ("en",), "de", "recording_language_outside_library"),
        (None, ("en",), "en", None),
        (None, ("en",), "en-GB", None),
        # Several accept any of them, without choosing one.
        (None, ("de", "en"), "en", None),
        (None, ("de", "en"), "de", None),
        (None, ("de", "en"), "fr", "recording_language_outside_library"),
        # A product stating no language, or an undetermined one, passes.
        (None, ("en",), None, None),
        (None, ("en",), "und", None),
        # The recording's own language outranks the library's.
        ("de", ("en",), "de", None),
        ("de", ("en",), "en", "recording_language_conflict"),
        # An undetermined local language states nothing, so the library does.
        ("und", ("en",), "de", "recording_language_outside_library"),
    ),
)
def test_a_product_is_held_to_the_library_languages_without_a_local_one(
    local: str | None,
    library: tuple[str, ...],
    product: str | None,
    expected: str | None,
) -> None:
    claims = _language_claims(local)
    candidate = audiobook_candidate(claims)
    if product is not None:
        candidate = _with_language(candidate, product, claims)

    assert expected == recording_variant_conflict(
        candidate, claims, "Ember Tide Protocol", library
    )


# -- the stated-runtime path (#1305) ------------------------------------------


def _work_observation(
    candidate: ProviderCandidate,
    claims: tuple[LocalClaim, ...],
    library: tuple[str, ...] = (),
) -> ProviderObservation:
    """One Audible WORK lane answer, qualified as preparation qualifies it."""
    return qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (candidate,),
            ProviderQueryKind.WORK,
            "audible_catalog",
            dispatch_lineage=_audio_work_dispatch(
                "Ember Tide Protocol", ("Corin Halloway",)
            ),
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            claims,
            "Ember Tide Protocol",
            ("Corin Halloway",),
            library_languages=library,
        ),
    )


def _report(
    observation: ProviderObservation,
    claims: tuple[LocalClaim, ...],
    library: tuple[str, ...] = (),
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(observation,),
        audio=single_track_audio_facts("b" * 64),
        library_languages=library,
    )


def _marketplace(candidate: ProviderCandidate) -> bool:
    return any(
        value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for value in candidate.evidence
    )


def test_a_matching_recording_is_still_admitted_on_its_runtime() -> None:
    claims = _language_claims("en")
    candidate = _with_language(audiobook_candidate(claims), "en", claims)
    observation = _work_observation(candidate, claims, ("en",))
    report = _report(observation, claims, ("en",))

    assert _marketplace(observation.candidates[0])
    assert admitted_audiobook_identity(report)
    assert recording_admitted(report)


@pytest.mark.parametrize(
    ("local", "library", "title"),
    (
        # The reproduction: a WORK lane admits the title exactly, the runtime
        # is inside the tight band, and the edition's language contradicts
        # the recording's own.
        ("en", (), "Ember Tide Protocol"),
        # The same product where only the library declares a language.
        (None, ("en",), "Ember Tide Protocol"),
        # A format conflict on the same path: the edition states two cuts.
        (None, (), "format"),
    ),
)
def test_a_runtime_agreeing_work_lane_product_with_a_conflict_is_not_admitted(
    local: str | None, library: tuple[str, ...], title: str
) -> None:
    claims = _language_claims(local)
    product = audiobook_candidate(claims)
    if title == "format":
        product = _two_cuts(product, claims)
    else:
        product = _with_language(product, "de", claims)
    observation = _work_observation(product, claims, library)
    report = _report(observation, claims, library)

    # The title is admitted and the runtime kept as a hint, but the product is
    # never the recording: no marketplace edition evidence and no admission.
    assert observation.status is ProviderStatus.CANDIDATES
    assert not _marketplace(observation.candidates[0])
    assert not admitted_audiobook_identity(report)
    assert not recording_admitted(report)


def _two_cuts(
    candidate: ProviderCandidate, local: tuple[LocalClaim, ...]
) -> ProviderCandidate:
    return finalize_candidate_comparisons(
        replace(
            candidate,
            evidence=tuple(
                sorted(
                    (
                        *candidate.evidence,
                        *(
                            ProviderEvidence(
                                ProviderEvidenceKind.EDITION_STATEMENT,
                                value,
                                ClaimScope.EDITION,
                            )
                            for value in ("abridged", "unabridged")
                        ),
                    ),
                    key=lambda value: (
                        value.scope.value,
                        value.kind.value,
                        value.value,
                    ),
                )
            ),
        ),
        local,
        "catalogue",
    )


def test_a_fed_conflicting_product_never_admits_on_its_stated_runtime() -> None:
    # #1305's other shape: a product retained with its runtime, as a manual or
    # fed observation retains it, whatever the lane did with it.
    claims = _language_claims("en")
    product = _with_language(audiobook_candidate(claims), "de", claims)
    report = _report(
        manual_audiobook_observation("catalogue", "audible_catalog", product), claims
    )

    assert not admitted_audiobook_identity(report)


# -- the report and plan carry the list ---------------------------------------


def test_a_report_carries_the_languages_it_was_prepared_under() -> None:
    claims = _language_claims(None)
    observation = _work_observation(audiobook_candidate(claims), claims, ("en",))
    report = _report(observation, claims, ("en",))
    restored = MetadataReport.from_json(report.to_json())

    assert ("en",) == restored.library_languages
    assert restored == report
    # The list is part of the content address.
    assert report.report_id != _report(observation, claims, ("en", "de")).report_id


def test_an_ebook_report_and_an_invalid_list_are_refused() -> None:
    claims = _language_claims(None)
    report = _report(_work_observation(audiobook_candidate(claims), claims), claims)
    data = json.loads(report.to_json())
    data["library_languages"] = ["english"]
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(data))
    with pytest.raises(ReportValueError, match="library languages"):
        MetadataReport.create(
            target=TargetFacts("primary", "books", "item-1", audio_target_fields()),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=(),
            providers=(),
            library_languages=("en",),
        )


def test_the_audiobook_plan_hands_the_list_to_every_lookup() -> None:
    from tests.support.audiobook_providers import audio_target

    plan = audiobook_discovery_plan(
        audio_target(), audio_local_claims(), library_languages=("de", "en")
    )

    assert ("de", "en") == plan.library_languages
