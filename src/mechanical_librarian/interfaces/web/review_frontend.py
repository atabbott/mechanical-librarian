"""Server-rendered owner login; authenticated workspace assets are packaged."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable


def login_html(return_path: str | None = None) -> str:
    """The caller validates ``return_path`` as a same-origin workspace page."""
    next_attribute = (
        "" if return_path is None else f' data-next="{html.escape(return_path)}"'
    )
    return (
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <script src="/assets/theme.js"></script>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Owner sign in</title>
  <link rel="stylesheet" href="/assets/login.css">
</head>
<body>
  <a class="skip-link" href="#workspace">Skip to sign in</a>
  <header class="topbar">
    <span class="brand"><span class="brand-mark" aria-hidden="true">ML</span>
      <span><strong>Mechanical Librarian</strong><small>Private review workspace</small></span>
    </span>
    <button class="theme-toggle" type="button" aria-pressed="false">
      <span aria-hidden="true">◐</span> <span class="theme-label">Dark mode</span>
    </button>
  </header>
  <main id="workspace" class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <p class="eyebrow">Private owner access</p>
      <h1 id="login-title">Review your library with confidence.</h1>
      <p class="lede">Sign in to see what needs your attention in your library. Nothing changes in your library until you give write permission, and every change is checked before it is written.</p>
      <form id="login" novalidate"""
        + next_attribute
        + """>
        <label for="password">Owner password</label>
        <div class="password-row">
          <input id="password" name="password" type="password" autocomplete="current-password" required aria-describedby="login-status">
          <button class="reveal" type="button" aria-pressed="false" aria-controls="password">Show password</button>
        </div>
        <button class="primary-button" type="submit">Sign in</button>
        <p id="login-status" role="alert"></p>
      </form>
      <details class="forgotten">
        <summary>Forgotten the owner password?</summary>
        <p>Mechanical Librarian keeps only a verifier, so it cannot show or reset the password here.</p>
        <p>If this deployment reads an owner password hash file, write a new Argon2id hash to that file and restart; every browser is signed out. If the password was created in guided setup, this release has no reset: restore a backup of the data volume from when you knew the password, or start again from an empty volume.</p>
        <p><a href="https://github.com/atabbott/mechanical-librarian/blob/main/docs/operations/deployment.md#forgotten-owner-password" rel="noreferrer">Read the recovery steps in the deployment guide</a></p>
      </details>
    </section>
  </main>
  <p class="visually-hidden" id="theme-status" role="status" aria-live="polite"></p>
  <script src="/assets/login.js" defer></script>
</body>
</html>"""
    )


# Runs blocking in the head of sign-in and setup, before first paint. It applies
# only a saved choice; with none, the stylesheets follow the system themselves.
THEME_JS = (
    "(()=>{const saved=/(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)"
    "?.[1];if(saved)document.documentElement.dataset.theme=saved})();\n"
)


# The workspace's own self-hosted Inter faces (frontend/src/styles.css), by the
# content-hashed file name each is built under.
_INTER_FACES = (
    (
        "inter-latin-wght-normal",
        "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,"
        "U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,"
        "U+FEFF,U+FFFD",
    ),
    (
        "inter-latin-ext-wght-normal",
        "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,"
        "U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,"
        "U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF",
    ),
)


def inter_font_faces(font_paths: Iterable[str]) -> str:
    """State the workspace's Inter faces for a page served before it loads.

    A face whose built file is absent is left out, so the page keeps the
    fallback stack rather than naming a font it cannot fetch.
    """
    paths = tuple(font_paths)
    rules = []
    for stem, unicode_range in _INTER_FACES:
        built = re.compile(rf"assets/{stem}-[A-Za-z0-9_-]{{8,}}\.woff2")
        path = next((value for value in paths if built.fullmatch(value)), None)
        if path is not None:
            rules.append(
                "@font-face{font-family:Inter;font-style:normal;"
                "font-weight:100 900;font-display:swap;"
                f"src:url(/assets/review-app/{path}) format('woff2');"
                f"unicode-range:{unicode_range}}}"
            )
    return "".join(rules)


