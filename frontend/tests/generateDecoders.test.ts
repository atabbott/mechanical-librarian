import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { afterAll, describe, expect, expectTypeOf, it } from 'vitest'

import document from '../../docs/reference/review-openapi-v1.json'
import { lits } from '../src/api/http'
import * as generated from '../src/generated/review-decoders'
import type { D } from '../src/generated/review-decoders'
import {
  arr,
  d_LibrarySettings,
  dflt,
  int,
  lit,
  map,
  nullable,
  obj,
  opt,
  req,
  responseDecoders,
  str,
} from '../src/generated/review-decoders'

// Synthetic documents run through the real CLI. The directory sits under the
// ignored generated tree because Vitest only imports modules inside its root.
const scratch = mkdtempSync(path.resolve('src/generated/synthetic-'))
afterAll(() => rmSync(scratch, { recursive: true, force: true }))
let runs = 0

type Schema = Record<string, unknown>
const ok = { description: 'ok' }
const json = (schema: Schema) => ({ ...ok, content: { 'application/json': { schema } } })
const ref = (name: string) => ({ $ref: `#/components/schemas/${name}` })
const closed = (properties: Record<string, Schema>, required = Object.keys(properties)) => ({
  type: 'object',
  additionalProperties: false,
  properties,
  required,
})
// Rule P names LibrarySettings, so every synthetic document reaches one.
const settings = closed(
  {
    display_name: { anyOf: [{ type: 'string', minLength: 1 }, { type: 'null' }] },
    languages: { type: 'array', items: { type: 'string' }, default: [] },
    name: { type: 'string' },
  },
  ['name'],
)
const synthetic = (schemas: Record<string, unknown>, paths: Record<string, unknown> = {}) => ({
  openapi: '3.1.0',
  info: { title: 'synthetic', version: '1' },
  paths: {
    '/settings': {
      get: { operationId: 'settings', responses: { '200': json(ref('LibrarySettings')) } },
    },
    ...paths,
  },
  components: { schemas: { LibrarySettings: settings, ...schemas } },
})

const generate = (doc: unknown) => {
  const directory = path.join(scratch, `run-${String(runs++)}`)
  const input = path.join(scratch, `input-${String(runs)}.json`)
  writeFileSync(input, JSON.stringify(doc))
  const result = spawnSync(
    process.execPath,
    [
      'scripts/generate-dtos.mjs',
      '--input',
      input,
      '--output',
      path.join(directory, 'review-api.d.ts'),
    ],
    { encoding: 'utf8' },
  )
  return { status: result.status, stderr: result.stderr, directory }
}

interface Emitted {
  readonly responseDecoders: Record<string, Record<string, D<unknown>>>
  readonly matchOperation: (method: string, url: string) => string | undefined
  readonly [name: `d_${string}`]: D<unknown>
}
const load = async (doc: unknown) => {
  const { status, stderr, directory } = generate(doc)
  expect(stderr).toBe('')
  expect(status).toBe(0)
  return (await import(/* @vite-ignore */ path.join(directory, 'review-decoders.ts'))) as Emitted
}
// The emitted module owns its failure class, so read the plain fields.
const failure = (decode: () => unknown) => {
  try {
    decode()
  } catch (error) {
    expect(error).not.toBeInstanceOf(Error)
    const { at, reason } = error as { at: string; reason: string }
    return `${reason}@${at}`
  }
  throw new Error('decoding succeeded')
}

