"""One run of the #1567 minimal repro: the test's close flow on a static page."""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

HERE = Path(__file__).resolve().parent
PAGE = (HERE / "page.html").read_bytes()
JS = (HERE / "repro.js").read_bytes()
START_Y = int(os.environ.get("REPRO_START_Y", "2788"))
TWO_FRAMES = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
LOG = os.environ.get("REPRO_LOG")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("REPRO_EXE") or None)
    page = browser.new_page(viewport={"width": 390, "height": 844}, locale="en-US")
    def serve(route):
        name = route.request.url.rsplit("/", 1)[-1]
        if name.endswith(".woff2"):
            route.fulfill(content_type="font/woff2", body=(HERE / name).read_bytes())
        elif name.endswith(".js"):
            route.fulfill(content_type="application/javascript", body=JS)
        else:
            route.fulfill(content_type="text/html", body=PAGE)
    page.route("https://repro.invalid/**", serve)
    if LOG:
        page.on("console", lambda m: print("console", m.text[:200], flush=True))
    toggle = page.locator(".attention-toggle")
    rail = page.locator(".attention-rail")
    for closer in ("Close", "toggle"):
        for width in (390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            page.goto("https://repro.invalid/library")
            page.evaluate(f"scrollTo(0, {START_Y})")
            page.evaluate(TWO_FRAMES)
            toggle.click()
            expect(toggle).to_have_attribute("aria-expanded", "true")
            (rail.get_by_role("button", name="Close", exact=True) if closer == "Close" else toggle).click()
            expect(rail).to_have_count(0)
            page.evaluate(TWO_FRAMES)
            print(closer, width, page.evaluate("[scrollY, document.activeElement.className]"), flush=True)
    browser.close()
