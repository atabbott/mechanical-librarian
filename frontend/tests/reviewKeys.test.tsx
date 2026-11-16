import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type {
  CaseDetailView,
  DecisionCommand,
  LibraryView,
  OwnerControlView,
  ReviewHttp,
} from '../src/api/http'
import { reconciliationKey } from '../src/reviewDecisionController'
import { reviewKey } from '../src/reviewKey'
import { createHttp, detail, library, recordedAcknowledgement, renderRoute } from './fixtures'

// Review keys (#1337): each key focuses and presses the control a pointer uses.
const present = <T,>(value: T | null | undefined): T => {
  if (value === null || value === undefined) throw new Error('the fixture is incomplete')
  return value
}
type Field = CaseDetailView['fields'][number]
const base = detail.fields[0]
if (!base) throw new Error('workspace fixture requires a title field')
const control = (value: Partial<OwnerControlView>): OwnerControlView => ({
  entry: null,
  subset: null,
  withdraw: null,
  ...value,
})
const entry = control({ entry: { maxCharacters: 512, suggestions: [] } })
// Title: Keep current value and one new value, so two cards.
const title: Field = { ...base, ownerControl: entry }
// Subtitle: Keep current value and two new values, so three cards.
const subtitle: Field = {
  ...base,
  destination: 'subtitle',
  currentValue: 'Old subtitle',
  currentRaw: 'Old subtitle',
  proposedValues: [
    { kind: 'text', value: 'First subtitle' },
    { kind: 'text', value: 'Second subtitle' },
  ],
  proposalId: 'proposal-subtitle',
  targetFingerprint: 'fingerprint-subtitle',
}
const enteredTitle: Field = {
  ...base,
  classification: 'human_recorded',
  classificationCause: 'human_decided',
  classificationReason: 'You decided this field. You can change it here.',
  decisionActions: [],
  needsAttention: false,
  navigationRole: 'inspect',
  proposedValues: [],
  humanDecision: {
    actor: 'owner',
    application: 'apply',
    application_reason: null,
    decision_id: 'decision-entered',
    destination: 'title',
    occurred_at: 1,
    outcome: 'owner_entered',
    proposal_id: base.proposalId,
    review_case_id: 'case-1',
    selected_value: { kind: 'text', value: 'The Silent Orchard' },
    target_fingerprint: base.targetFingerprint,
  },
  ownerControl: control({ ...entry, withdraw: 'available' }),
}
const genres: Field = {
  ...base,
  destination: 'genres',
  classification: 'unsupported',
  classificationCause: 'nothing_to_decide',
  classificationReason: 'No source offered a different value. You can enter your own.',
  decisionActions: [],
  needsAttention: false,
  navigationRole: 'inspect',
  currentValue: 'Drama',
  currentRaw: ['Drama'],
  proposedValues: [],
  proposalId: 'proposal-genres',
  targetFingerprint: 'fingerprint-genres',
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
const cover = (digit: string, incumbent: boolean) => ({
  artifactId: `cover_${digit.repeat(64)}`,
  candidateKind: 'candidate' as const,
  deterministicCitations: [`citation-${digit}`],
  deterministicRank: incumbent ? null : 0,
  deterministicRankKey: [1, 0, -960000, -800, -1200, incumbent ? 0 : 1, digit],
  height: 1200,
  identity: { outcome: 'verified_work_match' as const },
  incumbent,
  matchState: 'same_book' as const,
  observation: null,
  originalSha256: digit.repeat(64),
  ordinal: incumbent ? 0 : 1,
  previewSha256: digit.repeat(64),
  reference: `cover_ref_${digit.repeat(64)}`,
  selectable: !incumbent,
  manualEligible: !incumbent,
  sourceKinds: [incumbent ? ('epub' as const) : ('open_library' as const)],
  state: incumbent ? ('excluded' as const) : ('acceptable' as const),
  width: 800,
})
// Cover: the current cover and one found cover, so two cover cards.
const coverField: Field = {
  ...base,
  destination: 'cover',
  currentValue: `cover_${'1'.repeat(64)}`,
  currentRaw: `cover_${'1'.repeat(64)}`,
  proposedValues: [{ kind: 'cover', value: `cover_${'2'.repeat(64)}` }],
  proposalId: 'proposal-cover',
  targetFingerprint: 'fingerprint-cover',
  currentCoverSelectionId: null,
  coverConsideration: {
    id: 'cover_consideration_1',
    choiceContexts: [],
    current: true,
    candidates: [cover('1', true), cover('2', false)],
    incumbent: { reference: `cover_ref_${'1'.repeat(64)}`, state: 'available' },
    omissions: [],
    policy: null,
    policyPreferences: {
      avoidPromotionalText: false,
      avoidTieInBranding: false,
      preferMinimalComposition: false,
    },
    policyReference: `cover_policy_${'3'.repeat(64)}`,
    providerFetching: false,
    ready: true,
    reviewerAccessRevision: null,
    reviewerEngine: null,
    reviewerModel: null,
    reviewerStatus: 'unavailable',
    reviewerUnavailableReason: null,
    targetFingerprint: 'fingerprint-cover',
  },
}
const work = (id: string, name: string): LibraryView['works'][number] => ({
  ...present(library.works[0]),
  id,
  reviewCaseId: id,
  workId: `work-${id}`,
  title: name,
  detailUrl: `/review/${id}`,
})
const twoWorks: LibraryView = {
  ...library,
  counts: { attention: 2, all: 2, clear: 0, notMatched: 0, failed: 0 },
  works: [work('case-1', 'A Book'), work('case-2', 'Second Book')],
}

const store = (
  fields: () => readonly Field[],
  options: { works?: LibraryView; record?: ReviewHttp['recordDecision'] } = {},
) => {
  const recordDecision = vi.fn<ReviewHttp['recordDecision']>(
    options.record ??
      (async (caseId, command) => ({
        ...recordedAcknowledgement(
          { ...detail, id: caseId, fields: [...fields()] },
          {
            invalidation_sequences: [],
            schema_version: 'review-decision-v1',
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
        ),
        confirmation: {
          status: 'confirmed',
          case: { ...detail, id: caseId, fields: [...fields()] },
        },
      })),
  )
  return {
    ...createHttp(),
    getLibrary: vi.fn(async () => options.works ?? library),
    getAttention: vi.fn(async () => ({ total: 0, items: [] })),
    getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) => ({
      ...detail,
      id,
      title: id === 'case-2' ? 'Second Book' : detail.title,
      fields: [...fields()],
    })),
    recordDecision,
  }
}
const sent = (http: ReturnType<typeof store>): DecisionCommand[] =>
  http.recordDecision.mock.calls.map(([, command]) => command)
