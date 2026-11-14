import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type { CaseDetailView, ReviewHttp } from '../src/api/http'
import { activity, createHttp, createLiveStream, detail, library, renderRoute } from './fixtures'
import { view } from './settingsFixtures'

// Book matching in review (#1355, activation B): a work waiting for its check,
// a choice refused because the field's offer changed, the source line of a value
// offered for the book's title and author, and the History and Activity lines.

const checkingLine = 'Checking this work against the new Book matching setting.'
const offerChangedLine = 'Not recorded: Book matching changed what this field offers. Choose again.'
const genericRefusal = 'Your choice was not recorded. Choose again.'
const title = detail.fields[0]
if (!title) throw new Error('the decision fixture requires a title field')

const proposed = () => screen.getByRole('radio', { name: 'Choose proposed value: New title' })
const current = () => screen.getByRole('radio', { name: 'Choose current value: Old title' })

describe('a work waiting for its Book matching check', () => {
  it('says so, disables its choices, and opens them when the check announces the case', async () => {
    const updates = createLiveStream()
    let served: CaseDetailView = { ...detail, checking: true }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => served),
      getLibrary: vi.fn(async () => library),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByText(checkingLine)).toBeVisible()
    expect(proposed()).toBeDisabled()
    expect(current()).toBeDisabled()
    await userEvent.setup().click(proposed())
    expect(http.recordDecision).not.toHaveBeenCalled()

    // The check reconciles the case and announces it: the case reads again.
    served = { ...detail, checking: false }
    await act(async () => {
      updates.announce({ sequence: 1, subject: 'case-1', topic: 'case' })
    })
    // The update is coalesced before the case reads again.
    await waitFor(() => expect(screen.queryByText(checkingLine)).toBeNull(), { timeout: 4_000 })
    expect(proposed()).toBeEnabled()
    expect(current()).toBeEnabled()
  }, 15_000)
})

describe('a work whose Book matching check gave up', () => {
  const givenUp = 'This work could not be checked against the new Book matching setting.'
  const serve = (http: ReviewHttp): ReviewHttp => ({
    ...http,
    getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => ({
      ...detail,
      checking: true,
      checkGivenUp: true,
    })),
    getLibrary: vi.fn(async () => library),
  })
  const line = async () => {
    const link = await screen.findByRole('link', { name: 'Book matching' })
    const status = link.closest('p')
    if (!status) throw new Error('the given-up line is required')
    expect(status).toHaveAttribute('role', 'status')
    return status
  }

  it('says the check could not run, links Book matching, and keeps its choices disabled', async () => {
    const http = serve({ ...createHttp(), getSettings: vi.fn(async () => view('store')) })
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const status = await line()
    await waitFor(() =>
      expect(status).toHaveTextContent(`${givenUp} Save Book matching again to retry.`),
    )
    expect(screen.queryByText(checkingLine)).toBeNull()
    const link = within(status).getByRole('link', { name: 'Book matching' })
    expect(link.getAttribute('href')).toBe('/policy?section=curation#book-matching')
    expect(proposed()).toBeDisabled()
    expect(current()).toBeDisabled()
  })

  it('asks for a restart in file mode, where the setting cannot be saved', async () => {
    const http = serve({ ...createHttp(), getSettings: vi.fn(async () => view('file')) })
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const status = await line()
    await waitFor(() =>
      expect(status).toHaveTextContent(
        `${givenUp} Restart the Mechanical Librarian service to retry.`,
      ),
    )
    expect(within(status).getByRole('link', { name: 'Book matching' })).toBeVisible()
  })
})

