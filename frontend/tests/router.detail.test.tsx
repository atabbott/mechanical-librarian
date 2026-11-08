import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, BuildMismatchError } from '../src/api/http'
import { DecodeFailure } from '../src/generated/review-decoders'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import identifierEvidenceFixture from './identifierEvidenceFixture.json'
import { normalizeDetailSearch } from '../src/router'
import type { CaseDetailView, ReviewHttp } from '../src/api/http'
import {
  createHttp,
  detail,
  readCaseDetail,
  readEvidence,
  readHistory,
  renderRoute,
} from './fixtures'
import {
  caseHistory,
  coverCandidate,
  coverConsideration,
  coverIdentity,
  coverPolicyReport,
  decisionProjection,
  historyCoverItem,
} from './wireFixtures'

describe('Library review ownership', () => {
  it('opens readable direct Evidence and restores the exact Library selection', async () => {
    const { router } = renderRoute(
      '/review/case-1?lens=needs_attention&field=title&subview=evidence',
    )
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/review/case-1')
    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeVisible()
    expect(screen.getByText('Books · Ebook (EPUB)')).toBeVisible()
    expect(screen.queryByText('internal-item-1')).toBeNull()
    const back = screen.getByRole('link', { name: 'Back to work' })
    expect(document.querySelector('#main-content .diagnostic-route > :first-child')).toBe(back)
    expect(back).toHaveAttribute('href', '/library?lens=needs_attention&preview=case-1&field=title')
    expect(screen.queryByRole('button', { name: /Choose|Return|Resume|Try again/u })).toBeNull()
    expect(screen.queryByRole('radio')).toBeNull()
    await userEvent.setup().click(back)
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        preview: 'case-1',
        field: 'title',
      }),
    )
  })

  it.each(['/review/case-1?subview=evidence', '/library?lens=all&preview=case-1&field=title'])(
    'explains chapter recording identity at %s with secondary source attribution',
    async (route) => {
      const http: ReviewHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({
          ...detail,
          recordingIdentityBasis: {
            basis: 'chapter_runtime_and_structure' as const,
            provider: 'Audnexus',
            candidate: 'recording-1',
          },
        })),
      }
      renderRoute(route, http)
      const section = await screen.findByRole('region', { name: 'How this recording was matched' })
      expect(
        within(section).getByText(/Its chapter timings and structure confirm this recording/u),
      ).toBeVisible()
      expect(within(section).getByText(/since no total running time is available/u)).toBeVisible()
      const source = within(section).getByText('Recording source')
      expect(within(section).getByText('Audnexus · recording-1')).not.toBeVisible()
      await userEvent.setup().click(source)
      expect(within(section).getByText('Audnexus · recording-1')).toBeVisible()
      expect(screen.queryByRole('region', { name: 'Media health' })).toBeNull()
    },
  )

  it('names a language observation by its language, region and script included (#1286 X7, L1)', async () => {
    const [title] = detail.fields
    if (!title) throw new Error('the fixture offers a title')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, destination: 'language' as const }],
      })),
      getEvidence: vi.fn(async () => ({
        items: [
          {
            destinations: ['language' as const],
            observation: 'Language: en',
            role: 'local_presence' as const,
            source: 'Local library record',
          },
          {
            destinations: ['language' as const],
            observation: 'Language: pt-BR',
            role: 'external_observation' as const,
            source: 'Open Library',
          },
          {
            destinations: ['language' as const],
            observation: 'Language: French (fr). Not offered as a choice.',
            role: 'local_presence' as const,
            source: 'Local metadata',
          },
          {
            destinations: ['language' as const],
            observation: 'Language: UND. Not offered as a choice. The language is not known.',
            role: 'local_presence' as const,
            source: 'Local metadata',
          },
        ],
        missingLineageCount: 0,
        sourceLedger: [],
      })),
    }
    renderRoute('/review/case-1?subview=evidence', http)
    expect(await screen.findByText('Language: English (en)')).toBeVisible()
    expect(screen.getByText('Language: Brazilian Portuguese (pt-BR)')).toBeVisible()
    expect(screen.queryByText('Language: en')).toBeNull()
    expect(screen.getByText('Language: French (fr). Not offered as a choice.')).toBeVisible()
    expect(
      screen.getByText('Language: UND. Not offered as a choice. The language is not known.'),
    ).toBeVisible()
  })

  it('states which fields book-level evidence supports for an unresolved recording', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, audiobookReviewScope: 'work' as const })),
    }
    renderRoute('/review/case-1?subview=evidence', http)

    const section = await screen.findByRole('region', { name: 'Book-level evidence' })
    // Only the fields this case offers are named (C1): this case offers Title.
    expect(
      within(section).getByText(
        'Book-level evidence supports title choices. The exact recording is not known.',
      ),
    ).toBeVisible()
    // One offered field reads in the singular (#1642).
    expect(within(section).getByText(/^Your choice applies only to this field\./u)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'How this recording was matched' })).toBeNull()
  })

  it('names only the offered book-level fields, in the note’s order (C1)', async () => {
    const [title] = detail.fields
    if (!title) throw new Error('the fixture offers a title')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        audiobookReviewScope: 'work' as const,
        // Genres and description are offered and Series is not; ASIN is
        // offered but is not something book-level evidence decides. A field
        // the rail does not offer -- context only, or empty with nothing
        // proposed -- is not named either (#1286 X3).
        fields: [
          { ...title, destination: 'genres' as const },
          { ...title, destination: 'asin' as const },
          title,
          { ...title, destination: 'description' as const },
          { ...title, destination: 'series' as const, inventoryRole: 'contextual' as const },
          {
            ...title,
            destination: 'series' as const,
            currentRaw: null,
            proposedValues: [],
          },
        ],
      })),
    }
    renderRoute('/review/case-1?subview=evidence', http)

    const section = await screen.findByRole('region', { name: 'Book-level evidence' })
    expect(
      within(section).getByText(
        'Book-level evidence supports title, description, and genre choices. The exact recording is not known.',
      ),
    ).toBeVisible()
    expect(within(section).queryByText(/series|ASIN/u)).toBeNull()
  })

  it('names a work-route date and cover, and gives narrators their own sentence (#1642)', async () => {
    const [title] = detail.fields
    if (!title) throw new Error('the fixture offers a title')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        audiobookReviewScope: 'work' as const,
        fields: [
          title,
          { ...title, destination: 'narrators' as const },
          { ...title, destination: 'cover' as const },
          { ...title, destination: 'publishedDate' as const },
        ],
      })),
    }
    renderRoute('/review/case-1?subview=evidence', http)

    const section = await screen.findByRole('region', { name: 'Book-level evidence' })
    expect(
      within(section).getByText(
        'Book-level evidence supports title, publication date, and cover choices. A Hardcover audio edition supports a narrator choice. The exact recording is not known.',
      ),
    ).toBeVisible()
    // Covers can now be chosen here, so the note no longer says they cannot.
    expect(
      within(section).getByText(
        'Your choices apply only to these fields. This evidence cannot set recording identifiers or make automatic changes.',
      ),
    ).toBeVisible()
    expect(within(section).queryByText(/covers/u)).toBeNull()
  })

  it('states a narrator-only work route without a book-level sentence (#1642)', async () => {
    const [title] = detail.fields
    if (!title) throw new Error('the fixture offers a title')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        audiobookReviewScope: 'work' as const,
        fields: [{ ...title, destination: 'narrators' as const }],
      })),
    }
    renderRoute('/review/case-1?subview=evidence', http)

    const section = await screen.findByRole('region', { name: 'Book-level evidence' })
    expect(
      within(section).getByText(
        'A Hardcover audio edition supports a narrator choice. The exact recording is not known.',
      ),
    ).toBeVisible()
    // Narrators alone are one field, so the note reads in the singular.
    expect(
      within(section).getByText(
        'Your choice applies only to this field. This evidence cannot set recording identifiers or make automatic changes.',
      ),
    ).toBeVisible()
    expect(within(section).queryByText(/these fields/u)).toBeNull()
  })

  it('states media health on the case an admitted audiobook opens', async () => {
    // An audiobook's row opens its case, never the recorded-evidence view, so
    // a section that existed only there would be invisible for exactly the
    // items it was built for.
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        mediaHealth: [
          {
            bound:
              'A complete recording runs within 1 min 30 s of the edition’s stated running time.',
            cited: ['local_abc123', 'audible-product-B0SYNE7P1K'],
            expected: '20 h stated by the matched edition',
            kind: 'runtime_short_of_provider' as const,
            observed: '12 h of local audio, 8 h shorter than the matched edition',
            tracks: [],
          },
        ],
      })),
    }
    renderRoute('/review/case-1?subview=evidence', http)

    const section = await screen.findByRole('region', { name: 'Media health' })
    expect(within(section).getByText('Shorter than the edition')).toBeVisible()
    expect(
      within(section).getByText(
        'A complete recording runs within 1 min 30 s of the edition’s stated running time.',
      ),
    ).toBeVisible()
    expect(
      within(section).getByText(
        /12 h of local audio, 8 h shorter than the matched edition · expected 20 h/u,
      ),
    ).toBeVisible()
    // The opaque local reference stands for what it points at rather than
    // being printed at the owner.
    expect(
      within(section).getByText(/Read from local evidence, Audible product B0SYNE7P1K/u),
    ).toBeVisible()
    expect(screen.queryByText('local_abc123')).toBeNull()
  })

  it('states no media-health section on a case with nothing wrong with its media', async () => {
    renderRoute('/review/case-1?subview=evidence')

    expect(await screen.findByRole('heading', { name: 'Evidence' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Media health' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'How this recording was matched' })).toBeNull()
  })

  it('cross-links diagnostics with URL-owned state and browser history', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute(
      '/review/case-1?lens=no_attention_needed&field=title&subview=evidence',
    )
    await screen.findByRole('heading', { name: 'Evidence' })
    await user.click(screen.getByRole('link', { name: 'History' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        field: 'title',
        lens: 'no_attention_needed',
        subview: 'history',
      }),
    )
    expect(await screen.findByRole('heading', { name: 'History' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose|Return|Resume/u })).toBeNull()
    expect(screen.queryByRole('radio')).toBeNull()
    router.history.back()
    await waitFor(() => expect(router.state.location.search.subview).toBe('evidence'))
    router.history.forward()
    await waitFor(() => expect(router.state.location.search.subview).toBe('history'))
  })

  it('renders semantic evidence, limitations, provider outcomes, and pagination', async () => {
    const getEvidence = vi.fn<ReviewHttp['getEvidence']>(async (_id, cursor) =>
      cursor
        ? {
            items: [
              {
                destinations: ['title' as const],
                observation: 'Title: <script>Second observation</script>',
                role: 'external_corroboration',
                source: 'Open Library',
              },
            ],
            missingLineageCount: 2,
            sourceLedger: [],
          }
        : {
            items: [
              {
                destinations: ['title' as const],
                observation: 'Title: A Book',
                role: 'local_presence',
                source: 'This library',
              },
            ],
            missingLineageCount: 2,
            nextCursor: 'next-page',
            sourceLedger: [
              {
                laneKind: 'isbn' as const,
                provider: 'Hardcover',
                companionIsbn: false,
                coverOnly: false,
                providerRequests: 1,
                reason: 'Returned no matching metadata.',
                status: 'no_match',
              },
              {
                laneKind: 'work' as const,
                provider: 'Open Library',
                companionIsbn: false,
                coverOnly: false,
                providerRequests: 2,
                reason: 'Returned more than Mechanical Librarian reads, so nothing was recorded.',
                status: 'oversized',
              },
            ],
          },
    )
    const user = userEvent.setup()
    renderRoute('/review/case-1?field=title&subview=evidence', { ...createHttp(), getEvidence })
    expect(await screen.findByText('Title: A Book')).toBeVisible()
    expect(screen.getByText(/2 source answers are no longer available/u)).toBeVisible()
    // Fields with nothing from the sources are left out, not captioned.
    expect(screen.queryByText(/Nothing from the sources/u)).toBeNull()
    expect(document.querySelectorAll('.evidence-field h3').length).toBe(1)
    await user.click(screen.getByText('Sources'))
    expect(screen.getByText('No match')).toBeVisible()
    expect(screen.getByText('ISBN lookup')).toBeVisible()
    expect(screen.getByText('Answer too large')).toBeVisible()
    expect(document.querySelector('pre')).toBeNull()
    expect(document.body).not.toHaveTextContent('evidence_')
    await user.click(screen.getByRole('button', { name: 'Load more evidence' }))
    expect(await screen.findByText('Title: <script>Second observation</script>')).toBeVisible()
    expect(document.querySelector('script')).toBeNull()
    expect(getEvidence).toHaveBeenLastCalledWith(
      'case-1',
      'next-page',
      expect.any(AbortSignal),
      'foreground',
    )
  })

  it.each([
    ['evidence', 'Evidence'],
    ['history', 'History'],
  ])('names a %s read refused by a newer build and offers Reload', async (subview, lead) => {
    const refuse = async (): Promise<never> => {
      throw new BuildMismatchError()
    }
    renderRoute(`/review/case-1?field=title&subview=${subview}`, {
      ...createHttp(),
      getEvidence: vi.fn<ReviewHttp['getEvidence']>(refuse),
      getHistory: vi.fn<ReviewHttp['getHistory']>(refuse),
    })
    expect(await screen.findByText(`${lead} was not loaded.`)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeVisible()
    expect(screen.queryByText(/could not be loaded/u)).toBeNull()
  })

  it.each(['evidence', 'history'])(
    'asks for a reload after any other %s read failure',
    async (subview) => {
      const fail = async (): Promise<never> => {
        throw new ApiError(503, 'unavailable')
      }
      renderRoute(`/review/case-1?field=title&subview=${subview}`, {
        ...createHttp(),
        getEvidence: vi.fn<ReviewHttp['getEvidence']>(fail),
        getHistory: vi.fn<ReviewHttp['getHistory']>(fail),
      })
      expect(await screen.findByRole('alert')).toHaveTextContent(
        /could not be loaded\. Reload the page to try again\./u,
      )
      expect(screen.queryByText(/was not loaded/u)).toBeNull()
    },
  )

  it.each([
    [undefined, 'Nothing from the sources for this work.'],
    ['next-page', 'Nothing from the sources yet. More may load below.'],
  ])('says once that the sources gave nothing (cursor %s)', async (nextCursor, line) => {
    const getEvidence = vi.fn<ReviewHttp['getEvidence']>(async () => ({
      items: [],
      missingLineageCount: 0,
      ...(nextCursor ? { nextCursor } : {}),
      sourceLedger: [],
    }))
    renderRoute('/review/case-1?field=title&subview=evidence', { ...createHttp(), getEvidence })
    expect(await screen.findByText(line)).toBeVisible()
    expect(screen.getAllByText(/Nothing from the sources/u)).toHaveLength(1)
    expect(document.querySelector('.evidence-field')).toBeNull()
    expect(screen.queryByText('All evidence is shown.')).toBeNull()
    expect(screen.queryByText('Sources')).toBeNull()
  })

  it('shows retained conflicting identifiers as observations without offering choices', async () => {
    const evidence = readEvidence(identifierEvidenceFixture.evidence)
    const current = readCaseDetail(identifierEvidenceFixture.detail)
    const changed = structuredClone(identifierEvidenceFixture.detail)
    const choiceEvidence = changed.fields
      .flatMap((field) => field.choice_contexts)
      .flatMap((context) => context.evidence)[0]
    if (!choiceEvidence) throw new Error('actual linked choice evidence required')
    choiceEvidence.role = 'invented_support'
    expect(() => readCaseDetail(changed)).toThrow(DecodeFailure)
    renderRoute(`/review/${current.id}?field=isbn&subview=evidence`, {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      getEvidence: vi.fn(async () => evidence),
    })
    expect(await screen.findByText(/ISBN: 9781250077004/u)).toBeVisible()
    expect(screen.getByText(/ISBN: 9780593135204/u)).toBeVisible()
    const observations = evidence.items.filter((item) => item.role === 'external_observation')
    for (const observation of new Set(observations.map((item) => item.observation))) {
      const rendered = screen.getAllByText(observation)
      expect(rendered).toHaveLength(
        observations.filter((item) => item.observation === observation).length,
      )
      for (const fact of rendered) expect(fact).toBeVisible()
    }
    expect(screen.getAllByText(/Reports this value/u)).toHaveLength(observations.length)
    expect(screen.queryByRole('button', { name: /Choose|Accept|Apply/u })).toBeNull()
    expect(screen.queryByRole('radio')).toBeNull()
    expect(current.fields.find((field) => field.destination === 'isbn')?.proposedValues).toEqual([])
  })

  it('renders history values only when the read model carries them', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'title' as const,
          outcome: 'accept',
          previousValue: { kind: 'text', value: '<script>previous</script>' },
          selectedValue: { kind: 'text', value: '<script>chosen</script>' },
        },
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'language' as const,
          outcome: 'accept',
          previousValue: null,
          selectedValue: { kind: 'text', value: 'English' },
        },
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'publisher' as const,
          outcome: 'accept',
          previousValue: { kind: 'text', value: 'Former publisher' },
          selectedValue: null,
        },
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'narrators' as const,
          outcome: 'keep_current',
          previousValue: { kind: 'text', value: 'Applied candidate' },
          selectedValue: { kind: 'text', value: 'Original value' },
        },
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'subtitle' as const,
          outcome: 'keep_current',
          previousValue: { kind: 'text', value: 'Existing subtitle' },
          selectedValue: { kind: 'text', value: 'Existing subtitle' },
        },
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'publishedDate' as const,
          outcome: 'keep_current',
          previousValue: null,
          selectedValue: null,
        },
        {
          category: 'automatic',
          occurredAt: 1786000000,
          destination: 'series' as const,
          outcome: 'keep_current',
          previousValue: { kind: 'series', value: [{ name: 'Existing series', sequence: '2' }] },
          selectedValue: { kind: 'series', value: [{ name: 'Existing series', sequence: '2' }] },
        },
        {
          category: 'owner',
          occurredAt: 1786000000,
          applicationReason: null,
          destination: 'isbn' as const,
          outcome: 'reject',
          previousValue: { kind: 'text', value: '9780000000001' },
          selectedValue: null,
        },
        {
          category: 'automatic',
          occurredAt: 1786000000,
          destination: 'authors' as const,
          outcome: 'candidate',
          previousValue: { kind: 'list', value: ['Old Author'] },
          selectedValue: { kind: 'list', value: ['New Author'] },
        },
        {
          category: 'automatic',
          occurredAt: 1786000000,
          destination: 'description' as const,
          outcome: 'candidate',
          previousValue: null,
          selectedValue: null,
        },
        {
          category: 'automatic',
          occurredAt: 1786000000,
          destination: 'genres' as const,
          outcome: 'ignore',
          previousValue: { kind: 'list', value: ['Preserved genre'] },
          selectedValue: null,
        },
        {
          category: 'automatic',
          occurredAt: 1786000000,
          destination: 'tags' as const,
          outcome: 'ignore',
          previousValue: null,
          selectedValue: null,
        },
        {
          category: 'preview',
          occurredAt: 1786000000,
          dispositions: [
            { count: 7, outcome: 'manual' },
            { count: 1, outcome: 'unsupported' },
          ],
        },
        {
          category: 'cover',
          occurredAt: 1786000000,
          candidateCount: 2,
          outcome: 'selected',
          ownerOutcome: 'accept',
          policyReasons: ['deterministic_confirmed_order'],
          policyStatus: 'recommended',
          reviewerStatus: 'completed',
          supersedesAutomatic: false,
        },
        {
          automaticOutcome: 'candidate',
          category: 'cover',
          occurredAt: 1786000000,
          candidateCount: 3,
          outcome: 'automatic_prepared',
          policyReasons: [],
          reviewerStatus: 'completed',
          supersedesAutomatic: true,
        },
        {
          category: 'transition',
          occurredAt: null,
          outcome: 'admitted',
          reason: '<script>actionable</script>',
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })

    const title = await screen.findByRole('heading', { name: 'Title' })
    const titleChange = title.closest('article')
    if (!titleChange) throw new Error('title change history article required')
    expect(titleChange).toHaveTextContent('You chose a proposed value.')
    expect(titleChange.parentElement?.querySelector('time')).toHaveAttribute(
      'datetime',
      new Date(1786000000 * 1000).toISOString(),
    )
    // An entry with no recorded time shows no time line at all.
    expect(screen.queryByText('Time not recorded')).toBeNull()
    expect(screen.getByText('<script>previous</script>')).toBeVisible()
    expect(screen.getByText('<script>chosen</script>')).toBeVisible()
    const language = screen.getByRole('heading', { name: 'Language' }).closest('article')
    if (!language) throw new Error('absence to value history article required')
    expect(language).toHaveTextContent('From No value is recorded.')
    expect(language).toHaveTextContent('To English')
    const publisher = screen.getByRole('heading', { name: 'Publisher' }).closest('article')
    if (!publisher) throw new Error('value to absence history article required')
    expect(publisher).toHaveTextContent('From Former publisher')
    expect(publisher).toHaveTextContent('To No value is recorded.')
    const reversal = screen.getByText('Applied candidate').closest('article')
    if (!reversal) throw new Error('keep current reversal history article required')
    expect(reversal).toHaveTextContent('You kept the current value.')
    expect(reversal).toHaveTextContent('From Applied candidate')
    expect(reversal).toHaveTextContent('To Original value')
    expect(reversal).not.toHaveTextContent('Kept')
    const keptOwner = screen.getByRole('heading', { name: 'Subtitle' }).closest('article')
    if (!keptOwner) throw new Error('same-valued owner keep current article required')
    expect(keptOwner).toHaveTextContent('You kept the current value.')
    expect(keptOwner).toHaveTextContent('Kept Existing subtitle')
    expect(keptOwner).not.toHaveTextContent('From')
    expect(keptOwner).not.toHaveTextContent('To')
    expect(screen.getAllByText('Kept')).toHaveLength(6)
    const typedIdentical = screen.getByRole('heading', { name: 'Series' }).closest('article')
    if (!typedIdentical) throw new Error('typed identical history article required')
    expect(typedIdentical).toHaveTextContent('Automatic kept the current value.')
    expect(typedIdentical).toHaveTextContent('Existing series · 2')
    expect(typedIdentical).not.toHaveTextContent('From')
    expect(typedIdentical).not.toHaveTextContent('To')
    const rejected = screen.getByRole('heading', { name: 'ISBN' }).closest('article')
    if (!rejected) throw new Error('reject history article required')
    expect(rejected).toHaveTextContent('You declined the proposed change.')
    expect(rejected).toHaveTextContent('9780000000001')
    expect(rejected).not.toHaveTextContent('From')
    expect(rejected).not.toHaveTextContent('To')
    const ignored = screen.getByRole('heading', { name: 'Genres' }).closest('article')
    if (!ignored) throw new Error('ignore history article required')
    expect(ignored).toHaveTextContent('Preserved genre')
    expect(ignored).not.toHaveTextContent('From')
    expect(ignored).not.toHaveTextContent('To')
    const retainedAbsence = screen.getByRole('heading', { name: 'Tags' }).closest('article')
    if (!retainedAbsence) throw new Error('retained absence article required')
    expect(retainedAbsence).toHaveTextContent('No value is recorded.')
    const keptAbsence = screen.getByRole('heading', { name: 'Publication date' }).closest('article')
    if (!keptAbsence) throw new Error('kept absence history article required')
    expect(keptAbsence).toHaveTextContent('Kept No value is recorded.')
    expect(keptAbsence).not.toHaveTextContent('From')
    expect(keptAbsence).not.toHaveTextContent('To')
    const unmatchedCandidate = screen
      .getByRole('heading', { name: 'Description' })
      .closest('article')
    if (!unmatchedCandidate) throw new Error('unmatched automatic candidate article required')
    expect(unmatchedCandidate).not.toHaveTextContent('Kept')
    expect(unmatchedCandidate).toHaveTextContent('From No value is recorded.')
    expect(unmatchedCandidate).toHaveTextContent('To No value is recorded.')
    expect(ignored).not.toHaveTextContent('Previous')
    expect(ignored).not.toHaveTextContent('New')
    expect(screen.getByText(/The cover reviewer looked at 2 covers/u)).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Cover chosen' })).toBeVisible()
    expect(screen.getByText('You chose a proposed cover.', { exact: false })).toBeVisible()
    expect(screen.getByText('The policy recommended a cover.')).toBeVisible()
    expect(screen.getByText('Confirmed covers are ranked in a fixed order')).toBeVisible()
    const preview = screen.getByRole('list', { name: 'Preview outcomes' })
    expect(preview).toHaveTextContent('Needs you7')
    expect(preview).toHaveTextContent('Not supported1')
    expect(screen.getByRole('heading', { name: 'Entered review' })).toBeVisible()
    expect(screen.getByText('Automatic chose this cover.')).toBeVisible()
    expect(
      screen.getByText(
        'It replaced the earlier automatic choice with a clearly better confirmed cover.',
      ),
    ).toBeVisible()
    expect(screen.getByText('Nothing was changed.')).toBeVisible()
    // The transition's heading says it; no sentence repeats it (#1765).
    expect(screen.queryByText('This work entered the review queue.')).toBeNull()
    expect(document.body).not.toHaveTextContent('<script>actionable</script>')
    expect(document.body).not.toHaveTextContent(/decision-|event-|candidate-|reference-/u)
    expect(document.querySelector('script')).toBeNull()
  })

  it.each(['evidence', 'history'] as const)(
    'guards a superseded %s diagnostic and returns to Library authority',
    async (subview) => {
      const evidence = vi.fn()
      const history = vi.fn()
      const user = userEvent.setup()
      const { router } = renderRoute(
        `/review/case-1?lens=needs_attention&field=title&subview=${subview}`,
        {
          ...createHttp(),
          getCaseDetail: vi.fn(async () => ({ ...detail, current: false })),
          getEvidence: evidence,
          getHistory: history,
        },
      )

      expect(
        await screen.findByRole('heading', {
          name: 'This review case is out of date',
        }),
      ).toBeVisible()
      expect(screen.queryByRole('heading', { name: 'Evidence' })).toBeNull()
      expect(screen.queryByRole('heading', { name: 'History' })).toBeNull()
      expect(evidence).not.toHaveBeenCalled()
      expect(history).not.toHaveBeenCalled()
      await user.click(screen.getByRole('link', { name: 'Back to work' }))
      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'needs_attention',
          preview: 'case-1',
          field: 'title',
        }),
      )
    },
  )

  it('keeps Back first during diagnostic loading and typed read failure without retry', async () => {
    const pending = new Promise<CaseDetailView>(() => undefined)
    renderRoute('/review/case-1?lens=needs_attention&field=title&subview=evidence', {
      ...createHttp(),
      getCaseDetail: vi.fn(() => pending),
    })
    const loadingBack = await screen.findByRole('link', { name: 'Back to work' })
    expect(document.querySelector('#main-content .diagnostic-route > :first-child')).toBe(
      loadingBack,
    )
    expect(screen.getByRole('heading', { name: 'Loading the review case…' })).toBeVisible()
    renderRoute('/review/case-failed?lens=no_attention_needed&field=authors&subview=history', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => Promise.reject(new ApiError(503, 'unavailable'))),
    })
    expect(await screen.findByRole('heading', { name: 'The review case could not be opened' }))
    expect(screen.getAllByRole('link', { name: 'Back to work' }).at(-1)).toHaveAttribute(
      'href',
      '/library?lens=no_attention_needed&preview=case-failed&field=authors',
    )
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.queryByText(/503|Review API request failed/u)).toBeNull()
  })

  it('keeps the previous case busy on the diagnostic route until the next read settles', async () => {
    let settle: (view: CaseDetailView) => void = () => undefined
    const http = {
      ...createHttp(),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2'
          ? new Promise<CaseDetailView>((resolve) => (settle = resolve))
          : id === 'case-4'
            ? new Promise<CaseDetailView>(() => undefined)
            : id === 'case-3'
              ? Promise.reject(new ApiError(503, 'unavailable'))
              : detail,
      ),
      getEvidence: vi.fn(async (id: string) => ({
        items: [
          {
            destinations: ['title' as const],
            observation: `${id} observation`,
            role: 'external_observation' as const,
            source: 'Synthetic source',
          },
        ],
        missingLineageCount: 0,
        sourceLedger: [],
      })),
    }
    const { router } = renderRoute('/review/case-1?lens=all&subview=evidence', http)
    expect(await screen.findByRole('heading', { level: 1, name: 'A Book' })).toBeVisible()
    expect(await screen.findByText('case-1 observation')).toBeVisible()
    const route = document.querySelector('.diagnostic-route')
    const open = (reviewCaseId: string) =>
      router.navigate({
        to: '/review/$reviewCaseId',
        params: { reviewCaseId },
        search: { lens: 'all', subview: 'evidence' },
      })

    await open('case-2')
    await waitFor(() => expect(route).toHaveAttribute('aria-busy', 'true'))
    expect(screen.getByRole('heading', { level: 1, name: 'A Book' })).toBeVisible()
    // The next case's evidence never appears under the retained case's heading.
    expect(screen.getByText('case-1 observation')).toBeVisible()
    expect(screen.queryByText('case-2 observation')).toBeNull()
    settle({ ...detail, id: 'case-2', title: 'Another Book' })
    expect(await screen.findByRole('heading', { level: 1, name: 'Another Book' })).toBeVisible()
    expect(await screen.findByText('case-2 observation')).toBeVisible()
    expect(screen.queryByText('case-1 observation')).toBeNull()
    expect(screen.queryByRole('heading', { level: 1, name: 'A Book' })).toBeNull()
    expect(route).not.toHaveAttribute('aria-busy')

    await open('case-3')
    expect(
      await screen.findByRole('heading', { name: 'The review case could not be opened' }),
    ).toBeVisible()
    expect(screen.queryByRole('heading', { level: 1, name: 'Another Book' })).toBeNull()
    expect(document.querySelector('.diagnostic-route')).not.toHaveAttribute('aria-busy')

    // The failure cleared the case, so the next pending read cannot revive it.
    await open('case-4')
    expect(await screen.findByRole('heading', { name: 'Loading the review case…' })).toBeVisible()
    expect(screen.queryByRole('heading', { level: 1, name: 'Another Book' })).toBeNull()
  })

  it('records the exact selected scalar choice and refreshes the Library read model', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...field,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            application_reason: null,
            decision_id: 'decision-1',
            destination: field.destination,
            occurred_at: 1,
            outcome: 'accept',
            proposal_id: field.proposalId,
            review_case_id: 'case-1',
            selected_value: field.proposedValues[0] ?? null,
            target_fingerprint: field.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const recordDecision = vi.fn(async () => {
      current = settled
      return {
        schema_version: 'review-decision-v1' as const,
        actor: 'owner' as const,
        application: 'apply' as const,
        application_reason: null,
        decision_id: 'decision-1',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: field.proposedValues[0] ?? null,
        target_fingerprint: field.targetFingerprint,
        confirmation: { status: 'confirmed' as const, case: current },
        invalidation_sequences: [],
      }
    })
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision,
    })
    await user.click(await screen.findByRole('radio', { name: /Choose proposed value/u }))
    await waitFor(() =>
      expect(recordDecision).toHaveBeenCalledWith(
        'case-1',
        expect.objectContaining({
          destination: 'title' as const,
          outcome: 'accept',
          selectedValue: field.proposedValues[0],
        }),
      ),
    )
    expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
  })

  it('keeps a current-only field selectable without fabricating an alternative', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, preservationOnly: true, proposedValues: [] }],
      })),
    })
    expect(await screen.findByRole('radio', { name: /Choose current value/u })).toBeVisible()
    expect(screen.queryByText('Choose a new value')).toBeNull()
  })

  it('keeps named retained evidence optional, expandable, sticky, and part of its whole choice', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const contextual = {
      alternativeId: null,
      selection: 'current' as const,
      evidence: [
        {
          source: 'Hardcover',
          role: 'external_corroboration' as const,
          observation: 'Title: Old title',
        },
      ],
      limitations: [],
      rationale: 'Retained rationale.',
      recommendation: 'recommended' as const,
      recommendationReason: 'Retained recommendation.',
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, choiceContexts: [contextual] }],
      })),
    })
    const evidence = await screen.findByRole('button', { name: /Evidence for Current value/u })
    await user.click(evidence)
    expect(screen.getByText('Hardcover')).toBeVisible()
    expect(screen.getAllByText('Retained recommendation.')).not.toHaveLength(0)
    expect(evidence).toHaveAttribute('aria-expanded', 'true')
    expect(evidence).toHaveTextContent('Hide evidence')
    await user.click(evidence)
    expect(evidence).toHaveFocus()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Evidence for Current value/u })).toHaveAttribute(
        'aria-expanded',
        'false',
      ),
    )
  })

  it('renders recorded fields as safely escaped voluntary revisits outside Attention', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const recorded = {
      ...field,
      needsAttention: false,
      classification: 'automatic_recorded' as const,
      classificationCause: 'automatic_decided' as const,
      classificationReason: '<script>untrusted</script>',
      currentValue: '<script>untrusted</script>',
      currentRaw: '<script>untrusted</script>',
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-1',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: field.targetFingerprint,
      },
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [recorded] })),
    })
    await screen.findByRole('heading', { name: 'A Book' })
    expect(screen.getByText('<script>untrusted</script>')).toBeVisible()
    expect(document.querySelector('script')).toBeNull()
    expect(
      screen.getByText('The recorded outcome is marked below. You can choose another value.'),
    ).toBeVisible()
    expect(screen.getByRole('radio', { name: /Choose current value/u })).toBeVisible()
    expect(screen.queryByText('Current review case')).toBeNull()
  })

  it('requests fresh checks only after the Library manual-handling confirmation', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    let settleRequest: (() => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          settleRequest = resolve
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic,
    })

    await screen.findByRole('button', { name: 'Resume automatic handling' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    const cancel = await screen.findByRole('button', { name: 'Keep manual handling' })
    expect(cancel).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Resume automatic handling' })).toHaveFocus()
    expect(returnToAutomatic).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(screen.getByRole('button', { name: 'Requesting fresh checks…' })).toBeDisabled()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    settleRequest?.()
    expect(await screen.findByText('Fresh checks requested.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Keep manual handling' }))
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Fresh checks requested' }))
    expect(returnToAutomatic).toHaveBeenCalledOnce()
  })

  it('keeps manual handling unchanged when fresh-check request fails', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const returnToAutomatic = vi.fn(async () => Promise.reject(new Error('offline')))
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(
      await screen.findByText(
        'Fresh checks could not be requested. Choose Resume after fresh checks to try again.',
      ),
    ).toBeVisible()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toBeEnabled()
  })

  // #1755: an unconfirmed request is a warning that resolves from the case
  // reread after it, and focus stays on the safe button throughout.
  const U1 = 'No answer came back. Reloading this work to check…'
  const U2 =
    "No answer came back. This work doesn't show fresh checks as requested. Request them again."
  const S0 = 'Fresh checks requested.'
  const unconfirmed = (reload: (held: CaseDetailView) => Promise<CaseDetailView>) => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const returnToAutomatic = vi.fn(async () =>
      Promise.reject(new ApiError(500, 'invalid_error_response')),
    )
    const held = { ...detail, fields: [{ ...field, manualHold: true }] }
    let holdReload = false
    let finishReload: (() => void) | undefined
    const getCaseDetail = vi.fn(async () => {
      if (!holdReload) return held
      await new Promise<void>((resolve) => {
        finishReload = resolve
      })
      return reload(held)
    })
    const { queryClient } = renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail,
      returnToAutomatic,
    })
    return {
      queryClient,
      getCaseDetail,
      returnToAutomatic,
      startReload: () => {
        holdReload = true
      },
      finishReload: () => finishReload?.(),
    }
  }

  it('reloads the case and says it does not show fresh checks as requested when the outcome is unknown', async () => {
    const user = userEvent.setup()
    const run = unconfirmed(async (held) => held)
    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    const readsBefore = run.getCaseDetail.mock.calls.length
    run.startReload()
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    const warning = await screen.findByText(U1)
    expect(warning).toHaveAttribute('role', 'status')
    expect(warning).toHaveClass('automatic-handling-result', 'is-uncertain')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(
      screen.queryByText(
        'Fresh checks could not be requested. Choose Resume after fresh checks to try again.',
      ),
    ).toBeNull()
    await waitFor(() => expect(run.getCaseDetail.mock.calls.length).toBeGreaterThan(readsBefore))
    // Focus returns to the panel while the case is still reloading, so
    // Escape keeps closing it.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Keep manual handling' })).toHaveFocus(),
    )
    expect(screen.getByText(U1)).toBeVisible()
    run.finishReload()
    const resolved = await screen.findByText(U2)
    expect(resolved).toHaveAttribute('role', 'status')
    expect(resolved).toHaveClass('is-uncertain')
    expect(screen.queryByText(U1)).toBeNull()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toHaveFocus()
    // Requesting again does no harm, so it stays offered.
    expect(screen.getByRole('button', { name: 'Resume after fresh checks' })).toBeEnabled()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Resume automatic handling' })).toHaveFocus()
    expect(run.returnToAutomatic).toHaveBeenCalledOnce()
  })

  it('resolves an unknown outcome as requested when the reloaded case shows it', async () => {
    const user = userEvent.setup()
    const run = unconfirmed(async (held) => ({
      ...held,
      fields: held.fields.map((field) => ({ ...field, manualReturnRequested: true })),
    }))
    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    run.startReload()
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(await screen.findByText(U1)).toBeVisible()
    run.finishReload()
    const done = await screen.findByText(S0)
    expect(done).toHaveAttribute('role', 'status')
    expect(done).toHaveClass('is-success')
    expect(screen.queryByText(/No answer came back/u)).toBeNull()
    expect(screen.getByRole('button', { name: 'Resume after fresh checks' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toHaveFocus()
    expect(run.returnToAutomatic).toHaveBeenCalledOnce()
  })

  // A background reread after the outcome resolved (window focus, a live
  // update) keeps the resolved message rather than reverting to reloading.
  it.each([
    ['requested', true, S0],
    ['not requested', false, U2],
  ] as const)(
    'keeps a resolved %s outcome while the case rereads in the background',
    async (_name, requested, copy) => {
      const user = userEvent.setup()
      const run = unconfirmed(async (held) => ({
        ...held,
        fields: held.fields.map((field) => ({ ...field, manualReturnRequested: requested })),
      }))
      await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
      run.startReload()
      await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
      expect(await screen.findByText(U1)).toBeVisible()
      run.finishReload()
      expect(await screen.findByText(copy)).toBeVisible()
      const readsBefore = run.getCaseDetail.mock.calls.length
      void run.queryClient.refetchQueries({ queryKey: ['review-case', 'case-1'], exact: true })
      await waitFor(() => expect(run.getCaseDetail.mock.calls.length).toBeGreaterThan(readsBefore))
      expect(screen.getByText(copy)).toBeVisible()
      expect(screen.queryByText(U1)).toBeNull()
      run.finishReload()
      await waitFor(() => expect(screen.getByText(copy)).toBeVisible())
      expect(screen.queryByText(U1)).toBeNull()
    },
  )

  // A reread that fails replaces the work with its own failure and Try again
  // (#1288), so the panel never claims a reload that did not happen; the
  // next reread settles the uncertainty.
  it('settles an unknown outcome from the next reread when the first one fails', async () => {
    const user = userEvent.setup()
    let rereads = 0
    const run = unconfirmed(async (held) => {
      rereads += 1
      if (rereads === 1) throw new ApiError(503, 'unavailable')
      return held
    })
    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    run.startReload()
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(await screen.findByText(U1)).toBeVisible()
    run.finishReload()
    const retry = await screen.findByRole('button', { name: 'Try again' })
    expect(screen.getByRole('alert')).toHaveTextContent('Selected work could not be loaded.')
    expect(screen.queryByText(/No answer came back/u)).toBeNull()
    const readsBefore = run.getCaseDetail.mock.calls.length
    await user.click(retry)
    await waitFor(() => expect(run.getCaseDetail.mock.calls.length).toBeGreaterThan(readsBefore))
    run.finishReload()
    const settled = await screen.findByText(U2)
    expect(settled).toHaveClass('is-uncertain')
    expect(screen.getByRole('button', { name: 'Resume after fresh checks' })).toBeEnabled()
    expect(run.returnToAutomatic).toHaveBeenCalledOnce()
  })

  it('shows a return requested before a reload as requested, without sending one', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const returnToAutomatic = vi.fn(async () => undefined)
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, manualHold: true, manualReturnRequested: true }],
      })),
      returnToAutomatic,
    })
    const trigger = await screen.findByRole('button', { name: 'Fresh checks requested' })
    expect(trigger).toHaveAttribute('aria-disabled', 'true')
    expect(screen.queryByRole('button', { name: 'Resume automatic handling' })).toBeNull()
    await user.click(trigger)
    expect(screen.queryByRole('button', { name: 'Resume after fresh checks' })).toBeNull()
    expect(returnToAutomatic).not.toHaveBeenCalled()
  })

  // Requested checks that ended with failures keep the hold, and nothing
  // retries them, so the case offers the request again with one short line.
  const F0 = 'The fresh checks ended with failures.'
  const F1 = `${F0} Choose Resume automatic handling to request them again.`
  const F2 = `${F0} Choose Resume after fresh checks to request them again.`
  const ownDecision = (field: CaseDetailView['fields'][number]) =>
    ({
      actor: 'owner',
      application: 'apply' as const,
      application_reason: null,
      decision_id: 'decision-1',
      destination: field.destination,
      occurred_at: 1,
      outcome: 'accept',
      proposal_id: field.proposalId,
      review_case_id: 'case-1',
      selected_value: field.proposedValues[0] ?? null,
      target_fingerprint: field.targetFingerprint,
    }) as const
  it('offers fresh checks again when the requested ones ended with failures', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const returnToAutomatic = vi.fn(async () => undefined)
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...field,
            manualHold: true,
            manualReturnFailed: true,
            humanDecision: ownDecision(field),
          },
        ],
      })),
      returnToAutomatic,
    })
    const trigger = await screen.findByRole('button', { name: 'Resume automatic handling' })
    expect(trigger).not.toHaveAttribute('aria-disabled', 'true')
    const line = screen.getByText(F1)
    expect(line).toHaveClass('automatic-handling-result', 'is-error')
    expect(screen.queryByRole('alert')).toBeNull()
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(await screen.findByText(S0)).toBeVisible()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Resume after fresh checks' })).toBeDisabled()
  })

  // On a hold from an earlier case, the earlier-decision line already names
  // the button, so the failure line does not repeat the step.
  it('says the next step once on a hold from an earlier case', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, manualHold: true, manualReturnFailed: true, humanDecision: null }],
      })),
      returnToAutomatic: vi.fn(async () => undefined),
    })
    // Exact text: the line is the first sentence alone.
    await screen.findByText(F0)
    const panel = screen.getByRole('region', { name: 'Automatic handling' })
    expect(panel.textContent.match(/to request them again/gu) ?? []).toHaveLength(0)
    expect(panel.textContent.match(/choose Resume automatic handling/giu) ?? []).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Resume automatic handling' })).toBeEnabled()
  })

  it('shows checks that ended with failures in the open panel when a reread finds them', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    let phase: 'held' | 'requested' | 'failed' = 'held'
    const { queryClient } = renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...field,
            manualHold: true,
            manualReturnRequested: phase === 'requested',
            manualReturnFailed: phase === 'failed',
          },
        ],
      })),
      returnToAutomatic: vi.fn(async () => {
        phase = 'requested'
      }),
    })
    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(await screen.findByText(S0)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Resume after fresh checks' })).toBeDisabled()
    phase = 'failed'
    await queryClient.refetchQueries({ queryKey: ['review-case', 'case-1'], exact: true })
    // The panel stays open and says why its button is offered again.
    const line = await screen.findByText(F2)
    expect(line).toHaveAttribute('role', 'status')
    expect(line).toHaveClass('automatic-handling-result', 'is-error')
    expect(screen.queryByText(S0)).toBeNull()
    expect(screen.getByRole('button', { name: 'Resume after fresh checks' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Keep manual handling' }))
    // Closed, the hold here is from an earlier case: the step is said once.
    expect(screen.getByText(F0)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Resume automatic handling' })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('shows a failed fresh-check request as an alert in the danger tone', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic: vi.fn(async () => Promise.reject(new ApiError(409, 'stale'))),
    })
    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    const error = await screen.findByRole('alert')
    expect(error).toHaveTextContent(
      'Fresh checks could not be requested. Choose Resume after fresh checks to try again.',
    )
    expect(error).toHaveClass('automatic-handling-result', 'is-error')
  })

  it('keeps a fresh-check success with the field that requested it after navigation', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const authors = {
      ...title,
      destination: 'authors' as const,
      targetFingerprint: 'fingerprint-authors',
      manualHold: true,
    }
    let resolveRequest: (() => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRequest = resolve
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, manualHold: true }, authors],
      })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    await user.click(screen.getByRole('button', { name: /^Authors/u }))
    await screen.findByRole('heading', { name: 'Which authors should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    expect(screen.queryByText('Fresh checks requested.')).toBeNull()
    resolveRequest?.()
    await waitFor(() => expect(returnToAutomatic).toHaveBeenCalledOnce())
    expect(screen.queryByText('Fresh checks requested.')).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Title/u }))
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('keeps a fresh-check failure with the field that requested it after navigation', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const authors = {
      ...title,
      destination: 'authors' as const,
      targetFingerprint: 'fingerprint-authors',
      manualHold: true,
    }
    let rejectRequest: ((error: Error) => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectRequest = reject
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, manualHold: true }, authors],
      })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    await user.click(screen.getByRole('button', { name: /^Authors/u }))
    await screen.findByRole('heading', { name: 'Which authors should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    rejectRequest?.(new Error('offline'))
    await waitFor(() => expect(returnToAutomatic).toHaveBeenCalledOnce())
    expect(
      screen.queryByText(
        'Fresh checks could not be requested. Choose Resume after fresh checks to try again.',
      ),
    ).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Title/u }))
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    expect(
      await screen.findByText(
        'Fresh checks could not be requested. Choose Resume after fresh checks to try again.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toHaveFocus()
  })

  it('preserves a diagnostic direct entry through Back and Forward', async () => {
    const { router } = renderRoute('/library?lens=all')
    await router.navigate({
      to: '/review/$reviewCaseId',
      params: { reviewCaseId: 'case-1' },
      search: { field: 'title', lens: 'all', subview: 'evidence' },
    })
    await screen.findByRole('heading', { name: 'Evidence' })
    router.history.back()
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
    router.history.forward()
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        field: 'title',
        lens: 'all',
        subview: 'evidence',
      }),
    )
  })
})

