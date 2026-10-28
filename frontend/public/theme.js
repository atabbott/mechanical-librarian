/* global document, window */
// Runs before first paint. The same rule as ThemeControl in src/router.tsx, which then
// follows the system live and saves a choice, and as sign-in and setup.
;(() => {
  const saved = /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1]
  const dark =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.dataset.theme = saved ?? (dark ? 'dark' : 'light')
})()
