"""Static authenticated review frontend.

The browser remains a presentation layer over the bounded, server-authoritative
review APIs.  This module deliberately contains no persistence or target-service
integration.
"""

from __future__ import annotations

import html


def _shell(*, title: str, active: str, main: str, csrf_token: str | None) -> str:
    csrf = "" if csrf_token is None else html.escape(csrf_token, quote=True)
    brand_open = (
        '<span class="brand">'
        if active == "login"
        else '<a class="brand" href="/library" aria-label="Mechanical Librarian review library">'
    )
    brand_close = "</span>" if active == "login" else "</a>"
    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/assets/review.css">
</head>
<body data-csrf-token="{csrf}">
  <a class="skip-link" href="#workspace">Skip to review workspace</a>
  <header class="topbar">
    {brand_open}
      <span class="brand-mark" aria-hidden="true">ML</span>
      <span><strong>Mechanical Librarian</strong><small>Private review workspace</small></span>
    {brand_close}
    <div class="topbar-actions">
      {'' if csrf_token is None else '<span class="private-pill"><span aria-hidden="true">●</span> Owner session</span>'}
      {'' if csrf_token is None else '<button class="activity-button" type="button" aria-expanded="false" aria-controls="activity-popover" hidden><span class="activity-pulse" aria-hidden="true"></span><span class="activity-label">Background activity</span></button>'}
      <button class="theme-toggle" type="button" aria-pressed="false">
        <span aria-hidden="true">◐</span><span class="theme-label">Dark mode</span>
      </button>
      {'' if csrf_token is None else '<button class="sign-out" type="button">Sign out</button>'}
    </div>
  </header>
  {'' if csrf_token is None else _navigation(active)}
  {main}
  {'' if csrf_token is None else '<section id="activity-popover" class="activity-popover" aria-label="Background activity" hidden></section>'}
  <p class="visually-hidden" id="theme-status" role="status" aria-live="polite"></p>
  <script src="/assets/review.js" defer></script>
</body>
</html>"""


def _navigation(active: str) -> str:
    library = ' aria-current="page"' if active == "library" else ""
    attention = ' aria-current="page"' if active == "attention" else ""
    detail = ' aria-current="page"' if active == "detail" else ""
    policy = ' aria-current="page"' if active == "policy" else ""
    return f"""<nav class="workspace-navigation" aria-label="Review workspace">
  <div class="workspace-navigation-inner">
    <a class="workspace-tab" href="/library"{library}>Library</a>
    <a class="workspace-tab attention-tab" href="/attention"{attention}>Attention</a>
    <a class="workspace-tab" href="/policy"{policy}>Policy</a>
    <span class="workspace-tab detail-tab"{detail}>Detail</span>
    <span class="workspace-context">Human exception review · exact-current records</span>
  </div>
</nav>"""


def login_html() -> str:
    return _shell(
        title="Owner sign in",
        active="login",
        csrf_token=None,
        main="""<main id="workspace" class="login-page">
  <section class="login-card" aria-labelledby="login-title">
    <p class="eyebrow">Private owner access</p>
    <h1 id="login-title">Review your library with confidence.</h1>
    <p class="lede">Sign in to inspect exact-current exception cases. Nothing is written to your library from this workspace.</p>
    <form id="login">
      <label for="password">Owner password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button class="primary-button" type="submit">Sign in</button>
      <p id="login-status" role="alert"></p>
    </form>
  </section>
</main>""",
    )


def library_html(csrf_token: str, preparation_available: bool = False) -> str:
    capability = ' data-review-preparation="true"' if preparation_available else ""
    return _shell(
        title="Review library",
        active="library",
        csrf_token=csrf_token,
        main=f"""<main id="workspace" class="prototype-shell">
  <section id="library" class="library-page" tabindex="-1"{capability}>
    <p class="loading" role="status"><span aria-hidden="true"></span> Loading bounded library…</p>
  </section>
</main>""",
    )


def attention_html(csrf_token: str) -> str:
    return _shell(
        title="Attention queue",
        active="attention",
        csrf_token=csrf_token,
        main="""<main id="workspace" class="prototype-shell">
  <section id="attention" class="attention-page" tabindex="-1">
    <p class="loading" role="status"><span aria-hidden="true"></span> Loading bounded attention queue…</p>
  </section>
</main>""",
    )


def policy_html(csrf_token: str) -> str:
    """The selected B notebook is intentionally a policy-only surface."""
    return _shell(
        title="Metadata policy",
        active="policy",
        csrf_token=csrf_token,
        main="""<main id="workspace" class="prototype-shell">
  <section class="policy-page" aria-labelledby="policy-title" tabindex="-1">
    <p class="eyebrow">Metadata policy · B notebook</p>
    <h1 id="policy-title">Choose how prepared metadata reaches you</h1>
    <p class="lede">The built-in policy is active. Draft previews are read-only: they do not alter evidence, proposals, the queue, or your library.</p>
    <div class="policy-notebook" aria-label="Policy notebook">
      <nav class="policy-chapters" aria-label="Policy chapters">
        <a href="#identity">Identity</a><a href="#bibliography">Bibliography</a><a href="#descriptions">Descriptions</a><a href="#availability">Availability</a>
      </nav>
      <section id="identity" class="policy-card"><p class="eyebrow">Active · built-in</p><h2>Calm, conservative defaults</h2><p>Title, date, contributors, language, publisher, identifiers, genres, tags, description, cover order, and Explicit use named options only.</p></section>
      <section id="bibliography" class="policy-card"><h2>Draft before activation</h2><p>A draft is immutable and versioned. It can be validated and previewed, but cannot become active in this view.</p></section>
      <section id="descriptions" class="policy-card"><h2>Per-library effects</h2><p>Configured sparse overrides are resolved with the global draft. Preview names every affected category and current unapplied count.</p></section>
      <section id="availability" class="policy-card"><h2>Qualification availability</h2><p>Automatic is unavailable until qualified. Ask me and Ignore remain manual-only. Explicit is unsupported.</p></section>
    </div>
  </section>
</main>""",
    )


def review_html(review_case_id: str, csrf_token: str) -> str:
    case_id = html.escape(review_case_id, quote=True)
    token = html.escape(csrf_token, quote=True)
    return _shell(
        title="Review case",
        active="detail",
        csrf_token=csrf_token,
        main=f"""<main id="workspace" class="prototype-shell">
  <section id="review" class="detail-page" data-review-case-id="{case_id}" data-csrf-token="{token}" tabindex="-1">
    <p class="loading" role="status"><span aria-hidden="true"></span> Loading exact-current case…</p>
  </section>
</main>""",
    )


REVIEW_CSS = r"""
:root {
  --ink: #17201e;
  --ink-soft: #56615e;
  --paper: #f4f2ec;
  --surface: #fbfaf6;
  --surface-strong: #fff;
  --line: #d9d7cf;
  --line-strong: #b9beb9;
  --accent: #1b6558;
  --accent-strong: #0f4c42;
  --accent-soft: #dcebe6;
  --notice: #8a5a16;
  --notice-soft: #f4e7cf;
  --danger: #8c3434;
  --danger-soft: #f7e4e1;
  --focus: #146fba;
  --on-accent: #fff;
  --shadow: 0 18px 52px rgb(30 43 38 / .12);
  --radius: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
  color-scheme: light;
}

:root[data-theme="dark"] {
  --ink: #edf2ee;
  --ink-soft: #a9b6b0;
  --paper: #0f1512;
  --surface: #151d19;
  --surface-strong: #1b2520;
  --line: #2d3933;
  --line-strong: #4a5a52;
  --accent: #61b29f;
  --accent-strong: #8ed2c1;
  --accent-soft: #203b34;
  --notice: #efbd70;
  --notice-soft: #3b2d1b;
  --danger: #f39b9b;
  --danger-soft: #402527;
  --focus: #78bdf2;
  --on-accent: #0f1512;
  --shadow: 0 18px 52px rgb(0 0 0 / .28);
  color-scheme: dark;
}

