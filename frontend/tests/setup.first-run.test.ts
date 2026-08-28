import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { execFileSync } from 'node:child_process'

import { fireEvent, screen, waitFor, within } from '@testing-library/dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import settingsVocabulary from '../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'

const source = readFileSync(
  resolve(process.cwd(), '../src/mechanical_librarian/interfaces/web/setup_frontend.py'),
  'utf8',
)
const css = /SETUP_CSS = """([\s\S]*?)"""/u.exec(source)?.[1]

if (!css) throw new Error('setup assets are unavailable')
const script = execFileSync(
  resolve(process.cwd(), '../.venv/bin/python'),
  [
    '-c',
    "import sys;sys.path.insert(0,'src');from mechanical_librarian.interfaces.web.setup_frontend import SETUP_JS;print(SETUP_JS,end='')",
  ],
  { cwd: resolve(process.cwd(), '..'), encoding: 'utf8' },
)

const response = (body: object, status = 200) =>
  new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status,
  })

const setupState = (nextStep: string, overrides: Record<string, unknown> = {}) => ({
  completed_steps: ['claim', 'connect'],
  connection: { endpoint: 'https://example.invalid', name: 'primary' },
  csrf_token: 'csrf-token',
  libraries: [],
  mounts: [],
  next_step: nextStep,
  providers: [],
  revision: 3,
  schema_version: 'setup-state-v2',
  ...overrides,
})

const requestUrl = (request: RequestInfo | URL) => {
  if (typeof request === 'string') return request
  return request instanceof URL ? request.href : request.url
}

const jsonBody = (body: BodyInit | null | undefined): unknown => {
  if (typeof body !== 'string') throw new Error('JSON request body missing')
  return JSON.parse(body) as unknown
}

const runSetup = () => {
  window.eval(script)
}

beforeEach(() => {
  document.head.innerHTML = `<style>${css}</style>`
  document.body.innerHTML = `
    <ol id="setup-ledger"></ol>
    <main id="setup-main"></main>
    <div id="setup-status"></div>
  `
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  )
})

