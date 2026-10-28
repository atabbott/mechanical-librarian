"""Server-rendered owner login; authenticated workspace assets are packaged."""

from __future__ import annotations


def login_html() -> str:
    return """<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8">
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
      <span aria-hidden="true">◐</span><span class="theme-label">Dark mode</span>
    </button>
  </header>
  <main id="workspace" class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <p class="eyebrow">Private owner access</p>
      <h1 id="login-title">Review your library with confidence.</h1>
      <p class="lede">Sign in to see what needs your attention in your library. Nothing changes in your library until you give write permission, and every change is checked before it is written.</p>
      <form id="login">
        <label for="password">Owner password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button class="primary-button" type="submit">Sign in</button>
        <p id="login-status" role="alert"></p>
      </form>
    </section>
  </main>
  <p class="visually-hidden" id="theme-status" role="status" aria-live="polite"></p>
  <script src="/assets/login.js" defer></script>
</body>
</html>"""


LOGIN_CSS = r"""
:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#17201e;background:#f3f0e8;color-scheme:light}
:root[data-theme="dark"]{color:#edf2ef;background:#111816;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:inherit;color:inherit}
.skip-link{position:absolute;left:-9999px}.skip-link:focus{left:1rem;top:1rem;z-index:2}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:1rem 1.5rem;border-bottom:1px solid #9aa59f}
.brand{display:flex;align-items:center;gap:.75rem}.brand span:last-child{display:grid}.brand small{opacity:.72}
.brand-mark{display:grid;place-items:center;width:2.5rem;height:2.5rem;border-radius:.6rem;background:#315f52;color:white;font-weight:800}
.theme-toggle{border:1px solid currentColor;border-radius:999px;background:transparent;color:inherit;padding:.55rem .8rem}
.login-page{display:grid;place-items:center;padding:3rem 1rem}.login-card{width:min(32rem,100%);padding:2rem;border:1px solid #9aa59f;border-radius:1rem;background:color-mix(in srgb,currentColor 4%,transparent)}
.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:.75rem;font-weight:700}.lede{line-height:1.55}
form{display:grid;gap:.75rem;margin-top:1.5rem}input{font:inherit;padding:.8rem;border:1px solid #75827c;border-radius:.5rem}
.primary-button{font:inherit;font-weight:700;padding:.8rem;border:0;border-radius:.5rem;background:#315f52;color:white}
#login-status{min-height:1.3em;margin:0;color:#a12b2b}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
"""


LOGIN_JS = r"""
(() => {
  const root = document.documentElement;
  const toggle = document.querySelector('.theme-toggle');
  const label = document.querySelector('.theme-label');
  const status = document.querySelector('#theme-status');
  const setTheme = (theme, announce) => {
    root.dataset.theme = theme;
    toggle.setAttribute('aria-pressed', String(theme === 'dark'));
    label.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
    if (!announce) return;
    document.cookie = `ml_theme=${theme}; Path=/; Max-Age=31536000; SameSite=Strict${location.protocol === "https:" ? "; Secure" : ""}`;
    status.textContent = `${theme === 'dark' ? 'Dark' : 'Light'} mode enabled`;
  };
  // The saved choice, otherwise the system, followed live until a toggle saves one.
  const savedPattern = /(?:^|; )ml_theme=(light|dark)(?:;|$)/;
  const saved = savedPattern.exec(document.cookie)?.[1];
  const query = '(prefers-color-scheme: dark)';
  const media = typeof matchMedia === 'function' ? matchMedia(query) : null;
  setTheme(saved ?? (media?.media === query && media.matches ? 'dark' : 'light'), false);
  if (!saved && media?.media === query && typeof media.addEventListener === 'function') {
    media.addEventListener('change', (event) => {
      if (!savedPattern.test(document.cookie)) setTheme(event.matches ? 'dark' : 'light', false);
    });
  }
  toggle.addEventListener('click', () => setTheme(root.dataset.theme === 'dark' ? 'light' : 'dark', true));
  document.querySelector('#login').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button');
    const message = document.querySelector('#login-status');
    button.disabled = true;
    message.textContent = '';
    try {
      const response = await fetch('/api/v1/session/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({password: document.querySelector('#password').value}),
      });
      if (!response.ok) throw new Error('login');
      window.location.assign('/library');
    } catch (_) {
      message.textContent = 'Sign in failed. Check the password and try again.';
      button.disabled = false;
    }
  });
})();
"""


__all__ = ["LOGIN_CSS", "LOGIN_JS", "login_html"]
