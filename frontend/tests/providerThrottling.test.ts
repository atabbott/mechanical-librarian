import { describe, expect, it } from 'vitest'

import type {
  PreparationView,
  ProviderThrottleEntry,
  ProviderThrottlingView,
} from '../src/api/http'
import {
  mastheadState,
  needsYou,
  nextNoticeAt,
  retryClass,
  retryOutcome,
  ringFill,
  throttleLine,
  timerDelay,
  workLabel,
} from '../src/providerThrottling'
import { idlePreparation } from './fixtures'

const entry = (overrides: Partial<ProviderThrottleEntry> = {}): ProviderThrottleEntry => ({
  library: 'Synthetic ebooks',
  adapter: 'google_books',
  state: 'waiting',
  reasons: ['burst_throttled'],
  deferred: 214,
  next_retry_at: null,
  remedy: null,
  notice_at: null,
  ...overrides,
})
const throttling = (
  entries: ProviderThrottleEntry[],
  generation = 4,
  answered: ProviderThrottlingView['answered'] = [],
): ProviderThrottlingView => ({ generation, retry: 'available', entries, answered })
const NOW = 1_800_000_000_000

describe('needsYou (#1494 acceptance 1)', () => {
  it.each([
    ['a routine wait', entry(), false],
    ['nothing retries a dormant source', entry({ state: 'dormant' }), true],
    ['no automatic retry remains', entry({ state: 'exhausted' }), true],
    ['a missing Google Books key', entry({ remedy: 'add_api_key' }), true],
    ['a refused key, even while waiting', entry({ remedy: 'check_api_key' }), true],
    ['a day-long wait not yet reached', entry({ notice_at: NOW / 1000 + 1 }), false],
    ['a day-long wait reached', entry({ notice_at: NOW / 1000 }), true],
    ['a checking source', entry({ state: 'checking' }), false],
  ])('%s', (_name, value, expected) => {
    expect(needsYou(value, NOW)).toBe(expected)
  })

  it('flips on time and not a millisecond before', () => {
    const due = entry({ notice_at: NOW / 1000 })
    expect(needsYou(due, NOW - 1)).toBe(false)
    expect(needsYou(due, NOW)).toBe(true)
  })

  it('times one timer to the earliest day-long wait still ahead, capped', () => {
    const entries = [
      entry({ notice_at: NOW / 1000 + 600 }),
      entry({ notice_at: NOW / 1000 + 60 }),
      // Already needing the owner for another reason, so it schedules nothing.
      entry({ notice_at: NOW / 1000 + 5, state: 'dormant' }),
      entry({ notice_at: NOW / 1000 - 5 }),
    ]
    expect(nextNoticeAt(entries, NOW)).toBe(NOW + 60_000)
    expect(nextNoticeAt([entry()], NOW)).toBe(Infinity)
    expect(timerDelay(NOW + 60_000, NOW)).toBe(60_000)
    expect(timerDelay(NOW + 4_000_000_000, NOW)).toBe(2_147_483_647)
  })

  it('keeps the sentences the notice already says', () => {
    expect(throttleLine(entry(), new Date(NOW))).toBe(
      'It is limiting requests. 214 lookups waiting. Retry due now.',
    )
    expect(throttleLine(entry({ deferred: 1, reasons: [], state: 'dormant' }), new Date(NOW))).toBe(
      '1 lookup waiting. No automatic retry. Fix the source, then retry.',
    )
  })
})

