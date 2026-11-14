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
const shipped = (name: string) =>
  execFileSync(
    resolve(process.cwd(), '../.venv/bin/python'),
    [
      '-c',
      `import sys;sys.path.insert(0,'src');from mechanical_librarian.interfaces.web.${name};print(value,end='')`,
    ],
    { cwd: resolve(process.cwd(), '..'), encoding: 'utf8' },
  )
const script = shipped('setup_frontend import SETUP_JS as value')
// The blocking head script that applies a saved theme before first paint.
const themeScript = shipped('review_frontend import THEME_JS as value')

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
  schema_version: 'setup-state-v5',
  ...overrides,
})

// The sources step ticks the sources that suit the libraries once its read
// returns; a test about one chosen source starts from none ticked.
const untickSources = async () => {
  await waitFor(() => expect(screen.queryByText('Loading your libraries…')).not.toBeInTheDocument())
  for (const box of screen.getAllByRole('checkbox'))
    if (box instanceof HTMLInputElement && box.checked) fireEvent.click(box)
}

const requestUrl = (request: RequestInfo | URL) => {
  if (typeof request === 'string') return request
  return request instanceof URL ? request.href : request.url
}

const jsonBody = (body: BodyInit | null | undefined): unknown => {
  if (typeof body !== 'string') throw new Error('JSON request body missing')
  return JSON.parse(body) as unknown
}

// jsdom cannot navigate, and its `location.replace` cannot be spied on, so a
// test that follows Start runs the shipped script against a stub location.
interface LocationStub {
  protocol: string
  href: string
  origin: string
  replace: (url: string) => void
}
const runSetup = (place?: LocationStub) => {
  if (!place) {
    window.eval(script)
    return
  }
  // The same as `new Function('location', script)(place)`: the shipped script
  // reads `location` from this parameter instead of the window's.
  const run = window.eval(`(function (location) {\n${script}\n})`) as (
    location: LocationStub,
  ) => void
  run(place)
}
const stubLocation = (protocol = 'http:') => {
  const replace = vi.fn<(url: string) => void>()
  return {
    protocol,
    href: `${protocol}//setup.invalid/`,
    origin: `${protocol}//setup.invalid`,
    replace,
  }
}

