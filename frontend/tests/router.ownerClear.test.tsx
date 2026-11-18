import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type {
  CaseDetailView,
  DecisionCommand,
  OwnerControlView,
  ReviewHttp,
  TargetDestination,
} from '../src/api/http'
import { destinationLabel } from '../src/labels'
import { SettledFieldContext } from '../src/routes/FieldDecisionOutcome'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import {
  createHttp,
  detail,
  library,
  readCaseDetail,
  recordedAcknowledgement,
  renderRoute,
} from './fixtures'

// Clearing a field and entering names in the browser (#1523, ADR-0105): an
// emptied form clears a clearable field with one press; names are one per line.
type Field = CaseDetailView['fields'][number]
const base = detail.fields[0]
if (!base) throw new Error('workspace fixture requires a title field')
const EMPTY = { kind: 'empty', value: null }
const control = (value: Partial<OwnerControlView>): OwnerControlView => ({
  entry: null,
  subset: null,
  names: null,
  clear: false,
  current: null,
  withdraw: null,
  ...value,
})
const textControl = (current: string | null, clear: boolean, suggestions = []) =>
  control({ entry: { maxCharacters: 512, suggestions }, clear, current })
const namesControl = (
  current: readonly string[] | null,
  clear: boolean,
  previous: readonly string[] | null = null,
) => control({ names: { maxItems: 32, maxCharacters: 512, previous }, clear, current })
const field = (
  destination: TargetDestination,
  currentRaw: unknown,
  ownerControl: OwnerControlView,
  rest: Partial<Field> = {},
): Field => ({
  ...base,
  destination,
  classification: 'unsupported',
  classificationCause: 'nothing_to_decide',
  classificationReason: 'No source offered a different value. You can enter your own.',
  decisionActions: [],
  editable: false,
  needsAttention: false,
  navigationRole: 'inspect',
  currentValue: Array.isArray(currentRaw) ? currentRaw.join(' · ') : String(currentRaw),
  currentRaw,
  proposedValues: [],
  proposalId: `proposal-${destination}`,
  targetFingerprint: `fingerprint-${destination}`,
  ownerControl,
  ...rest,
})
// What the store returns once the owner's value is recorded.
const decided = (before: Field, selected: unknown): Field => ({
  ...before,
  classification: 'human_recorded',
  classificationCause: 'human_decided',
  classificationReason: 'You decided this field. You can change it here.',
  humanDecision: {
    actor: 'owner',
    application: 'apply',
    application_reason: null,
    decision_id: `decision-${before.destination}`,
    destination: before.destination,
    occurred_at: 2,
    outcome: 'owner_entered',
    proposal_id: before.proposalId,
    review_case_id: 'case-1',
    selected_value: selected as never,
    target_fingerprint: before.targetFingerprint,
  },
  ownerControl: before.ownerControl ? { ...before.ownerControl, withdraw: 'available' } : null,
  pinControl: { pin: 'available', unpin: false },
})
const quietLibrary = {
  ...library,
  counts: { all: 1, attention: 0, clear: 1, notMatched: 0, failed: 0, flagged: 0, new: 0 },
  works: library.works.map((work) => ({ ...work, attentionCount: 0 })),
}
// One field before the command, and what the store reads back after it.
const owner = (
  before: Field,
  after?: (command: DecisionCommand) => Field,
  record?: ReviewHttp['recordDecision'],
) => {
  let served = before
  const http = createHttp()
  const recordDecision = vi.fn<ReviewHttp['recordDecision']>(
    record ??
      (async (caseId, command) => {
        served = after ? after(command) : decided(before, command.selectedValue)
        const acknowledgement = recordedAcknowledgement(
          { ...detail, id: caseId },
          {
            invalidation_sequences: [],
            schema_version: 'human-decision-ack-v2' as const,
            actor: 'owner',
            application: 'apply',
            application_reason: null,
            decision_id: 'decision-owner',
            destination: command.destination,
            occurred_at: 2,
            outcome: command.outcome,
            proposal_id: command.proposalId,
            review_case_id: caseId,
            selected_value: command.selectedValue ?? null,
            target_fingerprint: command.targetFingerprint,
          },
        )
        return {
          ...acknowledgement,
          confirmation: { status: 'confirmed', case: { ...detail, fields: [served] } },
        }
      }),
  )
  return {
    ...http,
    getLibrary: vi.fn(async () => quietLibrary),
    getAttention: vi.fn(async () => ({ total: 0, items: [] })),
    getCaseDetail: vi.fn(async () => ({ ...detail, fields: [served] })),
    recordDecision,
  }
}
const open = async (http: ReturnType<typeof owner>, destination: TargetDestination) => {
  renderRoute(`/library?lens=all&preview=case-1&field=${destination}`, http)
  const name = destinationLabel(destination)
  return await screen.findByRole('region', { name: `Your value for ${name}` })
}
const sent = (http: ReturnType<typeof owner>): DecisionCommand[] =>
  http.recordDecision.mock.calls.map(([, command]) => command)