describe('retryOutcome (#1494 acceptance 6)', () => {
  const google = { library: 'Synthetic ebooks', adapter: 'google_books' } as const
  const hardcover = { library: 'Synthetic ebooks', adapter: 'hardcover' } as const

  it.each([
    ['absent', undefined, 'recovered'],
    ['dormant', entry({ state: 'dormant', reasons: ['authentication'] }), 'failed'],
    ['waiting with a reason', entry(), 'waiting_again'],
    ['exhausted with a reason', entry({ state: 'exhausted' }), 'waiting_again'],
    ['waiting with no reason yet', entry({ reasons: [] }), 'pending'],
    ['checking', entry({ state: 'checking' }), 'pending'],
  ] as const)('classifies %s', (_name, current, expected) => {
    expect(retryClass(current)).toBe(expected)
  })

  it('waits for the raised generation and for every pending entry', () => {
    expect(retryOutcome([google], 4, throttling([], 4))).toBeNull()
    expect(retryOutcome([google], 4, null)).toBeNull()
    expect(retryOutcome([google], 4, throttling([entry({ reasons: [] })], 5))).toBeNull()
    expect(
      retryOutcome([google, hardcover], 4, throttling([entry({ state: 'checking' })], 5)),
    ).toBeNull()
  })

  it('settles from a generation raised again elsewhere, by the same rules', () => {
    // Another tab pressed Retry now before this press settled: the snapshot
    // never shows the generation this press raised it to.
    expect(retryOutcome([google], 4, throttling([entry()], 6))).toBe(
      'Retried. Google Books is still limiting requests, so 214 lookups are waiting again.',
    )
    expect(retryOutcome([google], 4, throttling([], 7, [google]))).toBe(
      'Retried. Every waiting lookup got an answer.',
    )
    expect(retryOutcome([google], 4, throttling([], 6))).toBe('Retried. No lookups are waiting.')
    expect(retryOutcome([google], 4, throttling([entry({ reasons: [] })], 6))).toBeNull()
    expect(retryOutcome([google], 4, throttling([entry({ state: 'checking' })], 6))).toBeNull()
    expect(retryOutcome([google], 4, throttling([], 3))).toBeNull()
  })

  it.each([
    [
      [entry()],
      'Retried. Google Books is still limiting requests, so 214 lookups are waiting again.',
    ],
    [
      [entry({ reasons: ['quota_exhausted'] })],
      'Retried. Google Books still has no request quota left, so 214 lookups are waiting again.',
    ],
    [
      [
        entry({
          adapter: 'hardcover',
          state: 'dormant',
          reasons: ['authentication'],
          deferred: 31,
        }),
      ],
      'Retried. Hardcover refused the API key again, so 31 lookups did not run.',
    ],
    [
      [entry({ adapter: 'hardcover', state: 'dormant', reasons: ['authorization'], deferred: 31 })],
      'Retried. Hardcover refused access for this API key again, so 31 lookups did not run.',
    ],
    [
      [entry({ adapter: 'hardcover', state: 'dormant', reasons: ['configuration'], deferred: 31 })],
      'Retried. Hardcover still needs its settings checked, so 31 lookups did not run.',
    ],
    [
      [
        entry({
          adapter: 'open_library',
          state: 'exhausted',
          reasons: ['transient'],
          deferred: 12,
        }),
      ],
      'Retried. Open Library still did not answer, so 12 lookups have no automatic retry left.',
    ],
    [
      [entry({ deferred: 1 })],
      'Retried. Google Books is still limiting requests, so 1 lookup is waiting again.',
    ],
    [
      [entry({ adapter: 'hardcover', state: 'dormant', reasons: ['authentication'], deferred: 1 })],
      'Retried. Hardcover refused the API key again, so 1 lookup did not run.',
    ],
    [
      [entry({ state: 'exhausted', deferred: 1 })],
      'Retried. Google Books is still limiting requests, so 1 lookup has no automatic retry left.',
    ],
  ])('says %j', (current, sentence) => {
    const pressed = [
      google,
      hardcover,
      { library: 'Synthetic ebooks', adapter: 'open_library' } as const,
    ]
    expect(retryOutcome(pressed, 4, throttling(current, 5))).toBe(sentence)
  })

  it('claims answers only when every pressed source got one', () => {
    // A source also stops waiting when its lookups went stale: nothing is
    // waiting, and no provider answered (#1494).
    expect(retryOutcome([google, hardcover], 4, throttling([], 5, [hardcover, google]))).toBe(
      'Retried. Every waiting lookup got an answer.',
    )
    for (const answered of [[], [google]])
      expect(retryOutcome([google, hardcover], 4, throttling([], 5, answered))).toBe(
        'Retried. No lookups are waiting.',
      )
    // A source that is waiting again is still the one named.
    expect(retryOutcome([google, hardcover], 4, throttling([entry()], 5))).toBe(
      'Retried. Google Books is still limiting requests, so 214 lookups are waiting again.',
    )
  })

  it('says one Retried. and one sentence per source that did not recover, summed over libraries', () => {
    const audio = { library: 'Synthetic audio', adapter: 'google_books' } as const
    const outcome = retryOutcome(
      [google, audio, hardcover, { library: 'Synthetic ebooks', adapter: 'inventaire' }],
      4,
      throttling(
        [
          entry({ deferred: 10_000 }),
          entry({ library: 'Synthetic audio', deferred: 214 }),
          entry({
            adapter: 'hardcover',
            state: 'dormant',
            reasons: ['authentication'],
            deferred: 31,
          }),
        ],
        5,
      ),
    )
    expect(outcome).toBe(
      'Retried. Google Books is still limiting requests, so 10,214 lookups are waiting again. ' +
        'Hardcover refused the API key again, so 31 lookups did not run.',
    )
  })

  it('counts only the libraries in the state the sentence names', () => {
    const audio = { library: 'Synthetic audio', adapter: 'hardcover' } as const
    const outcome = retryOutcome(
      [hardcover, audio],
      4,
      throttling(
        [
          entry({
            adapter: 'hardcover',
            state: 'dormant',
            reasons: ['authentication'],
            deferred: 31,
          }),
          entry({
            library: 'Synthetic audio',
            adapter: 'hardcover',
            state: 'waiting',
            reasons: ['authentication'],
            deferred: 214,
          }),
        ],
        5,
      ),
    )
    // 214 lookups wait again in the other library; they did not "not run".
    expect(outcome).toBe('Retried. Hardcover refused the API key again, so 31 lookups did not run.')
  })
})

