import { describe, expect, it } from 'vitest'

import type { AskedAgainView } from '../src/api/http'
import { formatDate, formatWhen } from '../src/dates'
import { askedAgainSentence, keptFromSentence } from '../src/fieldReasons'

// The sentences of #2039 (contract 3.1 and U2), with the formatter's locale
// and zone fixed as the date tests fix them.
const zone = { locale: 'en-GB', timeZone: 'UTC' } as const
const now = new Date('2026-11-26T12:00:00Z')
const day = Date.parse('2026-11-03T09:30:00Z') / 1000
const lastYear = Date.parse('2025-11-04T09:30:00Z') / 1000

const asked = (
  reason: AskedAgainView['reason'],
  outcome: AskedAgainView['earlier']['outcome'] = 'accept',
  value: AskedAgainView['earlier']['value'] = { kind: 'text', value: 'Example Title' },
  occurredAt = day,
): AskedAgainView => ({ reason, earlier: { outcome, value, occurred_at: occurredAt } })
const say = (value: AskedAgainView) => askedAgainSentence(value, now, zone)

describe('formatDate (#2039)', () => {
  it('names the weekday, day and month, and the year only in another year', () => {
    expect(formatDate(day, now, zone)).toBe('Tue 3 Nov')
    expect(formatDate(lastYear, now, zone)).toBe('Tue, 4 Nov 2025')
  })

  it('is the day formatWhen shows beside a clock time', () => {
    expect(formatWhen(day, now, zone)).toBe(`${formatDate(day, now, zone)}, 9:30`)
    expect(formatWhen(lastYear, now, zone)).toBe('Tue, 4 Nov 2025, 9:30')
  })
})

describe('askedAgainSentence (#2039)', () => {
  it.each([
    ['question_changed', 'The choices changed since you chose “Example Title” on Tue 3 Nov.'],
    [
      'target_changed',
      'Audiobookshelf shows a different value from when you chose “Example Title” on Tue 3 Nov.',
    ],
    [
      'withdrawn',
      'You withdrew your later choice. Before it, you chose “Example Title” on Tue 3 Nov.',
    ],
    [
      'resume_failed',
      'Resume automatic handling did not finish. You chose “Example Title” on Tue 3 Nov.',
    ],
    ['retired', 'Your choice of “Example Title” on Tue 3 Nov was not written before the upgrade.'],
    [
      'matching_changed',
      'Book matching changed what this field offers since you chose “Example Title” on Tue 3 Nov.',
    ],
    ['other', 'You chose “Example Title” on Tue 3 Nov. This field asks you again.'],
  ] as const)('says %s in its own sentence', (reason, sentence) => {
    expect(say(asked(reason))).toBe(sentence)
  })

  it.each([
    ['accept', 'chose'],
    ['owner_entered', 'entered'],
    ['keep_current', 'kept'],
  ] as const)('names a %s as "%s" in every reason', (outcome, verb) => {
    expect(say(asked('question_changed', outcome))).toBe(
      `The choices changed since you ${verb} “Example Title” on Tue 3 Nov.`,
    )
    expect(say(asked('target_changed', outcome))).toBe(
      `Audiobookshelf shows a different value from when you ${verb} “Example Title” on Tue 3 Nov.`,
    )
    expect(say(asked('withdrawn', outcome))).toBe(
      `You withdrew your later choice. Before it, you ${verb} “Example Title” on Tue 3 Nov.`,
    )
    expect(say(asked('resume_failed', outcome))).toBe(
      `Resume automatic handling did not finish. You ${verb} “Example Title” on Tue 3 Nov.`,
    )
    expect(say(asked('matching_changed', outcome))).toBe(
      `Book matching changed what this field offers since you ${verb} “Example Title” on Tue 3 Nov.`,
    )
    expect(say(asked('other', outcome))).toBe(
      `You ${verb} “Example Title” on Tue 3 Nov. This field asks you again.`,
    )
    // The retired sentence names the value, not the verb.
    expect(say(asked('retired', outcome))).toBe(
      'Your choice of “Example Title” on Tue 3 Nov was not written before the upgrade.',
    )
  })

  it('quotes nothing for a clear, as the owner controls do', () => {
    expect(say(asked('target_changed', 'owner_entered', { kind: 'empty', value: null }))).toBe(
      'Audiobookshelf shows a different value from when you entered nothing on Tue 3 Nov.',
    )
    expect(say(asked('other', 'keep_current', null))).toBe(
      'You kept nothing on Tue 3 Nov. This field asks you again.',
    )
  })

  it('names the proposed change for a decline, which has no value', () => {
    expect(say(asked('question_changed', 'reject', null))).toBe(
      'The choices changed since you declined the proposed change on Tue 3 Nov.',
    )
    expect(say(asked('target_changed', 'reject', null))).toBe(
      'Audiobookshelf shows a different value from when you declined the proposed change on Tue 3 Nov.',
    )
    expect(say(asked('withdrawn', 'reject', null))).toBe(
      'You withdrew your later choice. Before it, you declined the proposed change on Tue 3 Nov.',
    )
    expect(say(asked('resume_failed', 'reject', null))).toBe(
      'Resume automatic handling did not finish. You declined the proposed change on Tue 3 Nov.',
    )
    expect(say(asked('matching_changed', 'reject', null))).toBe(
      'Book matching changed what this field offers since you declined the proposed change on Tue 3 Nov.',
    )
    expect(say(asked('other', 'reject', null))).toBe(
      'You declined the proposed change on Tue 3 Nov. This field asks you again.',
    )
    // A retired decline has no value to name: the field keeps its server reason.
    expect(say(asked('retired', 'reject', null))).toBeUndefined()
  })

  it('shows the year only when the answer was given in another year', () => {
    expect(say(asked('other', 'accept', undefined, lastYear))).toBe(
      'You chose “Example Title” on Tue, 4 Nov 2025. This field asks you again.',
    )
  })

  it('bounds a long value and lists several as the page does', () => {
    const long = 'x'.repeat(200)
    expect(say(asked('other', 'owner_entered', { kind: 'text', value: long }))).toBe(
      `You entered “${'x'.repeat(157)}…” on Tue 3 Nov. This field asks you again.`,
    )
    expect(say(asked('other', 'accept', { kind: 'names', value: ['Ada', 'Grace'] }))).toBe(
      'You chose “Ada · Grace” on Tue 3 Nov. This field asks you again.',
    )
  })

  it('says nothing without a reason', () => {
    expect(askedAgainSentence(null)).toBeUndefined()
    expect(askedAgainSentence(undefined)).toBeUndefined()
  })
})

describe('keptFromSentence (#2039)', () => {
  it('names the day of the choice a carried answer kept', () => {
    expect(keptFromSentence(day, now, zone)).toBe(
      'Kept from your choice on Tue 3 Nov, because the same choices were offered.',
    )
    expect(keptFromSentence(lastYear, now, zone)).toBe(
      'Kept from your choice on Tue, 4 Nov 2025, because the same choices were offered.',
    )
  })
})
