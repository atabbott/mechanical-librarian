import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

// The shell's blocking head script, evaluated as the browser runs it.
const script = readFileSync(resolve(process.cwd(), 'public/theme.js'), 'utf8')

// `saved` is the cookie pair exactly as the browser would hold it.
const paint = (saved: string | null, system: 'light' | 'dark' | null) => {
  if (saved) document.cookie = `${saved}; Path=/`
  vi.stubGlobal(
    'matchMedia',
    system === null
      ? undefined
      : (query: string) => ({
          matches: query === `(prefers-color-scheme: ${system})`,
          media: query,
        }),
  )
  delete document.documentElement.dataset.theme
  const write = vi.spyOn(document, 'cookie', 'set')
  window.eval(script)
  expect(write).not.toHaveBeenCalled()
  write.mockRestore()
  return document.documentElement.dataset.theme
}

describe('Theme before first paint', () => {
  afterEach(() => {
    for (const name of ['ml_theme', 'xml_theme']) {
      document.cookie = `${name}=; Path=/; Max-Age=0`
    }
    vi.unstubAllGlobals()
  })

  it('applies a saved choice over the system, and never saves one itself', () => {
    expect(paint('ml_theme=dark', 'light')).toBe('dark')
    expect(paint('ml_theme=light', 'dark')).toBe('light')
  })

  it('follows the system when nothing is saved', () => {
    expect(paint(null, 'dark')).toBe('dark')
    expect(paint(null, 'light')).toBe('light')
  })

  it('reads only its own cookie, never one whose name merely ends the same', () => {
    expect(paint('xml_theme=dark', 'light')).toBe('light')
    expect(paint('xml_theme=light', 'dark')).toBe('dark')
  })

  it('follows the system when the saved value is quoted or malformed', () => {
    expect(paint('ml_theme="dark"', 'light')).toBe('light')
    expect(paint('ml_theme=darkish', 'light')).toBe('light')
    expect(paint('ml_theme=Light', 'dark')).toBe('dark')
    expect(paint('ml_theme=', 'dark')).toBe('dark')
  })

  it('paints light where the browser cannot report a system theme', () => {
    expect(paint(null, null)).toBe('light')
  })
})
