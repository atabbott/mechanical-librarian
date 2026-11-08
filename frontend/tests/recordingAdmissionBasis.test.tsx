// How the matched recording was found when its title differs from this
// audiobook's (#1723), and where a companion EPUB's ISBN came from in Sources.

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type {
  ProviderOutcomeView,
  RecordingAdmissionBasisView,
  ReportDetailView,
  ReviewHttp,
} from '../src/api/http'
import { RecordingIdentityBasis } from '../src/components/RecordingIdentityBasis'
import { createHttp, detail, library, renderRoute } from './fixtures'

const running = 'Its running time closely matches this audiobook’s.'

const admission = (
  basis: RecordingAdmissionBasisView['basis'],
  searched: Partial<RecordingAdmissionBasisView['searched']>,
): RecordingAdmissionBasisView => ({
  basis,
  searched: { title: null, authors: null, isbn: null, ...searched },
  provider: 'Audible',
  candidate: 'audible-product-B0LINKED01',
})

const renderNote = (value: RecordingAdmissionBasisView) => {
  render(<RecordingIdentityBasis value={null} admission={value} scope="recording" offered={[]} />)
  return screen.getByRole('region', { name: 'How this recording was matched' })
}

describe('the admission sentence', () => {
  it.each([
    [
      'path_leaf',
      'Dune',
      `Mechanical Librarian found the matched recording by searching with “Dune”, read from this folder’s name, which differs from this audiobook’s title. ${running}`,
    ],
    [
      'title_tag',
      'Dune Messiah',
      `Mechanical Librarian found the matched recording by searching with “Dune Messiah”, read from the title tag in this audiobook’s audio file, which differs from this audiobook’s title. ${running}`,
    ],
    [
      'companion_title',
      'Children of Dune',
      `Mechanical Librarian found the matched recording by searching with “Children of Dune”, the title of the EPUB in this folder, which differs from this audiobook’s title. ${running}`,
    ],
  ] as const)('reads %s with the searched text quoted', (basis, title, sentence) => {
    const section = renderNote(admission(basis, { title }))
    expect(section).toHaveTextContent(sentence)
    // The quoted value is isolated, so a right-to-left title keeps its place.
    expect(section.querySelector('bdi')).toHaveTextContent(title)
  })

  it('names one author of the companion work, singular', () => {
    const section = renderNote(
      admission('companion_work', { title: 'Companion Title', authors: ['Ada Author'] }),
    )
    expect(section).toHaveTextContent(
      'Mechanical Librarian found the matched recording by searching with the title and author of the EPUB in this folder, “Companion Title” by Ada Author, because this audiobook’s own title or author is missing or incomplete. Its running time closely matches this audiobook’s.',
    )
  })

  it('lists three authors of the companion work, plural', () => {
    const section = renderNote(
      admission('companion_work', {
        title: 'Companion Title',
        authors: ['Ada Author', 'Ben Writer', 'Cy Scribe'],
      }),
    )
    expect(section).toHaveTextContent(
      'searching with the title and authors of the EPUB in this folder, “Companion Title” by Ada Author, Ben Writer, and Cy Scribe, because',
    )
    expect([...section.querySelectorAll('bdi')].map((node) => node.textContent)).toEqual([
      'Companion Title',
      'Ada Author, Ben Writer, and Cy Scribe',
    ])
  })

  it.each([
    ['an ISBN-13', '9780306406157'],
    ['an ISBN-10 ending in X', '080442957X'],
  ])('shows the companion ISBN as the EPUB states it, %s', (_label, isbn) => {
    const section = renderNote(admission('companion_isbn_work', { isbn }))
    expect(section).toHaveTextContent(
      `Hardcover lists the matched recording as an audio edition of the book with ISBN ${isbn}, which is stated in the EPUB in this folder. The matched recording’s running time closely matches this audiobook’s.`,
    )
    expect(section.querySelector('bdi')).toHaveTextContent(isbn)
  })

  it('names the recording source the way the chapter note does', async () => {
    const section = renderNote(admission('path_leaf', { title: 'Dune' }))
    await userEvent.setup().click(within(section).getByText('Recording source'))
    expect(within(section).getByText(/^Audible · /u)).toBeVisible()
  })

  it('labels the section for either note, with no live region', () => {
    render(
      <RecordingIdentityBasis
        value={{
          basis: 'chapter_runtime_and_structure',
          provider: 'Audnexus',
          candidate: 'recording-1',
        }}
        scope="recording"
        offered={[]}
      />,
    )
    const chapter = screen.getByRole('region', { name: 'How this recording was matched' })
    expect(chapter).toHaveTextContent('Its chapter timings and structure confirm this recording')
    expect(chapter).not.toHaveAttribute('aria-live')
    expect(chapter.querySelector('[role="status"]')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Recording identity evidence' })).toBeNull()
  })
})

describe('the Library decision surface', () => {
  it('opens Recording match for an admission basis alone', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => ({
        ...detail,
        target: { ...detail.target, mediaKind: 'm4b' as const },
        audiobookReviewScope: 'recording' as const,
        recordingAdmissionBasis: admission('companion_isbn_work', { isbn: '9780306406157' }),
      })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)
    const context = await screen.findByRole('region', { name: 'Recording match' })
    expect(within(context).getByRole('heading', { name: 'Recording match' })).toBeVisible()
    expect(
      within(context).getByRole('region', { name: 'How this recording was matched' }),
    ).toHaveTextContent(
      'Hardcover lists the matched recording as an audio edition of the book with ISBN 9780306406157',
    )
  })

  it('keeps Recording match closed for a recording with neither note', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async () => ({
        ...detail,
        target: { ...detail.target, mediaKind: 'm4b' as const },
        audiobookReviewScope: 'recording' as const,
      })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)
    expect(await screen.findByRole('navigation', { name: 'Review fields' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Recording match' })).toBeNull()
  })
})

