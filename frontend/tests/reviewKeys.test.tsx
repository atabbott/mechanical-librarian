import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type {
  CaseDetailView,
  DecisionCommand,
  LibraryView,
  OwnerControlView,
  ReviewHttp,
} from '../src/api/http'
import { reconciliationKey } from '../src/reviewDecisionController'
import { reviewKey } from '../src/reviewKey'
import {
  createHttp,
  detail,
  library,
  recordedAcknowledgement,
  renderRoute,
  loadRouteModules,
} from './fixtures'

beforeAll(loadRouteModules)

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
  names: null,
  clear: false,
  current: null,
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
  counts: { attention: 2, all: 2, clear: 0, notMatched: 0, failed: 0, flagged: 0, new: 0 },
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
// A scrolling page for mocked boxes. Each box is placed where it lies with
// nothing scrolled, and scrollIntoView moves the page, or the sideways strip
// for a box inside it, by the standard's rules and the shell's scroll padding.
// So a test can press a key, let the reveal happen, and press it again.
const align = (
  mode: ScrollLogicalPosition,
  start: number,
  end: number,
  from: number,
  to: number,
) =>
  mode === 'start'
    ? start - from
    : mode === 'center'
      ? (start + end) / 2 - (from + to) / 2
      : (start >= from && end <= to) || (start <= from && end >= to)
        ? 0
        : start < from === end - start <= to - from
          ? start - from
          : end - to
const scrollingPage = (paddingTop: number, strip?: { box: HTMLElement; at: DOMRect }) => {
  const scrolled = { x: 0, y: 0 }
  const placed = new Map<Element, () => DOMRect>()
  const sideways = new Set<Element>()
  const place = (element: HTMLElement, at: DOMRect, inStrip = false) => {
    const rect = () =>
      box(at.left - (inStrip ? scrolled.x : 0), at.top - scrolled.y, at.width, at.height)
    placed.set(element, rect)
    if (inStrip) sideways.add(element)
    vi.spyOn(element, 'getBoundingClientRect').mockImplementation(rect)
    vi.spyOn(element, 'offsetHeight', 'get').mockReturnValue(at.height)
  }
  if (strip) {
    place(strip.box, strip.at)
    strip.box.style.overflowX = 'auto'
    vi.spyOn(strip.box, 'clientWidth', 'get').mockReturnValue(strip.at.width)
    vi.spyOn(strip.box, 'clientHeight', 'get').mockReturnValue(strip.at.height)
  }
  vi.mocked(Element.prototype.scrollIntoView).mockImplementation(function (this: Element, options) {
    // The product also scrolls boxes of its own after a decision.
    const rect = placed.get(this)?.()
    if (!rect) return
    const { block = 'start', inline = 'nearest' } = typeof options === 'object' ? options : {}
    // The shell keeps 1rem clear at the foot of the window.
    scrolled.y += align(block, rect.top, rect.bottom, paddingTop, innerHeight - 16)
    if (strip && sideways.has(this))
      scrolled.x += align(inline, rect.left, rect.right, strip.at.left, strip.at.right)
  })
  vi.mocked(scrollBy).mockImplementation((...by: [ScrollToOptions?] | [number, number]) => {
    if (typeof by[1] === 'number') scrolled.y += by[1]
  })
  return {
    place,
    scrollTo: (y: number) => {
      scrolled.y = y
    },
  }
}
const masthead = (mode: 'sticky' | 'static', clearance: number) => {
  document.documentElement.style.setProperty('--masthead-clearance', `${String(clearance)}px`)
  if (mode === 'static') document.documentElement.dataset.masthead = 'static'
}
const press = (key: string, init: KeyboardEventInit = {}) =>
  act(async () => {
    fireEvent.keyDown(document.activeElement ?? document.body, { key, ...init })
  })