* { box-sizing: border-box; }
html { overflow-x: hidden; background: var(--paper); }
body { margin: 0; min-width: 0; background: var(--paper); color: var(--ink); font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; overflow-wrap: anywhere; }
button, input { min-height: 44px; font: inherit; }
button, a { -webkit-tap-highlight-color: transparent; }
button { color: inherit; }
a { color: var(--accent-strong); }
h1, h2, h3, p { margin-top: 0; }
h1, h2 { font-family: Georgia, "Times New Roman", serif; letter-spacing: -.025em; }
h1 { font-size: clamp(1.75rem, 2.8vw, 2.75rem); line-height: 1.08; }
h2 { font-size: 1.18rem; line-height: 1.2; }
h3 { font-size: .76rem; }
pre { max-width: 100%; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.visually-hidden { position: absolute !important; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.skip-link { position: fixed; z-index: 100; top: 8px; left: 8px; padding: 10px 14px; transform: translateY(-150%); border-radius: var(--radius); background: var(--surface-strong); }
.skip-link:focus { transform: none; }

.topbar { position: sticky; z-index: 20; top: 0; display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 16px; padding: 8px clamp(12px, 3vw, 38px); border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--surface) 94%, transparent); backdrop-filter: blur(14px); }
.brand { display: inline-flex; min-width: 0; align-items: center; gap: 11px; color: var(--ink); text-decoration: none; }
.brand-mark, .work-mark { display: grid; flex: 0 0 auto; place-items: center; border: 1px solid var(--accent); background: var(--accent-soft); color: var(--accent-strong); font-family: Georgia, serif; font-weight: 700; }
.brand-mark { width: 40px; height: 40px; border-radius: 50%; font-size: .76rem; }
.brand strong, .brand small { display: block; }
.brand strong { font-family: Georgia, serif; font-size: .93rem; }
.brand small { color: var(--ink-soft); font-size: .58rem; letter-spacing: .04em; text-transform: uppercase; }
.topbar-actions { display: flex; align-items: center; gap: 9px; }
.private-pill { color: var(--ink-soft); font-size: .67rem; }
.private-pill span { color: var(--accent); }
.theme-toggle, .sign-out { min-height: 40px; padding: 0 12px; border: 1px solid var(--line-strong); border-radius: 999px; background: var(--surface-strong); cursor: pointer; font-size: .68rem; font-weight: 700; }
.theme-toggle:hover, .sign-out:hover { border-color: var(--accent); background: var(--accent-soft); }

.workspace-navigation { position: sticky; z-index: 15; top: 64px; border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--surface) 94%, transparent); backdrop-filter: blur(14px); }
.workspace-navigation-inner { display: flex; max-width: 1480px; min-height: 50px; align-items: center; gap: 6px; margin: auto; padding: 5px clamp(12px, 3vw, 38px); }
.workspace-tab { display: inline-flex; min-height: 38px; align-items: center; padding: 0 14px; border-radius: var(--radius); color: var(--ink-soft); font-size: .7rem; font-weight: 800; text-decoration: none; }
.workspace-tab[aria-current="page"] { background: var(--accent-soft); color: var(--accent-strong); }
.detail-tab:not([aria-current="page"]) { display: none; }
.workspace-context { margin-left: auto; color: var(--ink-soft); font-size: .61rem; }

.prototype-shell { width: 100%; max-width: 1540px; margin: auto; padding: 22px clamp(12px, 2.4vw, 30px) 70px; }
.library-page:focus-visible, .attention-page:focus-visible, .detail-page:focus-visible { outline: none; }
.page-heading { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 18px; margin-bottom: 16px; }
.page-heading h1 { margin-bottom: 6px; }
.page-heading p { max-width: 66ch; margin-bottom: 0; color: var(--ink-soft); }
.eyebrow { margin-bottom: 6px; color: var(--accent-strong); font-size: .61rem; font-weight: 900; letter-spacing: .11em; text-transform: uppercase; }
.count-cluster { display: flex; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface); }
.count-cluster span { min-width: 86px; padding: 8px 12px; border-right: 1px solid var(--line); text-align: center; }
.count-cluster span:last-child { border-right: 0; }
.count-cluster strong, .count-cluster small { display: block; }
.count-cluster strong { font-family: Georgia, serif; font-size: 1.2rem; }
.count-cluster small { color: var(--ink-soft); font-size: .55rem; text-transform: uppercase; }

.library-tools { display: grid; grid-template-columns: minmax(260px, 1fr) auto; align-items: end; gap: 12px; margin-bottom: 14px; }
.activity-button { display: inline-flex; min-height: 40px; align-items: center; gap: 8px; padding: 0 12px; border: 1px solid var(--accent); border-radius: 999px; background: var(--accent-soft); color: var(--accent-strong); cursor: pointer; font-size: .62rem; font-weight: 850; }
.activity-button[hidden], .activity-popover[hidden] { display: none; }
.activity-button.actionable { border-color: var(--notice); background: var(--notice-soft); color: var(--notice); }
.activity-pulse { width: 8px; height: 8px; flex: none; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 16%, transparent); }
.activity-popover { position: fixed; z-index: 55; top: 72px; right: clamp(12px, 3vw, 38px); width: min(390px, calc(100vw - 24px)); max-height: calc(100vh - 90px); overflow: auto; padding: 16px; border: 1px solid var(--line-strong); border-radius: var(--radius-lg); background: var(--surface-strong); box-shadow: var(--shadow); }
.activity-popover h2 { margin-bottom: 5px; }
.activity-library { display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 4px 12px; padding: 10px 0; border-top: 1px solid var(--line); font-size: .66rem; }
.activity-library small { color: var(--ink-soft); }
.activity-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.scan-now { min-height: 44px; padding: 0 13px; border: 1px solid var(--accent); border-radius: var(--radius); background: var(--surface-strong); color: var(--accent-strong); cursor: pointer; font-size: .66rem; font-weight: 800; }
.scan-now:disabled { cursor: wait; opacity: .62; }
.search-form { display: flex; align-items: end; gap: 8px; }
.search-form label { flex: 1; color: var(--ink-soft); font-size: .62rem; font-weight: 800; text-transform: uppercase; }
.search-form input { display: block; width: 100%; margin-top: 5px; padding: 0 12px; border: 1px solid var(--line-strong); border-radius: var(--radius); background: var(--surface-strong); color: var(--ink); }
.search-form button, .secondary-button, .next, .recovery { display: inline-flex; min-height: 44px; align-items: center; justify-content: center; padding: 0 14px; border: 1px solid var(--accent); border-radius: var(--radius); background: var(--surface-strong); color: var(--accent-strong); cursor: pointer; font-size: .7rem; font-weight: 800; text-decoration: none; }
.lenses { display: flex; flex-wrap: wrap; gap: 6px; }
.lenses a { display: inline-flex; min-height: 40px; align-items: center; padding: 0 11px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface); color: var(--ink-soft); font-size: .64rem; font-weight: 800; text-decoration: none; }
.lenses a[aria-current="page"] { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-strong); }

