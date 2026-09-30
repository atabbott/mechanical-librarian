import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  settingsAudibleMarketplaceOrder,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsLocalLibraryNameValid,
  settingsProviderKeyRequired,
  settingsProviderKeyless,
  settingsSecretValid,
  settingsTextValid,
} from '../src/api/http'
import type { ReviewHttp, ReviewerAccessView, SettingsView, SettingsWrite } from '../src/api/http'
import { createHttp, renderRoute } from './fixtures'
import { document, view } from './settingsFixtures'

const reviewerView: ReviewerAccessView = {
  schema_version: 'reviewer-access-view-v2',
  revision: 3,
  mapping: null,
  description_mapping: null,
  description_metadata: null,
  description: null,
  openrouter_key_configured: false,
  codex: {
    engine: 'codex',
    state: 'failed',
    reason: 'permission_or_profile',
    model: 'gpt-5.6-sol',
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
  recommendations: [
    'mistralai/ministral-3b-2512',
    'openai/gpt-5.6-luna',
    'mistralai/mistral-small-2603',
  ],
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
    for (const adapter of ['audible_catalog', 'audiosilo', 'audnexus', 'itunes_search']) {
      expect(settingsProviderKeyless(adapter)).toBe(true)
      expect(settingsProviderKeyRequired(adapter)).toBe(false)
    }
    for (const adapter of ['hardcover', 'google_books', 'open_library'])
      expect(settingsProviderKeyless(adapter)).toBe(false)
    expect(settingsAudibleMarketplaceOrder[0]).toBe('us')
    expect(settingsAudibleMarketplaceOrder).toHaveLength(10)
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

  it('adds the keyless audiobook providers with no credential and saves them', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let savedRequest: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => ({
      ...view(),
      document: { ...document, providers: [], libraries: [] },
      provider_capabilities: [
        {
          adapter: 'audible_catalog' as const,
          credential: 'none' as const,
          label: 'Audible',
          name: 'audible_catalog',
        },
        {
          adapter: 'itunes_search' as const,
          credential: 'none' as const,
          label: 'iTunes',
          name: 'itunes_search',
        },
      ],
    }))
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      savedRequest = request
      return { ...view(), document: request.document }
    })
    renderRoute('/settings?group=providers', http)

    await user.click(await screen.findByRole('button', { name: 'Add Audible' }))
    await user.click(screen.getByRole('button', { name: 'Add iTunes' }))
    await user.click(
      screen.getByRole('checkbox', { name: 'Provider state Enable audible catalog' }),
    )

    // Keyless: no key, no email, and nothing to configure but enablement.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /API key/u })).not.toBeInTheDocument()
    expect(screen.getByText('Marketplace order')).toBeVisible()
    expect(screen.getByText('us, uk, de, fr, au, in, ca, jp, it, es')).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(savedRequest?.secrets).toEqual([])
    expect(savedRequest?.document.providers).toEqual([
      {
        adapter: 'audible_catalog',
        cover_enabled: false,
        enabled: true,
        marketplaces: ['us', 'uk', 'de', 'fr', 'au', 'in', 'ca', 'jp', 'it', 'es'],
        name: 'audible_catalog',
      },
      {
        adapter: 'itunes_search',
        cover_enabled: false,
        enabled: false,
        name: 'itunes_search',
      },
    ])
  })

  it('adds and re-enables credential-free Inventaire covers through save readback', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let savedRequest: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => ({
      ...view(),
      document: { ...document, providers: [], libraries: [] },
      provider_capabilities: [
        {
          adapter: 'inventaire' as const,
          credential: 'none' as const,
          label: 'Inventaire',
          name: 'inventaire',
        },
      ],
    }))
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      savedRequest = request
      return { ...view(), document: request.document, provider_capabilities: [] }
    })
    renderRoute('/settings?group=providers', http)

    await user.click(await screen.findByRole('button', { name: 'Add Inventaire' }))
    const enabled = screen.getByRole('checkbox', {
      name: 'Provider state Enable inventaire',
    })
    expect(enabled).toBeChecked()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /API key/u })).not.toBeInTheDocument()

    await user.click(enabled)
    expect(enabled).not.toBeChecked()
    await user.click(enabled)
    expect(enabled).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(savedRequest?.secrets).toEqual([])
    expect(savedRequest?.document.providers).toEqual([
      {
        adapter: 'inventaire',
        cover_enabled: true,
        enabled: true,
        name: 'inventaire',
      },
    ])
    expect(screen.getByRole('checkbox', { name: 'Provider state Enable inventaire' })).toBeChecked()
    expect(await screen.findByText('Settings saved and in use.')).toBeVisible()
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
          expect(omittedDisplayName).toBeTruthy()
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

  it.each([
    ['Ministral 3B', 'mistralai/ministral-3b-2512'],
    ['GPT-5.6 Luna', 'openai/gpt-5.6-luna'],
    ['Mistral Small 4', 'mistralai/mistral-small-2603'],
  ])('selects the labelled exact %s cover reviewer', async (label, model) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    http.configureOpenRouter = vi.fn(async () => ({ ...reviewerView, revision: 4 }))
    renderRoute('/settings?group=reviewer', http)
    await user.click(await screen.findByRole('button', { name: `Use ${label} (${model})` }))
    expect(screen.getByRole('textbox', { name: 'Model ID' })).toHaveValue(model)
    await user.type(screen.getByLabelText('API key'), 'synthetic-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
    await waitFor(() =>
      expect(http.configureOpenRouter).toHaveBeenCalledWith({
        expected_revision: 3,
        api_key: 'synthetic-key',
        model,
      }),
    )
  })

  it('renders wire recommendations, catalogue candidates, and manual model entry honestly', async () => {
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

    expect(await screen.findByText(/Paid cover reviewer choices/u)).toBeVisible()
    expect(screen.getByText(/Catalogue-capability-only free option/u)).toBeVisible()
    expect(screen.getByText(/20 requests\/minute/u)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toBeVisible()
    await user.click(
      screen.getByRole('button', { name: 'Use Ministral 3B (mistralai/ministral-3b-2512)' }),
    )
    const model = screen.getByRole('textbox', { name: 'Model ID' })
    expect(model).toHaveValue('mistralai/ministral-3b-2512')
    await user.click(
      screen.getByRole('button', {
        name: 'Use dots-studio/dots-3-note-preview:free',
      }),
    )
    expect(model).toHaveValue('dots-studio/dots-3-note-preview:free')
    await user.clear(model)
    await user.type(model, 'manual/vision-model')
    const key = screen.getByLabelText('API key')
    await user.type(key, 'secret-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
    await waitFor(() =>
      expect(http.configureOpenRouter).toHaveBeenCalledWith({
        expected_revision: 3,
        api_key: 'secret-key',
        model: 'manual/vision-model',
      }),
    )
    expect(key).toHaveValue('')
    expect(await screen.findByText('Advisory availability: usable')).toBeVisible()
  })

  it.each([
    [
      'an OpenRouter model',
      'openrouter',
      'openai/gpt-4o-mini',
      'OpenRouter — openai/gpt-4o-mini',
      'OpenRouter — openai/gpt-4o-mini',
    ],
    [
      'contained Codex with the admitted profile model',
      'codex',
      '',
      'Contained Codex — gpt-5.6-sol',
      'Contained Codex — gpt-5.6-sol',
    ],
    ['cleared', 'none', '', 'No reviewer', 'No reviewer'],
  ] as const)(
    'states the cover model beside its engine when the selection is %s',
    async (_label, engine, model, expected, emphasised) => {
      const http = createHttp()
      http.getSettings = vi.fn(async () => view())
      http.getReviewerAccess = vi.fn(async () => ({
        ...reviewerView,
        mapping:
          engine === 'openrouter'
            ? { engine: 'openrouter' as const, model }
            : engine === 'codex'
              ? { engine: 'codex' as const }
              : null,
        codex: { ...reviewerView.codex, state: 'usable' as const, reason: null },
      }))
      renderRoute('/settings?group=reviewer', http)

      const line = await screen.findByText(/Current selection:/u)
      expect(line).toHaveTextContent(expected)
      // Only the engine, and a model it actually has, carry the emphasis: the
      // explanation of an absent model stays plain text.
      expect(line.querySelector('strong')).toHaveTextContent(emphasised)
      expect(line.querySelector('strong')?.textContent).toBe(emphasised)
    },
  )

  it('discloses the cover request basis without a price, a date or a cheapest claim', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      mapping: { engine: 'openrouter' as const, model: 'openai/gpt-4o-mini' },
      openrouter_key_configured: true,
      openrouter: {
        ...reviewerView.openrouter,
        state: 'usable' as const,
        model: 'openai/gpt-4o-mini',
        tier: 'paid' as const,
      },
    }))
    renderRoute('/settings?group=reviewer', http)

    const region = await screen.findByRole('region', { name: 'Optional cover reviewer' })
    const cover = within(region)
    const facts = region.querySelector('.settings-reviewer-facts')
    expect(facts).not.toBeNull()
    // The terms read exactly as the description list's do; only the sentences
    // are cover-specific.
    expect(cover.getAllByRole('term').map((term) => term.textContent)).toEqual([
      'Model',
      'Capabilities',
      'Price metadata observed',
      'Prices in USD',
      'Maximum-size request estimate',
    ])
    expect(cover.getByText('openai/gpt-4o-mini', { selector: 'dd' })).toBeVisible()
    expect(cover.getByText(/Image input, text output and structured output/u)).toBeVisible()
    expect(cover.getByText(/no price observation date exists/u)).toBeVisible()
    expect(cover.getByText(/no model is presented as the cheapest/u)).toBeVisible()
    const estimate = cover.getByText(/at most 12 cover previews/u)
    expect(estimate).toHaveTextContent('320 by 480 pixels and 1 MiB and together at most 8 MiB')
    // The request also carries bounded text, so the basis names it rather than
    // asking the owner to price images and output alone.
    expect(estimate).toHaveTextContent('the pinned instruction (at most 4 KiB)')
    expect(estimate).toHaveTextContent('the sealed packet text (at most 1 MiB)')
    expect(estimate).toHaveTextContent('a strict output schema (at most 64 KiB)')
    expect(estimate).toHaveTextContent('8,192 output tokens')
    expect(estimate).toHaveTextContent(
      'Apply the published text-input, image-input and output rates',
    )
    expect(estimate).toHaveTextContent('an estimate, not a quote')
    expect(cover.queryByText(/per input token/u)).toBeNull()
    // The Model row tracks a controlled input, so the list must not be a live
    // region; the status paragraph above owns the announcements.
    expect(facts).not.toHaveAttribute('aria-live')
    expect(region.querySelector('p[aria-live]')).not.toBeNull()
    // The primary action precedes the disclosure, so the key field reaches it
    // without scrolling past the prose at any width.
    const action = cover.getByRole('button', { name: 'Replace OpenRouter key' })
    expect(
      action.compareDocumentPosition(facts as Node) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    // The disclosure carries no control, so the key field still tabs straight
    // to the next available action past the disabled replace button.
    screen.getByLabelText('API key').focus()
    await userEvent.setup().tab()
    expect(cover.getByRole('button', { name: 'Refresh current status' })).toHaveFocus()
  })

  it('tracks the typed cover model in the disclosure before anything is saved', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    renderRoute('/settings?group=reviewer', http)

    const cover = within(await screen.findByRole('region', { name: 'Optional cover reviewer' }))
    const model = cover.getByRole('textbox', { name: 'Model ID' })
    expect(cover.getByText(/None yet\. Enter a model ID above/u)).toBeVisible()
    await user.type(model, 'openai/gpt-4o')
    expect(cover.getByText('openai/gpt-4o', { selector: 'dd' })).toBeVisible()
    expect(cover.queryByText(/None yet\. Enter a model ID above/u)).toBeNull()
    // Clearing the field returns the row to the instruction rather than to a
    // model the page no longer offers.
    await user.clear(model)
    expect(cover.getByText(/None yet\. Enter a model ID above/u)).toBeVisible()
  })

  it('states an absent cover model in the disclosure instead of a blank fact', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    renderRoute('/settings?group=reviewer', http)

    const cover = within(await screen.findByRole('region', { name: 'Optional cover reviewer' }))
    expect(
      await cover.findByText(
        'None yet. Enter a model ID above; it is checked for image, text and structured output when you validate.',
        { selector: 'dd' },
      ),
    ).toBeVisible()
    expect(cover.getByText(/an estimate, not a quote/u)).toBeVisible()
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
    expect(await screen.findByText(`Advisory availability: ${state}`)).toBeVisible()
    expect(http.recheckCodex).toHaveBeenCalledOnce()
    if (failure) expect(screen.getByRole('alert')).toBeVisible()
  })

  it('resumes an active Codex device flow after the Settings panel remounts', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      codex: { ...reviewerView.codex, state: 'waiting' as const },
    }))
    http.getCodexDevice = vi.fn(async () => ({
      state: 'issued' as const,
      failure: null,
      verification_url: 'https://example.invalid/device',
      user_code: 'ABCD-EFGH',
      expires_in_seconds: 900,
    }))

    const { router } = renderRoute('/settings?group=reviewer', http)
    expect(await screen.findByText('ABCD-EFGH')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Cancel sign-in' })).toBeVisible()
    await act(async () => router.navigate({ to: '/library', search: { lens: 'all' } }))

    await act(async () => router.navigate({ to: '/settings', search: { group: 'reviewer' } }))
    expect(await screen.findByText('ABCD-EFGH')).toBeVisible()
    expect(http.getCodexDevice).toHaveBeenCalledOnce()
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
    expect(screen.getByText('Advisory availability: validating')).toBeVisible()
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
    expect(await screen.findByText('Advisory availability: usable')).toBeVisible()
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
    expect(await screen.findByText('Advisory availability: usable')).toBeVisible()
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
    await screen.findByText('Advisory availability: host unsupported')

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
    expect(await screen.findByText('Advisory availability: usable')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clear Codex sign-in' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Codex sign-in' }))
    expect(await screen.findByText('Advisory availability: ready')).toBeVisible()
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

  it('renders and range-validates the audiobook source limit beside the archive one', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=automation', http)

    await user.click(await screen.findByText('Advanced response and archive limits'))
    const audio = screen.getByRole('spinbutton', { name: 'Audiobook source limit' })
    expect(audio).toHaveValue(8_589_934_592)
    // Its own ceiling, not the EPUB archive's: an audiobook is read by header
    // and is routinely larger than any archive this build admits.
    expect(audio).toHaveAttribute('max', '17179869184')
    expect(screen.getByRole('spinbutton', { name: 'Source archive limit' })).toHaveAttribute(
      'max',
      '268435456',
    )

    await user.clear(audio)
    await user.type(audio, '1023')
    expect(audio).toHaveAttribute('aria-invalid', 'true')
    expect(audio).toHaveAccessibleDescription(/Audiobook source limit must be from 1,024/)
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()

    await user.clear(audio)
    await user.type(audio, '17179869185')
    expect(audio).toHaveAttribute('aria-invalid', 'true')

    await user.clear(audio)
    await user.type(audio, '17179869184')
    expect(audio).not.toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled()
  })
})

