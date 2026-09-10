"""Public, aggregate-safe F46 audiobook decision qualification envelope.

This module deliberately contains no candidate values or private evidence. It
names the three audiobook decision identities F45 measured as dominant and pins
which of them a published F46 record has qualified. While the passing set is
empty every audiobook proposal is the owner's exactly as it is today, and a
tranche is activated by three tokens and no wiring change: the manifest file in
`docs/reference/`, its digest here, and its identity in the passing set.

An identity is `category:option:audiobook_reason`. The first two segments are
the owner's existing policy category and option, so the shipped
`OPTION_CATALOGUE` never grows and no persisted profile is invalidated; the
third names the exact audiobook shape the record qualified, so an ebook
qualification of the same option can never speak for a recording, nor a
recording's for an ebook (#967).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

F46_QUALIFICATION_MANIFEST_SHA256: Final[str | None] = None

F46_NARRATORS_IDENTITY: Final = (
    "contributor_names:canonical:audiobook_narrators_fill_empty"
)
F46_SERIES_IDENTITY: Final = "series:primary_narrative:audiobook_fill_empty"
F46_ASIN_IDENTITY: Final = "asin:preserve_exact:audiobook_gate_corroborated"

F46_AUDIOBOOK_IDENTITIES: Final[frozenset[str]] = frozenset(
    {F46_NARRATORS_IDENTITY, F46_SERIES_IDENTITY, F46_ASIN_IDENTITY}
)
# One destination per identity. A destination absent from this map has no
# audiobook qualification route at all, so on a recording it is always the
# owner's.
F46_IDENTITY_BY_DESTINATION: Final[Mapping[str, str]] = MappingProxyType(
    {
        "asin": F46_ASIN_IDENTITY,
        "narrators": F46_NARRATORS_IDENTITY,
        "series": F46_SERIES_IDENTITY,
    }
)
F46_PASSING_AUTOMATIC_OPTIONS: Final[frozenset[str]] = frozenset()


class AudiobookQualificationError(ValueError):
    """An audiobook identity is outside the published F46 envelope."""


def audiobook_identity(policy_category: object, policy_option: object) -> str | None:
    """Return the F46 identity one category/option pair states, if any."""
    if type(policy_category) is not str or type(policy_option) is not str:
        return None
    identity = f"{policy_category}:{policy_option}"
    return identity if identity in F46_AUDIOBOOK_IDENTITIES else None


def qualified_policy_bases() -> frozenset[str]:
    """Return the `category:option` bases a record currently qualifies.

    This is what the owner may route to Automatic, so it is read live rather
    than frozen at import: activating a tranche must not require a second
    edit anywhere else.
    """
    return frozenset(
        identity.rsplit(":", 1)[0] for identity in F46_PASSING_AUTOMATIC_OPTIONS
    )


def qualification_manifest_sha256(identity: object) -> str:
    """Return the record digest one qualified audiobook identity binds."""
    digest = F46_QUALIFICATION_MANIFEST_SHA256
    if identity not in F46_PASSING_AUTOMATIC_OPTIONS or type(digest) is not str:
        raise AudiobookQualificationError("audiobook qualification is unavailable")
    return digest


def public_summary() -> tuple[dict[str, object], ...]:
    """Describe every F46 tranche without disclosing qualification cases.

    The identity grammar is this module's, so the split is made here: `field`
    and `option` are the owner's existing policy category and option, which is
    what a routing surface asks about, while `identity` and `destination` name
    the audiobook shape a record qualified and the field it decides. An
    unqualified tranche states itself unavailable and names no digest.
    """
    return tuple(
        {
            "automatic_available": identity in F46_PASSING_AUTOMATIC_OPTIONS,
            "destination": destination,
            "field": field,
            "identity": identity,
            "option": option,
            "record_sha256": (
                F46_QUALIFICATION_MANIFEST_SHA256
                if identity in F46_PASSING_AUTOMATIC_OPTIONS
                else None
            ),
        }
        for destination, identity in sorted(F46_IDENTITY_BY_DESTINATION.items())
        for field, option in (identity.rsplit(":", 1)[0].split(":", 1),)
    )


__all__ = [
    "F46_ASIN_IDENTITY",
    "F46_AUDIOBOOK_IDENTITIES",
    "F46_IDENTITY_BY_DESTINATION",
    "F46_NARRATORS_IDENTITY",
    "F46_PASSING_AUTOMATIC_OPTIONS",
    "F46_QUALIFICATION_MANIFEST_SHA256",
    "F46_SERIES_IDENTITY",
    "AudiobookQualificationError",
    "audiobook_identity",
    "public_summary",
    "qualification_manifest_sha256",
    "qualified_policy_bases",
]