.library-workspace { display: grid; grid-template-columns: minmax(360px, .88fr) minmax(520px, 1.12fr); min-height: 610px; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius-lg); background: var(--surface); box-shadow: var(--shadow); }
.work-list { min-width: 0; border-right: 1px solid var(--line); }
.work-list-header { display: flex; min-height: 44px; align-items: center; justify-content: space-between; padding: 0 14px; border-bottom: 1px solid var(--line); color: var(--ink-soft); font-size: .61rem; font-weight: 800; text-transform: uppercase; }
.work-row { display: grid; min-height: 92px; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 11px 14px; border-bottom: 1px solid var(--line); color: var(--ink); text-decoration: none; }
.work-row:hover, .work-row[aria-current="true"] { background: var(--accent-soft); }
.work-mark { width: 38px; height: 48px; border-radius: 4px 8px 8px 4px; font-size: .67rem; }
.work-copy { min-width: 0; }
.work-copy strong, .work-copy small { display: block; }
.work-copy strong { margin-bottom: 4px; font-family: Georgia, serif; font-size: .87rem; }
.work-copy small { color: var(--ink-soft); font-size: .62rem; }
.work-state { text-align: right; }
.badge { display: inline-flex; min-height: 24px; align-items: center; gap: 5px; padding: 0 8px; border: 1px solid var(--notice); border-radius: 999px; background: var(--notice-soft); color: var(--notice); font-size: .56rem; font-weight: 900; }
.badge.automatic { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-strong); }
.work-state small { display: block; margin-top: 5px; color: var(--ink-soft); font-size: .56rem; }
.workspace-preview { display: grid; min-width: 0; align-content: center; padding: clamp(24px, 4vw, 58px); background: linear-gradient(145deg, var(--surface), var(--surface-strong)); }
.preview-book { width: 56px; height: 72px; margin-bottom: 18px; }
.workspace-preview h2 { max-width: 18ch; font-size: clamp(1.5rem, 2.5vw, 2.3rem); }
.workspace-preview p { max-width: 48ch; color: var(--ink-soft); }
.pagination { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 14px; }
.pagination p { margin: 0; color: var(--ink-soft); font-size: .62rem; }

.detail-shell { display: grid; grid-template-columns: minmax(280px, .68fr) minmax(520px, 1.32fr); overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius-lg); background: var(--surface); box-shadow: var(--shadow); }
.case-sidebar { padding: 24px; border-right: 1px solid var(--line); background: linear-gradient(165deg, var(--surface-strong), var(--surface)); }
.case-sidebar h1 { margin-bottom: 8px; }
.case-identity { margin-bottom: 20px; color: var(--ink-soft); font-size: .68rem; }
.position { display: flex; align-items: baseline; gap: 7px; margin: 18px 0; padding: 12px 0; border-block: 1px solid var(--line); }
.position strong { font-family: Georgia, serif; font-size: 1.55rem; }
.position span { color: var(--ink-soft); font-size: .65rem; }
.field-navigation { display: grid; gap: 6px; }
.field-navigation a { display: grid; min-height: 48px; grid-template-columns: 9px minmax(0, 1fr) auto; align-items: center; gap: 9px; padding: 7px 9px; border-radius: var(--radius); color: var(--ink); font-size: .68rem; font-weight: 800; text-decoration: none; }
.field-navigation a:hover, .field-navigation a[aria-current="true"] { background: var(--accent-soft); }
.attention-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--notice); }
.field-navigation small { color: var(--ink-soft); font-size: .56rem; }
.detail-content { min-width: 0; padding: clamp(20px, 3vw, 36px); }
.detail-content > section, .detail-content > details { margin-bottom: 20px; }
.toast, .decision-error, .decision-recorded { padding: 12px 14px; border: 1px solid var(--accent); border-radius: var(--radius-md); background: var(--accent-soft); }
.decision-error { border-color: var(--danger); background: var(--danger-soft); }
.field-strip { display: grid; grid-auto-flow: column; grid-auto-columns: 100%; gap: 16px; overflow-x: auto; overscroll-behavior-inline: contain; scroll-snap-type: x mandatory; scrollbar-color: var(--accent) var(--line); }
.field { min-width: 0; scroll-snap-align: start; }
.field-heading { display: flex; align-items: start; justify-content: space-between; gap: 12px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.field-heading h2 { margin-bottom: 4px; font-size: 1.45rem; }
.field-heading p { margin: 0; color: var(--ink-soft); font-size: .64rem; }
.comparison { display: grid; grid-template-columns: 1fr auto 1fr; align-items: stretch; gap: 10px; margin: 16px 0; }
.value-card { min-width: 0; min-height: 126px; padding: 16px; border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface-strong); }
.value-card.proposed { border-color: var(--accent); background: var(--accent-soft); }
.value-card small { display: block; margin-bottom: 9px; color: var(--ink-soft); font-size: .55rem; font-weight: 900; letter-spacing: .08em; text-transform: uppercase; }
.value-card strong { display: block; font-family: Georgia, serif; font-size: 1rem; }
.command-arrow { align-self: center; color: var(--accent); font-size: 1.25rem; }
.field-meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 15px; }
.field-meta span { padding: 9px; border: 1px solid var(--line); border-radius: var(--radius); color: var(--ink-soft); font-size: .58rem; }
.field-meta strong { display: block; margin-bottom: 3px; color: var(--ink); font-size: .61rem; }
.choices { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0; padding: 0; border: 0; }
.choices legend { margin-bottom: 8px; font-size: .66rem; font-weight: 900; }
.choices button { min-width: 0; padding: 10px; border: 1px solid var(--accent); border-radius: var(--radius-md); background: var(--surface-strong); cursor: pointer; font-size: .68rem; font-weight: 800; }
.choices button:first-of-type { background: var(--accent); color: var(--on-accent); }
.choices button.reject { border-color: var(--danger); color: var(--danger); }
.choices button:hover { transform: translateY(-1px); box-shadow: 0 7px 18px rgb(0 0 0 / .08); }
.choices button:disabled { opacity: .6; transform: none; }
.decision-status { min-height: 28px; margin: 9px 0 0; color: var(--ink-soft); font-size: .63rem; }
.automatic-context, .ledger, .history, .model-advice { border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface-strong); }
.automatic-context > summary, .ledger > summary, .history > summary, .model-advice > summary { min-height: 50px; padding: 14px; cursor: pointer; font-family: Georgia, serif; font-weight: 700; }
.details-inner { padding: 0 14px 14px; }
.resolved-field { padding: 11px 0; border-top: 1px solid var(--line); }
.resolved-field h3 { margin-bottom: 4px; }
.resolved-field p { margin: 0; color: var(--ink-soft); font-size: .62rem; }
.ledger-items { display: grid; gap: 8px; }
.evidence-item { padding: 11px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.evidence-item h3 { margin-bottom: 6px; color: var(--accent-strong); }
.history-row { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; padding: 10px 0; border-top: 1px solid var(--line); font-size: .65rem; }
.history-row span:first-child { display: grid; width: 24px; height: 24px; place-items: center; border-radius: 50%; background: var(--accent-soft); color: var(--accent-strong); font-weight: 900; }
.advice-card { padding: 12px 0; border-top: 1px solid var(--line); }
.advice-card p { margin-bottom: 6px; color: var(--ink-soft); font-size: .64rem; }
.advice-card strong { color: var(--ink); }
.loading { display: flex; min-height: 420px; align-items: center; justify-content: center; gap: 10px; color: var(--ink-soft); }
.loading span { width: 18px; height: 18px; border: 2px solid var(--line); border-top-color: var(--accent); border-radius: 50%; animation: spin .8s linear infinite; }
.empty-state, .error-state { padding: 42px; border: 1px solid var(--line); border-radius: var(--radius-lg); background: var(--surface); text-align: center; }
.error-state { border-color: var(--danger); }

.login-page { display: grid; min-height: calc(100vh - 64px); place-items: center; padding: 24px; }
.login-card { width: min(560px, 100%); padding: clamp(26px, 5vw, 54px); border: 1px solid var(--line); border-radius: var(--radius-lg); background: var(--surface); box-shadow: var(--shadow); }
.login-card .lede { color: var(--ink-soft); }
.login-card label { display: block; margin-top: 24px; font-size: .68rem; font-weight: 800; }
.login-card input { width: 100%; margin: 6px 0 12px; padding: 0 12px; border: 1px solid var(--line-strong); border-radius: var(--radius); background: var(--surface-strong); color: var(--ink); }
.primary-button { width: 100%; border: 1px solid var(--accent); border-radius: var(--radius); background: var(--accent); color: var(--on-accent); cursor: pointer; font-weight: 800; }
#login-status { min-height: 24px; margin: 9px 0 0; color: var(--danger); }

.policy-page { max-width: 1080px; margin: 0 auto; }
.policy-page .lede { max-width: 720px; color: var(--ink-soft); }
.policy-notebook { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 18px; margin-top: 26px; }
.policy-chapters { position: sticky; top: 132px; align-self: start; display: grid; gap: 3px; padding: 9px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.policy-chapters a { padding: 10px; border-radius: 8px; color: var(--ink-soft); font-weight: 750; text-decoration: none; }
.policy-chapters a:focus-visible, .policy-chapters a:hover { background: var(--surface-strong); color: var(--accent); outline: 2px solid var(--accent); outline-offset: 1px; }
.policy-card { margin-bottom: 12px; padding: clamp(18px, 3vw, 30px); border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface); }
.policy-card h2 { margin-top: 0; }

@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; transition-duration: .01ms !important; } }