it('discloses description prices before activation and preserves the cover task and automatic preference', async () => {
  const http = createHttp()
  const initial: ReviewerAccessView = {
    ...reviewerView,
    mapping: { engine: 'openrouter', model: 'example/cover-a' },
    openrouter_key_configured: true,
  }
  const metadata = {
    model: 'example/text-b',
    observed_at: 100,
    prompt_price: '0.000001',
    completion_price: '0.000002',
    request_price: '0',
    text_input: true as const,
    text_output: true as const,
    structured_outputs: true as const,
  }
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => initial)
  http.testDescriptionModel = vi.fn(async () => metadata)
  http.configureOpenRouter = vi.fn(async () => ({
    ...initial,
    revision: 4,
    description_mapping: { engine: 'openrouter' as const, model: metadata.model },
    description_metadata: metadata,
  }))
  renderRoute('/settings?group=reviewer', http)
  const user = userEvent.setup()
  const enable = await screen.findByRole('button', {
    name: 'Use this OpenRouter model for descriptions',
  })
  expect(enable).toBeDisabled()
  expect(screen.getByRole('link', { name: /Review cleanup preferences/ })).toHaveAttribute(
    'href',
    '/policy?section=curation#description-advice',
  )
  await user.type(
    screen.getByRole('textbox', { name: 'OpenRouter description model' }),
    metadata.model,
  )
  await user.click(screen.getByRole('button', { name: 'Test description model and prices' }))
  expect(await screen.findByText(/Maximum-size input estimate/)).toBeVisible()
  expect(screen.getByText(metadata.model, { selector: 'dd' })).toBeVisible()
  expect(screen.getByText('Metadata observed', { selector: 'dt' })).toBeVisible()
  expect(screen.getByText(/0.000001 per input token/)).toBeVisible()
  expect(http.configureOpenRouter).not.toHaveBeenCalled()
  await user.click(enable)
  await waitFor(() =>
    expect(http.configureOpenRouter).toHaveBeenCalledWith({
      expected_revision: 3,
      api_key: '',
      model: metadata.model,
      task: 'description_rewrite',
    }),
  )
  expect(http.writeSettings).not.toHaveBeenCalled()
  expect(document.description_advice.automatic_application).toBe(false)
})

