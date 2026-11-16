import { chooseTheme, useTheme } from '../theme'
import { IconButton, MoonIcon, SunIcon } from './MastheadIcons'

/** The masthead's theme switch: a moon in the light theme, a sun in the dark one. */
export const ThemeControl = () => {
  const theme = useTheme()
  return (
    <IconButton
      className="theme-toggle"
      label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
      aria-pressed={theme === 'dark'}
      onClick={() => chooseTheme(theme === 'dark' ? 'light' : 'dark')}
    >
      {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
    </IconButton>
  )
}
