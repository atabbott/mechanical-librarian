"""Timing scan for #1567's minimal repro: open, then close after k ms, in-page."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PAGE = (HERE / "page.html").read_bytes()
JS = (HERE / "repro.js").read_bytes()
START_Y = int(os.environ.get("REPRO_START_Y", "2788"))
SETTLE = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50))))"

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
    for width in (390, 320):
        page.set_viewport_size({"width": width, "height": 844})
        page.goto("https://repro.invalid/library")
        for k in range(0, 48, 2):
            page.evaluate(f"scrollTo(0, {START_Y})")
            page.evaluate(SETTLE)
            page.evaluate(f"__repro.open(); setTimeout(__repro.close, {k})")
            page.evaluate(SETTLE)
        print("scan", width, "ok", flush=True)
    browser.close()
