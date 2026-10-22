import { describe, expect, it } from 'vitest'

import { activityEventKeys, activityOutcome, ledgerRows } from '../src/activityState'

describe('activity ledger state', () => {
  it('fails closed while classifying every authoritative Activity command outcome', () => {
    expect(activityOutcome({}, { path: 'field-effects/pause' })).toBe('unknown')
    expect(activityOutcome({ paused: true }, { path: 'field-effects/pause' })).toBe('applied')
    expect(activityOutcome({ paused: false }, { path: 'field-effects/pause' })).toBe('unknown')
    expect(activityOutcome({ paused: false }, { path: 'field-effects/resume' })).toBe('applied')
    expect(activityOutcome({ paused: true }, { path: 'field-effects/resume' })).toBe('unknown')
    expect(activityOutcome({ paused: 'false' }, { path: 'field-effects/resume' })).toBe('unknown')

    const revoke = { path: 'activity/primary/fiction/revoke' }
    expect(activityOutcome({ enabled_libraries: [] }, revoke)).toBe('applied')
    expect(
      activityOutcome(
        { enabled_libraries: [{ connection: 'primary', library: 'fiction' }] },
        revoke,
      ),
    ).toBe('unknown')
    expect(activityOutcome({ enabled_libraries: [{}] }, revoke)).toBe('unknown')

    const consent = {
      path: 'activity/consent',
      body: { grants: [{ library: 'fiction', destinations: ['title'] }] },
    }
    expect(
      activityOutcome(
        {
          capabilities: [
            { library: 'fiction', destinations: [{ destination: 'title', consented: true }] },
          ],
        },
        consent,
      ),
    ).toBe('applied')
    expect(
      activityOutcome(
        {
          capabilities: [
            { library: 'fiction', destinations: [{ destination: 'title', consented: false }] },
          ],
        },
        consent,
      ),
    ).toBe('unknown')
    expect(
      activityOutcome(
        { capabilities: [{ library: 'fiction', destinations: [{ destination: 'title' }] }] },
        consent,
      ),
    ).toBe('unknown')
    expect(
      activityOutcome(
        {
          capabilities: [
            {
              library: 'fiction',
              destinations: [
                { destination: 'title', consented: true },
                { destination: 'title', consented: true },
              ],
            },
          ],
        },
        consent,
      ),
    ).toBe('unknown')
    expect(
      activityOutcome(
        {
          capabilities: [
            { library: 'fiction', destinations: [{ destination: 'title', consented: true }] },
            { library: 'fiction', destinations: [{ destination: 'title', consented: false }] },
          ],
        },
        consent,
      ),
    ).toBe('unknown')
    expect(activityOutcome({ capabilities: [{}] }, consent)).toBe('unknown')
  })

  it('keeps numeric ledger identities stable across newest-first capped polling', () => {
    const existing = Array.from({ length: 50 }, (_, index) => ({
      work_id: `work-${String(index)}`,
      destination: index % 2 === 0 ? 'title' : 'authors',
      state: 'verified',
      occurred_at: 1_000 - index,
      library: 'fiction',
      origin: 'human',
      failure: null,
    }))
    const next = [
      { ...existing[0], work_id: 'new-work', occurred_at: 1_001 },
      ...existing.slice(0, 49),
    ]
    expect(activityEventKeys(next).slice(1)).toEqual(activityEventKeys(existing).slice(0, 49))

    const duplicate = {
      work_id: 'same',
      destination: 'title',
      state: 'verified',
      occurred_at: 1_000,
      library: 'fiction',
      origin: 'human',
      failure: null,
    }
    const duplicateWindow = Array.from({ length: 50 }, () => duplicate)
    expect(new Set(activityEventKeys([duplicate, ...duplicateWindow.slice(0, 49)]))).toEqual(
      new Set(activityEventKeys(duplicateWindow)),
    )
  })
})

describe('ledger rows (#1281)', () => {
  const event = (id: string, check_group: string | null = null) => ({ id, check_group })
  const shape = (events: ReturnType<typeof event>[]) =>
    ledgerRows(events).map((row) =>
      row.kind === 'event'
        ? row.event.id
        : `${row.key}[${row.members.map((member) => member.id).join(',')}]`,
    )
  it.each([
    ['no keys', [event('a'), event('b')], ['a', 'b']],
    ['one key, adjacent', [event('a', 'k'), event('b', 'k'), event('c')], ['k[a,b]', 'c']],
    [
      'one key around a change that keeps its place',
      [event('a', 'k'), event('change'), event('b', 'k')],
      ['k[a,b]', 'change'],
    ],
    [
      'two keys',
      [event('a', 'k'), event('b', 'j'), event('c', 'k'), event('d', 'j')],
      ['k[a,c]', 'j[b,d]'],
    ],
    ['a lone member still forms a checks row', [event('a'), event('b', 'k')], ['a', 'k[b]']],
    [
      'page order is kept within and between rows',
      [event('x'), event('a', 'k'), event('y'), event('b', 'k'), event('z')],
      ['x', 'k[a,b]', 'y', 'z'],
    ],
  ])('%s', (_name, events, expected) => {
    expect(shape(events)).toEqual(expected)
  })
  it('keeps every event exactly once', () => {
    const events = [event('a', 'k'), event('b'), event('c', 'j'), event('d', 'k')]
    const reached = ledgerRows(events).flatMap((row) =>
      row.kind === 'event' ? [row.event] : row.members,
    )
    expect(reached.map((item) => item.id).sort()).toEqual(['a', 'b', 'c', 'd'])
  })
})
