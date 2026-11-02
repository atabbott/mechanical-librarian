// Response-truth types and spec-form decoders for every JSON 2xx response of
// the review API. `generate-dtos.mjs` is the only caller; nothing here reads
// the environment, the clock or a path, so the output depends on the document
// alone.

// Pairs the server omits from a response (`exclude_if`); every other property
// of a response-reachable object is required on the wire (rule P). The Python
// omission sentinel parses this literal, so keep its exact form.
const RESPONSE_OMISSIONS = [
  ['LibrarySettings', 'display_name'],
  ['LibrarySettings', 'languages'],
]

const KNOWN = new Set([
  ...['type', 'const', 'enum', 'properties', 'required', 'additionalProperties', 'items'],
  ...['anyOf', 'oneOf', 'discriminator', '$ref', 'minimum', 'maximum', 'minLength'],
  ...['maxLength', 'pattern', 'minItems', 'maxItems', 'maxProperties', 'default', 'title'],
  ...['description', 'writeOnly', 'format', 'propertyNames'],
])
const TYPES = new Set(['string', 'integer', 'number', 'boolean', 'null', 'array', 'object'])
const METHODS = ['get', 'put', 'post', 'delete', 'patch']
const PREFIX = '#/components/schemas/'
const pointer = (...parts) => parts.map((p) => String(p).replaceAll('~', '~0').replaceAll('/', '~1')).join('/')
const plain = (value) => typeof value === 'object' && value !== null && !Array.isArray(value)
const fail = (at, what) => {
  throw new Error(`unsupported schema at ${at}: ${what}`)
}

function roots(document) {
  const found = []
  for (const template of Object.keys(document.paths ?? {}).sort()) {
    for (const method of METHODS) {
      const operation = document.paths[template][method]
      for (const [status, response] of Object.entries(operation?.responses ?? {})) {
        const schema = response?.content?.['application/json']?.schema
        if (!status.startsWith('2') || schema === undefined) continue
        const at = `#/${pointer('paths', template, method, 'responses', status)}/content/application~1json/schema`
        if (!plain(schema)) fail(at, 'not an object')
        if (!Object.keys(schema).some((key) => key !== 'title' && key !== 'description')) continue
        if (typeof operation.operationId !== 'string') fail(at, 'operation has no operationId')
        found.push({ key: `${method.toUpperCase()} ${template}`, template, status, id: operation.operationId, schema, at })
      }
    }
  }
  return found
}

function guard(document, node, at, top = false) {
  if (!plain(node)) fail(at, 'not an object')
  for (const key of Object.keys(node)) if (!KNOWN.has(key)) fail(at, `keyword ${key}`)
  const schemas = document.components?.schemas ?? {}
  if ('type' in node && !TYPES.has(node.type)) fail(at, `type ${JSON.stringify(node.type)}`)
  if ('format' in node && node.format !== 'binary' && node.format !== 'password') fail(at, `format ${node.format}`)
  if ('pattern' in node) {
    try {
      new RegExp(node.pattern, 'u')
    } catch {
      fail(at, 'pattern does not compile')
    }
  }
  const target = (ref, where) => {
    if (typeof ref !== 'string' || !ref.startsWith(PREFIX) || !Object.hasOwn(schemas, ref.slice(PREFIX.length)))
      fail(where, `$ref ${ref}`)
  }
  if ('$ref' in node) target(node.$ref, at)
  if ('oneOf' in node) {
    const { propertyName, mapping } = node.discriminator ?? {}
    if (typeof propertyName !== 'string' || !plain(mapping)) fail(at, 'oneOf without discriminator mapping')
    for (const [tag, ref] of Object.entries(mapping)) target(ref, `${at}/discriminator/mapping/${pointer(tag)}`)
  } else if ('discriminator' in node) fail(at, 'discriminator without oneOf')
  if ('properties' in node && !top) fail(at, 'object with properties outside a component')
  if ('properties' in node && node.additionalProperties !== false) fail(at, 'open object with properties')
  if (node.additionalProperties === false && !('properties' in node)) fail(at, 'closed object without properties')
  if (('propertyNames' in node || 'maxProperties' in node) && !plain(node.additionalProperties))
    fail(at, 'map constraint without a value schema')
  if ('propertyNames' in node) {
    const names = node.propertyNames
    const extra = plain(names) && Object.keys(names).filter((key) => !['title', 'description'].includes(key))
    const enumForm = extra?.length === 1 && extra[0] === 'enum' && Array.isArray(names.enum)
    if (!enumForm && !(extra?.length === 1 && extra[0] === 'const')) fail(`${at}/propertyNames`, 'propertyNames form')
    if ((enumForm ? names.enum : [names.const]).some((name) => typeof name !== 'string'))
      fail(`${at}/propertyNames`, 'propertyNames value')
  }
  for (const [key, value] of Object.entries(node.properties ?? {})) guard(document, value, `${at}/properties/${pointer(key)}`)
  if ('items' in node) guard(document, node.items, `${at}/items`)
  if (plain(node.additionalProperties)) guard(document, node.additionalProperties, `${at}/additionalProperties`)
  else if ('additionalProperties' in node && typeof node.additionalProperties !== 'boolean')
    fail(`${at}/additionalProperties`, 'not a schema')
  for (const key of ['anyOf', 'oneOf'])
    if (key in node) {
      if (!Array.isArray(node[key]) || node[key].length === 0) fail(`${at}/${key}`, 'not a list')
      node[key].forEach((branch, index) => guard(document, branch, `${at}/${key}/${index}`))
    }
}