describe('the works row of a work whose Book matching check gave up', () => {
  const givenUpLibrary = {
    ...library,
    counts: { ...library.counts, failed: 1 },
    works: library.works.map((work) => ({
      ...work,
      attentionCount: 0,
      attention: 'preparation_failed' as const,
      preparation: {
        attempt: 1,
        checkGivenUp: true,
        diagnosticReference: 'prep-0123456789abcdef',
        mediaHealth: [],
        provider: 'provider',
        reason: 'The review could not be put together.',
        recovery: 'Scan this library again to retry it.',
        recoveryAttempt: 0,
        stage: 'projection' as const,
        status: 'projection' as const,
      },
    })),
  }
  const row = async () => {
    const button = await screen.findByRole('button', { name: /^A Book\./u })
    return button
  }
  it.each([
    ['store', 'Save Book matching again to retry.'],
    ['file', 'Restart the Mechanical Librarian service to retry.'],
  ] as const)('says what the case line says in %s mode', async (authority, retry) => {
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => givenUpLibrary),
      getSettings: vi.fn(async () => view(authority)),
    }
    renderRoute('/library?lens=all', http)
    const work = await row()
    await waitFor(() =>
      expect(work).toHaveTextContent(
        `Could not be checked against the new Book matching setting. ${retry} Reference: prep-0123456789abcdef`,
      ),
    )
    expect(within(work).getByText('Not checked')).toBeVisible()
    expect(work).not.toHaveTextContent('Could not finish')
    expect(work).not.toHaveTextContent('The review could not be put together.')
    expect(work).not.toHaveTextContent('Scan this library again')
  })
})

describe('the given-up case line waits for the settings read', () => {
  it('shows no way to retry until the read answers, then the file mode one', async () => {
    let answer: (value: ReturnType<typeof view>) => void = () => undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => ({
        ...detail,
        checking: true,
        checkGivenUp: true,
      })),
      getLibrary: vi.fn(async () => library),
      getSettings: vi.fn(
        () =>
          new Promise<ReturnType<typeof view>>((resolve) => {
            answer = resolve
          }),
      ),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await waitFor(() => expect(proposed()).toBeDisabled())
    await waitFor(() => expect(http.getSettings).toHaveBeenCalled())
    expect(screen.queryByText(/Save Book matching again/u)).toBeNull()
    expect(screen.queryByRole('link', { name: 'Book matching' })).toBeNull()
    await act(async () => {
      answer(view('file'))
    })
    const link = await screen.findByRole('link', { name: 'Book matching' })
    expect(link.closest('p')).toHaveTextContent(
      'This work could not be checked against the new Book matching setting. Restart the Mechanical Librarian service to retry.',
    )
    expect(screen.queryByText(/Save Book matching again/u)).toBeNull()
  })
})

