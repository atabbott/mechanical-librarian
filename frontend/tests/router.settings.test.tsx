import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsLocalLibraryNameValid,
  settingsProviderKeyRequired,
  settingsSecretValid,
  settingsTextValid,
} from '../src/api/http'
import type { ReviewerAccessView, SettingsView, SettingsWrite } from '../src/api/http'
import { createHttp, renderRoute } from './fixtures'

const document: SettingsView['document'] = {
  schema_version: 'owner-settings-v2',
  revision: 3,
  connections: [{ adapter: 'audiobookshelf', endpoint: 'https://library.invalid', name: 'home' }],
  providers: [
    { adapter: 'hardcover', cover_enabled: true, enabled: true, name: 'hardcover' },
    {
      adapter: 'open_library',
      contact_email: 'owner@example.invalid',
      cover_enabled: true,
      enabled: true,
      name: 'open-library',
    },
  ],
  libraries: [
    {
      connection: 'home',
      library_id: 'books',
      media_kind: 'ebook',
      mount: 'ebooks',
      name: 'a-very_long-library-name',
      display_name: 'Ebooks & Audiobooks: Collected Editions',
      policy_override: null,
      provider_connections: ['hardcover'],
    },
  ],
  limits: {
    provider_response_bytes: 65_536,
    source_archive_bytes: 268_435_456,
    target_response_bytes: 1_048_576,
    timeout_seconds: 30,
  },
  pending_restart: [],
  provider_recovery_generation: 1,
  reduced_motion: 'follow_system',
  server: {
    diagnostic_timings_enabled: false,
    graceful_shutdown_seconds: 30,
    library_polling_interval_seconds: 900,
    session_lifetime_seconds: 2_592_000,
  },
  cover_preferences: {
    avoid_promotional_text: false,
    avoid_tie_in_branding: false,
    prefer_minimal_composition: false,
  },
}

const view = (authority: 'file' | 'store' = 'store'): SettingsView => ({
  authority,
  document,
  fields: [
    {
      apply_effect: 'applied_live',
      field_id: 'connections',
      group: 'connections',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
    },
    {
      apply_effect: 'applied_live',
      field_id: 'providers',
      group: 'providers',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
    },
    {
      apply_effect: 'applied_live',
      field_id: 'reduced_motion',
      group: 'appearance',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
    },
  ],
  managed_path: authority === 'file' ? '/data/config.json' : null,
  provider_capabilities: [],
  secrets: [
    {
      configured: true,
      field_id: 'connection.home.key',
      indicator: 'Configured · ····home',
    },
    { configured: true, field_id: 'provider.hardcover.key', indicator: 'Configured · ····abcd' },
  ],
})

const reviewerView: ReviewerAccessView = {
  schema_version: 'reviewer-access-view-v1',
  revision: 3,
  mapping: null,
  openrouter_key_configured: false,
  codex: {
    engine: 'codex',
    state: 'failed',
    reason: 'permission_or_profile',
    model: null,
    tier: null,
    host_headroom_bytes: 2_147_483_648,
  },
  openrouter: {
    engine: 'openrouter',
    state: 'unconfigured',
    reason: null,
    model: null,
    tier: null,
    host_headroom_bytes: null,
  },
  recommendations: [],
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
  free_tier_caveat: '20 requests/minute; 50 or 1000 requests/day by account tier.',
  data_use_caveat: 'Cover previews are sent to OpenRouter and its selected provider.',
}

describe('settings API field validation vocabulary', () => {
  it('matches the generated boundary at exact and unusual values', () => {
    expect(settingsEndpointValid('not a URL but API-valid')).toBe(true)
    expect(settingsEndpointValid('x'.repeat(2048))).toBe(true)
    expect(settingsEndpointValid('😀'.repeat(2048))).toBe(true)
    expect(settingsEndpointValid('')).toBe(false)
    expect(settingsEndpointValid('x'.repeat(2049))).toBe(false)
    expect(settingsEndpointValid('😀'.repeat(2049))).toBe(false)
    expect(settingsIntegerValid(60, 1, 60)).toBe(true)
    expect(settingsIntegerValid(61, 1, 60)).toBe(false)
    expect(settingsTextValid('x', 1, 128)).toBe(true)
    expect(settingsTextValid('', 1, 128)).toBe(false)
    expect(settingsSecretValid('printable-secret')).toBe(true)
    expect(settingsSecretValid('Bearer secret')).toBe(false)
    expect(settingsOpenLibraryEmailValid('owner@example.invalid')).toBe(true)
    expect(settingsOpenLibraryEmailValid('not-an-email')).toBe(false)
    expect(settingsLocalLibraryNameValid('books_1')).toBe(true)
    expect(settingsLocalLibraryNameValid('Not valid')).toBe(false)
    expect(settingsProviderKeyRequired('hardcover')).toBe(true)
    expect(settingsProviderKeyRequired('google_books')).toBe(false)
  })
})