afterEach(() => {
  document.cookie = 'ml_review_keys=; Path=/; Max-Age=0'
  document.documentElement.style.removeProperty('--masthead-clearance')
  delete document.documentElement.dataset.masthead
  document.querySelector('body > dialog')?.remove()
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
    // The first-visit picks introduction is a dialog too; it is seen here so
    // only a key help dialog could appear.
    document.cookie = 'ml_covers_intro=seen; Path=/'
    try {
      renderRoute('/library?lens=all&view=covers')
      await screen.findByRole('heading', { level: 1, name: 'Library' })
      expect(document.documentElement.dataset.reviewKeys).toBeUndefined()
      await press('?')
      expect(document.querySelector('dialog')).toBeNull()
    } finally {
      document.cookie = 'ml_covers_intro=; Path=/; Max-Age=0'
    }
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
    // A tab that cannot be pressed is not pressed.
    tab('Subtitle').setAttribute('aria-disabled', 'true')
    await press('j')
    expect(tab('Title')).toHaveFocus()
    expect(router.state.location.search.field).toBe('title')
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
    const next = await screen.findByRole('button', { name: 'Review next work →' })
    expect(next).toBeVisible()
    // Hidden, it is not pressed.
    next.hidden = true
    await press('j')
    expect(again.router.state.location.search.preview).toBe('case-1')
    next.hidden = false
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
    await screen.findByRole('button', { name: 'Change' })
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
    // Safari clicks a button without focusing it: focus still comes back to Keys.
    tab('Title').focus()
    fireEvent.click(keys)
    expect(dialog.open).toBe(true)
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(keys).toHaveFocus()
    // Keys is not shown on a phone or tablet once the keys are on: its toolbar takes focus.
    await user.click(keys)
    keys.style.display = 'none'
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(dialog.open).toBe(false)
    expect(screen.getByRole('banner', { name: 'Library controls' })).toHaveFocus()
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

  it('does nothing under any open dialog, and acts again once it closes', async () => {
    const http = store(() => [
      { ...title, ownerControl: control({ ...entry, withdraw: 'available' }) },
      subtitle,
    ])
    const { router } = await openAt(http, 'title')
    const other = document.createElement('dialog')
    other.setAttribute('open', '')
    document.body.append(other)
    for (const key of ['2', 'e', 'u', 'j', '?']) await press(key)
    expect(http.recordDecision).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox', { name: 'Your value for Title' })).toBeNull()
    expect(router.state.location.search.field).toBe('title')
    expect(document.querySelector<HTMLDialogElement>('.review-keys-dialog')?.open).toBe(false)
    other.remove()
    await press('j')
    await waitFor(() => expect(router.state.location.search.field).toBe('subtitle'))
  })

  it('does nothing while owner entry is open, wherever focus is', async () => {
    const user = userEvent.setup()
    const http = store(() => [
      { ...title, ownerControl: control({ ...entry, withdraw: 'available' }) },
      subtitle,
    ])
    const { router } = await openAt(http, 'title')
    await user.click(screen.getByRole('button', { name: 'Enter a value' }))
    const input = screen.getByRole('textbox', { name: 'Your value for Title' })
    expect(input.closest('.library-page form.owner-entry')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Withdraw' })).toBeVisible()
    input.blur()
    expect(document.body).toHaveFocus()
    for (const key of ['2', 'u', 'j', '?']) await press(key)
    expect(http.recordDecision).not.toHaveBeenCalled()
    expect(router.state.location.search.field).toBe('title')
    expect(document.querySelector<HTMLDialogElement>('.review-keys-dialog')?.open).toBe(false)
    // Closed again, the keys act.
    input.focus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.querySelector('form.owner-entry')).toBeNull())
    await press('j')
    await waitFor(() => expect(router.state.location.search.field).toBe('subtitle'))
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

  it('shows a card that is not wholly in view first, and the next press records it', async () => {
    const http = store(() => [title])
    await openAt(http, 'title')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.metadata-choice'))
    masthead('sticky', 100)
    const page = scrollingPage(100)
    // More than half of the control is in the window; its end is not.
    page.place(whole, box(40, innerHeight - 100, 300, 200))
    page.place(card, box(40, innerHeight - 100, 300, 160))
    await press('2')
    expect(card).toHaveFocus()
    // The whole card is shown, with its source line and Evidence link.
    const reveal = vi.mocked(whole.scrollIntoView).mock
    expect(reveal.contexts).toEqual([whole])
    expect(reveal.calls).toEqual([[{ block: 'nearest', inline: 'nearest' }]])
    expect(card.getBoundingClientRect().bottom).toBeLessThan(innerHeight)
    expect(http.recordDecision).not.toHaveBeenCalled()
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  })

  it('lets a control one pixel past the edge count as seen, and not two', async () => {
    const http = store(() => [title])
    await openAt(http, 'title')
    const card = present(screen.getAllByRole('radio')[1])
    const page = scrollingPage(8)
    page.place(present(card.closest<HTMLElement>('.metadata-choice')), box(40, 300, 300, 200))
    page.place(card, box(40, innerHeight - 158, 300, 160))
    await press('2')
    expect(card).toHaveFocus()
    expect(http.recordDecision).not.toHaveBeenCalled()
    // Back where the page was, one pixel over the edge.
    page.scrollTo(0)
    page.place(card, box(40, innerHeight - 159, 300, 160))
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  })

  it.each([
    ['below the window', 'sticky', 100, 2000],
    ['above the window under a sticky masthead', 'sticky', 100, -600],
    ['above the window with a masthead that scrolls away', 'static', 8, -600],
  ] as const)(
    'shows a card %s on the first press and records it on the second',
    async (_, mode, paddingTop, top) => {
      const http = store(() => [title])
      await openAt(http, 'title')
      const card = present(screen.getAllByRole('radio')[1])
      const whole = present(card.closest<HTMLElement>('.metadata-choice'))
      masthead(mode, 100)
      const page = scrollingPage(paddingTop)
      page.place(whole, box(40, top, 300, 200))
      page.place(card, box(40, top, 300, 160))
      await press('2')
      expect(card).toHaveFocus()
      // Bringing the card into view is enough to show its control.
      expect(vi.mocked(whole.scrollIntoView).mock.contexts).toEqual([whole])
      expect(http.recordDecision).not.toHaveBeenCalled()
      await press('2')
      await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    },
  )

  it('counts the band under a sticky masthead as covered, and under one that scrolls away as seen', async () => {
    const http = store(() => [title])
    await openAt(http, 'title')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.metadata-choice'))
    masthead('sticky', 100)
    const page = scrollingPage(100)
    page.place(whole, box(40, 30, 300, 100))
    page.place(card, box(40, 30, 300, 60))
    await press('2')
    expect(card).toHaveFocus()
    expect(card.getBoundingClientRect().top).toBe(100)
    expect(http.recordDecision).not.toHaveBeenCalled()
    // The same place in a window whose masthead has scrolled away with the page.
    page.scrollTo(0)
    masthead('static', 100)
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(
      vi.mocked(whole.scrollIntoView).mock.contexts.filter((box) => box === whole),
    ).toHaveLength(1)
  })

  it('shows the control itself when its card is taller than the window', async () => {
    // A long description comes before its control in the card.
    const description: Field = {
      ...base,
      destination: 'description',
      currentValue: 'Old words. '.repeat(40),
      currentRaw: 'Old words. '.repeat(40),
      proposedValues: [{ kind: 'text', value: 'New words. '.repeat(40) }],
    }
    const http = store(() => [description])
    await openAt(http, 'description')
    const card = screen.getByRole('radio', { name: 'Choose proposed description' })
    const whole = present(card.closest<HTMLElement>('.metadata-choice'))
    expect(whole.firstElementChild).toHaveClass('description-choice-content')
    masthead('sticky', 100)
    const page = scrollingPage(100)
    page.place(whole, box(40, 900, 300, innerHeight + 400))
    page.place(card, box(40, 900 + innerHeight + 340, 300, 60))
    await press('2')
    expect(card).toHaveFocus()
    const reveal = vi.mocked(card.scrollIntoView).mock
    // First the card from its top, which leaves the control below the window.
    expect(reveal.contexts).toEqual([whole, card])
    expect(reveal.calls).toEqual([
      [{ block: 'start', inline: 'nearest' }],
      [{ block: 'nearest', inline: 'nearest' }],
    ])
    expect(http.recordDecision).not.toHaveBeenCalled()
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(reveal.contexts.filter((box) => box === whole || box === card)).toHaveLength(2)
  })

  it.each([
    ['longer than the window', 'sticky', 100, 3 * innerHeight],
    // Brought to the top it would sit below the scroll padding and run past the foot.
    ['that only just fits the window', 'static', 8, innerHeight - 3],
  ] as const)(
    'shows the middle of a control %s, and the next press records it',
    async (_, mode, paddingTop, height) => {
      const http = store(() => [title])
      await openAt(http, 'title')
      const card = present(screen.getAllByRole('radio')[1])
      const whole = present(card.closest<HTMLElement>('.metadata-choice'))
      masthead(mode, 100)
      const page = scrollingPage(paddingTop)
      page.place(whole, box(40, 900, 300, height + 40))
      page.place(card, box(40, 900, 300, height))
      await press('2')
      expect(card).toHaveFocus()
      const { top, bottom } = card.getBoundingClientRect()
      expect((top + bottom) / 2).toBe(((mode === 'sticky' ? 100 : 0) + innerHeight) / 2)
      expect(http.recordDecision).not.toHaveBeenCalled()
      await press('2')
      await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    },
  )

  it('shows a cover the strip hides to one side before a digit records it', async () => {
    const http = store(() => [coverField])
    await openAt(http, 'cover')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.cover-choice'))
    // A strip 343 wide that scrolls sideways; the window is wider and taller.
    expect(innerWidth).toBeGreaterThan(700)
    const page = scrollingPage(8, { box: present(whole.parentElement), at: box(16, 100, 343, 400) })
    // Most of the cover is inside the strip; its far edge is not.
    page.place(whole, box(260, 110, 168, 380), true)
    page.place(card, box(260, 110, 168, 337), true)
    await press('2')
    expect(card).toHaveFocus()
    const reveal = vi.mocked(whole.scrollIntoView).mock
    expect(reveal.contexts).toEqual([whole])
    expect(reveal.calls).toEqual([[{ block: 'nearest', inline: 'nearest' }]])
    expect(card.getBoundingClientRect().right).toBe(359)
    expect(http.recordDecision).not.toHaveBeenCalled()
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  })

  it('lets the strip cut a cover inside its own padding, and no further', async () => {
    const http = store(() => [coverField])
    await openAt(http, 'cover')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.cover-choice'))
    card.style.padding = '10px'
    const page = scrollingPage(8, { box: present(whole.parentElement), at: box(16, 100, 343, 400) })
    // The strip ends at 359. Cut by 12 px, two more than the padding and one
    // more than rounding allows, the cover is shown first.
    page.place(whole, box(203, 110, 168, 380), true)
    page.place(card, box(203, 110, 168, 337), true)
    await press('2')
    expect(card).toHaveFocus()
    expect(vi.mocked(whole.scrollIntoView).mock.contexts).toEqual([whole])
    expect(http.recordDecision).not.toHaveBeenCalled()
    // Cut by 6 px, as the second cover is at 390 px, it records at once.
    page.place(whole, box(197 + 12, 110, 168, 380), true)
    page.place(card, box(197 + 12, 110, 168, 337), true)
    expect(card.getBoundingClientRect().right).toBe(365)
    await press('2')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(
      vi.mocked(whole.scrollIntoView).mock.contexts.filter((box) => box === whole),
    ).toHaveLength(1)
  })

  it('shows a cover whose caption is below the window first, though the strip leaves it little room', async () => {
    const http = store(() => [coverField])
    await openAt(http, 'cover')
    const card = present(screen.getAllByRole('radio')[1])
    const whole = present(card.closest<HTMLElement>('.cover-choice'))
    // The strip starts near the foot of the window, so less of it is in view
    // than the cover is tall; the cover still fits the window.
    const top = innerHeight - 250
    const page = scrollingPage(8, { box: present(whole.parentElement), at: box(16, top, 343, 400) })
    page.place(whole, box(20, top + 10, 168, 380), true)
    page.place(card, box(20, top + 10, 168, 337), true)
    await press('2')
    expect(card).toHaveFocus()
    expect(http.recordDecision).not.toHaveBeenCalled()
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
