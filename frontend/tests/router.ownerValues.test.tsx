import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type { CaseDetailView, DecisionCommand, OwnerControlView, ReviewHttp } from '../src/api/http'
import { coalesceMilliseconds } from '../src/liveInvalidation'
import {
  createHttp,
  createLiveStream,
  detail,
  library,
  recordedAcknowledgement,
  renderRoute,
} from './fixtures'

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
        'This decision is being written to your library. You can change it once the write finishes.',
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
        expect(
          await screen.findByText('Withdrawn. This field needs a decision again.'),
        ).toBeVisible()
      }
      // The Library result read after the command no longer lists the work;
      // its case read still names it (#1365).
      await waitFor(() => expect(screen.queryByRole('button', { name: /^A Book\./u })).toBeNull())
      expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
      expect(screen.getByRole('region', { name: 'Your value for Title' })).toBeVisible()
      expect(screen.queryByText(/not available in the current Library result/u)).toBeNull()
      if (outcome === 'withdrawn')
        await waitFor(() =>
          expect(screen.getByRole('heading', { level: 2, name: 'Title' })).toHaveFocus(),
        )
    },
  )

  it.each([
    [
      'automatic_hold',
      'This decision is holding automatic handling, so it can’t be withdrawn. Use Resume automatic handling to release it.',
    ],
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

describe('owner review fixes', () => {
  const heading = () => screen.getByRole('heading', { level: 2, name: 'Title' })

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
      manualHold: true,
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-1',
        destination: 'title',
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
        'An earlier owner decision is pausing automatic handling until you choose Resume automatic handling.',
      ),
    ).toBeVisible()
    expect(document.querySelector('[aria-pressed="true"]')).toBeNull()
    // Nothing is marked, so the header does not say something is.
    expect(screen.getByText(/Your earlier decision still holds this field\./u)).toBeVisible()
    expect(screen.queryByText(/The recorded outcome is marked below/u)).toBeNull()
    const resolved = screen.getByText('Resolved automatically', { selector: 'dt' })
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
    expect(screen.queryByText(/not available in the current Library result/u)).toBeNull()
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
        expect(frames.filter((frame) => frame.includes('not available in the current'))).toEqual([])
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
        const unavailable = (frame: string) => frame.includes('not available in the current')
        expect(moveEnded).toBeDefined()
        expect(frames.slice(0, moveEnded).filter(unavailable)).toEqual([])
        expect(frames.slice(moveEnded).some(unavailable)).toBe(true)
        return
      }
      await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
      await screen.findByRole('region', { name: 'Your value for Title' })
      observer.disconnect()
      expect(frames.some((frame) => frame.includes('Loading selected work…'))).toBe(true)
      expect(frames.filter((frame) => frame.includes('not available in the current'))).toEqual([])
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
    expect(screen.queryByText(/not available in the current Library result/u)).toBeNull()
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
      destination: 'genres',
      currentRaw: ['Drama'],
      ownerControl: control({
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
    await user.click(within(panel).getByRole('button', { name: 'Choose genres' }))
    expect(within(panel).getByRole('checkbox', { name: /Drama/u })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(within(panel).getByRole('button', { name: 'Choose genres' })).toHaveFocus()
  })

  it('S2 ties a refusal to the input it is about', async () => {
    const user = userEvent.setup()
    const panel = await open(owner(() => [contextTitle]))
    await user.click(within(panel).getByRole('button', { name: 'Enter a value' }))
    const input = within(panel).getByRole('textbox', { name: 'Your value for Title' })
    await user.click(within(panel).getByRole('button', { name: 'Record this value' }))
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(/Enter a value first\./u)
    expect(within(panel).getAllByRole('alert')).toHaveLength(1)
    await user.keyboard('{Escape}')
    expect(within(panel).getByRole('button', { name: 'Enter a value' })).toHaveFocus()
  })

  it('S4 announces an owner command once, in its own words', async () => {
    const user = userEvent.setup()
    const genres: Field = {
      ...base,
      destination: 'genres',
      decisionActions: ['keep_current', 'accept'],
      currentValue: 'Drama',
      currentRaw: ['Drama'],
      proposedValues: [{ kind: 'list', value: ['Fantasy'] }],
      ownerControl: control({
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
    await user.click(within(panel).getByRole('button', { name: 'Choose genres' }))
    await user.click(within(panel).getByRole('checkbox', { name: /Fantasy/u }))
    fields = [
      {
        ...genres,
        needsAttention: false,
        humanDecision: {
          ...decision('owner_entered', { kind: 'list', value: ['Drama', 'Fantasy'] }),
          destination: 'genres',
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
          destination: 'title',
          outcome: 'withdraw',
          previousValue: { kind: 'text', value: 'The Silent Orchard' },
          selectedValue: null,
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
    const article = (await screen.findByRole('heading', { name: 'Title' })).closest('article')
    expect(article).toHaveTextContent('Withdrawn')
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
