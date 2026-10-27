import { describe, expect, it } from 'vitest'

import { mapActivity, mapActivityConsent } from '../src/api/http'
import type { ActivityCapabilityView, DestinationState, TargetDestination } from '../src/api/http'
import {
  consentBasis,
  destinationReason,
  libraryWritesHeld,
  readWriteConsent,
  writesEnabled,
  writesOffSentence,
} from '../src/writeConsent'
import currentActivity from './descriptionAdviceActivityFixture.json'
import { activity } from './fixtures'

const view = (value: Partial<Parameters<typeof readWriteConsent>[0]>) => ({
  ...activity(),
  backup_ready: true,
  ...value,
})

const capability = (
  library: string,
  destinations: readonly {
    destination: TargetDestination
    state: DestinationState
    consented?: boolean
    prepared?: number
  }[],
): ActivityCapabilityView => ({
  connection: 'primary',
  library,
  state: 'partially_allowed',
  destinations: destinations.map((entry) => ({
    consented: entry.consented ?? false,
    destination: entry.destination,
    prepared: entry.prepared ?? 0,
    state: entry.state,
  })),
})

describe('the write consent offer', () => {
  it('offers every eligible destination, names why each other is left out, and counts only what it would carry', () => {
    const offer = readWriteConsent(
      view({
        configured_libraries: [{ connection: 'primary', library: 'fiction' }],
        capabilities: [
          capability('fiction', [
            { destination: 'title', state: 'allowed', prepared: 2 },
            { destination: 'authors', state: 'allowed', prepared: 3 },
            { destination: 'cover', state: 'insufficient_permission', prepared: 9 },
          ]),
        ],
        waiting: {
          applying: 0,
          awaiting_permission: 5,
          held_for_recovery: 0,
          retrying: 0,
          scheduled: 0,
        },
      }),
    )

    expect(offer.grants).toEqual([{ library: 'fiction', destinations: ['title', 'authors'] }])
    // The Cover behind its own gate is excluded with its reason and does not
    // take the metadata beside it out of the offer, nor its prepared work into
    // the number the action claims to carry.
    expect(offer.scopes[0]?.excluded.map((entry) => entry.destination)).toEqual(['cover'])
    expect(offer.changes).toBe(5)
    expect(offer.state).toBe('off')
    expect(offer.blocked).toBeNull()
    expect(offer.actionable).toBe(true)
  })

  it('is never complete while a lapse leaves no library holding permission (#1370)', () => {
    const lapse = (library: string) => ({
      connection: 'primary',
      library,
      reason: 'policy_changed' as const,
    })
    const scoped = (consented: boolean, lapsed = [lapse('history'), lapse('fiction')]) => ({
      configured_libraries: [
        { connection: 'primary', library: 'fiction' },
        { connection: 'primary', library: 'history' },
      ],
      capabilities: [
        capability('fiction', [{ destination: 'title', state: 'allowed', consented }]),
        capability('history', [{ destination: 'title', state: 'allowed' }]),
      ],
      lapsed_consents: lapsed,
    })

    const lapsed = readWriteConsent(view(scoped(false)))
    expect([lapsed.state, lapsed.actionable]).toEqual(['off', true])
    expect(lapsed.lapsed).toBe(
      'Write permission for history and fiction lapsed because it was given under a policy that is no longer active.',
    )
    expect(
      readWriteConsent(view(scoped(false)), {
        libraryName: (name) => (name === 'history' ? 'History Shelf' : name),
      }).lapsed,
    ).toBe(
      'Write permission for History Shelf and fiction lapsed because it was given under a policy that is no longer active.',
    )
    // Permission fiction still holds keeps an empty queue complete: writes are
    // on for it, so the state says so. History's lapse beside it is not
    // dropped: it stays on the offer for Library and Activity to state (#1370).
    const mixed = readWriteConsent(view(scoped(true, [lapse('history')])))
    expect([mixed.state, mixed.lapsed]).toEqual([
      'complete',
      'Write permission for history lapsed because it was given under a policy that is no longer active.',
    ])
    // A library that holds one destination while a lapse names it is held too:
    // the state follows the permission it holds and the lapse is still stated.
    const both = readWriteConsent(view(scoped(true, [lapse('fiction')])))
    expect([both.state, both.lapsed]).toEqual([
      'complete',
      'Write permission for fiction lapsed because it was given under a policy that is no longer active.',
    ])
    expect(readWriteConsent(view({ ...scoped(false), lapsed_consents: [] })).state).toBe('complete')
  })

  it('reads its state from the queue rather than from the last command', () => {
    const scoped = {
      configured_libraries: [{ connection: 'primary', library: 'fiction' }],
      capabilities: [
        capability('fiction', [{ destination: 'title', state: 'allowed', prepared: 1 }]),
      ],
    }
    const reasons = {
      applying: 0,
      awaiting_permission: 0,
      held_for_recovery: 0,
      retrying: 0,
      scheduled: 0,
    }

    expect(readWriteConsent(view({ ...scoped, waiting: reasons })).state).toBe('complete')
    expect(readWriteConsent(view({ ...scoped, waiting: { ...reasons, scheduled: 3 } })).state).toBe(
      'enabled',
    )
    expect(readWriteConsent(view({ ...scoped, waiting: { ...reasons, applying: 1 } })).state).toBe(
      'applying',
    )
    expect(readWriteConsent(view({ ...scoped, waiting: reasons }), { enabling: true }).state).toBe(
      'enabling',
    )
    // Nothing left to do is not an action, so it stops offering one.
    expect(readWriteConsent(view({ ...scoped, waiting: reasons })).actionable).toBe(false)
  })

  it('says why the action cannot be taken instead of offering an action that would fail', () => {
    const capabilities = [
      capability('fiction', [{ destination: 'cover', state: 'connection_unavailable' }]),
    ]
    const configured = [{ connection: 'primary', library: 'fiction' }]
    const waiting = {
      applying: 0,
      awaiting_permission: 4,
      held_for_recovery: 0,
      retrying: 0,
      scheduled: 2,
    }

    expect(
      readWriteConsent(view({ configured_libraries: configured, capabilities, waiting })).blocked,
    ).toBe('No selected destination is eligible for writes right now.')
    expect(
      readWriteConsent(
        view({ backup_ready: false, configured_libraries: configured, capabilities, waiting }),
      ).blocked,
    ).toBe('Backup readiness has not been confirmed, so writes cannot be enabled yet.')
    // A connection an owner has to check first makes enabling writes the wrong
    // next step; preparation still running does not, and is not consulted here.
    expect(
      readWriteConsent(view({ configured_libraries: configured, capabilities, waiting }), {
        connectionUnavailable: true,
      }).actionable,
    ).toBe(false)
  })
})