it('keeps automatic withholding attached to its field on direct Evidence entry', async () => {
  const reason =
    'Automatic decision withheld: a contributing provider lookup did not supply usable evidence. You can review this field.'
  const http = createHttp()
  const field = detail.fields[0]
  if (!field) throw new Error('Detail fixture field missing')
  http.getCaseDetail = vi.fn(async () => ({
    ...detail,
    fields: [{ ...field, automaticWithheldReason: reason }],
  }))
  renderRoute('/review/case-1?subview=evidence', http)
  expect(await screen.findByText(reason)).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Title' })).toBeVisible()
  expect(screen.queryByRole('button', { name: /Choose/ })).toBeNull()
  expect(screen.queryByRole('radio')).toBeNull()
})

describe('bounded diagnostic return context', () => {
  it.each([undefined, '', 1, [], ['x'], {}, 'x'.repeat(129)])(
    'rejects invalid context %j',
    (value) => {
      expect(
        normalizeDetailSearch({
          q: value,
          cursor: value,
          attentionCursor: value,
          preparation: value,
          field: value,
          attention: value,
          lens: value,
          subview: value,
        }),
      ).toEqual({ lens: 'all', subview: 'evidence' })
    },
  )
  it('projects bounded raw context through tabs, history and Back', async () => {
    const user = userEvent.setup()
    const context = {
      lens: 'needs_attention',
      field: 'title',
      q: 'x'.repeat(128),
      cursor: 'library-page',
      attentionCursor: 'attention-page',
      preparation: 'run',
      attention: 'open',
    }
    expect(normalizeDetailSearch({ ...context, subview: 'history' })).toEqual({
      ...context,
      subview: 'history',
    })
    const params = new URLSearchParams({
      ...context,
      subview: 'evidence',
      preview: 'forged',
      work: 'foreign',
      limit: '500',
      unknown: 'foreign',
    })
    const { router } = renderRoute(`/review/case-1?${params.toString()}`)
    await screen.findByRole('heading', { name: 'Evidence' })
    for (const tab of ['History', 'Evidence']) {
      const link = screen.getByRole('link', { name: tab })
      expect(
        Object.fromEntries(
          new URL(link.getAttribute('href') ?? '', 'https://example.invalid').searchParams,
        ),
      ).toEqual({ ...context, subview: tab.toLowerCase() })
      await user.click(link)
      await screen.findByRole('heading', { name: tab })
    }
    router.history.back()
    await screen.findByRole('heading', { name: 'History' })
    router.history.forward()
    await screen.findByRole('heading', { name: 'Evidence' })
    expect(
      Object.fromEntries(
        new URL(
          screen.getByRole('link', { name: 'Back to work' }).getAttribute('href') ?? '',
          'https://example.invalid',
        ).searchParams,
      ),
    ).toEqual({ ...context, preview: 'case-1' })
  })
  it.each(['loading', 'unavailable', 'stale'] as const)(
    'keeps bounded return context in %s',
    async (state) => {
      const http = createHttp()
      http.getCaseDetail = vi.fn(() =>
        state === 'loading'
          ? new Promise<CaseDetailView>(() => undefined)
          : Promise.reject(new ApiError(state === 'stale' ? 409 : 503, state)),
      )
      renderRoute(
        '/review/case-1?lens=needs_attention&q=needle&cursor=page-two&attentionCursor=queue-two&attention=open&preparation=run&field=title&preview=forged&work=foreign&limit=100',
        http,
      )
      await screen.findByRole('heading', {
        name:
          state === 'loading' ? 'Loading the review case…' : 'The review case could not be opened',
      })
      expect(
        Object.fromEntries(
          new URL(
            screen.getByRole('link', { name: 'Back to work' }).getAttribute('href') ?? '',
            'https://example.invalid',
          ).searchParams,
        ),
      ).toEqual({
        lens: 'needs_attention',
        q: 'needle',
        cursor: 'page-two',
        attentionCursor: 'queue-two',
        attention: 'open',
        preparation: 'run',
        field: 'title',
        preview: 'case-1',
      })
    },
  )
  it('drops malformed raw context from actual rendered links', async () => {
    const params = new URLSearchParams({
      q: '["array"]',
      cursor: '1',
      attentionCursor: '{}',
      preparation: 'x'.repeat(129),
      attention: 'closed',
      field: '',
      preview: 'foreign',
      work: 'foreign',
      unknown: 'x',
    })
    renderRoute(`/review/case-1?${params.toString()}`)
    await screen.findByRole('heading', { name: 'Evidence' })
    for (const name of ['History', 'Back to work']) {
      const query = Object.fromEntries(
        new URL(
          screen.getByRole('link', { name }).getAttribute('href') ?? '',
          'https://example.invalid',
        ).searchParams,
      )
      expect(query).toEqual(
        name === 'History'
          ? { lens: 'all', subview: 'history' }
          : { lens: 'all', preview: 'case-1' },
      )
    }
  })
})

