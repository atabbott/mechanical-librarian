import { useSyncExternalStore } from 'react'

// One rule with setup, sign-in and public/theme.js, which applies it before first
// paint: the owner's saved choice in the `ml_theme` cookie, otherwise the system,
// followed live. No cookie is the Follow system choice (#1013, #1341).
export type ThemeChoice = 'system' | 'light' | 'dark'

const savedTheme = () =>
  /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1] as 'light' | 'dark' | undefined
const systemMedia = () => {
  const query = '(prefers-color-scheme: dark)'
  const media = typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
  return media?.media === query ? media : null
}
const paintedTheme = () => savedTheme() ?? (systemMedia()?.matches ? 'dark' : 'light')
const themeChoice = (): ThemeChoice => savedTheme() ?? 'system'

// A choice made in one control reaches the others as a window event, so the
// browser holds the subscriptions and this module keeps no state of its own.
const CHOSEN = 'ml:theme-chosen'
const apply = () => {
  const theme = paintedTheme()
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}
const subscribe = (listener: () => void) => {
  const media = systemMedia()
  const follow = () => {
    apply()
    listener()
  }
  apply()
  media?.addEventListener('change', follow)
  window.addEventListener(CHOSEN, follow)
  return () => {
    media?.removeEventListener('change', follow)
    window.removeEventListener(CHOSEN, follow)
  }
}

/** Saves Light or Dark in this browser, or forgets the choice to follow the system. */
export const chooseTheme = (choice: ThemeChoice) => {
  const secure = location.protocol === 'https:' ? '; Secure' : ''
  document.cookie =
    choice === 'system'
      ? `ml_theme=; Path=/; Max-Age=0; SameSite=Strict${secure}`
      : `ml_theme=${choice}; Path=/; Max-Age=31536000; SameSite=Strict${secure}`
  apply()
  window.dispatchEvent(new Event(CHOSEN))
}

/** The theme on the page now. */
export const useTheme = () => useSyncExternalStore(subscribe, paintedTheme)
/** What the owner chose: Light, Dark, or the system's theme. */
export const useThemeChoice = () => useSyncExternalStore(subscribe, themeChoice)