it('does not offer readiness, a destination or a count from unchecked history', () => {
  const unchecked = activity({
    configured_libraries: [{ connection: 'primary', library: 'fiction' }],
    capabilities: [
      capability('fiction', [{ destination: 'title', state: 'allowed', prepared: 5 }]),
    ],
    waiting: {
      applying: 0,
      awaiting_permission: 5,
      held_for_recovery: 0,
      retrying: 0,
      scheduled: 0,
    },
  })
  const offer = readWriteConsent(unchecked)
  expect(offer.grants).toEqual([])
  expect(offer.scopes).toEqual([])
  expect(offer.changes).toBe(0)
  // Unchecked readiness is no block; each surface says once to check it (#1286).
  expect(offer.blocked).toBeNull()
  const enabled = readWriteConsent({
    ...unchecked,
    waiting: { ...unchecked.waiting, awaiting_permission: 0, scheduled: 5 },
  })
  expect(enabled.state).toBe('enabled')
  expect(enabled.blocked).toBeNull()
  expect(enabled.grants).toEqual([])
})

/**
 * Library's notice reads the narrow consent response and Activity reads its
 * whole page. The server sends both from one projection; this proves the
 * browser then states one offer from them in every state either can show,
 * including while a connection needs checking (#1236).
 */
describe('the Library notice and Activity state one offer', () => {
  const queue = {
    off: { applying: 0, awaiting_permission: 2, scheduled: 0 },
    applying: { applying: 1, awaiting_permission: 0, scheduled: 1 },
    enabled: { applying: 0, awaiting_permission: 0, scheduled: 2 },
    complete: { applying: 0, awaiting_permission: 0, scheduled: 0 },
  } as const
  const cases = Object.keys(queue).flatMap((state) =>
    ['allowed', 'connection_unavailable'].flatMap((capability) =>
      [false, true].flatMap((enabling) =>
        [false, true].map(
          (connectionUnavailable) =>
            [state as keyof typeof queue, capability, enabling, connectionUnavailable] as const,
        ),
      ),
    ),
  )
  it.each(cases)(
    '%s with a %s destination, enabling=%s, connection unavailable=%s',
    (state, capability, enabling, connectionUnavailable) => {
      const raw = {
        ...currentActivity,
        configured_libraries: [
          {
            connection: 'primary',
            library: 'fiction',
            prepared: { automatic: 1, human: 1, total: 2 },
          },
        ],
        capabilities: [
          {
            connection: 'primary',
            library: 'fiction',
            state: capability,
            destinations: [
              {
                consented: state !== 'off',
                destination: 'title',
                prepared: 2,
                state: capability,
              },
            ],
          },
        ],
        waiting: { ...queue[state], held_for_recovery: 0, retrying: 0 },
      }
      const { backup_ready, capabilities, configured_libraries, lapsed_consents, waiting } = raw
      const options = { enabling, connectionUnavailable }
      const notice = readWriteConsent(
        mapActivityConsent({
          backup_ready,
          capabilities,
          configured_libraries,
          lapsed_consents,
          waiting,
        }),
        options,
      )
      expect(notice).toEqual(readWriteConsent(mapActivity(raw), options))
      expect(notice.state).toBe(enabling ? 'enabling' : state)
    },
  )
})