it.each(['evidence', 'history'])(
  'keeps %s diagnostic-only while retaining authored history',
  async (subview) => {
    const authored = readCaseDetail(descriptionAdviceFixture)
    const candidate = authored.fields.find((field) => field.destination === 'description')
      ?.descriptionAdvice?.candidate
    if (!candidate) throw new Error('description fixture requires its candidate')
    const http = createHttp()
    http.getCaseDetail = vi.fn(async () => ({ ...authored, id: 'case-1' }))
    http.getHistory = vi.fn(async () => ({
      items: [
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_advice_recorded',
          state: 'succeeded' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
          chosenBy: null,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_selected',
          state: 'superseded' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: true,
          chosenBy: 'owner' as const,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_selected',
          state: 'succeeded' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: true,
          chosenBy: 'owner' as const,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_selected',
          state: 'succeeded' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: true,
          chosenBy: 'automatic' as const,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_advice_recorded',
          state: 'failed' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate: null,
          selected: false,
          chosenBy: null,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_advice_recorded',
          state: 'running' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate: null,
          selected: false,
          chosenBy: null,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_application',
          state: 'reconciling' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
          chosenBy: null,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_application',
          state: 'failed' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
          chosenBy: null,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_application',
          state: 'verified' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
          chosenBy: null,
        },
        {
          category: 'description' as const,
          occurredAt: 1786000000,
          outcome: 'description_application',
          state: 'stale' as const,
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
          chosenBy: null,
        },
      ],
    }))
    renderRoute(`/review/case-1?field=description&subview=${subview}`, http)
    expect(await screen.findByRole('heading', { level: 1, name: authored.title })).toBeVisible()
    expect(
      screen.queryByRole('button', { name: /Choose advised|Retry description|Use advised/ }),
    ).toBeNull()
    expect(screen.queryByRole('radio', { name: /Choose advised|Use advised/ })).toBeNull()
    expect(screen.queryByRole('article', { name: 'Advised description' })).toBeNull()
    if (subview === 'history') {
      expect(
        await screen.findByText('Written to the library and checked.', { exact: false }),
      ).toBeVisible()
      // The store's "stale" reads in the owner's words (#1542).
      expect(
        screen.getByText('The write was closed before it finished.', { exact: false }),
      ).toBeVisible()
      expect(document.body.textContent).not.toMatch(/\bstale\b/iu)
      // Each write state reads as a sentence, and a choice names who made it.
      expect(screen.getByText('The write failed.', { exact: false })).toBeVisible()
      expect(screen.getByText('Advice was written.', { exact: false })).toBeVisible()
      expect(screen.getByText('You chose this description.', { exact: false })).toBeVisible()
      expect(screen.getByText('Automatic chose this description.', { exact: false })).toBeVisible()
      expect(document.body.textContent).not.toMatch(/Write (closed|failed)\./u)
      // Older steps read as done, not as still happening.
      expect(screen.getByText('Checking the write started.', { exact: false })).toBeVisible()
      expect(screen.getByText('Advice writing started.', { exact: false })).toBeVisible()
      expect(document.body.textContent).not.toMatch(/\b(is being|is in progress|will be)\b/u)
      // Only advice that stopped says it has no text; advice still being
      // written has none yet, which is not news.
      expect(screen.getAllByText('No description was written.')).toHaveLength(1)
      const running = screen
        .getByText('Advice writing started.', { exact: false })
        .closest('article')
      expect(running).not.toHaveTextContent('No description was written.')
      expect(
        screen.getByText('Replaced by a later choice.', {
          exact: false,
        }),
      ).toBeVisible()
      expect(screen.getAllByRole('heading', { name: 'Description advice' })).toHaveLength(10)
    } else {
      await waitFor(() => expect(http.getEvidence).toHaveBeenCalled())
      expect(screen.queryByText(candidate.output.description)).toBeNull()
      expect(http.getHistory).not.toHaveBeenCalled()
    }
  },
)