const library = (
  overrides: Partial<PreparationView['libraries'][number]> = {},
): PreparationView['libraries'][number] => ({
  scan_id: null,
  connection: 'primary',
  library: 'Synthetic ebooks',
  library_id: 'library-1',
  state: 'completed',
  checked: 0,
  total: 0,
  ready: 0,
  blocked: 0,
  recorded: 0,
  not_matched: 0,
  rescan_requested: false,
  stages: [],
  ...overrides,
})
const view = (overrides: Partial<PreparationView> = {}): PreparationView => ({
  ...idlePreparation,
  libraries: [library()],
  ...overrides,
})
const waits = (...entries: ProviderThrottleEntry[]) => throttling(entries, 1)

describe('mastheadState (#1494 acceptance 7)', () => {
  it.each([
    ['idle', view(), 'idle', 'Preparation idle'],
    ['preparing', view({ active: true }), 'none', 'Preparing libraries'],
    [
      'preparing with waits',
      view({ active: true, provider_throttling: waits(entry()) }),
      'waiting',
      'Preparing libraries. Source lookups are waiting to retry.',
    ],
    [
      'preparing with a source needing you',
      view({ active: true, provider_throttling: waits(entry({ state: 'dormant' })) }),
      'needs_you',
      'Preparing libraries. A source needs you.',
    ],
    [
      'needing you and routine waits together',
      view({
        provider_throttling: waits(
          entry({ remedy: 'add_api_key' }),
          entry({ adapter: 'inventaire' }),
        ),
      }),
      'needs_you',
      'Preparation idle. A source needs you. Source lookups are waiting to retry.',
    ],
    ['paused', view({ paused: true }), 'paused', 'Preparation paused'],
    [
      'paused with waits',
      view({ paused: true, provider_throttling: waits(entry()) }),
      'paused',
      'Preparation paused. Source lookups are waiting to retry.',
    ],
    ['needs attention', view({ actionable: true }), 'attention', 'Preparation needs attention'],
    [
      'needs attention while paused',
      view({ actionable: true, paused: true }),
      'attention',
      'Preparation paused',
    ],
    [
      'a source needing you outranks needs attention',
      view({ actionable: true, provider_throttling: waits(entry({ state: 'exhausted' })) }),
      'needs_you',
      'Preparation needs attention. A source needs you.',
    ],
    [
      'stopped outranks everything',
      view({ recovery_required: true, provider_throttling: waits(entry({ state: 'dormant' })) }),
      'stopped',
      'Preparation stopped. A source needs you.',
    ],
  ])('%s', (_name, data, centre, name) => {
    const state = mastheadState(data, NOW)
    expect([state.centre, state.name]).toEqual([centre, name])
  })

  it('names a held run paused, as the Library does', () => {
    expect(workLabel(undefined)).toBe('Preparation idle')
    const blocked = view({
      presentation: { state: 'blocked' } as unknown as PreparationView['presentation'],
    })
    expect(mastheadState(blocked, NOW).centre).toBe('paused')
  })

  it('raises a day-long wait as needing you once its time passes', () => {
    const data = view({ provider_throttling: waits(entry({ notice_at: NOW / 1000 })) })
    expect(mastheadState(data, NOW - 1).centre).toBe('waiting')
    expect(mastheadState(data, NOW).centre).toBe('needs_you')
  })
})

describe('ringFill (#1494 acceptance 7)', () => {
  const run = (scanIds: string[]) =>
    ({ state: 'running', now: { scan_ids: scanIds } }) as unknown as PreparationView['presentation']

  it.each([
    ['5% checked shows the one-fifth minimum', 5, 100, 0.2],
    ['half checked', 50, 100, 0.5],
    ['97% checked holds at nine tenths', 97, 100, 0.9],
    ['nothing to count', 0, 0, 0.2],
  ])('%s', (_name, checked, total, fill) => {
    expect(ringFill(view({ libraries: [library({ state: 'processing', checked, total })] }))).toBe(
      fill,
    )
  })

  it('counts the libraries the current run enrolled, else those preparing', () => {
    const libraries = [
      library({ scan_id: 'scan-1', state: 'processing', checked: 30, total: 50 }),
      library({ scan_id: 'scan-2', state: 'completed', checked: 50, total: 50 }),
      library({ scan_id: null, state: 'queued', checked: 0, total: 100 }),
    ]
    expect(ringFill(view({ libraries, presentation: run(['scan-1', 'scan-2']) }))).toBe(0.8)
    expect(ringFill(view({ libraries }))).toBe(0.2)
    expect(ringFill(view({ libraries: libraries.slice(0, 2), presentation: null }))).toBeCloseTo(
      0.6,
    )
  })
})