LOGIN_CSS = r"""
:root{color-scheme:light;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;--page:#f4f2ec;--surface:#fbfaf6;--field:#fff;--ink:#17201e;--soft:#56615e;--accent:#1b6558;--accent-strong:#0f4c42;--accent-soft:#dcebe6;--line:#d9d7cf;--line-strong:#b9beb9;--danger:#8c3434;--focus:#146fba;color:var(--ink);background:var(--page)}
:root[data-theme="dark"]{color-scheme:dark;--page:#0f1512;--surface:#151d19;--field:#1b2520;--ink:#edf2ee;--soft:#a9b6b0;--accent:#61b29f;--accent-strong:#8ed2c1;--accent-soft:#203b34;--line:#2d3933;--line-strong:#4a5a52;--danger:#f39b9b;--focus:#78bdf2}
@media(prefers-color-scheme:dark){:root:not([data-theme]){color-scheme:dark;--page:#0f1512;--surface:#151d19;--field:#1b2520;--ink:#edf2ee;--soft:#a9b6b0;--accent:#61b29f;--accent-strong:#8ed2c1;--accent-soft:#203b34;--line:#2d3933;--line-strong:#4a5a52;--danger:#f39b9b;--focus:#78bdf2}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;min-width:320px;background:var(--page);color:var(--ink)}
button,input{font:inherit}
button{cursor:pointer}
a{color:var(--accent-strong)}
button:focus-visible,input:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid color-mix(in srgb,var(--focus) 80%,transparent);outline-offset:3px}
.skip-link{position:absolute;left:-9999px}
.skip-link:focus{left:1rem;top:1rem;z-index:2;padding:.7rem;background:var(--surface)}
.topbar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.75rem;padding:1rem clamp(1rem,3vw,2.6rem);border-bottom:1px solid var(--line);background:var(--surface)}
.brand{display:flex;align-items:center;gap:.75rem}
.brand span:last-child{display:grid}
.brand small{color:var(--soft);font-size:.78rem}
.brand-mark{display:grid;place-items:center;width:32px;height:38px;border-radius:2px 7px 7px 2px;background:var(--accent-strong);color:var(--surface);font:700 .75rem Georgia,serif}
.theme-toggle{display:inline-flex;align-items:center;gap:.4rem;min-height:44px;border:1px solid var(--line-strong);border-radius:999px;background:transparent;color:var(--ink);padding:.55rem .9rem;font-weight:700}
.login-page{display:grid;place-items:center;padding:3rem 1rem}
.login-card{width:min(32rem,100%);padding:clamp(1.25rem,4vw,2rem);border:1px solid var(--line);border-radius:1rem;background:var(--surface)}
.eyebrow{margin:0;text-transform:uppercase;letter-spacing:.08em;font-size:.75rem;font-weight:800;color:var(--accent-strong)}
h1{margin:.5rem 0 .75rem;font-family:Georgia,"Times New Roman",serif;font-size:clamp(1.65rem,3vw,2.5rem);line-height:1.12;letter-spacing:-.025em}
.lede{line-height:1.55;color:var(--soft)}
form{display:grid;gap:.75rem;margin-top:1.5rem}
label{font-weight:800;font-size:.85rem}
.password-row{display:flex;gap:.5rem}
input{flex:1 1 auto;min-width:0;min-height:44px;padding:.7rem;border:1px solid var(--line-strong);border-radius:.45rem;background:var(--field);color:var(--ink)}
input[aria-invalid=true]{border:2px solid var(--danger)}
.reveal{flex:0 0 auto;min-height:44px;padding:.6rem .8rem;border:1px solid var(--line-strong);border-radius:.5rem;background:var(--surface);color:var(--accent-strong);font-weight:700}
.reveal[aria-pressed=true]{background:var(--accent-soft)}
.primary-button{min-height:44px;font-weight:700;padding:.6rem .85rem;border:1px solid var(--accent-strong);border-radius:.5rem;background:var(--accent-strong);color:var(--surface)}
.primary-button:disabled{opacity:.55;cursor:not-allowed}
#login-status{min-height:1.3em;margin:0;color:var(--danger);font-weight:700}
.forgotten{margin-top:1.25rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--soft);font-size:.85rem;line-height:1.55}
.forgotten summary{min-height:44px;display:flex;align-items:center;cursor:pointer;color:var(--accent-strong);font-weight:700}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
@media(max-width:390px){.password-row{flex-wrap:wrap}.reveal{flex:1 1 100%}}
"""