// Every component a response root reaches, in name order, after guarding the
// whole document so an unreachable violation still fails generation.
function analyse(document) {
  const schemas = document.components?.schemas ?? {}
  for (const [name, schema] of Object.entries(schemas)) guard(document, schema, `${PREFIX}${pointer(name)}`, true)
  const entries = roots(document)
  for (const entry of entries) guard(document, entry.schema, entry.at)
  const reached = new Set()
  const visit = (node) => {
    const refs = [node.$ref, ...Object.values(node.discriminator?.mapping ?? {})]
    for (const name of refs.filter(Boolean).map((ref) => ref.slice(PREFIX.length)))
      if (!reached.has(name)) reached.add(name) && visit(schemas[name])
    const children = [...Object.values(node.properties ?? {}), ...(node.anyOf ?? []), ...(node.oneOf ?? [])]
    for (const child of [...children, node.items, node.additionalProperties]) if (plain(child)) visit(child)
  }
  entries.forEach((entry) => visit(entry.schema))
  const omitted = new Map()
  for (const [component, property] of RESPONSE_OMISSIONS) {
    if (!reached.has(component) || !Object.hasOwn(schemas[component].properties ?? {}, property))
      fail(`${PREFIX}${pointer(component)}`, `response omission ${property} is not a reachable property`)
    omitted.set(component, [...(omitted.get(component) ?? []), property])
  }
  return { schemas, entries, components: [...reached].sort(), omitted }
}

// Rule P: a deep copy whose reachable components require every property but
// the declared omissions. Request-only components are left as they are.
export function responseTruth(document) {
  const { components, omitted } = analyse(document)
  const copy = structuredClone(document)
  for (const name of components) {
    const schema = copy.components.schemas[name]
    const skip = omitted.get(name) ?? []
    if (plain(schema.properties)) schema.required = Object.keys(schema.properties).filter((key) => !skip.includes(key))
  }
  return copy
}