it('inspects the exact contained description model before selection without starting a request', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => ({
    ...reviewerView,
    codex: { ...reviewerView.codex, state: 'usable' as const },
  }))
  http.describeContainedDescription = vi.fn(async () => ({
    model: 'contained-model',
    profile_sha256: 'a'.repeat(64),
  }))
  renderRoute('/settings?group=reviewer', http)
  const use = await screen.findByRole('button', { name: 'Use Codex for descriptions' })
  expect(use).toBeDisabled()
  await userEvent
    .setup()
    .click(screen.getByRole('button', { name: 'Inspect contained description model' }))
  expect(await screen.findByText('contained-model', { selector: 'dd' })).toBeVisible()
  expect(screen.getByText(/per-request monetary estimate is unavailable/)).toBeVisible()
  expect(use).toBeEnabled()
  const identity = screen.getByText('a'.repeat(64))
  expect(identity).not.toBeVisible()
  await userEvent.setup().click(screen.getByText('Inspect contained profile identity'))
  expect(identity).toBeVisible()
  expect(
    screen.getByRole('link', { name: 'Check current write consent in Activity' }),
  ).toHaveAttribute('href', '/activity')
  expect(screen.getByText(/Adviser availability does not grant permission/)).toBeVisible()
  expect(http.selectReviewer).not.toHaveBeenCalled()
})

