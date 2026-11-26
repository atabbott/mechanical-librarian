import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { ApiError, displayValue } from '../src/api/http'
import type {
  CaseDetailView,
  DecisionCommand,
  DecisionOutcome,
  OwnerControlView,
  ReviewHttp,
  TargetDestination,
} from '../src/api/http'
import { decisionQuestion } from '../src/fieldDecision'
import { coalesceMilliseconds } from '../src/liveInvalidation'
import {
  createHttp,
  createLiveStream,
  detail,
  library,
  recordedAcknowledgement,
  renderRoute,
  loadRouteModules,
} from './fixtures'

beforeAll(loadRouteModules)

// F59 owner controls on the decision canvas (#1285, ADR-0085): V1-V7.
type Field = CaseDetailView['fields'][number]
const base = detail.fields[0]
if (!base) throw new Error('workspace fixture requires a title field')
const control = (value: Partial<OwnerControlView>): OwnerControlView => ({
  entry: null,
  subset: null,
  names: null,
  clear: false,
  current: null,
  withdraw: null,
  ...value,
})
const entry = control({
  current: 'THE SILENT ORCHARD (Disc 2)',
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
  classificationCause: 'nothing_to_decide',
  classificationReason: 'No source offered a different value. You can enter your own.',
  decisionActions: [],
  editable: false,
  needsAttention: false,
  navigationRole: 'inspect',
  currentValue: 'THE SILENT ORCHARD (Disc 2)',
  currentRaw: 'THE SILENT ORCHARD (Disc 2)',
  proposedValues: [{ kind: 'text', value: 'THE SILENT ORCHARD (Disc 2)' }],
  // The proposed value is the current one, as the server reads it.
  preservationOnly: true,
  ownerControl: entry,
}
const decision = (
  outcome: DecisionOutcome,
  selected: { kind: string; value: unknown } | null,
): NonNullable<Field['humanDecision']> => ({
  actor: 'owner',
  application: 'apply',
  application_reason: null,
  decision_id: `decision-${outcome}`,
  destination: 'title' as const,
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
  classificationCause: 'human_decided',
  classificationReason: 'You decided this field. You can change it here.',
  editable: true,
  humanDecision: decision('owner_entered', { kind: 'text', value: 'The Silent Orchard' }),
  ownerControl: control({ ...entry, withdraw: 'available' }),
}
const quietLibrary = {
  ...library,
  counts: { all: 1, attention: 0, clear: 1, notMatched: 0, failed: 0, flagged: 0, new: 0 },
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
    expect(
      screen.queryByText('No source offered a different value.', { selector: '.decision-context' }),
    ).toBeNull()
    // A field the owner cannot type into keeps the existing copy.
    const plain = owner(() => [{ ...contextTitle, ownerControl: null }])
    document.body.innerHTML = ''
    renderRoute('/library?lens=all&preview=case-1&field=title', plain)
    expect(
      await screen.findByText('No source offered a different value.', {
        selector: '.decision-context',
      }),
    ).toBeVisible()
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
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter a title.')
    expect(http.recordDecision).not.toHaveBeenCalled()
    await user.type(input, '  The   Silent Orchard ')
    // The count is said only once the value is too long (#1523).
    expect(count).toHaveTextContent('')
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
    // Closing the form leaves the refusal behind it.
    await user.keyboard('{Escape}')
    expect(within(panel).getByRole('alert')).toHaveTextContent('')
    expect(within(panel).getByRole('status')).toHaveTextContent('')
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
      destination: 'genres' as const,
      currentValue: 'Drama',
      currentRaw: ['Drama'],
      proposedValues: [{ kind: 'list', value: ['Fantasy', 'Mystery'] }],
      proposalId: 'proposal-genres',
      targetFingerprint: 'fingerprint-genres',
      ownerControl: control({
        current: ['Drama'],
        clear: true,
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
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
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
        destination: 'genres' as const,
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
        'This decision is being written to your library. You can change it once the write finishes.',
      ),
    ).toBeVisible()
  })

  it('V6 withdraws, announces it and moves focus to its outcome line', async () => {
    const user = userEvent.setup()
    let fields: readonly Field[] = [enteredTitle]
    const http = owner(() => fields)
    const panel = await open(http)
    fields = [{ ...contextTitle, needsAttention: false }]
    await user.click(within(panel).getByRole('button', { name: 'Withdraw' }))
    expect(sent(http)[0]).toMatchObject({ outcome: 'withdraw', selectedValue: null })
    const outcome = await screen.findByText('Withdrawn.')
    expect(outcome).toBeVisible()
    // It stands where Withdraw was, as a recorded value does (#1365).
    await waitFor(() => expect(outcome).toHaveFocus())
  })

  it('V6 says a withdrawn field needs a decision only when it asks again', async () => {
    const user = userEvent.setup()
    let fields: readonly Field[] = [enteredTitle]
    const http = owner(() => fields)
    const panel = await open(http)
    // Back in Needs you, as an Ask me field is once its answer is withdrawn.
    fields = [{ ...contextTitle, needsAttention: true }]
    await user.click(within(panel).getByRole('button', { name: 'Withdraw' }))
    const outcome = await screen.findByText('Withdrawn. This field needs a decision again.')
    await waitFor(() => expect(outcome).toHaveFocus())
  })

  it('V6 holds the detail column through a withdrawal and lets go on the next field (#1365)', async () => {
    const user = userEvent.setup()
    const subtitle: Field = { ...contextTitle, destination: 'subtitle', ownerControl: null }
    let fields: readonly Field[] = [enteredTitle, subtitle]
    const http = owner(() => fields)
    const panel = await open(http)
    const column = panel.closest<HTMLElement>('.workspace-detail')
    expect(column?.style.minHeight).toBe('')
    fields = [{ ...contextTitle, needsAttention: false }, subtitle]
    await user.click(within(panel).getByRole('button', { name: 'Withdraw' }))
    await waitFor(() => expect(screen.getByText('Withdrawn.')).toHaveFocus())
    // The column keeps the height it had when Withdraw was pressed.
    expect(column?.style.minHeight).toMatch(/^\d+(\.\d+)?px$/u)
    const rail = screen.getByRole('navigation', { name: 'Review fields' })
    await user.click(within(rail).getByRole('button', { name: /^Subtitle/u }))
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Your value for Title' })).toBeNull(),
    )
    expect(column?.style.minHeight).toBe('')
  })

  it.each(['withdraw', 'record'] as const)(
    'V10 keeps focus off the page when the reads after a %s fail (#1288)',
    async (command) => {
      const user = userEvent.setup()
      let fields: readonly Field[] = [command === 'withdraw' ? enteredTitle : contextTitle]
      let failing = false
      const http = owner(() => fields)
      // Audiobookshelf becomes unreachable: the command records, and every
      // read the change notice prompts after it fails.
      const unavailable = () => Promise.reject(new ApiError(503, 'unavailable'))
      http.getCaseDetail = vi.fn(async () =>
        failing ? await unavailable() : { ...detail, fields: [...fields] },
      )
      http.getLibrary = vi.fn(async () => (failing ? await unavailable() : quietLibrary))
      const { queryClient } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
      const panel = await screen.findByRole('region', { name: 'Your value for Title' })
      if (command === 'record') {
        await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
        await user.click(within(panel).getByRole('button', { name: 'Use “The Silent Orchard”' }))
      }
      fields = [command === 'withdraw' ? contextTitle : enteredTitle]
      await user.click(
        within(panel).getByRole('button', {
          name: command === 'withdraw' ? 'Withdraw' : 'Record this value',
        }),
      )
      const outcome = await screen.findByText(
        command === 'withdraw' ? 'Withdrawn.' : 'You entered “The Silent Orchard”.',
      )
      await waitFor(() => expect(outcome).toHaveFocus())
      failing = true
      await act(async () => {
        await queryClient.invalidateQueries()
      })
      // The canvas gives way to the read failure; its Try again takes focus.
      const retry = await screen.findByRole('button', { name: 'Try again' })
      await waitFor(() => expect(retry).toHaveFocus())
      expect(sent(http)).toHaveLength(1)
    },
  )

  it('V10 keeps focus off the page when an unreachable accept is followed by failing reads (#1288)', async () => {
    const user = userEvent.setup()
    let failing = false
    const unavailable = () => Promise.reject(new ApiError(503, 'unavailable'))
    const http = createHttp()
    http.recordDecision = vi.fn<ReviewHttp['recordDecision']>(unavailable)
    http.getCaseDetail = vi.fn(async () => (failing ? await unavailable() : detail))
    http.getLibrary = vi.fn(async () => (failing ? await unavailable() : library))
    const { queryClient } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const choice = await screen.findByRole('radio', { name: 'Choose proposed value: New title' })
    await user.click(choice)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    failing = true
    await act(async () => {
      await queryClient.invalidateQueries()
    })
    const retry = await screen.findByRole('button', { name: 'Try again' })
    await waitFor(() => expect(retry).toHaveFocus())
  })

  it.each(['entered', 'withdrawn'] as const)(
    'V9 keeps the work in view when the %s outcome takes it off the Library result',
    async (outcome) => {
      const user = userEvent.setup()
      let fields: readonly Field[] = outcome === 'entered' ? [contextTitle] : [enteredTitle]
      let listed = true
      const http = owner(() => fields)
      http.getLibrary = vi.fn(async () => (listed ? quietLibrary : { ...quietLibrary, works: [] }))
      const panel = await open(http)
      listed = false
      if (outcome === 'entered') {
        await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
        await user.click(within(panel).getByRole('button', { name: 'Use “The Silent Orchard”' }))
        fields = [enteredTitle]
        await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
        const recorded = await screen.findByText('You entered “The Silent Orchard”.')
        await waitFor(() => expect(recorded).toHaveFocus())
      } else {
        fields = [{ ...contextTitle, needsAttention: false }]
        await user.click(within(panel).getByRole('button', { name: 'Withdraw' }))
        expect(await screen.findByText('Withdrawn.')).toBeVisible()
      }
      // The Library result read after the command no longer lists the work;
      // its case read still names it (#1365).
      await waitFor(() => expect(screen.queryByRole('button', { name: /^A Book\./u })).toBeNull())
      expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
      expect(screen.getByRole('region', { name: 'Your value for Title' })).toBeVisible()
      expect(screen.queryByText(/not in the current list of works/u)).toBeNull()
      if (outcome === 'withdrawn')
        await waitFor(() => expect(screen.getByText('Withdrawn.')).toHaveFocus())
    },
  )

  it.each([
    ['written', 'This decision has been written to your library, so it can’t be withdrawn.'],
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
      current: 'The Silent Orchard',
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
      destination: 'genres' as const,
      currentValue: 'Fantasy',
      currentRaw: ['Fantasy'],
      proposedValues: [],
      ownerControl: control({
        current: ['Fantasy'],
        clear: true,
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
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
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
    const item = (destination: TargetDestination, outcome: DecisionOutcome) => ({
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
    expect(await article('Title')).toHaveTextContent('You entered a value.')
    expect(await article('Subtitle')).toHaveTextContent('You withdrew your choice.')
  })
})

describe('owner review fixes', () => {
  const heading = () => screen.getByRole('heading', { level: 3, name: 'Title' })

  it('M1 labels an entered value as the owner’s own', async () => {
    const http = owner(() => [{ ...enteredTitle, editable: false, ownerControl: null }])
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const recorded = await screen.findByRole('region', { name: 'Title recorded outcome' })
    expect(within(recorded).getByText('Your value')).toBeVisible()
    expect(within(recorded).queryByText('Current value')).toBeNull()
  })

  it('M3 lets a carried hold, not the automatic decision, speak for the field', async () => {
    const held: Field = {
      ...base,
      needsAttention: false,
      classification: 'automatic_recorded',
      classificationCause: 'automatic_decided',
      manualHold: true,
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-1',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'keep_current',
        proposal_id: base.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: base.targetFingerprint,
      },
    }
    renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      owner(() => [held]),
    )
    expect(
      await screen.findByText(
        'Automatic stays off for this field until you choose Resume automatic handling.',
      ),
    ).toBeVisible()
    // Library's Works | Covers switch is the only pressed control.
    expect(document.querySelector('.library-workspace [aria-pressed="true"]')).toBeNull()
    expect(document.querySelector('[role="radio"][aria-checked="true"]')).toBeNull()
    // Nothing is marked, so the header does not say something is.
    expect(screen.getByText(/Your earlier decision still holds this field\./u)).toBeVisible()
    expect(screen.queryByText(/The recorded outcome is marked below/u)).toBeNull()
    const resolved = screen.getByText('Automatic', { selector: 'dt' })
    expect(resolved.nextElementSibling).toHaveTextContent('0')
  })

  it('M4 says a stale entry was not recorded on the case that replaced it', async () => {
    const user = userEvent.setup()
    let stale = false
    const http = {
      ...owner(
        () => [contextTitle],
        async () => {
          stale = true
          throw new ApiError(409, 'stale')
        },
      ),
      getCaseSuccessor: vi.fn(async () => 'case-2'),
      // The server refuses a superseded case's read as stale.
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (stale && id === 'case-1') throw new ApiError(409, 'stale')
        return { ...detail, id, fields: [contextTitle] }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const panel = await screen.findByRole('region', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    await user.type(within(panel).getByRole('textbox'), 'A Fresh Orchard')
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    const moved = await screen.findByRole('region', { name: 'Your value for Title' })
    expect(
      await within(moved).findByText('Not recorded: the work changed. Review it again.'),
    ).toBeVisible()
    await waitFor(() => expect(heading()).toHaveFocus())
    expect(router.state.location.search.field).toBe('title')
    expect(screen.queryByText(/Selected work could not be loaded/u)).toBeNull()
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    // Acting on the replacement case clears the notice; a fresh input is valid.
    await user.click(within(moved).getByRole('button', { name: 'Enter a value' }))
    expect(within(moved).getByRole('textbox')).not.toHaveAttribute('aria-invalid')
    expect(within(moved).queryByText(/the work changed/u)).toBeNull()
  })

  it('M4 moves an owner with an open form to the case that superseded theirs', async () => {
    const user = userEvent.setup()
    let stale = false
    const updates = createLiveStream()
    const http = {
      ...owner(() => [contextTitle]),
      getLibrary: vi.fn(async () => (stale ? { ...quietLibrary, works: [] } : quietLibrary)),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
      // The server refuses a superseded case's read as stale.
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (stale && id === 'case-1') throw new ApiError(409, 'stale')
        return { ...detail, id, fields: [contextTitle] }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    const panel = await screen.findByRole('region', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    stale = true
    await act(async () => {
      updates.announce({ sequence: 80, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    const moved = await screen.findByRole('region', { name: 'Your value for Title' })
    expect(await within(moved).findByText('The work changed. Review it again.')).toBeVisible()
    await waitFor(() => expect(heading()).toHaveFocus())
    expect(screen.queryByText(/Selected work could not be loaded/u)).toBeNull()
    expect(screen.queryByText(/not in the current list of works/u)).toBeNull()
    await user.click(within(moved).getByRole('button', { name: 'Enter a value' }))
    expect(within(moved).queryByText('The work changed. Review it again.')).toBeNull()
  })

  it.each([
    ['lineage', 'moved'],
    ['lineage', 'successor gone'],
    ['library', 'moved'],
    ['library', 'successor gone'],
    ['library', 'predecessor unavailable'],
    ['library', 'predecessor current'],
    ['named', 'moved'],
    ['named', 'successor gone'],
    ['named', 'predecessor unavailable'],
    ['named', 'predecessor current'],
  ] as const)(
    // Which of Library and the lineage read answers first, or `named` where the
    // Library update itself names the successor and no lineage read is made.
    'M4 calls the work unavailable only once the move ends (%s, %s)',
    async (first, outcome) => {
      const user = userEvent.setup()
      let stale = false
      const updates = createLiveStream()
      const answered = { library: () => undefined, lineage: () => undefined } as Record<
        'library' | 'lineage',
        () => void
      >
      const settled = {
        library: new Promise<void>((resolve) => (answered.library = resolve)),
        lineage: new Promise<void>((resolve) => (answered.lineage = resolve)),
      }
      // The read that answers second waits for the other one to have answered.
      const after = async (read: 'library' | 'lineage') => {
        answered[read]()
        if (first === 'named' || first === read) return
        await settled[first]
        await new Promise((resolve) => setTimeout(resolve, 20))
      }
      // Every frame counts, including one a later effect replaces at once.
      const frames: string[] = []
      let moveEnded: number | undefined
      const http = {
        ...owner(() => [contextTitle]),
        getLibrary: vi.fn(async () => {
          if (!stale) return quietLibrary
          await after('library')
          return { ...quietLibrary, works: [] }
        }),
        getCaseSuccessor: vi.fn(async () => {
          if (!stale) return null
          await after('lineage')
          return 'case-2'
        }),
        getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id, signal) => {
          // The selected case's own query read again is where the move ends.
          if (stale && id === 'case-1' && signal) moveEnded ??= frames.length
          if (stale && id === 'case-1') {
            if (outcome === 'predecessor unavailable') throw new ApiError(503, 'unavailable')
            if (outcome !== 'predecessor current') throw new ApiError(409, 'stale')
          }
          if (id === 'case-2' && outcome === 'successor gone') throw new ApiError(404, 'not_found')
          return { ...detail, id, fields: [contextTitle] }
        }),
      }
      const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
      const panel = await screen.findByRole('region', { name: 'Your value for Title' })
      await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
      const observer = new MutationObserver((records) => {
        for (const record of records)
          for (const node of record.addedNodes) frames.push(node.textContent ?? '')
      })
      observer.observe(document.body, { childList: true, subtree: true })
      stale = true
      await act(async () => {
        updates.announce({
          sequence: 83,
          subject: first === 'named' ? 'case-2' : null,
          topic: 'library',
        })
        await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 60))
      })
      if (outcome === 'predecessor current') {
        // Its own read says the case is current, so the owner stays on it (#1365).
        expect(await screen.findByRole('region', { name: 'Your value for Title' })).toBeVisible()
        expect(router.state.location.search.preview).toBe('case-1')
        observer.disconnect()
        expect(
          frames.filter((frame) => frame.includes('not in the current list of works')),
        ).toEqual([])
        return
      }
      if (outcome !== 'moved') {
        // The move ends with nowhere to go, so the absence is news after all,
        // once the case's own read has been made again and refused (#1365).
        expect(await screen.findByRole('heading', { name: 'Selected work' })).toBeVisible()
        expect(router.state.location.search.preview).toBe('case-1')
        observer.disconnect()
        expect(http.getCaseDetail).toHaveBeenLastCalledWith(
          'case-1',
          expect.any(AbortSignal),
          'foreground',
        )
        const unavailable = (frame: string) => frame.includes('not in the current list of works')
        expect(moveEnded).toBeDefined()
        expect(frames.slice(0, moveEnded).filter(unavailable)).toEqual([])
        expect(frames.slice(moveEnded).some(unavailable)).toBe(true)
        return
      }
      await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
      await screen.findByRole('region', { name: 'Your value for Title' })
      observer.disconnect()
      expect(frames.some((frame) => frame.includes('Loading selected work…'))).toBe(true)
      expect(frames.filter((frame) => frame.includes('not in the current list of works'))).toEqual(
        [],
      )
    },
  )

  it('M4 keeps an open entry and its draft through a transient failure of the move check', async () => {
    const user = userEvent.setup()
    let stale = false
    const updates = createLiveStream()
    const http = {
      ...owner(() => [contextTitle]),
      getLibrary: vi.fn(async () => (stale ? { ...quietLibrary, works: [] } : quietLibrary)),
      // The move check reads the case directly and fails once; the case's own
      // query read answers late and succeeds.
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id, signal) => {
        if (stale && signal === undefined) throw new ApiError(503, 'unavailable')
        if (stale) await new Promise((resolve) => setTimeout(resolve, 80))
        return { ...detail, id, fields: [contextTitle] }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    const panel = await screen.findByRole('region', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Title' })
    await user.type(input, 'Half typed')
    stale = true
    await act(async () => {
      updates.announce({ sequence: 84, subject: 'case-2', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 200))
    })
    // The check failed, the case was read again, and the form the owner has
    // open never left (#1365).
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1')
    expect(http.getCaseDetail).toHaveBeenLastCalledWith(
      'case-1',
      expect.any(AbortSignal),
      'foreground',
    )
    expect(input).toBeInTheDocument()
    expect(input).toHaveValue('Half typed')
    expect(screen.queryByText(/not in the current list of works/u)).toBeNull()
  })

  it('M4 leaves focus on the work, not the page, when no successor is found', async () => {
    const user = userEvent.setup()
    let stale = false
    const updates = createLiveStream()
    const http = {
      ...owner(() => [contextTitle]),
      getLibrary: vi.fn(async () => (stale ? { ...quietLibrary, works: [] } : quietLibrary)),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
      // The server refuses the superseded case's read, and its named
      // replacement has gone too.
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (stale && id === 'case-1') throw new ApiError(409, 'stale')
        if (id === 'case-2') throw new ApiError(404, 'not_found')
        return { ...detail, id, fields: [contextTitle] }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    const panel = await screen.findByRole('region', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    stale = true
    await act(async () => {
      updates.announce({ sequence: 81, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, 3 * coalesceMilliseconds))
    })
    const unavailable = await screen.findByRole('heading', { name: 'Selected work' })
    await waitFor(() => expect(unavailable).toHaveFocus())
    expect(document.activeElement).not.toBe(document.body)
  })

  it('M4 says nothing when the owner was not using an owner control', async () => {
    let stale = false
    const updates = createLiveStream()
    const http = {
      ...owner(() => [contextTitle]),
      getLibrary: vi.fn(async () => (stale ? { ...quietLibrary, works: [] } : quietLibrary)),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
      // The server refuses a superseded case's read as stale.
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (stale && id === 'case-1') throw new ApiError(409, 'stale')
        return { ...detail, id, fields: [contextTitle] }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    await screen.findByRole('region', { name: 'Your value for Title' })
    stale = true
    await act(async () => {
      updates.announce({ sequence: 82, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    await screen.findByRole('region', { name: 'Your value for Title' })
    expect(screen.queryByText('The work changed. Review it again.')).toBeNull()
  })

  it('D1 puts focus on the first genre when the subset opens', async () => {
    const user = userEvent.setup()
    const genres: Field = {
      ...contextTitle,
      destination: 'genres' as const,
      currentRaw: ['Drama'],
      ownerControl: control({
        current: ['Drama'],
        clear: true,
        subset: {
          maxItems: 32,
          options: [{ value: 'Drama', current: true, proposed: false, previous: false }],
        },
      }),
    }
    renderRoute(
      '/library?lens=all&preview=case-1&field=genres',
      owner(() => [genres]),
    )
    const panel = await screen.findByRole('region', { name: 'Your value for Genres' })
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    expect(within(panel).getByRole('checkbox', { name: /Drama/u })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(within(panel).getByRole('button', { name: 'Change' })).toHaveFocus()
  })

  it('S2 ties a refusal to the input it is about', async () => {
    const user = userEvent.setup()
    const panel = await open(owner(() => [contextTitle]))
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(/Enter a title\./u)
    expect(within(panel).getAllByRole('alert')).toHaveLength(1)
    await user.keyboard('{Escape}')
    expect(within(panel).getByRole('button', { name: 'Enter a value' })).toHaveFocus()
  })

  it('S4 announces an owner command once, in its own words', async () => {
    const user = userEvent.setup()
    const genres: Field = {
      ...base,
      destination: 'genres' as const,
      decisionActions: ['keep_current', 'accept'],
      currentValue: 'Drama',
      currentRaw: ['Drama'],
      proposedValues: [{ kind: 'list', value: ['Fantasy'] }],
      ownerControl: control({
        current: ['Drama'],
        clear: true,
        subset: {
          maxItems: 32,
          options: [
            { value: 'Drama', current: true, proposed: false, previous: false },
            { value: 'Fantasy', current: false, proposed: true, previous: false },
          ],
        },
      }),
    }
    let fields: readonly Field[] = [genres]
    const http = owner(() => fields)
    renderRoute('/library?lens=all&preview=case-1&field=genres', http)
    const panel = await screen.findByRole('region', { name: 'Your value for Genres' })
    await user.click(within(panel).getByRole('button', { name: 'Change' }))
    await user.click(within(panel).getByRole('checkbox', { name: /Fantasy/u }))
    fields = [
      {
        ...genres,
        needsAttention: false,
        humanDecision: {
          ...decision('owner_entered', { kind: 'list', value: ['Drama', 'Fantasy'] }),
          destination: 'genres' as const,
        },
      },
    ]
    await user.click(within(panel).getByRole('button', { name: 'Record these genres' }))
    expect(await screen.findByText('Your value is recorded.')).toBeVisible()
    expect(screen.queryByText('Choice recorded.')).toBeNull()
    expect(screen.queryByText('Recording choice…')).toBeNull()
  })

  it('S5 and the written note promise only what is possible, once', async () => {
    const http = owner(() => [
      { ...enteredTitle, ownerControl: control({ ...entry, withdraw: 'in_progress' }) },
    ])
    const panel = await open(http)
    expect(
      within(panel).getByText(
        'This decision is being written to your library. You can change it once the write finishes.',
      ),
    ).toBeVisible()
  })

  it('S6 reads a withdrawal in History as a withdrawal', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [
        {
          category: 'owner' as const,
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'title' as const,
          outcome: 'withdraw',
          previousValue: { kind: 'text', value: 'The Silent Orchard' },
          selectedValue: null,
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
    const article = (await screen.findByRole('heading', { name: 'Title' })).closest('article')
    expect(article).toHaveTextContent('You withdrew your choice.')
    // The sentence says it; no Outcome row repeats it.
    expect(article).not.toHaveTextContent('Outcome')
    expect(article).not.toHaveTextContent('No value is recorded.')
  })

  it('O1 re-reads the open case on Activity while its owner write is in flight', async () => {
    const updates = createLiveStream()
    let writing = true
    const http = owner(() => [
      {
        ...enteredTitle,
        ownerControl: control({ ...entry, withdraw: writing ? 'in_progress' : 'written' }),
      },
    ])
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    const panel = await screen.findByRole('region', { name: 'Your value for Title' })
    expect(within(panel).getByText(/is being written to your library/u)).toBeVisible()
    writing = false
    await act(async () => {
      updates.announce({ sequence: 81, subject: null, topic: 'activity' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    await waitFor(() =>
      expect(within(panel).queryByText(/is being written to your library/u)).toBeNull(),
    )
    expect(within(panel).getByRole('button', { name: 'Change' })).toBeVisible()
  })
})

// A pin on each value's card (#1346, ADR-0085 Amendment 2).
describe('a pin on each value', () => {
  const PINNED =
    'You pinned this value. Mechanical Librarian will not ask about it or change it unless it changes in Audiobookshelf.'
  const CHANGED = 'Audiobookshelf shows a different value from the one you pinned.'
  const WAITING = 'You pinned this value. The pin starts once the change is written.'
  const NEW = { kind: 'text', value: 'New title' } as const
  const pinOf = (state: NonNullable<Field['pin']>['state'], value: string): Field['pin'] => ({
    state,
    value: { destination: 'title', state: 'value', value, formatted: null },
  })
  const offered: Field = { ...base, pinControl: { pin: 'available', unpin: false } }
  const inForce: Field = {
    ...offered,
    needsAttention: false,
    classification: 'human_recorded',
    classificationCause: 'pinned',
    classificationReason: PINNED,
    pin: pinOf('in_force', 'Old title'),
    pinControl: { pin: 'available', unpin: true },
  }
  // The store, as far as a pin reads: each command leaves the next case.
  const after = (field: Field, command: DecisionCommand): Field => {
    if (command.outcome === 'pin')
      return field.humanDecision?.outcome === 'accept' ||
        field.humanDecision?.outcome === 'owner_entered'
        ? {
            ...field,
            pin: pinOf('waiting', displayValue(field.humanDecision.selected_value?.value)),
            pinControl: { pin: 'available', unpin: true },
          }
        : {
            ...field,
            needsAttention: false,
            classification: 'human_recorded',
            classificationCause: 'pinned',
            classificationReason: PINNED,
            pin: pinOf('in_force', field.currentValue),
            pinControl: { pin: 'available', unpin: true },
          }
    if (command.outcome === 'unpin')
      return {
        ...field,
        classificationCause: 'human_decided',
        classificationReason: 'You decided this field. You can change it here.',
        pin: null,
        pinControl: { pin: 'available', unpin: false },
      }
    return {
      ...field,
      needsAttention: false,
      classification: 'human_recorded',
      classificationCause: 'human_decided',
      classificationReason: 'You decided this field. You can change it here.',
      humanDecision: decision(command.outcome, command.selectedValue ?? null),
      pin: null,
      pinControl: { pin: 'available', unpin: false },
    }
  }
  const pins = (
    initial: Field,
    refuse: (command: DecisionCommand) => unknown = () => undefined,
    // Whether the store's answer carries the case as it now reads.
    readBack: () => boolean = () => true,
  ) => {
    const state = { field: initial }
    const http = owner(
      () => [state.field],
      async (caseId, command) => {
        const refusal = await refuse(command)
        if (refusal !== undefined) throw refusal as Error
        state.field = after(state.field, command)
        return {
          ...recordedAcknowledgement(
            { ...detail, id: caseId },
            {
              invalidation_sequences: [],
              schema_version: 'human-decision-ack-v2' as const,
              actor: 'owner',
              application: 'apply',
              application_reason: null,
              decision_id: `decision-${command.idempotencyKey}`,
              destination: command.destination,
              occurred_at: 2,
              outcome: command.outcome,
              proposal_id: command.proposalId,
              review_case_id: caseId,
              selected_value: command.selectedValue ?? null,
              target_fingerprint: command.targetFingerprint,
            },
          ),
          confirmation: readBack()
            ? { status: 'confirmed', case: { ...detail, fields: [state.field] } }
            : { status: 'unavailable' },
        }
      },
    )
    return { http, state }
  }
  const show = async (http: ReturnType<typeof owner>) => {
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    return await screen.findByRole('region', { name: 'Pin on Title' })
  }
  const card = (name: string) => {
    const article = screen.getByRole('radio', { name }).closest('article')
    if (!article) throw new Error(`no card for ${name}`)
    return article
  }
  const CURRENT = 'Choose current value: Old title'
  const PROPOSED = 'Choose proposed value: New title'
  const outcomes = (http: ReturnType<typeof owner>) => sent(http).map((command) => command.outcome)

  it('P1 puts a pin at the foot of each value’s card, named for what a press does', async () => {
    const { http } = pins(offered)
    const foot = await show(http)
    for (const name of [CURRENT, PROPOSED]) {
      const pin = within(card(name)).getByRole('button', { name: 'Pin this value' })
      expect(pin).toHaveAttribute('title', 'Pin this value')
      expect(pin).not.toHaveAttribute('aria-pressed')
      expect(pin).not.toHaveClass('is-on')
      expect(pin.closest('.choice-foot')).not.toBeNull()
      // Each pin is told apart by the value it is for.
      expect(pin).toHaveAccessibleDescription(
        name.endsWith('Old title') ? 'Old title' : 'New title',
      )
      expect(card(name)).not.toHaveTextContent('Pinned')
    }
    // The foot says nothing and offers nothing until a pin is pressed.
    expect(foot).toHaveTextContent('')
    expect(within(foot).queryByRole('button')).toBeNull()
    expect(within(foot).getByRole('status')).toBeEmptyDOMElement()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('P2 chooses and pins an offered value in one press, and stays on the field', async () => {
    const user = userEvent.setup()
    const { http } = pins(offered)
    const foot = await show(http)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(sent(http)).toMatchObject([
      { outcome: 'accept', selectedValue: NEW },
      { outcome: 'pin', selectedValue: null },
    ])
    expect(sent(http)[0]?.idempotencyKey).not.toBe(sent(http)[1]?.idempotencyKey)
    expect(card(PROPOSED)).toHaveTextContent('Your choice · Pinned')
    expect(card(CURRENT)).not.toHaveTextContent('Pinned')
    const pin = within(card(PROPOSED)).getByRole('button', { name: 'Unpin' })
    expect(pin).toHaveClass('is-on')
    expect(pin).toHaveAttribute('title', 'Unpin')
    expect(pin).toHaveFocus()
    expect(within(foot).getByText(WAITING)).toBeVisible()
    // Unpin is pressed on the card that carries the pin, not at the foot.
    expect(within(foot).queryByRole('button')).toBeNull()
    expect(within(foot).getByRole('alert')).toBeEmptyDOMElement()
    // Nothing hides what happened: the field stays, and no other line repeats it.
    expect(screen.getByRole('radio', { name: PROPOSED })).toBeChecked()
    expect(screen.queryByText('Review complete')).toBeNull()
    expect(screen.queryByText('Your choice is recorded.')).toBeNull()
    // The line under the choices stays quiet during a pin: the foot alone says it.
    expect(screen.getAllByText('Pinned.')).toHaveLength(1)
    // The sentence above already says the value is pinned, so the word is
    // announced from the status and not printed again under it.
    expect(within(foot).getByRole('status')).toHaveClass('sr-only')
    expect(foot).toHaveClass('has-note')
  })

  it('P3 keeps and pins the current value, then unpins from the same card', async () => {
    const user = userEvent.setup()
    const { http } = pins(offered)
    const foot = await show(http)
    await user.click(within(card(CURRENT)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['keep_current', 'pin'])
    expect(card(CURRENT)).toHaveTextContent('Your choice · Pinned')
    expect(within(foot).getByText(PINNED)).toBeVisible()
    // The server's sentence is said once, at the foot.
    expect(screen.getAllByText(PINNED)).toHaveLength(1)
    await user.click(within(card(CURRENT)).getByRole('button', { name: 'Unpin' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Unpinned.'))
    expect(outcomes(http)).toEqual(['keep_current', 'pin', 'unpin'])
    // Nothing else says the pin is gone, so the word stays in view.
    expect(within(foot).getByRole('status')).not.toHaveClass('sr-only')
    expect(card(CURRENT)).toHaveTextContent('Your choice')
    expect(card(CURRENT)).not.toHaveTextContent('Pinned')
    const pin = within(card(CURRENT)).getByRole('button', { name: 'Pin this value' })
    expect(pin).toHaveFocus()
    expect(screen.queryByText(PINNED)).toBeNull()
    // The chosen card is pinned again at once: the choice is not sent twice.
    await user.click(pin)
    await waitFor(() => expect(outcomes(http)).toEqual(['keep_current', 'pin', 'unpin', 'pin']))
  })

  it('P4 marks a pin carried from an earlier case and leaves its radio a no-op', async () => {
    const user = userEvent.setup()
    // Automatic decided before the pin: under the pin it chose nothing.
    const { http } = pins({
      ...inForce,
      automaticDecision: {
        outcome: 'candidate',
        selected_value: NEW,
      } as never,
    })
    const foot = await show(http)
    const current = screen.getByRole('radio', { name: CURRENT })
    expect(current).toBeChecked()
    expect(current).toBeEnabled()
    expect(current).toHaveAttribute('tabindex', '0')
    expect(within(card(CURRENT)).getByText('Pinned')).toHaveClass('choice-chosen')
    expect(card(CURRENT)).not.toHaveTextContent('Your choice')
    expect(card(CURRENT)).toHaveClass('is-selected')
    expect(screen.getByRole('radio', { name: PROPOSED })).not.toBeChecked()
    expect(card(PROPOSED)).not.toHaveTextContent('Automatic’s choice')
    expect(within(card(CURRENT)).getByRole('button', { name: 'Unpin' })).toHaveClass('is-on')
    expect(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' })).toBeVisible()
    expect(screen.getByText('You can choose another value.')).toBeVisible()
    expect(screen.queryByText(/The recorded outcome is marked below/u)).toBeNull()
    expect(within(foot).getByText(PINNED)).toBeVisible()
    expect(screen.getAllByText(PINNED)).toHaveLength(1)
    // Pressing the pinned value again would end the pin, so it records nothing.
    await user.click(current)
    current.focus()
    await user.keyboard('{Enter}')
    expect(http.recordDecision).not.toHaveBeenCalled()
    // Another value is a plain choice, as on any field.
    await user.click(screen.getByRole('radio', { name: PROPOSED }))
    await waitFor(() => expect(outcomes(http)).toEqual(['accept']))
  })

  it('P5 moves a pin in force to another value in one press', async () => {
    const user = userEvent.setup()
    const { http } = pins(inForce)
    const foot = await show(http)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['accept', 'pin'])
    expect(card(PROPOSED)).toHaveTextContent('Your choice · Pinned')
    expect(card(CURRENT)).not.toHaveTextContent('Pinned')
    expect(within(foot).getByText(WAITING)).toBeVisible()
  })

  it('P6 pins and unpins the current value of a field with nothing to choose', async () => {
    const user = userEvent.setup()
    const { http } = pins({
      ...contextTitle,
      ownerControl: null,
      pinControl: { pin: 'available', unpin: false },
    })
    const foot = await show(http)
    const value = screen.getByRole('region', { name: 'Title current value' })
    expect(within(value).getByText('Current value')).toBeVisible()
    await user.click(within(value).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['pin'])
    expect(within(value).getByText('Current value · Pinned')).toBeVisible()
    const pin = within(value).getByRole('button', { name: 'Unpin' })
    expect(pin).toHaveClass('is-on')
    expect(pin).toHaveFocus()
    expect(pin).toHaveAccessibleDescription('THE SILENT ORCHARD (Disc 2)')
    expect(within(foot).getByText(PINNED)).toBeVisible()
    expect(within(foot).queryByRole('button')).toBeNull()
    // The pinned field says why it reads as it does once, at its foot.
    expect(screen.queryByText('No source offered a different value.')).toBeNull()
    await user.click(pin)
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Unpinned.'))
    expect(outcomes(http)).toEqual(['pin', 'unpin'])
    expect(within(value).getByText('Current value')).toBeVisible()
    expect(within(value).getByRole('button', { name: 'Pin this value' })).toHaveFocus()
  })

  it('P6b pins the value Automatic kept where the field shows no choice cards', async () => {
    const user = userEvent.setup()
    const { http } = pins({
      ...contextTitle,
      ownerControl: null,
      classification: 'automatic_recorded',
      classificationCause: 'automatic_decided',
      automaticDecision: { outcome: 'keep_current', selected_value: null } as never,
      pinControl: { pin: 'available', unpin: false },
    })
    const foot = await show(http)
    const outcome = screen.getByRole('region', { name: 'Title recorded outcome' })
    const pin = within(outcome).getByRole('button', { name: 'Pin this value' })
    expect(pin).toHaveAccessibleDescription('THE SILENT ORCHARD (Disc 2)')
    // The foot offers nothing of its own while the pin sits beside the value.
    expect(within(foot).queryByRole('button')).toBeNull()
    await user.click(pin)
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['pin'])
  })

  it('P7 asks again for a changed pin and shows what was pinned, with Unpin', async () => {
    const user = userEvent.setup()
    const changed: Field = {
      ...offered,
      classificationCause: 'pin_changed',
      classificationReason: CHANGED,
      pin: pinOf('changed', 'An older title'),
      pinControl: { pin: 'available', unpin: true },
      ownerControl: control({
        entry: { maxCharacters: 512, suggestions: [{ rule: 'pinned', value: 'An older title' }] },
      }),
    }
    const { http } = pins(changed)
    const foot = await show(http)
    expect(screen.getByRole('heading', { name: 'Which title should this work use?' })).toBeVisible()
    expect(screen.getAllByText(CHANGED)).toHaveLength(1)
    expect(within(foot).getByText('You pinned “An older title”.')).toBeVisible()
    // No card carries a changed pin: each offers a pin, and Unpin is at the foot.
    for (const name of [CURRENT, PROPOSED]) {
      expect(card(name)).not.toHaveTextContent('Pinned')
      expect(within(card(name)).getByRole('button', { name: 'Pin this value' })).toBeVisible()
    }
    const panel = screen.getByRole('region', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    expect(
      within(panel).getByRole('button', { name: 'Use pinned value “An older title”' }),
    ).toBeVisible()
    await user.click(within(panel).getByRole('button', { name: 'Cancel' }))
    const unpin = within(foot).getByRole('button', { name: 'Unpin' })
    await user.click(unpin)
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Unpinned.'))
    expect(outcomes(http)).toEqual(['unpin'])
    // The button is gone with the pin: focus is left on what the foot says.
    expect(within(foot).queryByRole('button')).toBeNull()
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveFocus())
  })

  it('P15 says to press Unpin again when the foot’s Unpin fails', async () => {
    const user = userEvent.setup()
    const refuse = (command: DecisionCommand) =>
      command.outcome === 'unpin' ? new ApiError(503, 'persistence') : undefined
    // A changed pin is on no card: Unpin is the button at the foot.
    const changed = pins(
      {
        ...offered,
        classificationCause: 'pin_changed',
        classificationReason: CHANGED,
        pin: pinOf('changed', 'An older title'),
        pinControl: { pin: 'available', unpin: true },
      },
      refuse,
    )
    const foot = await show(changed.http)
    await user.click(within(foot).getByRole('button', { name: 'Unpin' }))
    await waitFor(() =>
      expect(within(foot).getByRole('alert')).toHaveTextContent(
        'This field is still pinned. Press Unpin again.',
      ),
    )
    expect(within(foot).getByRole('button', { name: 'Unpin' })).toBeVisible()
  })

  it('P16 says to press the pin again when an unpin from a card fails', async () => {
    const user = userEvent.setup()
    const { http } = pins(inForce, (command) =>
      command.outcome === 'unpin' ? new ApiError(503, 'persistence') : undefined,
    )
    const cardFoot = await show(http)
    await user.click(within(card(CURRENT)).getByRole('button', { name: 'Unpin' }))
    await waitFor(() =>
      expect(within(cardFoot).getByRole('alert')).toHaveTextContent(
        'This field is still pinned. Press the pin again.',
      ),
    )
  })

  it('P8 asks about a changed pin where no source offers a value', async () => {
    const user = userEvent.setup()
    const { http } = pins({
      ...contextTitle,
      ownerControl: null,
      needsAttention: true,
      classification: 'needs_attention',
      classificationCause: 'pin_changed',
      classificationReason: CHANGED,
      pin: pinOf('changed', 'The Silent Orchard'),
      pinControl: { pin: 'available', unpin: true },
    })
    const foot = await show(http)
    expect(screen.getByRole('heading', { name: 'Title', level: 3 })).toBeVisible()
    expect(screen.getAllByText(CHANGED)).toHaveLength(1)
    expect(within(foot).getByText('You pinned “The Silent Orchard”.')).toBeVisible()
    expect(within(foot).getByRole('button', { name: 'Unpin' })).toBeVisible()
    const value = screen.getByRole('region', { name: 'Title current value' })
    expect(within(value).getByText('Current value')).toBeVisible()
    // Pinning the value the library shows now settles it.
    await user.click(within(value).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['pin'])
    expect(within(value).getByText('Current value · Pinned')).toBeVisible()
  })

  it('P9 says a recorded change comes first where no pin is offered', async () => {
    const waiting: Field = {
      ...offered,
      needsAttention: false,
      classification: 'automatic_recorded',
      classificationCause: 'automatic_decided',
      pinControl: { pin: 'change_waiting', unpin: false },
      // Automatic's write verified; the next scan has not read it back.
      writtenByCase: true,
      changeWritten: true,
    }
    const { http } = pins(waiting)
    const foot = await show(http)
    expect(foot).toHaveTextContent(
      'You can pin this field after the next scan reads its new value.',
    )
    expect(screen.queryByRole('button', { name: 'Pin this value' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Unpin' })).toBeNull()
    // An explanation, not a state: plain supporting text, not a pin's box.
    expect(foot.querySelector('.pin-why')).toHaveTextContent(
      'You can pin this field after the next scan reads its new value.',
    )
    expect(foot.querySelector('.pin-note')).toBeNull()
    expect(foot).not.toHaveClass('has-note')
  })

  it('P10 says the choice stands when its pin is refused, and the next press sends only the pin', async () => {
    const user = userEvent.setup()
    let refused = true
    const { http } = pins(offered, (command) =>
      command.outcome === 'pin' && refused ? new ApiError(503, 'persistence') : undefined,
    )
    const foot = await show(http)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() =>
      expect(within(foot).getByRole('alert')).toHaveTextContent(
        'Your choice is recorded, but the pin was not. Press the pin again.',
      ),
    )
    expect(within(foot).getByRole('status')).toBeEmptyDOMElement()
    expect(outcomes(http)).toEqual(['accept', 'pin'])
    expect(card(PROPOSED)).toHaveTextContent('Your choice')
    expect(card(PROPOSED)).not.toHaveTextContent('Pinned')
    expect(screen.getByRole('radio', { name: PROPOSED })).toBeChecked()
    expect(screen.queryByRole('button', { name: 'Try the same decision again' })).toBeNull()
    const pin = within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' })
    expect(pin).not.toHaveClass('is-on')
    expect(pin).toHaveFocus()
    refused = false
    await user.click(pin)
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['accept', 'pin', 'pin'])
    expect(within(foot).getByRole('alert')).toBeEmptyDOMElement()
    expect(card(PROPOSED)).toHaveTextContent('Your choice · Pinned')
  })

  it('P11 sends no pin while its value command fails, and sends it after the retry', async () => {
    const user = userEvent.setup()
    let refused = true
    const { http } = pins(offered, (command) =>
      command.outcome === 'accept' && refused ? new ApiError(503, 'persistence') : undefined,
    )
    const foot = await show(http)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    const retry = await screen.findByRole('button', { name: 'Try the same decision again' })
    expect(within(foot).getByRole('alert')).toHaveTextContent(
      'Your choice could not be confirmed. Choose Try the same decision again to send it once more.',
    )
    // The pin's own line says what failed, so its retry stays after it.
    expect(foot.compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(outcomes(http)).toEqual(['accept'])
    refused = false
    await user.click(retry)
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['accept', 'accept', 'pin'])
    expect(card(PROPOSED)).toHaveTextContent('Your choice · Pinned')
  })

  it('P12 keeps the pin focused and ignores a second press while it records', async () => {
    const user = userEvent.setup()
    const gate: { open?: () => void } = {}
    const { http } = pins(offered, async () => {
      await new Promise<void>((resolve) => {
        gate.open = resolve
      })
      return undefined
    })
    const foot = await show(http)
    const pin = within(card(CURRENT)).getByRole('button', { name: 'Pin this value' })
    await user.click(pin)
    await waitFor(() => expect(pin).toHaveAttribute('aria-disabled', 'true'))
    expect(pin).toBeEnabled()
    expect(pin).toHaveFocus()
    expect(within(foot).getByRole('status')).toHaveTextContent('Pinning…')
    await user.click(pin)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    expect(http.recordDecision).toHaveBeenCalledTimes(1)
    await act(async () => {
      gate.open?.()
      await Promise.resolve()
    })
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(2))
    gate.open?.()
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['keep_current', 'pin'])
  })

  it('P13 pins a value the owner entered from beside it, with the pin alone', async () => {
    const user = userEvent.setup()
    const { http } = pins({
      ...enteredTitle,
      ownerControl: control({ ...entry, withdraw: 'available' }),
      pinControl: { pin: 'available', unpin: false },
    })
    const foot = await show(http)
    const panel = screen.getByRole('region', { name: 'Your value for Title' })
    const pin = within(panel).getByRole('button', { name: 'Pin this value' })
    expect(pin).toHaveAccessibleDescription('You entered “The Silent Orchard”.')
    await user.click(pin)
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['pin'])
    expect(within(foot).getByText(WAITING)).toBeVisible()
    expect(within(foot).queryByRole('button')).toBeNull()
    expect(within(panel).getByRole('button', { name: 'Unpin' })).toHaveFocus()
    // The owner's own status line stays quiet: the foot says what the pin did.
    expect(panel.querySelector('.owner-status')).toBeEmptyDOMElement()
    await user.click(within(panel).getByRole('button', { name: 'Unpin' }))
    await waitFor(() => expect(outcomes(http)).toEqual(['pin', 'unpin']))
  })

  it('P17 says a pin that stands after its request failed in the status, not the alert', async () => {
    const user = userEvent.setup()
    // The store records the pin and the answer is lost on the way back.
    const lost = pins(offered, (command) => {
      if (command.outcome !== 'pin') return undefined
      lost.state.field = after(lost.state.field, command)
      return new ApiError(503, 'persistence')
    })
    const foot = await show(lost.http)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(within(foot).getByRole('alert')).toBeEmptyDOMElement()
    expect(card(PROPOSED)).toHaveTextContent('Your choice · Pinned')
    expect(outcomes(lost.http)).toEqual(['accept', 'pin'])
  })

  it('P18 moves a pin pressed on a replaced case to its successor and says it was not recorded', async () => {
    const user = userEvent.setup()
    let stale = false
    // No owner control on the field: the foot is where the notice is said.
    const plain: Field = { ...offered, ownerControl: null }
    const http = {
      ...owner(
        () => [plain],
        async () => {
          stale = true
          throw new ApiError(409, 'stale')
        },
      ),
      getCaseSuccessor: vi.fn(async () => 'case-2'),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (stale && id === 'case-1') throw new ApiError(409, 'stale')
        return { ...detail, id, fields: [plain] }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await screen.findByRole('region', { name: 'Pin on Title' })
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    // The owner stays on the work and the field, not sent to the Attention lens.
    expect(router.state.location.search.field).toBe('title')
    expect(router.state.location.search.lens).toBe('all')
    const moved = await screen.findByRole('region', { name: 'Pin on Title' })
    await waitFor(() =>
      expect(within(moved).getByRole('status')).toHaveTextContent(
        'Not recorded: the work changed. Review it again.',
      ),
    )
    expect(within(moved).getByRole('status')).not.toHaveClass('sr-only')
  })

  it('P19 says Pinned beside a pinned value the owner entered', async () => {
    const recorded: Field = {
      ...enteredTitle,
      editable: false,
      pinControl: { pin: 'available', unpin: false },
    }
    const { http } = pins(recorded)
    await show(http)
    const outcome = screen.getByRole('region', { name: 'Title recorded outcome' })
    expect(within(outcome).getByText('Your value')).toBeVisible()
    await userEvent.setup().click(
      within(screen.getByRole('region', { name: 'Your value for Title' })).getByRole('button', {
        name: 'Pin this value',
      }),
    )
    expect(await within(outcome).findByText('Your value · Pinned')).toBeVisible()
  })

  it('P14 shows no pin where the field offers none', async () => {
    const http = owner(() => [contextTitle])
    await open(http)
    expect(screen.queryByRole('region', { name: 'Pin on Title' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Pin this value' })).toBeNull()
  })

  it('P20 names the pin that was not sent once a recorded choice’s work is reloaded', async () => {
    const user = userEvent.setup()
    let reloads = false
    const { http } = pins(offered, undefined, () => reloads)
    await show(http)
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    const reload = await screen.findByRole('button', { name: 'Reload current case' })
    expect(outcomes(http)).toEqual(['accept'])
    reloads = true
    await user.click(reload)
    const foot = () => screen.getByRole('region', { name: 'Pin on Title' })
    await waitFor(() =>
      expect(within(foot()).getByRole('alert')).toHaveTextContent(
        'Your choice is recorded, but the pin was not. Press the pin again.',
      ),
    )
    // The pin was never sent, and the reload sends nothing.
    expect(outcomes(http)).toEqual(['accept'])
    expect(card(PROPOSED)).toHaveTextContent('Your choice')
    expect(card(PROPOSED)).not.toHaveTextContent('Pinned')
    await user.click(within(card(PROPOSED)).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(foot()).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(outcomes(http)).toEqual(['accept', 'pin'])
    expect(within(foot()).getByRole('alert')).toBeEmptyDOMElement()
  })
})

// A field with nothing to decide is pinned from its row of the No change
// panel and stays there (#1346).
describe('a pin in the No change panel', () => {
  const PINNED =
    'You pinned this value. Mechanical Librarian will not ask about it or change it unless it changes in Audiobookshelf.'
  const title: Field = { ...base, pinControl: { pin: 'available', unpin: false } }
  const subtitle: Field = {
    ...title,
    destination: 'subtitle',
    proposalId: 'proposal-subtitle',
    targetFingerprint: 'fingerprint-subtitle',
  }
  const publisher: Field = {
    ...base,
    classification: 'unsupported',
    classificationCause: 'nothing_to_decide',
    classificationReason: 'Nothing to decide for this field.',
    destination: 'publisher',
    currentValue: 'Example House',
    currentRaw: 'Example House',
    decisionActions: [],
    editable: false,
    inventoryRole: 'contextual',
    needsAttention: false,
    navigationRole: 'inspect',
    proposalId: 'proposal-publisher',
    proposedValues: [],
    targetFingerprint: 'fingerprint-publisher',
    ownerControl: null,
    pin: null,
    pinControl: { pin: 'available', unpin: false },
  }
  const after = (field: Field, command: DecisionCommand): Field =>
    field.destination !== command.destination
      ? field
      : command.outcome === 'pin'
        ? {
            ...field,
            classification: 'human_recorded',
            classificationCause: 'pinned',
            classificationReason: PINNED,
            pin: {
              state: 'in_force',
              value: {
                destination: 'publisher',
                state: 'value',
                value: 'Example House',
                formatted: null,
              },
            },
            pinControl: { pin: 'available', unpin: true },
          }
        : command.outcome === 'unpin'
          ? publisher
          : {
              ...field,
              needsAttention: false,
              classification: 'human_recorded',
              classificationCause: 'human_decided',
              humanDecision: decision(command.outcome, command.selectedValue ?? null),
            }
  const work = (
    refuse: (command: DecisionCommand, caseId: string) => unknown = () => undefined,
  ) => {
    const state = { fields: [title, subtitle, publisher] }
    const http = owner(
      () => state.fields,
      async (caseId, command) => {
        const refusal = await refuse(command, caseId)
        if (refusal !== undefined) throw refusal as Error
        state.fields = state.fields.map((field) => after(field, command))
        return {
          ...recordedAcknowledgement(
            { ...detail, id: caseId },
            {
              invalidation_sequences: [],
              schema_version: 'human-decision-ack-v2' as const,
              actor: 'owner',
              application: 'apply',
              application_reason: null,
              decision_id: `decision-${command.idempotencyKey}`,
              destination: command.destination,
              occurred_at: 2,
              outcome: command.outcome,
              proposal_id: command.proposalId,
              review_case_id: caseId,
              selected_value: command.selectedValue ?? null,
              target_fingerprint: command.targetFingerprint,
            },
          ),
          confirmation: {
            status: 'confirmed',
            case: { ...detail, id: caseId, fields: [...state.fields] },
          },
        }
      },
    )
    return { http, state }
  }
  const show = async (http: ReturnType<typeof owner>) => {
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await screen.findByRole('navigation', { name: 'Review fields' })
    return router
  }
  const panel = () => {
    const details = document.querySelector<HTMLDetailsElement>('.automatic-resolution-details')
    if (!details) throw new Error('the panel is missing')
    return details
  }
  const summary = () => {
    const found = panel().querySelector('summary')
    if (!found) throw new Error('the panel has no summary')
    return found
  }
  const row = () => {
    const found = within(panel()).getByText('Publisher').closest('.automatic-resolution-row')
    if (!(found instanceof HTMLElement)) throw new Error('no row for Publisher')
    return found
  }
  const heading = () => {
    const found = document.querySelector<HTMLElement>('.decision-canvas > header h3')
    if (!found) throw new Error('no field heading')
    return found
  }
  const TITLE_QUESTION = decisionQuestion('title')

  it('N1 pins and unpins a field from its row with one command each, and moves nothing', async () => {
    const user = userEvent.setup()
    const { http } = work()
    const router = await show(http)
    expect(panel()).not.toHaveAttribute('open')
    expect(summary()).toHaveTextContent(/^No change: PublisherShow why$/u)
    await user.click(summary())
    await user.click(within(row()).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(row()).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(sent(http)).toMatchObject([
      {
        destination: 'publisher',
        outcome: 'pin',
        selectedValue: null,
        proposalId: 'proposal-publisher',
        targetFingerprint: 'fingerprint-publisher',
        offerDigest: publisher.offerDigest,
      },
    ])
    expect(http.recordDecision.mock.calls[0]?.[0]).toBe('case-1')
    // The field stays in the panel, which stays open with focus on its pin.
    expect(panel()).toHaveAttribute('open')
    expect(within(row()).getByRole('button', { name: 'Unpin' })).toHaveFocus()
    expect(row()).toHaveTextContent('You pinned this value.')
    expect(within(row()).getByText('Pinned')).toHaveClass('status-pill')
    expect(summary()).toHaveTextContent(/^No change: Publisher · 1 pinnedHide$/u)
    expect(
      within(screen.getByRole('navigation', { name: 'Review fields' })).queryByText('Publisher'),
    ).toBeNull()
    // Nothing advances or completes: the owner is still on Title.
    expect(router.state.location.search.field).toBe('title')
    expect(heading()).toHaveTextContent(TITLE_QUESTION)
    expect(screen.queryByText('Review complete')).toBeNull()
    await user.click(within(row()).getByRole('button', { name: 'Unpin' }))
    await waitFor(() => expect(within(row()).getByRole('status')).toHaveTextContent('Unpinned.'))
    expect(sent(http).map((command) => [command.destination, command.outcome])).toEqual([
      ['publisher', 'pin'],
      ['publisher', 'unpin'],
    ])
    expect(within(row()).getByRole('button', { name: 'Pin this value' })).toHaveFocus()
    expect(row()).toHaveTextContent('No source offered a different value.')
    expect(row()).not.toHaveTextContent('Pinned')
    expect(summary()).toHaveTextContent(/^No change: PublisherHide$/u)
    expect(router.state.location.search.field).toBe('title')
  })

  it('N2 says a failed pin in its row, stays on the field and sends the next press', async () => {
    const user = userEvent.setup()
    let refused = true
    const { http } = work((command) =>
      command.outcome === 'pin' && refused ? new ApiError(500, 'other') : undefined,
    )
    const router = await show(http)
    await user.click(summary())
    await user.click(within(row()).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() =>
      expect(within(row()).getByRole('alert')).toHaveTextContent(
        'The pin was not recorded. Press the pin again.',
      ),
    )
    expect(router.state.location.search.field).toBe('title')
    expect(heading()).toHaveTextContent(TITLE_QUESTION)
    expect(panel()).toHaveAttribute('open')
    expect(row()).not.toHaveTextContent('Pinned')
    expect(screen.queryByRole('button', { name: 'Try the same decision again' })).toBeNull()
    const pin = within(row()).getByRole('button', { name: 'Pin this value' })
    expect(pin).toHaveFocus()
    expect(pin).not.toHaveAttribute('aria-disabled')
    refused = false
    await user.click(pin)
    await waitFor(() => expect(within(row()).getByRole('status')).toHaveTextContent('Pinned.'))
    expect(sent(http).map((command) => command.outcome)).toEqual(['pin', 'pin'])
    expect(within(row()).getByRole('alert')).toBeEmptyDOMElement()
    expect(router.state.location.search.field).toBe('title')
  })

  it('N3 says a pin pressed on a replaced case in its row on the successor, on the same field', async () => {
    const user = userEvent.setup()
    let stale = false
    const made = work((command, caseId) => {
      if (caseId !== 'case-1' || command.outcome !== 'pin') return undefined
      stale = true
      return new ApiError(409, 'stale')
    })
    const http = {
      ...made.http,
      getCaseSuccessor: vi.fn(async () => 'case-2'),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (stale && id === 'case-1') throw new ApiError(409, 'stale')
        return { ...detail, id, fields: [...made.state.fields] }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await screen.findByRole('navigation', { name: 'Review fields' })
    await user.click(summary())
    await user.click(within(row()).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    // The owner keeps the field they were on; the panel says what was not recorded.
    expect(router.state.location.search.field).toBe('title')
    expect(router.state.location.search.lens).toBe('all')
    await waitFor(() =>
      expect(within(row()).getByRole('status')).toHaveTextContent(
        'Not recorded: the work changed. Review it again.',
      ),
    )
    expect(panel()).toHaveAttribute('open')
    // Focus lands on the row's pin, which the notice describes.
    const pin = () => within(row()).getByRole('button', { name: 'Pin this value' })
    await waitFor(() => expect(pin()).toHaveFocus())
    expect(pin()).toHaveAccessibleDescription(
      'Publisher Example House Not recorded: the work changed. Review it again.',
    )
    expect(heading()).not.toHaveFocus()
    expect(row()).not.toHaveTextContent('Pinned')
    // The press belonged to the replaced case: this one's row holds nothing of it.
    expect(within(row()).queryByRole('alert')).toBeNull()
    // A later choice on Title is recorded on the successor and moves on as usual.
    await user.click(screen.getByRole('radio', { name: 'Choose proposed value: New title' }))
    await waitFor(() => expect(router.state.location.search.field).toBe('subtitle'))
    expect(http.recordDecision.mock.calls.map(([id, command]) => [id, command.outcome])).toEqual([
      ['case-1', 'pin'],
      ['case-2', 'accept'],
    ])
    expect(within(row()).getByRole('status')).toBeEmptyDOMElement()
  })

  it('N4 offers Reload where a pin locks a work that shows no field to decide', async () => {
    const user = userEvent.setup()
    const made = work()
    made.state.fields = [publisher]
    const recorded = made.http.recordDecision.getMockImplementation()
    if (!recorded) throw new Error('the fixture records no decision')
    made.http.recordDecision.mockImplementation(async (...args) => ({
      ...(await recorded(...args)),
      confirmation: { status: 'unavailable' },
    }))
    renderRoute('/library?lens=all&preview=case-1', made.http)
    await screen.findByText('This work has no fields requiring a decision.')
    expect(screen.queryByRole('button', { name: 'Reload current case' })).toBeNull()
    await user.click(summary())
    await user.click(within(row()).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() =>
      expect(within(row()).getByRole('alert')).toHaveTextContent(
        'Choose Reload current case before choosing again.',
      ),
    )
    expect(within(row()).getByRole('button')).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('This work has no fields requiring a decision.')).toBeVisible()
    // No canvas is on screen, so the page offers its Reload.
    await user.click(screen.getByRole('button', { name: 'Reload current case' }))
    await waitFor(() =>
      expect(within(row()).getByRole('button', { name: 'Unpin' })).not.toHaveAttribute(
        'aria-disabled',
      ),
    )
    expect(screen.queryByRole('button', { name: /Reload/u })).toBeNull()
    expect(within(row()).getByRole('alert')).toBeEmptyDOMElement()
    expect(panel()).toHaveAttribute('open')
    expect(made.http.recordDecision).toHaveBeenCalledTimes(1)
  })

  it('N5 offers Reload under Review complete when a pin pressed there locks the work', async () => {
    const user = userEvent.setup()
    const made = work()
    made.state.fields = [title, publisher]
    const recorded = made.http.recordDecision.getMockImplementation()
    if (!recorded) throw new Error('the fixture records no decision')
    made.http.recordDecision.mockImplementation(async (...args) => {
      const answer = await recorded(...args)
      return args[1].outcome === 'pin'
        ? { ...answer, confirmation: { status: 'unavailable' } }
        : answer
    })
    await show(made.http)
    await user.click(screen.getByRole('radio', { name: 'Choose proposed value: New title' }))
    await screen.findByText('Review complete')
    expect(document.querySelector('.decision-canvas')).toBeNull()
    await user.click(summary())
    await user.click(within(row()).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() =>
      expect(within(row()).getByRole('alert')).toHaveTextContent(
        'Choose Reload current case before choosing again.',
      ),
    )
    expect(document.querySelector('.decision-canvas')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Reload current case' }))
    await waitFor(() =>
      expect(within(row()).getByRole('button', { name: 'Unpin' })).not.toHaveAttribute(
        'aria-disabled',
      ),
    )
    expect(screen.queryByRole('button', { name: /Reload/u })).toBeNull()
  })

  it('N6 opens the panel closed on another work, with nothing said from the last one', async () => {
    const user = userEvent.setup()
    const { http } = work()
    const [listed] = quietLibrary.works
    if (!listed) throw new Error('the library fixture lists no work')
    const other = { ...listed, id: 'case-2', reviewCaseId: 'case-2', detailUrl: '/review/case-2' }
    http.getLibrary.mockResolvedValue({
      ...quietLibrary,
      counts: { ...quietLibrary.counts, all: 2, clear: 2 },
      works: [listed, other],
    })
    const router = await show(http)
    await user.click(summary())
    await user.click(within(row()).getByRole('button', { name: 'Pin this value' }))
    await waitFor(() => expect(within(row()).getByRole('status')).toHaveTextContent('Pinned.'))
    const first = panel()
    await act(() =>
      router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-2', field: 'title' },
      }),
    )
    await waitFor(() => expect(panel()).not.toBe(first))
    expect(panel()).not.toHaveAttribute('open')
    expect(summary()).toHaveTextContent(/^No change: Publisher · 1 pinnedShow why$/u)
    expect(within(row()).getByRole('status', { hidden: true })).toBeEmptyDOMElement()
    // Also on a work already read, which is on screen at once.
    await user.click(summary())
    const second = panel()
    await act(() =>
      router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-1', field: 'title' },
      }),
    )
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-1'))
    await screen.findByRole('navigation', { name: 'Review fields' })
    expect(panel()).not.toBe(second)
    expect(panel()).not.toHaveAttribute('open')
  })
})

// The recorded choice on a field this case wrote is the written value, so
// choosing it again would change nothing and sends nothing (#1980).
describe('the written choice', () => {
  const NEW = { kind: 'text', value: 'New title' } as const
  const PROPOSED = 'Choose proposed value: New title'
  // `ownWrite` is whether the accept's own write has landed (the server's
  // withdraw 'written'); `writtenByCase` whether any write of the case has.
  const written = (writtenByCase: boolean, ownWrite = writtenByCase): Field => ({
    ...base,
    needsAttention: false,
    classification: 'human_recorded',
    classificationCause: 'human_decided',
    classificationReason: 'You decided this field. You can change it here.',
    humanDecision: decision('accept', NEW),
    ownerControl: control({ withdraw: ownWrite ? 'written' : 'available' }),
    writtenByCase,
  })

  it('W1 leaves the chosen card of a written field a no-op that still reads as chosen', async () => {
    const user = userEvent.setup()
    const http = owner(() => [written(true)])
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const chosen = await screen.findByRole('radio', { name: PROPOSED })
    expect(chosen).toBeChecked()
    expect(chosen).toBeEnabled()
    expect(chosen).toHaveAttribute('tabindex', '0')
    expect(chosen.closest('article')).toHaveTextContent('Your choice')
    await user.click(chosen)
    chosen.focus()
    await user.keyboard('{Enter}')
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('W2 still sends the chosen card of a field this case has not written', async () => {
    const user = userEvent.setup()
    const http = owner(() => [written(false)])
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await user.click(await screen.findByRole('radio', { name: PROPOSED }))
    await waitFor(() => expect(sent(http).map((command) => command.outcome)).toEqual(['accept']))
  })

  it('W3 still sends a chosen accept not yet written after an earlier write of the case', async () => {
    const user = userEvent.setup()
    const http = owner(() => [written(true, false)])
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await user.click(await screen.findByRole('radio', { name: PROPOSED }))
    await waitFor(() => expect(sent(http).map((command) => command.outcome)).toEqual(['accept']))
  })

  it.each([
    ['its own write', false],
    ['no write', true],
  ] as const)(
    'W5 sends the chosen card again once Resume released an accept that needed %s',
    async (_, noChangeRequired) => {
      const user = userEvent.setup()
      const http = owner(() => [
        {
          ...written(true, !noChangeRequired),
          noChangeRequired,
          ownerControl: control({ withdraw: noChangeRequired ? null : 'written' }),
          pinControl: { pin: 'change_waiting', unpin: false },
        },
      ])
      renderRoute('/library?lens=all&preview=case-1&field=title', http)
      await user.click(await screen.findByRole('radio', { name: PROPOSED }))
      await waitFor(() => expect(sent(http).map((command) => command.outcome)).toEqual(['accept']))
    },
  )

  // An accept of the value the case's latest write already holds needed no
  // write of its own (ADR-0105).
  it.each([true, false])(
    'W4 says an accept needs no change, and its card sends nothing, only when it needs no write (%s)',
    async (noChangeRequired) => {
      const user = userEvent.setup()
      const http = owner(() => [{ ...written(true, false), noChangeRequired }])
      renderRoute('/library?lens=all&preview=case-1&field=title', http)
      const chosen = await screen.findByRole('radio', { name: PROPOSED })
      const box = screen.getByRole('complementary', { name: 'Writing to the library' })
      if (noChangeRequired) {
        expect(box).toHaveTextContent(
          /^Writing to the libraryNo change neededActivity lists it\.$/u,
        )
        await user.click(chosen)
        chosen.focus()
        await user.keyboard('{Enter}')
        expect(http.recordDecision).not.toHaveBeenCalled()
      } else {
        expect(box).toHaveTextContent('Change recorded')
        await user.click(chosen)
        await waitFor(() =>
          expect(sent(http).map((command) => command.outcome)).toEqual(['accept']),
        )
      }
    },
  )
})
