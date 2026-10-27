import { describe, expect, it } from 'vitest'

import { formatClock, formatDay, formatWhen } from '../src/dates'

// The formatter's own tests fix the locale and zone; the application reads the
// browser's (#1286).
const zone = { locale: 'en-GB', timeZone: 'UTC' } as const
const at = (iso: string) => Date.parse(iso) / 1000
const now = new Date('2026-10-28T14:05:00Z')

describe('formatWhen (#1286)', () => {
  it.each([
    ['2026-10-28T14:05:20Z', 'now'],
    ['2026-10-28T14:04:50Z', 'now'],
    ['2026-10-28T14:00:00Z', '5 minutes ago'],
    ['2026-10-28T14:10:00Z', 'in 5 minutes'],
    ['2026-10-28T13:06:00Z', '59 minutes ago'],
    ['2026-10-28T14:06:00Z', 'in 1 minute'],
  ])('reads %s, within the hour either side, relative to now as %j', (iso, shown) => {
    expect(formatWhen(at(iso), now, zone)).toBe(shown)
  })

  it('reads a time earlier or later the same day as its clock time', () => {
    expect(formatWhen(at('2026-10-28T09:30:00Z'), now, zone)).toBe('9:30')
    expect(formatWhen(at('2026-10-28T23:59:00Z'), now, zone)).toBe('23:59')
  })

  it('adds the weekday and date on another day, and the year in another year', () => {
    expect(formatWhen(at('2026-10-27T14:05:00Z'), now, zone)).toBe('Tue 27 Oct, 14:05')
    expect(formatWhen(at('2026-10-29T08:00:00Z'), now, zone)).toBe('Thu 29 Oct, 8:00')
    expect(formatWhen(at('2025-12-31T23:00:00Z'), now, zone)).toBe('Wed, 31 Dec 2025, 23:00')
  })

  it('reads the clock time alone where the day is already shown', () => {
    expect(formatWhen(at('2026-10-20T07:15:00Z'), now, { ...zone, dayShown: true })).toBe('7:15')
    // Recent times stay relative even under a day heading.
    expect(formatWhen(at('2026-10-28T14:00:00Z'), now, { ...zone, dayShown: true })).toBe(
      '5 minutes ago',
    )
  })

  it('decides the day in the reader’s zone, not in UTC', () => {
    const tokyo = { locale: 'en-GB', timeZone: 'Asia/Tokyo' } as const
    // 23:30 UTC on the 27th is the 28th in Tokyo, the same day as now there.
    expect(formatWhen(at('2026-10-27T23:30:00Z'), now, tokyo)).toBe('8:30')
  })

  it('names a ledger day with its weekday and year, and reads the 24-hour clock', () => {
    expect(formatDay(at('2026-10-28T14:05:00Z'), zone)).toBe('Wed, 28 Oct 2026')
    const clock = formatClock(at('2026-10-28T14:05:00Z'), { locale: 'en-US', timeZone: 'UTC' })
    expect(clock).toBe('14:05')
    expect(clock).not.toMatch(/ /u)
  })
})