it.each([false, true])(
  'shares refreshed Codex availability with descriptions while host blocked=%s',
  async (hostBlocked) => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      codex: {
        ...reviewerView.codex,
        state: hostBlocked ? ('host_unsupported' as const) : ('ready' as const),
      },
    }))
    http.describeContainedDescription = vi.fn(async () => ({
      model: 'contained-model',
      profile_sha256: 'a'.repeat(64),
    }))
    const device = {
      state: 'usable' as const,
      failure: null,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }
    http.recheckCodex = vi.fn(async () => device)
    const { queryClient } = renderRoute('/settings?group=reviewer', http)
    const use = await screen.findByRole('button', { name: 'Use Codex for descriptions' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Inspect contained description model' }))
    expect(await screen.findByText('contained-model', { selector: 'dd' })).toBeVisible()
    expect(use).toBeDisabled()
    if (hostBlocked) {
      await act(async () => {
        queryClient.setQueryData(['reviewer-access', 'codex-device'], device)
      })
      expect(use).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toBeDisabled()
    } else {
      await user.click(screen.getByRole('button', { name: 'Check existing Codex sign-in' }))
      await waitFor(() => expect(use).toBeEnabled())
      await user.click(use)
      await waitFor(() =>
        expect(http.selectReviewer).toHaveBeenCalledWith(
          'codex',
          reviewerView.revision,
          undefined,
          'description_rewrite',
        ),
      )
    }
  },
)

