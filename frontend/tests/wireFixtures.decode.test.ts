import { describe, expect, it } from 'vitest'

import activityV2Predecessor from './activityV2Predecessor.json'
import descriptionAdviceActivityFixture from './descriptionAdviceActivityFixture.json'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import descriptionAdvicePreservationFixture from './descriptionAdvicePreservationFixture.json'
import descriptionAdviceRetryFixture from './descriptionAdviceRetryFixture.json'
import identifierEvidenceFixture from './identifierEvidenceFixture.json'
import { reportDetail } from './wireFixtures'
import type { D } from '../src/generated/review-decoders'
import {
  d__DescriptionAdviceResponse,
  matchOperation,
  responseDecoders,
} from '../src/generated/review-decoders'

// Every fixture here is a server capture (`tests/unit/test_browser_wire_fixtures.py`
// and the identifier-evidence test) or a base builder fixed against one.
interface Wire {
  readonly method: string
  readonly path: string
  readonly status: number
  readonly body: unknown
}
const captures = import.meta.glob<Wire>('./wire/*.json', { eager: true, import: 'default' })
const decoders = responseDecoders as Record<string, Record<string, D<unknown>>>

const decoderFor = (method: string, path: string, status: number) => {
  const key = matchOperation(method, path)
  if (key === undefined) throw new Error(`no operation for ${method} ${path}`)
  const decode = decoders[key]?.[String(status)]
  if (decode === undefined) throw new Error(`no decoder for ${key} ${String(status)}`)
  return decode
}
const failure = (decode: () => unknown) => {
  try {
    decode()
  } catch (error) {
    const { at, reason } = error as { at: string; reason: string }
    return `${reason}@${at}`
  }
  throw new Error('decoding succeeded')
}
const roundTrip = (decode: D<unknown>, body: unknown) => {
  const decoded = decode(body, '')
  expect(decoded).toEqual(body)
  if (typeof body === 'object' && body !== null) expect(decoded).not.toBe(body)
}

describe('captured wire fixtures', () => {
  it('has a capture for every operation 2a-i records', () => {
    expect(Object.keys(captures)).toHaveLength(21)
  })

  it.each(Object.entries(captures))('%s decodes by operation and status', (_file, wire) => {
    roundTrip(decoderFor(wire.method, wire.path, wire.status), wire.body)
  })

  it.each([
    ['descriptionAdviceFixture', descriptionAdviceFixture],
    ['descriptionAdvicePreservationFixture', descriptionAdvicePreservationFixture],
  ])('%s decodes as a case detail', (_name, detail) => {
    roundTrip(decoderFor('GET', `/api/v1/review-cases/${detail.review_case_id}`, 200), detail)
  })

  it('decodes the description Activity capture', () => {
    roundTrip(decoderFor('GET', '/api/v1/activity', 200), descriptionAdviceActivityFixture)
  })

  it.each(Object.entries(descriptionAdviceRetryFixture))(
    'decodes the %s retry part as description advice',
    (_state, advice) => {
      roundTrip(d__DescriptionAdviceResponse, advice)
    },
  )

  it('decodes the identifier evidence capture', () => {
    const { detail, evidence } = identifierEvidenceFixture
    const path = `/api/v1/review-cases/${detail.review_case_id}`
    roundTrip(decoderFor('GET', path, 200), detail)
    roundTrip(decoderFor('GET', `${path}/evidence?limit=1`, 200), evidence)
  })

  it('decodes the shared report detail builder', () => {
    roundTrip(decoderFor('GET', '/api/v1/library/report?item=item-9', 200), reportDetail())
  })
})

describe('decode negative controls', () => {
  it('refuses the genuine earlier Activity producer', () => {
    const decode = decoderFor('GET', '/api/v1/activity', 200)
    expect(failure(() => decode(activityV2Predecessor, ''))).toBe('required@/lapsed_consents')
  })

  it('refuses a captured body with a key the server never sends', () => {
    const decode = decoderFor('GET', '/api/v1/metadata-policy/shadow', 200)
    const wire = captures['./wire/get-metadata-policy-shadow.200.json']
    expect(failure(() => decode({ ...(wire?.body as object), unexpected: true }, ''))).toBe(
      'unknown_key@/unexpected',
    )
  })
})
