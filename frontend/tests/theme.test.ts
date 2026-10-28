import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

// The shell's blocking head script, evaluated as the browser runs it.
const script = readFileSync(resolve(process.cwd(), 'public/theme.js'), 'utf8')

const paint = (saved: 'light' | 'dark' | null, system: 'light' | 'dark' | null) => {
  if (saved) document.cookie = `ml_theme=${saved}; Path=/`
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
    document.cookie = 'ml_theme=; Path=/; Max-Age=0'
    vi.unstubAllGlobals()
  })

  it('applies a saved choice over the system, and never saves one itself', () => {
    expect(paint('dark', 'light')).toBe('dark')
    expect(paint('light', 'dark')).toBe('light')
  })

  it('follows the system when nothing is saved', () => {
    expect(paint(null, 'dark')).toBe('dark')
    expect(paint(null, 'light')).toBe('light')
  })

  it('paints light where the browser cannot report a system theme', () => {
    expect(paint(null, null)).toBe('light')
  })
})