describe('the consent basis a permission draft is kept against', () => {
  const base = {
    configured_libraries: [
      { connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 1, total: 2 } },
      { connection: 'primary', library: 'history', prepared: { automatic: 0, human: 1, total: 1 } },
    ],
    capabilities: [
      capability('fiction', [
        { destination: 'title', state: 'allowed', prepared: 2 },
        { destination: 'cover', state: 'insufficient_permission' },
      ]),
      capability('history', [{ destination: 'title', state: 'allowed', prepared: 1 }]),
    ],
  }
  type Basis = typeof base
  const [fiction, history] = base.capabilities as [
    Basis['capabilities'][number],
    Basis['capabilities'][number],
  ]
  const withFiction = (change: Partial<typeof fiction>): Basis => ({
    ...base,
    capabilities: [{ ...fiction, ...change }, history],
  })
  const destinations = (change: (entry: (typeof fiction.destinations)[number]) => object) =>
    withFiction({
      destinations: fiction.destinations.map((entry) => ({ ...entry, ...change(entry) })),
    })

  it.each<[string, Basis, string]>([
    [
      'prepared counts',
      {
        configured_libraries: base.configured_libraries.map((item) => ({
          ...item,
          prepared: { automatic: 9, human: 9, total: 18 },
        })),
        capabilities: destinations(() => ({ prepared: 7 })).capabilities,
      },
      'policy-1',
    ],
    [
      'library order',
      { ...base, configured_libraries: [...base.configured_libraries].reverse() },
      'policy-1',
    ],
    ['capability order', { ...base, capabilities: [history, fiction] }, 'policy-1'],
    [
      'destination order',
      withFiction({ destinations: [...fiction.destinations].reverse() }),
      'policy-1',
    ],
  ])('is unchanged by %s', (_name, value, policyId) => {
    expect(consentBasis(value, policyId)).toBe(consentBasis(base, 'policy-1'))
  })

  it.each<[string, Basis, string]>([
    ['the active policy', base, 'policy-2'],
    [
      'a configured library',
      { ...base, configured_libraries: base.configured_libraries.slice(0, 1) },
      'policy-1',
    ],
    [
      'a library connection',
      {
        ...base,
        configured_libraries: base.configured_libraries.map((item) => ({
          ...item,
          connection: 'archive',
        })),
      },
      'policy-1',
    ],
    ['a library capability', withFiction({ state: 'allowed' }), 'policy-1'],
    [
      'a destination state',
      destinations((entry) => (entry.destination === 'cover' ? { state: 'allowed' } : {})),
      'policy-1',
    ],
    [
      'a destination consent',
      destinations((entry) => (entry.destination === 'title' ? { consented: true } : {})),
      'policy-1',
    ],
    [
      'an added destination',
      withFiction({
        destinations: [
          ...fiction.destinations,
          { destination: 'authors', state: 'allowed', consented: false, prepared: 0 },
        ],
      }),
      'policy-1',
    ],
  ])('changes with %s', (_name, value, policyId) => {
    expect(consentBasis(value, policyId)).not.toBe(consentBasis(base, 'policy-1'))
  })
})

