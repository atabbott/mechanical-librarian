import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8')
const styles = read('src/styles.css')
const sheets = [styles, read('public/preparation-handoff.css')].join('\n')

// A var() with no fallback must name a property some rule declares.
const undefinedTokens = (css: string) => {
  const declared = new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]))
  const used = [...css.matchAll(/var\(\s*(--[\w-]+)\s*\)/g)].map((match) => match[1])
  return [...new Set(used)].filter((token) => !declared.has(token))
}
const block = (selector: string) => {
  const start = styles.indexOf(`${selector} {`)
  return styles.slice(start, styles.indexOf('}', start))
}
const declarations = (css: string) =>
  [...css.matchAll(/(--[\w-]+)\s*:([^;]*)/g)].map(
    ([, token, value = '']) => [token, value] as const,
  )

// Every rule as selector and body, comments removed; a media query's rules
// are read as their own rules.
const rules = (css: string) =>
  [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(
    ([, selector = '', body = '']) => ({ selector: selector.trim(), body }),
  )
const property = (body: string, name: string) =>
  new RegExp(`(?:^|;)\\s*${name}\\s*:\\s*([^;]+)`).exec(body)?.[1]?.trim()

// sRGB colour with alpha, from the subset the stylesheet uses: hex, white,
// transparent, var() and color-mix(in srgb, …), mixed premultiplied as CSS does.
type Rgba = readonly [number, number, number, number]
const theme = (dark: boolean) =>
  new Map<string | undefined, string>([
    ...declarations(block(':root')),
    ...(dark ? declarations(block(":root[data-theme='dark']")) : []),
  ])
const color = (value: string, tokens: Map<string | undefined, string>): Rgba => {
  const text = value.trim()
  const token = /^var\((--[\w-]+)\)$/.exec(text)?.[1]
  if (token) return color(tokens.get(token) ?? '', tokens)
  if (text === 'white') return [255, 255, 255, 1]
  if (text === 'transparent') return [0, 0, 0, 0]
  const mix = /^color-mix\(in srgb,\s*(.+?)\s+([\d.]+)%\s*,\s*(.+)\)$/.exec(text)
  if (mix) {
    const [a, b, share] = [
      color(mix[1] ?? '', tokens),
      color(mix[3] ?? '', tokens),
      Number(mix[2]) / 100,
    ]
    const alpha = a[3] * share + b[3] * (1 - share)
    const channel = (i: number) =>
      ((a[i] ?? 0) * a[3] * share + (b[i] ?? 0) * b[3] * (1 - share)) / alpha
    return [channel(0), channel(1), channel(2), alpha]
  }
  const hex = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(text)?.[1]
  if (!hex) throw new Error(`unreadable colour ${text}`)
  const full = hex.length === 3 ? hex.replace(/./g, '$&$&') : hex
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)).concat(1) as unknown as Rgba
}
const over = (top: Rgba, ground: Rgba): Rgba =>
  [0, 1, 2]
    .map((i) => (top[i] ?? 0) * top[3] + (ground[i] ?? 0) * (1 - top[3]))
    .concat(1) as unknown as Rgba
