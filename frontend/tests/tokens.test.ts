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
})