describe('the real review API document', () => {
  it('decodes every non-empty JSON 2xx response and nothing else', () => {
    const methods = ['get', 'put', 'post', 'delete', 'patch'] as const
    const expected = new Set<string>()
    for (const [template, item] of Object.entries(document.paths)) {
      for (const method of methods) {
        const operation = (item as Record<string, { responses: Record<string, Schema> }>)[method]
        for (const [status, response] of Object.entries(operation?.responses ?? {})) {
          const content = response.content as Record<string, { schema?: Schema }> | undefined
          const schema = content?.['application/json']?.schema
          if (!status.startsWith('2') || !schema) continue
          if (Object.keys(schema).some((key) => key !== 'title' && key !== 'description'))
            expected.add(`${method.toUpperCase()} ${template} ${status}`)
        }
      }
    }
    const emitted = Object.entries(responseDecoders).flatMap(([key, statuses]) =>
      Object.keys(statuses).map((status) => `${key} ${status}`),
    )
    expect(emitted).toHaveLength(65)
    expect(new Set(emitted)).toEqual(expected)
    expect(emitted.some((key) => key.startsWith('POST /api/v1/operations/support/export'))).toBe(
      false,
    )
    expect(Object.keys(generated).filter((name) => name.startsWith('d_'))).toHaveLength(171)
  })

  it('fills a fresh default per decode and leaves an absent display name absent (NC12)', () => {
    const value = { connection: 'primary', library_id: 'library-1', media_kind: 'ebook' }
    const wire = { ...value, mount: null, name: 'ebooks', policy_override: null }
    const first = d_LibrarySettings({ ...wire, provider_connections: [] }, '')
    const second = d_LibrarySettings({ ...wire, provider_connections: [] }, '')
    expect(first.languages).toEqual([])
    expect(first.languages).not.toBe(second.languages)
    expect(Object.hasOwn(first, 'display_name')).toBe(false)
    expect(failure(() => d_LibrarySettings(wire, ''))).toBe('required@/provider_connections')
  })
})

describe('the generator guard', () => {
  const property = (schema: unknown) =>
    synthetic(
      { Probe: closed({ value: schema as Schema }) },
      {
        '/probe': { get: { operationId: 'probe', responses: { '200': json(ref('Probe')) } } },
      },
    )
  const at = '#/components/schemas/Probe/properties/value'
  it.each([
    ['allOf', { allOf: [{ type: 'string' }] }, at],
    ['not', { not: { type: 'string' } }, at],
    ['if', { if: { type: 'string' } }, at],
    ['patternProperties', { type: 'object', patternProperties: {} }, at],
    ['minProperties', { type: 'object', minProperties: 1 }, at],
    ['exclusiveMinimum', { type: 'integer', exclusiveMinimum: 1 }, at],
    ['nullable', { type: 'string', nullable: true }, at],
    ['a list type', { type: ['string', 'null'] }, at],
    ['an unknown type', { type: 'file' }, at],
    ['an unknown format', { type: 'string', format: 'date-time' }, at],
    ['a pattern that does not compile', { type: 'string', pattern: '(' }, at],
    ['an external reference', { $ref: 'other.json#/X' }, at],
    ['a missing component', ref('Missing'), at],
    ['oneOf without a discriminator', { oneOf: [ref('LibrarySettings')] }, at],
    ['an open object with properties', { type: 'object', properties: {} }, at],
    ['a propertyNames pattern', { type: 'object', propertyNames: { pattern: '^a' } }, at],
    [
      'propertyNames on an object with no value schema',
      { type: 'object', propertyNames: { enum: ['a'] } },
      at,
    ],
    ['maxProperties on an object with no value schema', { type: 'object', maxProperties: 1 }, at],
    ['a non-object schema node', true, at],
  ])('refuses %s and names where it is', (_name, schema, location) => {
    const { status, stderr } = generate(property(schema))
    expect(status).not.toBe(0)
    expect(stderr).toContain(`unsupported schema at ${location}`)
  })

  it('refuses a component with properties that admits other keys', () => {
    const { status, stderr } = generate(synthetic({ Open: { type: 'object', properties: {} } }))
    expect(status).not.toBe(0)
    expect(stderr).toContain('unsupported schema at #/components/schemas/Open: open object')
  })

  it('refuses a violation in a component no response reaches', () => {
    const { status, stderr } = generate(synthetic({ Unused: { allOf: [] } }))
    expect(status).not.toBe(0)
    expect(stderr).toContain('unsupported schema at #/components/schemas/Unused')
  })
})

