import { screen, waitFor, within } from '@testing-library/react'
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
import { createHttp, detail, library, recordedAcknowledgement, renderRoute } from './fixtures'

// Clearing a field and entering names in the browser (#1523, ADR-0104): an
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
      await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Choose genres' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
    await user.clear(within(panel).getByRole('textbox', { name: 'Your value for Authors' }))
    expect(within(panel).queryByRole('button', { name: /^Clear/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record these names' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter at least one author.')
    expect(authors.recordDecision).not.toHaveBeenCalled()
  })

  it('says an empty clearable field is already empty and sends nothing', async () => {
    const user = userEvent.setup()
    const http = owner(field('subtitle', null, textControl(null, true)))
    const panel = await open(http, 'subtitle')
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    expect(within(panel).queryByRole('button', { name: /^Clear/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent('This field is already empty.')
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
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
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
    await user.click(within(panel).getByRole('button', { name: 'Enter names' }))
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