describe('owner controls while the work waits for its check', () => {
  const genres = {
    ...title,
    destination: 'genres' as const,
    decisionActions: ['keep_current', 'accept'] as const,
    currentValue: 'Drama',
    currentRaw: ['Drama'],
    proposedValues: [{ kind: 'list' as const, value: ['Fantasy', 'Mystery'] }],
    choiceContexts: [],
    ownerControl: {
      entry: null,
      withdraw: null,
      subset: {
        maxItems: 32,
        options: [
          { value: 'Drama', current: true, proposed: false, previous: false },
          { value: 'Fantasy', current: false, proposed: true, previous: false },
        ],
      },
    },
  }
  it.each([
    ['checking', { checking: true, checkGivenUp: false }],
    ['given up', { checking: true, checkGivenUp: true }],
  ])('ignore a press on Choose genres while %s', async (_, state) => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => ({
        ...detail,
        ...state,
        fields: [genres],
      })),
      getLibrary: vi.fn(async () => library),
    }
    renderRoute('/library?lens=all&preview=case-1&field=genres', http)
    const panel = await screen.findByRole('region', { name: 'Your value for Genres' })
    const trigger = within(panel).getByRole('button', { name: 'Choose genres' })
    expect(trigger).toHaveAttribute('aria-disabled', 'true')
    await user.click(trigger)
    expect(within(panel).queryByRole('group', { name: 'Choose genres' })).toBeNull()
  })

  it('says a refused choice under the choice cards, above Choose genres', async () => {
    const user = userEvent.setup()
    const field = { ...genres, ownerControl: { ...genres.ownerControl } }
    let served: CaseDetailView = { ...detail, fields: [field] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => served),
      getLibrary: vi.fn(async () => library),
      recordDecision: vi.fn<ReviewHttp['recordDecision']>(async () => {
        served = { ...detail, fields: [{ ...field, offerDigest: 'offer-digest-now' }] }
        throw new ApiError(409, 'offer_changed', 'digest_mismatch')
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=genres', http)
    await user.click(
      await screen.findByRole('radio', { name: 'Choose proposed value: Fantasy · Mystery' }),
    )
    const notice = await screen.findByText(offerChangedLine)
    const panel = screen.getByRole('region', { name: 'Your value for Genres' })
    const cards = screen.getByRole('radiogroup')
    // Following in document order: cards, then the line, then Choose genres.
    expect(cards.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(notice.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getAllByText(offerChangedLine)).toHaveLength(1)
  })
})

describe('a choice refused because Book matching changed', () => {
  const refusingHttp = (reason: string, after: CaseDetailView) => {
    let served: CaseDetailView = detail
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => served),
      getLibrary: vi.fn(async () => library),
      recordDecision: vi.fn<ReviewHttp['recordDecision']>(async () => {
        served = after
        throw new ApiError(409, 'offer_changed', reason)
      }),
    }
    return http
  }

  it('keeps the owner on the field, which asks again in its new offer', async () => {
    const user = userEvent.setup()
    const http = refusingHttp('digest_mismatch', {
      ...detail,
      fields: [{ ...title, offerDigest: 'offer-digest-now' }],
    })
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await user.click(await screen.findByRole('radio', { name: 'Choose proposed value: New title' }))
    expect(await screen.findByText(offerChangedLine)).toBeVisible()
    expect(screen.queryByText(genericRefusal)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try the same decision again' })).toBeNull()
    // The case was read again and the field still offers its choice.
    expect(http.getCaseDetail).toHaveBeenCalledTimes(2)
    expect(proposed()).toBeEnabled()
    expect(screen.queryByText(checkingLine)).toBeNull()
  })

  it('reads the case again into its checking state when the work waits for its check', async () => {
    const user = userEvent.setup()
    const http = refusingHttp('unreconciled_case', { ...detail, checking: true })
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await user.click(await screen.findByRole('radio', { name: 'Choose proposed value: New title' }))
    expect(await screen.findByText(checkingLine)).toBeVisible()
    expect(proposed()).toBeDisabled()
    expect(screen.queryByText(genericRefusal)).toBeNull()
    expect(screen.queryByText(offerChangedLine)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try the same decision again' })).toBeNull()
  })
})

it('names a value offered for the book’s title and author beside its source', async () => {
  const user = userEvent.setup()
  const context = {
    evidence: [],
    limitations: [],
    rationale: '',
    recommendation: 'no_recommendation' as const,
    recommendationReason: '',
  }
  const served: CaseDetailView = {
    ...detail,
    fields: [
      {
        ...title,
        choiceContexts: [
          { ...context, alternativeId: null, selection: 'current', matchBasis: null },
          {
            ...context,
            alternativeId: 'alternative-1',
            selection: 'proposed',
            matchBasis: 'title_and_author',
            rationale: 'Hardcover',
            evidence: [
              {
                source: 'Hardcover',
                role: 'external_corroboration',
                observation: 'New title',
                language: null,
              },
              {
                source: 'Library',
                role: 'local_presence',
                observation: 'Old title',
                language: null,
              },
            ],
          },
        ],
      },
    ],
  }
  const http: ReviewHttp = {
    ...createHttp(),
    getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => served),
    getLibrary: vi.fn(async () => library),
  }
  renderRoute('/library?lens=all&preview=case-1&field=title', http)
  await user.click(
    await screen.findByRole('button', { name: 'Evidence for proposed value: New title' }),
  )
  const evidence = screen.getByRole('region', { name: 'evidence' })
  expect(within(evidence).getByText('Hardcover · same title and author')).toBeVisible()
  // The card's own source line says it too, and the value is offered, not
  // confirmed: it counts toward no "sources confirm" line.
  const card = screen.getByRole('radio', { name: 'Choose proposed value: New title' })
  expect(within(card).getByText('Hardcover · same title and author')).toBeVisible()
  expect(within(evidence).getByText('Offers this value')).toBeVisible()
  expect(within(evidence).queryByText('Confirms this value')).toBeNull()
  expect(within(evidence).queryByText(/sources? confirms?/u)).toBeNull()
  // The library's own value is never described as a title-and-author match.
  expect(within(evidence).getByText('Library')).toBeVisible()
  expect(within(evidence).queryByText(/Library · same title and author/u)).toBeNull()
})