@media (max-width: 1024px) {
  .library-workspace, .detail-shell { grid-template-columns: 1fr; }
  .work-list, .case-sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
  .workspace-preview { min-height: 290px; }
  .case-sidebar { padding: 20px; }
  .field-navigation { display: flex; overflow-x: auto; padding-bottom: 5px; }
  .field-navigation a { flex: 0 0 auto; min-width: 170px; }
  .policy-notebook { grid-template-columns: 1fr; }
  .policy-chapters { position: sticky; top: 118px; grid-auto-flow: column; grid-auto-columns: max-content; overflow-x: auto; }
}

@media (max-width: 700px) {
  .activity-button { width: 42px; justify-content: center; padding: 0; }
  .activity-label { display: none; }
  .topbar { min-height: 56px; padding: 7px 10px; }
  .brand-mark { width: 36px; height: 36px; }
  .brand strong, .brand small, .private-pill { display: none; }
  .sign-out { display: inline-flex; padding: 0 9px; }
  .theme-toggle { min-width: 44px; padding: 0 9px; }
  .theme-label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .workspace-navigation { top: 56px; }
  .workspace-navigation-inner { min-height: 54px; padding: 6px 10px; }
  .workspace-tab { min-height: 40px; padding: 0 10px; font-size: .64rem; }
  .workspace-context { display: none; }
  .prototype-shell { padding: 14px 10px 80px; }
  .page-heading { grid-template-columns: 1fr; }
  .count-cluster { width: 100%; }
  .count-cluster span { flex: 1; min-width: 0; }
  .library-tools { grid-template-columns: 1fr; }
  .search-form { align-items: stretch; }
  .library-workspace, .detail-shell { border-radius: var(--radius-md); box-shadow: none; }
  .workspace-preview { display: none; }
  .work-row { min-height: 88px; grid-template-columns: 38px minmax(0, 1fr); padding: 10px; }
  .work-mark { width: 34px; height: 44px; }
  .work-state { grid-column: 2; text-align: left; }
  .work-state small { display: inline; margin-left: 5px; }
  .detail-content { padding: 18px 14px; }
  .comparison { grid-template-columns: 1fr; }
  .command-arrow { justify-self: center; transform: rotate(90deg); }
  .choices { grid-template-columns: 1fr; }
  .field-meta { grid-template-columns: 1fr; }
}

@media (max-width: 360px) {
  .attention-tab { padding-inline: 8px; }
  .detail-tab[aria-current="page"] { padding-inline: 8px; }
  .lenses { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 4px; }
  .lenses a { flex: 0 0 auto; }
  .search-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
  .search-form label { min-width: 0; }
}
"""


REVIEW_JS = r"""
const element = (name, text, className) => {
  const value = document.createElement(name);
  if (text !== undefined && text !== null) value.textContent = text;
  if (className) value.className = className;
  return value;
};
const format = value => value === null ? "Not set" : Array.isArray(value) ? value.map(format).join(" · ") : typeof value === "object" ? JSON.stringify(value) : String(value);
const initials = title => title.split(/\s+/).filter(Boolean).slice(0, 2).map(word => word[0].toUpperCase()).join("") || "ML";
const friendly = value => String(value).replaceAll("_", " ");

const themeMatch = document.cookie.match(/(?:^|; )ml_theme=(light|dark)(?:;|$)/);
let theme = themeMatch ? themeMatch[1] : "light";
const applyTheme = (announce = false) => {
  document.documentElement.dataset.theme = theme;
  const toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.setAttribute("aria-pressed", String(theme === "dark"));
    toggle.setAttribute("aria-label", `${theme === "dark" ? "Dark" : "Light"} mode selected. Switch to ${theme === "dark" ? "light" : "dark"} mode.`);
    const label = toggle.querySelector(".theme-label");
    if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  }
  if (announce) document.querySelector("#theme-status").textContent = `${theme === "dark" ? "Dark" : "Light"} mode selected.`;
};
applyTheme();
document.querySelector(".theme-toggle")?.addEventListener("click", () => {
  theme = theme === "dark" ? "light" : "dark";
  document.cookie = `ml_theme=${theme}; Path=/; Max-Age=31536000; SameSite=Strict; Secure`;
  applyTheme(true);
});
addEventListener("pageshow", event => { if (event.persisted) location.reload(); });

const csrfFromPage = () => {
  const token = document.body.dataset.csrfToken;
  document.body.removeAttribute('data-csrf-token');
  return token;
};
let pageCsrf = csrfFromPage();
const refreshPageCsrf = async () => {
  const response = await fetch(location.href, {cache: "no-store"});
  if (!response.ok) {
    const error = new Error();
    error.loginRequired = response.status === 401;
    throw error;
  }
  const detached = new DOMParser().parseFromString(await response.text(), "text/html");
  const token = detached.body?.dataset.csrfToken;
  detached.body?.removeAttribute('data-csrf-token');
  if (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token)) throw new Error();
  return token;
};
document.querySelector(".sign-out")?.addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const send = () => fetch("/api/v1/session/logout", {method: "POST", headers: {"x-csrf-token": pageCsrf}});
    let response = await send();
    if (response.status === 403) {
      pageCsrf = await refreshPageCsrf();
      response = await send();
    }
    if (response.status === 401) { location.assign("/login"); return; }
    if (!response.ok) throw new Error();
    location.assign("/login");
  } catch (error) {
    if (error.loginRequired) { location.assign("/login"); return; }
    button.disabled = false;
    button.textContent = "Sign out failed";
  }
});

const login = document.querySelector("#login");
if (login) login.addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.querySelector("#login-status");
  const submit = login.querySelector("button");
  submit.disabled = true;
  status.textContent = "Signing in…";
  try {
    const password = new FormData(login).get("password");
    const response = await fetch("/api/v1/session/login", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({password})});
    if (!response.ok) throw new Error();
    location.assign("/library");
  } catch {
    submit.disabled = false;
    status.textContent = "Sign in failed. Check the owner password and try again.";
  }
});

const searchForm = (query, lens) => {
  const form = element("form", null, "search-form");
  form.method = "get";
  if (lens) {
    const selected = element("input");
    selected.type = "hidden";
    selected.name = "lens";
    selected.value = lens;
    form.append(selected);
  }
  const label = element("label", "Search title, library, or item");
  const input = element("input");
  input.name = "q";
  input.type = "search";
  input.value = query || "";
  input.maxLength = 128;
  label.append(input);
  form.append(label, element("button", "Search"));
  return form;
};

