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
  schema_version: 'setup-state-v3',
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

const resetSetupDocument = () => {
  document.head.innerHTML = `<style>${css}</style>`
  // The shipped shell carries the prerequisites beside the ledger, and the
  // kickoff step retires them, so the harness has to carry them too.
  document.body.innerHTML = `
    <ol id="setup-ledger"></ol>
    <section class="prerequisites">
      <h2>Before you start</h2>
      <ul><li>A prepared owner-only review-bootstrap.json.</li></ul>
    </section>
    <main id="setup-main"></main>
    <div id="setup-status"></div>
  `
}

beforeEach(() => {
  resetSetupDocument()
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

  it('names the claimed workspace when a late first claim is answered as a sign in', async () => {
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
        return response({ detail: { error: 'invalid_credentials' } }, 401)
      throw new Error('unexpected setup request')
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    fireEvent.input(await screen.findByLabelText('Owner password'), {
      target: { value: 'a different password' },
    })
    fireEvent.input(screen.getByLabelText('Confirm owner password'), {
      target: { value: 'a different password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create owner access' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to continue' })).toBeVisible()
    expect(screen.queryByLabelText('Confirm owner password')).not.toBeInTheDocument()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('claimed in another tab')
    expect(screen.getByLabelText('Owner password')).toHaveAttribute(
      'aria-describedby',
      'setup-error',
    )
    // The whole step changed, so the refusal has to keep the focus it asked
    // for: the fresh card's own frame must not take it back to the heading.
    await waitFor(() => expect(screen.getByLabelText('Owner password')).toHaveFocus())
  })

  it('states the owner-password rules, reveals on demand, and refuses an over-long value', async () => {
    let sessions = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/claim')
        return response({ claimed: false, nonce: 'n'.repeat(32), revision: 1 })
      if (url === '/api/setup/state') return response({ error: 'unauthorized' }, 401)
      if (url === '/api/setup/session' && init?.method === 'POST') {
        sessions += 1
        return response({ csrf_token: 'csrf-token' })
      }
      throw new Error('unexpected setup request')
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const claimHeading = await screen.findByRole('heading', {
      name: 'Create your owner password',
    })
    await waitFor(() => expect(claimHeading).toHaveFocus())
    // The seven-step ledger is drawn on the very first screen, which is
    // reached before any setup session exists.
    expect(document.querySelectorAll('#setup-ledger .ledger-row')).toHaveLength(7)
    expect(screen.getByText(/Between 1 and 1,024 bytes/u)).toBeVisible()
    expect(screen.getByText(/cannot be read back or reset/u)).toBeVisible()
    // ADR-0072's supported default first run is plain HTTP, and jsdom serves
    // this page over `http:`, so the clear-text statement is the live branch.
    expect(screen.getByText(/travel in clear text across this network/u)).toBeVisible()

    const password = screen.getByLabelText('Owner password')
    const confirmation = screen.getByLabelText('Confirm owner password')
    const reveal = screen.getByRole('button', { name: 'Show the owner password' })
    expect(password).toHaveAttribute('type', 'password')
    expect(reveal).toHaveAttribute('aria-pressed', 'false')
    expect(reveal).toHaveAttribute('aria-controls', password.id)
    fireEvent.click(reveal)
    expect(password).toHaveAttribute('type', 'text')
    expect(reveal).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Hide the owner password' }))
    expect(password).toHaveAttribute('type', 'password')

    const form = password.closest('form')
    if (!form) throw new Error('claim form missing')

    fireEvent.submit(form)
    const alert = await screen.findByRole('alert')
    // An empty field, a mismatch and an over-long value each read as
    // themselves instead of repeating the grey rules.
    expect(alert).toHaveTextContent(settingsVocabulary.errors.owner_password_empty)
    expect(password).toHaveAttribute('aria-invalid', 'true')
    expect(password).toHaveAttribute('aria-describedby', alert.id)
    expect(password).toHaveFocus()
    expect(sessions).toBe(0)

    fireEvent.input(password, { target: { value: 'correct horse battery staple' } })
    expect(password).not.toHaveAttribute('aria-invalid')
    expect(password).not.toHaveAttribute('aria-describedby')
    expect(alert.textContent).toBe('')

    fireEvent.input(confirmation, { target: { value: 'wrong horse' } })
    fireEvent.submit(form)
    await waitFor(() => expect(confirmation).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.getByRole('alert')).toHaveTextContent(
      settingsVocabulary.errors.owner_password_mismatch,
    )
    expect(confirmation).toHaveFocus()
    expect(password).not.toHaveAttribute('aria-invalid')
    expect(sessions).toBe(0)

    const oversize = 'a'.repeat(settingsVocabulary.owner_password.maximum_bytes + 1)
    fireEvent.input(password, { target: { value: oversize } })
    fireEvent.input(confirmation, { target: { value: oversize } })
    fireEvent.submit(form)
    await waitFor(() => expect(password).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.getByRole('alert')).toHaveTextContent(settingsVocabulary.errors.owner_password)
    expect(sessions).toBe(0)

    const admitted = 'a'.repeat(settingsVocabulary.owner_password.maximum_bytes)
    fireEvent.input(password, { target: { value: admitted } })
    fireEvent.input(confirmation, { target: { value: admitted } })
    fireEvent.submit(form)
    await waitFor(() => expect(sessions).toBe(1))
  })

  it('releases the previous field when a refusal moves to another one', async () => {
    let sessions = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/claim')
        return response({ claimed: false, nonce: 'n'.repeat(32), revision: 1 })
      if (url === '/api/setup/session' && init?.method === 'POST') {
        sessions += 1
        return response({ csrf_token: 'csrf-token' })
      }
      return response({ error: 'unauthorized' }, 401)
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const password = await screen.findByLabelText('Owner password')
    const confirmation = screen.getByLabelText('Confirm owner password')
    const form = password.closest('form')
    if (!form) throw new Error('claim form missing')

    // A mismatch marks the confirmation.
    fireEvent.input(password, { target: { value: 'first password' } })
    fireEvent.input(confirmation, { target: { value: 'second password' } })
    fireEvent.submit(form)
    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(confirmation).toHaveAttribute('aria-invalid', 'true'))

    // An empty password moves the same refusal to the password field.
    fireEvent.input(password, { target: { value: '' } })
    fireEvent.submit(form)
    await waitFor(() => expect(password).toHaveAttribute('aria-invalid', 'true'))
    expect(confirmation).not.toHaveAttribute('aria-invalid')

    // Typing in the field that is no longer marked must not blank the live
    // alert or leave the marked field describing empty text.
    fireEvent.input(confirmation, { target: { value: 'second password again' } })
    expect(alert).toHaveTextContent(settingsVocabulary.errors.owner_password_empty)
    expect(password).toHaveAttribute('aria-invalid', 'true')
    expect(password).toHaveAttribute('aria-describedby', alert.id)
    expect(confirmation).not.toHaveAttribute('aria-invalid')
    expect(confirmation).not.toHaveAttribute('aria-describedby')

    // The marked field still clears its own refusal.
    fireEvent.input(password, { target: { value: 'second password again' } })
    expect(alert.textContent).toBe('')
    expect(password).not.toHaveAttribute('aria-invalid')
    expect(sessions).toBe(0)
    fireEvent.submit(form)
    await waitFor(() => expect(sessions).toBe(1))
  })

  it.each([
    [422, 'invalid_endpoint', 'Enter the full Audiobookshelf address', 'endpoint'],
    [502, 'unauthorized', 'Issue a key that can read your libraries', 'key'],
    [502, 'tls', 'Mechanical Librarian never skips verification.', 'endpoint'],
    [502, 'timeout', 'Audiobookshelf did not answer in time.', 'endpoint'],
    [502, 'redirect', 'Enter the direct Audiobookshelf address', 'endpoint'],
    [502, 'malformed', 'not with the supported libraries response', 'endpoint'],
    [502, 'oversize', 'more data than setup can safely read', 'endpoint'],
    [400, 'invalid_origin', 'canonical forwarded host and protocol pair', 'endpoint'],
    [403, 'csrf', 'This setup form expired.', null],
    [401, 'unauthorized', 'Your setup sign-in expired', null],
    [500, 'unavailable', 'Setup could not finish that step', null],
  ] as const)(
    'explains the %i %s connection refusal and keeps the entered address',
    async (status, code, copy, invalidField) => {
      const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/connect' && init?.method === 'POST')
          return response({ detail: { error: code } }, status)
        return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
      })
      vi.stubGlobal('fetch', fetch)
      runSetup()

      const connectHeading = await screen.findByRole('heading', {
        name: 'Connect Audiobookshelf',
      })
      await waitFor(() => expect(connectHeading).toHaveFocus())
      const endpoint = screen.getByLabelText('Audiobookshelf address')
      const key = screen.getByLabelText('Audiobookshelf API key')
      fireEvent.input(endpoint, { target: { value: 'https://books.invalid:8443' } })
      fireEvent.input(key, { target: { value: 'target-key' } })
      fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))

      const alert = await screen.findByRole('alert')
      await waitFor(() => expect(alert).toHaveTextContent(copy))
      // A safe cause and a remedy, never the bare code.
      expect(alert.textContent).not.toMatch(/^[a-z_]+$/u)
      expect(endpoint).toHaveValue('https://books.invalid:8443')
      expect(key).toHaveValue('target-key')
      // A refusal about neither field marks neither: a reachable address
      // that answered badly is not an address the owner mistyped.
      if (invalidField === null) {
        expect(endpoint).not.toHaveAttribute('aria-invalid')
        expect(key).not.toHaveAttribute('aria-invalid')
        expect(document.querySelectorAll('#setup-main [aria-invalid]')).toHaveLength(0)
      } else {
        const invalid = invalidField === 'key' ? key : endpoint
        const released = invalidField === 'key' ? endpoint : key
        expect(invalid).toHaveAttribute('aria-invalid', 'true')
        expect(invalid).toHaveAttribute('aria-describedby', alert.id)
        expect(invalid).toHaveFocus()
        expect(released).not.toHaveAttribute('aria-invalid')
      }
    },
  )

  it('states an address the browser cannot parse and trims a stray space', async () => {
    const attempts: unknown[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/connect' && init?.method === 'POST') {
        attempts.push(jsonBody(init.body))
        return response({ detail: { error: 'unauthorized' } }, 502)
      }
      return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const endpoint = await screen.findByLabelText('Audiobookshelf address')
    const form = endpoint.closest('form')
    if (!form) throw new Error('connect form missing')
    // Without this the browser refuses these values itself, in a bubble the
    // page cannot see, and the step says nothing at all.
    expect(form.noValidate).toBe(true)
    fireEvent.input(screen.getByLabelText('Audiobookshelf API key'), {
      target: { value: 'target-key' },
    })
    for (const value of ['', 'not-an-address', '127.0.0.1:18081', 'http://']) {
      fireEvent.input(endpoint, { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))
      const alert = await screen.findByRole('alert')
      await waitFor(() => expect(alert).toHaveTextContent('Enter the full Audiobookshelf address'))
      expect(endpoint).toHaveAttribute('aria-invalid', 'true')
      expect(endpoint).toHaveFocus()
      expect(attempts).toEqual([])
    }

    // A stray space is trimmed instead of travelling to the probe, where it
    // reads as an unreachable address.
    fireEvent.input(endpoint, { target: { value: '  https://books.invalid:8443  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))
    await waitFor(() => expect(attempts).toHaveLength(1))
    expect(attempts[0]).toMatchObject({ endpoint: 'https://books.invalid:8443' })
    expect(endpoint).toHaveValue('https://books.invalid:8443')
  })

  it('states a key setup cannot carry before it travels to Audiobookshelf', async () => {
    const attempts: unknown[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/connect' && init?.method === 'POST') {
        attempts.push(jsonBody(init.body))
        return response({})
      }
      return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const endpoint = await screen.findByLabelText('Audiobookshelf address')
    const key = screen.getByLabelText('Audiobookshelf API key')
    fireEvent.input(endpoint, { target: { value: 'https://books.invalid:8443' } })

    // A key setup would refuse anyway states its own rule here, on the key,
    // instead of reaching Audiobookshelf and reading back as a bad address.
    for (const value of ['', 'a'.repeat(settingsVocabulary.secret.maximum + 1), 'Bearer key']) {
      fireEvent.input(key, { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))
      const alert = await screen.findByRole('alert')
      await waitFor(() => expect(alert).toHaveTextContent(settingsVocabulary.errors.secret))
      expect(key).toHaveAttribute('aria-invalid', 'true')
      expect(key).toHaveAttribute('aria-describedby', alert.id)
      expect(key).toHaveFocus()
      expect(endpoint).not.toHaveAttribute('aria-invalid')
      expect(attempts).toEqual([])
    }

    fireEvent.input(key, { target: { value: 'target-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))
    await waitFor(() => expect(attempts).toHaveLength(1))
    expect(attempts[0]).toMatchObject({ key: 'target-key' })
  })

  it('states a contact email the browser cannot parse instead of staying silent', async () => {
    const attempts: unknown[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/providers' && init?.method === 'POST') {
        attempts.push(jsonBody(init.body))
        return response({})
      }
      return response(setupState('providers', { completed_steps: ['claim', 'connect'] }))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const openLibrary = await screen.findByLabelText('Open Library')
    fireEvent.click(openLibrary)
    const email = screen.getByLabelText('Open Library contact email')
    const form = email.closest('form')
    if (!form) throw new Error('providers form missing')
    expect(form.noValidate).toBe(true)

    // The first value the browser itself rejects, the second it accepts and
    // the contract does not; both have to read the same way here.
    for (const value of ['owner@', 'owner@example']) {
      fireEvent.input(email, { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
      const alert = await screen.findByRole('alert')
      await waitFor(() =>
        expect(alert).toHaveTextContent(settingsVocabulary.errors.open_library_email),
      )
      expect(email).toHaveAttribute('aria-invalid', 'true')
      expect(email).toHaveFocus()
      expect(attempts).toEqual([])
    }

    fireEvent.input(email, { target: { value: 'owner@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(attempts).toHaveLength(1))
  })

  it('holds one verification in flight and re-enables the step when it fails', async () => {
    let attempts = 0
    let release!: (value: Response) => void
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/connect' && init?.method === 'POST') {
        attempts += 1
        return await held
      }
      return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const endpoint = await screen.findByLabelText('Audiobookshelf address')
    fireEvent.input(endpoint, { target: { value: 'http://books.invalid:13378' } })
    fireEvent.input(screen.getByLabelText('Audiobookshelf API key'), {
      target: { value: 'target-key' },
    })
    const verify = screen.getByRole('button', { name: 'Verify and continue' })
    fireEvent.click(verify)
    fireEvent.click(verify)

    await waitFor(() => expect(verify).toBeDisabled())
    expect(attempts).toBe(1)
    expect(document.querySelector('#setup-main')).toHaveAttribute('aria-busy', 'true')
    // The reveal control is not a mutation, so it stays usable while waiting.
    expect(screen.getByRole('button', { name: 'Show the Audiobookshelf API key' })).toBeEnabled()

    release(response({ detail: { error: 'timeout' } }, 502))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Audiobookshelf did not answer in time.',
    )
    expect(verify).toBeEnabled()
    expect(attempts).toBe(1)
    expect(endpoint).toHaveValue('http://books.invalid:13378')
  })

  it('names every provider, what it contributes, and its own credential fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(setupState('providers'))),
    )
    runSetup()
    await screen.findByRole('heading', { name: 'Choose metadata providers' })

    const contributions = settingsVocabulary.provider_contributions as Record<string, string>
    for (const [adapter, label] of Object.entries(settingsVocabulary.provider_labels)) {
      const control = screen.getByRole('checkbox', { name: label })
      const option = control.closest('.option')
      if (!option) throw new Error(`provider option missing for ${label}`)
      expect(option).toHaveTextContent(contributions[adapter] ?? '')
      if (
        !settingsVocabulary.keyless_provider_adapters.includes(adapter) &&
        adapter !== 'open_library'
      ) {
        if (!control.matches(':checked')) fireEvent.click(control)
        expect(screen.getByLabelText(`${label} API key`)).toBeEnabled()
        expect(screen.getByRole('button', { name: `Show the ${label} API key` })).toBeVisible()
      }
    }
    fireEvent.click(screen.getByRole('checkbox', { name: 'Open Library' }))
    expect(screen.getByLabelText('Open Library contact email')).toBeEnabled()
    expect(screen.queryByLabelText('API key')).toBeNull()
    expect(screen.queryByLabelText('Contact email')).toBeNull()

    // The wizard records `cover_enabled: false` for these two and says so.
    for (const label of ['Audible', 'iTunes'])
      expect(screen.getByRole('checkbox', { name: label }).closest('.option')).toHaveTextContent(
        `Cover evidence from ${label} stays off until you turn it on in Settings.`,
      )
    expect(
      screen.getByRole('checkbox', { name: 'Hardcover' }).closest('.option'),
    ).not.toHaveTextContent('Cover evidence from Hardcover')
  })

  it('keeps the entered service paths through a refused comparison and drops them on reopen', async () => {
    const library = {
      connection: 'primary',
      display_name: 'Reading Shelf',
      library_id: 'books-id',
      media_kind: 'book',
      name: 'books',
      provider_connections: [],
    }
    let mounts: { library: string; state: string }[] = []
    let step = 'mounts'
    let revision = 5
    const changes: unknown[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.startsWith('/api/setup/libraries?'))
        return response({
          libraries: [
            {
              library_id: 'books-id',
              media_kind: 'book',
              name: 'Reading Shelf',
              service_path: '/discovered/books',
              service_path_state: 'available',
            },
          ],
        })
      if (url === '/api/setup/mounts' && init?.method === 'POST') {
        const paths = jsonBody(init.body) as { service_paths: Record<string, string | null> }
        revision += 1
        if (paths.service_paths.books === '/owner/typed/books')
          mounts = [{ library: 'books', state: 'missing' }]
        else {
          mounts = [{ library: 'books', state: 'matched' }]
          step = 'kick_off'
        }
        return response({})
      }
      if (url === '/api/setup/change' && init?.method === 'POST') {
        changes.push(jsonBody(init.body))
        mounts = []
        step = 'mounts'
        revision += 1
        return response({})
      }
      if (url === '/api/setup/reviewer-access')
        return response({ mapping: null, revision, codex: { state: 'host_unsupported' } })
      return response(
        setupState(step, {
          completed_steps:
            step === 'kick_off'
              ? ['claim', 'connect', 'providers', 'libraries', 'mounts', 'reviewer']
              : ['claim', 'connect', 'providers', 'libraries'],
          libraries: [library],
          mounts,
          revision,
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const path = await screen.findByLabelText('Reading Shelf service path')
    expect(path).toHaveValue('/discovered/books')
    expect(screen.getByText(/the path inside that service/u)).toBeVisible()
    fireEvent.input(path, { target: { value: '/owner/typed/books' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check mounts' }))

    // UX-008: the refused comparison keeps what the owner typed.
    expect(await screen.findByRole('button', { name: 'Retest mounts' })).toBeVisible()
    expect(screen.getByLabelText('Reading Shelf service path')).toHaveValue('/owner/typed/books')
    const results = screen.getByRole('status')
    expect(results).toHaveTextContent('Reading Shelf: No mapping')
    expect(results).toHaveTextContent('Correct the Audiobookshelf service path')
    expect(results.textContent).not.toContain('missing')

    fireEvent.input(screen.getByLabelText('Reading Shelf service path'), {
      target: { value: '/discovered/books' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Retest mounts' }))
    expect(await screen.findByRole('heading', { name: 'Kick off preparation' })).toBeVisible()

    const ledger = document.querySelector('#setup-ledger')
    if (!ledger) throw new Error('ledger missing')
    const mountRow = [...ledger.querySelectorAll<HTMLElement>('.ledger-row')].find((row) =>
      row.textContent.includes('Check read-only mounts'),
    )
    if (!mountRow) throw new Error('mount ledger row missing')
    fireEvent.click(within(mountRow).getByRole('button', { name: 'Change Check read-only mounts' }))

    // Reopening the step abandons the draft and shows the discovered value.
    await waitFor(() =>
      expect(screen.getByLabelText('Reading Shelf service path')).toHaveValue('/discovered/books'),
    )
    expect(changes).toEqual([{ expected_revision: 7, step: 'mounts' }])
  })

  it('changes nothing when a reopen is refused', async () => {
    const library = {
      connection: 'primary',
      display_name: 'Reading Shelf',
      library_id: 'books-id',
      media_kind: 'book',
      name: 'books',
      provider_connections: [],
    }
    let mounts: { library: string; state: string }[] = []
    let revision = 5
    let changeAttempts = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.startsWith('/api/setup/libraries?'))
        return response({
          libraries: [
            {
              library_id: 'books-id',
              media_kind: 'book',
              name: 'Reading Shelf',
              service_path: '/discovered/books',
              service_path_state: 'available',
            },
          ],
        })
      if (url === '/api/setup/mounts' && init?.method === 'POST') {
        revision += 1
        mounts = [{ library: 'books', state: 'missing' }]
        return response({})
      }
      if (url === '/api/setup/change' && init?.method === 'POST') {
        changeAttempts += 1
        return response({ detail: { error: 'stale' } }, 409)
      }
      return response(
        setupState('mounts', {
          completed_steps: ['claim', 'connect', 'providers', 'libraries'],
          libraries: [library],
          mounts,
          revision,
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const path = await screen.findByLabelText('Reading Shelf service path')
    fireEvent.input(path, { target: { value: '/owner/typed/books' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check mounts' }))
    expect(await screen.findByRole('button', { name: 'Retest mounts' })).toBeVisible()

    const ledger = document.querySelector('#setup-ledger')
    if (!ledger) throw new Error('ledger missing')
    const librariesRow = [...ledger.querySelectorAll<HTMLElement>('.ledger-row')].find((row) =>
      row.textContent.includes('Choose libraries'),
    )
    if (!librariesRow) throw new Error('libraries ledger row missing')
    fireEvent.click(within(librariesRow).getByRole('button', { name: 'Change Choose libraries' }))

    // The refused change recorded nothing, so the step, the comparison and
    // the owner's typed path all stay exactly as they were.
    await waitFor(() => expect(changeAttempts).toBe(1))
    const status = document.querySelector('#setup-status')
    await waitFor(() => expect(status).toHaveTextContent('changed in another tab'))
    expect(screen.getByRole('heading', { name: 'Check read-only mounts' })).toBeVisible()
    expect(screen.getByLabelText('Reading Shelf service path')).toHaveValue('/owner/typed/books')
    expect(screen.getByRole('status')).toHaveTextContent('Reading Shelf: No mapping')
  })

  it('summarizes the configured scope with one reopen seam and clears it at kickoff', async () => {
    const changes: unknown[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer-access')
        return response({
          revision: 7,
          mapping: { engine: 'openrouter', model: 'vendor/model' },
          codex: { state: 'host_unsupported' },
        })
      if (url === '/api/setup/change' && init?.method === 'POST') {
        changes.push(jsonBody(init.body))
        return response({})
      }
      if (url === '/api/setup/kick-off' && init?.method === 'POST')
        return response({ message: 'Restarting into the normal workspace.' })
      if (url === '/api/v1/review-preparation') return response({ presentation: null })
      return response(
        setupState('kick_off', {
          completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
          connection: { endpoint: 'https://books.invalid:8443', name: 'primary' },
          libraries: [
            {
              connection: 'primary',
              display_name: 'Reading Shelf',
              library_id: 'books-id',
              media_kind: 'book',
              name: 'books',
              provider_connections: ['open'],
            },
          ],
          mounts: [{ library: 'books', state: 'matched' }],
          providers: [
            { adapter: 'open_library', enabled: true, name: 'open' },
            { adapter: 'hardcover', enabled: false, name: 'hardcover' },
          ],
          revision: 7,
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const summary = await waitFor(() => {
      const node = document.querySelector('.summary')
      if (!node) throw new Error('kickoff summary missing')
      return node
    })
    expect(summary).toHaveTextContent('Audiobookshelf address')
    expect(summary).toHaveTextContent('https://books.invalid:8443')
    expect(summary).toHaveTextContent('Metadata providers')
    expect(summary).toHaveTextContent('Open Library')
    expect(summary).not.toHaveTextContent('Hardcover')
    expect(summary).toHaveTextContent('Reading Shelf — read-only mount matched')
    expect(summary).toHaveTextContent('Cover reviewer')
    expect(summary).toHaveTextContent('OpenRouter')
    // No credential and no filesystem path reaches the summary.
    expect(summary.textContent).not.toContain('vendor/model')
    expect(summary.textContent).not.toMatch(/(^|\s)\/[A-Za-z]/u)

    // One `reopen` seam: four recorded ledger rows plus four summary rows,
    // and each control names the step it reopens.
    const ledgerChanges = [...document.querySelectorAll('#setup-ledger button')]
    expect(ledgerChanges.map((node) => node.getAttribute('aria-label'))).toEqual([
      'Change Connect Audiobookshelf',
      'Change Choose metadata providers',
      'Change Choose libraries',
      'Change Check read-only mounts',
    ])
    const summaryChanges = [...summary.querySelectorAll('.summary-change')]
    expect(summaryChanges).toHaveLength(4)
    expect(summaryChanges.map((node) => node.getAttribute('aria-label'))).toEqual([
      'Change the Audiobookshelf address',
      'Change the metadata providers',
      'Change the selected libraries',
      'Change the cover reviewer',
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Change the selected libraries' }))
    await waitFor(() => expect(changes).toEqual([{ expected_revision: 7, step: 'libraries' }]))

    fireEvent.click(screen.getByRole('button', { name: 'Kick off preparation' }))
    expect(await screen.findByRole('heading', { name: 'Preparation is starting' })).toBeVisible()
    expect(document.querySelector('.summary')).toBeNull()
  })

  it('posts one kickoff for two clicks and re-enables the control when activation fails', async () => {
    let kickoffs = 0
    let release!: (value: Response) => void
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer-access')
        return response({ revision: 7, mapping: null, codex: { state: 'host_unsupported' } })
      if (url === '/api/setup/kick-off' && init?.method === 'POST') {
        kickoffs += 1
        return await held
      }
      return response(
        setupState('kick_off', {
          completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const submit = await screen.findByRole('button', { name: 'Kick off preparation' })
    fireEvent.click(submit)
    fireEvent.click(submit)
    await waitFor(() => expect(submit).toBeDisabled())
    expect(kickoffs).toBe(1)

    release(response({ detail: { error: 'activation_failed' } }, 503))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Preparation could not be started, so nothing changed.',
    )
    expect(submit).toBeEnabled()
    expect(kickoffs).toBe(1)
    expect(screen.getByRole('heading', { name: 'Kick off preparation' })).toBeVisible()
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
    expect(screen.queryByRole('textbox', { name: 'Open Library contact email' })).toBeNull()

    fireEvent.click(openLibrary)

    expect(await screen.findByRole('textbox', { name: 'Open Library contact email' })).toBeEnabled()
    expect(contactField).not.toHaveAttribute('hidden')
    expect(getComputedStyle(contactField).display).toBe('grid')
  })

  it('offers the keyless audiobook providers with no credential control', async () => {
    let submitted: unknown
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        if (requestUrl(request) === '/api/setup/providers' && init?.method === 'POST') {
          submitted = jsonBody(init.body)
          return response(setupState('libraries'))
        }
        return response(setupState('providers'))
      }),
    )
    runSetup()

    for (const label of ['Audible', 'Audnexus', 'AudioSilo', 'iTunes']) {
      const control = await screen.findByRole('checkbox', { name: label })
      expect(control).toBeVisible()
      // ADR-0061 keyless: no key input and no account link is constructed.
      expect(control.closest('.option')?.querySelector('input[type=password]')).toBeNull()
      expect(control.closest('.option')?.querySelector('a')).toBeNull()
      fireEvent.click(control)
    }
    expect(
      screen.getByRole('checkbox', { name: 'Hardcover' }).closest('.option')?.querySelector('a'),
    ).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await waitFor(() => expect(submitted).toBeDefined())
    expect(submitted).toEqual({
      expected_revision: 3,
      providers: [
        {
          name: 'inventaire',
          adapter: 'inventaire',
          enabled: true,
          cover_enabled: true,
          contact_email: null,
          key: null,
        },
        { name: 'audible', adapter: 'audible_catalog', enabled: true },
        { name: 'audnexus', adapter: 'audnexus', enabled: true },
        { name: 'audiosilo', adapter: 'audiosilo', enabled: true },
        { name: 'itunes', adapter: 'itunes_search', enabled: true },
      ].map((value) =>
        'cover_enabled' in value
          ? value
          : { ...value, cover_enabled: false, contact_email: null, key: null },
      ),
    })
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
    // Each line leads with the library and one short state, so a matched
    // library is told apart from a failing one at a glance; only the failing
    // states carry a remedy.
    const lines = [...results.querySelectorAll<HTMLElement>('p')]
    expect(
      lines.map((line) => [
        line.querySelector('.mount-state')?.textContent,
        line.classList.contains('mount-line-attention'),
      ]),
    ).toEqual([
      ['Matched', false],
      ['No mapping', true],
      ['More than one mapping', true],
      ['No folder reported', true],
    ])
    const matched = lines[0]
    if (!matched) throw new Error('mount comparison lines missing')
    expect(matched.textContent).toBe('Books: Matched')
    expect(matched.querySelector('.mount-remedy')).toBeNull()
    expect(results).toHaveTextContent('History: No mapping')
    expect(results).toHaveTextContent(
      'Correct the Audiobookshelf service path and compare again, or have the deployment declare a read-only mapping for it; Mechanical Librarian never creates or changes mounts.',
    )
    expect(results).toHaveTextContent('Essays: More than one mapping')
    expect(results).toHaveTextContent(
      'Correct the service path so exactly one deployment-owned read-only mapping matches.',
    )
    expect(results).toHaveTextContent('Archive: No folder reported')
    expect(results).toHaveTextContent(
      'Audiobookshelf reported no folder path to compare. Enter the service path yourself and compare again; Mechanical Librarian never creates or changes mounts.',
    )
    expect(within(results).queryByText(/^books: matched$/u)).toBeNull()
  })

  it('renders reviewer recommendations, the free catalogue option, and manual entry', async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL) => {
      if (requestUrl(request) === '/api/setup/reviewer-access')
        return response({
          schema_version: 'reviewer-access-view-v2',
          cover_metadata: null,
          cost_evidence: {
            schema_version: 'reviewer-cost-evidence-v2',
            models: [],
          },
          revision: 3,
          mapping: null,
          openrouter_key_configured: false,
          codex: {
            engine: 'codex',
            state: 'host_unsupported',
            reason: 'insufficient_headroom',
            model: 'gpt-5.6-sol',
            tier: null,
            host_headroom_bytes: 512,
          },
          openrouter: {
            engine: 'openrouter',
            state: 'unconfigured',
            reason: null,
            model: null,
            tier: null,
            host_headroom_bytes: null,
          },
          recommendations: ['openai/gpt-6-luna'],
          candidates: [
            {
              model: 'dots-studio/dots-3-note-preview:free',
              tier: 'free',
              qualified: false,
              evidence: 'catalogue_capability_only',
              requests_per_minute: 20,
              requests_per_day: '50 before $10 lifetime purchases; 1000 after',
            },
          ],
          free_tier_caveat:
            '20 requests/minute; 50 requests/day before $10 in lifetime purchases or 1000 after.',
          data_use_caveat: 'Cover previews are sent to the selected provider.',
        })
      return response(
        setupState('reviewer', {
          completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    expect(await screen.findByText(/Recommended advisory cover reviewer/u)).toBeVisible()
    expect(screen.getByText(/paid; cover observations remain advisory/u)).toBeVisible()
    expect(screen.getByText(/Catalogue-capability-only free option/u)).toBeVisible()
    expect(
      screen.getByText(
        '20 requests/minute; 50 requests/day before $10 in lifetime purchases or 1000 after.',
      ),
    ).toBeVisible()
    expect(screen.getByText('Cover previews are sent to the selected provider.')).toBeVisible()
    const model = screen.getByLabelText('Model ID')
    fireEvent.click(screen.getByRole('button', { name: 'Use GPT-6 Luna (openai/gpt-6-luna)' }))
    expect(model).toHaveValue('openai/gpt-6-luna')
    expect(model).toHaveFocus()
    fireEvent.click(
      screen.getByRole('button', { name: 'Use dots-studio/dots-3-note-preview:free' }),
    )
    expect(model).toHaveValue('dots-studio/dots-3-note-preview:free')
    fireEvent.input(model, { target: { value: 'manual/vision-model' } })
    expect(model).toHaveValue('manual/vision-model')
  })

  it.each([
    [
      'Preparation continues',
      1,
      false,
      false,
      'Open the workspace while preparation continues.',
      false,
    ],
    [
      'Preparation needs attention',
      0,
      false,
      true,
      'Open the workspace to review recovery while preparation remains safely paused.',
      false,
    ],
    [
      'Preparation is complete',
      1,
      true,
      false,
      'Open the workspace to review the completed preparation record.',
      false,
    ],
    [
      'Preparation is complete',
      0,
      true,
      false,
      'Open the workspace to review the completed preparation record.',
      false,
    ],
    [
      'Preparation is complete',
      0,
      true,
      true,
      'Open the workspace to review the completed preparation record.',
      false,
    ],
    ['Preparation was dismissed', 0, true, false, 'Open the ordinary workspace.', true],
  ] as const)(
    'offers the setup bridge at %s without navigating',
    async (title, ready, terminal, actionable, expectedCopy, dismissed = false) => {
      const previousSetTimeout = globalThis.setTimeout
      const previousClearTimeout = globalThis.clearTimeout
      let firstPollScheduled = false
      vi.stubGlobal(
        'setTimeout',
        vi.fn((callback: () => void, delay?: number) => {
          if (delay === 1_500) {
            if (!firstPollScheduled) {
              firstPollScheduled = true
              queueMicrotask(callback)
            }
            return 1
          }
          return previousSetTimeout(callback, delay)
        }),
      )
      vi.stubGlobal('clearTimeout', vi.fn())
      const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/kick-off' && init?.method === 'POST')
          return response({ message: 'Restarting into the normal workspace.' })
        if (url === '/api/v1/review-preparation')
          return response({
            actionable,
            first_boot_complete: terminal,
            presentation: {
              run_id: `preparation_run_${'a'.repeat(64)}`,
              state: dismissed
                ? 'dismissed'
                : terminal
                  ? actionable
                    ? 'completed_with_failures'
                    : 'completed_clean'
                  : actionable
                    ? 'blocked'
                    : 'running',
              passage: {
                read: 1,
                searches_finished: 1,
                compared: 1,
                checked: 1,
                ready,
                failed: terminal && actionable ? 1 : 0,
              },
              total: 1,
            },
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
      expect(document.querySelectorAll('#setup-ledger button')).toHaveLength(4)
      expect(document.querySelector('.prerequisites')).not.toHaveClass('complete')
      fireEvent.click(kickoff)

      expect(await screen.findByRole('heading', { name: title })).toBeVisible()
      expect(screen.getByText(expectedCopy)).toBeVisible()
      expect(screen.getByRole('link', { name: 'Open workspace' })).toHaveAttribute(
        'href',
        dismissed
          ? '/library?lens=all'
          : `/library?lens=all&preparation=preparation_run_${'a'.repeat(64)}`,
      )
      expect(document.querySelectorAll('#setup-ledger button')).toHaveLength(0)
      expect(screen.queryAllByRole('button')).toHaveLength(0)
      // Nothing to prepare for once preparation has started.
      expect(document.querySelector('.prerequisites')).toHaveClass('complete')
      const completedRows = ledger.querySelectorAll('.ledger-row')
      expect(completedRows).toHaveLength(7)
      expect(
        [...completedRows].every((row) => row.querySelector('span')?.textContent === '✓'),
      ).toBe(true)
      expect(ledger.querySelector('[aria-current]')).toBeNull()
      vi.stubGlobal('setTimeout', previousSetTimeout)
      vi.stubGlobal('clearTimeout', previousClearTimeout)
    },
  )

  it('serializes preparation polling, retains reconnect state, and stops on terminal adoption', async () => {
    const previousSetTimeout = globalThis.setTimeout
    const timers: (() => Promise<void>)[] = []
    vi.stubGlobal(
      'setTimeout',
      vi.fn((callback: () => void | Promise<void>, delay?: number) => {
        if (delay === 1_500) {
          timers.push(async () => await callback())
          return timers.length
        }
        return previousSetTimeout(() => void callback(), delay)
      }),
    )
    let releaseFailure!: (value: Response) => void
    const heldFailure = new Promise<Response>((resolve) => {
      releaseFailure = resolve
    })
    let preparationReads = 0
    let inFlight = 0
    let maximumInFlight = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/kick-off' && init?.method === 'POST')
        return response({ message: 'Restarting into the normal workspace.' })
      if (url === '/api/v1/review-preparation') {
        preparationReads += 1
        inFlight += 1
        maximumInFlight = Math.max(maximumInFlight, inFlight)
        const result =
          preparationReads === 1
            ? await heldFailure
            : response({
                presentation: {
                  run_id: `preparation_run_${'a'.repeat(64)}`,
                  state: 'completed_clean',
                  passage: {
                    read: 1,
                    searches_finished: 1,
                    compared: 1,
                    checked: 1,
                    ready: 1,
                    failed: 0,
                  },
                  active_books: [],
                  active_overflow: 0,
                  recent_completions: [],
                  throughput_per_minute: 1,
                  total: 1,
                },
              })
        inFlight -= 1
        return result
      }
      return response(
        setupState('kick_off', {
          completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()
    fireEvent.click(await screen.findByRole('button', { name: 'Kick off preparation' }))
    await waitFor(() => expect(timers).toHaveLength(1))

    const first = timers.shift()
    if (!first) throw new Error('first preparation poll missing')
    const pending = first()
    expect(timers).toHaveLength(0)
    expect(inFlight).toBe(1)
    releaseFailure(response({ error: 'unavailable' }, 503))
    await pending
    expect(screen.getByText(/Reconnecting · showing the last confirmed state/u)).toBeVisible()
    expect(timers).toHaveLength(1)

    const retry = timers.shift()
    if (!retry) throw new Error('preparation retry missing')
    await retry()
    expect(await screen.findByRole('heading', { name: 'Preparation is complete' })).toBeVisible()
    expect(timers).toHaveLength(0)
    expect(maximumInFlight).toBe(1)
  })

  it('stops polling and offers normal sign-in when the one-hour setup session expires', async () => {
    const claimedAt = new Date('2026-08-25T10:00:00Z')
    let now = claimedAt.getTime()
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    const previousSetTimeout = globalThis.setTimeout
    let poll: (() => Promise<void>) | undefined
    vi.stubGlobal(
      'setTimeout',
      vi.fn((callback: () => void | Promise<void>, delay?: number) => {
        if (delay === 1_500) {
          poll = async () => await callback()
          return 1
        }
        return previousSetTimeout(() => void callback(), delay)
      }),
    )
    const clear = vi.fn()
    vi.stubGlobal('clearTimeout', clear)
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

const reviewerAccess = (state: string, reason: string | null = null) => ({
  schema_version: 'reviewer-access-view-v2',
  cover_metadata: null,
  cost_evidence: { schema_version: 'reviewer-cost-evidence-v2', models: [] },
  revision: 3,
  mapping: null,
  codex: { state, reason, model: state === 'not_provisioned' ? null : 'gpt-5.6-sol' },
  recommendations: [],
  candidates: [],
  free_tier_caveat: 'Account limits apply.',
  data_use_caveat: 'Cover previews are sent to the selected provider.',
})

it('keeps alternatives usable when the contained profile is absent', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL) =>
      response(
        requestUrl(request) === '/api/setup/reviewer-access'
          ? reviewerAccess('not_provisioned', 'profile_not_provisioned')
          : setupState('reviewer'),
      ),
    ),
  )
  runSetup()
  expect(await screen.findByText(/The contained profile is missing/u)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Start device sign-in' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Check existing sign-in' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Continue without a reviewer' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Validate and use OpenRouter' })).toBeEnabled()
  expect(screen.getByText(/Optional description advice is configured separately/u)).toBeVisible()
})

it('resumes an issued device flow after reload, cancels it, and offers a restart', async () => {
  let cancelled = false
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/device/cancel')) {
      cancelled = true
      return response({ state: 'cancelled' })
    }
    if (url.endsWith('/device'))
      return response({
        state: 'waiting',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-EFGH',
      })
    if (url.endsWith('/reviewer-access'))
      return response(reviewerAccess(cancelled ? 'cancelled' : 'waiting'))
    expect(init?.method).not.toBe('POST')
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  expect(await screen.findByRole('link', { name: 'Open the device sign-in page' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }))
  expect(await screen.findByRole('button', { name: 'Restart device sign-in' })).toBeEnabled()
  expect(
    fetch.mock.calls.filter(
      ([request, init]) => requestUrl(request).endsWith('/device') && init?.method === 'POST',
    ),
  ).toHaveLength(0)
})

it('prevents duplicate starts and preserves the reviewer step after session expiry', async () => {
  let release: ((value: Response) => void) | undefined
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/device') && init?.method === 'POST')
      return new Promise<Response>((resolve) => {
        release = resolve
      })
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('ready'))
    return response(
      setupState('reviewer', {
        completed_steps: ['claim', 'connect', 'providers', 'libraries', 'mounts'],
      }),
    )
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  const start = await screen.findByRole('button', { name: 'Start device sign-in' })
  fireEvent.click(start)
  fireEvent.click(start)
  expect(start).toBeDisabled()
  expect(
    fetch.mock.calls.filter(
      ([request, init]) => requestUrl(request).endsWith('/device') && init?.method === 'POST',
    ),
  ).toHaveLength(1)
  release?.(response({ error: 'unauthorized' }, 401))
  expect(await screen.findByRole('alert')).toHaveTextContent('Your setup sign-in expired')
  expect(screen.getByRole('heading', { name: 'Choose an optional cover reviewer' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Change Choose libraries' })).toBeVisible()
  expect(start).toBeEnabled()
})

it.each([
  ['reviewer_unconfigured', 'The selected reviewer is no longer ready'],
  ['not_provisioned', 'The contained reviewer profile is unavailable'],
  ['unreachable', 'OpenRouter could not be reached'],
])(
  'gives actionable reviewer recovery for %s without losing prior steps',
  async (code, message) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') return response({ error: code }, 422)
        return response(
          requestUrl(request).endsWith('/reviewer-access')
            ? reviewerAccess('ready')
            : setupState('reviewer'),
        )
      }),
    )
    runSetup()
    fireEvent.click(await screen.findByRole('button', { name: 'Start device sign-in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.getByRole('button', { name: 'Continue without a reviewer' })).toBeEnabled()
  },
)

it.each([
  ['cancelled', 'Any active sign-in was cancelled.'],
  ['ready', 'No active sign-in remains.'],
  ['failed', 'Cancellation could not be confirmed.'],
])('keeps cancellation reachable after blocked reload: %s', async (state, message) => {
  const fetch = vi.fn(async (request: RequestInfo | URL) => {
    const url = requestUrl(request)
    if (url.endsWith('/device/cancel')) return response({ state })
    if (url.endsWith('/reviewer-access'))
      return response(reviewerAccess('not_provisioned', 'profile_not_provisioned'))
    if (url.endsWith('/device')) throw new Error('blocked recovery must not probe authentication')
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel any active sign-in' }))
  expect(await screen.findByText(new RegExp(message.replaceAll('.', '\\.')))).toBeVisible()
  expect(screen.getByRole('button', { name: 'Start device sign-in' })).toBeDisabled()
  expect(screen.getByText('Status: not provisioned')).toBeVisible()
})

it('keeps validated reviewer prices visible before Continue and on reopen', async () => {
  let saved = false
  let advanced = false
  const access = () => ({
    ...reviewerAccess('not_provisioned', 'profile_not_provisioned'),
    mapping: saved ? { engine: 'openrouter', model: 'openai/gpt-6-luna' } : null,
    cover_metadata: saved
      ? {
          model: 'openai/gpt-6-luna',
          observed_at: 1700000000,
          prompt_price: '0.000001',
          completion_price: null,
          image_price: '0',
          request_price: '0',
        }
      : null,
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer-access') return response(access())
      if (url === '/api/setup/reviewer/openrouter') {
        saved = true
        return response(access())
      }
      if (url === '/api/setup/reviewer') advanced = true
      return response(setupState('reviewer'))
    }),
  )
  runSetup()
  fireEvent.input(await screen.findByRole('textbox', { name: 'Model ID' }), {
    target: { value: 'openai/gpt-6-luna' },
  })
  fireEvent.input(screen.getByLabelText('API key (write-only)'), {
    target: { value: 'synthetic-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
  expect(await screen.findByText(/Observed USD rates: 0.000001/u)).toBeVisible()
  expect(screen.getByText('Request cost: not measured for this model.')).toBeVisible()
  expect(screen.getByText('Current cover selection: OpenRouter — GPT-6 Luna')).toBeVisible()
  expect(advanced).toBe(false)
  expect(screen.getByRole('button', { name: 'Continue with this reviewer' })).toBeEnabled()
  resetSetupDocument()
  runSetup()
  expect(await screen.findByText(/Observed USD rates: 0.000001/u)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Continue with this reviewer' }))
  await waitFor(() => expect(advanced).toBe(true))
})