describe('clearing a field', () => {
  it.each([
    ['subtitle', 'Subtitle', 'A Quiet Subtitle'],
    ['publisher', 'Publisher', 'Example House'],
  ] as const)(
    'empties %s, presses Clear once and says only the clear line',
    async (destination, label, value) => {
      const user = userEvent.setup()
      let release: (() => void) | undefined
      const before = field(destination, value, textControl(value, true))
      const http = owner(before)
      const recordDecision = http.recordDecision.getMockImplementation()
      http.recordDecision.mockImplementation(async (...args) => {
        await new Promise<void>((resolve) => {
          release = resolve
        })
        if (!recordDecision) throw new Error('recording required')
        return await recordDecision(...args)
      })
      const panel = await open(http, destination)
      await user.click(within(panel).getByRole('button', { name: 'Change' }))
      const input = within(panel).getByRole('textbox', { name: `Your value for ${label}` })
      // It opens with the field's value, so emptying it is the owner's act.
      expect(input).toHaveValue(value)
      expect(within(panel).getByRole('button', { name: 'Record this value' })).toBeVisible()
      await user.clear(input)
      const noun = destination
      const clear = within(panel).getByRole('button', { name: `Clear ${noun}` })
      // No line explains the clear (owner's choice A, Amendment 1).
      expect(panel).not.toHaveTextContent(/Clearing removes/u)
      await user.click(clear)
      await user.click(within(panel).getByRole('button', { name: 'Clearing…' }))
      expect(http.recordDecision).toHaveBeenCalledTimes(1)
      expect(sent(http)[0]).toMatchObject({
        destination,
        outcome: 'owner_entered',
        selectedValue: EMPTY,
      })
      release?.()
      const recorded = await within(panel).findByText(`You cleared the ${noun}.`)
      await waitFor(() => expect(recorded).toHaveFocus())
      // The pin stands beside the line, and nothing repeats it.
      const beside = recorded.closest<HTMLElement>('.owner-recorded-pin')
      if (!beside) throw new Error('the pin stands beside the recorded line')
      expect(within(beside).getByRole('button', { name: 'Pin this value' })).toBeVisible()
      expect(within(panel).getByRole('status')).toHaveTextContent('')
      expect(screen.queryByText('Your value is recorded.')).toBeNull()
      expect(panel).not.toHaveTextContent('Unavailable')
    },
  )

  it('clears genres when every genre is unticked', async () => {
    const user = userEvent.setup()
    const before = field(
      'genres',
      ['Drama'],
      control({
        clear: true,
        current: ['Drama'],
        subset: {
          maxItems: 32,
          options: [{ value: 'Drama', current: true, proposed: false, previous: false }],
        },
      }),
    )
    const http = owner(before)
    const panel = await open(http, 'genres')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.click(within(panel).getByRole('checkbox', { name: /Drama/u }))
    await user.click(within(panel).getByRole('button', { name: 'Clear genres' }))
    expect(sent(http)).toEqual([
      expect.objectContaining({ destination: 'genres', selectedValue: EMPTY }),
    ])
    expect(await within(panel).findByText('You cleared the genres.')).toBeVisible()
  })

  it('clears narrators when every name is removed', async () => {
    const user = userEvent.setup()
    const before = field('narrators', ['Ada Reader'], namesControl(['Ada Reader'], true))
    const http = owner(before)
    const panel = await open(http, 'narrators')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Narrators' })
    expect(names).toHaveValue('Ada Reader')
    await user.clear(names)
    // Blank lines are not names.
    await user.type(names, '{Enter}  {Enter}')
    await user.click(within(panel).getByRole('button', { name: 'Clear narrators' }))
    expect(sent(http)).toEqual([
      expect.objectContaining({ destination: 'narrators', selectedValue: EMPTY }),
    ])
    expect(await within(panel).findByText('You cleared the narrators.')).toBeVisible()
  })

  it('refuses an empty title instead of clearing', async () => {
    const user = userEvent.setup()
    const title = owner(field('title', 'A Book', textControl('A Book', false)))
    const panel = await open(title, 'title')
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    expect(within(panel).getByRole('textbox', { name: 'Your value for Title' })).toHaveValue('')
    expect(within(panel).queryByRole('button', { name: /^Clear/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter a title.')
    expect(title.recordDecision).not.toHaveBeenCalled()
  })

  it('refuses empty authors instead of clearing', async () => {
    const user = userEvent.setup()
    const authors = owner(field('authors', ['Ada Example'], namesControl(['Ada Example'], false)))
    const panel = await open(authors, 'authors')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.clear(within(panel).getByRole('textbox', { name: 'Your value for Authors' }))
    expect(within(panel).queryByRole('button', { name: /^Clear/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter at least one author.')
    expect(authors.recordDecision).not.toHaveBeenCalled()
  })

  it('asks for a value on an empty clearable field and sends nothing', async () => {
    const user = userEvent.setup()
    const http = owner(field('subtitle', null, textControl(null, true)))
    const panel = await open(http, 'subtitle')
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    expect(within(panel).queryByRole('button', { name: /^Clear/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    // Nothing is set and nothing was typed: the remedy, not a refusal (#1523).
    expect(within(panel).getByRole('alert')).toHaveTextContent(/^Enter a value first\.$/u)
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('goes back to empty after a write that filled the subtitle', async () => {
    const user = userEvent.setup()
    // The case that wrote it compares with what it wrote (S5): the field read
    // empty when the case was made.
    const written = decided(
      field('subtitle', null, textControl('A Quiet Subtitle', true), {
        currentValue: 'Not set in Audiobookshelf',
      }),
      { kind: 'text', value: 'A Quiet Subtitle' },
    )
    if (!written.humanDecision) throw new Error('a recorded decision is required')
    const http = owner({
      ...written,
      humanDecision: { ...written.humanDecision, outcome: 'accept' },
    })
    const panel = await open(http, 'subtitle')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Subtitle' })
    expect(input).toHaveValue('A Quiet Subtitle')
    await user.clear(input)
    await user.click(within(panel).getByRole('button', { name: 'Clear subtitle' }))
    expect(sent(http)[0]).toMatchObject({ selectedValue: EMPTY })
  })

  it.each([
    ['owner_value_unchanged', 'subtitle', 'This field is already empty.'],
    ['owner_value_required', 'title', 'Enter a title.'],
  ] as const)('says the store’s %s refusal in words', async (code, destination, copy) => {
    const user = userEvent.setup()
    const http = owner(
      // A stale page: the store says what changed.
      field(destination, 'Shown value', textControl('Shown value', destination !== 'title')),
      undefined,
      async () => {
        throw new ApiError(422, code)
      },
    )
    const panel = await open(http, destination)
    await user.click(
      within(panel).getByRole('button', {
        name: destination === 'title' ? 'Enter a value' : 'Change',
      }),
    )
    const input = within(panel).getByRole('textbox')
    await user.clear(input)
    if (destination === 'title') {
      // The browser refuses an empty title itself; the store says the same.
      await user.type(input, 'x')
      await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    } else await user.click(within(panel).getByRole('button', { name: 'Clear subtitle' }))
    await waitFor(() => expect(within(panel).getByRole('alert')).toHaveTextContent(copy))
  })
})

describe('character and name counts', () => {
  it('says the character count only once the value is over the limit', async () => {
    const user = userEvent.setup()
    const http = owner(field('publisher', 'Example House', textControl('Example House', true)))
    const panel = await open(http, 'publisher')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Publisher' })
    const count = panel.querySelector('.owner-count')
    expect(count).toHaveTextContent('')
    await user.clear(input)
    await user.click(input)
    await user.paste('x'.repeat(512))
    expect(count).toHaveTextContent('')
    // An overlong paste is kept, counted and refused until it fits.
    await user.paste('y')
    expect(input).toHaveValue(`${'x'.repeat(512)}y`)
    expect(count).toHaveTextContent('513 of 512 characters')
    expect(input).toHaveAccessibleDescription(/513 of 512 characters/u)
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('Use up to 512 characters.')
    expect(http.recordDecision).not.toHaveBeenCalled()
    await user.type(input, '{Backspace}')
    expect(count).toHaveTextContent('')
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(sent(http)[0]).toMatchObject({ selectedValue: { kind: 'text', value: 'x'.repeat(512) } })
  })

  it('says the names count only once there are more than 32', async () => {
    const user = userEvent.setup()
    const http = owner(field('authors', ['Ada Example'], namesControl(['Ada Example'], false)))
    const panel = await open(http, 'authors')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Authors' })
    const count = panel.querySelector('.owner-count')
    await user.clear(names)
    await user.click(names)
    await user.paste(Array.from({ length: 32 }, (_, index) => `Name ${String(index)}`).join('\n'))
    expect(count).toHaveTextContent('')
    await user.paste('\nName 32')
    expect(count).toHaveTextContent('33 of 32 names')
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('Use up to 32 names.')
    expect(http.recordDecision).not.toHaveBeenCalled()
  })
})

describe('names entry', () => {
  it('opens with the current names and records them one per line', async () => {
    const user = userEvent.setup()
    const http = owner(
      field('authors', ['Ada Example'], namesControl(['Ada Example'], false, ['Old Writer'])),
    )
    const panel = await open(http, 'authors')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Authors' })
    expect(names).toHaveFocus()
    expect(names).toHaveValue('Ada Example')
    expect(names).toHaveAccessibleDescription(/One name per line/u)
    await user.click(within(panel).getByRole('button', { name: 'Use previous names “Old Writer”' }))
    expect(names).toHaveValue('Old Writer')
    expect(names).toHaveFocus()
    await user.type(names, '{Enter}  Ada   Example ')
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(sent(http)[0]).toMatchObject({
      destination: 'authors',
      outcome: 'owner_entered',
      selectedValue: { kind: 'list', value: ['Old Writer', 'Ada Example'] },
    })
    const recorded = await within(panel).findByText('You entered 2 names.')
    await waitFor(() => expect(recorded).toHaveFocus())
  })

  it('says one name in the singular', async () => {
    const user = userEvent.setup()
    const http = owner(field('narrators', ['Ada Reader'], namesControl(['Ada Reader'], true)))
    const panel = await open(http, 'narrators')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Narrators' })
    await user.clear(names)
    await user.type(names, 'Bo Reader')
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(await within(panel).findByText('You entered 1 name.')).toBeVisible()
  })

  it.each([
    ['Ada Example\nAda Example', 'Enter each name once.'],
    [`Ada Example\n${'n'.repeat(513)}`, 'Use up to 512 characters for each name.'],
    ['Ada Example', 'Those are already the current names.'],
  ])('refuses %j without sending it', async (text, copy) => {
    const user = userEvent.setup()
    const http = owner(field('authors', ['Ada Example'], namesControl(['Ada Example'], false)))
    const panel = await open(http, 'authors')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Authors' })
    await user.clear(names)
    await user.click(names)
    await user.paste(text)
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(copy)
    expect(names).toHaveAttribute('aria-invalid', 'true')
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it.each([
    [
      'owner_value_invalid',
      'These names can’t be recorded. Enter up to 32 different names, one per line, with no links or file paths.',
    ],
    ['owner_value_unchanged', 'Those are already the current names.'],
    ['owner_value_required', 'Enter at least one author.'],
  ])('says the store’s %s refusal for names in words', async (code, copy) => {
    const user = userEvent.setup()
    const http = owner(
      field('authors', ['Ada Example'], namesControl(['Ada Example'], false)),
      undefined,
      async () => {
        throw new ApiError(422, code)
      },
    )
    const panel = await open(http, 'authors')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.type(
      within(panel).getByRole('textbox', { name: 'Your value for Authors' }),
      '{Enter}Bo Writer',
    )
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    await waitFor(() => expect(within(panel).getByRole('alert')).toHaveTextContent(copy))
  })
})

describe('a clear in History and the recorded outcome', () => {
  it('reads a clear as Cleared, never as an empty quote', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [
        {
          category: 'owner' as const,
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'subtitle' as const,
          outcome: 'owner_entered' as const,
          previousValue: { kind: 'text', value: 'A Quiet Subtitle' },
          selectedValue: { kind: 'empty', value: null },
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
    const article = (await screen.findByRole('heading', { name: 'Subtitle' })).closest('article')
    expect(article).toHaveTextContent('You cleared the subtitle.')
    expect(article).toHaveTextContent('From A Quiet Subtitle')
    expect(article).toHaveTextContent('To Cleared')
    expect(article).not.toHaveTextContent(/Unavailable|“”/u)
  })

  it('reads a keep that went back to empty, and a fill of an empty field, naturally', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [
        {
          category: 'owner' as const,
          occurredAt: 1786000001,
          applicationReason: null,
          destination: 'subtitle' as const,
          outcome: 'keep_current' as const,
          previousValue: { kind: 'text', value: 'A Novel of the Outer Banks' },
          selectedValue: null,
        },
        {
          category: 'owner' as const,
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'isbn' as const,
          outcome: 'accept' as const,
          previousValue: null,
          selectedValue: { kind: 'text', value: '9780306406157' },
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
    const kept = (await screen.findByRole('heading', { name: 'Subtitle' })).closest('article')
    expect(kept).toHaveTextContent('You went back to the value before your change.')
    expect(kept).toHaveTextContent('From A Novel of the Outer Banks')
    // A keep restores Not set; only a clear reads Cleared.
    expect(kept).toHaveTextContent('To Not set')
    expect(kept).not.toHaveTextContent('Cleared')
    const filled = screen.getByRole('heading', { name: 'ISBN' }).closest('article')
    expect(filled).toHaveTextContent('From Not set')
    expect(filled).toHaveTextContent('To 9780306406157')
    for (const article of [kept, filled])
      expect(article).not.toHaveTextContent('No value is recorded.')
  })

  it('shows a recorded clear as Cleared beside Your value', async () => {
    const cleared = decided(field('subtitle', 'A Quiet Subtitle', textControl(null, true)), EMPTY)
    const http = owner({ ...cleared, editable: false, ownerControl: null })
    renderRoute('/library?lens=all&preview=case-1&field=subtitle', http)
    const recorded = await screen.findByRole('region', { name: 'Subtitle recorded outcome' })
    expect(within(recorded).getByText('Your value')).toBeVisible()
    expect(recorded).toHaveTextContent('Cleared')
    expect(recorded).not.toHaveTextContent('Unavailable')
  })
})

describe('keeping an empty value on the case that wrote it', () => {
  it('says a field that can’t be emptied stays as written', async () => {
    const user = userEvent.setup()
    const isbn: Field = {
      ...base,
      destination: 'isbn',
      decisionActions: ['keep_current', 'accept'],
      currentValue: '',
      currentRaw: null,
      proposedValues: [{ kind: 'text', value: '9780306406157' }],
      proposalId: 'proposal-isbn',
      targetFingerprint: 'fingerprint-isbn',
      ownerControl: null,
    }
    const http = owner(isbn, undefined, async () => {
      throw new ApiError(422, 'owner_value_required')
    })
    renderRoute('/library?lens=all&preview=case-1&field=isbn', http)
    await user.click(await screen.findByRole('radio', { name: /^Choose current value/u }))
    expect(sent(http)[0]).toMatchObject({ outcome: 'keep_current', destination: 'isbn' })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The ISBN can’t be emptied. Choose a new value.',
      ),
    )
  })
})

describe('the keep card on a field this case wrote (#1523)', () => {
  const offered = (destination: TargetDestination, rest: Partial<Field>): Field => ({
    ...base,
    destination,
    classification: 'actionable',
    decisionActions: ['keep_current', 'accept'],
    proposalId: `proposal-${destination}`,
    targetFingerprint: `fingerprint-${destination}`,
    ownerControl: null,
    writtenByCase: true,
    ...rest,
  })
  // An ISBN the case wrote over 9780306406157, now reading its write.
  const isbnOver = (before: string | null): Field =>
    offered('isbn', {
      currentValue: before ?? 'Not set in Audiobookshelf',
      currentRaw: before,
      proposedValues: [{ kind: 'text', value: '9781861972712' }],
      choiceContexts: [
        {
          alternativeId: null,
          evidence: [
            {
              language: null,
              observation: 'Your current value.',
              role: 'local_presence',
              source: 'Local library record',
            },
            {
              language: null,
              observation: 'Printed on the copyright page.',
              role: 'external_observation',
              source: 'Open Library',
            },
          ],
          limitations: [],
          rationale: '',
          recommendation: 'recommended',
          recommendationReason: 'Your settings keep the current value.',
          selection: 'current',
        },
      ],
    })
  const keepCardShown = () => screen.queryByText(/^(Keep current value|Before your change)$/u)
  const show = async (field: Field) => {
    const http = owner(field)
    renderRoute(`/library?lens=all&preview=case-1&field=${field.destination}`, http)
    await screen.findByRole('radio', { name: /^Choose proposed value/u })
    return http
  }

  it('still reads Keep current value on a field the case has not written', async () => {
    await show({ ...isbnOver('9780306406157'), writtenByCase: false })
    expect(screen.getByText('Keep current value')).toBeVisible()
    expect(screen.getByRole('radio', { name: /^Choose current value/u })).toBeVisible()
  })

  it('offers the value before the change on a written ISBN, and writes it back', async () => {
    const user = userEvent.setup()
    const http = await show(isbnOver('9780306406157'))
    const card = screen.getByText('Before your change').closest('.current-choice-list')
    if (!(card instanceof HTMLElement)) throw new Error('the before-your-change card is required')
    expect(card).toHaveTextContent('9780306406157')
    await user.click(
      within(card).getByRole('button', {
        name: 'Evidence for value before your change: 9780306406157',
      }),
    )
    // Its evidence keeps the sources and drops the record that calls it current.
    expect(card).toHaveTextContent('Printed on the copyright page.')
    expect(card).not.toHaveTextContent(/current|Already in the library/iu)
    expect(screen.queryByText('Keep current value')).toBeNull()
    expect(screen.queryByRole('radio', { name: /current value/u })).toBeNull()
    await user.click(
      screen.getByRole('radio', {
        name: 'Choose the value before your change: 9780306406157',
      }),
    )
    expect(sent(http)[0]).toMatchObject({ outcome: 'keep_current', destination: 'isbn' })
  })

  it('offers it where owner entry is closed, even on a clearable field', async () => {
    await show(
      offered('subtitle', {
        currentValue: 'Not set in Audiobookshelf',
        currentRaw: null,
        proposedValues: [{ kind: 'text', value: 'A Quiet Subtitle' }],
      }),
    )
    const card = screen.getByText('Before your change').closest('.current-choice-list')
    expect(card).toHaveTextContent('Not set')
    expect(card).not.toHaveTextContent('Audiobookshelf')
  })

  it('leaves it out where Clear goes back to the empty value', async () => {
    const user = userEvent.setup()
    await show(
      offered('subtitle', {
        currentValue: 'Not set in Audiobookshelf',
        currentRaw: null,
        proposedValues: [{ kind: 'text', value: 'A Quiet Subtitle' }],
        ownerControl: textControl('A Quiet Subtitle', true),
      }),
    )
    expect(keepCardShown()).toBeNull()
    const panel = screen.getByRole('region', { name: 'Your value for Subtitle' })
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.clear(within(panel).getByRole('textbox', { name: 'Your value for Subtitle' }))
    expect(within(panel).getByRole('button', { name: 'Clear subtitle' })).toBeVisible()
  })

  it.each([
    [
      'subtitle',
      'Original subtitle',
      control({
        entry: {
          maxCharacters: 512,
          suggestions: [{ rule: 'previous', value: 'Original subtitle' }],
        },
        clear: true,
        current: 'A Quiet Subtitle',
      }),
    ],
    ['narrators', ['Ada Reader'], namesControl(['Bo Reader'], true, ['Ada Reader'])],
    [
      'genres',
      ['Drama'],
      control({
        clear: true,
        current: ['Fantasy'],
        subset: {
          maxItems: 32,
          options: [
            { value: 'Fantasy', current: true, proposed: true, previous: false },
            { value: 'Drama', current: false, proposed: false, previous: true },
          ],
        },
      }),
    ],
  ] as const)(
    'leaves it out where %s entry offers the previous value',
    async (destination, before, ownerControl) => {
      await show(
        offered(destination, {
          currentValue: Array.isArray(before) ? before.join(' · ') : before,
          currentRaw: before,
          proposedValues: [{ kind: Array.isArray(before) ? 'list' : 'text', value: 'Proposed' }],
          ownerControl,
        } as Partial<Field>),
      )
      expect(keepCardShown()).toBeNull()
    },
  )

  it('shows it where the previous value offered is not the value before the change', async () => {
    await show(
      offered('subtitle', {
        currentValue: 'Original subtitle',
        currentRaw: 'Original subtitle',
        proposedValues: [{ kind: 'text', value: 'A Quiet Subtitle' }],
        ownerControl: textControl('A Quiet Subtitle', true),
      }),
    )
    expect(screen.getByText('Before your change')).toBeVisible()
  })

  it.each([
    ['nothing else', [{ kind: 'text', value: '9781861972712' }], false],
    [
      'another proposal',
      [
        { kind: 'text', value: '9781861972712' },
        { kind: 'text', value: '9780306406157' },
      ],
      true,
    ],
  ] as const)(
    'offers no card and says why on an ISBN written over nothing, with %s on offer',
    async (_, proposedValues, another) => {
      const written = decided(
        { ...isbnOver(null), proposedValues: [...proposedValues] },
        { kind: 'text', value: '9781861972712' },
      )
      if (!written.humanDecision) throw new Error('a recorded decision is required')
      renderRoute(
        '/library?lens=all&preview=case-1&field=isbn',
        owner({
          ...written,
          editable: true,
          needsAttention: false,
          // Withdraw alone is no other value to choose.
          ownerControl: control({ withdraw: 'written' }),
          pinControl: null,
          humanDecision: { ...written.humanDecision, outcome: 'accept' },
        }),
      )
      const heading = await screen.findByRole('heading', { name: 'ISBN' })
      const said = heading.parentElement?.querySelector('p')
      expect(said).toHaveTextContent(
        'The ISBN was empty before your change, and it can’t be emptied.',
      )
      expect(said).not.toHaveTextContent(/There was|this change/u)
      if (another)
        expect(said).toHaveTextContent(/can’t be emptied\. You can choose another value\.$/u)
      else expect(said).not.toHaveTextContent('You can choose another value.')
      expect(keepCardShown()).toBeNull()
    },
  )

  // A description the adviser's text replaced, read back after the write.
  const adviceWritten = (before: string | null): Field => {
    const authored = readCaseDetail(descriptionAdviceFixture)
    const field = authored.fields.find((item) => item.destination === 'description')
    const advice = field?.descriptionAdvice
    if (!field || !advice?.candidate) throw new Error('description fixture requires its advice')
    const [current, ...others] = field.choiceContexts
    if (!current) throw new Error('description fixture requires its current context')
    return {
      ...field,
      writtenByCase: true,
      currentValue: before ?? 'Not set in Audiobookshelf',
      currentRaw: before,
      choiceContexts: [
        {
          ...current,
          evidence: [
            ...current.evidence,
            {
              language: null,
              observation: 'Description: the same text.',
              role: 'external_corroboration',
              source: 'Hardcover',
            },
          ],
        },
        ...others,
      ],
      descriptionAdvice: {
        ...advice,
        current: false,
        application_outcome: 'verified',
        selected_advice: { candidate: advice.candidate, binding: advice.binding, current: false },
        selection: {
          decision_id: `automatic_decision_${'a'.repeat(64)}`,
          candidate_id: advice.candidate.candidate_id,
          binding: advice.binding,
          origin: 'automatic_policy',
          resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
          qualification_manifest_sha256: 'c'.repeat(64),
          idempotency_digest: 'd'.repeat(64),
          occurred_at: 10,
          destination: 'description' as const,
          outcome: 'accept',
          application: 'guarded',
        },
      },
    }
  }

  it('offers the description before the adviser’s text was written', async () => {
    const user = userEvent.setup()
    const http = owner(adviceWritten(`An earlier description. ${'It goes on. '.repeat(30)}`))
    renderRoute('/library?lens=all&preview=case-1&field=description', http)
    const advised = await screen.findByRole('radio', { name: /^Choose advised value:/u })
    expect(advised).toHaveTextContent('Applied and verified')
    const card = screen.getByText('Before your change').closest('.current-choice-list')
    if (!(card instanceof HTMLElement)) throw new Error('the before-your-change card is required')
    expect(
      within(card).getByRole('radio', { name: 'Choose the description before your change' }),
    ).toHaveAttribute('aria-checked', 'false')
    await user.click(
      within(card).getByRole('button', {
        name: 'Evidence for the description before your change',
      }),
    )
    expect(card).toHaveTextContent('Description: the same text.')
    expect(screen.queryByText('Keep current value')).toBeNull()
  })

  it('says a description read empty has no way back once the advice is written', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=description', owner(adviceWritten(null)))
    await screen.findByRole('radio', { name: /^Choose advised value:/u })
    const heading = screen.getByRole('heading', { name: 'Description' })
    expect(heading.parentElement?.querySelector('p')).toHaveTextContent(
      'The description was empty before your change, and it can’t be emptied.',
    )
    expect(keepCardShown()).toBeNull()
  })

  it('marks a recorded keep on a field back at the value the case read', async () => {
    const kept = decided({ ...isbnOver('9780306406157'), writtenByCase: false }, null)
    if (!kept.humanDecision) throw new Error('a recorded decision is required')
    renderRoute(
      '/library?lens=all&preview=case-1&field=isbn',
      owner({
        ...kept,
        editable: true,
        needsAttention: false,
        humanDecision: { ...kept.humanDecision, outcome: 'keep_current' },
      }),
    )
    const card = await screen.findByRole('radio', {
      name: /^Choose current value: 9780306406157/u,
    })
    expect(screen.getByText('Keep current value')).toBeVisible()
    expect(card).toHaveAttribute('aria-checked', 'true')
    expect(card).toHaveTextContent('Your choice')
  })

  // A field the case wrote, then took back to the value it read with an
  // owner entry or a clear: no longer written, and the entry is the outcome.
  const genresBack = (before: readonly string[] | null, entered: unknown): Field => {
    const back = decided(
      offered('genres', {
        writtenByCase: false,
        currentValue: before ? before.join(' · ') : 'Not set in Audiobookshelf',
        currentRaw: before,
        proposedValues: [{ kind: 'list', value: ['Fantasy'] }],
        ownerControl: control({
          clear: true,
          current: before,
          subset: {
            maxItems: 32,
            options: [
              ...(before ?? []).map((value) => ({
                value,
                current: true,
                proposed: false,
                previous: false,
              })),
              { value: 'Fantasy', current: false, proposed: true, previous: false },
            ],
          },
        }),
      }),
      entered,
    )
    return { ...back, editable: true, needsAttention: false }
  }

  it.each([
    ['a clear back to empty genres', null, EMPTY, 'You cleared the genres.'],
    [
      'an entry back to the genres read',
      ['Drama'],
      { kind: 'list', value: ['Drama'] },
      'You chose 1 genre.',
    ],
  ] as const)('leaves it out after %s', async (_, before, entered, said) => {
    renderRoute('/library?lens=all&preview=case-1&field=genres', owner(genresBack(before, entered)))
    await screen.findByRole('radio', { name: /^Choose proposed value/u })
    expect(keepCardShown()).toBeNull()
    expect(screen.queryByRole('radio', { name: /^Choose current value/u })).toBeNull()
    expect(screen.getByText(said)).toBeVisible()
    // The value read has one pin, the recorded entry's, beside the proposed value's.
    const pins = screen.getAllByRole('button', { name: 'Pin this value' })
    expect(pins).toHaveLength(2)
    expect(pins.filter((pin) => pin.closest('.owner-recorded-pin'))).toHaveLength(1)
  })

  it('leaves it out after authors entered back as they were read', async () => {
    const back = decided(
      offered('authors', {
        writtenByCase: false,
        currentValue: 'Ada Example · Bo Example',
        currentRaw: ['Ada Example', 'Bo Example'],
        proposedValues: [{ kind: 'list', value: ['Mick Herron'] }],
        ownerControl: namesControl(['Ada Example', 'Bo Example'], false),
      }),
      { kind: 'list', value: ['Ada Example', 'Bo Example'] },
    )
    renderRoute(
      '/library?lens=all&preview=case-1&field=authors',
      owner({ ...back, editable: true, needsAttention: false }),
    )
    await screen.findByRole('radio', { name: /^Choose proposed value/u })
    expect(keepCardShown()).toBeNull()
  })

  it('still offers the way back after genres entered back in another order', async () => {
    // The store compares in order: Drama · Mystery is not the Mystery · Drama it read.
    renderRoute(
      '/library?lens=all&preview=case-1&field=genres',
      owner({
        ...genresBack(['Mystery', 'Drama'], { kind: 'list', value: ['Drama', 'Mystery'] }),
        writtenByCase: true,
      }),
    )
    await screen.findByRole('radio', { name: /^Choose proposed value/u })
    expect(screen.getByText('Before your change')).toBeVisible()
    expect(
      screen.getByRole('radio', { name: 'Choose the value before your change: Mystery · Drama' }),
    ).toHaveAttribute('aria-checked', 'false')
  })

  it('leaves it out after a subtitle entered back as it was read', async () => {
    const back = decided(
      offered('subtitle', {
        writtenByCase: false,
        currentValue: 'Original subtitle',
        currentRaw: 'Original subtitle',
        proposedValues: [{ kind: 'text', value: 'A Quiet Subtitle' }],
        ownerControl: textControl('Original subtitle', true),
      }),
      { kind: 'text', value: 'Original subtitle' },
    )
    renderRoute(
      '/library?lens=all&preview=case-1&field=subtitle',
      owner({ ...back, editable: true, needsAttention: false }),
    )
    await screen.findByRole('radio', { name: /^Choose proposed value/u })
    expect(keepCardShown()).toBeNull()
  })

  it('still offers it beside an entry of another value', async () => {
    renderRoute(
      '/library?lens=all&preview=case-1&field=genres',
      owner(genresBack(['Drama'], { kind: 'list', value: ['Fantasy'] })),
    )
    await screen.findByRole('radio', { name: /^Choose proposed value/u })
    expect(screen.getByText('Keep current value')).toBeVisible()
    expect(screen.getByRole('radio', { name: /^Choose current value: Drama/u })).toHaveAttribute(
      'aria-checked',
      'false',
    )
  })

  it('says it in the plural on authors written over nothing', async () => {
    const written = decided(
      offered('authors', {
        currentValue: 'Not set in Audiobookshelf',
        currentRaw: null,
        proposedValues: [{ kind: 'list', value: ['Mick Herron'] }],
      }),
      { kind: 'list', value: ['Mick Herron'] },
    )
    if (!written.humanDecision) throw new Error('a recorded decision is required')
    renderRoute(
      '/library?lens=all&preview=case-1&field=authors',
      owner({
        ...written,
        editable: true,
        needsAttention: false,
        ownerControl: control({ withdraw: 'written' }),
        pinControl: null,
        humanDecision: { ...written.humanDecision, outcome: 'accept' },
      }),
    )
    const heading = await screen.findByRole('heading', { name: 'Authors' })
    expect(heading.parentElement?.querySelector('p')).toHaveTextContent(
      'The authors were empty before your change, and they can’t be emptied.',
    )
  })
})

describe('an emptied form that clears nothing (#1523)', () => {
  it('asks for a name on empty narrators', async () => {
    const user = userEvent.setup()
    const http = owner(field('narrators', [], namesControl([], true)))
    const panel = await open(http, 'narrators')
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(/^Enter at least one name\.$/u)
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('asks for a genre on empty genres', async () => {
    const user = userEvent.setup()
    const http = owner(
      field(
        'genres',
        [],
        control({
          clear: true,
          current: [],
          subset: {
            maxItems: 32,
            options: [{ value: 'Drama', current: false, proposed: true, previous: false }],
          },
        }),
      ),
    )
    const panel = await open(http, 'genres')
    await user.click(within(panel).getByRole('button', { name: 'Choose genres' }))
    await user.click(within(panel).getByRole('button', { name: 'Record these genres' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(/^Choose at least one genre\.$/u)
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('points a pending value on an empty field at Withdraw', async () => {
    const user = userEvent.setup()
    const pending = decided(field('subtitle', null, textControl(null, true)), {
      kind: 'text',
      value: 'Foo bar',
    })
    const http = owner(pending)
    const panel = await open(http, 'subtitle')
    expect(within(panel).getByText('You entered “Foo bar”.')).toBeVisible()
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.clear(within(panel).getByRole('textbox', { name: 'Your value for Subtitle' }))
    expect(within(panel).queryByRole('button', { name: /^Clear/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      'This field is already empty. Use Withdraw to remove your value.',
    )
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('offers no second clear after a pending clear and records nothing', async () => {
    const user = userEvent.setup()
    const pending = decided(
      field('subtitle', 'A Quiet Subtitle', textControl('A Quiet Subtitle', true)),
      EMPTY,
    )
    const http = owner(pending)
    const panel = await open(http, 'subtitle')
    expect(within(panel).getByText('You cleared the subtitle.')).toBeVisible()
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Subtitle' })
    expect(input).toHaveValue('')
    expect(within(panel).queryByRole('button', { name: 'Clear subtitle' })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      /^You already cleared the subtitle\.$/u,
    )
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('refuses the standing value again rather than recording a duplicate', async () => {
    const user = userEvent.setup()
    const pending = decided(field('subtitle', null, textControl(null, true)), {
      kind: 'text',
      value: 'Foo bar',
    })
    const http = owner(pending)
    const panel = await open(http, 'subtitle')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    expect(within(panel).getByRole('textbox', { name: 'Your value for Subtitle' })).toHaveValue(
      'Foo bar',
    )
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      /^You already entered this value\.$/u,
    )
    expect(http.recordDecision).not.toHaveBeenCalled()
  })
})

describe('re-entering your own pending value (#1523)', () => {
  it('says you already entered these names', async () => {
    const user = userEvent.setup()
    const pending = decided(
      field('narrators', ['Ada Reader'], namesControl(['Ada Reader'], true)),
      {
        kind: 'list',
        value: ['Bo Reader'],
      },
    )
    const http = owner(pending)
    const panel = await open(http, 'narrators')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    expect(within(panel).getByRole('textbox', { name: 'Your value for Narrators' })).toHaveValue(
      'Bo Reader',
    )
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      /^You already entered these names\.$/u,
    )
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('says you already chose these genres', async () => {
    const user = userEvent.setup()
    const pending = decided(
      field(
        'genres',
        ['Drama'],
        control({
          clear: true,
          current: ['Drama'],
          subset: {
            maxItems: 32,
            options: [
              { value: 'Drama', current: true, proposed: false, previous: false },
              { value: 'Fantasy', current: false, proposed: true, previous: false },
            ],
          },
        }),
      ),
      { kind: 'list', value: ['Fantasy'] },
    )
    const http = owner(pending)
    const panel = await open(http, 'genres')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.click(within(panel).getByRole('button', { name: 'Record these genres' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      /^You already chose these genres\.$/u,
    )
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('still says the current value where the library holds it', async () => {
    const user = userEvent.setup()
    const http = owner(field('subtitle', 'A Quiet Subtitle', textControl('A Quiet Subtitle', true)))
    const panel = await open(http, 'subtitle')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      /^That is already the current value\.$/u,
    )
  })
})

describe('names that differ only by case or composition (#1523)', () => {
  it.each([
    ['Ada Example\nada EXAMPLE'],
    ['Zoé Example\nZoé Example'],
    ['Anna Straße\nANNA STRASSE'],
    ['Anna Straße\nANNA STRAẞE'],
  ])('refuses %j as a repeated name', async (text) => {
    const user = userEvent.setup()
    const http = owner(field('narrators', ['Ada Reader'], namesControl(['Ada Reader'], true)))
    const panel = await open(http, 'narrators')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Narrators' })
    await user.clear(names)
    await user.click(names)
    await user.paste(text)
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter each name once.')
    expect(http.recordDecision).not.toHaveBeenCalled()
  })
})

describe('names the store keeps apart (#1523)', () => {
  it('records a dotless ı and a dotted i as two names', async () => {
    const user = userEvent.setup()
    const http = owner(field('narrators', ['Ada Reader'], namesControl(['Ada Reader'], true)))
    const panel = await open(http, 'narrators')
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    const names = within(panel).getByRole('textbox', { name: 'Your value for Narrators' })
    await user.clear(names)
    await user.click(names)
    await user.paste('Nilay Yılmaz\nNilay Yilmaz')
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(await within(panel).findByText('You entered 2 names.')).toBeVisible()
    expect(http.recordDecision).toHaveBeenCalledOnce()
  })
})

describe('the trigger says Change when the form opens holding a value (#1523)', () => {
  it.each([
    ['subtitle', textControl('A Quiet Subtitle', true), 'A Quiet Subtitle', 'Change'],
    ['subtitle', textControl(null, true), null, 'Enter a value'],
    ['title', textControl('A Book', false), 'A Book', 'Enter a value'],
    ['narrators', namesControl(['Ada Reader'], true), ['Ada Reader'], 'Change'],
    ['narrators', namesControl([], true), [], 'Enter names'],
  ] as const)('%s opening with %j reads %s', async (destination, ownerControl, current, label) => {
    const http = owner(field(destination, current, ownerControl))
    const panel = await open(http, destination)
    expect(within(panel).getByRole('button', { name: label })).toBeVisible()
  })
})

describe('History of entered names (#1523)', () => {
  it('counts the names entered', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [
        {
          category: 'owner' as const,
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'narrators' as const,
          outcome: 'owner_entered' as const,
          previousValue: { kind: 'list', value: ['Ada Reader'] },
          selectedValue: { kind: 'list', value: ['Ada Reader', 'Bo Reader'] },
        },
        {
          category: 'owner' as const,
          occurredAt: 1785000000,
          applicationReason: null,
          destination: 'subtitle' as const,
          outcome: 'owner_entered' as const,
          previousValue: null,
          selectedValue: { kind: 'text', value: 'A Quiet Subtitle' },
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
    const names = (await screen.findByRole('heading', { name: 'Narrators' })).closest('article')
    expect(names).toHaveTextContent('You entered 2 names.')
    const text = screen.getByRole('heading', { name: 'Subtitle' }).closest('article')
    expect(text).toHaveTextContent('You entered a value.')
  })
})

describe('a recorded clear on a field with a new choice (#1523)', () => {
  it.each([
    [EMPTY, /You cleared the subtitle\.$/u],
    [
      { kind: 'text', value: 'A Quiet Subtitle' },
      /The recorded value remains A Quiet Subtitle\.$/u,
    ],
  ])('says %j naturally', (selected, sentence) => {
    const recorded = decided(
      field('subtitle', 'A Quiet Subtitle', textControl(null, true)),
      selected,
    )
    render(
      <SettledFieldContext
        field={recorded}
        actionable
        ownerEntry={false}
        expanded={false}
        toggle={() => undefined}
      />,
    )
    const line = document.querySelector('.decision-recorded')
    expect(line).toHaveTextContent(sentence)
    expect(line).not.toHaveTextContent('remains Cleared')
  })
})