const luminance = (rgba: Rgba) =>
  [0.2126, 0.7152, 0.0722].reduce((sum, weight, i) => {
    const value = (rgba[i] ?? 0) / 255
    return sum + weight * (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
  }, 0)
const contrast = (foreground: Rgba, ground: Rgba) => {
  const [a, b] = [luminance(over(foreground, ground)), luminance(ground)]
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}
const grounds = ['--page', '--surface', '--surface-strong', '--accent-soft', '--notice-soft']
// Pairs below their threshold, named for the message, in one theme.
const failing = (
  dark: boolean,
  pairs: readonly (readonly [string, string, number])[],
): string[] => {
  const tokens = theme(dark)
  return pairs
    .map(([foreground, ground, minimum]) => {
      const ratio = contrast(color(foreground, tokens), color(ground, tokens))
      return ratio < minimum ? `${foreground} on ${ground}: ${ratio.toFixed(2)}` : ''
    })
    .filter(Boolean)
}
const textPairs = [
  '--ink',
  '--soft',
  '--accent',
  '--accent-strong',
  '--notice',
  '--danger',
].flatMap((text) =>
  [...grounds, '--danger-soft'].map((ground) => [`var(${text})`, `var(${ground})`, 4.5] as const),
)
const boundaryPairs = (line: string) =>
  grounds.map((ground) => [line, `var(${ground})`, 3] as const)

// The smallest size each font declaration can render, as a violation of the
// .75rem floor: rem below .75, px below 12, and em or % below the parent.
const belowFloor = (css: string) =>
  [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/font(?:-size)?\s*:\s*([^;}]+)/g)]
    .map(([declaration = '', value = '']) => {
      const size = /(?:clamp\(\s*)?(\d*\.?\d+)(rem|px|em|%)/.exec(value)
      if (!size) return ''
      const amount = Number(size[1])
      const floor = { rem: 0.75, px: 12, em: 1, '%': 100 }[size[2] as 'rem' | 'px' | 'em' | '%']
      return amount < floor ? declaration.trim() : ''
    })
    .filter(Boolean)

const ladder = new Set(['1200', '1199', '1024', '1025', '760', '761', '390', '391'])
const offLadder = (css: string) =>
  [...css.matchAll(/@media[^{]*/g)]
    .flatMap(([query]) => [...query.matchAll(/(?:min|max)-width:\s*(\d+)px/g)])
    .map((match) => match[1] ?? '')
    .filter((width) => !ladder.has(width))

describe('Design tokens', () => {
  it('places every width query on the 1200 / 1024 / 760 / 390 ladder', () => {
    expect(offLadder(styles)).toEqual([])
    expect(offLadder('@media (max-width:900px) and (min-width:761px) {}')).toEqual(['900'])
  })

  it('resolves every token a stylesheet uses without a fallback', () => {
    expect(undefinedTokens(sheets)).toEqual([])
    expect(undefinedTokens('a{color:var(--missing)} b{color:var(--set,red)}')).toEqual([
      '--missing',
    ])
  })

  it('gives the dark palette every light token that is not derived from others', () => {
    const light = declarations(block(':root'))
    const dark = declarations(block(":root[data-theme='dark']")).map(([token]) => token)
    const palette = light.filter(([, value]) => !value.includes('var(')).map(([token]) => token)
    expect(dark.sort()).toEqual(palette.sort())
    expect(light.map(([token]) => token)).toContain('--danger-soft')
  })

  it.each([false, true])('meets AA contrast for every listed pair (dark: %s)', (dark) => {
    // Text 4.5:1 on every surface, the primary label on its fill, and field
    // boundaries and the focus ring 3:1 on every surface they sit on.
    expect(failing(dark, textPairs)).toEqual([])
    expect(failing(dark, [['var(--surface)', 'var(--accent-strong)', 4.5]])).toEqual([])
    expect(failing(dark, boundaryPairs('var(--field-line)'))).toEqual([])
    // The strong catalogue line alone is a divider, not a field boundary.
    expect(failing(dark, boundaryPairs('var(--line-strong)'))).not.toEqual([])
    const ring = rules(styles).find(({ selector }) => selector === ':focus-visible')?.body ?? ''
    const outline = /^3px solid (.+)$/.exec(property(ring, 'outline') ?? '')?.[1] ?? ''
    expect(failing(dark, boundaryPairs(outline))).toEqual([])
    const marks = ['.brand-mark', '.library-book-mark', '.cm-policy-work-mark'].map((mark) => {
      const own = rules(styles).find(({ selector }) => selector === mark)?.body ?? ''
      const night = rules(styles).find(
        ({ selector, body }) =>
          selector.startsWith(":root[data-theme='dark']") &&
          selector.includes(mark) &&
          property(body, 'background'),
      )?.body
      const ground =
        (dark && night ? property(night, 'background') : property(own, 'background')) ?? ''
      return [property(own, 'color') ?? '', ground, 4.5] as const
    })
    expect(failing(dark, marks)).toEqual([])
  })

  it('keeps busy retained content at its text contrast', () => {
    // A busy list is marked busy, not dimmed below 4.5:1 for its soft text.
    // Its retained rows are aria-disabled, so neither a busy rule nor one on
    // every aria-disabled button may dim them.
    const reachesBusy = (selector: string) =>
      selector
        .split(',')
        .map((part) => part.trim())
        .some(
          (part) =>
            part.includes("[aria-busy='true']") ||
            /^(?:button)?\[aria-disabled='true'\]$/.test(part),
        )
    const dimmed = (css: string) =>
      rules(css)
        .filter(({ selector, body }) => reachesBusy(selector) && property(body, 'opacity'))
        .flatMap(({ selector, body }) => {
          const tokens = theme(false)
          const [soft, surface] = [color('var(--soft)', tokens), color('var(--surface)', tokens)]
          const opacity = Number(property(body, 'opacity'))
          const shown: Rgba = [soft[0], soft[1], soft[2], opacity]
          return contrast(shown, surface) < 4.5 ? [selector] : []
        })
    expect(dimmed(styles)).toEqual([])
    expect(dimmed(".list[aria-busy='true'] { opacity:.7; }")).toEqual([".list[aria-busy='true']"])
    expect(
      dimmed(
        "button:disabled, button[aria-disabled='true'] { opacity:.55; } .list[aria-busy='true'] button[aria-disabled='true'] { opacity:.55; } .cm-policy-route button[aria-disabled='true'] { opacity:.55; }",
      ),
    ).toEqual([
      "button:disabled, button[aria-disabled='true']",
      ".list[aria-busy='true'] button[aria-disabled='true']",
    ])
  })

  it('sets no type below the .75rem floor', () => {
    expect(belowFloor(sheets)).toEqual([])
    expect(
      belowFloor(
        'a{font-size:.7rem} b{font:700 .53rem/1 serif} c{font-size:clamp(.6rem,2vw,1rem)} d{font-size:.8em} e{font-size:11px} f{font-size:.75rem;font:inherit} g{font-size:clamp(12px,.75rem,14px)}',
      ),
    ).toEqual([
      'font-size:.7rem',
      'font:700 .53rem/1 serif',
      'font-size:clamp(.6rem,2vw,1rem)',
      'font-size:.8em',
      'font-size:11px',
    ])
  })
})