describe('shipped first-run setup script', () => {
  it('creates first-owner access with confirmation and the issued nonce', async () => {
    let claimed = false
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/claim')
        return response({ claimed, nonce: 'n'.repeat(32), revision: 1 })
      if (url === '/api/setup/state' && !claimed) return response({ error: 'unauthorized' }, 401)
      if (url === '/api/setup/session' && init?.method === 'POST') {
        expect(init.headers).toEqual(expect.objectContaining({ 'x-setup-nonce': 'n'.repeat(32) }))
        expect(jsonBody(init.body)).toEqual({
          confirmation: 'correct horse battery staple',
          expected_revision: 1,
          password: 'correct horse battery staple',
        })
        claimed = true
        return response({ csrf_token: 'csrf-token' })
      }
      return response(setupState('providers'))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    expect(await screen.findByRole('heading', { name: 'Create your owner password' })).toBeVisible()
    fireEvent.input(screen.getByLabelText('Owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.input(screen.getByLabelText('Confirm owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create owner access' }))

    expect(await screen.findByRole('heading', { name: 'Choose metadata providers' })).toBeVisible()
  })

  it('recovers a first-owner race by returning to established-owner sign in', async () => {
    let claimReads = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/state') return response({ error: 'unauthorized' }, 401)
      if (url === '/api/setup/claim') {
        claimReads += 1
        return response({
          claimed: claimReads > 1,
          nonce: 'n'.repeat(32),
          revision: claimReads > 1 ? 2 : 1,
        })
      }
      if (url === '/api/setup/session' && init?.method === 'POST')
        return response({ detail: { error: 'claimed' } }, 409)
      throw new Error('unexpected setup request')
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    fireEvent.input(await screen.findByLabelText('Owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.input(screen.getByLabelText('Confirm owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create owner access' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to continue' })).toBeVisible()
    expect(screen.queryByLabelText('Confirm owner password')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeVisible()
  })

  it('removes unchecked provider credentials from the visual and accessibility trees', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(setupState('providers'))),
    )
    runSetup()

    const openLibrary = await screen.findByRole('checkbox', { name: 'Open Library' })
    const contact = document.querySelector<HTMLInputElement>('input[name="open-email"]')
    expect(contact).not.toBeNull()
    const contactField = contact?.closest('.field')
    if (!contactField) throw new Error('provider credential field missing')
    expect(contact).toBeDisabled()
    expect(contactField).toHaveAttribute('hidden')
    expect(getComputedStyle(contactField).display).toBe('none')
    expect(screen.queryByRole('textbox', { name: 'Contact email' })).toBeNull()

    fireEvent.click(openLibrary)

    expect(await screen.findByRole('textbox', { name: 'Contact email' })).toBeEnabled()
    expect(contactField).not.toHaveAttribute('hidden')
    expect(getComputedStyle(contactField).display).toBe('grid')
  })

  it('validates selected supported libraries before mutation and advances after correction', async () => {
    let current = setupState('libraries')
    let libraryMutations = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.startsWith('/api/setup/libraries?'))
        return response({
          libraries: [
            { library_id: 'books', media_kind: 'book', name: 'Books' },
            { library_id: 'podcasts', media_kind: 'unsupported', name: 'Podcasts' },
          ],
        })
      if (url === '/api/setup/libraries' && init?.method === 'POST') {
        libraryMutations += 1
        current = setupState('mounts', {
          completed_steps: ['claim', 'connect', 'libraries'],
          libraries: [
            {
              connection: 'primary',
              library_id: 'books',
              media_kind: 'book',
              mount: 'books',
              name: 'books',
              display_name: 'My Reading Shelf',
              provider_connections: [],
            },
          ],
          revision: 4,
        })
        return response({})
      }
      return response(current)
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const heading = await screen.findByRole('heading', { name: 'Choose libraries' })
    await waitFor(() => expect(heading).toHaveFocus())
    const form = heading.closest('section')?.querySelector('form')
    if (!form) throw new Error('library form missing')

    fireEvent.submit(form)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Select at least one supported book library.',
    )
    expect(libraryMutations).toBe(0)

    const selected = screen.getByRole('checkbox', { name: 'Books' })
    fireEvent.click(selected)
    expect(selected).toBeChecked()
    expect(screen.getByText('1 unsupported podcast library excluded.')).toBeVisible()
    expect(document.querySelector('input[name="name-books"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Change name' }))
    const displayName = screen.getByRole('textbox', { name: 'Library name' })
    expect(displayName).toHaveValue('Books')
    fireEvent.input(displayName, { target: { value: ' My Reading Shelf' } })
    fireEvent.submit(form)
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Use a library name from 1 to 256 characters without leading or trailing spaces.',
      ),
    )
    expect(displayName).toHaveFocus()
    expect(libraryMutations).toBe(0)

    fireEvent.input(displayName, { target: { value: 'My Reading Shelf' } })
    fireEvent.submit(form)
    expect(await screen.findByRole('heading', { name: 'Check read-only mounts' })).toBeVisible()
    expect(libraryMutations).toBe(1)
    const mutation = fetch.mock.calls.find(
      ([request, init]) =>
        requestUrl(request) === '/api/setup/libraries' && init?.method === 'POST',
    )
    expect(jsonBody(mutation?.[1]?.body)).toEqual({
      expected_revision: 3,
      libraries: [
        {
          display_name: 'My Reading Shelf',
          library_id: 'books',
          media_kind: 'book',
          name: 'books',
          provider_connections: [],
        },
      ],
    })
  })

  it.each([
    ['Google Books', 'google-key', true],
    ['Hardcover', 'hardcover-key', false],
  ] as const)(
    'shares optional and required provider-key admission for %s',
    async (label, keyName, shouldAdvance) => {
      let providerMutations = 0
      const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        if (requestUrl(request) === '/api/setup/providers' && init?.method === 'POST') {
          providerMutations += 1
          return response({})
        }
        return response(setupState('providers'))
      })
      vi.stubGlobal('fetch', fetch)
      runSetup()
      const heading = await screen.findByRole('heading', { name: 'Choose metadata providers' })
      await waitFor(() => expect(heading).toHaveFocus())
      fireEvent.click(await screen.findByRole('checkbox', { name: label }))
      const key = document.querySelector<HTMLInputElement>(`input[name="${keyName}"]`)
      const form = key?.closest('form')
      if (!key || !form) throw new Error('provider form missing')

      fireEvent.submit(form)

      if (shouldAdvance) await waitFor(() => expect(providerMutations).toBe(1))
      else {
        expect(await screen.findByRole('alert')).toHaveTextContent(
          settingsVocabulary.errors.provider_key,
        )
        expect(key).toHaveFocus()
        expect(providerMutations).toBe(0)
      }
    },
  )

  it('explains every mount comparison state without exposing raw enums', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        response(
          setupState('mounts', {
            libraries: [
              { library_id: 'books-id', media_kind: 'book', name: 'Books' },
              { library_id: 'history-id', media_kind: 'book', name: 'History' },
              { library_id: 'essays-id', media_kind: 'book', name: 'Essays' },
              { library_id: 'archive-id', media_kind: 'book', name: 'Archive' },
            ],
            mounts: [
              { library: 'books', state: 'matched' },
              { library: 'history', state: 'missing' },
              { library: 'essays', state: 'ambiguous' },
              { library: 'archive', state: 'unavailable' },
            ],
          }),
        ),
      ),
    )
    runSetup()

    const results = await screen.findByRole('status')
    expect(results).toHaveTextContent(
      'Books: A deployment-owned read-only mapping matches this service path.',
    )
    expect(results).toHaveTextContent(
      'History: No read-only mapping matches yet. Correct the Audiobookshelf service path and compare again; Mechanical Librarian never creates or changes mounts.',
    )
    expect(results).toHaveTextContent(
      'Essays: More than one read-only mapping matches. Correct the service path so exactly one deployment-owned read-only mapping matches.',
    )
    expect(results).toHaveTextContent(
      'Archive: No service path was available to compare. Enter the Audiobookshelf service path; Mechanical Librarian never creates or changes mounts.',
    )
    expect(within(results).queryByText(/^books: matched$/u)).toBeNull()
  })

  it.each([
    ['Your work is ready', 1, false, false, 'Open the workspace while preparation continues.'],
    [
      'Preparation needs attention',
      0,
      false,
      true,
      'Open the workspace to review the paused connection and use Check connection again.',
    ],
    [
      'Your work is ready',
      1,
      true,
      false,
      'Preparation is complete. Open the workspace to review your ready work.',
    ],
    [
      'Preparation is complete',
      0,
      true,
      false,
      'Preparation finished without ready work. Open the workspace to continue.',
    ],
    [
      'Preparation is complete',
      0,
      true,
      true,
      'No work became ready. Open the workspace to review the failures and recovery.',
    ],
  ] as const)(
    'offers the setup bridge at %s without navigating',
    async (title, ready, terminal, actionable, expectedCopy) => {
      const previousSetInterval = globalThis.setInterval
      const previousClearInterval = globalThis.clearInterval
      vi.stubGlobal(
        'setInterval',
        vi.fn((callback: () => void) => {
          queueMicrotask(callback)
          return 1
        }),
      )
      vi.stubGlobal('clearInterval', vi.fn())
      const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/kick-off' && init?.method === 'POST')
          return response({ message: 'Restarting into the normal workspace.' })
        if (url === '/api/v1/review-preparation')
          return response({
            actionable,
            first_boot_complete: terminal,
            libraries: [
              {
                checked: 1,
                ready,
                total: 1,
              },
            ],
            paused: actionable,
          })
        return response(
          setupState('kick_off', {
            completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
          }),
        )
      })
      vi.stubGlobal('fetch', fetch)
      runSetup()

      const kickoff = await screen.findByRole('button', { name: 'Kick off preparation' })
      const ledger = document.querySelector('#setup-ledger')
      if (!(ledger instanceof HTMLElement)) throw new Error('setup ledger missing')
      const kickoffRow = within(ledger).getByText('Kick off preparation').closest('li')
      if (!(kickoffRow instanceof HTMLElement)) throw new Error('kickoff ledger row missing')
      expect(kickoffRow).toHaveAttribute('aria-current', 'step')
      expect(within(kickoffRow).getByText('→')).toBeVisible()
      expect(screen.getAllByRole('button', { name: 'Change' })).toHaveLength(4)
      fireEvent.click(kickoff)

      expect(await screen.findByRole('heading', { name: title })).toBeVisible()
      expect(screen.getByText(expectedCopy)).toBeVisible()
      expect(screen.getByRole('link', { name: 'Open workspace' })).toHaveAttribute(
        'href',
        '/library?lens=all',
      )
      expect(screen.queryByRole('button', { name: 'Change' })).toBeNull()
      expect(screen.queryAllByRole('button')).toHaveLength(0)
      const completedRows = ledger.querySelectorAll('.ledger-row')
      expect(completedRows).toHaveLength(7)
      expect(
        [...completedRows].every((row) => row.querySelector('span')?.textContent === '✓'),
      ).toBe(true)
      expect(ledger.querySelector('[aria-current]')).toBeNull()
      vi.stubGlobal('setInterval', previousSetInterval)
      vi.stubGlobal('clearInterval', previousClearInterval)
    },
  )

  it('stops polling and offers normal sign-in when the one-hour setup session expires', async () => {
    const claimedAt = new Date('2026-08-25T10:00:00Z')
    let now = claimedAt.getTime()
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    let poll: (() => Promise<void>) | undefined
    vi.stubGlobal(
      'setInterval',
      vi.fn((callback: () => Promise<void>) => {
        poll = callback
        return 1
      }),
    )
    const clear = vi.fn()
    vi.stubGlobal('clearInterval', clear)
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/kick-off' && init?.method === 'POST')
        return response({ message: 'Restarting into the normal workspace.' })
      if (url === '/api/v1/review-preparation') return response({ error: 'unauthorized' }, 401)
      return response(
        setupState('kick_off', {
          completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()
    fireEvent.click(await screen.findByRole('button', { name: 'Kick off preparation' }))
    await waitFor(() => expect(poll).toBeDefined())

    now = claimedAt.getTime() + 3_601_000
    await poll?.()

    expect(await screen.findByRole('heading', { name: 'Sign in to continue' })).toHaveFocus()
    expect(screen.getByText(/setup sign-in expired/u)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Sign in to workspace' })).toHaveAttribute(
      'href',
      '/library?lens=all',
    )
    expect(clear).toHaveBeenCalled()
  })
})