describe('owner settings route', () => {
  it('keeps absent supported providers configurable from an empty document', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => ({
      ...view(),
      document: { ...document, providers: [] },
      provider_capabilities: [
        {
          adapter: 'open_library' as const,
          credential: 'contact_email' as const,
          label: 'Open Library',
          name: 'open_library',
        },
      ],
    }))
    renderRoute('/settings?group=providers', http)

    const add = await screen.findByRole('button', { name: 'Add Open Library' })
    act(() => {
      add.click()
      add.click()
    })
    expect(
      screen.getByRole('checkbox', { name: 'Provider state Enable open library' }),
    ).toBeVisible()
    expect(
      screen.getAllByRole('checkbox', { name: 'Provider state Enable open library' }),
    ).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Open Library contact email' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Add Open Library' })).not.toBeInTheDocument()
  })

  it('keeps the primary shell intact and saves a redacted replacement with revision CAS', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async () => ({ ...view(), document: { ...document, revision: 4 } }))
    renderRoute('/settings', http)

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    for (const name of ['Library', 'Policy', 'Activity'])
      expect(screen.getByRole('link', { name })).toBeVisible()
    expect(screen.queryByText('Restart required for saved changes')).not.toBeInTheDocument()
    expect(screen.queryByText('Applies on restart')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Providers/ }))
    expect(screen.getByRole('checkbox', { name: 'Provider state Enable hardcover' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Get a Hardcover key' })).toBeVisible()
    expect(screen.getByText('Configured · ····abcd')).toBeVisible()
    expect(
      screen.getByRole('textbox', { name: 'Open Library contact email' }),
    ).toHaveAccessibleDescription(
      /Sent only to Open Library; never shared with this project or retained in reports/,
    )
    expect(screen.queryByText('Cover evidence')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /cover evidence/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replace hardcover API key' }))
    await user.type(screen.getByLabelText('Replacement hardcover API key'), 'new-secret-abcd')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(http.writeSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 3,
        secrets: [
          { field_id: 'provider.hardcover.key', operation: 'replace', value: 'new-secret-abcd' },
        ],
      }),
    )
    expect(screen.queryByDisplayValue('new-secret-abcd')).not.toBeInTheDocument()
    expect(await screen.findByText('Settings saved and in use.')).toBeVisible()
  })

  it('disables providers without clearing credentials and never restores associations', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let savedRequest: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      savedRequest = request
      return { ...view(), document: request.document }
    })
    renderRoute('/settings?group=providers', http)

    const hardcover = await screen.findByRole('checkbox', {
      name: 'Provider state Enable hardcover',
    })
    await user.click(hardcover)
    expect(screen.getByText('Configured · ····abcd')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Replace hardcover API key' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clear hardcover API key' })).toBeVisible()

    const openLibrary = screen.getByRole('checkbox', {
      name: 'Provider state Enable open-library',
    })
    await user.click(openLibrary)
    expect(screen.getByRole('textbox', { name: 'Open Library contact email' })).toHaveValue(
      'owner@example.invalid',
    )

    await user.click(hardcover)
    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    expect(screen.getByRole('checkbox', { name: /^hardcover$/u })).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(savedRequest?.secrets).toEqual([])
    expect(savedRequest?.document.libraries[0]?.provider_connections).toEqual([])
    expect(savedRequest?.document.providers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          contact_email: 'owner@example.invalid',
          enabled: false,
          name: 'open-library',
        }),
        expect.objectContaining({ enabled: true, name: 'hardcover' }),
      ]),
    )
  })

  it('renders file authority without covered edit affordances in the DOM', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    renderRoute('/settings', http)
    expect(await screen.findByText('Managed by /data/config.json')).toBeVisible()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Replace|Clear/ })).not.toBeInTheDocument()
    for (const name of [
      /Providers/,
      /Libraries/,
      /Automation & limits/,
      /Reviewer/,
      /Server/,
      /Appearance/,
    ]) {
      await user.click(screen.getByRole('button', { name }))
      expect(window.document.querySelector('.settings-sheet input')).toBeNull()
      expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
    }
  })

  it('owns group navigation in the URL and restores it with browser history', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const { router } = renderRoute('/settings?group=connection', http)
    await screen.findByRole('heading', { level: 2, name: 'Connection' })
    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Libraries' })).toBeVisible()
    expect(router.state.location.search).toEqual({ group: 'libraries' })
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Appearance' })).toBeVisible()
    act(() => router.history.back())
    expect(await screen.findByRole('heading', { level: 2, name: 'Libraries' })).toBeVisible()
  })

  it('uses a human library heading while keeping exact identifiers secondary', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=libraries', http)

    expect(
      await screen.findByRole('heading', {
        level: 3,
        name: 'Ebooks & Audiobooks: Collected Editions',
      }),
    ).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'a-very_long-library-name' })).toBeNull()
    const metadata = screen.getByText('a-very_long-library-name').closest('p')
    expect(metadata).toHaveClass('settings-record-meta')
    expect(metadata).toHaveTextContent('Local identifier: a-very_long-library-name')
    expect(metadata).toHaveTextContent('Audiobookshelf identifier: books')
    expect(
      screen.getByRole('group', {
        name: 'Providers for Ebooks & Audiobooks: Collected Editions',
      }),
    ).toBeVisible()
  })

  it('falls back to the exact stable library name without reconstructing punctuation', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => ({
      ...view(),
      document: {
        ...document,
        libraries: document.libraries.map((library) => {
          const { display_name: omittedDisplayName, ...legacyLibrary } = library
          void omittedDisplayName
          return legacyLibrary
        }),
      },
    }))
    renderRoute('/settings?group=libraries', http)

    expect(
      await screen.findByRole('heading', { level: 3, name: 'a-very_long-library-name' }),
    ).toBeVisible()
    expect(screen.queryByText('A Very Long Library Name')).not.toBeInTheDocument()
  })

  it('announces unavailable and stale recovery states without leaking a value', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, 'settings_unavailable'))
      .mockResolvedValue(view())
    http.writeSettings = vi.fn(async () => {
      throw new ApiError(409, 'settings_revision_stale')
    })
    renderRoute('/settings', http)
    expect(await screen.findByRole('alert')).toHaveTextContent('could not safely read')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    await user.clear(screen.getByDisplayValue('https://library.invalid'))
    await user.type(screen.getByRole('textbox'), 'https://other.invalid')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    const staleAlert = await screen.findByRole('alert')
    expect(staleAlert).toHaveTextContent('Settings changed elsewhere')
    await waitFor(() => expect(staleAlert).toHaveFocus())
    expect(screen.queryByText('https://other.invalid')).not.toBeInTheDocument()
  })

  it.each([new AmbiguousMutationError(), new ApiError(502, 'invalid_settings_response')])(
    'reconciles an uncertain save before confirming authoritative state',
    async (failure) => {
      const user = userEvent.setup()
      const http = createHttp()
      const reconciled: SettingsView = {
        ...view(),
        document: {
          ...document,
          revision: 4,
          connections: [
            { adapter: 'audiobookshelf', endpoint: 'https://authoritative.invalid', name: 'home' },
          ],
        },
      }
      http.getSettings = vi.fn().mockResolvedValueOnce(view()).mockResolvedValueOnce(reconciled)
      http.writeSettings = vi.fn(async () => {
        throw failure
      })
      renderRoute('/settings?group=connection', http)
      const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
      await waitFor(() =>
        expect(globalThis.document.getElementById('main-content')).toHaveAttribute(
          'data-restored-path',
          '/settings',
        ),
      )
      await user.click(endpoint)
      await user.clear(endpoint)
      await user.type(endpoint, 'https://submitted.invalid')
      await user.click(screen.getByRole('button', { name: 'Save settings' }))

      const recovered = await screen.findByText(
        'Current settings reloaded. Review the values before saving again.',
      )
      expect(recovered.closest('[role="alert"]')).toHaveFocus()
      expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveValue(
        'https://authoritative.invalid',
      )
      expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
      expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument()
      expect(http.getSettings).toHaveBeenCalledTimes(2)
      expect(http.writeSettings).toHaveBeenCalledOnce()
    },
  )

  it('blocks repeat mutation when uncertain reconciliation cannot reload', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi
      .fn()
      .mockResolvedValueOnce(view())
      .mockRejectedValue(new ApiError(503, 'settings_unavailable'))
    http.writeSettings = vi.fn(async () => {
      throw new ApiError(503, 'settings_unavailable')
    })
    renderRoute('/settings?group=connection', http)
    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await waitFor(() =>
      expect(globalThis.document.getElementById('main-content')).toHaveAttribute(
        'data-restored-path',
        '/settings',
      ),
    )
    await user.clear(endpoint)
    await user.type(endpoint, 'https://submitted.invalid')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    const uncertain = await screen.findByText(/Save outcome is uncertain and Settings could not/)
    expect(uncertain.closest('[role="alert"]')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry settings reload' })).toBeVisible()
    expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument()
    expect(http.writeSettings).toHaveBeenCalledOnce()
  })

  it('associates every editable validation family with its named field', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=connection', http)

    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await waitFor(() =>
      expect(globalThis.document.getElementById('main-content')).toHaveAttribute(
        'data-restored-path',
        '/settings',
      ),
    )
    await user.click(endpoint)
    await user.clear(endpoint)
    expect(endpoint).toHaveFocus()
    expect(endpoint).toHaveAttribute('aria-invalid', 'true')
    expect(endpoint).toHaveAccessibleDescription(/endpoint must contain 1–2,048 characters/)
    expect(screen.getByRole('alert')).toHaveTextContent('endpoint must contain')

    await user.click(screen.getByRole('button', { name: 'Replace home access key' }))
    const targetSecret = screen.getByLabelText('Replacement home access key')
    await user.type(targetSecret, 'Bearer secret')
    expect(targetSecret).toHaveAttribute('aria-invalid', 'true')
    expect(targetSecret).toHaveAccessibleDescription(/Secrets must be 1–2,048/)

    await user.click(screen.getByRole('button', { name: /Providers/ }))
    const email = screen.getByRole('textbox', { name: 'Open Library contact email' })
    await user.clear(email)
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription(/needs a contact email while enabled/)
    const openLibraryEnabled = screen.getByRole('checkbox', {
      name: 'Provider state Enable open-library',
    })
    await user.click(openLibraryEnabled)
    expect(email).not.toHaveAttribute('aria-invalid')
    await user.click(openLibraryEnabled)
    expect(email).toHaveAttribute('aria-invalid', 'true')

    await user.click(screen.getByRole('button', { name: 'Clear hardcover API key' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear hardcover API key' }))
    const requiredKey = screen.getByRole('button', {
      name: 'Keep configured hardcover API key',
    })
    expect(requiredKey).toHaveAttribute('aria-invalid', 'true')
    expect(requiredKey).toHaveAccessibleDescription(/requires an API key while enabled/)

    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    const libraryId = screen.getByRole('textbox', { name: 'Library identifier' })
    await user.clear(libraryId)
    expect(libraryId).toHaveAttribute('aria-invalid', 'true')
    expect(libraryId).toHaveAccessibleDescription(/identifier must contain 1–128 characters/)

    await user.click(screen.getByRole('button', { name: /Automation & limits/ }))
    const timeout = screen.getByRole('spinbutton', { name: 'Request timeout' })
    await user.clear(timeout)
    await user.type(timeout, '0')
    expect(timeout).toHaveAttribute('aria-invalid', 'true')
    expect(timeout).toHaveAccessibleDescription(/Request timeout must be from 1 to 60/)
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
  })

  it('keeps reviewer access owner-managed under file settings and labels candidates honestly', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    http.configureOpenRouter = vi.fn(async () => ({
      ...reviewerView,
      revision: 4,
      mapping: {
        engine: 'openrouter' as const,
        model: 'dots-studio/dots-3-note-preview:free',
      },
      openrouter_key_configured: true,
      openrouter: {
        ...reviewerView.openrouter,
        state: 'usable' as const,
        model: 'dots-studio/dots-3-note-preview:free',
        tier: 'free' as const,
      },
    }))
    renderRoute('/settings?group=reviewer', http)

    expect(await screen.findByText(/No model recommendation is qualified/u)).toBeVisible()
    expect(screen.getByText(/20 requests\/minute/u)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toBeVisible()
    await user.click(
      screen.getByRole('button', {
        name: 'Use dots-studio/dots-3-note-preview:free',
      }),
    )
    const model = screen.getByRole('textbox', { name: 'Model ID' })
    expect(model).toHaveValue('dots-studio/dots-3-note-preview:free')
    const key = screen.getByLabelText('API key')
    await user.type(key, 'secret-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
    await waitFor(() =>
      expect(http.configureOpenRouter).toHaveBeenCalledWith({
        expected_revision: 3,
        api_key: 'secret-key',
        model: 'dots-studio/dots-3-note-preview:free',
      }),
    )
    expect(key).toHaveValue('')
    expect(await screen.findByText('Status: usable')).toBeVisible()
  })

  it.each([
    ['usable', null],
    ['expired', null],
    ['cancelled', null],
    ['interrupted', 'restart_interrupted'],
    ['failed', 'permission_or_profile'],
  ] as const)('retains explicit Codex check terminal state %s', async (state, failure) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      codex: { ...reviewerView.codex, state: 'configured' as const, reason: null },
    }))
    http.recheckCodex = vi.fn(async () => ({
      state,
      failure,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }))
    renderRoute('/settings?group=reviewer', http)

    await user.click(await screen.findByRole('button', { name: 'Check existing Codex sign-in' }))
    expect(await screen.findByText(`Status: ${state}`)).toBeVisible()
    expect(http.recheckCodex).toHaveBeenCalledOnce()
    if (failure) expect(screen.getByRole('alert')).toBeVisible()
  })

  it('announces OpenRouter validation before retaining its terminal result', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let finish!: (value: ReviewerAccessView) => void
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    http.configureOpenRouter = vi.fn(
      async () => await new Promise<ReviewerAccessView>((resolve) => (finish = resolve)),
    )
    renderRoute('/settings?group=reviewer', http)
    await user.click(
      await screen.findByRole('button', {
        name: 'Use dots-studio/dots-3-note-preview:free',
      }),
    )
    await user.type(screen.getByLabelText('API key'), 'secret-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
    expect(screen.getByText('Status: validating')).toBeVisible()
    finish({
      ...reviewerView,
      revision: 4,
      mapping: {
        engine: 'openrouter',
        model: 'dots-studio/dots-3-note-preview:free',
      },
      openrouter_key_configured: true,
      openrouter: {
        ...reviewerView.openrouter,
        state: 'usable',
        model: 'dots-studio/dots-3-note-preview:free',
        tier: 'free',
      },
    })
    expect(await screen.findByText('Status: usable')).toBeVisible()
  })

  it('uses the replacement deselection revision before Codex reselection', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const selected = {
      ...reviewerView,
      mapping: { engine: 'codex' as const },
      codex: { ...reviewerView.codex, state: 'usable' as const, reason: null },
    }
    const deselected = {
      ...selected,
      revision: 4,
      mapping: null,
      codex: { ...selected.codex, state: 'ready' as const },
    }
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => selected)
    http.startCodexDevice = vi.fn(async () => ({
      access: deselected,
      device: {
        state: 'waiting' as const,
        failure: null,
        verification_url: null,
        user_code: null,
        expires_in_seconds: null,
      },
    }))
    http.getCodexDevice = vi.fn(async () => ({
      state: 'usable' as const,
      failure: null,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }))
    http.selectReviewer = vi.fn(async () => ({ ...selected, revision: 5 }))
    renderRoute('/settings?group=reviewer', http)

    await user.click(await screen.findByRole('button', { name: 'Replace Codex sign-in' }))
    await user.click(screen.getByRole('button', { name: 'Confirm replace Codex sign-in' }))
    expect(await screen.findByText(/Current selection:/u)).toHaveTextContent('No reviewer')
    expect(await screen.findByText('Status: usable')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Use Codex for cover observations' }))
    expect(http.selectReviewer).toHaveBeenCalledWith('codex', 4)
  })

  it.each([
    ['ready', null, false, null],
    [
      'failed',
      'permission_or_profile',
      false,
      'The installed Codex sign-in could not be validated. Replace the sign-in and retry.',
    ],
    ['expired', null, false, null],
    [
      'interrupted',
      'restart_interrupted',
      false,
      'A prior sign-in was interrupted. Restart the sign-in flow.',
    ],
    [
      'host_unsupported',
      'host_evidence_unavailable',
      true,
      'Host memory evidence is unavailable. Fix the host or container memory reporting before using Codex.',
    ],
    [
      'host_unsupported',
      'cgroup_limit_below_1_gib',
      true,
      'The container memory limit is below 1 GiB. Increase it before using Codex.',
    ],
    [
      'host_unsupported',
      'headroom_below_1_gib',
      true,
      'Less than 1 GiB of memory headroom is available. Free memory or raise the container limit.',
    ],
  ] as const)(
    'keeps mapped unhealthy Codex state %s manageable',
    async (state, reason, hostUnsupported, expectedReason) => {
      const user = userEvent.setup()
      const http = createHttp()
      http.getSettings = vi.fn(async () => view())
      http.getReviewerAccess = vi.fn(async () => ({
        ...reviewerView,
        mapping: { engine: 'codex' as const },
        codex: { ...reviewerView.codex, state, reason },
      }))
      renderRoute('/settings?group=reviewer', http)

      const replace = await screen.findByRole('button', { name: 'Replace Codex sign-in' })
      const clear = screen.getByRole('button', { name: 'Clear Codex sign-in' })
      const check = screen.getByRole('button', { name: 'Check existing Codex sign-in' })
      if (hostUnsupported) {
        expect(replace).toBeDisabled()
        expect(check).toBeDisabled()
      } else {
        expect(replace).toBeEnabled()
        expect(check).toBeEnabled()
      }
      expect(clear).toBeEnabled()
      expect(
        screen.getByRole('button', { name: 'Use Codex for cover observations' }),
      ).toBeDisabled()
      if (expectedReason) {
        expect(screen.getAllByRole('alert')).toHaveLength(1)
        expect(screen.getByRole('alert')).toHaveTextContent(expectedReason)
      } else {
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      }

      await user.click(clear)
      expect(screen.getByRole('button', { name: 'Confirm clear Codex sign-in' })).toHaveFocus()
    },
  )

  it('shows one newer device failure instead of the persisted Codex reason', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      mapping: { engine: 'codex' as const },
      codex: {
        ...reviewerView.codex,
        state: 'interrupted' as const,
        reason: 'restart_interrupted' as const,
      },
    }))
    http.recheckCodex = vi.fn(async () => ({
      state: 'failed' as const,
      failure: 'permission_or_profile' as const,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }))
    renderRoute('/settings?group=reviewer', http)

    await user.click(await screen.findByRole('button', { name: 'Check existing Codex sign-in' }))

    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The installed Codex sign-in could not be validated. Replace the sign-in and retry.',
    )
    expect(screen.queryByText(/prior sign-in was interrupted/u)).not.toBeInTheDocument()
  })

  it('keeps current host failure copy ahead of an older cached device failure', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      mapping: { engine: 'codex' as const },
      codex: {
        ...reviewerView.codex,
        state: 'host_unsupported' as const,
        reason: 'headroom_below_1_gib' as const,
      },
    }))
    const { queryClient } = renderRoute('/settings?group=reviewer', http)
    await screen.findByText('Status: host unsupported')

    act(() => {
      queryClient.setQueryData(['reviewer-access', 'codex-device'], {
        state: 'failed',
        failure: 'permission_or_profile',
        verification_url: null,
        user_code: null,
        expires_in_seconds: null,
      })
    })

    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Less than 1 GiB of memory headroom is available. Free memory or raise the container limit.',
    )
    expect(
      screen.queryByText(/installed Codex sign-in could not be validated/u),
    ).not.toBeInTheDocument()
  })

  it('evicts a checked usable device result after credential clear', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const configured = {
      ...reviewerView,
      codex: { ...reviewerView.codex, state: 'configured' as const, reason: null },
    }
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => configured)
    http.recheckCodex = vi.fn(async () => ({
      state: 'usable' as const,
      failure: null,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }))
    http.clearCodex = vi.fn(async () => ({
      ...reviewerView,
      codex: { ...reviewerView.codex, state: 'ready' as const, reason: null },
    }))
    renderRoute('/settings?group=reviewer', http)

    await user.click(await screen.findByRole('button', { name: 'Check existing Codex sign-in' }))
    expect(await screen.findByText('Status: usable')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clear Codex sign-in' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Codex sign-in' }))
    expect(await screen.findByText('Status: ready')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Use Codex for cover observations' }),
    ).not.toBeInTheDocument()
  })

  it('requires confirmation before clearing a reviewer key and restores cancel focus', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const configured = {
      ...reviewerView,
      mapping: {
        engine: 'openrouter' as const,
        model: 'dots-studio/dots-3-note-preview:free',
      },
      openrouter_key_configured: true,
      openrouter: {
        ...reviewerView.openrouter,
        state: 'configured' as const,
        model: 'dots-studio/dots-3-note-preview:free',
        tier: 'free' as const,
      },
    }
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => configured)
    http.clearOpenRouter = vi.fn(async () => reviewerView)
    renderRoute('/settings?group=reviewer', http)

    const clear = await screen.findByRole('button', { name: 'Clear OpenRouter key' })
    await user.click(clear)
    expect(http.clearOpenRouter).not.toHaveBeenCalled()
    const keep = screen.getByRole('button', { name: 'Keep OpenRouter key' })
    expect(screen.getByRole('button', { name: 'Confirm clear OpenRouter key' })).toHaveFocus()
    await user.click(keep)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Clear OpenRouter key' })).toHaveFocus(),
    )

    await user.click(screen.getByRole('button', { name: 'Clear OpenRouter key' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear OpenRouter key' }))
    await waitFor(() => expect(http.clearOpenRouter).toHaveBeenCalledWith(3))
  })

  it('requires confirmation before replacing a reviewer key and restores cancel focus', async () => {
    const originalRequestFrame = window.requestAnimationFrame
    const originalCancelFrame = window.cancelAnimationFrame
    const frames = new Map<number, FrameRequestCallback>()
    let nextFrame = 1
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const frame = nextFrame
      nextFrame += 1
      frames.set(frame, callback)
      return frame
    })
    vi.stubGlobal('cancelAnimationFrame', (frame: number) => frames.delete(frame))
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      mapping: {
        engine: 'openrouter' as const,
        model: 'dots-studio/dots-3-note-preview:free',
      },
      openrouter_key_configured: true,
    }))
    try {
      renderRoute('/settings?group=reviewer', http)

      await screen.findByRole('textbox', { name: 'Model ID' })
      const key = screen.getByLabelText('API key')
      await user.type(key, 'replacement-key')
      expect(key).toHaveValue('replacement-key')
      act(() => {
        const pending = [...frames.values()]
        frames.clear()
        for (const frame of pending) frame(0)
      })
      expect(screen.getByLabelText('API key')).toBe(key)
      expect(key).toHaveFocus()
      expect(key).toHaveValue('replacement-key')
      const replace = screen.getByRole('button', { name: 'Replace OpenRouter key' })
      expect(replace).toBeEnabled()
      await user.click(replace)
      expect(http.configureOpenRouter).not.toHaveBeenCalled()
      await user.click(screen.getByRole('button', { name: 'Keep existing OpenRouter key' }))
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Replace OpenRouter key' })).toHaveFocus(),
      )
    } finally {
      vi.stubGlobal('requestAnimationFrame', originalRequestFrame)
      vi.stubGlobal('cancelAnimationFrame', originalCancelFrame)
    }
  })
})