it('says in History that Book matching asked the field again, not that the owner withdrew', async () => {
  const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
    items: [
      {
        category: 'matching_changed',
        occurredAt: 1786000000,
        destination: 'description' as const,
      },
    ],
  }))
  renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })
  const heading = await screen.findByRole('heading', { name: 'Description' })
  const entry = heading.closest('article')
  if (!entry) throw new Error('the asked-again History entry is required')
  expect(
    within(entry).getByText('Asked again because Book matching changed what this field offers.'),
  ).toBeVisible()
  expect(screen.queryByText('You withdrew your choice.')).toBeNull()
})

it('labels Book matching’s Activity notes and gives only their sentences in Details', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  vi.mocked(http.getActivity).mockResolvedValue(
    activity({
      events: [
        {
          event_id: 'owner-waiting',
          state: 'planned',
          note: 'waiting_for_check',
          work_label: 'Waiting Work',
        },
        {
          event_id: 'owner-asked-matching',
          note: 'asked_again_matching',
          work_label: 'Matching Work',
        },
      ],
    }),
  )
  renderRoute('/activity', http)
  const row = async (label: string) => {
    const article = (await screen.findByText(label)).closest('article')
    if (!article) throw new Error(`Expected the ${label} row`)
    return article
  }
  const waiting = await row('Waiting Work')
  const asked = await row('Matching Work')
  expect(within(waiting).getByText('Waiting for check')).toBeVisible()
  expect(within(asked).getByText('Asked again')).toBeVisible()
  await user.click(within(waiting).getByRole('button', { name: /waiting for check/u }))
  expect(
    within(waiting).getByText(
      'Book matching changed, so this change waits until this work is checked again.',
    ),
  ).toBeVisible()
  await user.click(within(asked).getByRole('button', { name: /asked again/u }))
  expect(
    within(asked).getByText(
      'Book matching changed what this field offers, so this choice no longer counts and the field is asking you again.',
    ),
  ).toBeVisible()
  // No sentence repeats its label as a prefix (amendment 11).
  expect(screen.queryByText(/^Waiting for check:/u)).toBeNull()
  expect(screen.queryByText(/^Asked again:/u)).toBeNull()
})

it('shows no would-admit, work identity or shadow wording on the review screens (test 22)', async () => {
  const http: ReviewHttp = {
    ...createHttp(),
    getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => ({ ...detail, checking: true })),
    getLibrary: vi.fn(async () => library),
  }
  renderRoute('/library?lens=all&preview=case-1&field=title', http)
  await screen.findByText(checkingLine)
  expect(document.body.textContent).not.toMatch(/would.admit|work.identity|shadow/iu)
})

describe('a field Book matching substituted and made choosable', () => {
  // Live had no supported value; the level offers one. The server sends no
  // reason for withholding the live value (#1355), so the question line is the
  // field's own: it never says no value can be used beside one that can.
  const substituted = (automaticWithheldReason: string | null): CaseDetailView => ({
    ...detail,
    fields: [
      {
        ...title,
        disposition: 'ineligible',
        notices: ['no_supported_evidence'],
        decisionActions: ['keep_current', 'accept'],
        automaticWithheldReason,
      },
    ],
  })
  const questionLine = async () => {
    const question = await screen.findByRole('heading', {
      name: 'Which title should this work use?',
    })
    const line = question.nextElementSibling
    if (!line) throw new Error('the question line is required')
    return line
  }

  it('reads the field’s own reason under the question when the server sends none', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => substituted(null)),
      getLibrary: vi.fn(async () => library),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    expect(await questionLine()).toHaveTextContent(
      'None of these values can be applied for you. Compare them yourself.',
    )
    expect(proposed()).toBeEnabled()
    expect(screen.queryByText('No source offered a value that can be used here.')).toBeNull()
  })

  it('still shows a reason the server sends for a field it did not substitute', async () => {
    const reason = 'No source offered a value that can be used here.'
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => substituted(reason)),
      getLibrary: vi.fn(async () => library),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    expect(await questionLine()).toHaveTextContent(reason)
  })
})
