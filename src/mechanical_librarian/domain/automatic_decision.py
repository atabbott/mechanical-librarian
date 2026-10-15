"""Immutable decision-only facts admitted by an exact qualification envelope."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mechanical_librarian.domain.shadow_outcome import ShadowFieldOutcome

from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_QUALIFICATION_CANDIDATE_SHA256,
)


def title_preservation_bundle(source: ShadowFieldOutcome) -> str | None:
    """Bind citation-backed unchanged titles; retain audiobook witness identities."""
    if (
        source.destination != "title"
        or source.policy_category != "title_layout"
        or source.policy_option not in {
            "preserve", "title_and_subtitle",
            "preserve:audiobook_title_preserve",
            "title_and_subtitle:audiobook_title_preserve",
        }
        or source.disposition != "keep_current"
        or source.candidate_id is not None
        or source.combination_ids
        or not source.citation_ids
        or source.reason_code != "unique_supported_preservation"
    ):
        return None
    return "automatic_bundle_" + _digest({
        "kind": ("ordinary_title_preservation_v1"
                 if source.policy_option in {"preserve", "title_and_subtitle"}
                 else "audiobook_title_preservation_v1"),
        "source_outcome_id": source.outcome_id,
        "citation_ids": list(source.citation_ids),
    })


def audiobook_title_candidate_bundle(source: ShadowFieldOutcome) -> str | None:
    """Bind one qualified scalar audiobook title to its exact elected evidence."""
    if (
        source.destination != "title"
        or source.policy_category != "title_layout"
        or source.policy_option not in {
            "preserve:audiobook_title_fill",
            "preserve:audiobook_title_replace",
            "title_and_subtitle:audiobook_title_fill",
            "title_and_subtitle:audiobook_title_replace",
        }
        or source.policy_destination != "automatic"
        or source.disposition != "candidate"
        or source.candidate_id is None
        or source.combination_ids
        or not source.citation_ids
        or source.reason_code != "unique_supported_candidate"
    ):
        return None
    return "automatic_bundle_" + _digest({
        "kind": "audiobook_title_candidate_v1",
        "source_outcome_id": source.outcome_id,
        "candidate_id": source.candidate_id,
        "citation_ids": list(source.citation_ids),
    })


def audiobook_title_subtitle_bundle(members: tuple[ShadowFieldOutcome, ...]) -> str | None:
    """Bind exactly two independently owned split outcomes to one joint envelope."""
    ordered = tuple(sorted(members, key=lambda member: member.destination))
    if len(ordered) != 2 or tuple(member.destination for member in ordered) != ("subtitle", "title"):
        return None
    subtitle, title = ordered
    if (
        audiobook_decision_qualification.F46_TITLE_SUBTITLE_PAIR_IDENTITY
        not in audiobook_decision_qualification.F46_PASSING_PAIRED_OPTIONS
        or subtitle.policy_category != "subtitle"
        or subtitle.policy_option != "corroborated_split:audiobook_subtitle_fill"
        or title.policy_category != "title_layout"
        or title.policy_option != "title_and_subtitle:audiobook_title_replace"
        or any(member.policy_destination != "automatic" or member.disposition != "candidate"
               or member.candidate_id is None or not member.citation_ids for member in ordered)
        or len(title.combination_ids) != 1
        or title.combination_ids != subtitle.combination_ids
    ):
        return None
    return "automatic_bundle_" + _digest({
        "kind": "audiobook_title_subtitle_pair_v1",
        "qualification": audiobook_decision_qualification.F46_TITLE_SUBTITLE_PAIR_IDENTITY,
        "members": [{"outcome_id": member.outcome_id, "candidate_id": member.candidate_id,
                     "citations": list(member.citation_ids), "combinations": list(member.combination_ids)}
                    for member in ordered],
    })


class AutomaticDecisionError(ValueError):
    """Automatic authority is incomplete, stale, or outside its envelope."""


# Retained wire name: SHA-256 of the canonical current authority object only.
F19A_QUALIFICATION_MANIFEST_SHA256 = "627e9107011a2ce07d7e6eafce69274073f858f1db9b4210fcdc624e289521a4"
_PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS = frozenset({
    "title_layout:preserve", "title_layout:title_and_subtitle",
    "date_precision:year", "date_precision:full_date",
    "language_rendering:preserve", "language_rendering:display_name",
    "contributor_names:preserve", "contributor_names:canonical",
    "publisher_preference:publisher",
})
_PRE_F41_F19A_QUALIFIED_POLICY_OPTIONS = (
    _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS
    | frozenset({"title:replace_target:provider_consensus_noise"})
)
_PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS = _PRE_F41_F19A_QUALIFIED_POLICY_OPTIONS | frozenset({
    "title:replace_target:decoration_tidy",
})
F19A_QUALIFIED_POLICY_OPTIONS = _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS | frozenset({
    "title:replace_target:isbn_creator_supported",
})
F38_QUALIFIED_POLICY_OPTIONS = frozenset({
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}",
})
F46_QUALIFIED_POLICY_OPTIONS = (
    audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
)
QUALIFIED_POLICY_OPTIONS = (
    F19A_QUALIFIED_POLICY_OPTIONS
    | F20_PASSING_AUTOMATIC_OPTIONS
    | F38_QUALIFIED_POLICY_OPTIONS
    | F46_QUALIFIED_POLICY_OPTIONS
)

# Historical decoding permissions are frozen separately from current grants.
_HISTORICAL_PERMISSIONS_0 = frozenset({
    'asin:preserve_exact:audiobook_asin_fill',
    'contributor_names:canonical:audiobook_authors_add',
    'contributor_names:canonical:audiobook_authors_fill',
    'contributor_names:canonical:audiobook_authors_normalize',
    'contributor_names:canonical:audiobook_authors_preserve',
    'contributor_names:canonical:audiobook_authors_replace',
    'contributor_names:canonical:audiobook_narrators_add',
    'contributor_names:canonical:audiobook_narrators_fill',
    'contributor_names:canonical:audiobook_narrators_normalize',
    'contributor_names:canonical:audiobook_narrators_preserve',
    'contributor_names:canonical:audiobook_narrators_replace',
    'contributor_names:preserve:audiobook_authors_add',
    'contributor_names:preserve:audiobook_authors_fill',
    'contributor_names:preserve:audiobook_authors_normalize',
    'contributor_names:preserve:audiobook_authors_preserve',
    'contributor_names:preserve:audiobook_authors_replace',
    'cover_order:replace_if_strictly_better:audiobook_cover_replace',
    'date_precision:full_date:audiobook_publisheddate_fill',
    'description_fill:replace_safe_plain_text:audiobook_description_fill',
    'description_fill:replace_safe_plain_text:audiobook_description_preserve',
    'description_fill:replace_safe_plain_text:audiobook_description_replace',
    'description_fill:safe_plain_text:audiobook_description_fill',
    'description_fill:safe_plain_text:audiobook_description_preserve',
    'explicit:adult_product:audiobook_explicit_fill',
    'explicit:adult_product:audiobook_explicit_preserve',
    'explicit:adult_product:audiobook_explicit_replace',
    'genres:add:audiobook_genres_add',
    'genres:add:audiobook_genres_fill',
    'genres:add:audiobook_genres_preserve',
    'genres:fill_empty:audiobook_genres_fill',
    'genres:fill_empty:audiobook_genres_preserve',
    'genres:replace:audiobook_genres_add',
    'genres:replace:audiobook_genres_fill',
    'genres:replace:audiobook_genres_normalize',
    'genres:replace:audiobook_genres_preserve',
    'genres:replace:audiobook_genres_replace',
    'isbn_form:prefer_isbn13:audiobook_isbn_fill',
    'isbn_form:prefer_isbn13:audiobook_isbn_normalize',
    'isbn_form:prefer_isbn13:audiobook_isbn_replace',
    'isbn_form:preserve_exact:audiobook_isbn_fill',
    'isbn_form:preserve_exact:audiobook_isbn_preserve',
    'language_rendering:display_name:audiobook_language_fill',
    'language_rendering:display_name:audiobook_language_normalize',
    'language_rendering:display_name:audiobook_language_preserve',
    'language_rendering:display_name:audiobook_language_replace',
    'language_rendering:preserve:audiobook_language_fill',
    'language_rendering:preserve:audiobook_language_normalize',
    'language_rendering:preserve:audiobook_language_preserve',
    'language_rendering:preserve:audiobook_language_replace',
    'publisher_preference:publisher:audiobook_publisher_fill',
    'publisher_preference:publisher:audiobook_publisher_preserve',
    'series:add_qualified:audiobook_series_add',
    'series:add_qualified:audiobook_series_fill',
    'series:add_qualified:audiobook_series_preserve',
    'series:all_qualified:audiobook_series_fill',
    'series:all_qualified:audiobook_series_preserve',
    'series:primary_narrative:audiobook_series_fill',
    'series:primary_narrative:audiobook_series_preserve',
    'series:replace_qualified:audiobook_series_fill',
    'series:replace_qualified:audiobook_series_normalize',
    'series:replace_qualified:audiobook_series_preserve',
    'series:replace_qualified:audiobook_series_replace',
    'series:source_order:audiobook_series_fill',
    'series:source_order:audiobook_series_preserve',
    'subtitle:corroborated_split:audiobook_subtitle_fill',
    'subtitle:fill_empty:audiobook_subtitle_fill',
    'subtitle:replace_descriptive:audiobook_subtitle_preserve',
    'subtitle:replace_descriptive:audiobook_subtitle_replace',
    'tags:add:audiobook_tags_add',
    'tags:fill_empty:audiobook_tags_fill',
    'title_layout:preserve:audiobook_title_fill',
    'title_layout:preserve:audiobook_title_preserve',
    'title_layout:preserve:audiobook_title_replace',
    'title_layout:title_and_subtitle:audiobook_title_fill',
    'title_layout:title_and_subtitle:audiobook_title_preserve',
    'title_layout:title_and_subtitle:audiobook_title_replace',
})
_HISTORICAL_PERMISSIONS_1 = frozenset({
    'asin:preserve_exact:audiobook_asin_fill',
    'contributor_names:canonical:audiobook_authors_add',
    'contributor_names:canonical:audiobook_authors_fill',
    'contributor_names:canonical:audiobook_authors_normalize',
    'contributor_names:canonical:audiobook_authors_preserve',
    'contributor_names:canonical:audiobook_authors_replace',
    'contributor_names:canonical:audiobook_narrators_add',
    'contributor_names:canonical:audiobook_narrators_fill',
    'contributor_names:canonical:audiobook_narrators_normalize',
    'contributor_names:canonical:audiobook_narrators_preserve',
    'contributor_names:canonical:audiobook_narrators_replace',
    'contributor_names:preserve:audiobook_authors_add',
    'contributor_names:preserve:audiobook_authors_fill',
    'contributor_names:preserve:audiobook_authors_normalize',
    'contributor_names:preserve:audiobook_authors_preserve',
    'contributor_names:preserve:audiobook_authors_replace',
    'cover_order:replace_if_strictly_better:audiobook_cover_replace',
    'date_precision:full_date:audiobook_publisheddate_fill',
    'description_fill:replace_safe_plain_text:audiobook_description_replace',
    'description_fill:safe_plain_text:audiobook_description_fill',
    'explicit:adult_product:audiobook_explicit_fill',
    'explicit:adult_product:audiobook_explicit_preserve',
    'explicit:adult_product:audiobook_explicit_replace',
    'genres:add:audiobook_genres_add',
    'genres:add:audiobook_genres_fill',
    'genres:add:audiobook_genres_preserve',
    'genres:fill_empty:audiobook_genres_fill',
    'genres:fill_empty:audiobook_genres_preserve',
    'genres:replace:audiobook_genres_add',
    'genres:replace:audiobook_genres_fill',
    'genres:replace:audiobook_genres_normalize',
    'genres:replace:audiobook_genres_preserve',
    'genres:replace:audiobook_genres_replace',
    'isbn_form:prefer_isbn13:audiobook_isbn_fill',
    'isbn_form:prefer_isbn13:audiobook_isbn_normalize',
    'isbn_form:prefer_isbn13:audiobook_isbn_replace',
    'isbn_form:preserve_exact:audiobook_isbn_fill',
    'isbn_form:preserve_exact:audiobook_isbn_preserve',
    'language_rendering:display_name:audiobook_language_fill',
    'language_rendering:display_name:audiobook_language_normalize',
    'language_rendering:display_name:audiobook_language_preserve',
    'language_rendering:display_name:audiobook_language_replace',
    'language_rendering:preserve:audiobook_language_fill',
    'language_rendering:preserve:audiobook_language_normalize',
    'language_rendering:preserve:audiobook_language_preserve',
    'language_rendering:preserve:audiobook_language_replace',
    'publisher_preference:publisher:audiobook_publisher_fill',
    'publisher_preference:publisher:audiobook_publisher_preserve',
    'series:add_qualified:audiobook_series_add',
    'series:add_qualified:audiobook_series_fill',
    'series:add_qualified:audiobook_series_preserve',
    'series:all_qualified:audiobook_series_fill',
    'series:all_qualified:audiobook_series_preserve',
    'series:primary_narrative:audiobook_series_fill',
    'series:primary_narrative:audiobook_series_preserve',
    'series:replace_qualified:audiobook_series_fill',
    'series:replace_qualified:audiobook_series_normalize',
    'series:replace_qualified:audiobook_series_preserve',
    'series:replace_qualified:audiobook_series_replace',
    'series:source_order:audiobook_series_fill',
    'series:source_order:audiobook_series_preserve',
    'subtitle:corroborated_split:audiobook_subtitle_fill',
    'subtitle:fill_empty:audiobook_subtitle_fill',
    'subtitle:replace_descriptive:audiobook_subtitle_preserve',
    'subtitle:replace_descriptive:audiobook_subtitle_replace',
    'tags:add:audiobook_tags_add',
    'tags:fill_empty:audiobook_tags_fill',
    'title_layout:preserve:audiobook_title_fill',
    'title_layout:preserve:audiobook_title_preserve',
    'title_layout:preserve:audiobook_title_replace',
    'title_layout:title_and_subtitle:audiobook_title_fill',
    'title_layout:title_and_subtitle:audiobook_title_preserve',
    'title_layout:title_and_subtitle:audiobook_title_replace',
})
_HISTORICAL_PERMISSIONS_2 = frozenset({
    'contributor_names:canonical',
    'contributor_names:preserve',
    'date_precision:full_date',
    'date_precision:year',
    'language_rendering:display_name',
    'language_rendering:preserve',
    'publisher_preference:publisher',
    'title:replace_target:decoration_tidy',
    'title:replace_target:isbn_creator_supported',
    'title:replace_target:provider_consensus_noise',
    'title_layout:preserve',
    'title_layout:title_and_subtitle',
})
_HISTORICAL_PERMISSIONS_3 = frozenset({
    'contributor_names:canonical',
    'contributor_names:preserve',
    'date_precision:full_date',
    'date_precision:year',
    'language_rendering:display_name',
    'language_rendering:preserve',
    'publisher_preference:publisher',
    'title:replace_target:decoration_tidy',
    'title:replace_target:provider_consensus_noise',
    'title_layout:preserve',
    'title_layout:title_and_subtitle',
})
_HISTORICAL_PERMISSIONS_4 = frozenset({
    'contributor_names:canonical',
    'contributor_names:preserve',
    'date_precision:full_date',
    'date_precision:year',
    'language_rendering:display_name',
    'language_rendering:preserve',
    'publisher_preference:publisher',
    'title:replace_target:provider_consensus_noise',
    'title_layout:preserve',
    'title_layout:title_and_subtitle',
})
_HISTORICAL_PERMISSIONS_5 = frozenset({
    'contributor_names:canonical',
    'contributor_names:preserve',
    'date_precision:full_date',
    'date_precision:year',
    'language_rendering:display_name',
    'language_rendering:preserve',
    'publisher_preference:publisher',
    'title_layout:preserve',
    'title_layout:title_and_subtitle',
})
_HISTORICAL_PERMISSIONS_6 = frozenset({
    'contributor_names:canonical:audiobook_authors_fill',
    'contributor_names:canonical:audiobook_authors_replace',
    'contributor_names:canonical:audiobook_narrators_add',
    'contributor_names:canonical:audiobook_narrators_fill',
    'contributor_names:canonical:audiobook_narrators_normalize',
    'contributor_names:canonical:audiobook_narrators_preserve',
    'contributor_names:canonical:audiobook_narrators_replace',
    'contributor_names:preserve:audiobook_authors_fill',
    'contributor_names:preserve:audiobook_authors_replace',
    'genres:add:audiobook_genres_add',
    'genres:add:audiobook_genres_fill',
    'genres:add:audiobook_genres_preserve',
    'genres:fill_empty:audiobook_genres_fill',
    'genres:fill_empty:audiobook_genres_preserve',
    'genres:replace:audiobook_genres_add',
    'genres:replace:audiobook_genres_fill',
    'genres:replace:audiobook_genres_normalize',
    'genres:replace:audiobook_genres_preserve',
    'genres:replace:audiobook_genres_replace',
    'language_rendering:display_name:audiobook_language_fill',
    'language_rendering:display_name:audiobook_language_normalize',
    'language_rendering:display_name:audiobook_language_preserve',
    'language_rendering:display_name:audiobook_language_replace',
    'language_rendering:preserve:audiobook_language_fill',
    'language_rendering:preserve:audiobook_language_normalize',
    'language_rendering:preserve:audiobook_language_preserve',
    'language_rendering:preserve:audiobook_language_replace',
    'series:add_qualified:audiobook_series_add',
    'series:add_qualified:audiobook_series_fill',
    'series:add_qualified:audiobook_series_preserve',
    'series:all_qualified:audiobook_series_fill',
    'series:all_qualified:audiobook_series_preserve',
    'series:primary_narrative:audiobook_series_fill',
    'series:primary_narrative:audiobook_series_preserve',
    'series:replace_qualified:audiobook_series_fill',
    'series:replace_qualified:audiobook_series_normalize',
    'series:replace_qualified:audiobook_series_preserve',
    'series:replace_qualified:audiobook_series_replace',
    'series:source_order:audiobook_series_fill',
    'series:source_order:audiobook_series_preserve',
    'title_layout:preserve:audiobook_title_fill',
    'title_layout:preserve:audiobook_title_replace',
    'title_layout:title_and_subtitle:audiobook_title_fill',
    'title_layout:title_and_subtitle:audiobook_title_replace',
})
_HISTORICAL_PERMISSIONS_7 = frozenset({
    'description_fill:safe_plain_text',
    'genres:add',
    'genres:fill_empty',
    'genres:replace',
    'isbn_form:prefer_isbn13',
    'isbn_form:preserve_exact',
    'series:all_qualified',
    'series:primary_narrative',
    'series:source_order',
    'subtitle:fill_empty',
    'tags:add',
    'tags:fill_empty',
    'tags:replace',
})
_HISTORICAL_PERMISSIONS_8 = frozenset({
    'description_fill:safe_plain_text',
    'genres:add',
    'genres:fill_empty',
    'genres:replace',
    'isbn_form:prefer_isbn13',
    'isbn_form:preserve_exact',
    'series:all_qualified',
    'series:primary_narrative',
    'series:source_order',
    'tags:add',
    'tags:fill_empty',
    'tags:replace',
})
_HISTORICAL_PERMISSIONS_9 = frozenset({
    'description_fill:safe_plain_text',
    'genres:add',
    'genres:replace',
    'isbn_form:prefer_isbn13',
    'isbn_form:preserve_exact',
    'series:all_qualified',
    'series:primary_narrative',
    'series:source_order',
    'tags:add',
    'tags:fill_empty',
    'tags:replace',
})

_PRIOR_F19A_QUALIFICATION_MANIFESTS = (
    ("38075caf63c051fb2031d6e894bcf7a462cfe5b66399ff8746b74ed5850bd5f1", _HISTORICAL_PERMISSIONS_2),
    ("00b19ce194b1747007f45990fa8412820082160294c21943064185f50bb05add", _HISTORICAL_PERMISSIONS_2),
    ("5dddeb7bf21a0dfd7dabd3dfb6de59e85c705b349c229d9d40e15f0524e82d2e", _HISTORICAL_PERMISSIONS_2),
    ("d2a930fef7ff42cc2eb1ef2bc7ae9a0e804318c39b7b66647180f8f25919d4d9", _HISTORICAL_PERMISSIONS_2),
    ("817f9b8259502c8e51c538f7fc025198e9e9746f7f84f1b4bbbb44e0e7e3dbcd", _HISTORICAL_PERMISSIONS_2),
    ("ba8c43413b9419d41f225c99e81d72b87a3ffb955bff10449dc1fb5676ead0e0", _HISTORICAL_PERMISSIONS_2),
    ("d568b1d59a99ab6cd2e50165a7365742ab317fa2d4a0f76de877a2e185e9ea1d", _HISTORICAL_PERMISSIONS_2),
    ("d9f6e90b2bcb0b020556f7492990ee86727482f7148579443d2d1bf9ac49d6c4", _HISTORICAL_PERMISSIONS_2),
    ("f8dd88e5cf53a6e47871d8f8bf3357331a6b59baa72827b23891944c47d62344", _HISTORICAL_PERMISSIONS_2),
    ("631286528f23707c48a5782c43c108ad7a721c4a10b0c34199246fa68adde51a", _HISTORICAL_PERMISSIONS_2),
    ("35a6b200b77264c7b0ee1393c5594000d2872ff9d11fd8fc25d6b65ca919c024", _HISTORICAL_PERMISSIONS_2),
    ("a8478e315c61c53cbdedeb420fc4c115bc3e440bf879d3590c2865df0bf79c32", _HISTORICAL_PERMISSIONS_2),
    ("c4390328dbd2d39fefc68f68819d37b126974175843de4b3944e020508502dbf", _HISTORICAL_PERMISSIONS_2),
    ("38e51dfa85abd8be8606234ee7c1eb789daa176f863f8d9f701deea3969f1f5b", _HISTORICAL_PERMISSIONS_2),
    ("91c293b69ef7153b63c458b87ce289598ceda8363fabc33621c07e90cf04455f", _HISTORICAL_PERMISSIONS_2),
    ("3f512f587f999ce675672686b03d96213bbd8afe44abdc2b17fe7aa8f2328bd9", _HISTORICAL_PERMISSIONS_2),
    ("b236b2612772f298c44e196a82e053a6b63e8eee05ea4c105bc262bdce0489ab", _HISTORICAL_PERMISSIONS_2),
    ("b0987ca2be0c2171545da8942b1646a99197e1315714e985b07049b637ca63b3", _HISTORICAL_PERMISSIONS_2),
    ('8a443c1c879ec69b64f8eef5ce4905042b570f265669411eda9ee8cddd404187', _HISTORICAL_PERMISSIONS_2),
    ("437a7549d2007132ba228bd828fba0356fd89f46df41a2afbf38eed8a340a052", _HISTORICAL_PERMISSIONS_2),
    ("1469272be676f1c42591d87cf76033b25559082da7411cac0738b716b9c0fcb2", _HISTORICAL_PERMISSIONS_2),
    ("8e075ba6671ac1dee8ed6c5dfc9f7285380397ba47d292d3e174742a9f962147", _HISTORICAL_PERMISSIONS_2),
    ("d48649f16a28bd7cc083cd2c41ef3d5372a5ee050c9e763719fafd86178dcfb2", _HISTORICAL_PERMISSIONS_2),
    ("a46d75a576f05ed37c3d9b23b994d349968ab1e6bab52df27aa60480462df1ae", _HISTORICAL_PERMISSIONS_2),
    ("6c45a65639e89e9ae1f0bec1af042fbe95e4dd33ee6021aed296b66340b6c430", _HISTORICAL_PERMISSIONS_2),
    ("47a01405e62e9a583438026760aa2f98665bb1b018bca1a1c6514b7102452717", _HISTORICAL_PERMISSIONS_2),
    ("bd0c20ddfdb7909915ce3ad307b8853381eb8ac254b7cc821864467ef24aa180", _HISTORICAL_PERMISSIONS_2),
    ("d7013f0cc88f2fcd476de73c0f9185c265f30ba8360f2daee08266200b57eff3", _HISTORICAL_PERMISSIONS_2),
    ("f7eabaf07b3893babd2bbf4dd45d299b58a37b1d2f427ad663e31fca548474b7", _HISTORICAL_PERMISSIONS_2),
    ("8af85b77852fa7dcb7dcc84458809aec20fc7b8b5e3dfcf95577535eeedf2038", _HISTORICAL_PERMISSIONS_2),
    ("6151378c5ec4c044731eef559736ca9a09144c271644f7bfd8970c7d2fa98337", _HISTORICAL_PERMISSIONS_2),
    ("d378d33df53a5fae94860c05a9aabf01307d2ba87c2390a8aa3d3e498fda3cad", _HISTORICAL_PERMISSIONS_2),
    ("61fe819cafee34ef0ec743c08632449235d819798c33ab2b58aeb5db61dd513c", _HISTORICAL_PERMISSIONS_2),
    ("16d37103732ce6572c043e66f2984a2b17ef932aaf23f2eea36d0647f9d834c4", _HISTORICAL_PERMISSIONS_2),
    ("888f2144f5edc5c8c2fc6d279882fa6d88c1569b706f3d2d48644decc8e1d625", _HISTORICAL_PERMISSIONS_2),
    ('73cd3e9923600d1b85b5c82155f677485d0e51eda11b5c4920bfdfcd23e80ca9', _HISTORICAL_PERMISSIONS_2),
    ('cfee88ac6aa6f30a5fe918d1bdbaa0e8b005c5df901fae9f8c2eaf9197562051', _HISTORICAL_PERMISSIONS_2),
    ('4ce7351925e110a5808cfdd778192bc2e8a840dcb57ea73add6172c1e663dd0d', _HISTORICAL_PERMISSIONS_2),
    ('c23c7cdf1bdfc47e5c225b6d13299c47a7163cba6c33796f61f89410f0ee3ef3', _HISTORICAL_PERMISSIONS_2),
    ('e959dde63913605db2d0dc5ed1a4a3c98c1e3f70a36791e6fd593bf831d0bf2d', _HISTORICAL_PERMISSIONS_2),
    ('28489fb10f20a47c036ebfba11584829a6598a3ec1dfae52ce993c6be10bf935', _HISTORICAL_PERMISSIONS_2),
    ('3606173cd73ed348a151ae34794048e217f46e6e4bae83f5c080fdfe27dd0022', _HISTORICAL_PERMISSIONS_2),
    ('b71766e606ac6affb2251fb68f57c9640ba5455266a592f065916b549f3fcbfc', _HISTORICAL_PERMISSIONS_2),
    ('796b2a4d55851a997dae96f0863a527c8820a841e1a90c9712f4cf9fc50a4c29', _HISTORICAL_PERMISSIONS_2),
    ('d65cdbe68edeb9b93bda94d013b1c14fb9fea40e152506d9f1a0032cf6d54db2', _HISTORICAL_PERMISSIONS_2),
    ('0f59b668db9c95502cc3bd02d7eb3d75e5abdae3ae45e15ec927f7cdd7160aa6', _HISTORICAL_PERMISSIONS_2),
    ('d5903b889fe5b457c7303e5b27d51677bcf798a11ed25bb637a4a7ae4eb5cd77', _HISTORICAL_PERMISSIONS_2),
    ('00d8aa094f1a1e18a89741a37af670636c913bff48ba8dd56eb0ed722cfb9889', _HISTORICAL_PERMISSIONS_2),
    ('010c06261591a6f4fd959c8c1fe0cc8e1346b3fc58a7923daf4de1ec93049cae', _HISTORICAL_PERMISSIONS_3),
    ('01c876cd73706231bf6ab3de9371e0c60b755124d19427a807fc9472b65bbdd0', _HISTORICAL_PERMISSIONS_3),
    ('02202d0705cb8403612c4efc71224e0f097ce8fb28d641c3410802a9e4d8c451', _HISTORICAL_PERMISSIONS_2),
    ('09d99f036e562e8134b948694d3bd6651ffefcf5b1948532adeba1b49c3dc6f6', _HISTORICAL_PERMISSIONS_2),
    ('15298611ad0b50dc9a8adb924e6e3551a4da0dd641d2600843f3dac260a71dfb', _HISTORICAL_PERMISSIONS_3),
    ('15766b5b2232b2bb7e64cefa4e529c134b7c3da829f5f801a1b43afb897b4676', _HISTORICAL_PERMISSIONS_2),
    ('1b84a02a5629fea2475ac2a51b18d06219ba5e7c92f07b38e98eb4359a5a0f29', _HISTORICAL_PERMISSIONS_2),
    ('32949b585f0be6460b5e82961fe73139eec36ca77b0db9a892f41c631ef0f495', _HISTORICAL_PERMISSIONS_2),
    ('3624130f46e4f198a97d8892b9a950693d6b383b942a0466e8190b216ad226a5', _HISTORICAL_PERMISSIONS_2),
    ('3d0d7457d1704574f979121acaedfc5c4c7890f9f881042444161da8e1927202', _HISTORICAL_PERMISSIONS_2),
    ('45e94d998ee9e12ff071ce0210afa46e31f7f486d8d81fedb8ac3a7ef77acc73', _HISTORICAL_PERMISSIONS_2),
    ('50eaf202ea5d57407714427863634e45abe4be6380616e7d4c0b811e5df033c6', _HISTORICAL_PERMISSIONS_2),
    ('62e8db714fe9a35065bb700e6eac58f1611ec3acca5a90e29965fa2e5c5e6014', _HISTORICAL_PERMISSIONS_3),
    ('63151c7ea3f24b8a568148a7f73dc62aa3279760af9d35fcb77593d3ecd5f0b3', _HISTORICAL_PERMISSIONS_2),
    ('6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56', _HISTORICAL_PERMISSIONS_5),
    ('685a88818f97eeda1c302fad6a695ffffab634428248a8c9a3274980491d3a90', _HISTORICAL_PERMISSIONS_2),
    ('715bdf440093ae0857825f6cc465e94d8b12f773bf1543866e49018240ccda86', _HISTORICAL_PERMISSIONS_2),
    ('72a2f7cfc1d7d103538d2211709e608400d7102ba0ab4119ae236d9bce3434cf', _HISTORICAL_PERMISSIONS_2),
    ('75d0a02488970443f2933cb631cf3849f602071ae338c4cd1b9573ef4696258c', _HISTORICAL_PERMISSIONS_2),
    ('77fd7323b01446306577865ebdb4b47f4fb34748021a89ffdca3fa4d51017335', _HISTORICAL_PERMISSIONS_3),
    ('78aa429a9827e6fc23aa0d4eac30e6242f4f08b99326f3852e403199e99c2337', _HISTORICAL_PERMISSIONS_2),
    ('7f0bfa050ec5956f9d21fcb521f5c06f3a9310cbee42fd7e206a4ec2d71be1e6', _HISTORICAL_PERMISSIONS_3),
    ('7fbe2ea38f06b0ad0bd3a6941ec1bc7dc08079bdf1a38202898201a3720f9f00', _HISTORICAL_PERMISSIONS_2),
    ('91292dd98bef221efc6d75ed336bc1031e3917fa75c05dcd38474eef5b29e523', _HISTORICAL_PERMISSIONS_3),
    ('9d14ba06848eb001d160b617712b1126ba81a0f47465fb2ba0123fb569573130', _HISTORICAL_PERMISSIONS_2),
    ('acdc9fea8299e27b919a8d09d459e2441bd80017a168f4f040b3bdc03794a6b5', _HISTORICAL_PERMISSIONS_2),
    ('b07048c857e5946634f6eaa0ec57cd609e5288899eb020ef20d26df8ed37e426', _HISTORICAL_PERMISSIONS_2),
    ('b1dfe9eae1a74157590c010cb3fa0a1fa453372b2bdc595e9e3a51d75a1076a2', _HISTORICAL_PERMISSIONS_2),
    ('c2b2170b1e7ce6f9b199afbbdf303e72cf2fd011bcaa0b4d3e09ab1cbd577583', _HISTORICAL_PERMISSIONS_2),
    ('c975fa0f6c4f18a51615c1ee6000227f37e1595dd7f29b0aff17b9e4909fe0e8', _HISTORICAL_PERMISSIONS_2),
    ('ca8d03313746886788e2b83e6baca10fc2a6275726c7861902c032341c408093', _HISTORICAL_PERMISSIONS_2),
    ('cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37', _HISTORICAL_PERMISSIONS_5),
    ('df7d676444bbc74e168c30f1e7991184d61a32a2d235609554674b093bd6fd43', _HISTORICAL_PERMISSIONS_2),
    ('df88fef11c2d9d015c4642688cf5f5f62902f90de9474eb2828417aaeebdb420', _HISTORICAL_PERMISSIONS_2),
    ('e0cf71dcb87933df3fd62348426ff600500639c0783b5400d425bdc35b77f732', _HISTORICAL_PERMISSIONS_2),
    ('e337a843d95b3bb6198824e2678323a00f5a6d47c2ec518396a473aecbb1c192', _HISTORICAL_PERMISSIONS_2),
    ('e3f687cdf4eb191ce0782c1d0a8f036587d4e2eb8dd33e4fa47c9f59f7655147', _HISTORICAL_PERMISSIONS_2),
    ('e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec', _HISTORICAL_PERMISSIONS_4),
    ('e793f56547d815b069738deb19326ef6817c514478ef7f5b56dcb15413203d12', _HISTORICAL_PERMISSIONS_2),
    ('ede07974f94886aef832e30fe11fbcb8af7f82aa10049e301729c66738bf03c4', _HISTORICAL_PERMISSIONS_2),
    ('ef0532a90bc6f4dd35973d62d3d8caa7f6b56189631d748fa999dc0c6027625b', _HISTORICAL_PERMISSIONS_2),
    ('f0284ac2b336fc72071de5ad76d601d67fd78fbdc02b40214879c7c053d1453e', _HISTORICAL_PERMISSIONS_2),
    ('f22c32088a1a7048df6d9897352a874241c99fb75656553e850eb828dcce1361', _HISTORICAL_PERMISSIONS_2),
    ('f30e2c102d55c532dff554f7b6ca17c3998bcd965ce55d9ca6e2df052c48b7d9', _HISTORICAL_PERMISSIONS_3),
    ('f7a4493d94f0188e6974aad1b68f9238d9e7733aa1a2e064e5663e385af0fe6e', _HISTORICAL_PERMISSIONS_3),
    ('f9f172406ddb2a8b5ca282cad3fa25c29148ac7e04100caccc567c6648d6098b', _HISTORICAL_PERMISSIONS_2),
    ('7b5cfb2662246bc74f4ad7745f1edfe0656fdbe0116a712fc3af93673945f9a2', _HISTORICAL_PERMISSIONS_2),
)

_PRIOR_F20_QUALIFICATION_MANIFESTS = (
    ("136b3f8178791e743eef56f47ebd7b7ad9bfbbfa371a3ffa1c3707de0a2c6e73", _HISTORICAL_PERMISSIONS_7),
    ("9e14250546e7d8a266d9f8661bbe5aa7965d0cfa8517a9a3fe1504c9f09d9a37", _HISTORICAL_PERMISSIONS_7),
    ("96f08d12f6f4f7b6218c21e4525d25c4bdb5a37efdba3d3f71f38f0ebe02b285", _HISTORICAL_PERMISSIONS_7),
    ("e49e2c244459123efc7fa1e81ecb06e86b1ea223a3cd61b8fcaf9202005a2aed", _HISTORICAL_PERMISSIONS_7),
    ("020300d05a69272d4d207d2659f7c33f1be4c7df7316b77220f55437e88ee32b", _HISTORICAL_PERMISSIONS_7),
    ("7c0bc2cb436d637f1e9f1ddeae5fa0f80bf6b3a331219bf9eea2982bc2d0d6f6", _HISTORICAL_PERMISSIONS_7),
    ("74fe1f1bef806bd0df74b39d4931c1db997a2707d1c814723c9cd127a022bb61", _HISTORICAL_PERMISSIONS_7),
    ("f5babe5b7e176e52afa45aa4600647cfe435e1df8bbfad4ccd4e8f80703fd65b", _HISTORICAL_PERMISSIONS_7),
    ("688d5d9737f27d7edafda8dcb96ea0a5396f022860e93f9d065239317f78c254", _HISTORICAL_PERMISSIONS_7),
    ("5cc75cc1a2a4fe4dc1f4319ddde7a7adec586aba931b053cfcbeadc0550763ee", _HISTORICAL_PERMISSIONS_7),
    ("5f010f5812b9cf4ba2d2fa6879a289a95b2fd112630c0f7a10b41c6740e34fa3", _HISTORICAL_PERMISSIONS_7),
    ("29fa4991479c830e1163eeb1e8ac39663ce718f45166a3744587b3eb00ba3aab", _HISTORICAL_PERMISSIONS_7),
    ("e39b0633845c260bba64cdc30968b26c4805f06fed9d81e4153c61f3d7bf6eff", _HISTORICAL_PERMISSIONS_7),
    ("1fec18e0a5eea07eb5af5765756332e68ffb2a4f99254e171a12337ee4cc5312", _HISTORICAL_PERMISSIONS_7),
    ("559889d2d6d9dfefabdcbc391bb7f6cbf4a8092d1caa0e418421296be9fcbb4a", _HISTORICAL_PERMISSIONS_7),
    ("938b9ce811802183145b92f4d45a8b61ef239b2e2cf042e49b408173842a4d92", _HISTORICAL_PERMISSIONS_7),
    ("5da9c325b2cacd183ceb83d342d07f22e835aeda435583716e14d13080533b52", _HISTORICAL_PERMISSIONS_7),
    ("013fd7173f7b748d900e9088a0955c857f1aeb3543fc630d1f2f792d6d8e1a59", _HISTORICAL_PERMISSIONS_7),
    ('814ad344e1579bf81e295a8c46b07000086bda409182747f43b232c5d8b476c1', _HISTORICAL_PERMISSIONS_7),
    ("943996cf5f21f79ab5cb2a9991a4412f2e39c1643a0187b049844159182cb58e", _HISTORICAL_PERMISSIONS_7),
    ("62da3c4c37ce61692120adc4c578386acd058082d081c02870c2e6799e738a9a", _HISTORICAL_PERMISSIONS_7),
    ("7ca996f6f834c70addd870182e3911b783597e7487f7089e20d54750bebb036c", _HISTORICAL_PERMISSIONS_7),
    ("c8d7621d9f532daeb8a37a88627dfd128528705988be06c67dee9d286752a0a9", _HISTORICAL_PERMISSIONS_7),
    ("20260cf99162a6b452f74aa02afff42efce26d590cf5e792c01f9ce72b697092", _HISTORICAL_PERMISSIONS_7),
    ("08b13c4390c6abe494b0af882f4632b1c6ad74618f308e88df4ba7a216cce7ab", _HISTORICAL_PERMISSIONS_7),
    ("efed61bd11e4db288c881d88239a11e24fbcef25b4a8cdcdf2832929599f2ec8", _HISTORICAL_PERMISSIONS_7),
    ("46b78078736e845aabf123f6594ef0ab1f3d48a1e73b6cce0612173afd85d038", _HISTORICAL_PERMISSIONS_7),
    ("0085c6617dc55aaffa65f90931c9055cfd19a2663fcbb3555a11d343b4eeafef", _HISTORICAL_PERMISSIONS_7),
    ("622be5be6e6ac7b8a8506bb0045ef1c0a2ee8aec4958f2550e72e92078a8c805", _HISTORICAL_PERMISSIONS_7),
    ("b2d2ac644ab9bf7f811583c3c63aa5a0ca483ef6f566be2216c1d3dd23f6f656", _HISTORICAL_PERMISSIONS_7),
    ("fb1c3754ef9167a37fe49e27421688632e8ed8871e539289be5385ce483c8520", _HISTORICAL_PERMISSIONS_7),
    ("9d92f8d21be1e1ce1c8d1cf8e025c7feeb6c32143ff4cb0561e484afc7be5e9c", _HISTORICAL_PERMISSIONS_7),
    ("5ead1bb255187d079eaa712e2c15bfeae9ed75fbd32e463c748ac13515d7ab5c", _HISTORICAL_PERMISSIONS_7),
    ("5226ae2d56a23b1350da62b48819c5fd2277cda51e49706919abcfc0dd901985", _HISTORICAL_PERMISSIONS_7),
    ("41b78db864563f25246fe0d601d85470946c01e3c553dd4402da46a54b04d537", _HISTORICAL_PERMISSIONS_7),
    ('8feec1320711ca12a725a17f2edccb55a173bd0ba1da178d21b2e69754555bc8', _HISTORICAL_PERMISSIONS_7),
    ('d87ef2b962bf83b2a8efb5cb5e56de6f1d5dd9d2093778e57f4cc915d0f79daa', _HISTORICAL_PERMISSIONS_7),
    ('f3743d065f3bdf0df4ad9a902c7f235949f7a0aade296f9744958c5b502f2dbd', _HISTORICAL_PERMISSIONS_7),
    ('14e8b99fe109f080f3c3eb861f1f0c03ec5d3bb682c53cd0c41bb9a0b95a2211', _HISTORICAL_PERMISSIONS_7),
    ('bc5990df48e5ada2a7ffbbc1ed92d5290600dd667dfe9d9fc99ac678f4157a85', _HISTORICAL_PERMISSIONS_7),
    ('8ac05c1ef678cc7fca3d98186fd75ea53a2a91f402dd11d53ee406d47aa97465', _HISTORICAL_PERMISSIONS_7),
    ('b529c94a7ad2099c150d743cc31959bd97430d13c2474e46f1579cbfb8011a7b', _HISTORICAL_PERMISSIONS_7),
    ('d01c036ee3ff4072a08a83d3f2fb55305b3fe1b0f60acf525305c337e31fef47', _HISTORICAL_PERMISSIONS_7),
    ('19939b85acb471fb17c076a44c6a0dd173f6c32e5a951986b9e65060458cfd8a', _HISTORICAL_PERMISSIONS_7),
    ('05349d253bf340c2b1a7635fdae210bb53a640f10ca4b960305969801d3bd7ac', _HISTORICAL_PERMISSIONS_7),
    ('d1bc961a69d7cd37ca8763b297c68666f3156fbdcc8961ceced68f3ffaecddfb', _HISTORICAL_PERMISSIONS_7),
    ('28b95c60856b1c6a5f88b6e67f7cba9c55b215c10567eeb3b29d2a7e98233175', _HISTORICAL_PERMISSIONS_7),
    ('091cdf7f08f60e9e4e1860ff574a9c04c405c914d498560e9802294b69c53a30', _HISTORICAL_PERMISSIONS_7),
    ('10b10aa37008e6cfeb01745ebe75eed15913ca2e7c325517e110dbaf30bbdb74', _HISTORICAL_PERMISSIONS_7),
    ('12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5', _HISTORICAL_PERMISSIONS_7),
    ('127592d23e85214fa8ca22ada14a0403822747c72cab9009d6ff3c7e84b1e4e8', _HISTORICAL_PERMISSIONS_7),
    ('1315e324edd78e698587e14497aa0ab11e33fd65612f00cd8a78a462fd6445fd', _HISTORICAL_PERMISSIONS_7),
    ('14061bb1d1df8305d75cc2330ce37a931d01c954f1be3cd03e7587558704fbfc', _HISTORICAL_PERMISSIONS_7),
    ('1588793ea2063ff80a1e8fd8e5d11cf32c599da316665bf476ef32f10654d988', _HISTORICAL_PERMISSIONS_7),
    ('15cfb36db142e3783fa47a73157c72ecfd08bb282a967abc83fba152d1199205', _HISTORICAL_PERMISSIONS_7),
    ('1677b44ad6a8c2a8a53cba6cd821424b213e366f47e6d64e214225742338628c', _HISTORICAL_PERMISSIONS_7),
    ('1dcf4278d8cbba00df764ea17b13809a42304fece748632c217531296dfcddee', _HISTORICAL_PERMISSIONS_7),
    ('1e86a05ee58e845b1b6fa69401046ddcb19ff757ae8e09a9490ca7c9ab96742b', _HISTORICAL_PERMISSIONS_7),
    ('20a0321ce87d7f22dae749f0ee2162c5521ad7dcf386a98cf7b550d377c5fc5e', _HISTORICAL_PERMISSIONS_7),
    ('2628f9f02706e3e0e6afe244231e3fcab387b866007e016b8003f0978bd0780d', _HISTORICAL_PERMISSIONS_8),
    ('2728215f5ade910611d6806e58de40c867a05315efe9670192de48298e54a54b', _HISTORICAL_PERMISSIONS_7),
    ('27f2f2084177ee6f66f5d4458ba571adbbc17b0839806ebb1b558bee1ccdbc44', _HISTORICAL_PERMISSIONS_7),
    ('2e76fdb8372867c95b476208ebb0ae2bd175171439559d4f56eec8d29611c6c6', _HISTORICAL_PERMISSIONS_7),
    ('3b29fdb2e345676adb984a334aa19db8e26689e1b5927ed31bc51f1f09b1b54a', _HISTORICAL_PERMISSIONS_7),
    ('3b62f0fdbad07a0d893f1e18f9d20d23e0c4101db14a001f2df550a383a51927', _HISTORICAL_PERMISSIONS_7),
    ('42a057407a736bfd86f115ae03879f4909fe1217f34ec552c170db1c74af7a8a', _HISTORICAL_PERMISSIONS_7),
    ('438144a3b3a91b6b253b7cd4b52a73c1ecb48ab201efe9426388233879022a2e', _HISTORICAL_PERMISSIONS_7),
    ('4420a5d1ea34f2e46e9c854b07c068e827324fbce942b4da8c301b3cd7409168', _HISTORICAL_PERMISSIONS_7),
    ('47b142cfea8c5d73ed267cb71f85a0edf3ab4b646ad29bba457a1f03b7aa37f2', _HISTORICAL_PERMISSIONS_7),
    ('4a89dfc24a27bbe2a548a9b0c5ad2793cf2da1a2b0445f9dd1ad2f9323822bc9', _HISTORICAL_PERMISSIONS_7),
    ('4c1e65de7d93ed2473d4fb3200f810b550b8b57b7b97f5f3a356a04844405749', _HISTORICAL_PERMISSIONS_7),
    ('4d5fb5a6bba6e2e5c41cdc50c0091748fcbb578f000ec0cb48535d5792a95aa9', _HISTORICAL_PERMISSIONS_7),
    ('4ede15a2aa84e8726ea2dff4e4f9f0d04b27a70276ed53c400c2de5f7619d445', _HISTORICAL_PERMISSIONS_7),
    ('535348d225d6f160a4096c17c3379ee060741cb1d704ad0d741fc4631fc01d9b', _HISTORICAL_PERMISSIONS_7),
    ('637a32ef676a0e85adca6f447d910099b13564a32317970416802b11f42e25d4', _HISTORICAL_PERMISSIONS_7),
    ('6535df8f7c4cebefa62f9c38ab01823a377c0c7048fca20b81aaf250b498b7ff', _HISTORICAL_PERMISSIONS_7),
    ('679b4edfa4f3d79919dfed756140d852acc215e79e08d5bee9b77e3a41977321', _HISTORICAL_PERMISSIONS_7),
    ('694b9bfffd787c097893702990b942012f61f82cd5448ea1f55e7a25c53200d2', _HISTORICAL_PERMISSIONS_7),
    ('6c3e07eae971e632093c4cd57dddf95e6d5d8b197c7013b69bb553fbc00862d9', _HISTORICAL_PERMISSIONS_7),
    ('6c6648598de1fcc175cbbec0f68908eb6f6ca67b2927e4ef05967551d6a9c542', _HISTORICAL_PERMISSIONS_7),
    ('6e4209a2354bab74090c3688031cad7c8b0d2e1a3988d38de5a9b7de051f91a7', _HISTORICAL_PERMISSIONS_7),
    ('70563a47e768fe51cb911511ca67849893e4495266c84bb2e9fc7f6165ed2879', _HISTORICAL_PERMISSIONS_7),
    ('74285ed19d72c767b0589486c6850a2808e9fc9911b7a08b08c34e6979f3a8b2', _HISTORICAL_PERMISSIONS_7),
    ('766e6313f2f6bd81ab2ff5137ddf5eca120de5fa3800e7cab89327c8c020e0b1', _HISTORICAL_PERMISSIONS_7),
    ('770b56be2ce7ea9d1d809be189724e5f281a0dd8570af80d63429a5ab617ff99', _HISTORICAL_PERMISSIONS_7),
    ('7aeaca9b020022b9ab385a9baf249d1b63dc7ffcfcc4a20735d6f6480b5b68e7', _HISTORICAL_PERMISSIONS_7),
    ('8135de356454590a708bbf57af25c488f87933b5c8315a78688f000e29d643ad', _HISTORICAL_PERMISSIONS_7),
    ('8b14e134a146e8f03f9d36ad78506a7f234b0768ef61620ef1d1234a8df7c4a5', _HISTORICAL_PERMISSIONS_7),
    ('8c447eac5fa621e6ee705c79ac9757028ecf6f8d3073baaaba77faec5fa9cebc', _HISTORICAL_PERMISSIONS_7),
    ('901e25432750d3f02df1cd311f5a7146e56d5686bd4b6bc0ac589e5db3eb221a', _HISTORICAL_PERMISSIONS_9),
    ('91ce3c6433fcdcebad2bdb5b6d44a6ac970ad96fe0901f5dae5fc765d54fce62', _HISTORICAL_PERMISSIONS_7),
    ('97a5e0adca62c63acfba4cf7d67318e184e0bfa521f8c4e8b6321916b1afd2a7', _HISTORICAL_PERMISSIONS_7),
    ('99ba8426ee53e4d31e52ecf0f65a0fce4eb237d3e5acef42e4ee17217b57dae9', _HISTORICAL_PERMISSIONS_7),
    ('9c1410dcc6d221478a2b96afdc51da19fd8a54f82d7023034a3063afdedefc37', _HISTORICAL_PERMISSIONS_7),
    ('a07c6b156773d9d51938c7d6b72e95cb1868348de953df94da59da7b76c87723', _HISTORICAL_PERMISSIONS_7),
    ('a51a59e57ac5ea3f2bbaf32e90c0d12e2c1e6453458bc16a1fe2354430b5f73c', _HISTORICAL_PERMISSIONS_7),
    ('a67054686441dcc3430b028bce328c877ec83d5a27c5e23f43c954c00dd119a1', _HISTORICAL_PERMISSIONS_7),
    ('a7af724b0711177ff04ff1ae56c8a7db42c1166fe97390c29e60fed506440500', _HISTORICAL_PERMISSIONS_7),
    ('a813cba313a99597d2669a34ce98ed7454ae85064386a0596d7c7f81854999cd', _HISTORICAL_PERMISSIONS_7),
    ('aacb6e2ebce0490f58b86c8069963445dcb470ffdd70283b5558dde082449103', _HISTORICAL_PERMISSIONS_7),
    ('af0e2de1a9e5dfa7753fc23b873cf413fbbc29e23a44f4408c6457fa13321d04', _HISTORICAL_PERMISSIONS_7),
    ('b978bf5caf2c726c4d25999ef6ca313242fc55e1884b45380e9758c4db6bda88', _HISTORICAL_PERMISSIONS_7),
    ('ba42a24185273170f024d861c5dc6865baf298a785068800ab0a0f3f4a6da31a', _HISTORICAL_PERMISSIONS_8),
    ('bac4643fdb2836ebf3a9589348ead06fefecdce7c66a205fd422c075d659fcdb', _HISTORICAL_PERMISSIONS_7),
    ('bee26102b06154fe0f9beb48581e993b43025bc82713999a1b915633d6aa5648', _HISTORICAL_PERMISSIONS_7),
    ('bf8c9f29704e71ff96e820e7b973bca2a7ec13d5a5304e3474bfcf5e777ad676', _HISTORICAL_PERMISSIONS_7),
    ('c2f4639cb975690dac83f790d543190576f2b34385e81136ae0ff78afd604680', _HISTORICAL_PERMISSIONS_7),
    ('c4411ecceb94ae86549e62c7d68a199af4ea42cd201cb17ce947e905a39c4e6e', _HISTORICAL_PERMISSIONS_7),
    ('c8843d96dfda862b04f6269fd1ab3ab202338815ed5d0efbc0c4c480293adea3', _HISTORICAL_PERMISSIONS_7),
    ('cbdffb1fe38afa6eb03c2a3b9be05bd8ffe8930a884710b96cc25d0fa14f6c5c', _HISTORICAL_PERMISSIONS_7),
    ('d053558970478289c677e4ace49b7f997bfa46a84def0c3bbb0c997177229bb1', _HISTORICAL_PERMISSIONS_7),
    ('d065def423f5e3316172fae8b378ae2a4407481d6ccd1a9db94427baade2a9d1', _HISTORICAL_PERMISSIONS_7),
    ('e209f3f0e7bffede7f7f9aa048617c061652ce9c6e8de2e66b30816b4f639aec', _HISTORICAL_PERMISSIONS_7),
    ('e54d3fcd29b77dd0f81319b61f1491ab86c6c652fccdef61c6543e56e70922a7', _HISTORICAL_PERMISSIONS_7),
    ('f5975691720b81452f2f2b3a41046329c4c600ade45458478aebaf7190392176', _HISTORICAL_PERMISSIONS_7),
    ('f69a500950778b6298c2b149326ddd496bdaa501b5e50a593cd61f47558e32bc', _HISTORICAL_PERMISSIONS_7),
    ('f91bf7c73869d8b3aca8b189c614eb808442b4dbeec13ac106705ecd754e82de', _HISTORICAL_PERMISSIONS_7),
    ('5f943991748c5a613ba1d8f8451040a9224e9bd028941d1e63816879b5d1edfe', _HISTORICAL_PERMISSIONS_7),
)

_PRIOR_F46_QUALIFICATION_MANIFESTS = (
    ("3557b4c3c5e83283579cc7e12826798363de63a41fe7a37abbae94adeec24962", _HISTORICAL_PERMISSIONS_0),
    ("f6bd788ba619c77b01ac543f62eab25eff12d6367bf74e89d83d871474720bd5", _HISTORICAL_PERMISSIONS_0),
    ("e01d177554d3ffabeada95d7296c4fe1fd98d83babd88140a79ed50d44c0263b", _HISTORICAL_PERMISSIONS_0),
    ("155f50cc6228c9ee65ae91b9aeaa6c9a5b12a18800a1c2c44910f1df114d9359", _HISTORICAL_PERMISSIONS_0),
    ("b331350eb1d34c6db9083d140cd83819a6dd4bb6df92b26ec97b60f7ded4f17b", _HISTORICAL_PERMISSIONS_0),
    ("1653f229ab703d24c7b2d12c2a485202a17a85d092c1ca8883f4d06e30b8427d", _HISTORICAL_PERMISSIONS_0),
    ("37eb06232c5b346d1f1b35386cfac4666f620a5c91852099345d85082b6afc69", _HISTORICAL_PERMISSIONS_0),
    ("e557305e9830036ce0bb313dbe38b92ed0b05fd7c7db9f95f1d30668f633073b", _HISTORICAL_PERMISSIONS_0),
    ("f93b61f767eafd8a0e5438886171743f84311a2c5a5a964ec0632ad538442e16", _HISTORICAL_PERMISSIONS_0),
    ("9794229c4ba7dcd86a16ba10779910093029f75e36aeb2d3c4b5263053f502f9", _HISTORICAL_PERMISSIONS_0),
    ("b55c1270c3ac0b0c3e757297ccd965a0f03a9ae0720bb66424ceefcc569fad10", _HISTORICAL_PERMISSIONS_0),
    ("82681b78aad237bb0f614ecbcc6164ea2661478fee3551cc415bc33ad3315eb3", _HISTORICAL_PERMISSIONS_0),
    ("64142c5223c82464aec5cc99f0b42ec74322c0f5c8d109504268772149318ca7", _HISTORICAL_PERMISSIONS_0),
    ("e1a69df098eb7d659a8bd0e7e15769777c6efd337749e44c0282dcb0390e7a99", _HISTORICAL_PERMISSIONS_0),
    ("07e01f2598735b9a5b31f143b9e950f2e627ca0f80e32c954fec77e376e45d24", _HISTORICAL_PERMISSIONS_0),
    ("c3865b28c6f2c86d1105d3fb8bd3c86d17479cad61363df04ce450ac0cbfa75b", _HISTORICAL_PERMISSIONS_0),
    ("73ffca05139a5c108a812f0d77692c5e8742f134d74ef3591d3d0f27d618adfa", _HISTORICAL_PERMISSIONS_0),
    ("c242735d3aa59e823ed9db00968828acd6a3a6a536c5b72d2ca4fbe644ddfd06", _HISTORICAL_PERMISSIONS_0),
    ('964c3311475bbd6f623656cc3385591fef2c8c9ca97d90f3cd7a6ac020c623b7', _HISTORICAL_PERMISSIONS_0),
    ("6342bb7c483657aa9ea5c28af08be9c5bb34a79e773b1ad1d4f65ed759a79f67", _HISTORICAL_PERMISSIONS_0),
    ("54c79836aa7697fbcd0b90f23ac50ca7f17dc3d76ca8de4c133a7c1516042cb9", _HISTORICAL_PERMISSIONS_0),
    ("994dc921adc536bb7c22d1ed48e3b33defcf1e9a0a30b1393d508ce5af85a0b9", _HISTORICAL_PERMISSIONS_0),
    ("1ece3263e7f89f584bbe7a6d0d569ccc1afa6efd44570ae4bb87b4059bb7fc68", _HISTORICAL_PERMISSIONS_0),
    ("c23baea19f9bf94d515bc9672893aee73f2a0d27b399247ee4c1f7c53205f046", _HISTORICAL_PERMISSIONS_0),
    ("941c907ce734ca42bd59424e2e8e6950932e2baf00b4624fe19976718ad7943c", _HISTORICAL_PERMISSIONS_0),
    ("5754c99c3dd2f3ba7a50d9e424dc3aa53012ed1b5d1a2bf8a43afc1bd5bf7f79", _HISTORICAL_PERMISSIONS_0),
    ("524ad7469a09527d61ccd6a5f2daa1972e253aa5e98a835fc1abeb58d7461ed0", _HISTORICAL_PERMISSIONS_0),
    ("30765f5822c80b55b4c354578671cf16335f64a3b7d6548a2f7fa84294cf2f33", _HISTORICAL_PERMISSIONS_0),
    ("0fb2395d521fa24ff95ee716c64ff8ee6aa40e95085ef938de50d716e221caac", _HISTORICAL_PERMISSIONS_0),
    ("7674acb44b4f53dc0c91732d67e1d680e51819ac2bcdac7400821a74ef207173", _HISTORICAL_PERMISSIONS_0),
    ("96a1a33494ebf7af4c4cf2146b124fc75f6f489567f8b2281adce25787f5bc59", _HISTORICAL_PERMISSIONS_0),
    ("b8f52a5af2c1e35e21255eecf4cd23471a4950660b0288a8c85a6aa4f5f8a289", _HISTORICAL_PERMISSIONS_0),
    ("56358083ffe71b6213ad7c5001b2bdbf5f836701e9a76a80fa19a87c15da2cd6", _HISTORICAL_PERMISSIONS_0),
    ("48ed4e2e8a666eb96e5b8898285a4a6915253f3d9704cf8512da519202ebb01e", _HISTORICAL_PERMISSIONS_0),
    ("27d26381421a582b657c98ce7eab0502564feea6d58e242de9449fe39d6dcc02", _HISTORICAL_PERMISSIONS_0),
    ('a527f0292b14d944bab5258a428427836024ac8fc01a9289e623b9817d918df0', _HISTORICAL_PERMISSIONS_0),
    ('ffecf9fa22a8b2991a42274bd803fad88ad43d11c882a2f8a1a36464537f9110', _HISTORICAL_PERMISSIONS_0),
    ('b817180340f3b6200ae45ecd6789af20d2dbc6f66d16ac0ef0979762d8f10384', _HISTORICAL_PERMISSIONS_0),
    ('e37c4246cdbe4b3ad59a9d8ebe92460ed116d0dc54688615b0ea866072072571', _HISTORICAL_PERMISSIONS_0),
    ('84dce7450bcd96e0b0608172652e070bf41c511f08554addf32e43df60fc98f7', _HISTORICAL_PERMISSIONS_0),
    ('567197f08dbc77ff3d965d64a9e175a382815ee1476095620eb7a355ba348dc2', _HISTORICAL_PERMISSIONS_0),
    ('1fc2958cef7ad8cccb87f20fcc239a815552f3994fabcb70f951270471b6aeb6', _HISTORICAL_PERMISSIONS_0),
    ('5ead3d4b797994d57c062e09aefba6a6ee88bcbf63d68453d04e1df242ce692f', _HISTORICAL_PERMISSIONS_0),
    ('1380202f048a6f7dbd006041c18ce6eded8d7c1ecd5522f330af6f2023b0a12c', _HISTORICAL_PERMISSIONS_0),
    ('81b5bb21dad42ef84eda6b72b7ff44c693cc8125a6c699f4c86852fdcccb6f64', _HISTORICAL_PERMISSIONS_0),
    ('11917ae9a497ff2cecd420e45bf770b29c2d0e4b0f3700a5c2a3f9e69cdaa8f0', _HISTORICAL_PERMISSIONS_0),
    ('124e4695fc4a4511086b06c8734c9adbd3c7da014f5813540b1e2a81a71edeb3', _HISTORICAL_PERMISSIONS_0),
    ('16eabc9abc5d141a4ce85bf1f5b28c909c5705b49d7642cec3ed75989f6f8e98', _HISTORICAL_PERMISSIONS_1),
    ('17122016872a4cd771a60716b844ba1abe28d8073baa44fdde33cb2d683f5a92', _HISTORICAL_PERMISSIONS_1),
    ('29b89f37d509bcff9f09317f9c76a62af1309b1e71e8d8ebf9cb84a835c65b55', _HISTORICAL_PERMISSIONS_1),
    ('3bc740723d81d15cac137aa92e3731c4cf5da848a03ff88016e36db9553acd63', _HISTORICAL_PERMISSIONS_1),
    ('44a590388d697fb9d61faaea4e96010d4ccf23ea50577ae9af62e8446e872eb1', _HISTORICAL_PERMISSIONS_1),
    ('58957ed522dbc71491434735ea5581310472af7cf13026fec8e2ca89a9575189', _HISTORICAL_PERMISSIONS_1),
    ('5c99d641474bc81a538463eec6cd619de7d6770a4e3876bef20a1b9ac3bf5656', _HISTORICAL_PERMISSIONS_1),
    ('5e2d5bb1306585f45db4a7d2788791f1c98152b40cff272b2e9d34c5b3b8748d', _HISTORICAL_PERMISSIONS_1),
    ('66c8547a5092ec3665aaa566a75599bf22ba7b634d18e6927ae39b813db03489', _HISTORICAL_PERMISSIONS_1),
    ('6cf0944a817d176d2224a97e971f059314d109c96921e6e94eb8f073d216fa87', _HISTORICAL_PERMISSIONS_1),
    ('6dbedf9d8e953928300425aef493508aa74798b6c0ef6a5d6fec756a3bd7a366', _HISTORICAL_PERMISSIONS_1),
    ('6ee296bc750e68009a24b68b5fbc2202370f0d5a33e9cb47eee4cdec00535ebc', _HISTORICAL_PERMISSIONS_1),
    ('88f79b6038765c0a0932e6bd9893ada5f9d390ec2dd224555c9133182766a851', _HISTORICAL_PERMISSIONS_1),
    ('8a708d1bc25ca8cb42e42a09faa9616f0605eadcb0584f0b8a6f7cdfcf7750bc', _HISTORICAL_PERMISSIONS_1),
    ('8e43f608421f18bdcca447eb0b99c2b676648f8a156a9e4ac0e57a2e093eec7a', _HISTORICAL_PERMISSIONS_1),
    ('97874e99e016627d077ac503e8ab18c017d4444b06e0ee5a2dfb21e37e36b7d1', _HISTORICAL_PERMISSIONS_1),
    ('a1ddac16465b6f950fe345d1caf6a506bcafc0661a2f5b48381a1f59fdafa875', _HISTORICAL_PERMISSIONS_6),
    ('bcba61c83d38f91ee7d9577a4f5c3078504308017c84546145e7948d37b788e7', _HISTORICAL_PERMISSIONS_1),
    ('ce00fc0ce8b1de8ab855fb18451d75d24c1267c78f1a06f144d2e7b14953c461', _HISTORICAL_PERMISSIONS_0),
    ('d49a0313ac599b9db55f6fe7e9eb15bf2dd7df01c4ecb707013b6115b6ac7aaa', _HISTORICAL_PERMISSIONS_1),
    ('e6160457f180870eaf6914bc2b15ff2b18246e721cd6d101e8f2a40a6334c796', _HISTORICAL_PERMISSIONS_1),
    ('e675cf0efcc753ecdcb672bf96e4d0b225ece7f3822fe5d54316fc867b5ed41b', _HISTORICAL_PERMISSIONS_1),
    ('ef836f9cdc6b45d208bf05f406ffb3037864abf78a0e7262d471050a7b394b26', _HISTORICAL_PERMISSIONS_1),
    ('92802201955b3133c9ffb458b99570492a96fe28c7134e0a4eed2724da3223e7', _HISTORICAL_PERMISSIONS_0),
)

def automatic_safety_category(policy_category: str, policy_option: str) -> str:
    """Return the suspension lane for one immutable policy identity."""
    if policy_category == "title":
        # All title elections write the same target through the same lane.
        return "title_layout"
    return policy_category


def _current_qualification_manifest_sha256(
    policy_category: str, policy_option: str
) -> str:
    identity = f"{policy_category}:{policy_option}"
    if identity in F19A_QUALIFIED_POLICY_OPTIONS:
        return F19A_QUALIFICATION_MANIFEST_SHA256
    if identity in F38_QUALIFIED_POLICY_OPTIONS:
        return F38_QUALIFICATION_MANIFEST_SHA256
    if identity in F20_PASSING_AUTOMATIC_OPTIONS:
        return F20_QUALIFICATION_CANDIDATE_SHA256
    # An audiobook identity carries a third segment, so it can never collide
    # with an ebook option above and no prior envelope can speak for it. The
    # envelope is read live, because a tranche is activated by editing that
    # module alone.
    if audiobook_decision_qualification.audiobook_identity(
        policy_category, policy_option
    ) is not None:
        try:
            return audiobook_decision_qualification.qualification_manifest_sha256(
                identity
            )
        except audiobook_decision_qualification.AudiobookQualificationError as error:
            raise AutomaticDecisionError("invalid") from error
    raise AutomaticDecisionError("invalid")


def qualification_manifest_sha256(policy_category: str, policy_option: str) -> str:
    """Return the current manifest used to create a new decision."""
    return _current_qualification_manifest_sha256(policy_category, policy_option)


def qualification_manifest_is_current(
    policy_category: str,
    policy_option: str,
    manifest_sha256: object,
) -> bool:
    """Return whether one persisted decision still carries effect authority."""
    try:
        return type(manifest_sha256) is str and manifest_sha256 == (
            _current_qualification_manifest_sha256(policy_category, policy_option)
        )
    except AutomaticDecisionError:
        return False


def _qualification_manifest_is_valid(
    policy_category: str, policy_option: str, manifest_sha256: str
) -> bool:
    identity = f"{policy_category}:{policy_option}"
    return qualification_manifest_is_current(
        policy_category, policy_option, manifest_sha256
    ) or any(
        manifest_sha256 == prior_digest and identity in qualified_options
        for prior_digest, qualified_options in _PRIOR_F20_QUALIFICATION_MANIFESTS
        + _PRIOR_F19A_QUALIFICATION_MANIFESTS
        + _PRIOR_F46_QUALIFICATION_MANIFESTS
    )


@dataclass(frozen=True, slots=True)
class AutomaticSafetyState:
    """Closed persisted safety posture; suspension never clears by itself."""

    paused: bool
    global_suspension: str | None
    category_suspensions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.paused) is not bool or (
            self.global_suspension is not None
            and (type(self.global_suspension) is not str or not self.global_suspension)
        ) or self.category_suspensions != tuple(sorted(set(self.category_suspensions))) or any(
            type(category) is not str or not category or type(reason) is not str or not reason
            for category, reason in self.category_suspensions
        ):
            raise AutomaticDecisionError("invalid")

    def permits(self, category: str) -> bool:
        return not self.paused and self.global_suspension is None and category not in dict(self.category_suspensions)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AutomaticDecision:
    """A server-authoritative candidate/keep-current or Ignore disposition.

    This is deliberately not a HumanDecision: it has no actor and every input
    that supplied authority is retained in its immutable identity.
    """

    decision_id: str
    review_case_id: str
    report_id: str
    proposal_set_id: str
    proposal_id: str
    target_fingerprint: str
    destination: str
    outcome: str
    candidate_id: str | None
    resolved_policy_id: str
    policy_generation: int
    rule_id: str
    policy_category: str
    policy_option: str
    qualification_manifest_sha256: str
    source_outcome_id: str
    bundle_id: str | None
    idempotency_digest: str
    occurred_at: int

    @classmethod
    def create(
        cls, *, review_case_id: str, report_id: str, proposal_set_id: str,
        proposal_id: str, target_fingerprint: str, destination: str,
        outcome: str, candidate_id: str | None, resolved_policy_id: str,
        policy_generation: int, rule_id: str, policy_category: str,
        policy_option: str, qualification_manifest_sha256: str,
        source_outcome_id: str, bundle_id: str | None, occurred_at: int,
    ) -> "AutomaticDecision":
        if qualification_manifest_sha256 != _current_qualification_manifest_sha256(
            policy_category, policy_option
        ):
            raise AutomaticDecisionError("invalid")
        core = {"candidate_id": candidate_id, "destination": destination,
            "outcome": outcome, "policy_category": policy_category,
            "policy_generation": policy_generation, "policy_option": policy_option,
            "proposal_id": proposal_id, "proposal_set_id": proposal_set_id,
            "qualification_manifest_sha256": qualification_manifest_sha256,
            "report_id": report_id, "resolved_policy_id": resolved_policy_id,
            "review_case_id": review_case_id, "rule_id": rule_id,
            "source_outcome_id": source_outcome_id, "bundle_id": bundle_id,
            "target_fingerprint": target_fingerprint}
        return cls("automatic_decision_" + _digest({**core, "occurred_at": occurred_at}),
            **core, idempotency_digest=_digest(core), occurred_at=occurred_at)

    @classmethod
    def from_json(cls, document: str) -> "AutomaticDecision":
        """Rehydrate one exact canonical persisted automatic decision."""
        try:
            if type(document) is not str:
                raise AutomaticDecisionError("invalid")
            data = json.loads(document)
            expected = {
                "bundle_id", "candidate_id", "decision_id", "destination",
                "idempotency_digest", "occurred_at", "origin",
                "outcome", "policy_category", "policy_generation", "policy_option",
                "proposal_id", "proposal_set_id",
                "qualification_manifest_sha256", "report_id",
                "resolved_policy_id", "review_case_id", "rule_id",
                "schema_version", "source_outcome_id", "target_fingerprint",
            }
            if (
                type(data) is not dict
                or set(data) != expected
                or data.get("origin") != "automatic_policy"
                or data.get("schema_version") != "automatic-decision-v1"
                or _canonical(data) != document
            ):
                raise AutomaticDecisionError("invalid")
            values = dict(data)
            values.pop("origin")
            values.pop("schema_version")
            return cls(**values)
        except AutomaticDecisionError:
            raise
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            raise AutomaticDecisionError("invalid") from None

    def __post_init__(self) -> None:
        valid_id = lambda prefix, value: type(value) is str and re.fullmatch(prefix + r"_[0-9a-f]{64}", value) is not None
        if (
            not valid_id("automatic_decision", self.decision_id)
            or not valid_id("review_case", self.review_case_id)
            or not valid_id("report", self.report_id)
            or not valid_id("proposal_set", self.proposal_set_id)
            or not valid_id("review_proposal", self.proposal_id)
            or not valid_id("shadow_outcome", self.source_outcome_id)
            or not valid_id("shadow_rule", self.rule_id)
            or self.outcome not in {"candidate", "keep_current", "ignore"}
            or (self.outcome == "candidate") != (type(self.candidate_id) is str and re.fullmatch(r"alternative_[0-9a-f]{64}", self.candidate_id) is not None)
            or (self.outcome != "candidate" and self.candidate_id is not None)
            or type(self.policy_generation) is not int or self.policy_generation < 1
            or type(self.occurred_at) is not int or self.occurred_at < 0
            or any(type(v) is not str or not v for v in (self.destination, self.policy_category, self.policy_option))
            or any(re.fullmatch(r"[0-9a-f]{64}", v) is None for v in (self.target_fingerprint, self.qualification_manifest_sha256, self.idempotency_digest))
            or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", self.resolved_policy_id) is None
            or not _qualification_manifest_is_valid(
                self.policy_category,
                self.policy_option,
                self.qualification_manifest_sha256,
            )
            or (self.policy_category in {"title_layout", "date_precision"}
                and (type(self.bundle_id) is not str or re.fullmatch(r"automatic_bundle_[0-9a-f]{64}", self.bundle_id) is None))
            or (self.policy_category not in {"title_layout", "date_precision"} and self.bundle_id is not None
                and not (self.policy_category == "subtitle"
                         and self.policy_option == "corroborated_split:audiobook_subtitle_fill"
                         and type(self.bundle_id) is str
                         and re.fullmatch(r"automatic_bundle_[0-9a-f]{64}", self.bundle_id) is not None))
        ):
            raise AutomaticDecisionError("invalid")
        if self.decision_id != "automatic_decision_" + _digest({**self._core(), "occurred_at": self.occurred_at}) or self.idempotency_digest != _digest(self._core()):
            raise AutomaticDecisionError("invalid")

    def _core(self) -> dict[str, object]:
        return {"bundle_id": self.bundle_id, "candidate_id": self.candidate_id, "destination": self.destination, "outcome": self.outcome, "policy_category": self.policy_category, "policy_generation": self.policy_generation, "policy_option": self.policy_option, "proposal_id": self.proposal_id, "proposal_set_id": self.proposal_set_id, "qualification_manifest_sha256": self.qualification_manifest_sha256, "report_id": self.report_id, "resolved_policy_id": self.resolved_policy_id, "review_case_id": self.review_case_id, "rule_id": self.rule_id, "source_outcome_id": self.source_outcome_id, "target_fingerprint": self.target_fingerprint}

    def to_data(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, **self._core(), "idempotency_digest": self.idempotency_digest, "occurred_at": self.occurred_at, "origin": "automatic_policy", "schema_version": "automatic-decision-v1"}

    def to_json(self) -> str:
        return _canonical(self.to_data())