const companionRow = (
  status: ProviderOutcomeView['status'],
  reason: string,
): ProviderOutcomeView => ({
  companionIsbn: true,
  coverOnly: false,
  providerRequests: 1,
  laneKind: 'isbn',
  provider: 'Hardcover',
  reason,
  status,
})

const companionRows = [
  companionRow('candidates', "Looked up the book and its audio editions by the EPUB's ISBN."),
  companionRow('no_match', "The EPUB's ISBN did not lead to this audiobook."),
  companionRow('unavailable', 'Did not answer.'),
]

const reportDetail = (overrides: Partial<ReportDetailView> = {}): ReportDetailView => ({
  chapters: [],
  chaptersTruncated: false,
  coverPresent: false,
  coverSurface: null,
  evidence: [],
  evidenceTruncated: false,
  item: 'item-9',
  library: 'audio',
  mediaHealth: [],
  mediaKind: 'm4b',
  providerSources: [],
  providerEvidence: [],
  providerEvidenceTruncated: false,
  audiobookReviewScope: 'recording',
  recordingIdentityBasis: null,
  recordingAdmissionBasis: null,
  reportId: 'report_' + 'a'.repeat(64),
  title: 'Recorded work',
  ...overrides,
})

describe('the companion ISBN row in Sources', () => {
  const expectCompanionRows = (rows: HTMLElement[]) => {
    expect(rows).toHaveLength(3)
    for (const row of rows) expect(row).toHaveTextContent('ISBN lookup (EPUB in this folder)')
    expect(rows[0]).toHaveTextContent(
      "Looked up the book and its audio editions by the EPUB's ISBN.",
    )
    expect(rows[1]).toHaveTextContent("The EPUB's ISBN did not lead to this audiobook.")
    // Every other ending keeps its generic reason under the companion label.
    expect(rows[2]).toHaveTextContent('Did not answer.')
  }

  it('names the EPUB on case Evidence Sources', async () => {
    const user = userEvent.setup()
    renderRoute('/review/case-1?field=title&subview=evidence', {
      ...createHttp(),
      getEvidence: vi.fn<ReviewHttp['getEvidence']>(async () => ({
        items: [],
        missingLineageCount: 0,
        sourceLedger: companionRows,
      })),
    })
    await user.click(await screen.findByText('Sources'))
    const ledger = document.querySelector('.source-ledger')
    if (!(ledger instanceof HTMLElement)) throw new Error('the Sources ledger did not render')
    expectCompanionRows(within(ledger).getAllByRole('listitem'))
  })

  it('names the EPUB on WorkSummary Sources, beside the admission sentence', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const recorded = {
      ...baseWork,
      id: 'library_work_audio',
      workId: 'library_work_audio',
      title: 'Recorded work',
      mediaKind: 'm4b' as const,
      attentionCount: 0,
      attention: 'no_attention_needed' as const,
      detailUrl: '/api/v1/library/report?connection=primary&library=audio&item=item-9',
      detailKind: 'report' as const,
      summary: {
        state: 'no_decision_recorded' as const,
        reason: 'Nothing to decide for this work.',
        fields: [],
      },
      preparation: null,
      preparationOutcomes: companionRows,
    }
    renderRoute('/library?lens=all&work=library_work_audio', {
      ...createHttp(),
      getReportDetail: vi.fn<ReviewHttp['getReportDetail']>(async () =>
        reportDetail({
          recordingAdmissionBasis: admission('companion_isbn_work', { isbn: '9780306406157' }),
        }),
      ),
      getLibrary: vi.fn(async () => ({ ...library, works: [...library.works, recorded] })),
    })
    const evidence = await screen.findByRole('region', { name: 'Recorded evidence' })
    expect(
      within(evidence).getByRole('region', { name: 'How this recording was matched' }),
    ).toHaveTextContent('Hardcover lists the matched recording as an audio edition')
    const sources = document.querySelector('.work-sources')
    if (!(sources instanceof HTMLElement)) throw new Error('WorkSummary Sources did not render')
    await user.click(within(sources).getByText('Sources'))
    expectCompanionRows(within(sources).getAllByRole('listitem'))
  })

  it('names the EPUB in the Library comparison footer', async () => {
    const user = userEvent.setup()
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires a work')
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        ...library,
        works: [{ ...first, preparationOutcomes: companionRows }],
      })),
    })
    const footer = await screen.findByText('3 source lookups')
    await user.click(footer)
    const sources = footer.closest('.work-sources')
    if (!(sources instanceof HTMLElement)) throw new Error('the comparison footer did not render')
    expectCompanionRows(within(sources).getAllByRole('listitem'))
  })
})