LOGIN_JS = r"""
(() => {
  const root = document.documentElement;
  const toggle = document.querySelector('.theme-toggle');
  const label = document.querySelector('.theme-label');
  const status = document.querySelector('#theme-status');
  const query = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;
  // The saved choice is applied by theme.js before first paint; with none, the
  // stylesheet follows the system and the toggle only names the other theme.
  const current = () => root.dataset.theme ?? (query?.matches ? 'dark' : 'light');
  const describe = () => {
    const dark = current() === 'dark';
    toggle.setAttribute('aria-pressed', String(dark));
    label.textContent = dark ? 'Light mode' : 'Dark mode';
  };
  describe();
  query?.addEventListener?.('change', describe);
  toggle.addEventListener('click', () => {
    const theme = current() === 'dark' ? 'light' : 'dark';
    root.dataset.theme = theme;
    document.cookie = `ml_theme=${theme}; Path=/; Max-Age=31536000; SameSite=Strict${location.protocol === 'https:' ? '; Secure' : ''}`;
    describe();
    status.textContent = `${theme === 'dark' ? 'Dark' : 'Light'} mode enabled`;
  });

  const password = document.querySelector('#password');
  const reveal = document.querySelector('.reveal');
  reveal.addEventListener('click', () => {
    const shown = password.type === 'password';
    password.type = shown ? 'text' : 'password';
    reveal.setAttribute('aria-pressed', String(shown));
    reveal.textContent = shown ? 'Hide password' : 'Show password';
  });
  password.addEventListener('input', () => password.removeAttribute('aria-invalid'));

  const refusals = {
    401: 'That password was not accepted. Check it and try again.',
    429: 'Too many sign-in attempts from this device. Wait five minutes, then try again.',
  };
  // The server names a return page only after checking it; this check keeps
  // the destination on this origin even so (#1371).
  const returnPath = () => {
    const next = document.querySelector('#login').dataset.next;
    if (next && next.startsWith('/') && !next.startsWith('//') && !next.includes('\\')) {
      try {
        const url = new URL(next, window.location.origin);
        if (url.origin === window.location.origin) return url.pathname + url.search;
      } catch (_) {
        // Fall through to Library.
      }
    }
    return '/library';
  };
  document.querySelector('#login').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type=submit]');
    const message = document.querySelector('#login-status');
    if (!password.value) {
      message.textContent = 'Enter the owner password.';
      password.setAttribute('aria-invalid', 'true');
      password.focus();
      return;
    }
    button.disabled = true;
    message.textContent = '';
    let status = 0;
    try {
      const response = await fetch('/api/v1/session/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({password: password.value}),
      });
      status = response.status;
      if (response.ok) {
        window.location.assign(returnPath());
        return;
      }
    } catch (_) {
      status = 0;
    }
    message.textContent = refusals[status] ?? 'Sign-in is unavailable right now. Wait a moment, then try again.';
    // A refused password is cleared; focus returns to the field to type it again.
    if (status === 401) {
      password.value = '';
      password.setAttribute('aria-invalid', 'true');
    }
    button.disabled = false;
    password.focus();
  });
})();
"""


__all__ = ["LOGIN_CSS", "LOGIN_JS", "THEME_JS", "inter_font_faces", "login_html"]
