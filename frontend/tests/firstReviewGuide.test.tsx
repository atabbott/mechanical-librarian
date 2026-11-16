import { readFileSync } from 'node:fs'

import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest'

import type { ReviewHttp } from '../src/api/http'
import { createHttp, createLiveStream, renderRoute } from './fixtures'

// jsdom has no native dialog methods. The top layer, the inert page and Escape
// are proved in a real browser (`test_review_browser.py`).
Object.defineProperties(HTMLDialogElement.prototype, {
  showModal: {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.setAttribute('open', '')
    },
  },
  close: {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.removeAttribute('open')
    },
  },
})

const owed = () => /(?:^|; )ml_first_review=owed(?:;|$)/u.test(document.cookie)
const owe = () => {
  document.cookie = 'ml_first_review=owed; Path=/; Max-Age=2592000; SameSite=Strict'
}
afterEach(() => {
  document.cookie = 'ml_first_review=; Path=/; Max-Age=0'
})

type Page = Awaited<ReturnType<ReviewHttp['getAttention']>>
const waiting: Page = {
  total: 1,
  items: [
    {
      id: 'case-1',
      destination: 'title',
      title: 'A Book',
      authors: [],
      library: 'Books',
      mediaKind: 'epub',
      classificationReason: 'Title needs a decision',
      notices: [],
      provenance: [],
      workAttentionCount: 1,
    },
  ],
}
const withQueue = (page: Page = waiting) => {
  const http = createHttp()
  http.getAttention = vi.fn<ReviewHttp['getAttention']>(async () => page)
  return http
}
// The queue's rows are on the page only while the Attention rail is open.
const queueOpen = '/library?lens=all&attention=open'
const firstRow = async () =>
  within(await screen.findByRole('complementary', { name: 'Attention queue' })).findByRole(
    'button',
    { name: /Title needs a decision/u },
  )
const openGuide = async (http = withQueue()) => {
  owe()
  const rendered = renderRoute(queueOpen, http)
  const dialog = await screen.findByRole('dialog', { name: 'How a decision works' })
  return { ...rendered, dialog, user: userEvent.setup() }
}
const button = (dialog: HTMLElement, name: string) => within(dialog).getByRole('button', { name })
const stepHeading = (dialog: HTMLElement) =>
  within(dialog)
    .getAllByRole('heading', { level: 3 })
    .map((heading) => heading.textContent)
// Every call a page could use to change something: all but the reads.
const changes = (http: ReviewHttp) =>
  Object.entries(http)
    .filter(([name, call]) => !name.startsWith('get') && vi.isMockFunction(call))
    .map(([name, call]) => [name, vi.mocked(call as () => unknown).mock.calls.length])