it('retains unconfirmed incumbent evidence in actual mapped owner history', async () => {
  const http = createHttp()
  http.getHistory = vi.fn(async () =>
    readHistory(
      caseHistory({
        items: [
          historyCoverItem({
            kind: 'kept_incumbent',
            occurred_at: 3,
            consideration: coverConsideration({
              candidates: [
                coverCandidate({
                  incumbent: true,
                  artifact_id: 'retained-incumbent',
                  match_state: 'match_not_confirmed',
                }),
              ],
              policy: null,
              reviewer_status: 'unavailable',
            }),
            decision: decisionProjection({ outcome: 'keep_current', selected_value: null }),
          }),
        ],
      }),
    ),
  )
  renderRoute('/review/case-1?lens=all&field=cover&subview=history', http)
  expect(await screen.findByText(/You kept the current cover\./u)).toHaveTextContent(
    'You kept the current cover. No source confirmed the match.',
  )
})

it('says a chosen cover from the matched book leaves the recording unconfirmed (#1642)', async () => {
  const http = createHttp()
  http.getHistory = vi.fn(async () =>
    readHistory(
      caseHistory({
        items: [
          historyCoverItem({
            kind: 'selected',
            occurred_at: 3,
            consideration: coverConsideration({
              candidates: [
                coverCandidate({
                  artifact_id: 'matched-book-cover',
                  deterministic_rank: null,
                  deterministic_rank_key: null,
                  identity: coverIdentity({ outcome: 'unknown' }),
                  match_reason: 'matched_book',
                  match_state: 'match_not_confirmed',
                  selectable: false,
                  state: 'excluded',
                }),
              ],
              policy: coverPolicyReport({
                acceptable_candidate_references: [],
                reasons: ['no_confirmed_candidates'],
                status: 'abstained',
              }),
              reviewer_status: 'unavailable',
            }),
            decision: decisionProjection({
              outcome: 'accept',
              selected_value: { kind: 'cover', value: 'matched-book-cover' },
            }),
          }),
        ],
      }),
    ),
  )
  renderRoute('/review/case-1?lens=all&field=cover&subview=history', http)
  expect(await screen.findByText(/You chose a proposed cover\./u)).toHaveTextContent(
    'You chose a proposed cover. This cover is from the matched book and is not confirmed for this recording.',
  )
  expect(screen.getByText('No cover is confirmed for this recording')).toBeVisible()
  expect(screen.queryByText('No cover is confirmed for this book')).toBeNull()
  expect(screen.queryByText(/No source confirmed the match/u)).toBeNull()
})
