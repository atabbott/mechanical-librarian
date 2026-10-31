"""Enlarged text for browser checks that measure layout."""

from __future__ import annotations

from playwright.sync_api import Page

# The root size with no override: the browser default the app never changes.
_DEFAULT_ROOT_PX = 16


def set_root_text(page: Page, size: str) -> None:
    """Set the root font size to `size` ("200%", or "" for the default) and
    return once the root computes to that size and two frames have run.

    A transition on the root's font size would delay its computed size, so a
    check that measured straight after setting it could read the old layout.
    Waiting for the computed size is what proves the check measured enlarged
    text.
    """

    scale = float(size.removesuffix("%")) / 100 if size else 1
    expected = f"{_DEFAULT_ROOT_PX * scale:g}px"
    page.evaluate("size => { document.documentElement.style.fontSize = size }", size)
    page.wait_for_function(
        "px => getComputedStyle(document.documentElement).fontSize === px",
        arg=expected,
        timeout=5_000,
    )
    page.evaluate(
        "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