it('presents peer reviewer tasks with one shared sign-in and labelled description disclosure', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => reviewerView)
  renderRoute('/settings?group=reviewer', http)
  const cover = await screen.findByRole('region', { name: 'Optional cover reviewer' })
  const description = screen.getByRole('region', { name: 'Optional description adviser' })
  expect(
    within(cover).getByRole('heading', { level: 3, name: 'Optional cover reviewer' }),
  ).toBeVisible()
  expect(
    within(description).getByRole('heading', { level: 3, name: 'Optional description adviser' }),
  ).toBeVisible()
  expect(cover.contains(description)).toBe(false)
  expect(within(cover).queryByRole('heading', { name: 'Shared Codex sign-in' })).toBeNull()
  expect(within(description).queryByRole('heading', { name: 'Shared Codex sign-in' })).toBeNull()
  expect(screen.getAllByRole('heading', { name: 'Shared Codex sign-in' })).toHaveLength(1)
  expect(screen.getAllByRole('button', { name: 'Check existing Codex sign-in' })).toHaveLength(1)
  expect(
    within(description).getByLabelText('Shared OpenRouter API key'),
  ).toHaveAccessibleDescription(/Leave the key blank to use the saved credential/)
  expect(
    within(description).getByRole('textbox', { name: 'OpenRouter description model' }),
  ).toBeVisible()
  expect(
    within(description).getByRole('button', { name: 'Use this OpenRouter model for descriptions' }),
  ).toBeDisabled()
})