const load = async path => {
  const response = await fetch(path, {cache: "no-store"});
  if (!response.ok) {
    const error = new Error();
    error.status = response.status;
    throw error;
  }
  return response.json();
};
const sendPreparationAction = async path => {
  const send = () => fetch(path, {method: "POST", headers: {"x-csrf-token": pageCsrf}});
  let response = await send();
  if (response.status === 403) { pageCsrf = await refreshPageCsrf(); response = await send(); }
  if (!response.ok) throw new Error();
  return response.json();
};
const activityButton = document.querySelector(".activity-button");
const activityPopover = document.querySelector(".activity-popover");
const renderActivity = data => {
  if(!activityButton||!activityPopover)return;
  const focusedAction = activityPopover.contains(document.activeElement) ? document.activeElement.dataset.actionKey : null;
  const visible=data.active||data.actionable||data.paused;
  if (!visible && (activityButton === document.activeElement || activityPopover.contains(document.activeElement))) {
    const focusTarget = document.querySelector('.lenses a[aria-current="page"]') || document.querySelector('.workspace-tab[aria-current="page"][href]') || document.querySelector('.workspace-tab[href]');
    focusTarget?.focus({preventScroll: true});
  }
  activityButton.hidden = !visible;
  activityButton.classList.toggle("actionable", data.actionable || data.paused);
  activityButton.querySelector(".activity-label").textContent = data.paused ? "Preparation paused" : data.actionable ? "Preparation needs attention" : "Preparing libraries";
  if (!visible) { activityPopover.hidden = true; activityButton.setAttribute("aria-expanded", "false"); return; }
  const heading = element("h2", data.paused ? "Preparation paused" : data.actionable ? "Action available" : "Preparing libraries");
  const copy = element("p", data.paused ? "Committed work may finish; no new scans are admitted." : "New review work appears as each library item becomes ready.");
  const children = [element("p", "Background activity", "eyebrow"), heading, copy];
  for (const item of data.libraries) {
    const row = element("div", null, "activity-library");
    row.append(element("strong", item.library), element("span", friendly(item.state)), element("small", `${item.checked} checked · ${Math.max(0,item.total-item.checked)} remaining`), element("small", `${item.ready} ready · ${item.blocked} blocked`));
    const scan = element("button", "Scan now", "inline-action"); scan.type = "button"; scan.dataset.actionKey = `${item.connection}:${item.library}:scan`; scan.disabled = data.paused || ["queued","discovering"].includes(item.state);
    scan.addEventListener("click",async()=>{scan.disabled=true;try{await sendPreparationAction(`/api/v1/review-preparation/${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/scan`);activityStatus.textContent="Scan requested.";}catch{scan.disabled=false;activityStatus.setAttribute("role","alert");activityStatus.textContent="Scan request failed safely. Try again.";}});
    row.append(scan);
    if (item.state === "completed_with_failures") {
      const retry = element("button", "Retry", "inline-action"); retry.type = "button"; retry.dataset.actionKey = `${item.connection}:${item.library}:retry`;
      retry.disabled = data.paused;
      retry.addEventListener("click",async()=>{retry.disabled=true;try{await sendPreparationAction(`/api/v1/review-preparation/${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/retry`);activityStatus.textContent="Validation retry requested.";}catch{retry.disabled=false;activityStatus.setAttribute("role","alert");activityStatus.textContent="Retry failed safely. Correct configuration if needed, then try again.";}});
      row.append(retry);
    }
    children.push(row);
  }
  const activityStatus = element("p", "", "activity-status"); activityStatus.setAttribute("role", "status"); children.push(activityStatus);
  const actions = element("div", null, "activity-actions");
  const pause = element("button", data.paused ? "Resume" : "Pause", "inline-action"); pause.type = "button"; pause.dataset.actionKey = "control:pause";
  pause.addEventListener("click",async()=>{pause.disabled=true;try{renderActivity(await sendPreparationAction(`/api/v1/review-preparation/${data.paused?"resume":"pause"}`));}catch{pause.disabled=false;activityStatus.setAttribute("role","alert");activityStatus.textContent="Preparation control failed safely. Try again.";}});
  const close = element("button", "Close", "inline-action"); close.type = "button"; close.dataset.actionKey = "control:close";
  close.addEventListener("click", () => { activityPopover.hidden = true; activityButton.setAttribute("aria-expanded", "false"); activityButton.focus(); });
  actions.append(pause, close); children.push(actions); activityPopover.replaceChildren(...children);
  if (focusedAction) {
    const replacement = [...activityPopover.querySelectorAll("button")].find(button => button.dataset.actionKey === focusedAction);
    if (replacement) replacement.focus({preventScroll: true});
    else { activityPopover.tabIndex = -1; activityPopover.focus({preventScroll: true}); }
  }
};
let activityProjection = null;
const loadActivity=()=>load("/api/v1/review-preparation").then(data=>{
  const projection = JSON.stringify(data.libraries.map(item => [item.connection,item.library,item.state,item.checked,item.total,item.ready,item.blocked]));
  renderActivity(data);
  if (activityProjection !== null && projection !== activityProjection) dispatchEvent(new Event("review-preparation-change"));
  activityProjection = projection;
  setTimeout(loadActivity,data.active||data.actionable||data.paused?1500:15000);
}).catch(()=>setTimeout(loadActivity,15000));
if(activityButton&&activityPopover){
  activityButton.addEventListener("click",()=>{const opening=activityPopover.hidden;activityPopover.hidden=!opening;activityButton.setAttribute("aria-expanded",String(opening));if(opening)activityPopover.querySelector("button")?.focus();});
  loadActivity();
}
const failPage = target => {
  const box = element("section", null, "error-state");
  const alert = element("p", "The bounded review data could not be loaded. Refresh to retry.");
  alert.setAttribute("role", "alert");
  box.append(element("p", "Review unavailable", "eyebrow"), element("h1", "This workspace could not be loaded."), alert);
  target.replaceChildren(box);
  target.focus();
};
const heading = (eyebrow, title, copy) => {
  const wrapper = element("header", null, "page-heading");
  const text = element("div");
  text.append(element("p", eyebrow, "eyebrow"), element("h1", title), element("p", copy));
  wrapper.append(text);
  return wrapper;
};
const workRow = item => {
  const link = element("a", null, "work-row");
  link.href = item.detail_url;
  const mark = element("span", initials(item.title), "work-mark");
  mark.setAttribute("aria-hidden", "true");
  const copy = element("span", null, "work-copy");
  copy.append(element("strong", item.title), element("small", `${item.library} · ${item.item}`));
  const state = element("span", null, "work-state");
  const noAttention = item.attention_state === "no_attention_needed";
  state.append(element("span", noAttention ? "No attention needed" : `${item.attention_count} need attention`, `badge${noAttention ? " automatic" : ""}`));
  state.append(element("small", item.dispositions.map(friendly).join(" · ") || "Current"));
  link.append(mark, copy, state);
  return link;
};

const libraryProjection = data => JSON.stringify([data.counts, data.items, data.next_cursor]);

