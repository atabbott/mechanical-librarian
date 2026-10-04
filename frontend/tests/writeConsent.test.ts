import { describe, expect, it } from 'vitest'

import { readWriteConsent } from '../src/writeConsent'
import { activity } from './fixtures'

const view = (value: Partial<Parameters<typeof readWriteConsent>[0]>) => ({
  ...activity(),
  backup_ready: true,
  ...value,
})

const capability = (
  library: string,
  destinations: readonly {
    destination: string
    state: string
    consented?: boolean
    prepared?: number
  }[],
) => ({
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
  expect(offer.blocked).toBe('Open permission review to check current readiness.')
  const enabled = readWriteConsent({
    ...unchecked,
    waiting: { ...unchecked.waiting, awaiting_permission: 0, scheduled: 5 },
  })
  expect(enabled.state).toBe('enabled')
  expect(enabled.blocked).toBeNull()
  expect(enabled.grants).toEqual([])
})