const PRELUDE = `// Generated by scripts/generate-decoders.mjs. Do not edit.
import type { components, operations } from './review-api-response'

type S = components['schemas']
type Prim = string | number | boolean | null
type Kind = 'string' | 'integer' | 'number' | 'boolean' | 'null' | 'array' | 'object'
type Body<O extends keyof operations, St extends keyof operations[O]['responses']> =
  operations[O]['responses'][St] extends { readonly content: { readonly 'application/json': infer B } } ? B : never

export class DecodeFailure {
  constructor(
    readonly at: string,
    readonly reason: string,
  ) {}
}
export type D<T> = (v: unknown, at: string) => T
export interface Field<T> {
  readonly kind: 'req' | 'opt' | 'dflt'
  readonly d: D<T>
  readonly fill?: () => T
  readonly inv?: (t: T) => void
}
type Rec = Record<string, unknown>
const fail = (at: string, reason: string): never => {
  throw new DecodeFailure(at, reason)
}
const ptr = (at: string, key: string | number) => \`\${at}/\${String(key).replaceAll('~', '~0').replaceAll('/', '~1')}\`
const record = (v: unknown, at: string): Rec =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Rec) : fail(at, 'type')
const is = (kind: Kind, v: unknown): boolean =>
  kind === 'null' ? v === null
  : kind === 'array' ? Array.isArray(v)
  : kind === 'object' ? typeof v === 'object' && v !== null && !Array.isArray(v)
  : kind === 'integer' ? Number.isSafeInteger(v)
  : kind === 'number' ? Number.isFinite(v)
  : typeof v === kind
const bounds = (v: number, at: string, min?: number, max?: number) => {
  if (min !== undefined && v < min) fail(at, 'min')
  if (max !== undefined && v > max) fail(at, 'max')
  return v
}
export const req = <T>(d: D<T>): Field<T> => ({ kind: 'req', d })
export const opt = <T>(d: D<T>): Field<T | undefined> => ({ kind: 'opt', d })
export const dflt = <T>(d: D<T>, fill: () => T): Field<T> => ({ kind: 'dflt', d, fill })
export const obj =
  <T>(spec: { readonly [K in keyof T]-?: Field<T[K]> }): D<T> =>
  (v, at) => {
    const value = record(v, at)
    const fields = Object.entries(spec) as [string, Field<unknown>][]
    for (const key of Object.keys(value)) if (!Object.hasOwn(spec, key)) fail(ptr(at, key), 'unknown_key')
    for (const [key, field] of fields) if (field.kind === 'req' && !Object.hasOwn(value, key)) fail(ptr(at, key), 'required')
    const out: [string, unknown][] = []
    for (const [key, field] of fields)
      if (Object.hasOwn(value, key)) out.push([key, field.d(value[key], ptr(at, key))])
      else if (field.fill) out.push([key, field.fill()])
    return Object.fromEntries(out) as T
  }
export const str =
  (min?: number, max?: number, pattern?: RegExp): D<string> =>
  (v, at) => {
    if (typeof v !== 'string') return fail(at, 'type')
    const length = [...v].length
    if (min !== undefined && length < min) fail(at, 'min_length')
    if (max !== undefined && length > max) fail(at, 'max_length')
    if (pattern && !pattern.test(v)) fail(at, 'pattern')
    return v
  }
export const int =
  (min?: number, max?: number): D<number> =>
  (v, at) => (Number.isSafeInteger(v) ? bounds(v as number, at, min, max) : fail(at, 'type'))
export const num =
  (min?: number, max?: number): D<number> =>
  (v, at) => (Number.isFinite(v) ? bounds(v as number, at, min, max) : fail(at, 'type'))
export const bool: D<boolean> = (v, at) => (typeof v === 'boolean' ? v : fail(at, 'type'))
export const nil: D<null> = (v, at) => (v === null ? null : fail(at, 'type'))
export const unk: D<unknown> = (v) => v
export const lit =
  <const L extends readonly Prim[]>(kind: Kind | null, ...literals: L): D<L[number]> =>
  (v, at) => {
    if (kind !== null && !is(kind, v)) return fail(at, 'type')
    return literals.some((literal) => literal === v) ? (v as L[number]) : fail(at, literals.length === 1 ? 'const' : 'enum')
  }
export const arr =
  <T>(item: D<T>, min?: number, max?: number): D<readonly T[]> =>
  (v, at) => {
    if (!Array.isArray(v)) return fail(at, 'type')
    if (min !== undefined && v.length < min) fail(at, 'min_items')
    if (max !== undefined && v.length > max) fail(at, 'max_items')
    return v.map((element, index) => item(element, ptr(at, index)))
  }
export const map =
  <T>(value: D<T>, max?: number, names?: readonly string[]): D<{ readonly [key: string]: T }> =>
  (v, at) => {
    const entries = Object.entries(record(v, at))
    for (const [key] of entries) if (names && !names.includes(key)) fail(ptr(at, key), 'property_name')
    if (max !== undefined && entries.length > max) fail(at, 'max_properties')
    return Object.fromEntries(entries.map(([key, entry]) => [key, value(entry, ptr(at, key))]))
  }
export const open: D<{ readonly [key: string]: unknown }> = (v, at) => Object.fromEntries(Object.entries(record(v, at)))
export const nullable =
  <T>(d: D<T>): D<T | null> =>
  (v, at) => (v === null ? null : d(v, at))
export const ref =
  <T>(get: () => D<T>): D<T> =>
  (v, at) => get()(v, at)
export const tagged =
  <const M extends Readonly<Record<string, D<unknown>>>>(tag: string, mapping: M): D<{ [K in keyof M]: M[K] extends D<infer U> ? U : never }[keyof M]> =>
  (v, at) => {
    const value = record(v, at)[tag]
    if (typeof value !== 'string' || !Object.hasOwn(mapping, value)) return fail(ptr(at, tag), 'discriminator')
    return mapping[value]!(v, at) as never
  }
// A branch is tried only when the value has the branch's JSON type (or the
// branch has none); without a success the deepest failure wins, the first
// branch in schema order on a tie, and \`no_branch\` only when none was tried.
export const anyOf =
  <const B extends readonly (readonly [Kind | null, D<unknown>])[]>(...branches: B): D<{ [I in keyof B]: B[I] extends readonly [unknown, D<infer U>] ? U : never }[number]> =>
  (v, at) => {
    let deepest: DecodeFailure | undefined
    for (const [kind, d] of branches)
      if (kind === null || is(kind, v))
        try {
          return d(v, at) as never
        } catch (error) {
          if (!(error instanceof DecodeFailure)) throw error
          if (!deepest || error.at.split('/').length > deepest.at.split('/').length) deepest = error
        }
    throw deepest ?? new DecodeFailure(at, 'no_branch')
  }
const rt = <O extends keyof operations, St extends keyof operations[O]['responses']>(d: D<Body<O, St>>) => d
`