const libraryRoot = document.querySelector("#library");
if (libraryRoot) {
  const parameters = new URLSearchParams(location.search);
  const lens = parameters.get("lens") || "needs_attention";
  load(`/api/v1/library?${parameters}`).then(data => {
    const top = heading("Authenticated review", "Review library", "Attention-first, exact-current review cases. Automatic results remain visible without asking you to approve them.");
    const counts = element("div", null, "count-cluster");
    for (const [label, value] of [["Attention", data.counts.needs_attention], ["All cases", data.counts.all], ["No attention", data.counts.no_attention_needed]]) {
      const metric = element("span"); metric.append(element("strong", String(value)), element("small", label)); counts.append(metric);
    }
    top.append(counts);
    const tools = element("div", null, "library-tools");
    tools.append(searchForm(data.query, lens));
    const lenses = element("nav", null, "lenses");
    lenses.setAttribute("aria-label", "Library lenses");
    for (const [value, label] of [["needs_attention", "Needs attention"], ["all", "All review cases"], ["no_attention_needed", "No attention needed"]]) {
      const link = element("a", `${label} (${data.counts[value]})`);
      const target = new URLSearchParams(); target.set("lens", value); if (data.query) target.set("q", data.query);
      link.href = `/library?${target}`;
      if (value === lens) link.setAttribute("aria-current", "page");
      lenses.append(link);
    }
    tools.append(lenses);
    const workspace = element("div", null, "library-workspace");
    const list = element("section", null, "work-list");
    list.setAttribute("aria-label", "Review cases");
    const listHead = element("div", null, "work-list-header");
    listHead.append(element("span", friendly(lens)), element("span", `${data.items.length} shown`));
    list.append(listHead);
    for (const item of data.items) list.append(workRow(item));
    if (!data.items.length) list.append(element("p", "No exact-current cases match this view.", "empty-state"));
    const pagination = element("div", null, "pagination");
    pagination.append(element("p", data.next_cursor ? "A bounded page of current cases" : "End of the bounded current result"));
    if (data.next_cursor) {
      const next = element("a", "Next page", "next"); parameters.set("cursor", data.next_cursor); next.href = `/library?${parameters}`; pagination.append(next);
    }
    list.append(pagination);
    const preview = element("aside", null, "workspace-preview");
    const book = element("span", "ML", "work-mark preview-book"); book.setAttribute("aria-hidden", "true");
    preview.append(book, element("p", "Current review cases", "eyebrow"), element("h2", data.items.length ? "Choose a work to review its exact-current fields." : "This view is clear."), element("p", data.items.length ? "Each decision is recorded as an immutable human outcome. This workspace never writes to your target library." : "Try another lens or search term to inspect other bounded cases."));
    workspace.append(list, preview);
    libraryRoot.replaceChildren(top, tools, workspace);
    let currentLibraryProjection = libraryProjection(data);
    let libraryRefreshGeneration = 0;
    const refreshLibrary = () => {
      const generation = ++libraryRefreshGeneration;
      return load(`/api/v1/library?${new URLSearchParams(location.search)}`).then(updated => {
      if (generation !== libraryRefreshGeneration) return;
      const updatedProjection = libraryProjection(updated);
      if (updatedProjection === currentLibraryProjection) return;
      currentLibraryProjection = updatedProjection;
      const active = document.activeElement;
      const focusedLink = active?.closest?.(".lenses a, .work-list a");
      const focusRegion = focusedLink?.closest(".lenses") ? ".lenses" : focusedLink?.closest(".work-list") ? ".work-list" : null;
      const focusedHref = focusedLink?.getAttribute("href") || null;
      const refreshedCounts = libraryRoot.querySelector(".count-cluster");
      refreshedCounts.replaceChildren();
      for (const [label, value] of [["Attention", updated.counts.needs_attention], ["All cases", updated.counts.all], ["No attention", updated.counts.no_attention_needed]]) {
        const metric = element("span"); metric.append(element("strong", String(value)), element("small", label)); refreshedCounts.append(metric);
      }
      const refreshedLenses = libraryRoot.querySelector(".lenses");
      refreshedLenses.replaceChildren();
      for (const [value, label] of [["needs_attention", "Needs attention"], ["all", "All review cases"], ["no_attention_needed", "No attention needed"]]) {
        const link = element("a", `${label} (${updated.counts[value]})`);
        const target = new URLSearchParams(); target.set("lens", value); if (updated.query) target.set("q", updated.query);
        link.href = `/library?${target}`;
        if (value === lens) link.setAttribute("aria-current", "page");
        refreshedLenses.append(link);
      }
      const refreshedList = libraryRoot.querySelector(".work-list");
      const refreshedHead = element("div", null, "work-list-header");
      refreshedHead.append(element("span", friendly(lens)), element("span", `${updated.items.length} shown`));
      const refreshedChildren = [refreshedHead, ...updated.items.map(workRow)];
      if (!updated.items.length) refreshedChildren.push(element("p", "No exact-current cases match this view.", "empty-state"));
      const refreshedPagination = element("div", null, "pagination");
      refreshedPagination.append(element("p", updated.next_cursor ? "A bounded page of current cases" : "End of the bounded current result"));
      if (updated.next_cursor) {
        const next = element("a", "Next page", "next");
        const target = new URLSearchParams(location.search); target.set("cursor", updated.next_cursor); next.href = `/library?${target}`;
        refreshedPagination.append(next);
      }
      refreshedChildren.push(refreshedPagination);
      refreshedList.replaceChildren(...refreshedChildren);
      const refreshedPreview = libraryRoot.querySelector(".workspace-preview");
      const refreshedBook = element("span", "ML", "work-mark preview-book"); refreshedBook.setAttribute("aria-hidden", "true");
      refreshedPreview.replaceChildren(refreshedBook, element("p", "Current review cases", "eyebrow"), element("h2", updated.items.length ? "Choose a work to review its exact-current fields." : "This view is clear."), element("p", updated.items.length ? "Each decision is recorded as an immutable human outcome. This workspace never writes to your target library." : "Try another lens or search term to inspect other bounded cases."));
      if (focusRegion && focusedHref) {
        const region = libraryRoot.querySelector(focusRegion);
        const replacement = [...region.querySelectorAll("a")].find(link => link.getAttribute("href") === focusedHref);
        if (replacement) replacement.focus({preventScroll: true});
        else { region.tabIndex = -1; region.focus({preventScroll: true}); }
      }
      });
    };
    addEventListener("review-preparation-change", refreshLibrary);
    libraryRoot.focus({preventScroll: true});
  }).catch(() => failPage(libraryRoot));
}

const attentionRoot = document.querySelector("#attention");
if (attentionRoot) {
  const parameters = new URLSearchParams(location.search);
  load(`/api/v1/attention?${parameters}`).then(data => {
    const top = heading("Human exceptions", "Fields needing attention", "A field-level queue for choices automation cannot make. Every row links to the exact-current case.");
    const counts = element("div", null, "count-cluster");
    const metric = element("span"); metric.append(element("strong", String(data.total)), element("small", "Fields")); counts.append(metric); top.append(counts);
    const tools = element("div", null, "library-tools"); tools.append(searchForm(data.query, null));
    const workspace = element("div", null, "library-workspace");
    const list = element("section", null, "work-list"); list.setAttribute("aria-label", "Fields needing attention");
    const listHead = element("div", null, "work-list-header"); listHead.append(element("span", "Attention first"), element("span", `${data.items.length} shown`)); list.append(listHead);
    for (const item of data.items) {
      const row = element("a", null, "work-row"); row.href = item.detail_url;
      const mark = element("span", initials(item.title), "work-mark"); mark.setAttribute("aria-hidden", "true");
      const copy = element("span", null, "work-copy"); copy.append(element("strong", item.title), element("small", `${item.destination} · ${item.library} · ${item.item}`));
      const state = element("span", null, "work-state"); state.append(element("span", "Needs a choice", "badge"), element("small", item.notices.map(friendly).join(" · ") || "Evidence differs"));
      row.append(mark, copy, state); list.append(row);
    }
    if (!data.items.length) list.append(element("p", "No exact-current field needs a human choice.", "empty-state"));
    const pagination = element("div", null, "pagination"); pagination.append(element("p", "Bounded exact-current field queue"));
    if (data.next_cursor) { const next = element("a", "Next page", "next"); parameters.set("cursor", data.next_cursor); next.href = `/attention?${parameters}`; pagination.append(next); }
    list.append(pagination);
    const preview = element("aside", null, "workspace-preview"); const icon = element("span", "!", "work-mark preview-book"); icon.setAttribute("aria-hidden", "true");
    preview.append(icon, element("p", "Decision boundary", "eyebrow"), element("h2", data.items.length ? "Open a field to compare current and proposed outcomes." : "Nothing needs your attention."), element("p", "Automatic decisions are never created here. Human choices are durable, explicit, and limited to the current case."));
    workspace.append(list, preview); attentionRoot.replaceChildren(top, tools, workspace); attentionRoot.focus({preventScroll: true});
  }).catch(() => failPage(attentionRoot));
}