const NAMES = { type: 'object', additionalProperties: { type: 'integer' } }
const rich = synthetic(
  {
    A: closed({ x: { type: 'string' } }),
    B: closed({ y: ref('C') }),
    C: closed({ z: { type: 'integer' } }),
    Cat: closed({ kind: { const: 'cat', type: 'string' }, purr: { type: 'boolean' } }),
    Dog: closed({ bark: { type: 'boolean' }, kind: { const: 'dog', type: 'string' } }),
    Item: closed(
      {
        a: { type: 'string', minLength: 2, maxLength: 3, pattern: '^[a-z]+$' },
        b: { type: 'integer', minimum: 0, maximum: 10, default: 3 },
        c: { type: 'string', enum: ['x', 'y'] },
        d: { type: 'string', const: 'k' },
        e: { type: 'array', items: { type: 'integer' }, minItems: 1, maxItems: 2 },
        f: { ...NAMES, maxProperties: 1, propertyNames: { enum: ['p', 'q'] } },
        g: { anyOf: [{ type: 'string' }, { type: 'null' }] },
        h: {
          oneOf: [ref('Cat'), ref('Dog')],
          discriminator: {
            propertyName: 'kind',
            mapping: { cat: '#/components/schemas/Cat', dog: '#/components/schemas/Dog' },
          },
        },
        i: { anyOf: [ref('A'), ref('B')] },
        j: { type: 'number', minimum: 0.5 },
        k: { ...NAMES, propertyNames: { const: 'only', title: 'Only' } },
        l: { title: 'Anything' },
        m: { type: 'object', additionalProperties: true },
        n: NAMES,
        o: { anyOf: [{ type: 'string' }, { type: 'integer' }] },
      },
      // `b` is optional with a default in the document; rule P still requires it.
      ['a', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o'],
    ),
  },
  {
    '/items': { post: { operationId: 'create', responses: { '201': json(ref('Item')) } } },
    '/items/special': { get: { operationId: 'special', responses: { '200': json(ref('A')) } } },
    '/items/{id}': {
      get: {
        operationId: 'item',
        responses: { '200': json({ anyOf: [ref('Item'), { type: 'null' }] }) },
      },
    },
  },
)
let cached: Promise<Emitted> | undefined
const richModule = () => (cached ??= load(rich))
const item = {
  a: 'ab',
  b: 3,
  c: 'x',
  d: 'k',
  e: [1],
  f: { p: 1 },
  g: null,
  h: { kind: 'cat', purr: true },
  i: { y: { z: 1 } },
  j: 1.5,
  k: { only: 1 },
  l: [{ any: 'thing' }],
  m: { free: 1 },
  n: {},
  o: 2,
}

describe('emitted decoders', () => {
  it('emit byte-identical text for the same document', () => {
    const first = generate(rich).directory
    const second = generate(rich).directory
    for (const file of ['review-decoders.ts', 'review-api-response.d.ts', 'review-api.d.ts'])
      expect(readFileSync(path.join(first, file), 'utf8')).toBe(
        readFileSync(path.join(second, file), 'utf8'),
      )
  })

  it('embeds schema text that could end a script or a line as plain data', async () => {
    const odd = '</script>\u2028\u2029<!--'
    const emitted = await load(
      synthetic(
        { Odd: closed({ [odd]: { type: 'string', enum: [odd, 'y'] } }) },
        { '/odd': { get: { operationId: 'odd', responses: { '200': json(ref('Odd')) } } } },
      ),
    )
    expect(emitted.d_Odd?.({ [odd]: odd }, '')).toEqual({ [odd]: odd })
    expect(failure(() => emitted.d_Odd?.({ [odd]: 'x' }, ''))).toContain('enum@')
  })

  it('rebuild a valid value as a new object and route literal paths first', async () => {
    const emitted = await richModule()
    const decoded = emitted.d_Item?.(item, '')
    expect(decoded).toEqual(item)
    expect(decoded).not.toBe(item)
    expect(emitted.matchOperation('GET', '/items/special?x=1')).toBe('GET /items/special')
    expect(emitted.matchOperation('get', 'https://review.test/items/7')).toBe('GET /items/{id}')
    expect(emitted.matchOperation('POST', '/items')).toBe('POST /items')
    expect(emitted.matchOperation('DELETE', '/items/7')).toBeUndefined()
    expect(emitted.responseDecoders['GET /items/{id}']?.['200']?.(null, '')).toBeNull()
    expect(Object.keys(emitted.responseDecoders['POST /items'] ?? {})).toEqual(['201'])
  })

  it.each([
    ['an unknown key before a missing one', { ...item, zz: 1, a: undefined }, 'unknown_key@/zz'],
    [
      'the first missing key in schema order',
      { ...item, a: undefined, c: undefined },
      'required@/a',
    ],
    ['a missing key that has a default', { ...item, b: undefined }, 'required@/b'],
    ['a type before length', { ...item, a: 5 }, 'type@/a'],
    ['length before pattern', { ...item, a: 'abcd1' }, 'max_length@/a'],
    ['length in code points', { ...item, a: '😀' }, 'min_length@/a'],
    ['a pattern', { ...item, a: 'a1' }, 'pattern@/a'],
    ['a fractional integer', { ...item, b: 1.5 }, 'type@/b'],
    ['an integer above 2^53', { ...item, b: 2 ** 53 }, 'type@/b'],
    ['a minimum', { ...item, b: -1 }, 'min@/b'],
    ['a maximum', { ...item, b: 11 }, 'max@/b'],
    ['an enum type', { ...item, c: 5 }, 'type@/c'],
    ['an enum member', { ...item, c: 'z' }, 'enum@/c'],
    ['a constant', { ...item, d: 'j' }, 'const@/d'],
    ['too few items', { ...item, e: [] }, 'min_items@/e'],
    ['too many items', { ...item, e: [1, 2, 3] }, 'max_items@/e'],
    ['an element', { ...item, e: [1, 'x'] }, 'type@/e/1'],
    ['a key name before size', { ...item, f: { p: 1, r: 1 } }, 'property_name@/f/r'],
    ['a map size before entries', { ...item, f: { p: 'x', q: 1 } }, 'max_properties@/f'],
    ['a map entry', { ...item, f: { p: 'x' } }, 'type@/f/p'],
    ['a nullable value', { ...item, g: 1 }, 'type@/g'],
    ['a missing tag', { ...item, h: { purr: true } }, 'discriminator@/h/kind'],
    ['an unknown tag', { ...item, h: { kind: 'cow' } }, 'discriminator@/h/kind'],
    ['the tagged body', { ...item, h: { kind: 'dog' } }, 'required@/h/bark'],
    ['the deepest branch failure', { ...item, i: { y: { z: 'x' } } }, 'type@/i/y/z'],
    ['the first branch on a tie', { ...item, i: {} }, 'required@/i/x'],
    ['a number minimum', { ...item, j: 0.25 }, 'min@/j'],
    ['a constant key name', { ...item, k: { other: 1 } }, 'property_name@/k/other'],
    ['no branch of the value type', { ...item, o: true }, 'no_branch@/o'],
    ['a key needing escapes', { ...item, n: { 'a/b~c': 'x' } }, 'type@/n/a~1b~0c'],
  ])('reports %s', async (_name, value, expected) => {
    const emitted = await richModule()
    const wire = JSON.parse(JSON.stringify(value)) as unknown
    expect(failure(() => emitted.d_Item?.(wire, ''))).toBe(expected)
  })

  it('keeps a __proto__ map key as an own key without touching the prototype', async () => {
    const emitted = await richModule()
    const wire = JSON.parse(`{"__proto__": 1, "safe": 2}`) as unknown
    const decoded = emitted.d_Item?.({ ...item, n: wire, m: wire }, '') as Record<string, object>
    for (const key of ['n', 'm'] as const) {
      expect(Object.getPrototypeOf(decoded[key])).toBe(Object.prototype)
      expect(Object.hasOwn(decoded[key] ?? {}, '__proto__')).toBe(true)
    }
  })

  it('refuses a top-level array where an object is declared', async () => {
    const emitted = await richModule()
    expect(failure(() => emitted.d_Item?.([], ''))).toBe('type@')
  })
})

// Field specs are invariant in their type, so each mismatch below fails `tsc`
// on its own line; an unused directive would fail it too.
interface Probe {
  readonly s: string
  readonly n: string | null
  readonly o?: string | null
  readonly l: readonly number[]
  readonly e: 'a' | 'b'
}
const s = req(str())
const n = req(nullable(str()))
const o = opt(nullable(str()))
const l = req(arr(int()))
const e = req(lit('string', 'a', 'b'))
describe('field spec type controls', () => {
  it('accept the exact spec and refuse each mismatch (NC1-NC11)', () => {
    expect(obj<Probe>({ s, n, o, l, e })({ s: 'x', n: null, l: [], e: 'a' }, '')).toEqual({
      s: 'x',
      n: null,
      l: [],
      e: 'a',
    })
    // @ts-expect-error NC1: an integer decoder for a string property
    obj<Probe>({ s: req(int()), n, o, l, e })
    // @ts-expect-error NC2: a non-null decoder for a nullable property
    obj<Probe>({ s, n: req(str()), o, l, e })
    // @ts-expect-error NC3: a nullable decoder for a non-null property
    obj<Probe>({ s: req(nullable(str())), n, o, l, e })
    // @ts-expect-error NC4: a property without a field
    obj<Probe>({ s, n, l, e })
    // @ts-expect-error NC5: a field the type does not declare
    obj<Probe>({ s, n, o, l, e, x: s })
    // @ts-expect-error NC6: an optional field for a required property
    obj<Probe>({ s: opt(str()), n, o, l, e })
    // @ts-expect-error NC7: a required field for an omitted property
    obj<Probe>({ s, n, o: req(nullable(str())), l, e })
    // @ts-expect-error NC8: a literal set missing a member
    obj<Probe>({ s, n, o, l, e: req(lit('string', 'a')) })
    // @ts-expect-error NC9: a literal set with an extra member
    obj<Probe>({ s, n, o, l, e: req(lit('string', 'a', 'b', 'c')) })
    // @ts-expect-error NC10: an array of the wrong element type
    obj<Probe>({ s, n, o, l: req(arr(str())), e })
    // @ts-expect-error NC11: a default that fills the wrong type
    obj<Probe>({ s, n, o, l: dflt(arr(int()), () => ['x']), e })
    expect(map(int())({ a: 1 }, '')).toEqual({ a: 1 })
  })
})

describe('two-way literal sets', () => {
  it('accept the exact members and name the missing ones', () => {
    const check = lits<'a' | 'b'>()
    expect(check(['b', 'a'])).toEqual(['b', 'a'])
    // @ts-expect-error a missing member fails the parameter type
    check(['a'])
    // @ts-expect-error an extra member fails the element type
    check(['a', 'b', 'c'])
    expectTypeOf<Parameters<typeof check<readonly ['a']>>[0]>().toEqualTypeOf<
      readonly ['a'] & { missing_members: 'b' }
    >()
    expectTypeOf<Parameters<typeof check<readonly ['a', 'b']>>[0]>().toEqualTypeOf<
      readonly ['a', 'b']
    >()
  })
})
