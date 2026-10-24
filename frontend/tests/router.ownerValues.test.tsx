import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type { CaseDetailView, DecisionCommand, OwnerControlView, ReviewHttp } from '../src/api/http'
import { createHttp, detail, library, recordedAcknowledgement, renderRoute } from './fixtures'

// F59 owner controls on the decision canvas (#1285, ADR-0085): V1-V7.
type Field = CaseDetailView['fields'][number]
const base = detail.fields[0]
if (!base) throw new Error('workspace fixture requires a title field')
const control = (value: Partial<OwnerControlView>): OwnerControlView => ({
  entry: null,
  subset: null,
  withdraw: null,
  ...value,
})
const entry = control({
  entry: {
    maxCharacters: 512,
    suggestions: [
      { rule: 'title_case', value: 'The Silent Orchard (Disc 2)' },
      { rule: 'strip_part', value: 'THE SILENT ORCHARD' },
      { rule: 'combined', value: 'The Silent Orchard' },
    ],
  },
})
// A title the provider agrees with: nothing to choose, but a value to enter.
const contextTitle: Field = {
  ...base,
  classification: 'unsupported',
  classificationReason: 'There is no other value to choose from. You can enter your own.',
  decisionActions: [],
  editable: false,
  needsAttention: false,
  navigationRole: 'inspect',
  currentValue: 'THE SILENT ORCHARD (Disc 2)',
  currentRaw: 'THE SILENT ORCHARD (Disc 2)',
  proposedValues: [{ kind: 'text', value: 'THE SILENT ORCHARD (Disc 2)' }],
  ownerControl: entry,
}
const decision = (
  outcome: string,
  selected: { kind: string; value: unknown } | null,
): NonNullable<Field['humanDecision']> => ({
  actor: 'owner',
  application: 'apply',
  decision_id: `decision-${outcome}`,
  destination: 'title',
  occurred_at: 1,
  outcome,
  proposal_id: base.proposalId,
  review_case_id: 'case-1',
  selected_value: selected as never,
  target_fingerprint: base.targetFingerprint,
})
const enteredTitle: Field = {
  ...contextTitle,
  classification: 'human_recorded',
  classificationReason: 'Your recorded decision is retained. You can change it here.',
  editable: true,
  humanDecision: decision('owner_entered', { kind: 'text', value: 'The Silent Orchard' }),
  ownerControl: control({ ...entry, withdraw: 'available' }),
}
const quietLibrary = {
  ...library,
  counts: { all: 1, attention: 0, clear: 1 },
  works: library.works.map((work) => ({ ...work, attentionCount: 0 })),
}

