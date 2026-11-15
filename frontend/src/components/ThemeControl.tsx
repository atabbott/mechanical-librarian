import { useEffect, useState } from 'react'

import { IconButton, MoonIcon, SunIcon } from './MastheadIcons'

// One rule with setup, sign-in and public/theme.js, which applies it before first
// paint: the owner's saved choice, otherwise the system, followed live until a
// choice is saved (#1013).
const savedTheme = () => /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1]
const systemMedia = () => {
  const query = '(prefers-color-scheme: dark)'
  const media = typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
  return media?.media === query ? media : null
}

/** The masthead's theme switch: a moon in the light theme, a sun in the dark one. */
export const ThemeControl = () => {
  const [theme, setTheme] = useState(
    () => savedTheme() ?? (systemMedia()?.matches ? 'dark' : 'light'),
  )
  useEffect(() => {
    const media = systemMedia()
    if (savedTheme() || typeof media?.addEventListener !== 'function') return
    const follow = (event: MediaQueryListEvent) => {
      if (!savedTheme()) setTheme(event.matches ? 'dark' : 'light')
    }
    media.addEventListener('change', follow)
    return () => media.removeEventListener('change', follow)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.cookie = `ml_theme=${next}; Path=/; Max-Age=31536000; SameSite=Strict${location.protocol === 'https:' ? '; Secure' : ''}`
    setTheme(next)
  }
  return (
    <IconButton
      className="theme-toggle"
      label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
      aria-pressed={theme === 'dark'}
      onClick={toggle}
    >
      {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
    </IconButton>
  )
}