// JSON text made safe to embed as JavaScript source: escapes the characters
// that could close a script context or end a line inside a string literal.
const js = (value) =>
  JSON.stringify(value).replace(/[<>/\u2028\u2029]/gu, (c) => `\\u${c.codePointAt(0).toString(16).padStart(4, '0')}`)
const ident = (name) => name.replace(/[^A-Za-z0-9_]/gu, (c) => `$${c.codePointAt(0)}`)
const key = (name) => (name === '__proto__' ? '["__proto__"]' : js(name))
const args = (...values) => {
  while (values.length && values.at(-1) === undefined) values.pop()
  return values.map((value) => (value === undefined ? 'undefined' : value)).join(', ')
}

function kind(node) {
  if (typeof node.type === 'string') return node.type
  if ('$ref' in node || 'oneOf' in node) return 'object'
  const literals = 'const' in node ? [node.const] : node.enum
  const kinds = new Set((literals ?? []).map((value) => (value === null ? 'null' : Number.isInteger(value) ? 'integer' : typeof value)))
  return kinds.size === 1 ? [...kinds][0] : null
}

export function emitDecoders(document) {
  const { schemas, entries, components, omitted } = analyse(document)
  const patterns = new Map()
  const pattern = (source) => {
    if (!patterns.has(source)) patterns.set(source, `P${patterns.size}`)
    return patterns.get(source)
  }
  const expr = (node) => {
    if ('$ref' in node) return `ref(() => d_${ident(node.$ref.slice(PREFIX.length))})`
    if ('oneOf' in node) {
      const mapping = Object.entries(node.discriminator.mapping)
        .map(([tag, ref]) => `${key(tag)}: ref(() => d_${ident(ref.slice(PREFIX.length))})`)
        .join(', ')
      return `tagged(${js(node.discriminator.propertyName)}, { ${mapping} })`
    }
    if ('anyOf' in node) {
      const others = node.anyOf.filter((branch) => branch.type !== 'null')
      if (others.length === 1 && others.length < node.anyOf.length) return `nullable(${expr(others[0])})`
      return `anyOf(${node.anyOf.map((branch) => `[${js(kind(branch))}, ${expr(branch)}]`).join(', ')})`
    }
    if ('const' in node || 'enum' in node) {
      const literals = 'const' in node ? [node.const] : node.enum
      return `lit(${[js(node.type ?? null), ...literals.map((value) => js(value))].join(', ')})`
    }
    switch (node.type) {
      case 'string':
        return `str(${args(node.minLength, node.maxLength, node.pattern === undefined ? undefined : pattern(node.pattern))})`
      case 'integer':
        return `int(${args(node.minimum, node.maximum)})`
      case 'number':
        return `num(${args(node.minimum, node.maximum)})`
      case 'boolean':
        return 'bool'
      case 'null':
        return 'nil'
      case 'array':
        return `arr(${args(expr(node.items ?? {}), node.minItems, node.maxItems)})`
      case 'object': {
        if (plain(node.additionalProperties)) {
          const names = node.propertyNames && ('const' in node.propertyNames ? [node.propertyNames.const] : node.propertyNames.enum)
          return `map(${args(expr(node.additionalProperties), node.maxProperties, names && js(names))})`
        }
        return 'open'
      }
      default:
        return 'unk'
    }
  }
  const body = []
  for (const name of components) {
    const schema = schemas[name]
    const skip = omitted.get(name) ?? []
    const fields = Object.entries(schema.properties ?? {}).map(([property, value]) => {
      const d = expr(value)
      const field = !skip.includes(property) ? `req(${d})` : 'default' in value ? `dflt(${d}, () => (${js(value.default)}))` : `opt(${d})`
      return `  ${key(property)}: ${field},`
    })
    body.push(`export const d_${ident(name)}: D<S[${js(name)}]> = obj<S[${js(name)}]>({\n${fields.join('\n')}\n})`)
  }
  const byKey = new Map()
  for (const entry of entries) {
    const decoders = byKey.get(entry.key) ?? []
    decoders.push(`    ${js(entry.status)}: rt<${js(entry.id)}, ${entry.status}>(${expr(entry.schema)}),`)
    byKey.set(entry.key, decoders)
  }
  body.push(`export const responseDecoders = {\n${[...byKey].map(([k, d]) => `  ${js(k)}: {\n${d.join('\n')}\n  },`).join('\n')}\n} as const`)
  body.push('export type OperationKey = keyof typeof responseDecoders')
  const routes = [...byKey.keys()]
    .map((k) => {
      const template = k.slice(k.indexOf(' ') + 1)
      const source = template.split(/(\{[^}]*\})/u).map((part) => (part.startsWith('{') ? '[^/]+' : part.replace(/[.*+?^${}()|[\]\\/]/gu, '\\$&'))).join('')
      return { k, params: (template.match(/\{/gu) ?? []).length, source: `^${source}$` }
    })
    .sort((a, b) => a.params - b.params || (a.k < b.k ? -1 : a.k > b.k ? 1 : 0))
  body.push(
    `const ROUTES: readonly (readonly [OperationKey, string, RegExp])[] = [\n${routes
      .map(({ k, source }) => `  [${js(k)}, ${js(k.slice(0, k.indexOf(' ')))}, new RegExp(${js(source)}, 'u')],`)
      .join('\n')}\n]`,
    `export const matchOperation = (method: string, url: string): OperationKey | undefined => {
  const path = new URL(url, 'http://local').pathname
  return ROUTES.find(([, verb, pattern]) => verb === method.toUpperCase() && pattern.test(path))?.[0]
}`,
  )
  const hoisted = [...patterns].map(([source, name]) => `const ${name} = new RegExp(${js(source)}, 'u')`)
  return `${PRELUDE}${hoisted.join('\n')}\n\n${body.join('\n\n')}\n`
}