const root = document.querySelector("#review");
if (root) {
  const id = encodeURIComponent(root.dataset.reviewCaseId);
  let csrf = root.dataset.csrfToken;
  pageCsrf = csrf;
  root.removeAttribute("data-csrf-token");
  let currentDetail;
  const refreshCsrf = async () => {
    const response = await fetch(`/review/${id}`, {cache: "no-store"});
    if (!response.ok) { const error = new Error(); error.loginRequired = response.status === 401; throw error; }
    const detached = new DOMParser().parseFromString(await response.text(), "text/html");
    const token = detached.querySelector("#review")?.dataset.csrfToken;
    detached.querySelector("#review")?.removeAttribute("data-csrf-token");
    if (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token)) throw new Error();
    return token;
  };
  const choiceButton = (field, label, outcome, selectedValue) => {
    const button = element("button", label);
    button.type = "button";
    if (outcome === "reject") button.className = "reject";
    let idempotencyKey;
    let refreshBeforeRetry = false;
    let loginRequired = false;
    const submit = async () => {
      if (!idempotencyKey) idempotencyKey = crypto.randomUUID();
      const group = button.closest(".choices");
      const status = button.closest(".field").querySelector(".decision-status");
      for (const control of group.querySelectorAll("button")) control.disabled = true;
      status.className = "decision-status"; status.setAttribute("role", "status"); status.textContent = `Recording ${label.toLowerCase()}…`;
      try {
        if (refreshBeforeRetry) { csrf = await refreshCsrf(); refreshBeforeRetry = false; }
        const send = async () => {
          const response = await fetch(`/api/v1/review-cases/${id}/decisions`, {method: "POST", headers: {"content-type": "application/json", "x-csrf-token": csrf, "idempotency-key": idempotencyKey}, body: JSON.stringify({destination: field.destination, outcome, proposal_id: field.proposal_id, selected_value: selectedValue, target_fingerprint: field.target_fingerprint})});
          if (!response.ok) return {response};
          const acknowledgement = await response.json();
          if (acknowledgement.outcome !== outcome || acknowledgement.proposal_id !== field.proposal_id) throw new Error();
          return {acknowledgement, response};
        };
        let result; let reconciling = false;
        try { result = await send(); } catch { reconciling = true; try { result = await send(); } catch { location.reload(); return; } }
        const response = result.response;
        if (!response.ok) {
          if (response.status === 409) { let code; try { code = (await response.json()).error?.code; } catch {} if (code === "stale") location.assign('/library'); else location.reload(); return; }
          if (reconciling) { if (response.status === 401) location.assign("/login"); else location.reload(); return; }
          if (response.status === 401) loginRequired = true; else if (response.status === 403) refreshBeforeRetry = true;
          throw new Error();
        }
        const acknowledgement = result.acknowledgement;
        const unresolved = currentDetail.fields.filter(value => value.needs_attention);
        const index = unresolved.findIndex(value => value.destination === field.destination);
        const next = unresolved[index + 1] || unresolved.find(value => value.destination !== field.destination);
        const parameters = new URLSearchParams(); parameters.set("decided", field.destination); if (next) parameters.set("field", next.destination);
        const strip = document.querySelector(".field-strip"); if (strip) parameters.set("strip", String(Math.max(0, Math.round(strip.scrollLeft))));
        location.assign(`/review/${id}?${parameters}`);
      } catch (error) {
        if (error.loginRequired) loginRequired = true;
        for (const control of group.querySelectorAll("button")) control.disabled = false;
        status.className = "decision-status decision-error"; status.setAttribute("role", "alert"); status.replaceChildren(element("span", `${label} was not recorded. Your choice is unchanged. `));
        const recovery = loginRequired ? element("a", "Sign in again") : element("button", "Try again");
        if (loginRequired) { recovery.href = "/login"; recovery.className = "recovery"; } else { recovery.type = "button"; recovery.addEventListener("click", submit); }
        status.append(recovery); recovery.focus();
      }
    };
    button.addEventListener("click", submit);
    return button;
  };
  Promise.all([load(`/api/v1/review-cases/${id}`), load(`/api/v1/review-cases/${id}/evidence`), load(`/api/v1/review-cases/${id}/history`)]).then(([detail, evidence, history]) => {
    currentDetail = detail;
    const parameters = new URLSearchParams(location.search);
    const shell = element("div", null, "detail-shell");
    const sidebar = element("aside", null, "case-sidebar");
    sidebar.append(element("p", detail.current ? "Exact-current case" : "Unavailable case", "eyebrow"), element("h1", "Current review case"), element("h2", detail.target.item), element("p", `${detail.target.library} · ${detail.target.connection}`, "case-identity"));
    const unresolved = detail.fields.filter(field => field.needs_attention);
    const position = element("div", null, "position"); position.append(element("strong", String(unresolved.length)), element("span", unresolved.length === 1 ? "field needs attention" : "fields need attention")); sidebar.append(position);
    const fieldNav = element("nav", null, "field-navigation"); fieldNav.setAttribute("aria-label", "Fields needing attention");
    const fieldFromHash = () => {
      if (!location.hash.startsWith("#field-")) return null;
      try { return decodeURIComponent(location.hash.slice(7)); } catch { return null; }
    };
    const requestedField = fieldFromHash() || parameters.get("field");
    const requestedIndex = unresolved.findIndex(field => field.destination === requestedField);
    const initialFieldIndex = requestedIndex >= 0 ? requestedIndex : 0;
    const fallbackField = unresolved[initialFieldIndex]?.destination || null;
    const activeField = () => {
      const fragmentField = fieldFromHash();
      return unresolved.some(field => field.destination === fragmentField) ? fragmentField : fallbackField;
    };
    const selectField = (destination, focus = false) => {
      const selectedLink = [...fieldNav.querySelectorAll("a")].find(link => link.dataset.destination === destination);
      if (!selectedLink) return;
      for (const item of fieldNav.querySelectorAll("a")) item.removeAttribute("aria-current");
      selectedLink.setAttribute("aria-current", "true");
      if (focus) document.querySelector(`#field-${CSS.escape(destination)}`)?.focus();
    };
    unresolved.forEach((field, index) => { const link = element("a"); link.href = `#field-${field.destination}`; link.dataset.destination = field.destination; if (index === initialFieldIndex) link.setAttribute("aria-current", "true"); link.addEventListener("click", () => selectField(field.destination)); link.append(element("span", "", "attention-dot"), element("span", field.destination), element("small", `${index + 1} of ${unresolved.length}`)); fieldNav.append(link); });
    if (!unresolved.length) fieldNav.append(element("p", "All exact-current fields are resolved or automatic."));
    sidebar.append(fieldNav);
    const content = element("div", null, "detail-content");
    const announced = parameters.get("decided");
    const acknowledged = detail.fields.find(field => field.destination === announced && field.human_decision);
    if (acknowledged) { const toast = element("p", `Decision recorded for ${acknowledged.destination}.`, "toast"); toast.setAttribute("role", "status"); content.append(toast); }
    const strip = element("div", null, "field-strip"); strip.setAttribute("aria-label", "Fields needing attention");
    const resolved = [];
    for (const field of detail.fields) {
      if (!field.needs_attention) { resolved.push(field); continue; }
      const section = element("section", null, "field"); section.id = `field-${field.destination}`; section.tabIndex = -1;
      const fieldHeading = element("header", null, "field-heading"); const fieldTitle = element("div"); fieldTitle.append(element("p", "Current to outcome", "eyebrow"), element("h2", field.destination), element("p", `Deterministic disposition: ${friendly(field.deterministic_disposition)}`)); fieldHeading.append(fieldTitle, element("span", "Needs a choice", "badge")); section.append(fieldHeading);
      const comparison = element("div", null, "comparison"); const current = element("div", null, "value-card"); current.append(element("small", `Current · ${friendly(field.current.state)}`), element("strong", format(field.current.value))); comparison.append(current, element("span", "→", "command-arrow"));
      const proposed = element("div", null, "value-card proposed"); proposed.append(element("small", "Proposed outcome"), element("strong", field.proposed_values.length ? field.proposed_values.map(value => format(value.value)).join(" · or · ") : "No proposed value")); comparison.append(proposed); section.append(comparison);
      const meta = element("div", null, "field-meta"); for (const [label, values] of [["Evidence", field.provenance], ["Notices", field.notices], ["Uncertainty", field.uncertainty]]) { const box = element("span"); box.append(element("strong", label), document.createTextNode(values.length ? values.map(friendly).join(" · ") : "None")); meta.append(box); } section.append(meta);
      const choices = element("fieldset", null, "choices"); choices.append(element("legend", `Choose one immutable human outcome for ${field.destination}`));
      field.proposed_values.forEach((value, index) => choices.append(choiceButton(field, field.proposed_values.length === 1 ? "Accept proposed value" : `Accept proposed value ${index + 1}`, "accept", value)));
      choices.append(choiceButton(field, "Keep current value", "keep_current", null), choiceButton(field, "Reject proposed change", "reject", null)); section.append(choices);
      const status = element("p", "No human decision recorded.", "decision-status"); status.setAttribute("aria-live", "polite"); section.append(status); strip.append(section);
    }
    if (strip.childElementCount) content.append(strip); else content.append(element("section", "No exact-current field needs a human choice.", "empty-state"));
    if (resolved.length) {
      const context = element("details", null, "automatic-context"); context.append(element("summary", `Resolved and automatic context (${resolved.length})`)); const inner = element("div", null, "details-inner");
      for (const field of resolved) {
        const row = element("section", null, "resolved-field");
        const usesProposed = field.deterministic_disposition === "use_proposed_value" && field.proposed_values.length;
        const outcomeValue = field.human_decision?.outcome === "accept" ? field.human_decision.selected_value : usesProposed ? field.proposed_values.map(value => value.value) : field.current.value;
        row.append(element("h3", field.destination), element("p", `${friendly(field.deterministic_disposition)} · Current: ${format(field.current.value)} · Outcome value: ${format(outcomeValue)}`));
        if (field.human_decision) row.append(element("p", `Human decision: ${friendly(field.human_decision.outcome)} by ${field.human_decision.actor}.`, "decision-recorded"));
        inner.append(row);
      }
      context.append(inner); content.append(context);
    }
    const advice = element("details", null, "model-advice"); advice.append(element("summary", `Model advice: ${friendly(detail.model_advice.state)}`)); const adviceInner = element("div", null, "details-inner"); adviceInner.append(element("p", "Model advice is contextual only and is not a human decision."));
    for (const result of detail.model_advice.results) for (const recommendation of result.recommendations) { const card = element("section", null, "advice-card"); card.append(element("h3", recommendation.proposal_id), element("p", `Recommendation: ${recommendation.recommendation}`), element("p", `Reasons: ${recommendation.reasons.join(" · ")}`), element("p", `Problems: ${recommendation.problems.length ? recommendation.problems.join(" · ") : "none"}`), element("p", `Citations: ${recommendation.citations.length ? recommendation.citations.join(" · ") : "none"}`)); adviceInner.append(card); }
    if (!adviceInner.childElementCount) adviceInner.append(element("p", "No model advice is available for this case.")); advice.append(adviceInner); content.append(advice);
    const ledger = element("details", null, "ledger"); ledger.open = true; ledger.append(element("summary", `Evidence and source ledger (${evidence.items.length}${evidence.next_cursor ? "+" : ""})`)); const ledgerInner = element("div", null, "details-inner"); const evidenceItems = element("div", null, "ledger-items"); ledgerInner.append(evidenceItems);
    const appendEvidence = items => { for (const item of items) { const card = element("section", null, "evidence-item"); const identity = [item.source, item.provider, item.candidate].filter(Boolean).join(" · "); card.append(element("h3", `${friendly(item.kind)} · ${identity}`), element("pre", item.fact)); evidenceItems.append(card); } };
    appendEvidence(evidence.items); if (!evidence.items.length) evidenceItems.append(element("p", "No referenced evidence."));
    if (evidence.next_cursor) { const more = element("button", "Load more evidence", "secondary-button"); more.type = "button"; let cursor = evidence.next_cursor; const status = element("p", ""); status.setAttribute("role", "status"); more.addEventListener("click", async () => { more.disabled = true; status.setAttribute("role", "status"); status.textContent = "Loading more evidence…"; try { const page = await load(`/api/v1/review-cases/${id}/evidence?limit=50&cursor=${encodeURIComponent(cursor)}`); appendEvidence(page.items); cursor = page.next_cursor; if (cursor) { more.disabled = false; status.textContent = `Loaded ${page.items.length} more evidence item(s).`; } else { more.remove(); status.textContent = "All referenced evidence is shown."; } } catch { more.disabled = false; status.setAttribute("role", "alert"); status.textContent = "More evidence could not be loaded. Try again."; } }); ledgerInner.append(more, status); }
    ledger.append(ledgerInner); content.append(ledger);
    const historyDetails = element("details", null, "history"); historyDetails.append(element("summary", `Current-case history (${history.items.length})`)); const historyInner = element("div", null, "details-inner");
    history.items.forEach((item, index) => { const row = element("div", null, "history-row"); const text = item.kind==='human_decision_recorded' ? `${item.occurred_at}. ${item.destination}: ${item.outcome} by ${item.actor}` : `${item.checkpoint}. ${friendly(item.kind)}: ${item.reason}`; row.append(element("span", String(index + 1)), element("span", text)); historyInner.append(row); });
    historyDetails.append(historyInner); content.append(historyDetails);
    shell.append(sidebar, content); root.replaceChildren(shell);
    const stripPosition = Number.parseInt(parameters.get("strip") || "0", 10); if (Number.isSafeInteger(stripPosition) && stripPosition >= 0) strip.scrollLeft = stripPosition;
    addEventListener("hashchange", () => selectField(activeField(), true));
    const selected = activeField(); const target = (selected ? document.querySelector(`#field-${CSS.escape(selected)}`) : null) || root; target.focus(target === root ? {preventScroll: true} : undefined);
  }).catch(error => {
    if (error.status === 401) { location.assign("/login"); return; }
    const box = element("section", null, "error-state"); const alert = element("p", "The exact current case could not be loaded. Return to the library and reopen the current case."); alert.setAttribute("role", "alert"); const link = element("a", "Return to library", "recovery"); link.href = "/library"; box.append(element("p", "Exact-current protection", "eyebrow"), element("h1", "Case unavailable"), alert, link); root.replaceChildren(box); root.focus();
  });
}
"""