it.each(['clear', 'start'] as const)(
  'reconciles a partially completed Codex %s without claiming success or retrying',
  async (command) => {
    const user = userEvent.setup()
    const http = createHttp()
    const configured = {
      ...reviewerView,
      mapping: { engine: 'codex' as const },
      codex: { ...reviewerView.codex, state: 'configured' as const, reason: null },
    }
    const reconciled = {
      ...reviewerView,
      revision: configured.revision + 1,
      mapping: null,
      codex: { ...reviewerView.codex, state: 'ready' as const, reason: null },
    }
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(configured)
      .mockResolvedValue(reconciled)
    http.getCodexDevice = vi.fn(async () => ({
      state: 'ready' as const,
      failure: null,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }))
    http.clearCodex = vi.fn(async () => {
      throw new ApiError(409, 'reviewer_access_stale')
    })
    http.startCodexDevice = vi.fn(async () => {
      throw new ApiError(409, 'reviewer_access_stale')
    })
    renderRoute('/settings?group=reviewer', http)
    await user.click(
      await screen.findByRole('button', {
        name: command === 'clear' ? 'Clear Codex sign-in' : 'Replace Codex sign-in',
      }),
    )
    await user.click(
      screen.getByRole('button', {
        name: command === 'clear' ? 'Confirm clear Codex sign-in' : 'Confirm replace Codex sign-in',
      }),
    )
    expect(await screen.findByText(/The change could not be fully confirmed/u)).toHaveTextContent(
      'Your prior sign-in or reviewer selection may have changed',
    )
    await waitFor(() => expect(http.getReviewerAccess).toHaveBeenCalledTimes(2))
    expect(http.getCodexDevice).toHaveBeenCalledOnce()
    expect(screen.getByText(/Current selection:/u)).toHaveTextContent('No reviewer')
    expect(command === 'clear' ? http.clearCodex : http.startCodexDevice).toHaveBeenCalledOnce()
    expect(command === 'clear' ? http.startCodexDevice : http.clearCodex).not.toHaveBeenCalled()
  },
)

it.each([
  ['cancelled', 'Any active sign-in was cancelled.'],
  ['ready', 'No active sign-in remains.'],
] as const)(
  'keeps blocked reload cancellation available without probing credentials: %s',
  async (state, message) => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      codex: {
        ...reviewerView.codex,
        state: 'not_provisioned' as const,
        reason: 'profile_not_provisioned' as const,
        model: null,
      },
    }))
    http.cancelCodexDevice = vi.fn(async () => ({
      state,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
      failure: null,
    }))
    renderRoute('/settings?group=reviewer', http)
    await userEvent
      .setup()
      .click(await screen.findByRole('button', { name: 'Cancel any active sign-in' }))
    expect(await screen.findByText(message)).toBeVisible()
    expect(http.getCodexDevice).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Replace or create Codex sign-in' })).toBeDisabled()
    expect(screen.getByText('Advisory availability: not provisioned')).toBeVisible()
  },
)

it('keeps shared sign-in clear available for a description-only selection after profile drift', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  const unavailable = {
    ...reviewerView,
    description_mapping: { engine: 'codex' as const },
    codex: {
      ...reviewerView.codex,
      state: 'not_provisioned' as const,
      reason: 'profile_not_provisioned' as const,
      model: null,
    },
  }
  http.getReviewerAccess = vi.fn(async () => unavailable)
  http.clearCodex = vi.fn(async () => ({
    ...unavailable,
    revision: unavailable.revision + 1,
    description_mapping: null,
  }))
  renderRoute('/settings?group=reviewer', http)
  await user.click(await screen.findByRole('button', { name: 'Clear Codex sign-in' }))
  await user.click(screen.getByRole('button', { name: 'Confirm clear Codex sign-in' }))
  await waitFor(() => expect(http.clearCodex).toHaveBeenCalledWith(unavailable.revision))
  expect(await screen.findByText('No description adviser selected.')).toBeVisible()
  expect(http.recheckCodex).not.toHaveBeenCalled()
  expect(http.startCodexDevice).not.toHaveBeenCalled()
})