describe('first review guide', () => {
  it('does not render without the cookie setup leaves', async () => {
    renderRoute(queueOpen, withQueue())

    await firstRow()
    expect(document.querySelector('dialog')).toBeNull()
    expect(screen.queryByText('How a decision works')).toBeNull()
  })

  it('stays owed and adds nothing while no field needs a decision', async () => {
    owe()
    const http = createHttp()
    renderRoute(queueOpen, http)

    await waitFor(() => expect(http.getAttention).toHaveBeenCalled())
    await screen.findByRole('complementary', { name: 'Attention queue' })
    expect(document.querySelector('dialog')).toBeNull()
    expect(document.body.textContent).not.toMatch(/How a decision works|Example|Step \d/u)
    expect(owed()).toBe(true)
  })

  it('opens at the first step, on its heading, when a field is waiting at load', async () => {
    // jsdom leaves the page behind a dialog live, so Library can take focus
    // back here; what is checked is that opening put focus on the heading.
    const focused: string[] = []
    const note = (event: FocusEvent) => focused.push((event.target as Element).id)
    document.addEventListener('focusin', note)
    onTestFinished(() => document.removeEventListener('focusin', note))
    const { dialog } = await openGuide()

    expect(dialog).toHaveAttribute('open')
    expect(dialog.parentElement).toBe(document.body)
    expect(within(dialog).getByRole('heading', { level: 2 })).toHaveAttribute(
      'id',
      'first-review-heading',
    )
    expect(focused).toContain('first-review-heading')
    expect(within(dialog).getByText('Step 1 of 3', { ignore: '[aria-hidden] *' })).toBeVisible()
    // Opening does not use it up: a reload before closing shows it again.
    expect(owed()).toBe(true)
  })

  it('opens when a live update brings the first field', async () => {
    owe()
    const http = createHttp()
    const updates = createLiveStream()
    renderRoute('/library?lens=all', http, updates)
    await waitFor(() => expect(http.getAttention).toHaveBeenCalled())
    expect(document.querySelector('dialog')).toBeNull()

    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async () => waiting)
    act(() => updates.announce({ sequence: 1, subject: null, topic: 'library' }))

    expect(await screen.findByRole('dialog', { name: 'How a decision works' })).toHaveAttribute(
      'open',
    )
  })

  it('shows each step with its words, and moves with Next and Back', async () => {
    const { dialog, user } = await openGuide()
    const live = dialog.querySelector('[aria-live="polite"]')
    const step = () => live?.textContent

    expect(stepHeading(dialog)).toEqual(['Which title should this work use?', 'Evidence'])
    expect(step()).toBe(
      'EvidenceStep 1 of 3Each field asks one question. Evidence under a value lists the sources that gave it.',
    )
    expect(within(dialog).getByText('Example field')).toBeVisible()
    expect(within(dialog).queryByRole('button', { name: 'Back' })).toBeNull()

    await user.click(button(dialog, 'Next'))
    expect(step()).toBe(
      'ChoicesStep 2 of 3Keep the current value or choose a new one. Try it on this example.',
    )
    // Next is still there, so focus stays on it.
    expect(button(dialog, 'Next')).toHaveFocus()

    await user.click(button(dialog, 'Next'))
    expect(step()).toBe(
      'WritesStep 3 of 3A choice records your decision for this one field. Nothing is written to Audiobookshelf until you give write permission in Activity.',
    )
    expect(within(dialog).queryByRole('button', { name: 'Next' })).toBeNull()
    // Next is gone, so focus moves to the step's main button.
    expect(button(dialog, 'Open your first field')).toHaveFocus()
    expect(button(dialog, 'Open your first field')).toHaveClass('button-primary')
    // The example is not live on the last step.
    expect(dialog.querySelector('.first-review-example')).toHaveAttribute('inert')

    await user.click(button(dialog, 'Back'))
    expect(step()).toMatch(/^ChoicesStep 2 of 3/u)
    expect(dialog.querySelector('.first-review-example')).not.toHaveAttribute('inert')
    await user.click(button(dialog, 'Back'))
    expect(step()).toMatch(/^EvidenceStep 1 of 3/u)
    expect(button(dialog, 'Next')).toHaveFocus()
    // One copy of every step holds the dialog's height; none is read aloud.
    expect(dialog.querySelectorAll('.first-review-step[aria-hidden="true"]')).toHaveLength(3)
    expect(new Set([...document.querySelectorAll('[id]')].map((each) => each.id)).size).toBe(
      document.querySelectorAll('[id]').length,
    )
  })

  it('lets the example be chosen without sending anything', async () => {
    const { dialog, http, user } = await openGuide()
    const before = changes(http)
    const status = within(dialog).getByRole('status')
    expect(status).toBeEmptyDOMElement()

    // The example is on the real cards: evidence, its control and the radios.
    expect(within(dialog).getByText('2 sources confirm this choice.')).toBeVisible()
    await user.click(
      button(dialog, 'Evidence for proposed value: Example Story'), // closes it
    )
    expect(within(dialog).queryByText('2 sources confirm this choice.')).toBeNull()
    await user.click(button(dialog, 'Next'))
    const proposed = within(dialog).getByRole('radio', {
      name: 'Choose proposed value: Example Story',
    })
    await user.click(proposed)

    expect(proposed).toBeChecked()
    expect(within(proposed).getByText('Your choice')).toBeVisible()
    expect(status).toHaveTextContent('This is an example, so nothing was recorded.')
    await user.click(
      within(dialog).getByRole('radio', { name: 'Choose current value: Example Story - Disc 1' }),
    )
    expect(proposed).not.toBeChecked()
    expect(changes(http)).toEqual(before)
    expect(before.length).toBeGreaterThan(10)
    expect(owed()).toBe(true)
  })

  it.each([0, 1, 2])('Close on step %i ends it for good and leaves the dialog', async (presses) => {
    const { dialog, user } = await openGuide()
    for (let press = 0; press < presses; press += 1) await user.click(button(dialog, 'Next'))

    await user.click(button(dialog, 'Close'))

    expect(document.querySelector('dialog')).toBeNull()
    expect(owed()).toBe(false)
    expect(document.querySelector('#main-content')).toContainElement(
      document.activeElement as HTMLElement,
    )
    cleanup()
    renderRoute(queueOpen, withQueue())
    await firstRow()
    expect(document.querySelector('dialog')).toBeNull()
  })

  it('Escape ends it as Close does', async () => {
    const { dialog } = await openGuide()

    // A browser fires `cancel` for Escape; the browser journey presses the key.
    const cancel = new Event('cancel', { cancelable: true })
    fireEvent(dialog, cancel)

    await waitFor(() => expect(document.querySelector('dialog')).toBeNull())
    expect(cancel.defaultPrevented).toBe(true)
    expect(owed()).toBe(false)
  })

  it.each(['the search box', 'nothing'])(
    'returns focus to where it was when %s had it',
    async (holder) => {
      owe()
      const http = createHttp()
      const updates = createLiveStream()
      renderRoute(queueOpen, http, updates)
      const search = await screen.findByRole('searchbox')
      await waitFor(() => expect(http.getAttention).toHaveBeenCalled())
      // Let Library finish its own focus move for the route before choosing.
      await act(() => new Promise((resolve) => setTimeout(resolve, 100)))
      if (holder === 'nothing') (document.activeElement as HTMLElement).blur()
      else search.focus()
      http.getAttention = vi.fn<ReviewHttp['getAttention']>(async () => waiting)
      act(() => updates.announce({ sequence: 1, subject: null, topic: 'library' }))
      const dialog = await screen.findByRole('dialog', { name: 'How a decision works' })

      fireEvent.click(button(dialog, 'Close'))

      await waitFor(() => expect(document.querySelector('dialog')).toBeNull())
      expect(
        holder === 'nothing' ? document.querySelector('#main-content [data-route-focus]') : search,
      ).toHaveFocus()
    },
  )

  it('opens the field the Attention queue’s first row opens', async () => {
    const control = renderRoute(queueOpen, withQueue())
    fireEvent.click(await firstRow())
    await waitFor(() => expect(control.router.state.location.search.preview).toBe('case-1'))
    const fromQueue = control.router.state.location.search
    cleanup()

    const { dialog, router, user } = await openGuide()
    await user.click(button(dialog, 'Next'))
    await user.click(button(dialog, 'Next'))
    await user.click(button(dialog, 'Open your first field'))

    await waitFor(() => expect(router.state.location.search).toEqual(fromQueue))
    expect(fromQueue).toMatchObject({ preview: 'case-1', field: 'title' })
    expect(document.querySelector('dialog')).toBeNull()
    expect(owed()).toBe(false)
  })

  it('ends on Close when the queue has emptied by the last step', async () => {
    const updates = createLiveStream()
    owe()
    const http = withQueue()
    renderRoute('/library?lens=all', http, updates)
    const dialog = await screen.findByRole('dialog', { name: 'How a decision works' })
    const user = userEvent.setup()
    await user.click(button(dialog, 'Next'))

    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async () => ({ total: 0, items: [] }))
    act(() => updates.announce({ sequence: 1, subject: null, topic: 'library' }))
    await waitFor(() => expect(http.getAttention).toHaveBeenCalled())
    await user.click(button(dialog, 'Next'))

    // It stays open, and its last step no longer offers a field to open.
    await waitFor(() => expect(button(dialog, 'Close')).toHaveClass('button-primary'))
    expect(within(dialog).queryByRole('button', { name: 'Open your first field' })).toBeNull()
    expect(dialog).toHaveAttribute('open')
  })

  it('reaches no API and no storage, and writes only its cookie’s removal', () => {
    const source = readFileSync('src/routes/FirstReviewGuide.tsx', 'utf8')
    const imports = [...source.matchAll(/^import (type )?.* from '(.+)'$/gmu)].map(
      (match) => `${match[1] ?? ''}${match[2] ?? ''}`,
    )

    expect(imports).toEqual([
      'react',
      'react-dom',
      'type ../api/http',
      '../choiceGroup',
      './FieldDecisionChoices',
    ])
    expect(source).not.toMatch(/fetch\(|XMLHttpRequest|localStorage|sessionStorage|indexedDB/u)
    expect(source.match(/document\.cookie\s*=[^=]/gu)).toHaveLength(1)
    expect(source).toContain("document.cookie = 'ml_first_review=; Path=/; Max-Age=0'")
    // Steps are one list, and the counter takes its length.
    expect(source).toContain('of {steps.length}')
  })
})