const openAt = async (http: ReviewHttp, field: string, preview = 'case-1') => {
  const rendered = renderRoute(`/library?lens=all&preview=${preview}&field=${field}`, http)
  await screen.findAllByRole('radio')
  return rendered
}
const tab = (name: string) =>
  within(screen.getByRole('navigation', { name: 'Review fields' })).getByRole('button', {
    name: new RegExp(`^${name}`, 'u'),
  })
const box = (left: number, top: number, width: number, height: number) =>
  ({ left, top, right: left + width, bottom: top + height, width, height }) as DOMRect
const press = (key: string, init: KeyboardEventInit = {}) =>
  act(async () => {
    fireEvent.keyDown(document.activeElement ?? document.body, { key, ...init })
  })

afterEach(() => {
  document.cookie = 'ml_review_keys=; Path=/; Max-Age=0'
})

describe('reviewKey', () => {
  const event = (init: KeyboardEventInit, target: EventTarget = document.body) => {
    const keyboard = new KeyboardEvent('keydown', { cancelable: true, ...init })
    Object.defineProperty(keyboard, 'target', { value: target })
    return keyboard
  }
  it('maps the review keys and ignores everything else', () => {
    expect(
      ['j', 'K', '1', '2', '3', 'e', 'U', '?'].map((key) => reviewKey(event({ key }))),
    ).toEqual(['j', 'k', '1', '2', '3', 'e', 'u', '?'])
    expect(['4', 'a', 'Enter', ' ', 'Escape'].map((key) => reviewKey(event({ key })))).toEqual([
      null,
      null,
      null,
      null,
      null,
    ])
  })
  it('refuses modifiers, repeats, composition and handled events', () => {
    for (const init of [
      { ctrlKey: true },
      { metaKey: true },
      { altKey: true },
      { repeat: true },
      { isComposing: true },
      { keyCode: 229 },
    ])
      expect(reviewKey(event({ key: '1', ...init }))).toBeNull()
    expect(reviewKey(event({ key: 'J', shiftKey: true }))).toBeNull()
    expect(reviewKey(event({ key: '1', shiftKey: true }))).toBe('1')
    expect(reviewKey(event({ key: '?', shiftKey: true }))).toBe('?')
    const handled = event({ key: '1' })
    handled.preventDefault()
    expect(reviewKey(handled)).toBeNull()
  })
  it('acts only from the body or inside Library, never while typing', () => {
    const page = document.createElement('div')
    page.className = 'library-page'
    page.innerHTML =
      '<button>b</button><input><textarea></textarea><select></select><div contenteditable="true"></div><form><button>f</button></form><aside class="attention-rail"><button>r</button></aside><dialog class="review-keys-dialog"><button>d</button></dialog>'
    const outside = document.createElement('button')
    document.body.append(page, outside)
    const [button, ...typing] = [
      ...page.querySelectorAll('button, input, textarea, select, [contenteditable]'),
    ]
    expect(reviewKey(event({ key: 'j' }, button))).toBe('j')
    for (const target of typing) expect(reviewKey(event({ key: 'j' }, target))).toBeNull()
    expect(reviewKey(event({ key: 'j' }, outside))).toBeNull()
    page.remove()
    outside.remove()
  })
})