const resetSetupDocument = () => {
  document.head.innerHTML = `<style>${css}</style>`
  document.body.innerHTML = `
    <ol id="setup-ledger"></ol>
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
  // With nothing saved the root stays unmarked, so the stylesheet's system
  // fallback follows the system live; a saved choice marks it (#1013).
  it.each([
    ['the system preference when no theme is saved', '', true, undefined],
    ['a saved dark choice over a light system', 'ml_theme=dark', false, 'dark'],
    ['a saved light choice over a dark system', 'ml_theme=light', true, 'light'],
    ['the system when it prefers light and nothing is saved', '', false, undefined],
  ] as const)('follows %s', (_name, cookie, dark, theme) => {
    if (cookie) document.cookie = `${cookie}; Path=/`
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({
        matches: dark && query === '(prefers-color-scheme: dark)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    )
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response({ claimed: true, nonce: 'n'.repeat(32), revision: 1 })),
    )
    try {
      window.eval(themeScript)
      runSetup()
      expect(document.documentElement.dataset.theme).toBe(theme)
    } finally {
      document.cookie = 'ml_theme=; Path=/; Max-Age=0'
      delete document.documentElement.dataset.theme
    }
  })

  it('renders an unmarked root dark under a dark system with every saved-dark token', () => {
    const tokens = (block: string | undefined) =>
      (block ?? '').split(';').filter((declaration) => declaration.startsWith('--'))
    const saved = /:root\[data-theme=dark\]\{([^}]*)\}/u.exec(css)?.[1]
    const system =
      /@media\(prefers-color-scheme:dark\)\{:root:not\(\[data-theme\]\)\{([^}]*)\}/u.exec(css)?.[1]
    expect(tokens(saved).length).toBeGreaterThan(0)
    expect(tokens(system)).toEqual(tokens(saved))
    // The unmarked root already declares both schemes, so the system picks dark.
    expect(css).toMatch(/^:root\{color-scheme:light dark;/u)
  })

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
          claim_token: 'deployment-held-claim-token',
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

    expect(await screen.findByRole('heading', { name: 'Create your password' })).toBeVisible()
    // The token is copied from a log line or a file, so stray whitespace from
    // the copy never reaches the claim.
    fireEvent.input(screen.getByLabelText('Setup claim token'), {
      target: { value: '  deployment-held-claim-token\n' },
    })
    fireEvent.input(screen.getByLabelText('Owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.input(screen.getByLabelText('Confirm owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your password' }))

    expect(await screen.findByRole('heading', { name: 'Choose metadata sources' })).toBeVisible()
  })

  it('names where the claim token is and recovers a refused token in place', async () => {
    let claimReads = 0
    const submitted: { nonce: unknown; token: unknown }[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/state' && submitted.length < 2)
        return response({ error: 'unauthorized' }, 401)
      if (url === '/api/setup/claim') {
        claimReads += 1
        return response({ claimed: false, nonce: String(claimReads).repeat(32), revision: 1 })
      }
      if (url === '/api/setup/session' && init?.method === 'POST') {
        const body = jsonBody(init.body) as { claim_token: unknown }
        submitted.push({
          nonce: (init.headers as Record<string, string>)['x-setup-nonce'],
          token: body.claim_token,
        })
        if (submitted.length === 1) return response({ detail: { error: 'claim_token' } }, 403)
        return response({ csrf_token: 'csrf-token' })
      }
      return response(setupState('providers'))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const token = await screen.findByLabelText('Setup claim token')
    expect(token).toHaveAttribute('type', 'password')
    expect(token).toHaveAttribute('maxlength', '128')
    expect(screen.getByRole('button', { name: 'Show the setup claim token' })).toBeVisible()
    // The hint names the log and the command that prints it, and is said
    // with the field.
    const where = screen.getByText(
      /It’s in the container log\. Restarting the container replaces it\./u,
    )
    expect(where).toBeVisible()
    expect(within(where).getByText('docker compose logs mechanical-librarian').tagName).toBe('CODE')
    expect(token).toHaveAttribute('aria-describedby', where.id)
    expect(screen.queryByText(/private\/setup-claim-token/u)).not.toBeInTheDocument()
    expect(screen.queryByText(/first visitor/u)).not.toBeInTheDocument()
    const password = screen.getByLabelText('Owner password')
    const confirmation = screen.getByLabelText('Confirm owner password')
    fireEvent.input(token, { target: { value: 'mistyped-token' } })
    fireEvent.input(password, { target: { value: 'correct horse battery staple' } })
    fireEvent.input(confirmation, { target: { value: 'correct horse battery staple' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create your password' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'That setup claim token was not accepted. Copy the newest one from the container log.',
    )
    await waitFor(() => expect(token).toHaveAttribute('aria-invalid', 'true'))
    expect(token).toHaveAttribute('aria-describedby', `${alert.id} ${where.id}`)
    await waitFor(() => expect(token).toHaveFocus())
    // The refused request spent its nonce; a fresh one is fetched in place and
    // every typed field stays where it was.
    expect(claimReads).toBe(2)
    expect(screen.getByLabelText('Setup claim token')).toBe(token)
    expect(token).toHaveValue('mistyped-token')
    expect(password).toHaveValue('correct horse battery staple')
    expect(confirmation).toHaveValue('correct horse battery staple')

    fireEvent.input(token, { target: { value: 'deployment-held-claim-token' } })
    expect(token).not.toHaveAttribute('aria-invalid')
    fireEvent.click(screen.getByRole('button', { name: 'Create your password' }))

    expect(await screen.findByRole('heading', { name: 'Choose metadata sources' })).toBeVisible()
    expect(submitted).toEqual([
      { nonce: '1'.repeat(32), token: 'mistyped-token' },
      { nonce: '2'.repeat(32), token: 'deployment-held-claim-token' },
    ])
  })

  it('returns to sign in when a refused claim token finds the workspace claimed', async () => {
    let claimReads = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/state') return response({ error: 'unauthorized' }, 401)
      if (url === '/api/setup/claim') {
        claimReads += 1
        return response({ claimed: claimReads > 1, nonce: 'n'.repeat(32), revision: claimReads })
      }
      if (url === '/api/setup/session' && init?.method === 'POST')
        return response({ detail: { error: 'claim_token' } }, 403)
      throw new Error('unexpected setup request')
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    fireEvent.input(await screen.findByLabelText('Setup claim token'), {
      target: { value: 'stale-token' },
    })
    fireEvent.input(screen.getByLabelText('Owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.input(screen.getByLabelText('Confirm owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your password' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to continue' })).toBeVisible()
    expect(screen.queryByLabelText('Setup claim token')).not.toBeInTheDocument()
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
    fireEvent.click(screen.getByRole('button', { name: 'Create your password' }))

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
    fireEvent.click(screen.getByRole('button', { name: 'Create your password' }))

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

  it('states the owner-password rules, reveals on demand, and refuses a short or over-long value', async () => {
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
      name: 'Create your password',
    })
    await waitFor(() => expect(claimHeading).toHaveFocus())
    // The ledger is drawn on the very first screen, which is reached before
    // any setup session exists; the folder step shows only when it is asked.
    expect(document.querySelectorAll('#setup-ledger .ledger-row')).toHaveLength(6)
    // #1384: the rules follow the password field they govern and describe it,
    // rather than reading as rules for the claim token above them.
    const stated = screen.getByText('At least 12 characters.')
    expect(stated).toBeVisible()
    const rulesList = stated.closest('ul')
    if (!rulesList) throw new Error('password rules missing')
    expect(rulesList.id).toBe('setup-password-rules')
    expect(screen.queryByText('Both fields must match exactly.')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Confirm owner password')).not.toHaveAttribute('aria-describedby')
    expect(rulesList.previousElementSibling).toContainElement(
      screen.getByLabelText('Owner password'),
    )
    expect(screen.getByText('There’s no password reset, so keep it somewhere safe.')).toBeVisible()
    // ADR-0072's supported default first run is plain HTTP, and jsdom serves
    // this page over `http:`, so the one-line notice is the live branch.
    expect(
      screen.getByText('This page uses plain HTTP. Use it only on a network you trust.'),
    ).toBeVisible()

    const password = screen.getByLabelText('Owner password')
    const confirmation = screen.getByLabelText('Confirm owner password')
    const reveal = screen.getByRole('button', { name: 'Show the owner password' })
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveAttribute('aria-describedby', 'setup-password-rules')
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
    expect(password).toHaveAttribute('aria-describedby', `${alert.id} setup-password-rules`)
    expect(password).toHaveFocus()
    // The refusal sits directly under the field it refuses.
    expect(alert.previousElementSibling).toContainElement(password)
    expect(sessions).toBe(0)

    fireEvent.input(password, { target: { value: 'correct horse battery staple' } })
    expect(password).not.toHaveAttribute('aria-invalid')
    expect(password).toHaveAttribute('aria-describedby', 'setup-password-rules')
    expect(alert.textContent).toBe('')

    // Characters are counted, not bytes: eleven two-byte characters are still
    // too short.
    for (const short of ['a'.repeat(11), 'é'.repeat(11), '😀'.repeat(11)]) {
      fireEvent.input(password, { target: { value: short } })
      fireEvent.input(confirmation, { target: { value: short } })
      fireEvent.submit(form)
      await waitFor(() => expect(password).toHaveAttribute('aria-invalid', 'true'))
      // The rule it breaks becomes the refusal, said once (#1516).
      expect(screen.queryByRole('alert')).toBeNull()
      expect(stated).toHaveClass('rule-refused')
      expect(password).toHaveAttribute('aria-describedby', 'setup-password-rules')
      await waitFor(() =>
        expect(document.querySelector('#setup-status')).toHaveTextContent(
          'At least 12 characters.',
        ),
      )
      expect(within(form).getAllByText(/12 characters/u)).toHaveLength(1)
      expect(password).toHaveFocus()
      expect(sessions).toBe(0)
      // Let the refusal's focus settle before the next refusal, as a person
      // typing into another field would by focusing it first.
      await new Promise(requestAnimationFrame)
    }
    fireEvent.input(password, { target: { value: 'é'.repeat(12) } })

    fireEvent.input(confirmation, { target: { value: 'wrong horse' } })
    fireEvent.submit(form)
    await waitFor(() => expect(confirmation).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.getByRole('alert')).toHaveTextContent(
      settingsVocabulary.errors.owner_password_mismatch,
    )
    expect(confirmation).toHaveAttribute('aria-describedby', 'setup-error')
    expect(confirmation).toHaveFocus()
    expect(password).not.toHaveAttribute('aria-invalid')
    expect(sessions).toBe(0)

    const oversize = 'a'.repeat(settingsVocabulary.owner_password.maximum_bytes + 1)
    fireEvent.input(password, { target: { value: oversize } })
    fireEvent.input(confirmation, { target: { value: oversize } })
    fireEvent.submit(form)
    await waitFor(() => expect(password).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.getByRole('alert')).toHaveTextContent(
      settingsVocabulary.errors.owner_password_long,
    )
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
    expect(password).toHaveAttribute('aria-describedby', `${alert.id} setup-password-rules`)
    expect(alert.previousElementSibling).toContainElement(password)
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

  it('refuses a password the server rejects on that field, with a fresh nonce', async () => {
    const nonces: (string | null)[] = []
    let claimReads = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/claim') {
        claimReads += 1
        return response({ claimed: false, nonce: String(claimReads).repeat(32), revision: 1 })
      }
      if (url === '/api/setup/session' && init?.method === 'POST') {
        nonces.push(new Headers(init.headers).get('x-setup-nonce'))
        if (nonces.length === 1) return response({ detail: { error: 'invalid_password' } }, 422)
        return response({ csrf_token: 'csrf-token' })
      }
      return response({ error: 'unauthorized' }, 401)
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const password = await screen.findByLabelText('Owner password')
    const confirmation = screen.getByLabelText('Confirm owner password')
    const claimToken = screen.getByLabelText('Setup claim token')
    const form = password.closest('form')
    if (!form) throw new Error('claim form missing')
    fireEvent.input(claimToken, { target: { value: 'synthetic-claim-token' } })
    fireEvent.input(password, { target: { value: 'correct horse battery staple' } })
    fireEvent.input(confirmation, { target: { value: 'correct horse battery staple' } })
    fireEvent.submit(form)

    // The refused claim spent its nonce, so a fresh one is read before the
    // refusal is shown on the password's own rule, not on the claim token.
    await waitFor(() => expect(password).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('At least 12 characters.')).toHaveClass('rule-refused')
    expect(password).toHaveAttribute('aria-describedby', 'setup-password-rules')
    expect(claimToken).not.toHaveAttribute('aria-invalid')
    expect(password).toHaveFocus()
    expect(claimReads).toBe(2)

    fireEvent.submit(form)
    await waitFor(() => expect(nonces).toHaveLength(2))
    expect(nonces).toEqual(['1'.repeat(32), '2'.repeat(32)])
  })

  it.each([
    [422, 'invalid_endpoint', 'Enter only the address and port', 'endpoint'],
    [502, 'unauthorized', 'Create a key that can read your libraries', 'key'],
    [
      502,
      'name_resolution',
      'could not find https://books.invalid:8443. Check the address',
      'endpoint',
    ],
    [502, 'refused', 'refused the connection', 'endpoint'],
    [502, 'tls', 'Use an address with a trusted certificate', 'endpoint'],
    [502, 'timeout', 'did not answer in time.', 'endpoint'],
    [502, 'unavailable', 'could not be read. Check that it is running', 'endpoint'],
    [502, 'redirect', 'Enter Audiobookshelf’s own address.', 'endpoint'],
    [502, 'malformed', 'did not answer like Audiobookshelf', 'endpoint'],
    [502, 'oversize', 'more data than setup can read', 'endpoint'],
    [400, 'invalid_origin', 'one X-Forwarded-Host and one X-Forwarded-Proto', 'endpoint'],
    // Another tab rotated the form token: the step reloads and says so.
    [403, 'csrf', 'Setup changed in another tab.', null],
    [401, 'unauthorized', 'Your sign-in expired', null],
    [500, 'unavailable', 'That step was not saved.', null],
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
      const verify = screen.getByRole('button', { name: 'Verify and continue' })
      verify.focus()
      fireEvent.click(verify)

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
        if (code === 'csrf') {
          // A rotated token reloads the step: the alert opens the fresh form.
          expect(alert.parentElement?.firstElementChild).toBe(alert)
        } else {
          // A refusal about neither field sits directly under the pressed
          // button, which is usable again and keeps focus (#1664).
          expect(alert.previousElementSibling).toBe(verify)
          expect(alert.nextElementSibling).toBeNull()
          expect(verify).toBeEnabled()
          expect(verify).toHaveFocus()
        }
      } else {
        const invalid = invalidField === 'key' ? key : endpoint
        const released = invalidField === 'key' ? endpoint : key
        // A field refusal stays after its field, above the button.
        expect(alert.previousElementSibling).toBe(invalid.closest('.field'))
        expect(invalid).toHaveAttribute('aria-invalid', 'true')
        // The refusal leads, and the field's own hint is still said with it.
        expect(invalid).toHaveAttribute(
          'aria-describedby',
          `${alert.id} ${invalid.dataset.hint ?? ''}`,
        )
        expect(invalid).toHaveFocus()
        expect(released).not.toHaveAttribute('aria-invalid')
      }
    },
  )

  it.each(['name_resolution', 'refused', 'tls', 'timeout', 'unauthorized', 'unavailable'])(
    'names only the origin the owner entered for a %s failure, never the key or user',
    async (code) => {
      const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        if (requestUrl(request) === '/api/setup/connect' && init?.method === 'POST')
          return response({ detail: { error: code } }, 502)
        return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
      })
      vi.stubGlobal('fetch', fetch)
      runSetup()

      const endpoint = await screen.findByLabelText('Audiobookshelf address')
      fireEvent.input(endpoint, {
        target: { value: 'https://books.invalid:8443/' },
      })
      fireEvent.input(screen.getByLabelText('Audiobookshelf API key'), {
        target: { value: 'target-key-secret' },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))

      const alert = await screen.findByRole('alert')
      await waitFor(() => expect(alert).toHaveTextContent('https://books.invalid:8443'))
      expect(alert.textContent).not.toContain('target-key-secret')
      expect(alert.textContent).not.toContain('https://books.invalid:8443/')
      expect(
        code === 'unauthorized' ? screen.getByLabelText('Audiobookshelf API key') : endpoint,
      ).toHaveFocus()
    },
  )

  it.each([
    'http://127.0.0.1:13378/audiobookshelf',
    'https://owner:hunter2@books.invalid:8443',
    'https://books.invalid:8443/?token=query-secret',
    'https://books.invalid:8443/#fragment',
  ])('refuses %s as more than scheme, host and port before sending it', async (address) => {
    const attempts: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        if (requestUrl(request) === '/api/setup/connect' && init?.method === 'POST')
          attempts.push(jsonBody(init.body))
        return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
      }),
    )
    runSetup()
    const endpoint = await screen.findByLabelText('Audiobookshelf address')
    fireEvent.input(endpoint, { target: { value: address } })
    fireEvent.input(screen.getByLabelText('Audiobookshelf API key'), {
      target: { value: 'target-key-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'Enter only the address and port, such as http://192.168.1.10:13378, with no path, user name or password.',
    )
    for (const secret of ['owner', 'hunter2', 'query-secret', 'target-key-secret'])
      expect(alert.textContent).not.toContain(secret)
    expect(endpoint).toHaveFocus()
    expect(endpoint).toHaveAttribute('aria-invalid', 'true')
    expect(attempts).toEqual([])
  })

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
      await waitFor(() => expect(alert).toHaveTextContent('Enter only the address and port'))
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
      expect(key).toHaveAttribute('aria-describedby', `${alert.id} ${key.dataset.hint ?? ''}`)
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
    await untickSources()
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
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Connect Audiobookshelf' })).toHaveFocus(),
    )
    verify.focus()
    fireEvent.click(verify)
    fireEvent.click(verify)

    // jsdom has no focus fixup, so focus alone cannot fail here; a browser drops
    // focus to the page when the focused button becomes natively disabled (#1678).
    await waitFor(() => expect(verify).toHaveAttribute('aria-disabled', 'true'))
    expect(verify).not.toBeDisabled()
    expect(verify).toHaveFocus()
    expect(attempts).toBe(1)
    expect(document.querySelector('#setup-main')).toHaveAttribute('aria-busy', 'true')
    // The reveal control is not a mutation, so it stays usable while waiting.
    expect(screen.getByRole('button', { name: 'Show the Audiobookshelf API key' })).toBeEnabled()

    release(response({ detail: { error: 'timeout' } }, 502))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'http://books.invalid:13378 did not answer in time.',
    )
    expect(verify).toBeEnabled()
    expect(verify).not.toHaveAttribute('aria-disabled')
    // A reach failure is about the address, so its refusal focuses that field.
    expect(endpoint).toHaveFocus()
    expect(attempts).toBe(1)
    expect(endpoint).toHaveValue('http://books.invalid:13378')
  })

  it.each([
    ['connect', 'Connect Audiobookshelf', '/api/setup/connect', 'Verify and continue'],
    ['providers', 'Choose metadata sources', '/api/setup/providers', 'Continue'],
    ['libraries', 'Choose libraries', '/api/setup/libraries', 'Continue'],
    ['mounts', 'Find library folders', '/api/setup/mounts', 'Check again'],
  ] as const)(
    'runs no %s client check for a repeat submit while the step is recorded (#1678)',
    async (step, heading, path, name) => {
      let attempts = 0
      let release!: (value: Response) => void
      const held = new Promise<Response>((resolve) => {
        release = resolve
      })
      const shelf = {
        library_id: 'books-id',
        media_kind: 'book',
        name: 'Reading Shelf',
        service_path: '/discovered/books',
        service_path_state: 'available',
      }
      vi.stubGlobal(
        'fetch',
        vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
          const url = requestUrl(request)
          if (url === path && init?.method === 'POST') {
            attempts += 1
            return await held
          }
          if (url.startsWith('/api/setup/libraries?')) return response({ libraries: [shelf] })
          if (step === 'connect')
            return response(setupState('connect', { completed_steps: ['claim'], connection: null }))
          if (step === 'mounts')
            return response(
              setupState('mounts', {
                completed_steps: ['claim', 'connect', 'libraries'],
                libraries: [
                  {
                    connection: 'primary',
                    display_name: 'Reading Shelf',
                    library_id: 'books-id',
                    media_kind: 'book',
                    name: 'books',
                    provider_connections: [],
                  },
                ],
                revision: 5,
              }),
            )
          return response(setupState(step))
        }),
      )
      runSetup()

      const title = await screen.findByRole('heading', { name: heading })
      await waitFor(() => expect(title).toHaveFocus())
      // Each row fills the step so the first press passes its client checks, then
      // edits a field so that a second submit would fail them.
      let edited: HTMLElement
      let edit: () => void
      if (step === 'connect') {
        const endpoint = screen.getByLabelText('Audiobookshelf address')
        fireEvent.input(endpoint, { target: { value: 'http://books.invalid:13378' } })
        fireEvent.input(screen.getByLabelText('Audiobookshelf API key'), {
          target: { value: 'target-key' },
        })
        edited = endpoint
        edit = () => fireEvent.input(endpoint, { target: { value: 'not-a-url' } })
      } else if (step === 'providers') {
        await untickSources()
        fireEvent.click(screen.getByLabelText('Open Library'))
        const email = screen.getByLabelText('Open Library contact email')
        fireEvent.input(email, { target: { value: 'owner@example.org' } })
        edited = email
        edit = () => fireEvent.input(email, { target: { value: 'not an email' } })
      } else if (step === 'libraries') {
        // Every book library starts ticked.
        const books = await screen.findByRole('checkbox', { name: 'Reading Shelf' })
        expect(books).toBeChecked()
        edited = books
        edit = () => fireEvent.click(books)
      } else {
        const typed = screen.getByLabelText('Reading Shelf folder in Audiobookshelf')
        fireEvent.input(typed, { target: { value: '/owner/typed/books' } })
        edited = typed
        edit = () => fireEvent.input(typed, { target: { value: '' } })
      }
      const pressed = screen.getByRole('button', { name })
      pressed.focus()
      fireEvent.click(pressed)
      await waitFor(() => expect(attempts).toBe(1))

      // Enter in a field submits the form again; the step is still being recorded.
      edit()
      const form = edited.closest('form')
      if (!form) throw new Error(`${step} form missing`)
      fireEvent.submit(form)
      expect(attempts).toBe(1)
      expect(screen.queryByRole('alert')).toBeNull()
      expect(edited).not.toHaveAttribute('aria-invalid')
      expect(pressed).toHaveFocus()

      if (step === 'mounts') {
        // The mount check keeps the draft of the press that is running, so a
        // restored step shows the path that was sent, not the cleared one.
        release(response({ detail: { error: 'stale' } }, 409))
        expect(await screen.findByRole('alert')).toHaveTextContent('Setup changed in another tab.')
        expect(screen.getByLabelText('Reading Shelf folder in Audiobookshelf')).toHaveValue(
          '/owner/typed/books',
        )
      } else {
        release(response({ detail: { error: 'unavailable' } }, 500))
        const alert = await screen.findByRole('alert')
        await waitFor(() => expect(alert).toHaveTextContent('That step was not saved.'))
        if (step === 'connect') expect(alert.previousElementSibling).toBe(pressed)
      }
      expect(attempts).toBe(1)
    },
  )

  it('names every provider, what it contributes, and its own credential fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(setupState('providers'))),
    )
    runSetup()
    await screen.findByRole('heading', { name: 'Choose metadata sources' })

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
    const openLibrary = screen.getByRole('checkbox', { name: 'Open Library' })
    if (!openLibrary.matches(':checked')) fireEvent.click(openLibrary)
    expect(screen.getByLabelText('Open Library contact email')).toBeEnabled()
    expect(openLibrary.closest('.option')).toHaveTextContent(
      'Needs your email address. No account needed.',
    )
    expect(screen.queryByLabelText('API key')).toBeNull()
    expect(screen.queryByLabelText('Contact email')).toBeNull()

    // Each card is one line: no cover line, and iTunes claims no covers.
    expect(screen.queryByText(/Cover evidence from/u)).toBeNull()
    expect(screen.getByRole('checkbox', { name: 'iTunes' }).closest('.option')).toHaveTextContent(
      "Checks an ebook's title and author against iTunes audiobook listings.",
    )
    expect(screen.queryByText(/adds audiobook covers/u)).toBeNull()
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
    let mounts: { library: string; state: string }[] = [{ library: 'books', state: 'missing' }]
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
        revision += 1
        mounts = [{ library: 'books', state: 'missing' }]
        return response({})
      }
      if (url === '/api/setup/libraries' && init?.method === 'POST') {
        revision += 1
        mounts = [{ library: 'books', state: 'missing' }]
        step = 'mounts'
        return response({})
      }
      if (url === '/api/setup/change' && init?.method === 'POST') {
        changes.push(jsonBody(init.body))
        mounts = []
        step = 'libraries'
        revision += 1
        return response({})
      }
      return response(
        setupState(step, {
          completed_steps:
            step === 'mounts' ? ['claim', 'connect', 'libraries'] : ['claim', 'connect'],
          libraries: step === 'mounts' ? [library] : [],
          mounts,
          revision,
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const path = await screen.findByLabelText('Reading Shelf folder in Audiobookshelf')
    expect(path).toHaveValue('/discovered/books')
    expect(screen.queryByText(/the path inside that service/u)).toBeNull()
    fireEvent.input(path, { target: { value: '/owner/typed/books' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))

    // UX-008: the refused comparison keeps what the owner typed.
    await waitFor(() => expect(revision).toBe(6))
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeVisible()
    expect(screen.getByLabelText('Reading Shelf folder in Audiobookshelf')).toHaveValue(
      '/owner/typed/books',
    )
    const result = document.querySelector('.mount-line')
    if (!result) throw new Error('folder result missing')
    expect(result).toHaveTextContent('Reading Shelf: Not found')
    expect(result.textContent).not.toContain('missing')

    // Reopening an earlier step abandons the draft: the folder step shows the
    // reported value again when it is asked next.
    const ledger = document.querySelector('#setup-ledger')
    if (!ledger) throw new Error('ledger missing')
    const librariesRow = [...ledger.querySelectorAll<HTMLElement>('.ledger-row')].find((row) =>
      row.textContent.includes('Choose libraries'),
    )
    if (!librariesRow) throw new Error('libraries ledger row missing')
    fireEvent.click(within(librariesRow).getByRole('button', { name: 'Change Choose libraries' }))
    expect(await screen.findByRole('heading', { name: 'Choose libraries' })).toBeVisible()
    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Reading Shelf folder in Audiobookshelf')).toHaveValue(
        '/discovered/books',
      ),
    )
    expect(changes).toEqual([{ expected_revision: 6, step: 'libraries' }])
  })

  it('places a refused mount comparison under Check again and keeps the typed path (#1664)', async () => {
    const library = {
      connection: 'primary',
      display_name: 'Reading Shelf',
      library_id: 'books-id',
      media_kind: 'book',
      name: 'books',
      provider_connections: [],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
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
        if (url === '/api/setup/mounts' && init?.method === 'POST')
          return response({ detail: { error: 'invalid_mounts' } }, 422)
        return response(
          setupState('mounts', {
            completed_steps: ['claim', 'connect', 'libraries'],
            libraries: [library],
            revision: 5,
          }),
        )
      }),
    )
    runSetup()

    const heading = await screen.findByRole('heading', { name: 'Find library folders' })
    await waitFor(() => expect(heading).toHaveFocus())
    const path = screen.getByLabelText('Reading Shelf folder in Audiobookshelf')
    fireEvent.input(path, { target: { value: '/owner/typed/books' } })
    const check = screen.getByRole('button', { name: 'Check again' })
    check.focus()
    fireEvent.click(check)

    const alert = await screen.findByRole('alert')
    await waitFor(() =>
      expect(alert).toHaveTextContent(
        'Those folders could not be checked. Reload the page, then check again.',
      ),
    )
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(alert.previousElementSibling).toBe(check)
    expect(path).toHaveValue('/owner/typed/books')
    expect(check).toBeEnabled()
    expect(check).toHaveFocus()
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
          completed_steps: ['claim', 'connect', 'libraries'],
          libraries: [library],
          mounts,
          revision,
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const path = await screen.findByLabelText('Reading Shelf folder in Audiobookshelf')
    fireEvent.input(path, { target: { value: '/owner/typed/books' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(document.querySelector('.mount-line')).not.toBeNull())

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
    expect(screen.getByRole('heading', { name: 'Find library folders' })).toBeVisible()
    expect(screen.getByLabelText('Reading Shelf folder in Audiobookshelf')).toHaveValue(
      '/owner/typed/books',
    )
    expect(document.querySelector('.mount-line')).toHaveTextContent('Reading Shelf: Not found')
  })

  it('summarizes the configured scope with one reopen seam in step order', async () => {
    const changes: unknown[] = []
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer-access')
        return response({
          revision: 7,
          mapping: { engine: 'openrouter', model: 'openai/gpt-6-luna' },
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
          completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers', 'reviewer'],
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
    expect(screen.getByRole('heading', { name: 'Start' })).toBeVisible()
    expect(
      screen.getByText(
        'Check your choices, then start. Mechanical Librarian will read your library and suggest changes for you to review.',
      ),
    ).toBeVisible()
    // Rows follow the step order: address, libraries, sources, reviewer.
    expect([...summary.querySelectorAll('dt')].map((node) => node.textContent)).toEqual([
      'Audiobookshelf address',
      'Libraries',
      'Metadata sources',
      'Cover reviewer',
    ])
    expect(summary).toHaveTextContent('https://books.invalid:8443')
    expect(summary).toHaveTextContent('Open Library')
    expect(summary).not.toHaveTextContent('Hardcover')
    expect(summary).toHaveTextContent('Reading Shelf: files found')
    expect(summary).toHaveTextContent('GPT-6 Luna on OpenRouter')
    // No credential and no filesystem path reaches the summary.
    expect(summary.textContent).not.toContain('openai/gpt-6-luna')
    expect(summary.textContent).not.toMatch(/(^|\s)\/[A-Za-z]/u)
    // The write notice is said once, before Start.
    expect(
      screen.getAllByText(
        'Nothing in your library changes. Writes stay off until you turn them on in Activity.',
      ),
    ).toHaveLength(1)

    // One `reopen` seam: the recorded ledger rows plus four summary rows, and
    // each control names the step it reopens. The folder step is not listed
    // once every folder was found.
    const ledgerChanges = [...document.querySelectorAll('#setup-ledger button')]
    expect(ledgerChanges.map((node) => node.getAttribute('aria-label'))).toEqual([
      'Change Connect Audiobookshelf',
      'Change Choose libraries',
      'Change Choose metadata sources',
      'Change Cover reviewer (optional)',
    ])
    const summaryChanges = [...summary.querySelectorAll('.summary-change')]
    expect(summaryChanges).toHaveLength(4)
    expect(summaryChanges.map((node) => node.getAttribute('aria-label'))).toEqual([
      'Change the Audiobookshelf address',
      'Change the selected libraries',
      'Change the metadata sources',
      'Change the cover reviewer',
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Change the selected libraries' }))
    await waitFor(() => expect(changes).toEqual([{ expected_revision: 7, step: 'libraries' }]))
  })

  it.each([
    [
      503,
      'activation_failed',
      'Mechanical Librarian could not start. Press Start again. If it fails again, the container log says why.',
    ],
    [401, 'session_expired', 'Your sign-in expired, so that step was not saved.'],
    [422, 'mounts_unready', 'A library’s folder was not found. Find it before you start.'],
  ] as const)(
    'posts one kickoff for two presses and places a %s %s failure under the button (#1677)',
    async (status, code, copy) => {
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
            completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers'],
          }),
        )
      })
      vi.stubGlobal('fetch', fetch)
      runSetup()

      const heading = await screen.findByRole('heading', { name: 'Start' })
      await waitFor(() => expect(heading).toHaveFocus())
      const submit = await screen.findByRole('button', { name: 'Start' })
      const form = submit.closest('form')
      if (!form) throw new Error('kickoff form missing')
      submit.focus()
      fireEvent.click(submit)
      fireEvent.click(submit)
      // Natively disabling the focused button would drop focus to the page in a browser.
      expect(submit).not.toBeDisabled()
      expect(submit).toHaveAttribute('aria-disabled', 'true')
      expect(submit).toHaveFocus()
      expect(kickoffs).toBe(1)

      release(response({ detail: { error: code } }, status))
      const alert = await screen.findByRole('alert')
      await waitFor(() => expect(alert).toHaveTextContent(copy))
      expect(screen.getAllByRole('alert')).toHaveLength(1)
      expect(alert.previousElementSibling).toBe(submit)
      expect(alert.nextElementSibling).toBeNull()
      expect(form.firstElementChild).toHaveClass('notice')
      expect(submit).not.toHaveAttribute('aria-disabled')
      expect(submit).toBeEnabled()
      expect(submit).toHaveFocus()
      expect(screen.getByRole('heading', { name: 'Start' })).toBeVisible()
      expect(kickoffs).toBe(1)
    },
  )

  it.each([
    [403, 'csrf'],
    [409, 'stale'],
  ] as const)(
    'reloads the kickoff step when it is refused with %s %s (#1677)',
    async (status, code) => {
      let reads = 0
      const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/reviewer-access')
          return response({ revision: 7, mapping: null, codex: { state: 'host_unsupported' } })
        if (url === '/api/setup/kick-off' && init?.method === 'POST')
          return response({ detail: { error: code } }, status)
        if (url === '/api/setup/state') reads += 1
        return response(
          setupState('kick_off', {
            completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers'],
          }),
        )
      })
      vi.stubGlobal('fetch', fetch)
      runSetup()

      const heading = await screen.findByRole('heading', { name: 'Start' })
      await waitFor(() => expect(heading).toHaveFocus())
      const submit = screen.getByRole('button', { name: 'Start' })
      submit.focus()
      fireEvent.click(submit)

      await waitFor(() => expect(reads).toBe(2))
      const alert = await screen.findByRole('alert')
      await waitFor(() =>
        expect(alert).toHaveTextContent(
          'Setup changed in another tab. The latest recorded step has been restored.',
        ),
      )
      expect(screen.getAllByRole('alert')).toHaveLength(1)
      const fresh = screen.getByRole('button', { name: 'Start' })
      expect(fresh).not.toBe(submit)
      expect(alert.closest('form')?.firstElementChild).toBe(alert)
      expect(fresh).not.toHaveAttribute('aria-disabled')
      await waitFor(() => expect(screen.getByRole('heading', { name: 'Start' })).toHaveFocus())
    },
  )

  it('removes unchecked provider credentials from the visual and accessibility trees', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(setupState('providers'))),
    )
    runSetup()

    const openLibrary = await screen.findByRole('checkbox', { name: 'Open Library' })
    // A default-ticked source shows its fields on its own card.
    await waitFor(() => expect(openLibrary).toBeChecked())
    const contact = await screen.findByRole('textbox', { name: 'Open Library contact email' })
    expect(contact).toBeEnabled()
    const contactField = contact.closest('.field')
    if (!contactField) throw new Error('provider credential field missing')
    expect(getComputedStyle(contactField).display).toBe('grid')

    // Unticked, its fields leave the visual and accessibility trees.
    fireEvent.click(openLibrary)

    expect(contact).toBeDisabled()
    expect(contactField).toHaveAttribute('hidden')
    expect(getComputedStyle(contactField).display).toBe('none')
    expect(screen.queryByRole('textbox', { name: 'Open Library contact email' })).toBeNull()
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

    await screen.findByRole('heading', { name: 'Choose metadata sources' })
    await untickSources()
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
    // Only the four the owner left ticked.
    expect(submitted).toEqual({
      expected_revision: 3,
      providers: [
        // iTunes is listed with the book providers (#1458), so it comes first.
        { name: 'itunes', adapter: 'itunes_search', enabled: true },
        { name: 'audible', adapter: 'audible_catalog', enabled: true },
        { name: 'audnexus', adapter: 'audnexus', enabled: true },
        { name: 'audiosilo', adapter: 'audiosilo', enabled: true },
      ].map((value) => ({ ...value, cover_enabled: false, contact_email: null, key: null })),
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
            { library_id: 'audio', media_kind: 'book', name: 'Audio' },
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
              display_name: 'Books',
              provider_connections: [],
            },
          ],
          mounts: [{ library: 'books', state: 'missing' }],
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
    // The wizard's own refusal speaks first; no browser bubble precedes it.
    expect(form.noValidate).toBe(true)
    // Every book library starts ticked; the podcast library is named once.
    const books = screen.getByRole('checkbox', { name: 'Books' })
    const audio = screen.getByRole('checkbox', { name: 'Audio' })
    expect(books).toBeChecked()
    expect(audio).toBeChecked()
    expect(
      screen.getByText('Podcast libraries aren’t supported, so they’re not shown.'),
    ).toBeVisible()

    fireEvent.click(books)
    fireEvent.click(audio)
    fireEvent.submit(form)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Select at least one supported book library.',
    )
    // It is about no one field, so it sits under Continue, as the server's
    // refusal of the same press does (#1677).
    expect(screen.getByRole('alert').previousElementSibling).toBe(
      screen.getByRole('button', { name: 'Continue' }),
    )
    expect(libraryMutations).toBe(0)

    fireEvent.click(books)
    fireEvent.submit(form)
    expect(await screen.findByRole('heading', { name: 'Find library folders' })).toBeVisible()
    expect(libraryMutations).toBe(1)
    const mutation = fetch.mock.calls.find(
      ([request, init]) =>
        requestUrl(request) === '/api/setup/libraries' && init?.method === 'POST',
    )
    // No owner name is sent: the server records the Audiobookshelf name.
    expect(jsonBody(mutation?.[1]?.body)).toEqual({
      expected_revision: 3,
      libraries: [{ library_id: 'books', media_kind: 'book', name: 'books' }],
    })
  })

  it('places a library step failure under Continue and keeps the selection (#1664)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url.startsWith('/api/setup/libraries?'))
          return response({
            libraries: [{ library_id: 'books', media_kind: 'book', name: 'Books' }],
          })
        if (url === '/api/setup/libraries' && init?.method === 'POST')
          return response({ detail: { error: 'unavailable' } }, 502)
        return response(setupState('libraries'))
      }),
    )
    runSetup()

    const heading = await screen.findByRole('heading', { name: 'Choose libraries' })
    await waitFor(() => expect(heading).toHaveFocus())
    const books = screen.getByRole('checkbox', { name: 'Books' })
    expect(books).toBeChecked()
    const next = screen.getByRole('button', { name: 'Continue' })
    next.focus()
    fireEvent.click(next)

    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(alert).toHaveTextContent('That step was not saved.'))
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(alert.previousElementSibling).toBe(next)
    expect(books).toBeChecked()
    expect(next).toBeEnabled()
    expect(next).toHaveFocus()
  })

  it('uses each Audiobookshelf name with no rename control', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL) => {
        const url = requestUrl(request)
        if (url.startsWith('/api/setup/libraries?'))
          return response({
            libraries: [
              { library_id: 'books', media_kind: 'book', name: 'Books ' },
              { library_id: 'audio', media_kind: 'book', name: 'Audio' },
            ],
          })
        return response(setupState('libraries'))
      }),
    )
    runSetup()

    const heading = await screen.findByRole('heading', { name: 'Choose libraries' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(
      screen.getAllByRole('checkbox').map((node) => node.closest('label')?.textContent),
    ).toEqual(['Books ', 'Audio'])
    expect(screen.queryByRole('button', { name: /^Change name/u })).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(document.querySelector('input[name^="display-"]')).toBeNull()
  })

  it.each([
    ['Google Books', 'google-key'],
    ['Hardcover', 'hardcover-key'],
  ] as const)('refuses Continue for %s without a key on its card', async (label, keyName) => {
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
    const heading = await screen.findByRole('heading', { name: 'Choose metadata sources' })
    await waitFor(() => expect(heading).toHaveFocus())
    await untickSources()
    fireEvent.click(screen.getByRole('checkbox', { name: label }))
    const key = document.querySelector<HTMLInputElement>(`input[name="${keyName}"]`)
    const form = key?.closest('form')
    if (!key || !form) throw new Error('provider form missing')

    fireEvent.submit(form)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(settingsVocabulary.errors.provider_key)
    // The refusal sits on that source's own card, by its key.
    expect(key.closest('.option')).toContainElement(alert)
    expect(key).toHaveFocus()
    expect(providerMutations).toBe(0)

    fireEvent.input(key, { target: { value: 'a-real-looking-key' } })
    fireEvent.submit(form)
    await waitFor(() => expect(providerMutations).toBe(1))
  })

  it('explains every mount comparison state without exposing raw enums', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        response(
          setupState('mounts', {
            completed_steps: ['claim', 'connect', 'libraries'],
            libraries: [
              { library_id: 'books-id', media_kind: 'book', name: 'books', display_name: 'Books' },
              {
                library_id: 'history-id',
                media_kind: 'book',
                name: 'history',
                display_name: 'History',
              },
              {
                library_id: 'essays-id',
                media_kind: 'book',
                name: 'essays',
                display_name: 'Essays',
              },
              {
                library_id: 'archive-id',
                media_kind: 'book',
                name: 'archive',
                display_name: 'Archive',
              },
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

    await screen.findByRole('heading', { name: 'Find library folders' })
    const lines = await waitFor(() => {
      const found = [...document.querySelectorAll<HTMLElement>('.mount-line')]
      if (found.length !== 3) throw new Error('folder lines missing')
      return found
    })
    // Each line leads with the library and one short state, then what to do.
    expect(lines.map((line) => line.querySelector('span')?.textContent)).toEqual([
      'History: Not found',
      'Essays: Not found',
      'Archive: Not found',
    ])
    expect(lines.map((line) => line.querySelector('.mount-remedy')?.textContent)).toEqual([
      'Mechanical Librarian can’t see this library’s folder. Add it to the container as a read-only volume, then check again.',
      'More than one read-only volume matches this library’s folders. Keep one, then check again.',
      'Audiobookshelf reported no folder for this library. Enter its folder, then check again.',
    ])
    // Only the folder that is not mounted links the deployment guide.
    const [notMounted] = lines
    if (!notMounted) throw new Error('folder line missing')
    const guide = within(notMounted).getByRole('link', {
      name: 'Media mounts in the deployment guide',
    })
    expect(guide).toHaveAttribute(
      'href',
      'https://github.com/atabbott/mechanical-librarian/blob/main/docs/operations/deployment.md#media-mounts',
    )
    expect(guide).toHaveAttribute('rel', 'noreferrer')
    expect(document.querySelectorAll('.mount-line a')).toHaveLength(1)
    for (const raw of [/matched/u, /missing/u, /ambiguous/u, /unavailable/u, /No mapping/u])
      expect(screen.queryByText(raw)).toBeNull()
  })

  it('shows only the libraries whose folder was not found', async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.startsWith('/api/setup/libraries?'))
        return response({
          libraries: [
            { library_id: 'books-id', media_kind: 'book', name: 'Books', service_path: '/books' },
            {
              library_id: 'audio-id',
              media_kind: 'book',
              name: 'Listening',
              service_path: '/srv/listening',
            },
          ],
        })
      if (url === '/api/setup/mounts' && init?.method === 'POST') return response({})
      return response(
        setupState('mounts', {
          completed_steps: ['claim', 'connect', 'libraries'],
          libraries: [
            { library_id: 'books-id', media_kind: 'book', name: 'books', display_name: 'Books' },
            {
              library_id: 'audio-id',
              media_kind: 'book',
              name: 'audio',
              display_name: 'Listening',
            },
          ],
          mounts: [
            { library: 'books', state: 'matched' },
            { library: 'audio', state: 'missing' },
          ],
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    const folder = await screen.findByRole('textbox', {
      name: 'Listening folder in Audiobookshelf',
    })
    expect(folder).toHaveValue('/srv/listening')
    expect(folder).toHaveAttribute('placeholder', '/audiobooks')
    expect(screen.queryByRole('textbox', { name: 'Books folder in Audiobookshelf' })).toBeNull()
    expect(screen.queryByText(/^Books:/u)).toBeNull()
    fireEvent.input(folder, { target: { value: ' /audiobooks ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))

    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([request, init]) =>
            requestUrl(request) === '/api/setup/mounts' && init?.method === 'POST',
        ),
      ).toBe(true),
    )
    const mutation = fetch.mock.calls.find(
      ([request, init]) => requestUrl(request) === '/api/setup/mounts' && init?.method === 'POST',
    )
    // A found library is compared as Audiobookshelf reports it.
    expect(jsonBody(mutation?.[1]?.body)).toEqual({
      expected_revision: 3,
      service_paths: { books: null, audio: '/audiobooks' },
    })
  })

  it('draws the providers form at once while the library read is pending', async () => {
    let release: (value: Response) => void = () => undefined
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    const fetch = vi.fn<(request: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async (request) => {
        if (requestUrl(request).startsWith('/api/setup/libraries?')) return held
        return response(setupState('providers'))
      },
    )
    vi.stubGlobal('fetch', fetch)
    const recorded = () =>
      fetch.mock.calls.filter(
        ([request, init]) =>
          requestUrl(request) === '/api/setup/providers' && init?.method === 'POST',
      ).length
    runSetup()

    const heading = await screen.findByRole('heading', { name: 'Choose metadata sources' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(screen.getByText('Loading your libraries…')).toBeVisible()
    for (const label of ['Inventaire', 'Open Library', 'Audible', 'iTunes'])
      expect(screen.getByRole('checkbox', { name: label })).toBeEnabled()
    // Continue is held until the read has ticked the suited sources, so a
    // fast press cannot record none.
    const next = screen.getByRole('button', { name: 'Continue' })
    expect(next).toBeEnabled()
    expect(next).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(next)
    await new Promise(requestAnimationFrame)
    expect(recorded()).toBe(0)
    expect(screen.queryByRole('button', { name: 'Skip providers' })).toBeNull()
    // A source the owner ticks before the read returns is theirs to keep.
    const audible = screen.getByRole('checkbox', { name: 'Audible' })
    fireEvent.click(audible)
    expect(audible).toBeChecked()

    release(
      response({
        libraries: [
          {
            library_id: 'books',
            media_kind: 'book',
            name: 'Reading Shelf',
            audiobooks_only: false,
          },
        ],
      }),
    )
    await waitFor(() => expect(screen.queryByText('Loading your libraries…')).toBeNull())
    // Late defaults never override the owner's own choice.
    expect(
      [...document.querySelectorAll<HTMLInputElement>('input[type=checkbox]:checked')].map(
        (box) => box.name,
      ),
    ).toEqual(['audible-enabled'])
    expect(next).not.toHaveAttribute('aria-disabled')
    fireEvent.click(next)
    await waitFor(() => expect(recorded()).toBe(1))
  })

  const allSources = [
    'inventaire-enabled',
    'open-enabled',
    'hardcover-enabled',
    'google-enabled',
    'itunes-enabled',
    'audible-enabled',
    'audnexus-enabled',
    'audiosilo-enabled',
  ]
  it.each([
    [
      'a library not marked audiobooks-only',
      [{ library_id: 'books', media_kind: 'book', name: 'Reading Shelf', audiobooks_only: false }],
      allSources,
    ],
    [
      'an audiobooks-only library',
      [{ library_id: 'audio', media_kind: 'book', name: 'Listening Room', audiobooks_only: true }],
      [
        'open-enabled',
        'hardcover-enabled',
        'itunes-enabled',
        'audible-enabled',
        'audnexus-enabled',
        'audiosilo-enabled',
      ],
    ],
    [
      'both kinds of library',
      [
        { library_id: 'books', media_kind: 'book', name: 'Reading Shelf', audiobooks_only: false },
        { library_id: 'audio', media_kind: 'book', name: 'Listening Room', audiobooks_only: true },
      ],
      allSources,
    ],
    ['a failed library read', null, allSources],
  ] as const)(
    'ticks the sources suited to the chosen libraries: %s',
    async (_name, libraries, ticked) => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (request: RequestInfo | URL) => {
          if (requestUrl(request).startsWith('/api/setup/libraries?'))
            return libraries === null
              ? response({ detail: { error: 'refused' } }, 502)
              : response({ libraries })
          return response(
            setupState('providers', {
              completed_steps: ['claim', 'connect', 'libraries', 'mounts'],
              libraries: (libraries ?? [{ library_id: 'books' }]).map((item) => ({
                library_id: item.library_id,
                media_kind: 'book',
                name: item.library_id,
                provider_connections: [],
              })),
            }),
          )
        }),
      )
      runSetup()

      await screen.findByRole('heading', { name: 'Choose metadata sources' })
      await waitFor(() => expect(screen.queryByText('Loading your libraries…')).toBeNull())
      const groups = [...document.querySelectorAll('fieldset.provider-group')]
      expect(groups.map((group) => group.querySelector('legend')?.textContent)).toEqual([
        'Books and ebooks',
        'Audiobooks',
      ])
      // No audiobook is sent to iTunes, so it sits with the book sources (#1458).
      expect(
        within(groups[1] as HTMLElement).queryByRole('checkbox', { name: 'iTunes' }),
      ).toBeNull()
      expect(
        [...document.querySelectorAll<HTMLInputElement>('input[type=checkbox]:checked')].map(
          (box) => box.name,
        ),
      ).toEqual(ticked)
      // Every ticked source stays visible and can be unticked before Continue.
      for (const box of document.querySelectorAll<HTMLInputElement>('input[type=checkbox]'))
        expect(box).toBeVisible()
      expect(document.querySelector('.suggestion')).toBeNull()
      expect(screen.queryByText(/^Suggested for/u)).toBeNull()
      expect(document.querySelector('[role=alert]')).toBeNull()
    },
  )

  it('says most ebooks will wait when neither Hardcover nor Google Books is ticked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(setupState('providers'))),
    )
    runSetup()

    await screen.findByRole('heading', { name: 'Choose metadata sources' })
    await waitFor(() => expect(screen.queryByText('Loading your libraries…')).toBeNull())
    const coverage = document.querySelector('.muted[role=status]')
    expect(coverage?.textContent).toBe('')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hardcover' }))
    expect(coverage?.textContent).toBe('')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Google Books' }))
    expect(coverage?.textContent).toBe(
      'Without Hardcover or Google Books, most ebooks will wait for your review.',
    )
    // It only says so; Continue stays available.
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Google Books' }))
    expect(coverage?.textContent).toBe('')
  })

  it('asks Open Library only for an email and links each key to the page that issues it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(setupState('providers'))),
    )
    runSetup()

    const open = (await screen.findByRole('checkbox', { name: 'Open Library' })).closest('.option')
    expect(open?.querySelector('a')).toBeNull()
    expect(open).toHaveTextContent('Needs your email address. No account needed.')
    expect(screen.getByRole('link', { name: 'Get a Hardcover key' })).toHaveAttribute(
      'href',
      'https://hardcover.app/account/api',
    )
    expect(screen.getByRole('link', { name: 'Get a Google Books key' })).toHaveAttribute(
      'href',
      'https://console.cloud.google.com/apis/credentials',
    )
  })

  it('says which folder is still not found after a check that finds nothing', async () => {
    const missing = setupState('mounts', {
      completed_steps: ['claim', 'connect', 'libraries'],
      libraries: [
        {
          connection: 'primary',
          display_name: 'Listening Room',
          library_id: 'audio-id',
          media_kind: 'book',
          name: 'audio',
          provider_connections: [],
        },
      ],
      mounts: [{ library: 'audio', state: 'missing' }],
      revision: 5,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url.startsWith('/api/setup/libraries?')) return response({ libraries: [] })
        if (url === '/api/setup/mounts' && init?.method === 'POST') return response({})
        return response(missing)
      }),
    )
    runSetup()
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    await waitFor(() =>
      expect(document.querySelector('#setup-status')?.textContent).toBe(
        'Still not found: Listening Room.',
      ),
    )
    expect(document.querySelector('.confirmation')).toHaveTextContent(
      'Still not found: Listening Room.',
    )
    expect(screen.getByRole('heading', { name: 'Find library folders' })).toBeVisible()
  })

  it('confirms the mounts it found on the next step in one line', async () => {
    let current = setupState('mounts', {
      completed_steps: ['claim', 'connect', 'libraries'],
      libraries: [
        {
          connection: 'primary',
          display_name: 'Reading Shelf',
          library_id: 'books-id',
          media_kind: 'book',
          name: 'books',
          provider_connections: [],
        },
        {
          connection: 'primary',
          display_name: 'Listening Room',
          library_id: 'audio-id',
          media_kind: 'book',
          name: 'audio',
          provider_connections: [],
        },
      ],
      revision: 5,
    })
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
            {
              library_id: 'audio-id',
              media_kind: 'book',
              name: 'Listening Room',
              service_path: '/discovered/audio',
              service_path_state: 'available',
            },
          ],
        })
      if (url === '/api/setup/mounts' && init?.method === 'POST') {
        current = setupState('providers', {
          completed_steps: ['claim', 'connect', 'libraries', 'mounts'],
          libraries: [
            {
              connection: 'primary',
              display_name: 'Reading Shelf',
              library_id: 'books-id',
              media_kind: 'book',
              mount: 'ebooks',
              name: 'books',
              provider_connections: [],
            },
            {
              connection: 'primary',
              display_name: 'Listening Room',
              library_id: 'audio-id',
              media_kind: 'book',
              mount: 'audio',
              name: 'audio',
              provider_connections: [],
            },
          ],
          mounts: [
            { library: 'books', state: 'matched' },
            { library: 'audio', state: 'matched' },
          ],
          revision: 6,
        })
        return response({})
      }
      return response(current)
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByRole('heading', { name: 'Choose metadata sources' })).toBeVisible()
    const confirmation = document.querySelector('.confirmation')
    expect(confirmation).toHaveTextContent('Found the files for Reading Shelf and Listening Room.')
    // Setup never counts, lists or names a mount or a path.
    expect(confirmation?.textContent).not.toMatch(/\/discovered|mount/u)
    await waitFor(() =>
      expect(document.querySelector('#setup-status')?.textContent).toBe(
        'Found the files for Reading Shelf and Listening Room.',
      ),
    )
  })

  it('keeps the form-expired copy for a refused claim nonce', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/claim')
          return response({ claimed: true, nonce: 'n'.repeat(32), revision: 2 })
        if (url === '/api/setup/session' && init?.method === 'POST')
          return response({ detail: { error: 'csrf' } }, 403)
        return response({ detail: { error: 'unauthorized' } }, 401)
      }),
    )
    runSetup()

    fireEvent.input(await screen.findByLabelText('Owner password'), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This setup form expired. Reload it and try again.',
    )
  })

  it('reads a form token rotated by another tab as a change in another tab', async () => {
    let reads = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/providers' && init?.method === 'POST')
          return response({ detail: { error: 'csrf' } }, 403)
        if (url === '/api/setup/state') reads += 1
        return response(setupState('providers', { csrf_token: `token-${String(reads)}` }))
      }),
    )
    runSetup()

    await screen.findByRole('heading', { name: 'Choose metadata sources' })
    await untickSources()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'Setup changed in another tab. The latest recorded step has been restored.',
    )
    // The reloaded step opens with the alert and focuses its heading.
    expect(alert.parentElement?.firstElementChild).toBe(alert)
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Choose metadata sources' })).toHaveFocus(),
    )
    // The state was read again, which issues this tab a fresh form token.
    expect(reads).toBe(2)
  })

  it.each([
    [422, 'invalid_providers', 'A source could not be saved.'],
    [503, 'unavailable', 'That step was not saved.'],
  ] as const)(
    'places a failed Continue (%i) under the providers action row (#1664)',
    async (status, code, copy) => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
          const url = requestUrl(request)
          if (url.startsWith('/api/setup/libraries?')) return response({ libraries: [] })
          if (url === '/api/setup/providers' && init?.method === 'POST')
            return response({ detail: { error: code } }, status)
          return response(setupState('providers'))
        }),
      )
      runSetup()

      const heading = await screen.findByRole('heading', { name: 'Choose metadata sources' })
      await waitFor(() => expect(heading).toHaveFocus())
      await untickSources()
      const pressed = screen.getByRole('button', { name: 'Continue' })
      pressed.focus()
      fireEvent.click(pressed)

      const alert = await screen.findByRole('alert')
      await waitFor(() => expect(alert).toHaveTextContent(copy))
      expect(screen.getAllByRole('alert')).toHaveLength(1)
      // After the whole row, not inside it beside the buttons.
      const row = alert.previousElementSibling
      expect(row).toHaveClass('actions')
      expect(row).toContainElement(screen.getByRole('button', { name: 'Continue' }))
      expect(alert.nextElementSibling).toBeNull()
      expect(pressed).toBeEnabled()
      expect(pressed).toHaveFocus()
    },
  )

  it('preselects the recommended OpenRouter model with its cost and hides the others behind Choose another model', async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL) => {
      if (requestUrl(request) === '/api/setup/reviewer-access')
        return response({
          schema_version: 'reviewer-access-view-v2',
          cover_metadata: null,
          cost_evidence: {
            schema_version: 'reviewer-cost-evidence-v2',
            models: [{ model: 'openai/gpt-6-luna', task: 'cover', per_1000_usd: '4.2' }],
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
          completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers'],
        }),
      )
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()

    expect(await screen.findByText('GPT-6 Luna: about $4.2 per 1,000 works.')).toBeVisible()
    expect(
      screen.getByText(
        'An AI model can look at cover images and tell you what it sees. It only advises, and you still choose. You can set this up later in Settings.',
      ),
    ).toBeVisible()
    // The recommended model is chosen; the others and the Model ID wait.
    const model = document.querySelector<HTMLInputElement>('input[name="reviewer-model"]')
    if (!model) throw new Error('reviewer model field missing')
    expect(model).toHaveValue('openai/gpt-6-luna')
    expect(model).not.toBeVisible()
    const toggle = screen.getByRole('button', { name: 'Choose another model' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: /^Use dots/u })).toBeNull()
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('textbox', { name: 'Model ID' })).toBe(model)
    // Rates and data use are reference detail, folded until the owner opens them.
    const limits = screen.getByText(
      '20 requests/minute; 50 requests/day before $10 in lifetime purchases or 1000 after.',
    )
    const about = limits.closest('details')
    if (!about) throw new Error('reviewer detail disclosure missing')
    expect(about).not.toHaveAttribute('open')
    expect(about.querySelector('summary')).toHaveTextContent('Prices and data use')
    about.open = true
    expect(limits).toBeVisible()
    expect(screen.getByText('Check the key to see this model’s prices.')).toBeVisible()
    expect(screen.getByText('One request per work, with every cover it compares.')).toBeVisible()
    expect(screen.getByText('Cover previews are sent to the selected provider.')).toBeVisible()
    const getKey = screen.getByRole('link', { name: 'Get a key' })
    expect(getKey).toHaveAttribute('href', 'https://openrouter.ai/settings/keys')
    expect(getKey).toHaveAttribute('rel', 'noreferrer')
    expect(getKey).toBeVisible()
    // The link sits right beside the key field, outside its label (#1516).
    const keyInput = screen.getByLabelText('OpenRouter API key')
    expect(getKey.parentElement?.previousElementSibling).toBe(keyInput.closest('.field'))
    fireEvent.click(
      screen.getByRole('button', { name: 'Use dots-studio/dots-3-note-preview:free (Free)' }),
    )
    expect(model).toHaveValue('dots-studio/dots-3-note-preview:free')
    expect(model).toHaveFocus()
    expect(
      screen.getByText('dots-studio/dots-3-note-preview:free: cost not measured.'),
    ).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Use GPT-6 Luna (Paid)' }))
    expect(model).toHaveValue('openai/gpt-6-luna')
    fireEvent.input(model, { target: { value: 'manual/vision-model' } })
    expect(model).toHaveValue('manual/vision-model')
    for (const gone of [
      /Recommended advisory cover reviewer/u,
      /Optional description advice/u,
      /Current cover selection/u,
    ])
      expect(screen.queryByText(gone)).toBeNull()
  })

  const startState = () =>
    setupState('kick_off', {
      completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers', 'reviewer'],
    })
  // The Start target carries only the lens and, once known, the run.
  const landedAt = (place: ReturnType<typeof stubLocation>) => {
    expect(place.replace).toHaveBeenCalledTimes(1)
    const target = new URL(String(place.replace.mock.calls[0]?.[0]), 'https://setup.invalid')
    expect(target.pathname).toBe('/library')
    expect(
      [...target.searchParams.keys()].every((key) => ['lens', 'preparation'].includes(key)),
    ).toBe(true)
    return `${target.pathname}${target.search}`
  }

  it('lands in the library with the preparation run once it is known', async () => {
    const run = `preparation_run_${'a'.repeat(64)}`
    let reads = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/kick-off' && init?.method === 'POST')
        return response({ message: 'Restarting into the normal workspace.' }, 202)
      if (url === '/api/v1/review-preparation') {
        reads += 1
        return response({ presentation: reads > 1 ? { run_id: run, state: 'running' } : null })
      }
      return response(startState())
    })
    vi.stubGlobal('fetch', fetch)
    const place = stubLocation()
    runSetup(place)

    fireEvent.click(await screen.findByRole('button', { name: 'Start' }))
    await waitFor(() => expect(place.replace).toHaveBeenCalled(), { timeout: 3000 })
    expect(landedAt(place)).toBe(`/library?lens=all&preparation=${run}`)
    expect(reads).toBe(2)
    // No progress page is drawn on the way.
    expect(screen.queryByRole('heading', { name: 'Preparing your library' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open workspace' })).toBeNull()
  })

  it('lands in the library without a run when none appears in time', async () => {
    const previousSetTimeout = globalThis.setTimeout
    const started = Date.now()
    let offset = 0
    vi.spyOn(Date, 'now').mockImplementation(() => started + offset)
    // Each half-second wait passes at once, on a clock that moves with it.
    vi.stubGlobal(
      'setTimeout',
      vi.fn((callback: () => void, delay?: number) => {
        if (delay === 500) {
          offset += 500
          queueMicrotask(callback)
          return 1
        }
        return previousSetTimeout(callback, delay)
      }),
    )
    let reads = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/kick-off' && init?.method === 'POST')
          return response({ message: 'Restarting into the normal workspace.' }, 202)
        if (url === '/api/v1/review-preparation') {
          reads += 1
          return response({ presentation: null })
        }
        return response(startState())
      }),
    )
    const place = stubLocation()
    runSetup(place)

    fireEvent.click(await screen.findByRole('button', { name: 'Start' }))
    await waitFor(() => expect(place.replace).toHaveBeenCalled())
    expect(landedAt(place)).toBe('/library?lens=all')
    // Every 500 ms for 10 seconds, then no more.
    expect(reads).toBe(20)
    expect(offset).toBe(10_000)
  })

  it('gives up a hung read at the deadline and lands in the library', async () => {
    const limits: number[] = []
    const timer = new AbortController()
    vi.spyOn(AbortSignal, 'timeout').mockImplementation((delay: number) => {
      limits.push(delay)
      return timer.signal
    })
    let reads = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/kick-off' && init?.method === 'POST')
          return response({ message: 'Restarting into the normal workspace.' }, 202)
        if (url === '/api/v1/review-preparation') {
          reads += 1
          // The read never answers; only its time limit ends it.
          return new Promise<Response>((_, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('timed out', 'TimeoutError'))
            })
          })
        }
        return response(startState())
      }),
    )
    const place = stubLocation()
    runSetup(place)

    fireEvent.click(await screen.findByRole('button', { name: 'Start' }))
    await waitFor(() => expect(reads).toBe(1))
    // The read is limited to the time left before the 10-second deadline.
    expect(limits).toHaveLength(1)
    expect(limits[0]).toBeGreaterThan(0)
    expect(limits[0]).toBeLessThanOrEqual(10_000)
    expect(place.replace).not.toHaveBeenCalled()
    timer.abort()
    await waitFor(() => expect(place.replace).toHaveBeenCalled())
    expect(landedAt(place)).toBe('/library?lens=all')
    expect(reads).toBe(1)
  })

  it('lands in the library after a 401', async () => {
    let reads = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/kick-off' && init?.method === 'POST')
          return response({ message: 'Restarting into the normal workspace.' }, 202)
        if (url === '/api/v1/review-preparation') {
          reads += 1
          return response({ error: 'unauthorized' }, 401)
        }
        return response(startState())
      }),
    )
    const place = stubLocation()
    runSetup(place)

    fireEvent.click(await screen.findByRole('button', { name: 'Start' }))
    await waitFor(() => expect(place.replace).toHaveBeenCalled())
    // The workspace's own sign-in returns the owner to Library.
    expect(landedAt(place)).toBe('/library?lens=all')
    expect(reads).toBe(1)
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

// The reviewer step checks an existing sign-in once per visit, unasked.
const recheckAs = (state: string, failure: string | null = null) =>
  response({ state, failure, verification_url: null, user_code: null })
const isRecheck = (url: string, init?: RequestInit) =>
  url.endsWith('/codex/recheck') && init?.method === 'POST'
// Sign in with ChatGPT waits while that check runs.
const signInReady = async () => {
  await screen.findByRole('heading', { name: 'Cover reviewer (optional)' })
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled(),
  )
  return screen.getByRole('button', { name: 'Sign in with ChatGPT' })
}

it('keeps alternatives usable when the contained profile is absent', async () => {
  const fetch = vi.fn(async (request: RequestInfo | URL) =>
    response(
      requestUrl(request) === '/api/setup/reviewer-access'
        ? reviewerAccess('not_provisioned', 'profile_not_provisioned')
        : setupState('reviewer'),
    ),
  )
  vi.stubGlobal('fetch', fetch)
  runSetup()
  expect(
    await screen.findByText(
      'The Codex profile is missing. Restore the image’s profile or correct the profile path, then restart the service.',
    ),
  ).toBeVisible()
  // Only the remedy: no sign-in can start on this host, and none is in
  // progress to cancel (#1516).
  expect(screen.queryByRole('button', { name: 'Sign in with ChatGPT' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Cancel sign-in' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Check existing sign-in' })).toBeNull()
  expect(
    fetch.mock.calls.some(([request]) => /\/codex\/(device|recheck)$/u.test(requestUrl(request))),
  ).toBe(false)
  expect(screen.getByRole('button', { name: 'Skip for now' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Check key and use OpenRouter' })).toBeEnabled()
  expect(screen.getByText('Uses your ChatGPT plan.')).toBeVisible()
  expect(screen.queryByText(/Model:/u)).toBeNull()
  expect(screen.queryByText(/Optional description advice/u)).toBeNull()
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
  expect(await screen.findByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled()
  expect(
    fetch.mock.calls.filter(
      ([request, init]) => requestUrl(request).endsWith('/device') && init?.method === 'POST',
    ),
  ).toHaveLength(0)
})

// The reviewer step focuses its heading on the next animation frame. A test
// presses a control only after that, as an owner would; otherwise, under load,
// the frame can land after the press and take focus from the control.
const reviewerStepSettled = () =>
  waitFor(() =>
    expect(screen.getByRole('heading', { name: 'Cover reviewer (optional)' })).toHaveFocus(),
  )

it('moves focus to Cancel sign-in after a pressed start (#1607)', async () => {
  let started = false
  let cancelled = false
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/device/cancel')) {
      cancelled = true
      return response({ state: 'cancelled' })
    }
    if (url.endsWith('/device') && init?.method === 'POST') {
      started = true
      return response({ state: 'starting' })
    }
    if (url.endsWith('/device'))
      return response({
        state: 'waiting',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-1234',
      })
    if (isRecheck(url, init)) return recheckAs('ready')
    if (url.endsWith('/reviewer-access'))
      return response(reviewerAccess(cancelled ? 'cancelled' : started ? 'starting' : 'ready'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  await screen.findByRole('heading', { name: 'Cover reviewer (optional)' })
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled(),
  )
  const start = screen.getByRole('button', { name: 'Sign in with ChatGPT' })
  await reviewerStepSettled()
  start.focus()
  fireEvent.click(start)
  const cancel = await screen.findByRole('button', { name: 'Cancel sign-in' })
  await waitFor(() => expect(cancel).toHaveFocus())
  expect(await screen.findByText('ABCD-1234')).toBeVisible()
  fireEvent.click(cancel)
  expect(await screen.findByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled()
  expect(screen.getByRole('heading', { name: 'Cover reviewer (optional)' })).toBeVisible()
  expect(screen.getByText('Not signed in', { selector: '.option p' })).toBeVisible()
})

it.each([
  ['usable access', 'usable', null, null, 'Signed in', null],
  [
    'failed access (#1598 shape)',
    'failed',
    'permission_or_profile',
    null,
    'Not signed in',
    'Your ChatGPT sign-in could not be checked. Sign in with ChatGPT again.',
  ],
  [
    'a failed poll (main shape)',
    'waiting',
    null,
    'protocol_or_process',
    'Not signed in',
    'Codex returned an unexpected sign-in result. Sign in with ChatGPT again.',
  ],
  ['cancelled access', 'cancelled', null, null, 'Not signed in', null],
] as const)(
  'names the Codex status in words, with its remedy, after %s (#1607)',
  async (_case, state, reason, pollFailure, status, remedy) => {
    let reads = 0
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      // The automatic check proves what the recorded state says.
      if (isRecheck(url, init)) return recheckAs(state, reason)
      if (url.endsWith('/device'))
        return response({
          state: 'failed',
          failure: pollFailure,
          verification_url: null,
          user_code: null,
        })
      if (url.endsWith('/reviewer-access')) {
        reads += 1
        // Before #1598 the passive state after a finished flow reads `ready`.
        return response(reviewerAccess(pollFailure && reads > 1 ? 'ready' : state, reason))
      }
      return response(setupState('reviewer'))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()
    // The step draws again once the check or the flow settles.
    await waitFor(() => expect(screen.getByText(status)).toBeVisible())
    if (remedy) await waitFor(() => expect(screen.getByText(remedy)).toBeVisible())
    if (state === 'usable') expect(screen.getByRole('button', { name: 'Use Codex' })).toBeEnabled()
    else
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled(),
      )
    if (remedy) expect(screen.getByText(remedy)).toBeVisible()
    expect(screen.queryAllByText(remedyCopy)).toHaveLength(remedy ? 1 : 0)
    expect(screen.queryByText(/^Status:/u)).toBeNull()
  },
)

const remedyCopy = /Sign in with ChatGPT again\./u

it.each([
  ['failed', 'protocol_or_process'],
  ['interrupted', 'restart_interrupted'],
] as const)(
  'clears the %s remedy once Restart starts a new sign-in (#1607)',
  async (state, reason) => {
    let started = false
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.endsWith('/device') && init?.method === 'POST') {
        started = true
        return response({ state: 'starting' })
      }
      if (url.endsWith('/device'))
        return response({
          state: 'waiting',
          verification_url: 'https://auth.openai.com/codex/device',
          user_code: 'ABCD-1234',
        })
      if (isRecheck(url, init)) return recheckAs(state, reason)
      if (url.endsWith('/reviewer-access'))
        return response(started ? reviewerAccess('starting') : reviewerAccess(state, reason))
      return response(setupState('reviewer'))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled(),
    )
    expect(screen.getByText(remedyCopy)).toBeVisible()
    const restart = screen.getByRole('button', { name: 'Sign in with ChatGPT' })
    await reviewerStepSettled()
    restart.focus()
    fireEvent.click(restart)
    const cancel = await screen.findByRole('button', { name: 'Cancel sign-in' })
    await waitFor(() => expect(cancel).toHaveFocus())
    expect(await screen.findByText('ABCD-1234')).toBeVisible()
    expect(screen.queryByText(remedyCopy)).toBeNull()
  },
)

it('keys the remedy on the shown result when the automatic check reports usable over a failed access (#1607)', async () => {
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/recheck') && init?.method === 'POST')
      return response({ state: 'usable', failure: null, verification_url: null, user_code: null })
    if (url.endsWith('/reviewer-access'))
      return response(reviewerAccess('failed', 'permission_or_profile'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  expect(await screen.findByText('Signed in')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Use Codex' })).toBeEnabled()
  expect(screen.queryByText(remedyCopy)).toBeNull()
})

it.each(['usable access at load', 'a usable automatic check over failed access'] as const)(
  'shows the recorded failure after a refused Use Codex with %s (#1617)',
  async (shape) => {
    let refused = false
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.endsWith('/reviewer/selection') && init?.method === 'POST') {
        refused = true
        return response({ detail: { error: 'credential_unavailable' } }, 409)
      }
      if (url.endsWith('/recheck') && init?.method === 'POST')
        return response({ state: 'usable', failure: null, verification_url: null, user_code: null })
      if (url.endsWith('/reviewer-access'))
        return response(
          shape === 'usable access at load' && !refused
            ? reviewerAccess('usable')
            : reviewerAccess('failed', 'permission_or_profile'),
        )
      return response(setupState('reviewer'))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()
    expect(await screen.findByText('Signed in')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Use Codex' }))

    expect(await screen.findByText('Not signed in')).toBeVisible()
    expect(screen.getByText(remedyCopy)).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'You’re not signed in to ChatGPT. Sign in with ChatGPT, then choose Codex again.',
    )
    // The refusal sits just above the fresh option's first control, and the
    // recorded refusal is not checked again on the same visit.
    expect(screen.getByRole('alert').nextElementSibling).toBe(
      screen.getByRole('button', { name: 'Sign in with ChatGPT' }),
    )
    expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled()
    expect(
      fetch.mock.calls.filter(([request, init]) => isRecheck(requestUrl(request), init)),
    ).toHaveLength(shape === 'usable access at load' ? 0 : 1)
    expect(screen.queryByRole('button', { name: 'Use Codex' })).toBeNull()
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Cover reviewer (optional)' })).toHaveFocus(),
    )
  },
)

it('keeps the read failure when access cannot be read after a refused Use Codex (#1617)', async () => {
  let refused = false
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/reviewer/selection') && init?.method === 'POST') {
      refused = true
      return response({ detail: { error: 'credential_unavailable' } }, 409)
    }
    if (url.endsWith('/reviewer-access'))
      return refused ? response({ error: 'unavailable' }, 503) : response(reviewerAccess('usable'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  fireEvent.click(await screen.findByRole('button', { name: 'Use Codex' }))

  await waitFor(() => expect(screen.queryByRole('button', { name: 'Use Codex' })).toBeNull())
  const alerts = await screen.findAllByRole('alert')
  expect(alerts).toHaveLength(1)
  expect(alerts[0]).not.toHaveTextContent('You’re not signed in to ChatGPT')
  // With no control to sit under, the read failure opens the form (#1618).
  expect(alerts[0]?.parentElement?.firstElementChild).toBe(alerts[0])
})

it.each([
  [
    'access_changed',
    409,
    { detail: { error: 'access_changed' } },
    'Reviewer settings changed elsewhere. Reload, then try again.',
  ],
  ['unavailable', 503, { error: 'unavailable' }, 'That step was not saved.'],
])(
  'shows a failed Use Codex (%s) directly under the button (#1618)',
  async (_, status, body, message) => {
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.endsWith('/reviewer/selection') && init?.method === 'POST')
        return response(body, status)
      if (url.endsWith('/reviewer-access')) return response(reviewerAccess('usable'))
      return response(setupState('reviewer'))
    })
    vi.stubGlobal('fetch', fetch)
    runSetup()
    const use = await screen.findByRole('button', { name: 'Use Codex' })
    await reviewerStepSettled()
    use.focus()
    fireEvent.click(use)

    const alert = await screen.findByRole('alert')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(alert).toHaveTextContent(message)
    expect(alert.previousElementSibling).toBe(use)
    expect(use).toBeEnabled()
    expect(use).toHaveFocus()
    expect(screen.getByText('Signed in')).toBeVisible()
  },
)

it('shows a failed automatic check under Sign in with ChatGPT without moving focus (#1618)', async () => {
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (isRecheck(url, init)) return response({ error: 'unavailable' }, 503)
    if (url.endsWith('/reviewer-access'))
      return response(reviewerAccess('failed', 'permission_or_profile'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  await reviewerStepSettled()

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('That step was not saved.')
  const signIn = screen.getByRole('button', { name: 'Sign in with ChatGPT' })
  expect(alert.previousElementSibling).toBe(signIn)
  expect(signIn).toBeEnabled()
  expect(screen.getByText('Not signed in')).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Cover reviewer (optional)' })).toHaveFocus()
})

it('checks an existing Codex sign-in once without a press', async () => {
  let release: (() => void) | undefined
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (isRecheck(url, init)) {
      await new Promise<void>((resolve) => {
        release = resolve
      })
      return recheckAs('usable')
    }
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('ready'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()

  // While it runs the line says so, and Sign in with ChatGPT waits.
  expect(await screen.findByText('Checking your ChatGPT sign-in…')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeDisabled()
  await waitFor(() => expect(release).toBeDefined())
  release?.()
  expect(await screen.findByText('Signed in', { selector: '.option p' })).toBeVisible()
  await waitFor(() =>
    expect(document.querySelector('#setup-status')).toHaveTextContent('Signed in'),
  )
  expect(screen.getByRole('button', { name: 'Use Codex' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: 'Sign in with ChatGPT' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Check existing sign-in' })).toBeNull()
  expect(
    fetch.mock.calls.filter(([request, init]) => isRecheck(requestUrl(request), init)),
  ).toHaveLength(1)
})

it('shows a failed Cancel sign-in under the button and above the code (#1618)', async () => {
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/device/cancel') && init?.method === 'POST')
      return response({ error: 'unavailable' }, 503)
    if (url.endsWith('/device'))
      return response({
        state: 'waiting',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-1234',
      })
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('waiting'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  expect(await screen.findByText('ABCD-1234')).toBeVisible()
  const cancel = screen.getByRole('button', { name: 'Cancel sign-in' })
  await reviewerStepSettled()
  cancel.focus()
  fireEvent.click(cancel)

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('That step was not saved.')
  expect(alert.previousElementSibling).toBe(cancel)
  expect(alert.nextElementSibling).toBe(document.querySelector('[data-device-prompt]'))
  expect(screen.getByText('ABCD-1234')).toBeVisible()
  expect(cancel).toBeEnabled()
  expect(cancel).toHaveFocus()
})

it('shows a failed device poll under Cancel sign-in without moving focus (#1618)', async () => {
  let polls = 0
  const fetch = vi.fn(async (request: RequestInfo | URL) => {
    const url = requestUrl(request)
    if (url.endsWith('/device')) {
      polls += 1
      return polls > 1
        ? response({ error: 'unavailable' }, 503)
        : response({
            state: 'waiting',
            verification_url: 'https://auth.openai.com/codex/device',
            user_code: 'ABCD-1234',
          })
    }
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('waiting'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  await reviewerStepSettled()

  const alert = await screen.findByRole('alert', {}, { timeout: 3000 })
  expect(alert).toHaveTextContent('That step was not saved.')
  expect(screen.getAllByRole('alert')).toHaveLength(1)
  expect(alert.previousElementSibling).toBe(screen.getByRole('button', { name: 'Cancel sign-in' }))
  expect(screen.getByRole('heading', { name: 'Cover reviewer (optional)' })).toHaveFocus()
})

it('clears a failed Start once the next Start succeeds (#1618)', async () => {
  let starts = 0
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/device') && init?.method === 'POST') {
      starts += 1
      return starts === 1
        ? response({ error: 'unavailable' }, 503)
        : response({ state: 'starting' })
    }
    if (url.endsWith('/device'))
      return response({
        state: 'waiting',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-1234',
      })
    if (isRecheck(url, init)) return recheckAs('ready')
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('ready'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  await screen.findByRole('heading', { name: 'Cover reviewer (optional)' })
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Sign in with ChatGPT' })).toBeEnabled(),
  )
  const start = screen.getByRole('button', { name: 'Sign in with ChatGPT' })
  await reviewerStepSettled()
  start.focus()
  fireEvent.click(start)
  expect((await screen.findByRole('alert')).previousElementSibling).toBe(start)
  await waitFor(() => expect(start).not.toHaveAttribute('aria-disabled'))
  fireEvent.click(start)

  const cancel = await screen.findByRole('button', { name: 'Cancel sign-in' })
  await waitFor(() => expect(cancel).toHaveFocus())
  expect(await screen.findByText('ABCD-1234')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
})

it('restores a reviewer step whose form token rotated at the top of the fresh form (#1618)', async () => {
  let reads = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url.endsWith('/recheck') && init?.method === 'POST')
        return response({ detail: { error: 'csrf' } }, 403)
      if (url.endsWith('/reviewer-access'))
        return response(reviewerAccess('failed', 'permission_or_profile'))
      if (url === '/api/setup/state') reads += 1
      return response(setupState('reviewer', { csrf_token: `token-${String(reads)}` }))
    }),
  )
  runSetup()
  // The automatic check meets a token another tab rotated.
  const check = await screen.findByRole('button', { name: 'Sign in with ChatGPT' })

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent(
    'Setup changed in another tab. The latest recorded step has been restored.',
  )
  expect(alert.parentElement?.tagName).toBe('FORM')
  expect(alert.parentElement?.firstElementChild).toBe(alert)
  expect(check.isConnected).toBe(false)
  expect(reads).toBe(2)
  await reviewerStepSettled()
})

it('keeps an issued code in place across polls and copies exactly the code', async () => {
  const fetch = vi.fn(async (request: RequestInfo | URL) => {
    const url = requestUrl(request)
    if (url.endsWith('/device'))
      return response({
        state: 'issued',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-EFGHJ',
      })
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('issued'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  const link = await screen.findByRole('link', { name: 'Open the device sign-in page' })
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', 'noreferrer')
  const code = screen.getByText('ABCD-EFGHJ')
  expect(code.tagName).toBe('CODE')
  const polls = () =>
    fetch.mock.calls.filter(([request]) => requestUrl(request).endsWith('/device')).length
  const seen = polls()
  await waitFor(() => expect(polls()).toBeGreaterThan(seen), { timeout: 3000 })
  // A poll that returns the same code must not rebuild it under a selection.
  expect(code.isConnected).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Copy code' }))
  expect(await screen.findByText(/Code selected\. Press Ctrl\+C/u)).toBeVisible()
  expect(getSelection()?.toString()).toBe('ABCD-EFGHJ')
})

it('prevents duplicate starts and preserves the reviewer step after session expiry', async () => {
  let release: ((value: Response) => void) | undefined
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url.endsWith('/device') && init?.method === 'POST')
      return new Promise<Response>((resolve) => {
        release = resolve
      })
    if (isRecheck(url, init)) return recheckAs('ready')
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('ready'))
    return response(
      setupState('reviewer', {
        completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers'],
      }),
    )
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  const start = await signInReady()
  await reviewerStepSettled()
  start.focus()
  fireEvent.click(start)
  fireEvent.click(start)
  expect(start).not.toBeDisabled()
  expect(start).toHaveAttribute('aria-disabled', 'true')
  expect(
    fetch.mock.calls.filter(
      ([request, init]) => requestUrl(request).endsWith('/device') && init?.method === 'POST',
    ),
  ).toHaveLength(1)
  release?.(response({ error: 'unauthorized' }, 401))
  expect(await screen.findByRole('alert')).toHaveTextContent('Your sign-in expired')
  expect(screen.getByRole('heading', { name: 'Cover reviewer (optional)' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Change Choose libraries' })).toBeVisible()
  expect(start).toBeEnabled()
})

it.each([
  ['reviewer_unconfigured', 'The chosen reviewer is not ready any more'],
  ['not_provisioned', 'Codex is not set up on this host'],
  ['unreachable', 'OpenRouter could not be reached'],
  ['timed_out', 'OpenRouter did not answer in time. Check network access, then retry.'],
  [
    'malformed_response',
    'OpenRouter’s answer could not be read. Retry later, or choose another model.',
  ],
  [
    'response_too_large',
    'OpenRouter returned an answer too large to read. Retry later, or choose another model.',
  ],
])(
  'gives actionable reviewer recovery for %s without losing prior steps',
  async (code, message) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        if (isRecheck(requestUrl(request), init)) return recheckAs('ready')
        if (init?.method === 'POST') return response({ error: code }, 422)
        return response(
          requestUrl(request).endsWith('/reviewer-access')
            ? reviewerAccess('ready')
            : setupState('reviewer'),
        )
      }),
    )
    runSetup()
    const start = await signInReady()
    await reviewerStepSettled()
    start.focus()
    fireEvent.click(start)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(message)
    // The alert sits directly under the button that was pressed (#1618).
    expect(alert.previousElementSibling).toBe(start)
    expect(start).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Skip for now' })).toBeEnabled()
  },
)

it.each([
  ['Skip for now', 503, 'unavailable', 'That step was not saved.', false],
  ['Continue', 409, 'reviewer_unconfigured', 'The chosen reviewer is not ready any more', true],
] as const)(
  'places a failed %s under that button (#1664)',
  async (name, status, code, copy, mapped) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(request)
        if (url === '/api/setup/reviewer' && init?.method === 'POST')
          return response({ detail: { error: code } }, status)
        if (isRecheck(url, init)) return recheckAs('ready')
        if (url.endsWith('/reviewer-access'))
          return response({
            ...reviewerAccess('ready'),
            mapping: mapped ? { engine: 'openrouter', model: 'openai/gpt-6-luna' } : null,
          })
        return response(setupState('reviewer'))
      }),
    )
    runSetup()
    await signInReady()
    const pressed = screen.getByRole('button', { name })
    await reviewerStepSettled()
    pressed.focus()
    fireEvent.click(pressed)

    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(alert).toHaveTextContent(copy))
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    // After the row that holds Continue and Skip for now.
    const row = alert.previousElementSibling
    expect(row).toHaveClass('actions')
    expect(row).toContainElement(pressed)
    expect(alert.nextElementSibling).toBeNull()
    expect(pressed).toBeEnabled()
    expect(pressed).toHaveFocus()
    expect(screen.getByText(mapped ? 'Selected: GPT-6 Luna' : 'Not signed in')).toBeVisible()
  },
)

it('keeps one alert when a failed Start is followed by a failed Continue (#1664)', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer' && init?.method === 'POST')
        return response({ detail: { error: 'unavailable' } }, 503)
      if (isRecheck(url, init)) return recheckAs('ready')
      if (init?.method === 'POST') return response({ error: 'unreachable' }, 422)
      return response(
        url.endsWith('/reviewer-access') ? reviewerAccess('ready') : setupState('reviewer'),
      )
    }),
  )
  runSetup()
  const start = await signInReady()
  await reviewerStepSettled()
  start.focus()
  fireEvent.click(start)
  const first = await screen.findByRole('alert')
  await waitFor(() => expect(first).toHaveTextContent('OpenRouter could not be reached'))
  expect(first.previousElementSibling).toBe(start)

  const skip = screen.getByRole('button', { name: 'Skip for now' })
  skip.focus()
  fireEvent.click(skip)
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent('That step was not saved.'),
  )
  const alerts = screen.getAllByRole('alert')
  expect(alerts).toHaveLength(1)
  expect(alerts[0]?.previousElementSibling).toContainElement(skip)
})

it('asks for a choice when Continue is pressed with no reviewer chosen', async () => {
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (isRecheck(url, init)) return recheckAs('ready')
    if (url.endsWith('/reviewer-access')) return response(reviewerAccess('ready'))
    return response(setupState('reviewer'))
  })
  vi.stubGlobal('fetch', fetch)
  runSetup()
  await signInReady()
  const next = screen.getByRole('button', { name: 'Continue' })
  expect(next).toHaveClass('primary')
  fireEvent.click(next)

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Choose Codex or OpenRouter, or skip for now.')
  expect(alert.previousElementSibling).toContainElement(next)
  expect(
    fetch.mock.calls.filter(
      ([request, init]) => requestUrl(request) === '/api/setup/reviewer' && init?.method === 'POST',
    ),
  ).toHaveLength(0)
})

it('leaves blocked Codex controls disabled after a failed Continue without a reviewer (#1664)', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer' && init?.method === 'POST')
        return response({ detail: { error: 'unavailable' } }, 503)
      return response(
        url.endsWith('/reviewer-access')
          ? reviewerAccess('not_provisioned', 'profile_not_provisioned')
          : setupState('reviewer'),
      )
    }),
  )
  runSetup()
  const skip = await screen.findByRole('button', { name: 'Skip for now' })
  await reviewerStepSettled()
  const codex = screen.getByRole('heading', { name: 'Codex' }).closest('.option')
  if (!codex) throw new Error('Codex option missing')
  // A blocked host offers no sign-in controls at all.
  expect(within(codex as HTMLElement).queryAllByRole('button')).toHaveLength(0)
  skip.focus()
  fireEvent.click(skip)

  const alert = await screen.findByRole('alert')
  await waitFor(() => expect(alert).toHaveTextContent('That step was not saved.'))
  expect(skip).toBeEnabled()
  // Only what the press disabled is enabled again; the blocked host still
  // offers no sign-in.
  expect(within(codex as HTMLElement).queryAllByRole('button')).toHaveLength(0)
  expect(screen.getByRole('button', { name: 'Check key and use OpenRouter' })).toBeEnabled()
})

// jsdom has no focus fixup, so focus alone cannot fail in these tests; a browser
// drops focus to the page when the focused button becomes natively disabled. The
// mechanism is proved instead: the pressed button stays enabled, is marked
// aria-disabled, and a repeat press sends nothing (#1678).
it.each([
  ['Use Codex', 'usable', null, '/api/setup/reviewer/selection'],
  ['Sign in with ChatGPT', 'ready', null, '/api/setup/reviewer/codex/device'],
  ['Cancel sign-in', 'waiting', null, '/api/setup/reviewer/codex/device/cancel'],
  [
    'Check key and use OpenRouter',
    'not_provisioned',
    'profile_not_provisioned',
    '/api/setup/reviewer/openrouter',
  ],
] as const)('keeps %s focused while it runs (#1678)', async (name, state, reason, path) => {
  let release: ((value: Response) => void) | undefined
  const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(request)
    if (url === path && init?.method === 'POST')
      return new Promise<Response>((resolve) => {
        release = resolve
      })
    if (isRecheck(url, init)) return recheckAs(state, reason)
    if (url === '/api/setup/reviewer/codex/device')
      return response({
        state: 'waiting',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-1234',
      })
    if (url === '/api/setup/reviewer-access') return response(reviewerAccess(state, reason))
    return response(setupState('reviewer'))
  })
  const posts = () =>
    fetch.mock.calls.filter(
      ([request, init]) => requestUrl(request) === path && init?.method === 'POST',
    ).length
  vi.stubGlobal('fetch', fetch)
  runSetup()
  if (name === 'Sign in with ChatGPT') await signInReady()
  const pressed = await screen.findByRole('button', { name })
  await reviewerStepSettled()
  const openRouter = name === 'Check key and use OpenRouter'
  if (openRouter) {
    fireEvent.click(screen.getByRole('button', { name: 'Choose another model' }))
    fireEvent.input(screen.getByRole('textbox', { name: 'Model ID' }), {
      target: { value: 'provider/model' },
    })
    fireEvent.input(screen.getByLabelText('OpenRouter API key'), {
      target: { value: 'synthetic-key' },
    })
  }
  pressed.focus()
  fireEvent.click(pressed)

  expect(pressed).not.toBeDisabled()
  expect(pressed).toHaveAttribute('aria-disabled', 'true')
  expect(pressed).toHaveFocus()
  fireEvent.click(pressed)
  expect(posts()).toBe(1)

  if (openRouter) {
    release?.(response({ detail: { error: 'invalid_key' } }, 400))
    const alert = await screen.findByRole('alert')
    await waitFor(() =>
      expect(alert).toHaveTextContent(
        'OpenRouter rejected this key. Replace it and check it again.',
      ),
    )
    // A field refusal still focuses its field (#1618).
    const key = screen.getByLabelText('OpenRouter API key')
    expect(key).toHaveFocus()
    expect(key).toHaveAttribute('aria-invalid', 'true')
    expect(alert.nextElementSibling).toBe(pressed)
  } else {
    release?.(response({ detail: { error: 'unavailable' } }, 503))
    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(alert).toHaveTextContent('That step was not saved.'))
    expect(pressed.nextElementSibling).toBe(alert)
    expect(pressed).toHaveFocus()
  }
  expect(pressed).not.toHaveAttribute('aria-disabled')
  expect(pressed).toBeEnabled()
  expect(posts()).toBe(1)
})

const discoveredShelf = {
  library_id: 'books-id',
  media_kind: 'book',
  name: 'Reading Shelf',
  service_path: '/discovered/books',
  service_path_state: 'available',
}
const recordedShelf = {
  connection: 'primary',
  display_name: 'Reading Shelf',
  library_id: 'books-id',
  media_kind: 'book',
  name: 'books',
  provider_connections: [],
}

it.each([
  [
    'Continue',
    'providers',
    'Choose metadata sources',
    '/api/setup/providers',
    422,
    'invalid_providers',
    null,
  ],
  ['Continue', 'libraries', 'Choose libraries', '/api/setup/libraries', 502, 'unavailable', null],
  [
    'Check again',
    'mounts',
    'Find library folders',
    '/api/setup/mounts',
    422,
    'invalid_mounts',
    null,
  ],
  [
    'Skip for now',
    'reviewer',
    'Cover reviewer (optional)',
    '/api/setup/reviewer',
    503,
    'unavailable',
    'Sign in with ChatGPT',
  ],
  [
    'Continue',
    'reviewer',
    'Cover reviewer (optional)',
    '/api/setup/reviewer',
    409,
    'reviewer_unconfigured',
    'Sign in with ChatGPT',
  ],
  ['Start', 'kick_off', 'Start', '/api/setup/kick-off', 503, 'activation_failed', null],
] as const)(
  'keeps %s focused while the %s step is recorded (#1678)',
  async (name, step, heading, path, status, code, siblingName) => {
    let release: ((value: Response) => void) | undefined
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (url === path && init?.method === 'POST')
        return new Promise<Response>((resolve) => {
          release = resolve
        })
      if (url.startsWith('/api/setup/libraries?')) return response({ libraries: [discoveredShelf] })
      if (isRecheck(url, init)) return recheckAs('ready')
      if (url === '/api/setup/reviewer-access')
        return response({
          ...reviewerAccess('ready'),
          mapping:
            name === 'Continue' ? { engine: 'openrouter', model: 'openai/gpt-6-luna' } : null,
        })
      return response(
        step === 'mounts'
          ? setupState('mounts', {
              completed_steps: ['claim', 'connect', 'libraries'],
              libraries: [recordedShelf],
              revision: 5,
            })
          : setupState(step),
      )
    })
    const posts = () =>
      fetch.mock.calls.filter(
        ([request, init]) => requestUrl(request) === path && init?.method === 'POST',
      ).length
    vi.stubGlobal('fetch', fetch)
    runSetup()

    if (step === 'reviewer') await signInReady()
    const title = await screen.findByRole('heading', { name: heading })
    await waitFor(() => expect(title).toHaveFocus())
    if (step === 'providers') await untickSources()
    const pressed = await screen.findByRole('button', { name })
    const sibling = siblingName ? screen.getByRole('button', { name: siblingName }) : null
    pressed.focus()
    fireEvent.click(pressed)

    expect(pressed).not.toBeDisabled()
    expect(pressed).toHaveAttribute('aria-disabled', 'true')
    expect(pressed).toHaveFocus()
    // Every other button of the form stays natively disabled while the step is recorded.
    if (sibling) expect(sibling).toBeDisabled()
    fireEvent.click(pressed)
    expect(posts()).toBe(1)

    release?.(response({ detail: { error: code } }, status))
    expect(await screen.findByRole('alert')).toBeVisible()
    await waitFor(() => expect(pressed).not.toHaveAttribute('aria-disabled'))
    expect(pressed).toBeEnabled()
    expect(pressed).toHaveFocus()
    if (sibling) expect(sibling).toBeEnabled()
    expect(posts()).toBe(1)
  },
)

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
  fireEvent.click(await screen.findByRole('button', { name: 'Choose another model' }))
  fireEvent.input(screen.getByRole('textbox', { name: 'Model ID' }), {
    target: { value: 'openai/gpt-6-luna' },
  })
  fireEvent.input(screen.getByLabelText('OpenRouter API key'), {
    target: { value: 'synthetic-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Check key and use OpenRouter' }))
  const rates =
    'Prices in USD: 0.000001 per input token, Unavailable per output token, 0 per image, 0 per request.'
  const prices = await screen.findByText(rates)
  // The rates are reference detail, inside the closed Prices and data use.
  const about = prices.closest('details')
  if (!about) throw new Error('reviewer detail disclosure missing')
  expect(about).not.toHaveAttribute('open')
  about.open = true
  expect(prices).toBeVisible()
  expect(screen.getByText('GPT-6 Luna: cost not measured.')).toBeVisible()
  expect(screen.getByText('Selected: GPT-6 Luna')).toBeVisible()
  await waitFor(() =>
    expect(document.querySelector('#setup-status')?.textContent).toBe('OpenRouter selected.'),
  )
  expect(advanced).toBe(false)
  expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
  resetSetupDocument()
  runSetup()
  expect(await screen.findByText(rates)).toBeInTheDocument()
  expect(screen.getByText('Selected: GPT-6 Luna')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  await waitFor(() => expect(advanced).toBe(true))
})

it.each([
  [
    'invalid_key',
    'OpenRouter API key',
    'OpenRouter rejected this key. Replace it and check it again.',
  ],
  ['model_unavailable', 'Model ID', 'That model is unavailable. Choose another model.'],
])(
  'places a refused OpenRouter validation (%s) beside the submit button and keeps focus on its field',
  async (code, fieldName, message) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: RequestInfo | URL) => {
        const url = requestUrl(request)
        if (url === '/api/setup/reviewer-access')
          return response(reviewerAccess('not_provisioned', 'profile_not_provisioned'))
        if (url === '/api/setup/reviewer/openrouter')
          return response({ detail: { error: code } }, 422)
        return response(setupState('reviewer'))
      }),
    )
    runSetup()
    fireEvent.click(await screen.findByRole('button', { name: 'Choose another model' }))
    fireEvent.input(screen.getByRole('textbox', { name: 'Model ID' }), {
      target: { value: 'provider/model' },
    })
    fireEvent.input(screen.getByLabelText('OpenRouter API key'), {
      target: { value: 'synthetic-key' },
    })
    const save = screen.getByRole('button', { name: 'Check key and use OpenRouter' })
    fireEvent.click(save)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(message)
    const keyField = screen.getByLabelText('OpenRouter API key')
    // The alert sits between the key field (with its Get a key link) and the
    // button that was pressed, not at the top of a form that runs far above a
    // phone viewport.
    const getKey = screen.getByRole('link', { name: 'Get a key' }).parentElement
    expect(getKey?.previousElementSibling).toBe(keyField.closest('.field'))
    expect(alert.previousElementSibling).toBe(getKey)
    expect(alert.nextElementSibling).toBe(save)
    const field = screen.getByLabelText(fieldName)
    expect(field).toHaveFocus()
    expect(document.activeElement).not.toBe(document.body)
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field.getAttribute('aria-describedby')).toContain('setup-error')
    expect(keyField).toHaveValue('')
  },
)

it('opens Choose another model to show a refused model from the closed state', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL) => {
      const url = requestUrl(request)
      if (url === '/api/setup/reviewer-access')
        return response(reviewerAccess('not_provisioned', 'profile_not_provisioned'))
      if (url === '/api/setup/reviewer/openrouter')
        return response({ detail: { error: 'model_text_only' } }, 422)
      return response(setupState('reviewer'))
    }),
  )
  runSetup()
  const toggle = await screen.findByRole('button', { name: 'Choose another model' })
  await reviewerStepSettled()
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  fireEvent.input(screen.getByLabelText('OpenRouter API key'), {
    target: { value: 'synthetic-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Check key and use OpenRouter' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'That model cannot inspect covers. Choose a model with image input.',
  )
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const model = screen.getByRole('textbox', { name: 'Model ID' })
  expect(model).toBeVisible()
  expect(model).toHaveAttribute('aria-invalid', 'true')
  expect(model).toHaveFocus()
})

const setupLedger = () => {
  const ledger = document.querySelector<HTMLElement>('#setup-ledger')
  if (!ledger) throw new Error('setup ledger missing')
  return ledger
}
const ledgerLabels = () =>
  [...document.querySelectorAll('#setup-ledger li')].map(
    (row) => row.querySelectorAll('span')[1]?.firstChild?.textContent,
  )

it('orders the steps with libraries before sources and hides the folder step until a folder is not found', async () => {
  let current: ReturnType<typeof setupState> = setupState('libraries')
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response(current)),
  )
  runSetup()
  await screen.findByRole('heading', { name: 'Choose libraries' })
  expect(ledgerLabels()).toEqual([
    'Create your password',
    'Connect Audiobookshelf',
    'Choose libraries',
    'Choose metadata sources',
    'Cover reviewer (optional)',
    'Start',
  ])

  current = setupState('mounts', {
    completed_steps: ['claim', 'connect', 'libraries'],
    libraries: [
      { library_id: 'books-id', media_kind: 'book', name: 'books', display_name: 'Books' },
    ],
    mounts: [{ library: 'books', state: 'missing' }],
  })
  resetSetupDocument()
  runSetup()
  await screen.findByRole('heading', { name: 'Find library folders' })
  expect(ledgerLabels()).toEqual([
    'Create your password',
    'Connect Audiobookshelf',
    'Choose libraries',
    'Find library folders',
    'Choose metadata sources',
    'Cover reviewer (optional)',
    'Start',
  ])
  const folders = within(setupLedger()).getByText('Find library folders').closest('li')
  expect(folders).toHaveAttribute('aria-current', 'step')

  // Once every folder is found, the step leaves the list again.
  current = setupState('providers', {
    completed_steps: ['claim', 'connect', 'libraries', 'mounts'],
    libraries: [
      { library_id: 'books-id', media_kind: 'book', name: 'books', display_name: 'Books' },
    ],
    mounts: [{ library: 'books', state: 'matched' }],
  })
  resetSetupDocument()
  runSetup()
  await screen.findByRole('heading', { name: 'Choose metadata sources' })
  expect(ledgerLabels()).not.toContain('Find library folders')
  expect(screen.queryByRole('button', { name: 'Change Find library folders' })).toBeNull()
})

it('says each step’s state in words and hides its mark from assistive tech', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response(setupState('libraries', { completed_steps: ['claim', 'connect'] }))),
  )
  runSetup()
  await screen.findByRole('heading', { name: 'Choose libraries' })
  const rows = [...setupLedger().querySelectorAll('li')]
  for (const row of rows) expect(row.querySelector('span')).toHaveAttribute('aria-hidden', 'true')
  const said = rows.map((row) => row.querySelector('.sr-only')?.textContent ?? null)
  expect(said).toEqual([' Done', ' Done', ' Current step', null, null, null])
  expect(rows[0]).toHaveTextContent('Create your password Done')
  expect(rows[2]).toHaveTextContent('Choose libraries Current step')
  expect(rows[3]).toHaveTextContent(/^·Choose metadata sources$/u)
})

it.each([
  ['http://192.168.1.10:13378', false],
  ['http://10.0.0.5:13378', false],
  ['http://100.64.1.2:13378', false],
  ['http://localhost:13378', false],
  ['http://nas.local:13378', false],
  ['http://abs:13378', false],
  ['http://abs.example.com:13378', true],
  ['http://8.8.8.8:13378', true],
  ['https://abs.example.com', false],
  ['https://8.8.8.8:13378', false],
] as const)(
  'warns about clear text only for an http address outside a private network: %s',
  async (address, warned) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        response(
          setupState('connect', { completed_steps: ['claim'], connection: null, revision: 2 }),
        ),
      ),
    )
    runSetup()

    const endpoint = await screen.findByLabelText('Audiobookshelf address')
    const warning = document.querySelector<HTMLElement>('#setup-endpoint-warning')
    if (!warning) throw new Error('clear-text warning missing')
    expect(warning).not.toBeVisible()
    fireEvent.input(endpoint, { target: { value: address } })
    if (warned) {
      expect(warning).toBeVisible()
      expect(warning).toHaveTextContent(
        'This address is outside your private network, so the API key would be sent unencrypted. Use an https:// address.',
      )
      expect(endpoint.getAttribute('aria-describedby')).toContain('setup-endpoint-warning')
    } else {
      expect(warning).not.toBeVisible()
      expect(endpoint.getAttribute('aria-describedby') ?? '').not.toContain(
        'setup-endpoint-warning',
      )
    }
  },
)

it('resumes at the folder step with only the not-found libraries', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL) => {
      if (requestUrl(request).startsWith('/api/setup/libraries?'))
        return response({ libraries: [] })
      return response(
        setupState('mounts', {
          completed_steps: ['claim', 'connect', 'libraries'],
          libraries: [
            { library_id: 'books-id', media_kind: 'book', name: 'books', display_name: 'Books' },
            {
              library_id: 'audio-id',
              media_kind: 'book',
              name: 'audio',
              display_name: 'Listening',
            },
          ],
          mounts: [
            { library: 'books', state: 'matched' },
            { library: 'audio', state: 'missing' },
          ],
          revision: 4,
        }),
      )
    }),
  )
  runSetup()

  const heading = await screen.findByRole('heading', { name: 'Find library folders' })
  await waitFor(() => expect(heading).toHaveFocus())
  expect(screen.getByRole('textbox', { name: 'Listening folder in Audiobookshelf' })).toBeVisible()
  expect(screen.queryByRole('textbox', { name: 'Books folder in Audiobookshelf' })).toBeNull()
  expect(document.querySelectorAll('.mount-line')).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Change Choose libraries' })).toBeVisible()
})

it('resumes at the reviewer step after sources', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(request)
      if (isRecheck(url, init)) return recheckAs('ready')
      if (url.endsWith('/reviewer-access')) return response(reviewerAccess('ready'))
      return response(
        setupState('reviewer', {
          completed_steps: ['claim', 'connect', 'libraries', 'mounts', 'providers'],
          libraries: [
            { library_id: 'books-id', media_kind: 'book', name: 'books', display_name: 'Books' },
          ],
          mounts: [{ library: 'books', state: 'matched' }],
          revision: 6,
        }),
      )
    }),
  )
  runSetup()

  await signInReady()
  await reviewerStepSettled()
  const ledger = setupLedger()
  expect(within(ledger).getByText('Cover reviewer (optional)').closest('li')).toHaveAttribute(
    'aria-current',
    'step',
  )
  for (const step of ['Connect Audiobookshelf', 'Choose libraries', 'Choose metadata sources'])
    expect(within(ledger).getByRole('button', { name: `Change ${step}` })).toBeVisible()
  expect(ledgerLabels()).not.toContain('Find library folders')
})

it('shows no plain-HTTP notice on a page served over HTTPS', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: RequestInfo | URL) => {
      const url = requestUrl(request)
      if (url === '/api/setup/claim')
        return response({ claimed: false, nonce: 'n'.repeat(32), revision: 1 })
      return response({ error: 'unauthorized' }, 401)
    }),
  )
  runSetup(stubLocation('https:'))

  await screen.findByRole('heading', { name: 'Create your password' })
  expect(screen.getByText('At least 12 characters.')).toBeVisible()
  expect(screen.queryByText(/plain HTTP/u)).toBeNull()
})