it('explains an exclusion after a lapse by what is prepared, not readiness (#1286)', () => {
  expect(destinationReason('not_ready')).toBe('it is not ready yet')
  expect(destinationReason('not_ready', true)).toBe(
    'nothing is prepared for it yet; you can give permission again once a change is prepared',
  )
  // A held destination keeps its own reason whether or not permission lapsed.
  expect(destinationReason('insufficient_permission', true)).toBe(
    'the connection lacks permission for it',
  )
  const lapsed = activity({
    configured_libraries: [{ connection: 'primary', library: 'history' }],
    capabilities: [
      capability('history', [{ destination: 'title', state: 'not_ready', prepared: 0 }]),
    ],
    lapsed_consents: [{ connection: 'primary', library: 'history', reason: 'policy_changed' }],
  })
  // Nothing eligible: no scope is offered, and the card says writes are off.
  const offer = readWriteConsent({ ...lapsed, backup_ready: true })
  expect(offer.state).toBe('off')
  expect(offer.scopes).toEqual([])
})

describe('consent outlives a suspension (#1286 B1)', () => {
  const destination = (
    name: TargetDestination,
    state: DestinationState,
    consented: boolean,
  ): ActivityCapabilityView['destinations'][number] => ({
    destination: name,
    state,
    consented,
    prepared: 1,
  })
  const library = (name: string, destinations: ActivityCapabilityView['destinations']) => ({
    capability: {
      connection: 'primary',
      library: name,
      state: 'partially_allowed' as const,
      destinations,
    },
    configured: { connection: 'primary', library: name, prepared: null },
  })

  it('enables writes only for a consented destination that is not suspended', () => {
    expect(writesEnabled(destination('title', 'allowed', true))).toBe(true)
    expect(writesEnabled(destination('title', 'unexpected_effect_behavior', true))).toBe(false)
    expect(writesEnabled(destination('title', 'field_unavailable', true))).toBe(false)
    // Not yet checked is not held: the permission stands.
    expect(writesEnabled(destination('title', 'not_ready', true))).toBe(true)
    expect(writesEnabled(destination('title', 'allowed', false))).toBe(false)
  })

  it('holds a library whose consented destinations are all suspended', () => {
    expect(libraryWritesHeld([destination('title', 'unexpected_effect_behavior', true)])).toBe(true)
    expect(
      libraryWritesHeld([
        destination('title', 'unexpected_effect_behavior', true),
        destination('genres', 'allowed', false),
      ]),
    ).toBe(false)
  })

  it('never says writes are on for a library whose consented writes are all held', () => {
    const fiction = library('fiction', [
      destination('title', 'allowed', true),
      destination('genres', 'unexpected_effect_behavior', true),
    ])
    const research = library('research', [destination('title', 'unexpected_effect_behavior', true)])
    const history = library('history', [destination('title', 'allowed', false)])
    const all = [fiction, research, history]
    const data = {
      capabilities: all.map((item) => item.capability),
      configured_libraries: all.map((item) => item.configured),
    }
    // Both fiction and research hold permission; only fiction still writes.
    expect(writesOffSentence(data, ['fiction', 'research'], (name) => name)).toBe(
      'Writes are on for fiction; history needs permission; writes to research are held.',
    )
    // With no library writing, writes are off and the held one is named (#1286 L2).
    expect(writesOffSentence(data, ['research'], (name) => name)).toBe(
      'Writes are off; writes to research are held.',
    )
  })
})
