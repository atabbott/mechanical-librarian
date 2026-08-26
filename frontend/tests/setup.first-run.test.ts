import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { fireEvent, screen, waitFor, within } from '@testing-library/dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import settingsVocabulary from '../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'

const source = readFileSync(
  resolve(process.cwd(), '../src/mechanical_librarian/interfaces/web/setup_frontend.py'),
  'utf8',
)
const rawScript = /SETUP_JS = r"""([\s\S]*?)"""/u.exec(source)?.[1]
const css = /SETUP_CSS = """([\s\S]*?)"""/u.exec(source)?.[1]

if (!rawScript || !css) throw new Error('setup assets are unavailable')
const script = `const settingsVocabulary=${JSON.stringify(settingsVocabulary)};
const settingsTextValid=(value,rule)=>Array.from(value).length>=rule.minimum&&Array.from(value).length<=rule.maximum;
const settingsSecretValid=value=>settingsTextValid(value,settingsVocabulary.secret)&&new RegExp(settingsVocabulary.secret.printable_ascii,'u').test(value)&&!value.toLowerCase().startsWith(settingsVocabulary.secret.forbidden_prefix);
const settingsOpenLibraryEmailValid=value=>settingsTextValid(value,settingsVocabulary.open_library_email)&&new RegExp(settingsVocabulary.open_library_email.pattern,'u').test(value);
const settingsLocalLibraryNameValid=value=>settingsTextValid(value,settingsVocabulary.local_library_name)&&new RegExp(settingsVocabulary.local_library_name.pattern,'u').test(value);
const settingsProviderKeyRequired=adapter=>settingsVocabulary.required_provider_keys.includes(adapter);
${rawScript}`

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
  schema_version: 'setup-state-v1',
  ...overrides,
})

const requestUrl = (request: RequestInfo | URL) => {
  if (typeof request === 'string') return request
  return request instanceof URL ? request.href : request.url
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
            { library_id: 'books', media_kind: 'ebook', name: 'Books' },
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
              media_kind: 'ebook',
              mount: 'books',
              name: 'valid_name',
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
    const localName = document.querySelector<HTMLInputElement>('input[name="name-books"]')
    const unsupportedName = document.querySelector<HTMLInputElement>('input[name="name-podcasts"]')
    const unsupportedKind = document.querySelector<HTMLSelectElement>(
      'select[name="kind-podcasts"]',
    )
    if (!localName) throw new Error('supported local-name control missing')
    expect(unsupportedName).toBeDisabled()
    expect(unsupportedKind).toBeDisabled()
    fireEvent.input(localName, { target: { value: 'Not valid' } })
    fireEvent.submit(form)
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Use a local name of 1–64 lowercase letters, numbers, underscores, or hyphens, starting with a letter.',
      ),
    )
    expect(localName).toHaveFocus()
    expect(localName).toBeInvalid()
    expect(libraryMutations).toBe(0)

    fireEvent.input(localName, { target: { value: 'valid_name' } })
    fireEvent.submit(form)
    expect(await screen.findByRole('heading', { name: 'Check read-only mounts' })).toBeVisible()
    expect(libraryMutations).toBe(1)
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
              { media_kind: 'ebook', name: 'books' },
              { media_kind: 'ebook', name: 'history' },
              { media_kind: 'ebook', name: 'essays' },
              { media_kind: 'ebook', name: 'archive' },
            ],
            mounts: [
              { library: 'books', state: 'matched' },
              { library: 'history', state: 'unmatched' },
              { library: 'essays', state: 'ambiguous' },
              { library: 'archive', state: 'unobservable' },
            ],
          }),
        ),
      ),
    )
    runSetup()

    const results = await screen.findByRole('status')
    expect(results).toHaveTextContent(
      'books: A deployment-owned read-only mapping matches this service path.',
    )
    expect(results).toHaveTextContent(
      'history: No read-only mapping matches yet. Correct the Audiobookshelf service path and compare again; Mechanical Librarian never creates or changes mounts.',
    )
    expect(results).toHaveTextContent(
      'essays: More than one read-only mapping matches. Correct the service path so exactly one deployment-owned read-only mapping matches.',
    )
    expect(results).toHaveTextContent(
      'archive: No service path was available to compare. Enter the Audiobookshelf service path; Mechanical Librarian never creates or changes mounts.',
    )
    expect(within(results).queryByText(/^books: matched$/u)).toBeNull()
  })

  it.each([
    [
      'Your work is ready',
      1,
      false,
      false,
      'Open the workspace while preparation continues.',
    ],
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
      expect(completedRows).toHaveLength(6)
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