// The case as the store returns it before and after each command.
const owner = (fields: () => readonly Field[], record?: ReviewHttp['recordDecision']) => {
  const http = createHttp()
  const recordDecision = vi.fn<ReviewHttp['recordDecision']>(
    record ??
      (async (caseId, command) => {
        const acknowledgement = recordedAcknowledgement(
          { ...detail, id: caseId },
          {
            invalidation_sequences: [],
            schema_version: 'review-decision-v1',
            actor: 'owner',
            application: 'apply',
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
        // The store's own read-back: exactly the case the next read returns.
        return {
          ...acknowledgement,
          confirmation: { status: 'confirmed', case: { ...detail, fields: [...fields()] } },
        }
      }),
  )
  return {
    ...http,
    getLibrary: vi.fn(async () => quietLibrary),
    getAttention: vi.fn(async () => ({ total: 0, items: [] })),
    getCaseDetail: vi.fn(async () => ({ ...detail, fields: [...fields()] })),
    recordDecision,
  }
}
const open = async (http: ReturnType<typeof owner>) => {
  renderRoute('/library?lens=all&preview=case-1&field=title', http)
  return await screen.findByRole('region', { name: 'Your value for Title' })
}
const sent = (http: ReturnType<typeof owner>): DecisionCommand[] =>
  http.recordDecision.mock.calls.map(([, command]) => command)

describe('owner-entered values', () => {
  it('V1 offers Enter a value in place of the no-choice copy', async () => {
    const http = owner(() => [contextTitle])
    const panel = await open(http)
    expect(within(panel).getByRole('button', { name: 'Enter a value' })).toBeVisible()
    expect(screen.queryByText('There’s no other value to choose from.')).toBeNull()
    // A field the owner cannot type into keeps the existing copy.
    const plain = owner(() => [{ ...contextTitle, ownerControl: null }])
    document.body.innerHTML = ''
    renderRoute('/library?lens=all&preview=case-1&field=title', plain)
    expect(await screen.findByText('There’s no other value to choose from.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Enter a value' })).toBeNull()
  })

  it('V2 records once while busy and says a refusal beside the input', async () => {
    const user = userEvent.setup()
    let release: (() => void) | undefined
    const http = owner(
      () => [contextTitle],
      async () => {
        await new Promise<void>((resolve) => {
          release = resolve
        })
        throw new ApiError(422, 'owner_value_invalid')
      },
    )
    const panel = await open(http)
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Title' })
    expect(input).toHaveFocus()
    const count = panel.querySelector('.owner-count')
    expect(count).not.toBeNull()
    expect(count).toHaveAttribute('aria-live', 'polite')
    expect(count).toHaveTextContent('')
    const record = within(panel).getByRole('button', { name: 'Record this value' })
    await user.click(record)
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter a value first.')
    expect(http.recordDecision).not.toHaveBeenCalled()
    await user.type(input, '  The   Silent Orchard ')
    expect(count).toHaveTextContent('of 512 characters')
    await user.click(record)
    await user.click(within(panel).getByRole('button', { name: 'Recording…' }))
    expect(http.recordDecision).toHaveBeenCalledTimes(1)
    expect(sent(http)[0]).toMatchObject({
      outcome: 'owner_entered',
      selectedValue: { kind: 'text', value: 'The Silent Orchard' },
    })
    expect(within(panel).getByRole('button', { name: 'Recording…' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    release?.()
    await waitFor(() =>
      expect(within(panel).getByRole('alert')).toHaveTextContent(
        'This value can’t be recorded. Use plain text of up to 512 characters, with no links or file paths.',
      ),
    )
    expect(within(panel).getByRole('button', { name: 'Record this value' })).toHaveFocus()
  })

  it('V3 fills the input from a suggestion without recording', async () => {
    const user = userEvent.setup()
    const http = owner(() => [contextTitle])
    const panel = await open(http)
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    await user.click(within(panel).getByRole('button', { name: 'Use “The Silent Orchard”' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Title' })
    expect(input).toHaveValue('The Silent Orchard')
    expect(input).toHaveFocus()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('V3 moves focus to the recorded value after a recorded entry', async () => {
    const user = userEvent.setup()
    let fields: readonly Field[] = [contextTitle]
    const http = owner(() => fields)
    const panel = await open(http)
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    await user.click(within(panel).getByRole('button', { name: 'Use “The Silent Orchard”' }))
    fields = [enteredTitle]
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    const recorded = await screen.findByText('You entered “The Silent Orchard”.')
    await waitFor(() => expect(recorded).toHaveFocus())
    expect(screen.getByText('Your value is recorded.')).toBeVisible()
  })

  it('V4 submits a genre subset as raw values in union order', async () => {
    const user = userEvent.setup()
    const genres: Field = {
      ...contextTitle,
      destination: 'genres',
      currentValue: 'Drama',
      currentRaw: ['Drama'],
      proposedValues: [{ kind: 'list', value: ['Fantasy', 'Mystery'] }],
      proposalId: 'proposal-genres',
      targetFingerprint: 'fingerprint-genres',
      ownerControl: control({
        subset: {
          maxItems: 32,
          options: [
            { value: 'Drama', current: true, proposed: false, previous: false },
            { value: 'Fantasy', current: false, proposed: true, previous: false },
            { value: 'Mystery', current: false, proposed: true, previous: false },
          ],
        },
      }),
    }
    const http = owner(() => [genres])
    renderRoute('/library?lens=all&preview=case-1&field=genres', http)
    const panel = await screen.findByRole('region', { name: 'Your value for Genres' })
    await user.click(within(panel).getByRole('button', { name: 'Choose genres' }))
    const group = within(panel).getByRole('group', { name: 'Choose genres' })
    const boxes = within(group).getAllByRole('checkbox')
    expect(boxes.map((box) => box.closest('label')?.textContent)).toEqual([
      'DramaCurrent',
      'FantasyProposed',
      'MysteryProposed',
    ])
    expect(boxes[0]).toBeChecked()
    await user.click(within(panel).getByRole('button', { name: 'Record these genres' }))
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      'Those are already the current genres.',
    )
    await user.click(within(group).getByRole('checkbox', { name: /Mystery/u }))
    await user.click(within(group).getByRole('checkbox', { name: /Drama/u }))
    await user.click(within(group).getByRole('checkbox', { name: /Fantasy/u }))
    await user.click(within(panel).getByRole('button', { name: 'Record these genres' }))
    expect(sent(http)).toEqual([
      expect.objectContaining({
        destination: 'genres',
        outcome: 'owner_entered',
        selectedValue: { kind: 'list', value: ['Fantasy', 'Mystery'] },
      }),
    ])
  })

  it('V5 offers Change on a recorded value with no alternatives, prefilled', async () => {
    const user = userEvent.setup()
    const http = owner(() => [enteredTitle])
    const panel = await open(http)
    expect(within(panel).getByText('You entered “The Silent Orchard”.')).toBeVisible()
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    expect(within(panel).getByRole('textbox', { name: 'Your value for Title' })).toHaveValue(
      'The Silent Orchard',
    )
    await user.click(within(panel).getByRole('button', { name: 'Cancel' }))
    expect(within(panel).getByRole('button', { name: 'Change' })).toHaveFocus()
    // While its write is in flight the decision says so instead of offering Change.
    const writing = owner(() => [
      { ...enteredTitle, ownerControl: control({ ...entry, withdraw: 'in_progress' }) },
    ])
    document.body.innerHTML = ''
    const busy = await open(writing)
    expect(within(busy).queryByRole('button', { name: 'Change' })).toBeNull()
    expect(
      within(busy).getByText(
        'This decision is being written to your library. You can change or withdraw it once the write finishes.',
      ),
    ).toBeVisible()
  })

  it('V6 withdraws, announces it and moves focus to the field heading', async () => {
    const user = userEvent.setup()
    let fields: readonly Field[] = [enteredTitle]
    const http = owner(() => fields)
    const panel = await open(http)
    fields = [{ ...contextTitle, needsAttention: false }]
    await user.click(within(panel).getByRole('button', { name: 'Withdraw' }))
    expect(sent(http)[0]).toMatchObject({ outcome: 'withdraw', selectedValue: null })
    expect(await screen.findByText('Withdrawn. This field needs a decision again.')).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 2, name: 'Title' })).toHaveFocus(),
    )
  })

  it.each([
    [
      'automatic_hold',
      'This decision is holding automatic handling, so it can’t be withdrawn. Use Resume automatic handling to release it.',
    ],
    ['written', 'This decision has been written to your library.'],
  ] as const)('V6 states why %s withdraw is not offered', async (state, copy) => {
    const http = owner(() => [{ ...enteredTitle, ownerControl: control({ withdraw: state }) }])
    const panel = await open(http)
    expect(within(panel).queryByRole('button', { name: 'Withdraw' })).toBeNull()
    expect(within(panel).getByText(copy)).toBeVisible()
  })
})

describe('choosing the previous value again', () => {
  // After a write, the value it replaced is one more suggestion; going back is
  // an ordinary entry under the same guards (ADR-0085).
  const written: Field = {
    ...contextTitle,
    currentValue: 'The Silent Orchard',
    currentRaw: 'The Silent Orchard',
    ownerControl: control({
      entry: {
        maxCharacters: 512,
        suggestions: [{ rule: 'previous', value: 'THE SILENT ORCHARD (Disc 2)' }],
      },
    }),
  }

  it('V7 offers the previous value as a suggestion that only fills the input', async () => {
    const user = userEvent.setup()
    const http = owner(() => [written])
    const panel = await open(http)
    expect(within(panel).queryByRole('button', { name: /Restore/u })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    await user.click(
      within(panel).getByRole('button', {
        name: 'Use previous value “THE SILENT ORCHARD (Disc 2)”',
      }),
    )
    const input = within(panel).getByRole('textbox', { name: 'Your value for Title' })
    expect(input).toHaveValue('THE SILENT ORCHARD (Disc 2)')
    expect(http.recordDecision).not.toHaveBeenCalled()
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(sent(http)).toEqual([
      expect.objectContaining({
        outcome: 'owner_entered',
        selectedValue: { kind: 'text', value: 'THE SILENT ORCHARD (Disc 2)' },
      }),
    ])
    expect(sent(http)[0]).not.toHaveProperty('revertsPlanId')
  })

  it('V7 tags a previous genre beside Current and Proposed', async () => {
    const user = userEvent.setup()
    const genres: Field = {
      ...contextTitle,
      destination: 'genres',
      currentValue: 'Fantasy',
      currentRaw: ['Fantasy'],
      proposedValues: [],
      ownerControl: control({
        subset: {
          maxItems: 32,
          options: [
            { value: 'Fantasy', current: true, proposed: false, previous: false },
            { value: 'Drama', current: false, proposed: false, previous: true },
          ],
        },
      }),
    }
    const http = owner(() => [genres])
    renderRoute('/library?lens=all&preview=case-1&field=genres', http)
    const panel = await screen.findByRole('region', { name: 'Your value for Genres' })
    await user.click(within(panel).getByRole('button', { name: 'Choose genres' }))
    const group = within(panel).getByRole('group', { name: 'Choose genres' })
    expect(
      within(group)
        .getAllByRole('checkbox')
        .map((box) => box.closest('label')?.textContent),
    ).toEqual(['FantasyCurrent', 'DramaPrevious'])
    await user.click(within(group).getByRole('checkbox', { name: /Drama/u }))
    await user.click(within(group).getByRole('checkbox', { name: /Fantasy/u }))
    await user.click(within(panel).getByRole('button', { name: 'Record these genres' }))
    expect(sent(http)[0]).toMatchObject({
      outcome: 'owner_entered',
      selectedValue: { kind: 'list', value: ['Drama'] },
    })
  })
})

describe('owner command labels', () => {
  it('V8 names each new owner outcome in the case history', async () => {
    const item = (destination: string, outcome: string) => ({
      category: 'owner' as const,
      occurredAt: 1786000000,
      applicationReason: null,
      destination,
      outcome,
      previousValue: null,
      selectedValue: null,
    })
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [item('title', 'owner_entered'), item('subtitle', 'withdraw')],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
    const article = async (name: string) =>
      (await screen.findByRole('heading', { name })).closest('article')
    expect(await article('Title')).toHaveTextContent('Owner entered a value.')
    expect(await article('Subtitle')).toHaveTextContent('Owner withdrew the decision.')
  })
})