// iTunes is asked for an audiobook's cover alone (#1779): what it found is a
// cover, never a match, and its failures keep their own labels.
const coverOnlyRow = (
  status: ProviderOutcomeView['status'],
  reason: string,
): ProviderOutcomeView => ({
  companionIsbn: false,
  coverOnly: true,
  providerRequests: 1,
  laneKind: 'work',
  provider: 'iTunes',
  reason,
  status,
})

const coverOnlyRows = [
  coverOnlyRow('candidates', 'Searched for cover art only; nothing else it returns is compared.'),
  coverOnlyRow('no_match', 'Searched for cover art only and found none.'),
  coverOnlyRow(
    'unavailable',
    'Searched for cover art only. No cover came back, and it is not retried.',
  ),
]

describe('the cover-only iTunes row in Sources', () => {
  const expectCoverOnlyRows = (rows: HTMLElement[]) => {
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Cover found')
    expect(rows[0]).not.toHaveTextContent('Matches found')
    expect(rows[1]).toHaveTextContent('No match')
    expect(rows[2]).toHaveTextContent('Unavailable')
    expect(rows[2]).toHaveTextContent('No cover came back, and it is not retried.')
  }

  it('says Cover found on case Evidence Sources', async () => {
    const user = userEvent.setup()
    renderRoute('/review/case-1?field=title&subview=evidence', {
      ...createHttp(),
      getEvidence: vi.fn<ReviewHttp['getEvidence']>(async () => ({
        items: [],
        missingLineageCount: 0,
        sourceLedger: coverOnlyRows,
      })),
    })
    await user.click(await screen.findByText('Sources'))
    const ledger = document.querySelector('.source-ledger')
    if (!(ledger instanceof HTMLElement)) throw new Error('the Sources ledger did not render')
    expectCoverOnlyRows(within(ledger).getAllByRole('listitem'))
  })

  it('says Cover found on WorkSummary Sources', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const recorded = {
      ...baseWork,
      id: 'library_work_audio',
      workId: 'library_work_audio',
      title: 'Recorded work',
      mediaKind: 'm4b' as const,
      attentionCount: 0,
      attention: 'no_attention_needed' as const,
      detailUrl: '/api/v1/library/report?connection=primary&library=audio&item=item-9',
      detailKind: 'report' as const,
      summary: {
        state: 'no_decision_recorded' as const,
        reason: 'No review decision is recorded.',
        fields: [],
      },
      preparation: null,
      preparationOutcomes: coverOnlyRows,
    }
    renderRoute('/library?lens=all&work=library_work_audio', {
      ...createHttp(),
      getReportDetail: vi.fn<ReviewHttp['getReportDetail']>(async () => reportDetail()),
      getLibrary: vi.fn(async () => ({ ...library, works: [...library.works, recorded] })),
    })
    await screen.findByRole('region', { name: 'Recorded evidence' })
    const sources = document.querySelector('.work-sources')
    if (!(sources instanceof HTMLElement)) throw new Error('WorkSummary Sources did not render')
    await user.click(within(sources).getByText('Sources'))
    expectCoverOnlyRows(within(sources).getAllByRole('listitem'))
  })
})
