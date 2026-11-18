import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  FormattedDescription,
  formattedLength,
  isFormatted,
  readFormatting,
  visibleText,
} from '../src/descriptionFormatting'

// The formatted content inside its own block, or the value as written.
const html = (value: string, limit?: number, cut?: boolean) => {
  const { container } = render(
    <FormattedDescription value={value} limit={limit} cut={cut}>
      {value}
    </FormattedDescription>,
  )
  const block = container.querySelector(':scope > .description-formatted')
  return block ? block.innerHTML : container.innerHTML
}
const asWritten = (value: string) => {
  expect(isFormatted(value)).toBe(false)
  const { container } = render(<FormattedDescription value={value}>{value}</FormattedDescription>)
  expect(container.querySelector('*')).toBeNull()
  expect(container.textContent).toBe(value)
}

describe('description formatting', () => {
  it('shows bold, italic, underline, paragraphs, breaks and lists as elements', () => {
    expect(html('<p>One <b>bold</b> and <i>slanted</i></p><p>Two<br>lines <u>u</u></p>')).toBe(
      '<p>One <strong>bold</strong> and <em>slanted</em></p><p>Two<br>lines <u>u</u></p>',
    )
    expect(html('<ul><li>a</li><li><strong>b</strong></li></ul>')).toBe(
      '<ul><li>a</li><li><strong>b</strong></li></ul>',
    )
    expect(html('Plain then <em>emphasis</em>.')).toBe('Plain then <em>emphasis</em>.')
  })

  it('drops attributes and other tags, keeping their text', () => {
    expect(html('<p class="x" style="color:red">Hi <span title="t">there</span></p>')).toBe(
      '<p>Hi there</p>',
    )
    expect(html('<img src="https://example.invalid/x.png">Shown')).toBe('Shown')
    expect(html('<h2>Heading</h2><div>Block</div>')).toBe('<p>Heading</p><p>Block</p>')
    expect(html('<font face="x">Old</font> <custom-el>new</custom-el>')).toBe('Old new')
    // A harmless attribute that merely contains "on" is no handler.
    expect(html('<p title="a one" lang=en>x</p>')).toBe('<p>x</p>')
  })

  it('shows a link as written', () => {
    asWritten('<a href="javascript:alert(1)">Link text</a>')
    asWritten('<p>Read <a href="https://example.invalid/">more</a>.</p>')
    asWritten('<p>Read <A HREF="https://example.invalid/">more</A>.</p>')
    asWritten('<b>a</b><a/>')
  })

  it('keeps strikethrough as Audiobookshelf does', () => {
    expect(html('<del>a</del> <s>b</s> <strike>c</strike>')).toBe(
      '<del>a</del> <del>b</del> <del>c</del>',
    )
  })

  it('treats a horizontal rule as a break', () => {
    expect(html('<b>One</b><hr>Two<hr/>Three')).toBe('<strong>One</strong><br>Two<br>Three')
  })

  it('reads tags in any case, with spaces, self-closing breaks and bare attributes', () => {
    expect(html('<B>a</B><BR><I >b</I ><br/>c<br />d</br><p hidden>e</p>')).toBe(
      '<strong>a</strong><br><em>b</em><br>c<br>d<p>e</p>',
    )
    // Not a tag at all: kept as text.
    expect(html('<b>x</b> < b> y')).toBe('<strong>x</strong> &lt; b&gt; y')
    // A closing bracket or comment the server refuses: shown as written.
    asWritten('<b>x</b></ b>')
    asWritten('<b>x</b><!DOCTYPE html>')
    asWritten('<b>x</b><?xml version="1.0"?>')
    asWritten('<!-- a --><b>x</b>')
    asWritten('<b"x">y</b>')
    // A bracket inside a quoted attribute is the attribute's, as on the server.
    expect(html('<p title="a>b">x</p><p title=\'c<d\'>y</p>')).toBe('<p>x</p><p>y</p>')
  })

  it('keeps the page valid: no block inside a paragraph or inline element, list items only in lists', () => {
    expect(html('<div><div>a</div><div>b</div></div>')).toBe('<p>a<br>b</p>')
    expect(html('<strong><p>a</p><p>b</p></strong>')).toBe('<strong>a<br>b</strong>')
    expect(html('<p>a<ul><li>x</li><li>y</li></ul></p>')).toBe('<p>a<br>x<br>y</p>')
    expect(html('<li>alone</li>')).toBe('<p>alone</p>')
    expect(html('<ul> <li>a</li> loose <b>text</b> <li>b</li></ul>')).toBe(
      '<ul><li>a</li><li> loose <strong>text</strong> </li><li>b</li></ul>',
    )
    expect(html('<ul><li>a<ul><li>b</li></ul></li></ul>')).toBe(
      '<ul><li>a<ul><li>b</li></ul></li></ul>',
    )
    expect(html('<ol><li><p>a</p><p>b</p></li></ol>')).toBe('<ol><li><p>a</p><p>b</p></li></ol>')
    expect(html('<ul><ul><li>x</li></ul></ul>')).toBe('<ul><li><ul><li>x</li></ul></li></ul>')
  })

  it('shows markup nested deeper than 32 elements as written', () => {
    const nested = (depth: number) => `${'<b>'.repeat(depth)}x${'</b>'.repeat(depth)}`
    expect(isFormatted(nested(32))).toBe(true)
    asWritten(nested(33))
  })

  it('reads a 16,384-character hostile value in linear time', () => {
    const fill = (head: string, unit: string, tail = '') =>
      head + unit.repeat(Math.floor((16_384 - head.length - tail.length) / unit.length)) + tail
    const cases: [string, boolean][] = [
      [fill('<b', ' ', 'x'), false],
      [fill('<b>a</b><i', ' '), false],
      [fill('<p title="', "'>"), false],
      [fill("<p title='", '" a="'), false],
      [fill('', '<b>'), false],
      [fill('<b', ' a', '>x</b>'), true],
      [fill('', '<b>a</b>'), true],
      [fill('<p>', '&amp;', '</p>'), true],
    ]
    for (const [value, formatted] of cases) {
      expect(value.length).toBeLessThanOrEqual(16_384)
      const started = performance.now()
      const nodes = readFormatting(value)
      expect(performance.now() - started).toBeLessThan(250)
      expect(nodes !== null).toBe(formatted)
    }
  })

  it('shows markup that shows no words as written', () => {
    asWritten('<b></b>')
    asWritten('<p> <br> </p>')
  })

  it('shows a value over the description bound as written', () => {
    const value = `<b>${'a'.repeat(16_384)}</b>`
    expect(isFormatted(value)).toBe(false)
    expect(formattedLength(value)).toBe(value.length)
  })

  it('decodes character references once inside formatted text', () => {
    expect(html('<b>Fish &amp; chips &lt;b&gt;</b>')).toBe(
      '<strong>Fish &amp; chips &lt;b&gt;</strong>',
    )
    expect(html('<b>&amp;lt;b&amp;gt;</b>')).toBe('<strong>&amp;lt;b&amp;gt;</strong>')
    expect(html('<b>&#0;&#x1F600;</b>')).toBe('<strong>&amp;#0;😀</strong>')
  })

  it.each([
    ['C0', '&#1;'],
    ['DEL', '&#127;'],
    ['C1', '&#x85;'],
    ['C1 as decimal', '&#150;'],
    ['surrogate', '&#xD800;'],
    ['line separator', '&#x2028;'],
    ['paragraph separator', '&#x2029;'],
    ['noncharacter', '&#xFDD0;'],
    ['FFFE', '&#xFFFE;'],
    ['FFFF on a plane', '&#x1FFFF;'],
    ['out of range', '&#x110000;'],
    ['Arabic letter mark', '&#x61C;'],
    ['left-to-right mark', '&#x200E;'],
    ['named right-to-left mark', '&rlm;'],
    ['embedding', '&#x202A;'],
    ['override', '&#x202E;'],
    ['isolate', '&#x2066;'],
    ['pop isolate', '&#x2069;'],
  ])('leaves a reference to a refused code point as written: %s', (_name, reference) => {
    expect(visibleText(`<b>a${reference}b</b>`)).toBe(`a${reference}b`)
  })

  it('decodes named references', () => {
    expect(
      visibleText('<p>Caf&eacute; &mdash; &Omega; &hearts; &euro; &copy; &apos;x&apos;</p>'),
    ).toBe("Café — Ω ♥ € © 'x'")
    expect(visibleText('<p>a&nbsp;b &unknown; &AMP;</p>')).toBe('a b &unknown; &AMP;')
  })

  it('leaves an escaped tag as text', () => {
    expect(isFormatted('&lt;b&gt;bold&lt;/b&gt;')).toBe(false)
    expect(html('<p>&lt;script&gt;x&lt;/script&gt;</p>')).toBe(
      '<p>&lt;script&gt;x&lt;/script&gt;</p>',
    )
  })

  it.each([
    ['script', '<b>a</b><script>alert(1)</script>'],
    ['style', '<style>p{}</style><b>a</b>'],
    ['handler', '<b onclick="alert(1)">a</b>'],
    ['handler in capitals', '<b ONLOAD=x>a</b>'],
    ['handler after another attribute', '<p class=x onmouseover=alert(1)>a</p>'],
    ['svg', '<svg><b>a</b></svg>'],
    ['comment', '<b>a</b><!-- c -->'],
    ['unclosed', '<b>a'],
    ['misnested', '<b><i>a</b></i>'],
    ['stray close', 'a</b>'],
    ['unterminated tag', '<b>a</b><i'],
    ['bracket outside quotes', '2 < 3 <b>a</b>'],
    ['unbalanced quote', '<p title="a>x</p>'],
  ])('shows %s as written', (_name, value) => {
    asWritten(value)
  })

  it('leaves text without markup alone, including a lone angle bracket', () => {
    expect(isFormatted('2 < 3 and 5 > 4')).toBe(false)
    expect(html('Fish &amp; chips')).toBe('Fish &amp;amp; chips')
  })

  it('cuts by visible characters, never inside a tag', () => {
    expect(html('<p><b>abcdef</b>ghij</p>', 4)).toBe('<p><strong>abcd…</strong></p>')
    expect(formattedLength('<p><b>abcdef</b>ghij</p>')).toBe(10)
  })

  it('ends a cut with an ellipsis on a node boundary', () => {
    expect(html('<p><b>abcd</b>ghij</p>', 4)).toBe('<p><strong>abcd…</strong></p>')
    expect(html('<p>ab</p><p>cd</p>', 2)).toBe('<p>ab…</p>')
  })

  it('reads a value Activity cut short', () => {
    expect(html('<p>One <b>bold and mo…', undefined, true)).toBe(
      '<p>One <strong>bold and mo…</strong></p>',
    )
    expect(html('<p>One</p><p>Two <b class="x…', undefined, true)).toBe('<p>One</p><p>Two …</p>')
    // Only a value Activity cut is read leniently.
    asWritten('<p>One <b>bold')
    expect(isFormatted('<p>One <b>bold…')).toBe(false)
  })

  it('still refuses a cut value holding a refused element', () => {
    for (const value of [
      '<p>a</p><script>alert(1)…',
      '<p onclick=x>a…',
      '<p>a</p><a href="https://example.invalid/">b…',
      '<p>a</b>…',
    ])
      expect(readFormatting(value, true)).toBeNull()
  })

  it('builds only elements from the closed set, with no attributes', () => {
    const corpus = [
      '<p class="a" style="b" id="c">x</p>',
      '<img src=x onerror=alert(1)>Hi',
      '<b>a</b><img src="https://example.invalid/i.png">',
      '<span style="background:url(https://example.invalid/)">x</span>',
      '<form action="/"><input name=x><button>Go</button></form>',
      '<h1>Big</h1><table><tr><td>cell</td></tr></table>',
      '<video src=x></video><audio src=y></audio><b>z</b>',
      '<base href="https://example.invalid/"><link rel=stylesheet href=x><meta charset=x>',
      '<details open><summary>s</summary>d</details>',
      '<ul><li data-x="1">a</li></ul><ol start=3><li>b</li></ol>',
      '<b>a</b><script>x</script>',
      '<a href="https://example.invalid/">a</a>',
      '&lt;img src=x onerror=alert(1)&gt;<b>b</b>',
    ]
    const allowed = new Set(['STRONG', 'EM', 'U', 'DEL', 'P', 'BR', 'UL', 'OL', 'LI'])
    for (const value of corpus) {
      const { container } = render(
        <FormattedDescription value={value}>{value}</FormattedDescription>,
      )
      for (const element of container.querySelectorAll('.description-formatted *')) {
        expect(allowed.has(element.tagName)).toBe(true)
        expect(element.attributes.length).toBe(0)
      }
      expect(container.querySelectorAll('*').length).toBe(
        container.querySelectorAll('.description-formatted, .description-formatted *').length,
      )
    }
  })

  it('names a formatted description by its visible text', () => {
    expect(visibleText('<p>One <b>bold</b></p><p>Two</p>')).toBe('One bold Two')
    expect(visibleText('<ul><li>a</li><li>b <i>c</i></li></ul>Then<br>after')).toBe(
      'a b c Then after',
    )
    expect(visibleText('<b onclick=x>a</b>')).toBe('<b onclick=x>a</b>')
  })

  it('hands no description markup to a browser parser', () => {
    // Vitest runs from the frontend directory.
    const root = join(process.cwd(), 'src')
    const sources = readdirSync(root, { recursive: true, encoding: 'utf8' })
      .filter((file) => /\.tsx?$/u.test(file))
      .map((file) => [file, readFileSync(join(root, file), 'utf8')] as const)
    expect(sources.length).toBeGreaterThan(50)
    for (const [file, text] of sources)
      for (const name of [
        'dangerouslySetInnerHTML',
        'innerHTML',
        'insertAdjacentHTML',
        'createContextualFragment',
      ])
        expect(text.includes(name), `${file} uses ${name}`).toBe(false)
    const parsers = sources.filter(([, text]) => text.includes('DOMParser'))
    expect(parsers.map(([file]) => file)).toEqual([join('api', 'http.ts')])
    expect(parsers[0]?.[1].split('DOMParser').length).toBe(2)
  })
})
