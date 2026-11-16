"""One owner search card's preview, fetched and decoded but never stored.

The URL is one an adapter admitted for its own cover host. The bytes are
fetched by the same pinned, no-retry cover transport the pipeline uses and
decoded in the same isolated cover child; only the child's bounded preview is
returned. Nothing reaches the artifact store (#1477 S14).
"""

from __future__ import annotations

from mechanical_librarian.adapters.cover_artifacts import decode_cover
from mechanical_librarian.adapters.providers.remote_cover import fetch_remote_cover
from mechanical_librarian.application.metadata_report import CoverRedirects
from mechanical_librarian.domain.owner_link import OwnerSearchCover


def fetch_owner_preview(cover: OwnerSearchCover, deadline: float) -> bytes:
    """The decoded preview of one card's cover, or an error."""
    remote = fetch_remote_cover(
        cover.url,
        deadline=deadline,
        redirects=(
            CoverRedirects.OPEN_LIBRARY_ARCHIVE
            if cover.archive_redirects
            else CoverRedirects.NONE
        ),
        user_agent=cover.user_agent,
    )
    return decode_cover(remote.content, deadline=deadline).preview


__all__ = ["fetch_owner_preview"]