describe('review keys', () => {
  it('1 records exactly what a click on the first card records', async () => {
    const user = userEvent.setup()
    const clicked = store(() => [title])
    await openAt(clicked, 'title')
    await user.click(present(screen.getAllByRole('radio')[0]))
    await waitFor(() => expect(clicked.recordDecision).toHaveBeenCalledTimes(1))
    cleanup()
    const keyed = store(() => [title])
    await openAt(keyed, 'title')
    await press('1')
    await waitFor(() => expect(keyed.recordDecision).toHaveBeenCalledTimes(1))
    const withoutKey = (commands: DecisionCommand[]) =>
      commands.map((command) => ({ ...command, idempotencyKey: undefined }))
    expect(withoutKey(sent(keyed))).toEqual(withoutKey(sent(clicked)))
    expect(sent(keyed)[0]).toMatchObject({ outcome: 'keep_current' })
  })

  it('2 records the first new value, and 3 with two cards records nothing', async () => {
    const http = store(() => [title])
    await openAt(http, 'title')
    await press('3')
    expect(http.recordDecision).not.toHaveBeenCalled()
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(sent(http)[0]).toMatchObject({
      outcome: 'accept',
      selectedValue: { kind: 'text', value: 'New title' },
    })
  })

  it('3 records the second new value on a field with three cards', async () => {
    const http = store(() => [subtitle])
    await openAt(http, 'subtitle')
    await press('3')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(sent(http)[0]).toMatchObject({
      selectedValue: { kind: 'text', value: 'Second subtitle' },
    })
  })

  it('numbers the first three cards with hidden badges and leaves radio names alone', async () => {
    const fourCards = {
      ...subtitle,
      proposedValues: [...subtitle.proposedValues, { kind: 'text' as const, value: 'Third' }],
    }
    await openAt(
      store(() => [fourCards]),
      'subtitle',
    )
    const radios = screen.getAllByRole('radio')
    expect(
      radios.map((radio) => radio.querySelector('kbd[aria-hidden="true"]')?.textContent),
    ).toEqual(['1', '2', '3', undefined])
    expect(radios.map((radio) => radio.getAttribute('aria-label'))).toEqual([
      'Choose current value: Old subtitle',
      'Choose proposed value: First subtitle',
      'Choose proposed value: Second subtitle',
      'Choose proposed value: Third',
    ])
    expect(document.documentElement.dataset.reviewKeys).toBe('on')
  })

  it('numbers cover cards the same way, and a digit presses the cover with that number', async () => {
    const http = store(() => [coverField])
    await openAt(http, 'cover')
    const radios = screen.getAllByRole('radio')
    expect(
      radios.map((radio) => radio.querySelector('kbd[aria-hidden="true"]')?.textContent),
    ).toEqual(['1', '2'])
    expect(radios.map((radio) => radio.getAttribute('aria-label'))).toEqual([
      'Keep current cover',
      'Choose Open Library cover',
    ])
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(sent(http)[0]).toMatchObject({
      destination: 'cover',
      selectedValue: { kind: 'cover', value: `cover_${'2'.repeat(64)}` },
    })
  })

  it('shows no badges and takes no key in the Covers view', async () => {
    renderRoute('/library?lens=all&view=covers')
    await screen.findByRole('heading', { level: 1, name: 'Library' })
    expect(document.documentElement.dataset.reviewKeys).toBeUndefined()
    await press('?')
    expect(document.querySelector('dialog')).toBeNull()
  })

  it('J and K step through the field tabs, focus the tab and update the URL', async () => {
    const http = store(() => [title, subtitle])
    const { router } = await openAt(http, 'title')
    await press('j')
    await waitFor(() => expect(router.state.location.search.field).toBe('subtitle'))
    expect(tab('Subtitle')).toHaveFocus()
    expect(tab('Subtitle')).toHaveAttribute('aria-current', 'true')
    await press('k')
    await waitFor(() => expect(router.state.location.search.field).toBe('title'))
    expect(tab('Title')).toHaveFocus()
  })

  it('J past the last tab opens the next work and K before the first opens the previous one', async () => {
    const http = store(() => [title, subtitle], { works: twoWorks })
    const { router } = await openAt(http, 'title')
    // The first work on the page: K before its first tab does nothing.
    await press('k')
    expect(router.state.location.search).toMatchObject({ preview: 'case-1', field: 'title' })
    await press('j')
    await waitFor(() => expect(router.state.location.search.field).toBe('subtitle'))
    await press('j')
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    expect(await screen.findByRole('heading', { name: 'Second Book' })).toBeVisible()
    await screen.findAllByRole('radio')
    // The last work: J past its last tab does nothing.
    await press('j')
    await waitFor(() => expect(router.state.location.search.field).toBe('subtitle'))
    await press('j')
    expect(router.state.location.search.preview).toBe('case-2')
    await press('k')
    await waitFor(() => expect(router.state.location.search.field).toBe('title'))
    await press('k')
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-1'))
  })

  it('skips a row that does not open and passes straight through a work with no case', async () => {
    const noCase = work('plain', 'Plain Book')
    delete noCase.reviewCaseId
    const works: LibraryView = {
      ...twoWorks,
      works: [
        present(twoWorks.works[0]),
        // Could not finish with no report: an article, never a row that opens.
        {
          ...noCase,
          id: 'failed',
          workId: 'work-failed',
          detailUrl: null,
          detailKind: null,
          attention: 'preparation_failed',
          preparation: {
            attempt: 1,
            checkGivenUp: false,
            diagnosticReference: 'prep-0123456789abcdef',
            mediaHealth: [],
            provider: 'hardcover-main',
            reason: 'A source refused access.',
            recovery: 'Scan this library again.',
            recoveryAttempt: 0,
            stage: 'provider',
            status: 'authentication',
          },
        },
        { ...noCase, detailUrl: null, detailKind: null },
        present(twoWorks.works[1]),
      ],
    }
    const { router } = await openAt(
      store(() => [title], { works }),
      'title',
    )
    expect(document.querySelector('article.library-work-row')).not.toBeNull()
    await press('j')
    await waitFor(() => expect(router.state.location.search.work).toBe('work-plain'))
    expect(router.state.location.search.preview).toBeUndefined()
    await press('j')
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-2'))
    await screen.findAllByRole('radio')
    await press('k')
    await waitFor(() => expect(router.state.location.search.work).toBe('work-plain'))
    await press('k')
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-1'))
  })

  it('does not move while the page is busy, the list is held or its work is not listed', async () => {
    const http = store(() => [title], { works: twoWorks })
    const { router } = await openAt(http, 'title', 'case-2')
    const main = present(document.querySelector('#main-content'))
    main.setAttribute('aria-busy', 'true')
    await press('k')
    main.setAttribute('aria-busy', 'false')
    const previous = screen.getByRole('button', { name: /^A Book\./u })
    previous.setAttribute('aria-disabled', 'true')
    await press('k')
    previous.removeAttribute('aria-disabled')
    // At the end of the tabs with no current row, neither key picks a row.
    screen.getByRole('button', { name: /^Second Book\./u }).removeAttribute('aria-current')
    await press('j')
    await press('k')
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(router.state.location.search).toMatchObject({ preview: 'case-2', field: 'title' })
  })

  it('J with no work open opens the first work and K does nothing', async () => {
    const http = store(() => [title], { works: twoWorks })
    const { router } = renderRoute('/library?lens=all', http)
    await screen.findByRole('button', { name: /^Second Book\./u })
    await press('k')
    expect(router.state.location.search.preview).toBeUndefined()
    await press('j')
    await waitFor(() => expect(router.state.location.search.preview).toBe('case-1'))
  })

  it('on Review complete, J presses Review next work and K reopens the last field', async () => {
    let fields: readonly Field[] = [title]
    const http = {
      ...store(() => fields),
      getAttention: vi.fn(async () => ({
        total: 1,
        items: [
          {
            id: 'case-2',
            destination: 'title' as const,
            title: 'Second Book',
            authors: [],
            library: 'Books',
            mediaKind: 'epub' as const,
            notices: [],
            provenance: ['Local catalogue'],
            workAttentionCount: 1,
          },
        ],
      })),
    }
    const { router } = await openAt(http, 'title')
    fields = [
      {
        ...title,
        needsAttention: false,
        classification: 'human_recorded',
        humanDecision: enteredTitle.humanDecision,
      },
    ]
    await press('1')
    expect(await screen.findByText('Review complete')).toBeVisible()
    await press('k')
    await waitFor(() => expect(screen.queryByText('Review complete')).toBeNull())
    expect(router.state.location.search).toMatchObject({ preview: 'case-1', field: 'title' })
    expect(tab('Title')).toHaveFocus()
    cleanup()
    fields = [title]
    const again = await openAt(http, 'title')
    fields = [
      {
        ...title,
        needsAttention: false,
        classification: 'human_recorded',
        humanDecision: enteredTitle.humanDecision,
      },
    ]
    await press('1')
    expect(await screen.findByRole('button', { name: 'Review next work →' })).toBeVisible()
    await press('j')
    await waitFor(() => expect(again.router.state.location.search.preview).toBe('case-2'))
  })

  it('E opens owner entry with focus in an empty input, and no e is typed', async () => {
    const http = store(() => [title])
    await openAt(http, 'title')
    await press('e')
    const input = await screen.findByRole('textbox', { name: 'Your value for Title' })
    expect(input).toHaveFocus()
    expect(input).toHaveValue('')
  })

  it('E on a recorded value opens Change prefilled, and on genres opens the subset', async () => {
    renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      store(() => [enteredTitle, subtitle]),
    )
    await screen.findByRole('button', { name: 'Change' })
    await press('e')
    expect(await screen.findByRole('textbox', { name: 'Your value for Title' })).toHaveValue(
      'The Silent Orchard',
    )
    cleanup()
    renderRoute(
      '/library?lens=all&preview=case-1&field=genres',
      store(() => [genres]),
    )
    await screen.findByRole('button', { name: 'Choose genres' })
    await press('e')
    expect(await screen.findByRole('checkbox', { name: /Drama/u })).toHaveFocus()
  })

  it('E with no entry and U with no Withdraw do nothing', async () => {
    const http = store(() => [subtitle])
    await openAt(http, 'subtitle')
    await press('e')
    await press('u')
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('U withdraws once and focuses the outcome line', async () => {
    let fields: readonly Field[] = [enteredTitle]
    const http = store(() => fields)
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await screen.findByRole('button', { name: 'Withdraw' })
    fields = [{ ...title, needsAttention: false }]
    await press('u')
    const outcome = await screen.findByText('Withdrawn. This field needs a decision again.')
    await waitFor(() => expect(outcome).toHaveFocus())
    expect(sent(http)).toEqual([
      expect.objectContaining({ outcome: 'withdraw', selectedValue: null }),
    ])
  })

  it('? and Keys open the key list; Escape and Close close it and return focus', async () => {
    const user = userEvent.setup()
    await openAt(
      store(() => [title]),
      'title',
    )
    const dialog = present(document.querySelector('dialog'))
    tab('Title').focus()
    const scrolled = vi.spyOn(dialog, 'scrollTop', 'set')
    await press('?')
    expect(dialog.open).toBe(true)
    expect(scrolled.mock.calls).toEqual([[0]])
    expect(within(dialog).getByRole('heading', { name: 'Keys' })).toBeInTheDocument()
    expect(dialog).toHaveAccessibleName('Keys')
    // It opens at its top: focus is on the heading, and Tab goes on to the controls.
    const heading = within(dialog).getByRole('heading', { name: 'Keys' })
    expect(heading).toHaveFocus()
    expect(heading).toHaveAttribute('tabindex', '-1')
    await user.tab()
    expect(within(dialog).getByRole('checkbox', { name: 'Use these keys' })).toHaveFocus()
    await user.tab()
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus()
    // While the list is open no other key acts.
    within(dialog).getByRole('checkbox', { name: 'Use these keys' }).focus()
    await press('2')
    await act(async () => {
      fireEvent.keyDown(document.body, { key: '2' })
    })
    await user.keyboard('{Escape}')
    expect(dialog.open).toBe(false)
    expect(tab('Title')).toHaveFocus()
    const keys = screen.getByRole('button', { name: 'Keys' })
    await user.click(keys)
    expect(dialog.open).toBe(true)
    expect(heading).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(dialog.open).toBe(false)
    expect(keys).toHaveFocus()
    expect(screen.getAllByRole('radio')[1]).toHaveAttribute('aria-checked', 'false')
  })

  it('does nothing while typing in search or in the owner input', async () => {
    const user = userEvent.setup()
    const http = store(() => [title])
    const { router } = await openAt(http, 'title')
    const search = screen.getByRole('searchbox')
    await user.click(search)
    await user.keyboard('jk1eu?')
    expect(search).toHaveValue('jk1eu?')
    expect(document.querySelector('dialog')?.open).toBe(false)
    await user.clear(search)
    await waitFor(() => expect(router.state.location.search.q).toBeUndefined())
    await user.click(screen.getByRole('button', { name: 'Enter a value' }))
    const input = screen.getByRole('textbox', { name: 'Your value for Title' })
    await user.keyboard('jk1eu?')
    expect(input).toHaveValue('jk1eu?')
    expect(http.recordDecision).not.toHaveBeenCalled()
    expect(router.state.location.search.field).toBe('title')
  })

  it('does nothing with a modifier, a repeat or composition', async () => {
    const http = store(() => [title, subtitle])
    const { router } = await openAt(http, 'title')
    for (const init of [
      { ctrlKey: true },
      { metaKey: true },
      { altKey: true },
      { repeat: true },
      { isComposing: true },
      { keyCode: 229 },
    ]) {
      await press('1', init)
      await press('j', init)
    }
    await press('J', { shiftKey: true })
    expect(http.recordDecision).not.toHaveBeenCalled()
    expect(router.state.location.search.field).toBe('title')
  })

  it('does nothing while Attention is open or a masthead control is expanded', async () => {
    const user = userEvent.setup()
    const http = store(() => [title])
    await openAt(http, 'title')
    const expanded = document.createElement('button')
    expanded.setAttribute('aria-expanded', 'true')
    document.querySelector('.masthead')?.append(expanded)
    await press('2')
    await press('?')
    expanded.setAttribute('aria-haspopup', 'listbox')
    document.body.append(expanded)
    await press('2')
    await press('?')
    expanded.remove()
    await user.click(screen.getByRole('button', { name: /^Attention/u }))
    await press('2')
    await press('?')
    expect(document.querySelector('dialog')?.open).toBe(false)
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('sends nothing more while a choice records, checks or needs a reload', async () => {
    let release: () => void = () => undefined
    const http = store(() => [title], {
      record: () =>
        new Promise((_, reject) => {
          release = () => reject(new Error('unanswered'))
        }),
    })
    await openAt(http, 'title')
    await press('2')
    await press('1')
    expect(http.recordDecision).toHaveBeenCalledTimes(1)
    release()
    cleanup()
    // Checking and reload-required hold J as well as the choices.
    const checking = store(() => [title, subtitle])
    checking.getCaseDetail = vi.fn(async () => ({
      ...detail,
      checking: true,
      fields: [title, subtitle],
    }))
    const held = await openAt(checking, 'title')
    await press('2')
    await press('j')
    expect(checking.recordDecision).not.toHaveBeenCalled()
    expect(held.router.state.location.search.field).toBe('title')
    cleanup()
    const reload = store(() => [title, subtitle])
    const { queryClient, router } = await openAt(reload, 'title')
    await act(async () => {
      queryClient.setQueryData(reconciliationKey('case-1'), {
        destination: 'title',
        message: 'Reload first.',
        detailUpdatedAt: Number.MAX_SAFE_INTEGER,
      })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    await screen.findByRole('button', { name: 'Reload current case' })
    await press('j')
    await press('2')
    expect(router.state.location.search.field).toBe('title')
    expect(reload.recordDecision).not.toHaveBeenCalled()
  })

  it('does not move while the selected work is loading', async () => {
    const http = store(() => [title], { works: twoWorks })
    http.getCaseDetail = vi.fn(() => new Promise<CaseDetailView>(() => undefined))
    const { router } = renderRoute('/library?lens=all&preview=case-1', http)
    await screen.findByText('Loading selected work…')
    await press('j')
    expect(router.state.location.search.preview).toBe('case-1')
  })

  it('brings an off-screen card into view first, and the next press records it', async () => {
    const http = store(() => [title])
    await openAt(http, 'title')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.metadata-choice'))
    // Its top edge is inside the window, its centre is not.
    const rect = vi
      .spyOn(card, 'getBoundingClientRect')
      .mockReturnValue(box(40, innerHeight - 20, 300, 160))
    await press('2')
    expect(card).toHaveFocus()
    // The whole card is shown, with its source line and Evidence link.
    const reveal = vi.mocked(whole.scrollIntoView).mock
    expect(reveal.contexts).toEqual([whole])
    expect(reveal.calls).toEqual([[{ block: 'nearest', inline: 'nearest' }]])
    expect(http.recordDecision).not.toHaveBeenCalled()
    // Taller than the window, it is shown from its top.
    vi.spyOn(whole, 'offsetHeight', 'get').mockReturnValue(innerHeight + 1)
    await press('2')
    expect(reveal.calls[1]).toEqual([{ block: 'start', inline: 'nearest' }])
    expect(http.recordDecision).not.toHaveBeenCalled()
    // With its centre in the window it records, though its end is still cut off.
    rect.mockReturnValue(box(40, innerHeight - 100, 300, 160))
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  })

  it('shows a cover the strip hides to one side before a digit records it', async () => {
    const http = store(() => [coverField])
    await openAt(http, 'cover')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.cover-choice'))
    // A strip 390 wide that scrolls sideways; the window is wider and taller.
    const strip = present(whole.parentElement)
    strip.style.overflowX = 'auto'
    vi.spyOn(strip, 'clientWidth', 'get').mockReturnValue(390)
    vi.spyOn(strip, 'clientHeight', 'get').mockReturnValue(400)
    vi.spyOn(strip, 'getBoundingClientRect').mockReturnValue(box(0, 100, 390, 400))
    expect(innerWidth).toBeGreaterThan(700)
    const rect = vi.spyOn(card, 'getBoundingClientRect').mockReturnValue(box(480, 120, 240, 360))
    await press('2')
    expect(card).toHaveFocus()
    const reveal = vi.mocked(whole.scrollIntoView).mock
    expect(reveal.contexts).toEqual([whole])
    expect(reveal.calls).toEqual([[{ block: 'nearest', inline: 'nearest' }]])
    expect(http.recordDecision).not.toHaveBeenCalled()
    // Half of the card is past the strip's edge, but its centre is inside.
    rect.mockReturnValue(box(260, 120, 240, 360))
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  })

  it('turns every key off, ? included, and keeps that choice through a remount', async () => {
    const user = userEvent.setup()
    const http = store(() => [title, subtitle])
    const { router } = await openAt(http, 'title')
    await press('?')
    const dialog = present(document.querySelector('dialog'))
    const toggle = within(dialog).getByRole('checkbox', { name: 'Use these keys' })
    expect(toggle).toBeChecked()
    const kept = vi.spyOn(document, 'cookie', 'set')
    await user.click(toggle)
    expect(kept).toHaveBeenCalledWith(
      'ml_review_keys=off; Path=/; Max-Age=31536000; SameSite=Strict',
    )
    kept.mockRestore()
    expect(document.cookie).toContain('ml_review_keys=off')
    expect(document.documentElement.dataset.reviewKeys).toBe('off')
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    for (const key of ['1', 'j', 'e', '?']) await press(key)
    expect(dialog.open).toBe(false)
    expect(http.recordDecision).not.toHaveBeenCalled()
    expect(router.state.location.search.field).toBe('title')
    cleanup()
    const again = store(() => [title])
    await openAt(again, 'title')
    expect(document.documentElement.dataset.reviewKeys).toBe('off')
    await press('2')
    expect(again.recordDecision).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Keys' }))
    await user.click(screen.getByRole('checkbox', { name: 'Use these keys' }))
    expect(document.cookie).not.toContain('ml_review_keys')
    expect(document.documentElement.dataset.reviewKeys).toBe('on')
  })
})
