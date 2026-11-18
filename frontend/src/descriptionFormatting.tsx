import type { ReactNode } from 'react'
import { NAMED_REFERENCES } from './descriptionReferences'

// A description's formatting, read from the markup it carries, and nothing
// else. Descriptions come from providers, the library and the adviser, so
// they are untrusted: this never hands markup to the browser to parse into
// the page. It scans the text itself and builds a closed set of elements with
// text children and no attributes, so no attribute, URL, style or script from
// the text ever reaches the page. A description it cannot read that way is
// shown as written, as before (ADR-0106).

type Shown = 'strong' | 'em' | 'u' | 'del' | 'p' | 'ul' | 'ol' | 'li'
type FormatNode =
  | { kind: 'text'; text: string }
  | { kind: 'element'; tag: Shown; children: FormatNode[] }
  | { kind: 'break' }

const SHOWN: Record<string, Shown | undefined> = {
  b: 'strong',
  strong: 'strong',
  i: 'em',
  em: 'em',
  u: 'u',
  del: 'del',
  s: 'del',
  strike: 'del',
  p: 'p',
  div: 'p',
  // Headings read as paragraphs, so a description never adds to the page's outline.
  h1: 'p',
  h2: 'p',
  h3: 'p',
  h4: 'p',
  h5: 'p',
  h6: 'p',
  ul: 'ul',
  ol: 'ol',
  li: 'li',
}
// Elements whose content is not text a reader sees, the server renderer's
// refusals and more, and links, which Audiobookshelf keeps and would be
// written unseen: a description holding one is shown as written.
const REFUSED = new Set([
  'script',
  'style',
  'iframe',
  'object',
  'embed',
  'svg',
  'math',
  'template',
  'textarea',
  'title',
  'noscript',
  'xmp',
  'plaintext',
  'noembed',
  'noframes',
  'a',
])
const VOID = new Set([
  'area',
  'base',
  'br',
  'col',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'param',
  'source',
  'track',
  'wbr',
])
const BLOCK = new Set<Shown>(['p', 'ul', 'ol', 'li'])
// The server's description bound: a longer value is shown as written, unread.
const MAX_DESCRIPTION_CHARACTERS = 16_384
const MAX_DEPTH = 32
const STARTS_TAG = /<[A-Za-z/!?]/u
// Sticky patterns, each read at one index of a bounded tag: none overlaps
// another, so reading a tag is linear in its length.
const NAME = /[A-Za-z][A-Za-z0-9-]*/uy
const SPACE = /\s*/uy
const ATTRIBUTE_NAME = /[^\s"'>/=]+/uy
const UNQUOTED_VALUE = /[^\s"'=<>`]+/uy
const REFERENCE = /&(#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,7});/gu
const BIDI_CONTROLS = new Set([
  0x061c, 0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069,
])

// The code points the server refuses in a value: a reference to one stays as written.
const refusedCodePoint = (code: number) =>
  code < 0x20 ||
  (code >= 0x7f && code <= 0x9f) ||
  (code >= 0xd800 && code <= 0xdfff) ||
  code === 0x2028 ||
  code === 0x2029 ||
  (code >= 0xfdd0 && code <= 0xfdef) ||
  (code & 0xffff) >= 0xfffe ||
  code > 0x10ffff ||
  BIDI_CONTROLS.has(code)

const decode = (text: string) =>
  text.replace(REFERENCE, (whole, ref: string) => {
    const code = ref.startsWith('#')
      ? ref[1] === 'x' || ref[1] === 'X'
        ? parseInt(ref.slice(2), 16)
        : Number(ref.slice(1))
      : NAMED_REFERENCES.get(ref)
    if (code === undefined || refusedCodePoint(code)) return whole
    // A no-break space reads as the space it shows, as on the server.
    return code === 0xa0 ? ' ' : String.fromCodePoint(code)
  })

const skipSpace = (body: string, at: number) => {
  SPACE.lastIndex = at
  SPACE.exec(body)
  return SPACE.lastIndex
}
const sticky = (pattern: RegExp, body: string, at: number) => {
  pattern.lastIndex = at
  return pattern.exec(body) ? pattern.lastIndex : -1
}

interface Tag {
  closing: boolean
  name: string
  selfClosing: boolean
}

// Read one tag from the text between its brackets: the tag, 'text' where it
// is not a tag at all (`< b>`), or null where the description is refused.
function readTag(body: string): Tag | 'text' | null {
  if (body.startsWith('!') || body.startsWith('?')) return null
  const closing = body.startsWith('/')
  const nameEnd = sticky(NAME, body, closing ? 1 : 0)
  if (nameEnd < 0) return closing ? null : 'text'
  const name = body.slice(closing ? 1 : 0, nameEnd).toLowerCase()
  let at = skipSpace(body, nameEnd)
  if (closing) return at === body.length ? { closing, name, selfClosing: false } : null
  if (at === nameEnd && at < body.length && body[at] !== '/') return null
  while (at < body.length) {
    if (body[at] === '/') {
      if (skipSpace(body, at + 1) !== body.length) return null
      return { closing, name, selfClosing: true }
    }
    const attributeEnd = sticky(ATTRIBUTE_NAME, body, at)
    if (attributeEnd < 0) return null
    // An event handler, as the server refuses one: any name starting `on`.
    if (body.slice(at, at + 2).toLowerCase() === 'on') return null
    at = skipSpace(body, attributeEnd)
    if (body[at] === '=') {
      at = skipSpace(body, at + 1)
      const quote = body[at]
      if (quote === '"' || quote === "'") {
        const close = body.indexOf(quote, at + 1)
        if (close < 0) return null
        at = close + 1
      } else {
        at = sticky(UNQUOTED_VALUE, body, at)
        if (at < 0) return null
      }
      at = skipSpace(body, at)
    }
  }
  return { closing, name, selfClosing: false }
}

interface Open {
  name: string
  children: FormatNode[]
}

const append = (target: FormatNode[], nodes: readonly FormatNode[]) => {
  for (const node of nodes) target.push(node)
}
const close = (stack: Open[], closed: Open) => {
  const parent = stack.at(-1)?.children ?? []
  const shown = SHOWN[closed.name]
  if (shown) parent.push({ kind: 'element', tag: shown, children: closed.children })
  else append(parent, closed.children)
}

/**
 * Read a description's formatting, or `null` where it is shown as written: no
 * markup, a refused element or a link, an event handler, a comment or
 * declaration, markup that does not close as it opens, nesting deeper than 32
 * elements, a value over the description bound, or one that shows no words.
 * With `cut`, a value ending in `…` (cut short by Activity) drops a trailing
 * incomplete tag and closes what is still open; every refusal still applies to
 * what remains.
 */
export function readFormatting(value: string, cut = false): FormatNode[] | null {
  if (value.length > MAX_DESCRIPTION_CHARACTERS || !STARTS_TAG.test(value)) return null
  const tolerant = cut && value.endsWith('…')
  let source = tolerant ? value.slice(0, -1) : value
  const root: FormatNode[] = []
  const stack: Open[] = [{ name: '', children: root }]
  const top = () => stack.at(-1)?.children ?? root
  let position = 0
  let from = 0
  // Whether any words were read, set as text is flushed.
  const read = { words: false }
  const flush = (end: number) => {
    if (end <= position) return
    const text = decode(source.slice(position, end))
    read.words ||= /\S/u.test(text)
    top().push({ kind: 'text', text })
  }
  for (;;) {
    const start = source.indexOf('<', from)
    if (start < 0) break
    // Walk to the closing bracket, as the server's pre-scan does: a bracket
    // inside quotes is the attribute's, and another `<` outside them refuses.
    let quote = ''
    let end = start + 1
    for (; end < source.length; end += 1) {
      const character = source[end]
      if (quote) {
        if (character === quote) quote = ''
      } else if (character === '"' || character === "'") quote = character
      else if (character === '<') return null
      else if (character === '>') break
    }
    if (end >= source.length) {
      if (!tolerant || !STARTS_TAG.test(source.slice(start, start + 2))) return null
      source = source.slice(0, start)
      break
    }
    const tag = readTag(source.slice(start + 1, end))
    if (tag === null) return null
    from = end + 1
    if (tag === 'text') continue
    if (REFUSED.has(tag.name)) return null
    flush(start)
    position = end + 1
    if (tag.closing) {
      // `</br>` and other void closings are ignored.
      if (VOID.has(tag.name)) continue
      const closed = stack.pop()
      if (closed?.name !== tag.name || !stack.length) return null
      close(stack, closed)
    } else if (tag.name === 'br' || tag.name === 'hr') top().push({ kind: 'break' })
    else if (!VOID.has(tag.name) && !tag.selfClosing) {
      if (stack.length > MAX_DEPTH) return null
      stack.push({ name: tag.name, children: [] })
    }
  }
  flush(source.length)
  if (tolerant) {
    top().push({ kind: 'text', text: '…' })
    while (stack.length > 1) {
      const open = stack.pop()
      if (open) close(stack, open)
    }
  }
  // Markup that shows no words is not a description to format, as on the server.
  return stack.length === 1 && read.words ? normalise(root, 'flow') : null
}

// Valid HTML from what was read: no block inside a paragraph or an inline
// element (its words are inlined, a break between), list items only in lists.
function normalise(nodes: readonly FormatNode[], context: 'flow' | 'inline' | 'list') {
  const out: FormatNode[] = []
  if (context === 'list') {
    let run: FormatNode[] = []
    const endRun = () => {
      if (run.length) out.push({ kind: 'element', tag: 'li', children: normalise(run, 'flow') })
      run = []
    }
    for (const node of nodes) {
      if (node.kind === 'element' && node.tag === 'li') {
        endRun()
        out.push({ ...node, children: normalise(node.children, 'flow') })
      } else if (run.length || node.kind !== 'text' || node.text.trim()) run.push(node)
    }
    endRun()
    return out
  }
  let afterBlock = false
  const breakUnlessThere = () => {
    const last = out[out.length - 1]
    if (last && last.kind !== 'break') out.push({ kind: 'break' })
  }
  for (const node of nodes) {
    if (node.kind !== 'element') {
      if (afterBlock) {
        if (node.kind === 'text' && !node.text.trim()) continue
        breakUnlessThere()
        afterBlock = false
      }
      out.push(node)
    } else if (context === 'inline' && BLOCK.has(node.tag)) {
      breakUnlessThere()
      append(out, normalise(node.children, 'inline'))
      afterBlock = true
    } else {
      if (afterBlock) breakUnlessThere()
      afterBlock = false
      const tag = node.tag === 'li' ? 'p' : node.tag
      const inner = tag === 'ul' || tag === 'ol' ? 'list' : 'inline'
      out.push({ kind: 'element', tag, children: normalise(node.children, inner) })
    }
  }
  return out
}

const textLength = (nodes: readonly FormatNode[]): number =>
  nodes.reduce(
    (total, node) =>
      total +
      (node.kind === 'text'
        ? node.text.length
        : node.kind === 'break'
          ? 1
          : textLength(node.children)),
    0,
  )

/** The words a reader sees, on one line, for names, sentences and single lines. */
export const visibleText = (value: string): string => {
  const nodes = readFormatting(value)
  if (!nodes) return value
  const walk = (list: readonly FormatNode[]): string =>
    list
      .map((node) =>
        node.kind === 'text'
          ? node.text
          : node.kind === 'break'
            ? '\n'
            : BLOCK.has(node.tag)
              ? `\n${walk(node.children)}\n`
              : walk(node.children),
      )
      .join('')
  return walk(nodes)
    .split('\n')
    .map((line) => line.replace(/\s+/gu, ' ').trim())
    .filter(Boolean)
    .join(' ')
}

// Keep at most `limit` visible characters.
function cutTo(nodes: readonly FormatNode[], limit: number): FormatNode[] {
  const kept: FormatNode[] = []
  let left = limit
  for (const node of nodes) {
    if (left <= 0) break
    if (node.kind === 'text') {
      kept.push(node.text.length > left ? { kind: 'text', text: node.text.slice(0, left) } : node)
      left -= node.text.length
    } else if (node.kind === 'break') {
      kept.push(node)
      left -= 1
    } else {
      const length = textLength(node.children)
      kept.push({ ...node, children: length > left ? cutTo(node.children, left) : node.children })
      left -= length
    }
  }
  return kept
}

// End a cut with an ellipsis at its last words, wherever the cut fell.
function withEllipsis(nodes: readonly FormatNode[]): FormatNode[] {
  const last = nodes[nodes.length - 1]
  const head = nodes.slice(0, -1)
  if (last?.kind === 'text') return [...head, { kind: 'text', text: `${last.text.trimEnd()}…` }]
  if (last?.kind === 'element' && last.children.length)
    return [...head, { ...last, children: withEllipsis(last.children) }]
  return [...nodes, { kind: 'text', text: '…' }]
}

function render(nodes: readonly FormatNode[], key = 'n'): ReactNode[] {
  return nodes.map((node, index) => {
    const id = `${key}.${String(index)}`
    if (node.kind === 'text') return node.text
    if (node.kind === 'break') return <br key={id} />
    const Tag = node.tag
    return <Tag key={id}>{render(node.children, id)}</Tag>
  })
}

type FormattedProps = { className?: string | undefined; limit?: number | undefined } & (
  | { nodes: readonly FormatNode[] }
  // Read from `value`; `children` show where it is not formatted.
  | { value: string; cut?: boolean | undefined; children: ReactNode }
)

/** A description's formatting in its own block, or `children` when it has none to show. */
export function FormattedDescription(props: FormattedProps) {
  const nodes = 'nodes' in props ? props.nodes : readFormatting(props.value, props.cut)
  if (!nodes) return <>{'children' in props ? props.children : null}</>
  const { limit, className } = props
  const shown = limit !== undefined && textLength(nodes) > limit
  return (
    <div className={className ? `description-formatted ${className}` : 'description-formatted'}>
      {render(shown ? withEllipsis(cutTo(nodes, limit)) : nodes)}
    </div>
  )
}

export const formattedLength = (
  value: string,
  nodes: readonly FormatNode[] | null = readFormatting(value),
) => (nodes ? textLength(nodes) : value.length)
export const isFormatted = (value: string) => readFormatting(value) !== null
