import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, onTestFinished, vi } from 'vitest'

import {
  AmbiguousMutationError,
  CsrfRenewedError,
  ApiError,
  BuildMismatchError,
  settingsAudibleMarketplaceOrder,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsLocalLibraryNameValid,
  settingsProviderKeyRequired,
  settingsProviderKeyless,
  settingsSecretValid,
  settingsTextValid,
  UncertainMutationError,
} from '../src/api/http'
import type {
  CodexDeviceAuthState,
  PreparationView,
  ReviewHttp,
  ReviewerAccessView,
  SettingsView,
  SettingsWrite,
} from '../src/api/http'
import { coverReviewerLabel } from '../src/labels'
import { createHttp, idlePreparation, renderRoute } from './fixtures'
import { document, savedDocument, view } from './settingsFixtures'

const reviewerView: ReviewerAccessView = {
  schema_version: 'reviewer-access-view-v4',
  taste_mapping: null,
  taste_metadata: null,
  taste_daily_requests: 8,
  taste_recommendations: ['anthropic/claude-sonnet-5.5'],
  cover_metadata: null,
  cost_evidence: { models: [] },
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
  free_tier_caveat: '20 requests/minute; 50 or 1000 requests/day by account tier.',
  data_use_caveat: 'Cover previews are sent to OpenRouter and its selected provider.',
}

// Chooses a model, or the custom entry, from a reviewer model picker.
const pickModel = async (
  user: ReturnType<typeof userEvent.setup>,
  picker: string,
  name: string,
) => {
  await user.click(screen.getByRole('combobox', { name: picker }))
  await user.click(screen.getByRole('option', { name }))
}

// The pressed-button choice inside a named segmented control.
const segment = (group: string, choice: string) =>
  within(screen.getByRole('group', { name: group })).getByRole('button', { name: choice })

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
    expect(settingsProviderKeyRequired('google_books')).toBe(true)
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
    expect(segment('Use Open Library', 'Off')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByRole('group', { name: 'Use Open Library' })).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Contact email' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Add Open Library' })).not.toBeInTheDocument()
  })

  it('offers to add a source only when the settings are not file-managed', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => ({ ...view(), document: { ...document, providers: [] } }))
    renderRoute('/settings?group=providers', http)
    expect(await screen.findByText('No metadata sources yet. Add one below.')).toBeVisible()
    cleanup()
    http.getSettings = vi.fn(async () => ({
      ...view('file'),
      document: { ...document, providers: [] },
    }))
    renderRoute('/settings?group=providers', http)
    expect(await screen.findByText('No metadata sources.')).toBeVisible()
    expect(screen.queryByText(/Add one below/u)).toBeNull()
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
      return { ...view(), document: savedDocument(request.document) }
    })
    renderRoute('/settings?group=providers', http)

    await user.click(await screen.findByRole('button', { name: 'Add Audible' }))
    await user.click(screen.getByRole('button', { name: 'Add iTunes' }))
    await user.click(segment('Use Audible', 'On'))

    // Keyless: no key, no email, and nothing to configure but enablement.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /API key/u })).not.toBeInTheDocument()
    expect(screen.getByText('Marketplace order')).toBeVisible()
    expect(screen.getByText('us, uk, de, fr, au, in, ca, jp, it, es')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save settings' })).toHaveClass('button-primary')
    expect(screen.getByRole('button', { name: 'Discard all' })).not.toHaveClass('button-primary')

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
      return { ...view(), document: savedDocument(request.document), provider_capabilities: [] }
    })
    renderRoute('/settings?group=providers', http)

    await user.click(await screen.findByRole('button', { name: 'Add Inventaire' }))
    expect(segment('Use Inventaire', 'On')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /API key/u })).not.toBeInTheDocument()

    await user.click(segment('Use Inventaire', 'Off'))
    expect(segment('Use Inventaire', 'Off')).toHaveAttribute('aria-pressed', 'true')
    await user.click(segment('Use Inventaire', 'On'))
    expect(segment('Use Inventaire', 'On')).toHaveAttribute('aria-pressed', 'true')
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
    expect(await screen.findByText('Settings saved and in use.')).toBeVisible()
    expect(segment('Use Inventaire', 'On')).toHaveAttribute('aria-pressed', 'true')
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
    await user.click(screen.getByRole('button', { name: /Metadata sources/ }))
    expect(screen.getByRole('group', { name: 'Use Hardcover' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Get a Hardcover key' })).toBeVisible()
    expect(screen.getByText('Configured · ····abcd')).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Contact email' })).toHaveAccessibleDescription(
      /Sent to Open Library with each lookup, as it asks/,
    )
    expect(screen.queryByText('Cover evidence')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /cover evidence/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replace Hardcover API key' }))
    await user.type(screen.getByLabelText('Replacement Hardcover API key'), 'new-secret-abcd')
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    const review = screen.getByRole('list')
    expect(review).toHaveTextContent('Hardcover API key')
    expect(review).toHaveTextContent('Will be replaced')
    expect(review).not.toHaveTextContent('new-secret-abcd')
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
      return { ...view(), document: savedDocument(request.document) }
    })
    renderRoute('/settings?group=providers', http)

    await screen.findByRole('group', { name: 'Use Hardcover' })
    await user.click(segment('Use Hardcover', 'Off'))
    expect(screen.getByText('Configured · ····abcd')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Replace Hardcover API key' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clear Hardcover API key' })).toBeVisible()

    await user.click(segment('Use Open Library', 'Off'))
    expect(screen.getByRole('textbox', { name: 'Contact email' })).toHaveValue(
      'owner@example.invalid',
    )

    // Turning a source off removed it from the library: an indirect change
    // that the Libraries count and the change review both carry.
    expect(screen.getByRole('button', { name: /Libraries/ })).toHaveAccessibleDescription(
      '1 changed field',
    )
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    expect(screen.getByRole('list')).toHaveTextContent(
      'Ebooks & Audiobooks: Collected Editions: sources (a source was turned off)',
    )
    await user.click(segment('Use Hardcover', 'On'))
    await user.click(
      within(screen.getByRole('navigation', { name: 'Settings sections' })).getByRole('button', {
        name: /Libraries/,
      }),
    )
    expect(screen.getByRole('checkbox', { name: 'Hardcover' })).not.toBeChecked()
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
      /Libraries/,
      /Metadata sources/,
      /Reviewer/,
      /Background work/,
      /Access and diagnostics/,
      /Appearance/,
    ]) {
      await user.click(screen.getByRole('button', { name }))
      expect(window.document.querySelector('.settings-sheet input')).toBeNull()
      expect(window.document.querySelector('.settings-sheet .cm-segmented')).toBeNull()
      expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Discard all' })).not.toBeInTheDocument()
    }
    // The locked view reads a size as the editable field does (#1286 X13).
    await user.click(screen.getByRole('button', { name: /Background work/ }))
    await user.click(screen.getByText('Response and file size limits'))
    expect(screen.getByText('0.0625 MB (64 KB)')).toBeVisible()
    expect(screen.getByText('8,192 MB (8 GB)')).toBeVisible()
  })

  it('owns group navigation in the URL and restores it with browser history', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const { router } = renderRoute('/settings?group=connection', http)
    await screen.findByRole('heading', { level: 2, name: 'Audiobookshelf' })
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
    const metadata = screen.getByText('a-very_long-library-name').closest('.settings-record-meta')
    expect(metadata).not.toBeNull()
    expect(metadata).toHaveTextContent('Local identifier: a-very_long-library-name')
    expect(metadata).not.toHaveTextContent('Audiobookshelf identifier')
    expect(
      screen.getByRole('group', {
        name: 'Sources for Ebooks & Audiobooks: Collected Editions',
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

  it.each([
    [
      'unrelated',
      'https://library.invalid',
      'https://other.invalid',
      /Your changes were kept/,
      'alert',
    ],
    // The only edit took the newer value, so the notice leaves the empty tray.
    [
      'conflicting',
      'https://elsewhere.invalid',
      'https://elsewhere.invalid',
      /Nothing is left to save/,
      'status',
    ],
  ])(
    'keeps the draft over %s newer settings and says what a stale save changed',
    async (_case, newerEndpoint, shownEndpoint, message, role) => {
      const user = userEvent.setup()
      const http = createHttp()
      const newer: SettingsView = {
        ...view(),
        document: {
          ...document,
          revision: 4,
          reduced_motion: 'always_reduced',
          connections: [{ adapter: 'audiobookshelf', endpoint: newerEndpoint, name: 'home' }],
        },
      }
      http.getSettings = vi
        .fn()
        .mockRejectedValueOnce(new ApiError(503, 'settings_unavailable'))
        .mockResolvedValueOnce(view())
        .mockResolvedValue(newer)
      http.writeSettings = vi.fn(async () => {
        throw new ApiError(409, 'settings_revision_stale')
      })
      renderRoute('/settings', http)
      expect(await screen.findByRole('alert')).toHaveTextContent('If trying again does not help')
      await user.click(screen.getByRole('button', { name: 'Try again' }))
      expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
      await user.clear(screen.getByDisplayValue('https://library.invalid'))
      await user.type(screen.getByRole('textbox'), 'https://other.invalid')
      await user.click(screen.getByRole('button', { name: 'Save settings' }))
      const staleAlert = await screen.findByText(message)
      await waitFor(() => expect(staleAlert).toHaveFocus())
      expect(staleAlert).toHaveAttribute('role', role)
      if (role === 'status')
        expect(screen.queryByRole('region', { name: 'Unsaved settings' })).toBeNull()
      expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveValue(
        shownEndpoint,
      )
      // The newer saved motion preference is kept, not reverted by the draft.
      await user.click(screen.getByRole('button', { name: /Appearance/ }))
      expect(segment('Motion', 'Always reduced')).toHaveAttribute('aria-pressed', 'true')
    },
  )

  it.each([
    new AmbiguousMutationError(),
    // A 2xx the decoder refused (#1689).
    new UncertainMutationError({
      operation: 'PATCH /api/v1/settings',
      responseStatus: 200,
      at: '/fields',
      reason: 'required',
    }),
  ])('reconciles an uncertain save before confirming authoritative state', async (failure) => {
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
      'Saved settings loaded. Check whether your changes are there before saving again.',
    )
    expect(recovered.closest('[role="alert"]')).toHaveFocus()
    expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveValue(
      'https://authoritative.invalid',
    )
    // The reloaded values are the saved state, so no draft or tray remains.
    expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument()
    expect(http.getSettings).toHaveBeenCalledTimes(2)
    expect(http.writeSettings).toHaveBeenCalledOnce()
  })

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

    const uncertain = await screen.findByText(
      /No answer came back, and the saved settings could not be loaded/,
    )
    expect(uncertain.closest('[role="alert"]')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Load saved settings' })).toBeVisible()
    expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument()
    // Nothing is still coming, so the tray does not say it is waiting (#1742).
    expect(screen.queryByText(/Checking what was saved/u)).toBeNull()
    expect(http.writeSettings).toHaveBeenCalledOnce()
  })

  describe('an uncertain save on an earlier build (#1742)', () => {
    const reloadName = 'Reload'
    const lead = 'No answer came back for the Settings save.'
    const saveUncertain = async (http: ReturnType<typeof createHttp>) => {
      const user = userEvent.setup()
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
      return user
    }
    const expectStale = async () => {
      const reload = await screen.findByRole('button', { name: reloadName })
      const element = screen.getByText(lead, { exact: false })
      expect(element).not.toHaveAttribute('role')
      expect(element.closest('[role="alert"]')).toBeNull()
      await waitFor(() => expect(reload).toHaveFocus())
      expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
      expect(screen.queryByRole('button', { name: 'Load saved settings' })).toBeNull()
      expect(screen.queryByText(/could not be reloaded/u)).toBeNull()
      expect(screen.queryByText(/Checking what was saved/u)).toBeNull()
    }

    it('offers the reload when the reconciling read meets a newer build', async () => {
      const http = createHttp()
      http.getSettings = vi
        .fn()
        .mockResolvedValueOnce(view())
        .mockRejectedValue(new BuildMismatchError())
      await saveUncertain(http)
      await expectStale()
      expect(http.writeSettings).toHaveBeenCalledOnce()
    })

    it('moves focus to the reload when Load saved settings meets a newer build', async () => {
      const http = createHttp()
      http.getSettings = vi
        .fn()
        .mockResolvedValueOnce(view())
        .mockRejectedValueOnce(new ApiError(503, 'settings_unavailable'))
        .mockRejectedValue(new BuildMismatchError())
      const user = await saveUncertain(http)
      await user.click(await screen.findByRole('button', { name: 'Load saved settings' }))
      await expectStale()
    })
  })

  it('offers the reload, not a rebase, when a save meets a newer build (#1742)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async () => {
      throw new BuildMismatchError()
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
    const reads = vi.mocked(http.getSettings).mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    const reload = await screen.findByRole('button', { name: 'Reload' })
    const element = screen.getByText(
      'Settings were not saved. Reloading discards your unsaved changes.',
      { exact: false },
    )
    expect(element).not.toHaveAttribute('role')
    expect(element.closest('[role="alert"]')).toBeNull()
    await waitFor(() => expect(reload).toHaveFocus())
    expect(screen.queryByText(/Settings changed elsewhere/u)).toBeNull()
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
    expect(vi.mocked(http.getSettings).mock.calls.length).toBe(reads)
    expect(http.writeSettings).toHaveBeenCalledOnce()
    // An edit after the mismatch does not offer a save that cannot succeed.
    await user.type(endpoint, '/edited')
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeVisible()
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
    expect(endpoint).toHaveAccessibleDescription(
      /Enter an Audiobookshelf endpoint of up to 2,048 characters/,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Enter an Audiobookshelf endpoint')

    await user.click(screen.getByRole('button', { name: 'Replace Audiobookshelf access key' }))
    const targetSecret = screen.getByLabelText('Replacement Audiobookshelf access key')
    await user.type(targetSecret, 'Bearer secret')
    expect(targetSecret).toHaveAttribute('aria-invalid', 'true')
    expect(targetSecret).toHaveAccessibleDescription(
      /Enter the key without spaces or a Bearer prefix/,
    )

    await user.click(screen.getByRole('button', { name: /Metadata sources/ }))
    const email = screen.getByRole('textbox', { name: 'Contact email' })
    await user.clear(email)
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription(
      /Enter a contact email for Open Library, or turn it off/,
    )
    await user.click(segment('Use Open Library', 'Off'))
    expect(email).not.toHaveAttribute('aria-invalid')
    await user.click(segment('Use Open Library', 'On'))
    expect(email).toHaveAttribute('aria-invalid', 'true')
    await user.type(email, 'owner@example')
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription(
      /Enter a full email address for Open Library, such as name@example\.com/,
    )

    await user.click(screen.getByRole('button', { name: 'Clear Hardcover API key' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Hardcover API key' }))
    const requiredKey = screen.getByRole('button', {
      name: 'Keep configured Hardcover API key',
    })
    expect(requiredKey).toHaveAttribute('aria-invalid', 'true')
    expect(requiredKey).toHaveAccessibleDescription(/This source needs an API key while it is on/)

    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    const libraryId = screen.getByRole('textbox', { name: 'Audiobookshelf library ID' })
    await user.clear(libraryId)
    expect(libraryId).toHaveAttribute('aria-invalid', 'true')
    expect(libraryId).toHaveAccessibleDescription(
      /Enter an Audiobookshelf library ID of up to 128 characters/,
    )

    await user.click(screen.getByRole('button', { name: /Background work/ }))
    const timeout = screen.getByRole('spinbutton', { name: 'Request timeout' })
    await user.clear(timeout)
    await user.type(timeout, '0')
    expect(timeout).toHaveAttribute('aria-invalid', 'true')
    expect(timeout).toHaveAccessibleDescription(/Request timeout must be from 1 to 60/)

    // Save validates the whole draft, including sections not on screen, and
    // takes the owner to the first invalid field instead of sending it.
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveFocus(),
    )
    expect(screen.getByRole('heading', { level: 2, name: 'Audiobookshelf' })).toBeVisible()
    expect(screen.getByText(/values need fixing before saving/)).toBeVisible()
    expect(http.writeSettings).not.toHaveBeenCalled()
  })

  it('asks for a Google Books key while Google Books is on (#1516)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => ({
      ...view(),
      document: {
        ...document,
        providers: [
          ...document.providers,
          {
            adapter: 'google_books' as const,
            cover_enabled: false as const,
            enabled: true,
            name: 'google',
          },
        ],
      },
    }))
    renderRoute('/settings?group=providers', http)

    const key = await screen.findByLabelText('New Google Books API key')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(key).toHaveAttribute('aria-invalid', 'true'))
    expect(key).toHaveAccessibleDescription(/This source needs an API key while it is on/)
    expect(http.writeSettings).not.toHaveBeenCalled()

    // Turned off, Google Books needs no key.
    await user.click(segment('Use Google Books', 'Off'))
    await waitFor(() => expect(key).not.toHaveAttribute('aria-invalid'))
  })

  it.each([
    ['mistralai/ministral-3b-2512', 'Ministral 3B'],
    ['openai/gpt-5.6-luna', 'GPT-5.6 Luna'],
    ['mistralai/mistral-small-2603', 'Mistral Small 4'],
    ['xiaomi/mimo-v2.6-flash', 'MiMo 2.6 Flash'],
    ['qwen/qwen3.8-flash', 'Qwen 3.8 Flash'],
  ])('retains the readable historical or custom label for %s', (model, label) => {
    expect(coverReviewerLabel(model)).toBe(`${label} (${model})`)
  })

  it.each([['GPT-6 Luna', 'openai/gpt-6-luna']])(
    'selects the labelled exact %s cover reviewer',
    async (label, model) => {
      const user = userEvent.setup()
      const http = createHttp()
      http.getSettings = vi.fn(async () => view('file'))
      http.getReviewerAccess = vi.fn(async () => reviewerView)
      http.configureOpenRouter = vi.fn(async () => ({ ...reviewerView, revision: 4 }))
      renderRoute('/settings?group=reviewer', http)
      await screen.findByRole('combobox', { name: 'Cover model' })
      await pickModel(user, 'Cover model', `${label} (${model})`)
      expect(screen.getByRole('combobox', { name: 'Cover model' })).toHaveTextContent(label)
      await user.type(screen.getByLabelText('OpenRouter API key'), 'synthetic-key')
      await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
      await waitFor(() =>
        expect(http.configureOpenRouter).toHaveBeenCalledWith({
          expected_revision: 3,
          api_key: 'synthetic-key',
          model,
        }),
      )
    },
  )

  it.each([
    [
      new ApiError(503, 'reviewer_access_unavailable'),
      'No answer came back. Check whether the reviewer change was saved before trying again.',
      true,
    ],
    [
      new ApiError(500, 'invalid_error_response'),
      'No answer came back. Check whether the reviewer change was saved before trying again.',
      true,
    ],
    // An answer the decoder refuses may follow a committed write (#1690).
    [
      new UncertainMutationError({
        operation: 'PUT /api/v1/reviewer-access/openrouter',
        responseStatus: 200,
        at: '/codex',
        reason: 'required',
      }),
      'No answer came back. Check whether the reviewer change was saved before trying again.',
      true,
    ],
    [
      new ApiError(503, 'unreachable'),
      'OpenRouter could not be reached. Check network access, then retry.',
      false,
    ],
  ])('reports an OpenRouter configuration failing with %s', async (failure, copy, reloads) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    http.configureOpenRouter = vi.fn(async () => {
      throw failure
    })
    renderRoute('/settings?group=reviewer', http)
    await screen.findByRole('combobox', { name: 'Cover model' })
    await pickModel(user, 'Cover model', 'GPT-6 Luna (openai/gpt-6-luna)')
    await user.type(screen.getByLabelText('OpenRouter API key'), 'synthetic-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))

    expect(await screen.findByText(copy)).toBeVisible()
    // Nothing else reads reviewer access after the write, so a read after the
    // failed call is the panel's own reload of the authoritative state.
    const readsAfterFailure = () => {
      const failedAt = vi.mocked(http.configureOpenRouter).mock.invocationCallOrder[0] ?? Infinity
      return vi
        .mocked(http.getReviewerAccess)
        .mock.invocationCallOrder.filter((order) => order > failedAt).length
    }
    if (reloads) await waitFor(() => expect(readsAfterFailure()).toBe(1))
    expect(http.configureOpenRouter).toHaveBeenCalledOnce()
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

    await user.click(await screen.findByRole('combobox', { name: 'Cover model' }))
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Choose a model',
      'GPT-6 Luna (openai/gpt-6-luna)',
      'dots-studio/dots-3-note-preview:free',
      'Custom model ID',
    ])
    await user.click(screen.getByRole('option', { name: 'GPT-6 Luna (openai/gpt-6-luna)' }))
    expect(screen.getByText('Recommended. Paid.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toBeVisible()
    await pickModel(user, 'Cover model', 'dots-studio/dots-3-note-preview:free')
    expect(
      screen.getByText(/Not yet tested by Mechanical Librarian\. 20 requests\/minute/u),
    ).toBeVisible()
    await pickModel(user, 'Cover model', 'Custom model ID')
    const model = screen.getByRole('textbox', { name: 'Cover model ID' })
    await user.type(model, 'xiaomi/mimo-v2.6-flash')
    const key = screen.getByLabelText('OpenRouter API key')
    await user.type(key, 'secret-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
    await waitFor(() =>
      expect(http.configureOpenRouter).toHaveBeenCalledWith({
        expected_revision: 3,
        api_key: 'secret-key',
        model: 'xiaomi/mimo-v2.6-flash',
      }),
    )
    expect(key).toHaveValue('')
    expect(await screen.findByText('Status: ready to use')).toBeVisible()
  })

  it.each([
    [
      'an OpenRouter model',
      'openrouter',
      'openai/gpt-4o-mini',
      'openai/gpt-4o-mini on OpenRouter',
      'openai/gpt-4o-mini on OpenRouter',
    ],
    ['Codex with the profile model', 'codex', '', 'Codex (gpt-5.6-sol)', 'Codex (gpt-5.6-sol)'],
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

      const line = await within(
        await screen.findByRole('region', { name: 'Cover reviewer' }),
      ).findByText(/^Selected:/u)
      expect(line).toHaveTextContent(expected)
      // Only the engine, and a model it actually has, carry the emphasis: the
      // explanation of an absent model stays plain text.
      expect(line.querySelector('strong')).toHaveTextContent(emphasised)
      expect(line.querySelector('strong')?.textContent).toBe(emphasised)
      const allowance = screen.getByText(/Reviews count against your plan’s usage limits/)
      expect(allowance).toHaveTextContent('so no cost is shown')
      expect(allowance).not.toHaveTextContent('$')
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

    const region = await screen.findByRole('region', { name: 'Cover reviewer' })
    const cover = within(region)
    const facts = region.querySelector('.settings-reviewer-facts')
    expect(facts).not.toBeNull()
    // The terms read exactly as the description list's do; only the sentences
    // are cover-specific.
    expect(cover.getAllByRole('term').map((term) => term.textContent)).toEqual([
      'Model',
      'Capabilities',
      'Prices in USD',
      'Example cost per 1,000 works',
      'Cover request',
    ])
    expect(cover.getByText('openai/gpt-4o-mini', { selector: 'dd' })).toBeVisible()
    expect(cover.getByText(/Image input, text output and structured output/u)).toBeVisible()
    expect(cover.getByText('Validate this model to see its prices.')).toBeVisible()
    expect(cover.getByText('Cover review: not measured for this model.')).toBeVisible()
    expect(cover.getByText('Description advice: not measured for this model.')).toBeVisible()
    expect(cover.getByText('One request per work, with every cover it compares.')).toBeVisible()
    expect(cover.queryByText(/Metadata observed|Maximum-size|sample|methodology/u)).toBeNull()
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
    screen.getByLabelText('OpenRouter API key').focus()
    await userEvent.setup().tab()
    expect(cover.getByRole('button', { name: 'Refresh current status' })).toHaveFocus()
  })

  it('tracks the typed cover model in the disclosure before anything is saved', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    renderRoute('/settings?group=reviewer', http)

    const cover = within(await screen.findByRole('region', { name: 'Cover reviewer' }))
    expect(cover.getByText(/None yet\. Choose a model above/u)).toBeVisible()
    await pickModel(user, 'Cover model', 'Custom model ID')
    const model = cover.getByRole('textbox', { name: 'Cover model ID' })
    await user.type(model, 'openai/gpt-4o')
    expect(cover.getByText('openai/gpt-4o', { selector: 'dd' })).toBeVisible()
    expect(cover.queryByText(/None yet\. Choose a model above/u)).toBeNull()
    // Clearing the field returns the row to the instruction rather than to a
    // model the page no longer offers.
    await user.clear(model)
    expect(cover.getByText(/None yet\. Choose a model above/u)).toBeVisible()
  })

  it('states an absent cover model in the disclosure instead of a blank fact', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    renderRoute('/settings?group=reviewer', http)

    const cover = within(await screen.findByRole('region', { name: 'Cover reviewer' }))
    expect(
      await cover.findByText('None yet. Choose a model above.', { selector: 'dd' }),
    ).toBeVisible()
    expect(cover.getByText('Cover review: not measured for this model.')).toBeVisible()
  })

  // Each terminal state reads in words, never as its code (#1286).
  const terminalWords = {
    usable: 'ready to use',
    expired: 'sign-in expired',
    cancelled: 'sign-in cancelled',
    interrupted: 'interrupted',
    failed: 'failed',
  } as const
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
    expect(await screen.findByText(`Status: ${terminalWords[state]}`)).toBeVisible()
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
    http.getCodexDevice = vi.fn(async (): Promise<CodexDeviceAuthState> => ({
      state: 'issued',
      failure: null,
      verification_url: 'https://auth.openai.com/codex/device',
      user_code: 'ABCD-EFGH',
      expires_in_seconds: 900,
    }))

    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    onTestFinished(() => {
      Reflect.deleteProperty(navigator, 'clipboard')
    })
    const { router } = renderRoute('/settings?group=reviewer', http)
    expect(await screen.findByText('ABCD-EFGH')).toBeVisible()
    expect(screen.getByRole('link', { name: 'the device sign-in page' })).toHaveAttribute(
      'target',
      '_blank',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }))
    expect(await screen.findByText('Copied.')).toBeVisible()
    expect(writeText).toHaveBeenCalledWith('ABCD-EFGH')
    expect(screen.getByRole('button', { name: 'Cancel sign-in' })).toBeVisible()
    await act(async () => router.navigate({ to: '/library', search: { lens: 'all' } }))
    expect(screen.queryByText('ABCD-EFGH')).not.toBeInTheDocument()

    await act(async () => router.navigate({ to: '/settings', search: { group: 'reviewer' } }))
    // The remounted panel resumes from the cached flow, then refreshes it once.
    expect(screen.getByText('ABCD-EFGH')).toBeVisible()
    await waitFor(() => expect(http.getCodexDevice).toHaveBeenCalledTimes(2))
  })

  // #1607: the start seeds access in an in-progress state, so leaving the flow
  // must re-read access. The server reports the passive state after the flow
  // as `ready` (before #1598) or as the outcome itself (#1598); the panel reads
  // the same either way.
  const codexDevice = (
    state: CodexDeviceAuthState['state'],
    failure: CodexDeviceAuthState['failure'] = null,
  ): CodexDeviceAuthState => ({
    state,
    failure,
    verification_url: state === 'waiting' ? 'https://auth.openai.com/codex/device' : null,
    user_code: state === 'waiting' ? 'ABCD-1234' : null,
    expires_in_seconds: state === 'waiting' ? 900 : null,
  })
  const startedAccess: ReviewerAccessView = {
    ...reviewerView,
    codex: { ...reviewerView.codex, state: 'starting', reason: null },
  }
  it.each([
    ['main', 'ready'],
    ['#1598', 'cancelled'],
  ] as const)(
    'closes the waiting branch after a Codex sign-in is cancelled (%s server shape)',
    async (_shape, passive) => {
      const user = userEvent.setup()
      const http = createHttp()
      http.getSettings = vi.fn(async () => view())
      http.getReviewerAccess = vi
        .fn<ReviewHttp['getReviewerAccess']>()
        .mockResolvedValueOnce(reviewerView)
        .mockResolvedValue({
          ...reviewerView,
          codex: { ...reviewerView.codex, state: passive, reason: null },
        })
      http.startCodexDevice = vi.fn(async () => ({
        access: startedAccess,
        device: codexDevice('starting'),
      }))
      http.getCodexDevice = vi.fn(async () => codexDevice('waiting'))
      http.cancelCodexDevice = vi.fn(async () => codexDevice('cancelled'))
      renderRoute('/settings?group=reviewer', http)

      // Driven from the keyboard: each pressed button unmounts on success, and
      // focus moves to the next control instead of falling to the page.
      ;(await screen.findByRole('button', { name: 'Restart device sign-in' })).focus()
      await user.keyboard('{Enter}')
      expect(await screen.findByText('ABCD-1234')).toBeVisible()
      const cancel = screen.getByRole('button', { name: 'Cancel sign-in' })
      await waitFor(() => expect(cancel).toHaveFocus())
      await user.keyboard('{Enter}')

      const restart = await screen.findByRole('button', { name: 'Restart device sign-in' })
      await waitFor(() => expect(restart).toHaveFocus())
      expect(screen.queryByRole('button', { name: 'Cancel sign-in' })).toBeNull()
      expect(restart).toBeEnabled()
      expect(restart).not.toHaveAttribute('aria-disabled')
      expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toBeEnabled()
      expect(screen.getByText('Status: sign-in cancelled')).toBeVisible()
      expect(http.cancelCodexDevice).toHaveBeenCalledOnce()
      const cancelledAt = vi.mocked(http.cancelCodexDevice).mock.invocationCallOrder[0] ?? Infinity
      expect(
        vi
          .mocked(http.getReviewerAccess)
          .mock.invocationCallOrder.filter((order) => order > cancelledAt).length,
      ).toBeGreaterThan(0)
    },
  )

  it.each([
    ['usable', null, 'main'],
    ['usable', null, '#1598'],
    ['expired', null, 'main'],
    ['expired', null, '#1598'],
    ['failed', 'permission_or_process', 'main'],
    ['failed', 'permission_or_process', '#1598'],
  ] as const)(
    'closes the waiting branch when the Codex sign-in poll reports %s (%s, %s server shape)',
    async (state, failure, shape) => {
      const user = userEvent.setup()
      const http = createHttp()
      http.getSettings = vi.fn(async () => view())
      http.getReviewerAccess = vi
        .fn<ReviewHttp['getReviewerAccess']>()
        .mockResolvedValueOnce(reviewerView)
        .mockResolvedValue({
          ...reviewerView,
          codex: {
            ...reviewerView.codex,
            state: shape === 'main' ? 'ready' : state,
            reason: shape === 'main' ? null : failure,
          },
        })
      http.startCodexDevice = vi.fn(async () => ({
        access: startedAccess,
        device: codexDevice('starting'),
      }))
      http.getCodexDevice = vi
        .fn<ReviewHttp['getCodexDevice']>()
        .mockResolvedValueOnce(codexDevice('waiting'))
        .mockResolvedValue(codexDevice(state, failure))
      renderRoute('/settings?group=reviewer', http)

      await user.click(await screen.findByRole('button', { name: 'Restart device sign-in' }))
      expect(await screen.findByText('ABCD-1234')).toBeVisible()
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Cancel sign-in' })).toHaveFocus(),
      )
      await waitFor(
        () => expect(screen.queryByRole('button', { name: 'Cancel sign-in' })).toBeNull(),
        { timeout: 3000 },
      )
      expect(screen.queryByText('ABCD-1234')).toBeNull()
      // The focused Cancel unmounts; focus moves to the outcome's control.
      await waitFor(() =>
        expect(
          screen.getByRole('button', {
            name: state === 'usable' ? 'Replace Codex sign-in' : 'Restart device sign-in',
          }),
        ).toHaveFocus(),
      )
      if (state === 'usable') {
        expect(
          await screen.findByRole('button', { name: 'Use Codex for cover review' }),
        ).toBeEnabled()
        expect(screen.getByRole('button', { name: 'Replace Codex sign-in' })).toBeEnabled()
        expect(screen.getByRole('button', { name: 'Clear Codex sign-in' })).toBeEnabled()
        expect(screen.getByText('Status: ready to use')).toBeVisible()
        return
      }
      expect(await screen.findByRole('button', { name: 'Restart device sign-in' })).toBeEnabled()
      expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toBeEnabled()
      if (failure) {
        expect(screen.getAllByRole('alert')).toHaveLength(1)
        expect(screen.getByRole('alert')).toHaveTextContent(
          'Codex refused or could not finish device sign-in. Restart the sign-in flow.',
        )
      }
    },
  )

  it('reads access once when a finished sign-in leaves it unavailable (#1607)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(reviewerView)
      .mockRejectedValue(new ApiError(503, 'unavailable'))
    http.startCodexDevice = vi.fn(async () => ({
      access: startedAccess,
      device: codexDevice('starting'),
    }))
    http.getCodexDevice = vi
      .fn<ReviewHttp['getCodexDevice']>()
      .mockResolvedValueOnce(codexDevice('waiting'))
      .mockResolvedValue(codexDevice('usable', null))
    renderRoute('/settings?group=reviewer', http)

    ;(await screen.findByRole('button', { name: 'Restart device sign-in' })).focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('ABCD-1234')).toBeVisible()
    await waitFor(() => expect(http.getReviewerAccess).toHaveBeenCalledTimes(2), {
      timeout: 3000,
    })
    // The focused Cancel left with the panel; Try again is the next control (#1617).
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus())
    await new Promise((resolve) => setTimeout(resolve, 500))
    expect(http.getReviewerAccess).toHaveBeenCalledTimes(2)
  })

  // #1617: Check re-reads access, so the panel shows what the server recorded
  // instead of a view the check has made stale.
  const usableAccess: ReviewerAccessView = {
    ...reviewerView,
    codex: { ...reviewerView.codex, state: 'usable', reason: null },
  }
  const pressCheck = async (user: ReturnType<typeof userEvent.setup>) => {
    ;(await screen.findByRole('button', { name: 'Check existing Codex sign-in' })).focus()
    await user.keyboard('{Enter}')
  }
  it('shows a sign-in running in another tab after Check (#1617)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(reviewerView)
      .mockResolvedValue({
        ...reviewerView,
        codex: { ...reviewerView.codex, state: 'waiting', reason: null },
      })
    http.recheckCodex = vi.fn(async () => codexDevice('waiting'))
    http.getCodexDevice = vi.fn(async () => codexDevice('waiting'))
    renderRoute('/settings?group=reviewer', http)

    await pressCheck(user)
    expect(await screen.findByText('ABCD-1234', {}, { timeout: 3000 })).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Cancel sign-in' })).toHaveFocus(),
    )
    expect(screen.queryByRole('button', { name: 'Check existing Codex sign-in' })).toBeNull()
  })

  it('withdraws Use Codex when Check finds the sign-in refused (#1617)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(usableAccess)
      .mockResolvedValue(reviewerView)
    http.recheckCodex = vi.fn(async () => codexDevice('failed', 'permission_or_profile'))
    renderRoute('/settings?group=reviewer', http)
    expect(await screen.findByRole('button', { name: 'Use Codex for cover review' })).toBeEnabled()

    await pressCheck(user)
    expect(await screen.findByText('Status: failed')).toBeVisible()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The installed Codex sign-in could not be validated. Restart device sign-in, or check the existing sign-in.',
    )
    expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Use Codex for cover review' })).toBeNull()
    // React keeps the pressed Check's node across the branch switch, so focus stays on it.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toHaveFocus(),
    )
  })

  // #1663: Check reads the recorded access before it writes its own result, so
  // the row never shows the checked failure over the stale usable view first.
  it.each(['fresh usable', 'Check-cached usable'] as const)(
    'announces one alert when Check finds the sign-in refused (%s, #1663)',
    async (shape) => {
      const unmanaged =
        'The installed Codex sign-in could not be validated. Restart device sign-in, or check the existing sign-in.'
      const user = userEvent.setup()
      const http = createHttp()
      http.getSettings = vi.fn(async () => view())
      let release!: (value: ReviewerAccessView) => void
      const heldRead = new Promise<ReviewerAccessView>((resolve) => (release = resolve))
      const reads = vi.fn<ReviewHttp['getReviewerAccess']>().mockResolvedValueOnce(usableAccess)
      const checks = vi.fn<ReviewHttp['recheckCodex']>()
      if (shape === 'Check-cached usable') {
        reads.mockResolvedValueOnce(usableAccess)
        checks.mockResolvedValueOnce(codexDevice('usable'))
      }
      reads.mockImplementation(() => heldRead)
      checks.mockResolvedValue(codexDevice('failed', 'permission_or_profile'))
      http.getReviewerAccess = reads
      http.recheckCodex = checks
      renderRoute('/settings?group=reviewer', http)
      const check = await screen.findByRole('button', { name: 'Check existing Codex sign-in' })
      if (shape === 'Check-cached usable') {
        await pressCheck(user)
        await waitFor(() => expect(reads).toHaveBeenCalledTimes(2))
        await waitFor(() => expect(check).not.toHaveAttribute('aria-disabled'))
      }
      const readsBefore = reads.mock.calls.length
      const recorded: string[] = []
      const observer = new MutationObserver(() => {
        for (const alert of window.document.body.querySelectorAll('[role=alert]')) {
          const text = alert.textContent
          if (!recorded.includes(text)) recorded.push(text)
        }
      })
      observer.observe(window.document.body, {
        childList: true,
        subtree: true,
        characterData: true,
      })
      onTestFinished(() => observer.disconnect())

      await pressCheck(user)
      await waitFor(() => expect(reads.mock.calls.length).toBe(readsBefore + 1))
      await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
      expect(screen.queryByText(/Replace the sign-in and retry/u)).toBeNull()

      await act(async () => release(reviewerView))
      expect(await screen.findByText('Status: failed')).toBeVisible()
      await waitFor(() => expect(check).not.toHaveAttribute('aria-disabled'))
      expect(screen.getAllByRole('alert')).toHaveLength(1)
      expect(screen.getByRole('alert')).toHaveTextContent(unmanaged)
      expect(recorded).toEqual([unmanaged])
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toHaveFocus(),
      )
      // One Check reads access once, even after the held read settles.
      expect(reads.mock.calls.length).toBe(readsBefore + 1)
    },
  )

  // The second row is a server that skipped recording the check (criterion 4):
  // the reason still follows the shown, checked state.
  it.each([
    ['recorded', usableAccess],
    ['not recorded', reviewerView],
  ] as const)(
    'leaves no stale warning under ready to use after a usable Check (%s, #1617)',
    async (_record, after) => {
      const user = userEvent.setup()
      const http = createHttp()
      http.getSettings = vi.fn(async () => view())
      http.getReviewerAccess = vi
        .fn<ReviewHttp['getReviewerAccess']>()
        .mockResolvedValueOnce(reviewerView)
        .mockResolvedValue(after)
      http.recheckCodex = vi.fn(async () => codexDevice('usable'))
      renderRoute('/settings?group=reviewer', http)
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The installed Codex sign-in could not be validated.',
      )

      await pressCheck(user)
      expect(await screen.findByText('Status: ready to use')).toBeVisible()
      await waitFor(() => expect(http.getReviewerAccess).toHaveBeenCalledTimes(2))
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.getByRole('button', { name: 'Use Codex for cover review' })).toBeEnabled()
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toHaveFocus(),
      )
    },
  )

  it('labels a cancelled sign-in ready to sign in after Check (#1617, #1607 O3)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const passive = (state: 'cancelled' | 'ready'): ReviewerAccessView => ({
      ...reviewerView,
      codex: { ...reviewerView.codex, state, reason: null },
    })
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(passive('cancelled'))
      .mockResolvedValue(passive('ready'))
    http.recheckCodex = vi.fn(async () => codexDevice('ready'))
    renderRoute('/settings?group=reviewer', http)
    expect(await screen.findByText('Status: sign-in cancelled')).toBeVisible()

    await pressCheck(user)
    expect(await screen.findByText('Status: ready to sign in')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Replace or create Codex sign-in' })).toBeVisible()
  })

  it('focuses Try again when the read after Check fails (#1617)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(reviewerView)
      .mockRejectedValue(new ApiError(503, 'unavailable'))
    http.recheckCodex = vi.fn(async () => codexDevice('usable'))
    renderRoute('/settings?group=reviewer', http)

    await pressCheck(user)
    expect(await screen.findByText('Reviewer settings could not be loaded.')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus())
    expect(http.getReviewerAccess).toHaveBeenCalledTimes(2)
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
    await screen.findByRole('combobox', { name: 'Cover model' })
    await pickModel(user, 'Cover model', 'dots-studio/dots-3-note-preview:free')
    await user.type(screen.getByLabelText('OpenRouter API key'), 'secret-key')
    await user.click(screen.getByRole('button', { name: 'Validate and use OpenRouter' }))
    expect(screen.getByText('Status: checking')).toBeVisible()
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
    expect(await screen.findByText('Status: ready to use')).toBeVisible()
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
    expect(
      await within(await screen.findByRole('region', { name: 'Cover reviewer' })).findByText(
        /^Selected:/u,
      ),
    ).toHaveTextContent('No reviewer')
    expect(await screen.findByText('Status: ready to use')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Use Codex for cover review' }))
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
      'Mechanical Librarian cannot read the host’s free memory. Fix the host or container memory reporting before using Codex.',
    ],
    [
      'host_unsupported',
      'cgroup_limit_below_1_gib',
      true,
      'The container memory limit is below 1 GB. Increase it before using Codex.',
    ],
    [
      'host_unsupported',
      'headroom_below_1_gib',
      true,
      'Less than 1 GB of memory is free. Free some memory or raise the container limit.',
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
      expect(screen.getByRole('button', { name: 'Use Codex for cover review' })).toBeDisabled()
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

  it('names the offered controls for an unmanaged Codex sign-in that cannot be validated', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => reviewerView)
    renderRoute('/settings?group=reviewer', http)

    expect(await screen.findByRole('button', { name: 'Restart device sign-in' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Check existing Codex sign-in' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Replace Codex sign-in' })).toBeNull()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The installed Codex sign-in could not be validated. Restart device sign-in, or check the existing sign-in.',
    )
  })

  it.each([
    ['response_too_large', /answer too large to read\. Retry later, or choose another model\./u],
    ['timed_out', /did not answer in time\. Check network access, then retry\./u],
    [
      'data_policy_refused',
      /Change your privacy settings on OpenRouter, or choose another model\./u,
    ],
    ['image_limit_exceeded', /fewer images than a cover review sends\. Choose another vision/u],
  ] as const)('says what to change for an OpenRouter %s status', async (state, copy) => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      mapping: { engine: 'openrouter' as const, model: 'provider/model' },
      openrouter_key_configured: true,
      openrouter: {
        ...reviewerView.openrouter,
        state,
        reason: state,
        model: 'provider/model',
        tier: null,
      },
    }))
    renderRoute('/settings?group=reviewer', http)
    expect(await screen.findByText(copy)).toHaveAttribute('role', 'alert')
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
    await screen.findByText('Status: not supported on this host')

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
      'Less than 1 GB of memory is free. Free some memory or raise the container limit.',
    )
    expect(
      screen.queryByText(/installed Codex sign-in could not be validated/u),
    ).not.toBeInTheDocument()
    // The bound and the headroom beside it read in one unit (#1286).
    expect(screen.getByText('Free memory for Codex: 2 GB')).toBeVisible()
    expect(window.document.body).not.toHaveTextContent(/GiB|MiB/u)
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
    expect(await screen.findByText('Status: ready to use')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clear Codex sign-in' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Codex sign-in' }))
    expect(await screen.findByText('Status: ready to sign in')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Use Codex for cover review' }),
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

      await screen.findByRole('combobox', { name: 'Cover model' })
      const key = screen.getByLabelText('OpenRouter API key')
      await user.type(key, 'replacement-key')
      expect(key).toHaveValue('replacement-key')
      act(() => {
        const pending = [...frames.values()]
        frames.clear()
        for (const frame of pending) frame(0)
      })
      expect(screen.getByLabelText('OpenRouter API key')).toBe(key)
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

  it('keeps one draft across sections with true per-section counts that clear on revert', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings', http)
    const nav = within(await screen.findByRole('navigation', { name: 'Settings sections' }))
    const endpoint = screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })
    expect(screen.queryByRole('button', { name: 'Save settings' })).toBeNull()

    await user.clear(endpoint)
    await user.type(endpoint, 'https://other.invalid')
    expect(nav.getByRole('button', { name: /Audiobookshelf/ })).toHaveAccessibleDescription(
      '1 changed field',
    )
    const row = endpoint.closest('.settings-field-row')
    expect(row).toHaveClass('is-changed')
    // The same compact pill as every other state chip (#1289).
    expect(row?.querySelector('.status-pill.is-notice')).toHaveTextContent('Changed')
    await user.click(nav.getByRole('button', { name: /Appearance/ }))
    await user.click(segment('Motion', 'Always reduced'))
    expect(screen.getByText('2 unsaved changes')).toBeVisible()
    expect(nav.getByRole('button', { name: /Appearance/ })).toHaveAccessibleDescription(
      '1 changed field',
    )

    await user.click(nav.getByRole('button', { name: /Audiobookshelf/ }))
    const kept = screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })
    expect(kept).toHaveValue('https://other.invalid')
    await user.clear(kept)
    await user.type(kept, 'https://library.invalid')
    expect(nav.getByRole('button', { name: /Audiobookshelf/ })).toHaveAccessibleDescription(
      '0 changed fields',
    )
    expect(kept.closest('.settings-field-row')).not.toHaveClass('is-changed')
    expect(screen.getByText('1 unsaved change')).toBeVisible()
    await user.click(nav.getByRole('button', { name: /Appearance/ }))
    await user.click(segment('Motion', 'Follow system'))
    expect(screen.queryByRole('button', { name: 'Save settings' })).toBeNull()
    expect(http.writeSettings).not.toHaveBeenCalled()
  })

  it('saves edits from several sections in one revision-checked write', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let savedRequest: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      savedRequest = request
      return { ...view(), document: { ...savedDocument(request.document), revision: 4 } }
    })
    renderRoute('/settings?group=providers', http)
    await screen.findByRole('group', { name: 'Use Hardcover' })
    await user.click(segment('Use Hardcover', 'Off'))
    await user.click(screen.getByRole('button', { name: 'Clear Hardcover API key' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Hardcover API key' }))
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    await user.click(segment('Motion', 'Always reduced'))
    expect(screen.getByText('4 unsaved changes')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    const review = screen.getByRole('list')
    expect(review).toHaveTextContent('Hardcover API key')
    expect(review).toHaveTextContent('Will be cleared')
    expect(review).not.toHaveTextContent('abcd')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(savedRequest?.expected_revision).toBe(3)
    expect(savedRequest?.document.reduced_motion).toBe('always_reduced')
    expect(savedRequest?.secrets).toEqual([
      { field_id: 'provider.hardcover.key', operation: 'clear' },
    ])
    expect(await screen.findByText('Settings saved and in use.')).toBeVisible()
  })

  it('validates sections that are not on screen and takes the owner to the first problem', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings', http)
    await user.click(await screen.findByRole('button', { name: 'Clear Audiobookshelf access key' }))
    await user.click(
      screen.getByRole('button', { name: 'Confirm clear Audiobookshelf access key' }),
    )
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    await user.click(segment('Motion', 'Always reduced'))
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    expect(await screen.findByRole('heading', { level: 2, name: 'Audiobookshelf' })).toBeVisible()
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Keep configured Audiobookshelf access key' }),
      ).toHaveFocus(),
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Audiobookshelf needs an access key.')
    expect(http.writeSettings).not.toHaveBeenCalled()
  })

  it('reopens a pending key replacement when the review list links back to it', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=providers', http)
    await user.click(await screen.findByRole('button', { name: 'Replace Hardcover API key' }))
    await user.type(screen.getByLabelText('Replacement Hardcover API key'), 'new-secret')
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    await user.click(
      within(screen.getByRole('list')).getByRole('button', { name: /Hardcover API key/ }),
    )

    const replacement = await screen.findByLabelText('Replacement Hardcover API key')
    expect(replacement).toHaveValue('new-secret')
    await waitFor(() => expect(replacement).toHaveFocus())
  })

  it('turns a pending replacement into a set when newer settings cleared the key', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const cleared: SettingsView = {
      ...view(),
      document: { ...document, revision: 4 },
      secrets: view().secrets.filter((item) => item.field_id !== 'provider.hardcover.key'),
    }
    http.getSettings = vi.fn().mockResolvedValueOnce(view()).mockResolvedValue(cleared)
    http.writeSettings = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, 'settings_revision_stale'))
      .mockResolvedValue({ ...cleared, document: { ...document, revision: 5 } })
    renderRoute('/settings?group=providers', http)
    await user.click(await screen.findByRole('button', { name: 'Replace Hardcover API key' }))
    await user.type(screen.getByLabelText('Replacement Hardcover API key'), 'new-secret')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await screen.findByText(/Your changes were kept/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledTimes(2))
    expect(http.writeSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({
        expected_revision: 4,
        secrets: [{ field_id: 'provider.hardcover.key', operation: 'set', value: 'new-secret' }],
      }),
    )
  })

  it('discards every section at once and announces it', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings', http)
    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await user.type(endpoint, '/edited')
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    await user.click(segment('Motion', 'Always reduced'))
    await user.click(screen.getByRole('button', { name: 'Discard all' }))

    expect(segment('Motion', 'Follow system')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('All unsaved settings changes were discarded.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save settings' })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Audiobookshelf/ }))
    expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveValue(
      'https://library.invalid',
    )
    expect(http.writeSettings).not.toHaveBeenCalled()
  })

  it('links a change in the review list back to a control in a closed advanced section', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=background', http)
    await user.click(await screen.findByText('Response and file size limits'))
    const archive = screen.getByRole('spinbutton', { name: 'Ebook file limit' })
    await user.clear(archive)
    await user.type(archive, '1048576')
    await user.click(screen.getByText('Response and file size limits'))
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    await user.click(
      within(screen.getByRole('list')).getByRole('button', { name: /Ebook file limit/ }),
    )

    expect(await screen.findByRole('heading', { level: 2, name: 'Background work' })).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Ebook file limit' })).toHaveFocus(),
    )
  })

  it.each([
    [
      new ApiError(422, 'invalid_settings'),
      /^Nothing was saved\. Check the values and save again\.$/,
    ],
    [new CsrfRenewedError(), /^Your session was refreshed\. Press again\.$/],
  ])('keeps every edit when the server rejects a save with %s', async (failure, copy) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async () => {
      throw failure
    })
    renderRoute('/settings', http)
    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await user.type(endpoint, '/edited')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    expect(await screen.findByText(copy)).toBeVisible()
    expect(endpoint).toHaveValue('https://library.invalid/edited')
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled()
  })

  it('offers a compact section selector that carries change counts', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const { router } = renderRoute('/settings?group=appearance', http)
    await user.click(await screen.findByRole('button', { name: 'Always reduced' }))
    await user.click(screen.getByRole('combobox', { name: 'Settings section' }))
    expect(screen.getByRole('option', { name: 'Appearance · 1 changed' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Libraries' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Libraries' })).toBeVisible()
    expect(router.state.location.search).toEqual({ group: 'libraries' })
    expect(screen.getByText('1 unsaved change')).toBeVisible()
  })

  // Sizes read in MB (1,048,576 bytes) and polling and sign-in length in
  // minutes; the document still keeps whole bytes and seconds (#1286, A5).
  it('shows sizes in MB and polling in minutes', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const { router } = renderRoute('/settings?group=background', http)

    const polling = await screen.findByRole('spinbutton', { name: 'Library polling interval' })
    expect(polling).toHaveValue(15)
    expect(polling).toHaveAttribute('min', '1')
    expect(polling).toHaveAttribute('max', '1440')
    expect(polling.parentElement).toHaveTextContent('minutes')
    expect(screen.getByRole('spinbutton', { name: 'Request timeout' })).toHaveValue(30)
    await user.click(screen.getByText('Response and file size limits'))
    expect(screen.getByRole('spinbutton', { name: 'Audiobookshelf response limit' })).toHaveValue(1)
    // 64 KiB is exactly 0.0625 MB, and the hint says 64 KB beside it.
    const source = screen.getByRole('spinbutton', { name: 'Source response limit' })
    expect(source).toHaveValue(0.0625)
    expect(source.parentElement).toHaveTextContent('MB (64 KB)')
    // The unit beside each number is announced with it, not only seen.
    const spinbuttons = screen.getAllByRole('spinbutton')
    expect(spinbuttons.length).toBeGreaterThan(3)
    for (const input of spinbuttons) {
      const unit = input.parentElement?.querySelector('small')
      expect(unit?.textContent).toMatch(/^(MB|seconds|minutes)/u)
      expect(input.getAttribute('aria-describedby')?.split(' ')).toContain(unit?.id)
      expect(input).toHaveAccessibleDescription(
        expect.stringMatching(
          new RegExp(`^${(unit?.textContent ?? '').replace(/[()]/gu, '\\$&')} `, 'u'),
        ),
      )
    }
    expect(screen.getByRole('spinbutton', { name: 'Ebook file limit' })).toHaveValue(256)
    expect(
      screen.getByRole('spinbutton', { name: 'Ebook file limit' }).parentElement,
    ).toHaveTextContent('MB')
    expect(window.document.body).not.toHaveTextContent(/\bbytes\b|KiB|MiB/u)

    await act(async () => router.navigate({ to: '/settings', search: { group: 'access' } }))
    expect(await screen.findByRole('spinbutton', { name: 'Sign-in length' })).toHaveValue(43_200)
  })

  it('saves an untouched document byte-identically', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    // Values no display unit divides evenly survive a save they were not part of.
    const awkward = {
      ...document,
      limits: { ...document.limits, provider_response_bytes: 65_537, target_response_bytes: 1_025 },
      server: { ...document.server, library_polling_interval_seconds: 901 },
    }
    let saved: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => ({ ...view(), document: awkward }))
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      saved = request
      return { ...view(), document: savedDocument(request.document) }
    })
    renderRoute('/settings', http)
    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await user.type(endpoint, '/edited')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(saved?.document.limits).toEqual(awkward.limits)
    expect(saved?.document.server).toEqual(awkward.server)
  })

  it('writes an edited MB or minute value as exact whole bytes or seconds', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let saved: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      saved = request
      return { ...view(), document: savedDocument(request.document) }
    })
    renderRoute('/settings?group=background', http)

    const polling = await screen.findByRole('spinbutton', { name: 'Library polling interval' })
    await user.clear(polling)
    await user.type(polling, '2.5')
    await user.click(screen.getByText('Response and file size limits'))
    const provider = screen.getByRole('spinbutton', { name: 'Source response limit' })
    await user.clear(provider)
    await user.type(provider, '0.2')
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    expect(screen.getByRole('list')).toHaveTextContent('15 minutes')
    expect(screen.getByRole('list')).toHaveTextContent('2.5 minutes')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(saved?.document.server.library_polling_interval_seconds).toBe(150)
    expect(saved?.document.limits.provider_response_bytes).toBe(209_715)
    expect(Number.isInteger(saved?.document.limits.provider_response_bytes)).toBe(true)
    expect(saved?.document.limits.target_response_bytes).toBe(document.limits.target_response_bytes)
  })

  it('reads a size below one MB exactly, with its KB beside it, and keeps its bytes', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let saved: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      saved = request
      return { ...view(), document: savedDocument(request.document) }
    })
    renderRoute('/settings?group=background', http)

    await user.click(await screen.findByText('Response and file size limits'))
    const source = screen.getByRole('spinbutton', { name: 'Source response limit' })
    expect(source).toHaveValue(0.0625)
    expect(source.parentElement).toHaveTextContent('MB (64 KB)')
    expect(source.parentElement).not.toHaveTextContent('0.063')
    // A whole MB needs no KB beside it.
    expect(
      screen.getByRole('spinbutton', { name: 'Audiobookshelf response limit' }).parentElement,
    ).not.toHaveTextContent('KB')
    // An edit to 0.125 MB writes exactly 128 KB, and the hint follows the edit.
    await user.clear(source)
    await user.type(source, '0.125')
    expect(source.parentElement).toHaveTextContent('MB (128 KB)')
    // The range error reads in the field's own unit, its exact MB reading back
    // to 1,024 bytes, with the KB beside it.
    await user.clear(source)
    await user.type(source, '0.0001')
    expect(source).toHaveAccessibleDescription(
      /Source response limit must be from 0\.000977 MB \(1 KB\) to 0\.25 MB \(256 KB\)\./u,
    )
    // What the field shows is what it saves: 0.123 MB stays 0.123, never 0.12.
    await user.clear(source)
    await user.type(source, '0.123')
    expect(source).toHaveValue(0.123)
    expect(source.parentElement).toHaveTextContent('MB (125.952 KB)')
    await user.clear(source)
    await user.type(source, '0.125')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(saved?.document.limits.provider_response_bytes).toBe(131_072)
  })

  it('states range errors in display units', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=background', http)

    const polling = await screen.findByRole('spinbutton', { name: 'Library polling interval' })
    await user.clear(polling)
    await user.type(polling, '0.5')
    expect(polling).toHaveAttribute('aria-invalid', 'true')
    expect(polling).toHaveAccessibleDescription(
      /Library polling interval must be from 1 to 1,440 minutes\./u,
    )
    await user.clear(polling)
    await user.type(polling, '1')
    expect(polling).not.toHaveAttribute('aria-invalid', 'true')
    const timeout = screen.getByRole('spinbutton', { name: 'Request timeout' })
    expect(timeout).toHaveAccessibleDescription(/1 to 60 seconds/u)
    await user.clear(timeout)
    await user.type(timeout, '61')
    expect(timeout).toHaveAccessibleDescription(/Request timeout must be from 1 to 60 seconds\./u)
  })

  it('renders and range-validates the audiobook source limit beside the archive one', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=background', http)

    await user.click(await screen.findByText('Response and file size limits'))
    const audio = screen.getByRole('spinbutton', { name: 'Audiobook file limit' })
    // Sizes read in MB (1,048,576 bytes), never as raw bytes (#1286).
    expect(audio).toHaveValue(8192)
    // Its own ceiling, not the EPUB archive's: an audiobook is read by header
    // and is routinely larger than any archive this build admits.
    expect(audio).toHaveAttribute('max', '16384')
    expect(screen.getByRole('spinbutton', { name: 'Ebook file limit' })).toHaveAttribute(
      'max',
      '256',
    )

    await user.clear(audio)
    await user.type(audio, '0.0009')
    expect(audio).toHaveAttribute('aria-invalid', 'true')
    expect(audio).toHaveAccessibleDescription(
      /Audiobook file limit must be from 0\.000977 MB \(1 KB\) to 16,384 MB \(16 GB\)\./u,
    )
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(audio).toHaveFocus())
    expect(http.writeSettings).not.toHaveBeenCalled()

    // A GB aside stops at two decimals; the MB value beside it stays exact.
    await user.clear(audio)
    await user.type(audio, '1025.3115')
    expect(audio).toHaveValue(1025.3115)
    expect(audio.parentElement).toHaveTextContent('MB (1 GB)')
    await user.clear(audio)
    await user.type(audio, '1536.5')
    expect(audio.parentElement).toHaveTextContent('MB (1.5 GB)')
    expect(audio).toHaveAccessibleDescription(/^MB \(1\.5 GB\) /u)
    await user.clear(audio)
    await user.type(audio, '2047.99')
    expect(audio.parentElement).toHaveTextContent(/MB \(2 GB\)$/u)
    await user.clear(audio)
    await user.type(audio, '1100.123')
    expect(audio.parentElement).toHaveTextContent(/MB \(1\.07 GB\)$/u)

    await user.clear(audio)
    await user.type(audio, '16385')
    expect(audio).toHaveAttribute('aria-invalid', 'true')

    await user.clear(audio)
    await user.type(audio, '16384')
    expect(audio).not.toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByText(/need fixing before saving/u)).toBeNull()
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
  expect(screen.getByRole('link', { name: /Set description advice in Policy/ })).toHaveAttribute(
    'href',
    '/policy?section=curation#description-advice',
  )
  await user.click(screen.getByRole('combobox', { name: 'Description model' }))
  await user.click(screen.getByRole('option', { name: 'Custom model ID' }))
  await user.type(screen.getByRole('textbox', { name: 'Description model ID' }), metadata.model)
  await user.click(screen.getByRole('button', { name: 'Test description model and prices' }))
  expect(
    await screen.findAllByText(
      /^(Cover review|Description advice): not measured for this model\.$/u,
    ),
  ).toHaveLength(4)
  expect(screen.getByText(metadata.model, { selector: 'dd' })).toBeVisible()
  expect(screen.queryByText('Metadata observed', { selector: 'dt' })).toBeNull()
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

it('discloses an unavailable description request price and still saves the model', async () => {
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
    request_price: null,
    text_input: true as const,
    text_output: true as const,
    structured_outputs: true as const,
  }
  const prices = '0.000001 per input token, 0.000002 per output token, Unavailable per request.'
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => initial)
  http.testDescriptionModel = vi.fn(async () => metadata)
  http.configureOpenRouter = vi.fn(async () => ({
    ...initial,
    revision: 4,
    description_mapping: { engine: 'openrouter' as const, model: metadata.model },
    description_metadata: metadata,
    description: {
      engine: 'openrouter' as const,
      state: 'usable' as const,
      reason: null,
      model: metadata.model,
      tier: 'paid' as const,
      host_headroom_bytes: null,
    },
  }))
  renderRoute('/settings?group=reviewer', http)
  const user = userEvent.setup()
  const enable = await screen.findByRole('button', {
    name: 'Use this OpenRouter model for descriptions',
  })
  await user.click(screen.getByRole('combobox', { name: 'Description model' }))
  await user.click(screen.getByRole('option', { name: 'Custom model ID' }))
  await user.type(screen.getByRole('textbox', { name: 'Description model ID' }), metadata.model)
  await user.click(screen.getByRole('button', { name: 'Test description model and prices' }))
  expect(await screen.findByText(prices, { selector: 'dd' })).toBeVisible()
  await user.click(enable)
  expect(
    await screen.findByText('Selected: example/text-b on OpenRouter. Status: ready to use.'),
  ).toBeVisible()
  expect(http.configureOpenRouter).toHaveBeenCalledWith({
    expected_revision: 3,
    api_key: '',
    model: metadata.model,
    task: 'description_rewrite',
  })
  expect(screen.getByText(prices, { selector: 'dd' })).toBeVisible()
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
  await userEvent.setup().click(screen.getByRole('button', { name: 'Check Codex model' }))
  expect(await screen.findByText('contained-model', { selector: 'dd' })).toBeVisible()
  expect(
    screen.getByText(/Advice counts against your plan’s usage limits, so no cost is shown/),
  ).toBeVisible()
  expect(use).toBeEnabled()
  const identity = screen.getByText('a'.repeat(64))
  expect(identity).not.toBeVisible()
  await userEvent.setup().click(screen.getByText('Show checksum'))
  expect(identity).toBeVisible()
  expect(
    screen.getByRole('link', { name: 'Review write permissions in Activity' }),
  ).toHaveAttribute('href', '/activity')
  expect(
    screen.getByText(/Advice is written to your library only with your write permission/),
  ).toBeVisible()
  expect(http.selectReviewer).not.toHaveBeenCalled()
})

it('reloads reviewer access and says no answer came back for a description adviser change', async () => {
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
  http.selectReviewer = vi.fn(async () => {
    throw new ApiError(503, 'reviewer_access_unavailable')
  })
  renderRoute('/settings?group=reviewer', http)
  const user = userEvent.setup()
  const use = await screen.findByRole('button', { name: 'Use Codex for descriptions' })
  await user.click(screen.getByRole('button', { name: 'Check Codex model' }))
  await waitFor(() => expect(use).toBeEnabled())
  const readsBefore = vi.mocked(http.getReviewerAccess).mock.calls.length
  await user.click(use)

  const description = screen.getByRole('region', { name: 'Description adviser' })
  const alert = await within(description).findByRole('alert')
  expect(alert).toHaveTextContent(
    'No answer came back. Check whether the description adviser change was saved before trying again.',
  )
  // The failure sits under the pressed button (#1663).
  expect(alert.previousElementSibling).toBe(use)
  await waitFor(() =>
    expect(vi.mocked(http.getReviewerAccess).mock.calls.length).toBeGreaterThan(readsBefore),
  )
})

it.each(['fresh usable', 'Check-cached usable'] as const)(
  're-reads access with Codex copy after a refused Use Codex for descriptions (%s, #1617)',
  async (shape) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const ready = { ...reviewerView, codex: { ...reviewerView.codex, state: 'ready' as const } }
    const reads = vi.fn<ReviewHttp['getReviewerAccess']>()
    if (shape === 'fresh usable')
      reads.mockResolvedValueOnce({
        ...reviewerView,
        codex: { ...reviewerView.codex, state: 'usable' as const, reason: null },
      })
    else reads.mockResolvedValueOnce(ready).mockResolvedValueOnce(ready)
    reads.mockResolvedValue(reviewerView)
    http.getReviewerAccess = reads
    http.describeContainedDescription = vi.fn(async () => ({
      model: 'contained-model',
      profile_sha256: 'a'.repeat(64),
    }))
    http.recheckCodex = vi.fn(async () => ({
      state: 'usable' as const,
      failure: null,
      verification_url: null,
      user_code: null,
      expires_in_seconds: null,
    }))
    http.selectReviewer = vi.fn(async () => {
      throw new ApiError(409, 'credential_unavailable')
    })
    renderRoute('/settings?group=reviewer', http)
    const use = await screen.findByRole('button', { name: 'Use Codex for descriptions' })
    await user.click(screen.getByRole('button', { name: 'Check Codex model' }))
    expect(await screen.findByText('contained-model', { selector: 'dd' })).toBeVisible()
    if (shape === 'Check-cached usable')
      await user.click(screen.getByRole('button', { name: 'Check existing Codex sign-in' }))
    await waitFor(() => expect(use).toBeEnabled())
    const readsBefore = vi.mocked(http.getReviewerAccess).mock.calls.length
    use.focus()
    await user.keyboard('{Enter}')

    const description = screen.getByRole('region', { name: 'Description adviser' })
    const alert = await within(description).findByRole('alert')
    expect(alert).toHaveTextContent(
      'Codex sign-in is unavailable. Check or replace the sign-in, then try again.',
    )
    // The refusal sits under Use Codex, not at the foot of the OpenRouter row (#1663).
    expect(alert.previousElementSibling).toBe(use)
    expect(within(description).queryByText(/model, key and available pricing/u)).toBeNull()
    expect(await screen.findByText('Status: failed')).toBeVisible()
    expect(screen.queryByText('Status: ready to use')).toBeNull()
    expect(use).toBeDisabled()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toHaveFocus(),
    )
    expect(vi.mocked(http.getReviewerAccess).mock.calls.length).toBeGreaterThan(readsBefore)
  },
)

it('offers a reload, not another try, when settings meet a newer build', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => {
    throw new BuildMismatchError()
  })
  renderRoute('/settings', http)

  expect(await screen.findByRole('heading', { name: 'Settings were not loaded' })).toBeVisible()
  // The shell notice states the build mismatch; the page does not repeat it.
  expect(screen.queryByText(/Mechanical Librarian was updated/u)).toBeNull()
  expect(screen.getByRole('button', { name: 'Reload' })).toBeVisible()
  expect(screen.queryByText('Settings could not be loaded')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
})

it('offers a reload, not another try, when reviewer access meets a newer build', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => {
    throw new BuildMismatchError()
  })
  renderRoute('/settings?group=reviewer', http)

  expect(
    await screen.findByText('Reviewer settings were not loaded.', { exact: false }),
  ).toBeVisible()
  expect(screen.getByRole('button', { name: 'Reload' })).toBeVisible()
  expect(screen.queryByText('Reviewer settings could not be loaded.')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
})

it.each(['mismatch', 'success'] as const)(
  'moves focus to what replaces the reviewer access Try again after a %s (#1742)',
  async (outcome) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    let reads = 0
    http.getReviewerAccess = vi.fn(async () => {
      reads += 1
      if (reads === 1) throw new ApiError(503, 'unavailable')
      if (outcome === 'mismatch') throw new BuildMismatchError()
      return reviewerView
    })
    renderRoute('/settings?group=reviewer', http)
    const alert = (
      await screen.findByText('Reviewer settings could not be loaded.')
    ).closest<HTMLElement>('[role="alert"]')
    if (!alert) throw new Error('the reviewer access alert is required')
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    const target =
      outcome === 'mismatch'
        ? await screen.findByRole('button', { name: 'Reload' })
        : await screen.findByRole('heading', { name: 'Shared Codex sign-in' })
    await waitFor(() => expect(target).toHaveFocus())
    expect(globalThis.document.body).not.toHaveFocus()
  },
)

it('keeps focus off the page while the reviewer access Try again reads (#1742)', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  let release: () => void = () => undefined
  let reads = 0
  http.getReviewerAccess = vi.fn(async () => {
    reads += 1
    if (reads === 1) throw new ApiError(503, 'unavailable')
    await new Promise<void>((resolve) => {
      release = resolve
    })
    return reviewerView
  })
  renderRoute('/settings?group=reviewer', http)
  const alert = (
    await screen.findByText('Reviewer settings could not be loaded.')
  ).closest<HTMLElement>('[role="alert"]')
  if (!alert) throw new Error('the reviewer access alert is required')
  await user.click(within(alert).getByRole('button', { name: 'Try again' }))
  // A first-read retry renders the loading line in place of the alert.
  const reading = await screen.findByText('Loading reviewer settings…')
  expect(globalThis.document.body).not.toHaveFocus()
  expect(globalThis.document.activeElement).toContainElement(reading)
  release()
  const heading = await screen.findByRole('heading', { name: 'Shared Codex sign-in' })
  await waitFor(() => expect(heading).toHaveFocus())
})

// The disclosure writes nothing: an answer it cannot read keeps the setup
// copy, though the client reports it as uncertain (#1690).
it.each([
  new ApiError(503, 'unreachable'),
  new UncertainMutationError({
    operation: 'POST /api/v1/reviewer-access/description-model/contained',
    responseStatus: 200,
    at: '/profile_sha256',
    reason: 'pattern',
  }),
])(
  'replaces an unconfirmed description adviser change with a later inspection failure (%s)',
  async (inspectionFailure) => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => ({
      ...reviewerView,
      codex: { ...reviewerView.codex, state: 'usable' as const },
    }))
    http.describeContainedDescription = vi
      .fn()
      .mockResolvedValueOnce({ model: 'contained-model', profile_sha256: 'a'.repeat(64) })
      .mockRejectedValue(inspectionFailure)
    http.selectReviewer = vi.fn(async () => {
      throw new ApiError(503, 'reviewer_access_unavailable')
    })
    renderRoute('/settings?group=reviewer', http)
    const user = userEvent.setup()
    const use = await screen.findByRole('button', { name: 'Use Codex for descriptions' })
    const inspect = screen.getByRole('button', { name: 'Check Codex model' })
    await user.click(inspect)
    await waitFor(() => expect(use).toBeEnabled())
    await user.click(use)
    const description = screen.getByRole('region', { name: 'Description adviser' })
    const unconfirmed = await within(description).findByRole('alert')
    expect(unconfirmed).toHaveTextContent(
      'No answer came back. Check whether the description adviser change was saved',
    )
    expect(unconfirmed.previousElementSibling).toBe(use)

    // Pending is aria-disabled now, so the native state no longer shows settling (#1663).
    await waitFor(() => expect(inspect).not.toHaveAttribute('aria-disabled'))
    await user.click(inspect)
    await waitFor(() =>
      expect(within(description).getByRole('alert')).toHaveTextContent(
        'Description adviser setup did not complete.',
      ),
    )
    expect(within(description).getAllByRole('alert')).toHaveLength(1)
    expect(within(description).getByRole('alert')).not.toHaveTextContent('No answer came back')
    expect(within(description).getByRole('alert').previousElementSibling).toBe(inspect)
    expect(http.describeContainedDescription).toHaveBeenCalledTimes(2)
  },
)

// The model test writes nothing either: an unreadable answer keeps the setup
// copy and reloads nothing (#1690).
it.each([
  new ApiError(503, 'unreachable'),
  new UncertainMutationError({
    operation: 'POST /api/v1/reviewer-access/description-model/test',
    responseStatus: 200,
    at: 'invalid_description_metadata',
    reason: 'invariant',
  }),
])('keeps the setup copy when a description model test fails (%s)', async (testFailure) => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => ({
    ...reviewerView,
    mapping: { engine: 'openrouter' as const, model: 'example/cover-a' },
    openrouter_key_configured: true,
  }))
  http.testDescriptionModel = vi.fn(async () => {
    throw testFailure
  })
  renderRoute('/settings?group=reviewer', http)
  const user = userEvent.setup()
  await screen.findByRole('button', { name: 'Use this OpenRouter model for descriptions' })
  await user.click(screen.getByRole('combobox', { name: 'Description model' }))
  await user.click(screen.getByRole('option', { name: 'Custom model ID' }))
  await user.type(screen.getByRole('textbox', { name: 'Description model ID' }), 'example/text-b')
  const readsBefore = vi.mocked(http.getReviewerAccess).mock.calls.length
  await user.click(screen.getByRole('button', { name: 'Test description model and prices' }))

  const description = screen.getByRole('region', { name: 'Description adviser' })
  const alert = await within(description).findByRole('alert')
  expect(alert).toHaveTextContent('Description adviser setup did not complete.')
  expect(alert.previousElementSibling).toBe(
    screen.getByRole('button', { name: 'Test description model and prices' }),
  )
  expect(vi.mocked(http.getReviewerAccess).mock.calls.length).toBe(readsBefore)
})

// Description adviser fixtures for #1663: a tested model, a usable contained
// Codex, and a saved OpenRouter description mapping (which offers Refresh).
const descriptionModel = {
  model: 'example/text-b',
  observed_at: 100,
  prompt_price: '0.000001',
  completion_price: '0.000002',
  request_price: '0',
  text_input: true as const,
  text_output: true as const,
  structured_outputs: true as const,
}
const usableCodex: ReviewerAccessView = {
  ...reviewerView,
  codex: { ...reviewerView.codex, state: 'usable', reason: null },
}
const describedOpenRouter: ReviewerAccessView = {
  ...reviewerView,
  openrouter_key_configured: true,
  description_mapping: { engine: 'openrouter', model: descriptionModel.model },
}
const typeDescriptionModel = async (user: ReturnType<typeof userEvent.setup>) => {
  await pickModel(user, 'Description model', 'Custom model ID')
  await user.type(
    screen.getByRole('textbox', { name: 'Description model ID' }),
    descriptionModel.model,
  )
  await user.type(
    within(screen.getByRole('region', { name: 'Description adviser' })).getByLabelText(
      'Shared OpenRouter API key',
    ),
    'synthetic-key',
  )
}

it('shows a failed OpenRouter description selection under its button, above Refresh (#1663)', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => describedOpenRouter)
  http.testDescriptionModel = vi.fn(async () => descriptionModel)
  http.configureOpenRouter = vi.fn(async () => {
    throw new ApiError(400, 'invalid_key')
  })
  renderRoute('/settings?group=reviewer', http)
  const use = await screen.findByRole('button', {
    name: 'Use this OpenRouter model for descriptions',
  })
  await user.click(screen.getByRole('button', { name: 'Test description model and prices' }))
  await waitFor(() => expect(use).toBeEnabled())
  await user.click(use)

  const description = screen.getByRole('region', { name: 'Description adviser' })
  const alert = await within(description).findByRole('alert')
  expect(within(description).getAllByRole('alert')).toHaveLength(1)
  expect(alert.previousElementSibling).toBe(use)
  expect(alert.nextElementSibling).toBe(
    screen.getByRole('button', { name: 'Refresh description model status and prices' }),
  )
})

it('shows a failed description refresh under Refresh (#1663)', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => describedOpenRouter)
  http.refreshOpenRouter = vi.fn(async () => {
    throw new ApiError(503, 'unreachable')
  })
  renderRoute('/settings?group=reviewer', http)
  const refresh = await screen.findByRole('button', {
    name: 'Refresh description model status and prices',
  })
  await user.click(refresh)

  const description = screen.getByRole('region', { name: 'Description adviser' })
  const alert = await within(description).findByRole('alert')
  expect(within(description).getAllByRole('alert')).toHaveLength(1)
  expect(alert.previousElementSibling).toBe(refresh)
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
    await user.click(screen.getByRole('button', { name: 'Check Codex model' }))
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
  const cover = await screen.findByRole('region', { name: 'Cover reviewer' })
  const description = screen.getByRole('region', { name: 'Description adviser' })
  expect(within(cover).getByRole('heading', { level: 3, name: 'Cover reviewer' })).toBeVisible()
  expect(
    within(description).getByRole('heading', { level: 3, name: 'Description adviser' }),
  ).toBeVisible()
  expect(cover.contains(description)).toBe(false)
  expect(within(cover).queryByRole('heading', { name: 'Shared Codex sign-in' })).toBeNull()
  expect(within(description).queryByRole('heading', { name: 'Shared Codex sign-in' })).toBeNull()
  expect(screen.getAllByRole('heading', { name: 'Shared Codex sign-in' })).toHaveLength(1)
  expect(screen.getAllByRole('button', { name: 'Check existing Codex sign-in' })).toHaveLength(1)
  expect(
    within(description).getByLabelText('Shared OpenRouter API key'),
  ).toHaveAccessibleDescription(/Leave blank to use the saved key/)
  expect(within(description).getByRole('combobox', { name: 'Description model' })).toBeVisible()
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
    expect(
      await screen.findByText(/The Codex sign-in change was not confirmed/u),
    ).toHaveTextContent('Check whether the sign-in changed before trying again.')
    await waitFor(() => expect(http.getReviewerAccess).toHaveBeenCalledTimes(2))
    expect(http.getCodexDevice).toHaveBeenCalledOnce()
    expect(
      within(screen.getByRole('region', { name: 'Cover reviewer' })).getByText(/^Selected:/u),
    ).toHaveTextContent('No reviewer')
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
    expect(screen.getByText('Status: not set up on this host')).toBeVisible()
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

it('separates measured cover cost from observed rates and never lends prices to typed models', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => ({
    ...reviewerView,
    mapping: { engine: 'openrouter' as const, model: 'openai/gpt-6-luna' },
    cover_metadata: {
      model: 'openai/gpt-6-luna',
      observed_at: 1700000000,
      prompt_price: '0.000001',
      completion_price: null,
      image_price: '0',
      request_price: null,
    },
    cost_evidence: {
      models: [
        {
          model: 'openai/gpt-6-luna',
          task: 'cover' as const,
          requests_selected: 100,
          requests_attempted: 100,
          requests_completed: 100,
          requests_failed: 0,
          works: 100,
          distinct_images: 270,
          total_cost_usd: '0.063641500',
          per_1000_usd: '0.636415000',
          measured_run: {
            source_head: 'a'.repeat(40),
            cohort_sha256: 'b'.repeat(64),
            observed_at: 1700000000,
          },
        },
      ],
    },
  }))
  renderRoute('/settings?group=reviewer', http)
  const cover = within(await screen.findByRole('region', { name: 'Cover reviewer' }))
  expect(
    cover.getByText('Cover review: about $0.64, including about 2.7 cover images per work.'),
  ).toBeVisible()
  expect(cover.getByText('Description advice: not measured for this model.')).toBeVisible()
  expect(
    cover.getByText(/0.000001 per input token, Unavailable per output token, 0 per image/u),
  ).toBeVisible()
  expect(cover.queryByText(/2023|1700000000|sample|Maximum-size/u)).toBeNull()
  const user = userEvent.setup()
  await pickModel(user, 'Cover model', 'Custom model ID')
  await user.type(cover.getByRole('textbox', { name: 'Cover model ID' }), 'example/unsaved')
  expect(cover.getByText('Validate this model to see its prices.')).toBeVisible()
  expect(cover.getByText('Cover review: not measured for this model.')).toBeVisible()
  expect(cover.queryByText(/0.000001 per input token/u)).toBeNull()
})

// #1584: a pending reviewer command keeps its button focused. The button is
// aria-disabled, not natively disabled, and a second press sends nothing.
const pendingDevice = {
  state: 'waiting' as const,
  failure: null,
  verification_url: null,
  user_code: null,
  expires_in_seconds: null,
}
const managedCodex: ReviewerAccessView = {
  ...reviewerView,
  mapping: { engine: 'codex' },
  codex: { ...reviewerView.codex, state: 'configured', reason: null },
}
const configuredOpenRouter: ReviewerAccessView = {
  ...reviewerView,
  mapping: { engine: 'openrouter', model: 'openai/gpt-6-luna' },
  openrouter_key_configured: true,
  openrouter: { ...reviewerView.openrouter, state: 'configured', model: 'openai/gpt-6-luna' },
}
type HeldCommand =
  | 'cancelCodexDevice'
  | 'recheckCodex'
  | 'clearCodex'
  | 'startCodexDevice'
  | 'configureOpenRouter'
  | 'refreshOpenRouter'
  | 'clearOpenRouter'
  | 'selectReviewer'
it.each<[string, ReviewerAccessView, HeldCommand, readonly string[], boolean]>([
  [
    'Cancel sign-in',
    { ...reviewerView, codex: { ...reviewerView.codex, state: 'waiting', reason: null } },
    'cancelCodexDevice',
    [],
    false,
  ],
  ['Check existing Codex sign-in', reviewerView, 'recheckCodex', [], false],
  ['Check existing Codex sign-in', managedCodex, 'recheckCodex', [], false],
  ['Confirm clear Codex sign-in', managedCodex, 'clearCodex', ['Clear Codex sign-in'], false],
  [
    'Confirm replace Codex sign-in',
    managedCodex,
    'startCodexDevice',
    ['Replace Codex sign-in'],
    false,
  ],
  ['Restart device sign-in', reviewerView, 'startCodexDevice', [], false],
  [
    'Replace or create Codex sign-in',
    { ...reviewerView, codex: { ...reviewerView.codex, state: 'ready', reason: null } },
    'startCodexDevice',
    [],
    false,
  ],
  [
    'Cancel any active sign-in',
    { ...reviewerView, codex: { ...reviewerView.codex, state: 'host_unsupported', reason: null } },
    'cancelCodexDevice',
    [],
    false,
  ],
  ['Validate and use OpenRouter', reviewerView, 'configureOpenRouter', [], true],
  [
    'Confirm replace OpenRouter key',
    configuredOpenRouter,
    'configureOpenRouter',
    ['Replace OpenRouter key'],
    true,
  ],
  ['Refresh current status', configuredOpenRouter, 'refreshOpenRouter', [], false],
  [
    'Confirm clear OpenRouter key',
    configuredOpenRouter,
    'clearOpenRouter',
    ['Clear OpenRouter key'],
    false,
  ],
  ['Continue without a reviewer', configuredOpenRouter, 'selectReviewer', [], false],
  [
    'Use Codex for cover review',
    { ...reviewerView, codex: { ...reviewerView.codex, state: 'usable', reason: null } },
    'selectReviewer',
    [],
    false,
  ],
])(
  'keeps %s focused while its reviewer command is pending (#1584)',
  async (name, access, command, steps, typesKey) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => access)
    http.getCodexDevice = vi.fn(async () => pendingDevice)
    const held = vi.fn(() => new Promise<never>(() => undefined))
    Object.assign(http, { [command]: held })
    renderRoute('/settings?group=reviewer', http)
    await screen.findByRole('combobox', { name: 'Cover model' })
    if (typesKey) {
      if (access.mapping === null) {
        await pickModel(user, 'Cover model', 'GPT-6 Luna (openai/gpt-6-luna)')
      }
      await user.type(screen.getByLabelText('OpenRouter API key'), 'synthetic-key')
    }
    for (const step of steps) await user.click(screen.getByRole('button', { name: step }))

    const button = await screen.findByRole('button', { name })
    await user.click(button)
    await waitFor(() => expect(held).toHaveBeenCalledOnce())
    expect(button).not.toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveFocus()
    await user.click(button)
    fireEvent.click(button)
    expect(held).toHaveBeenCalledOnce()
  },
)

// #1663: the description adviser's commands follow the cover panel (#1584).
// jsdom has no focus fixup, so not.toBeDisabled() is the proof here that a
// browser keeps focus; the experience review proves real focus.
const nothingToPrepare = () => Promise.resolve()
type HeldDescriptionCommand =
  | 'selectReviewer'
  | 'describeContainedDescription'
  | 'testDescriptionModel'
  | 'configureOpenRouter'
  | 'refreshOpenRouter'
it.each<
  [
    string,
    ReviewerAccessView,
    HeldDescriptionCommand,
    (user: ReturnType<typeof userEvent.setup>) => Promise<void>,
  ]
>([
  ['Continue without description advice', describedOpenRouter, 'selectReviewer', nothingToPrepare],
  ['Check Codex model', usableCodex, 'describeContainedDescription', nothingToPrepare],
  [
    'Use Codex for descriptions',
    usableCodex,
    'selectReviewer',
    async (user) => {
      await user.click(screen.getByRole('button', { name: 'Check Codex model' }))
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Use Codex for descriptions' })).toBeEnabled(),
      )
    },
  ],
  ['Test description model and prices', reviewerView, 'testDescriptionModel', typeDescriptionModel],
  [
    'Use this OpenRouter model for descriptions',
    reviewerView,
    'configureOpenRouter',
    async (user) => {
      await typeDescriptionModel(user)
      await user.click(screen.getByRole('button', { name: 'Test description model and prices' }))
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'Use this OpenRouter model for descriptions' }),
        ).toBeEnabled(),
      )
    },
  ],
  [
    'Refresh description model status and prices',
    describedOpenRouter,
    'refreshOpenRouter',
    nothingToPrepare,
  ],
])(
  'keeps %s focused while its description adviser command is pending (#1663)',
  async (name, access, command, prepare) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => access)
    http.describeContainedDescription = vi.fn(async () => ({
      model: 'contained-model',
      profile_sha256: 'a'.repeat(64),
    }))
    http.testDescriptionModel = vi.fn(async () => descriptionModel)
    const held = vi.fn(() => new Promise<never>(() => undefined))
    Object.assign(http, { [command]: held })
    renderRoute('/settings?group=reviewer', http)
    await screen.findByRole('region', { name: 'Description adviser' })
    await prepare(user)

    const button = screen.getByRole('button', { name })
    button.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(held).toHaveBeenCalledOnce())
    expect(button).not.toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveFocus()
    await user.click(button)
    fireEvent.click(button)
    expect(held).toHaveBeenCalledOnce()
  },
)

it('moves focus to Cancel sign-in after Confirm replace starts the sign-in (#1607)', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => managedCodex)
  http.startCodexDevice = vi.fn(async () => ({
    access: { ...managedCodex, codex: { ...managedCodex.codex, state: 'starting' as const } },
    device: { ...pendingDevice, state: 'starting' as const },
  }))
  http.getCodexDevice = vi.fn(async () => pendingDevice)
  renderRoute('/settings?group=reviewer', http)
  await user.click(await screen.findByRole('button', { name: 'Replace Codex sign-in' }))
  const confirm = screen.getByRole('button', { name: 'Confirm replace Codex sign-in' })
  expect(confirm).toHaveFocus()
  await user.keyboard('{Enter}')
  const cancel = await screen.findByRole('button', { name: 'Cancel sign-in' })
  await waitFor(() => expect(cancel).toHaveFocus())
  expect(http.startCodexDevice).toHaveBeenCalledOnce()
})

it('keeps host-blocked reviewer commands natively disabled (#1584)', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => ({
    ...reviewerView,
    codex: { ...reviewerView.codex, state: 'host_unsupported' as const, reason: null },
  }))
  http.recheckCodex = vi.fn(async () => pendingDevice)
  renderRoute('/settings?group=reviewer', http)
  // The press did not cause these conditions, so they keep native disabled.
  for (const name of ['Check existing Codex sign-in', 'Replace or create Codex sign-in']) {
    const button = await screen.findByRole('button', { name })
    expect(button).toBeDisabled()
    expect(button).not.toHaveAttribute('aria-disabled')
    await user.click(button)
  }
  expect(http.recheckCodex).not.toHaveBeenCalled()
  expect(http.startCodexDevice).not.toHaveBeenCalled()
})

it.each([
  ['success', async () => ({ ...reviewerView, revision: 4 }), null],
  [
    'an unconfirmed failure',
    async () => {
      throw new ApiError(503, 'reviewer_access_unavailable')
    },
    'No answer came back. Check whether the reviewer change was saved before trying again.',
  ],
] as const)(
  'keeps Continue without a reviewer focused after %s, with its failure under it (#1584)',
  async (_outcome, select, copy) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(configuredOpenRouter)
      .mockResolvedValue({ ...reviewerView, revision: 4 })
    http.selectReviewer = vi.fn(select)
    renderRoute('/settings?group=reviewer', http)
    const button = await screen.findByRole('button', { name: 'Continue without a reviewer' })
    await user.click(button)
    await waitFor(() =>
      expect(
        within(screen.getByRole('region', { name: 'Cover reviewer' })).getByText(/^Selected:/u),
      ).toHaveTextContent('No reviewer'),
    )
    // The settled state its own press created keeps the button focusable.
    expect(button).not.toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveFocus()
    await user.click(button)
    expect(http.selectReviewer).toHaveBeenCalledOnce()
    if (copy === null) {
      expect(screen.queryByText(/No answer came back/u)).toBeNull()
      return
    }
    const alert = await screen.findByText(copy)
    expect(alert).toHaveAttribute('role', 'alert')
    expect(button.nextElementSibling).toBe(alert)
    expect(alert.closest('.settings-field-control')).toBe(button.parentElement)
  },
)

it.each([
  [
    'success',
    async () => ({ ...describedOpenRouter, description_mapping: null, revision: 4 }),
    null,
  ],
  [
    'an unconfirmed failure',
    async () => {
      throw new ApiError(503, 'reviewer_access_unavailable')
    },
    'No answer came back. Check whether the description adviser change was saved before trying again.',
  ],
  [
    'an answer the browser cannot read',
    async () => {
      throw new UncertainMutationError({
        operation: 'PUT /api/v1/reviewer-access/selection',
        responseStatus: 200,
        at: '/revision',
        reason: 'required',
      })
    },
    'No answer came back. Check whether the description adviser change was saved before trying again.',
  ],
] as const)(
  'keeps Continue without description advice focused after %s, with its failure under it (#1663)',
  async (_outcome, select, copy) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi
      .fn<ReviewHttp['getReviewerAccess']>()
      .mockResolvedValueOnce(describedOpenRouter)
      .mockResolvedValue({ ...describedOpenRouter, description_mapping: null, revision: 4 })
    http.selectReviewer = vi.fn(select)
    renderRoute('/settings?group=reviewer', http)
    const button = await screen.findByRole('button', {
      name: 'Continue without description advice',
    })
    await user.click(button)
    expect(await screen.findByText('No description adviser selected.')).toBeVisible()
    if (copy !== null) await screen.findByText(copy)
    // The settled state its own press created keeps the button focusable.
    expect(button).not.toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveFocus()
    await user.click(button)
    expect(http.selectReviewer).toHaveBeenCalledOnce()
    if (copy === null) {
      expect(screen.queryByText(/could not be confirmed/u)).toBeNull()
      return
    }
    const alert = screen.getByText(copy)
    expect(alert).toHaveAttribute('role', 'alert')
    expect(button.nextElementSibling).toBe(alert)
  },
)

it('shows a Use Codex failure under its button, not in the sign-in row (#1584)', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  http.getReviewerAccess = vi.fn(async () => ({
    ...reviewerView,
    codex: { ...reviewerView.codex, state: 'usable' as const, reason: null },
  }))
  http.selectReviewer = vi.fn(async () => {
    throw new ApiError(409, 'credential_unavailable')
  })
  renderRoute('/settings?group=reviewer', http)
  const button = await screen.findByRole('button', { name: 'Use Codex for cover review' })
  await user.click(button)
  const alert = await screen.findByText(
    'Codex sign-in is unavailable. Check or replace the sign-in, then try again.',
  )
  expect(button.nextElementSibling).toBe(alert)
  expect(screen.getAllByRole('alert')).toEqual([alert])
  expect(button).toHaveFocus()
})

// #1617: a refused Codex selection is recorded by the server, so the panel
// re-reads access and drops a cached device result that would mask it.
const unmanagedProfileCopy =
  'The installed Codex sign-in could not be validated. Restart device sign-in, or check the existing sign-in.'
const credentialUnavailableCopy =
  'Codex sign-in is unavailable. Check or replace the sign-in, then try again.'
const usableUnmapped: ReviewerAccessView = {
  ...reviewerView,
  codex: { ...reviewerView.codex, state: 'usable', reason: null },
}
const usableDevice = { ...pendingDevice, state: 'usable' as const }
it.each(['fresh usable', 'Check-cached usable', 'description-mapped'] as const)(
  're-reads access after a refused Use Codex for cover review (%s, #1617)',
  async (shape) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const reads = vi.fn<ReviewHttp['getReviewerAccess']>()
    if (shape === 'fresh usable') reads.mockResolvedValueOnce(usableUnmapped)
    if (shape === 'Check-cached usable')
      reads.mockResolvedValueOnce(reviewerView).mockResolvedValueOnce(usableUnmapped)
    if (shape === 'description-mapped')
      reads
        .mockResolvedValueOnce({ ...usableUnmapped, description_mapping: { engine: 'codex' } })
        .mockResolvedValue({ ...reviewerView, description_mapping: { engine: 'codex' } })
    else reads.mockResolvedValue(reviewerView)
    http.getReviewerAccess = reads
    http.recheckCodex = vi.fn(async () => usableDevice)
    http.selectReviewer = vi.fn(async () => {
      throw new ApiError(409, 'credential_unavailable')
    })
    renderRoute('/settings?group=reviewer', http)
    if (shape === 'Check-cached usable') {
      ;(await screen.findByRole('button', { name: 'Check existing Codex sign-in' })).focus()
      await user.keyboard('{Enter}')
      expect(await screen.findByText('Status: ready to use')).toBeVisible()
    }
    const use = await screen.findByRole('button', { name: 'Use Codex for cover review' })
    await waitFor(() => expect(use).toBeEnabled())
    use.focus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText('Status: failed')).toBeVisible()
    expect(screen.queryByText('Status: ready to use')).toBeNull()
    if (shape === 'description-mapped') {
      // A description mapping keeps the row: the refusal stays under its
      // now-disabled button, and focus moves off it to Replace.
      const alert = screen.getByText(credentialUnavailableCopy)
      expect(alert).toHaveAttribute('role', 'alert')
      expect(use.nextElementSibling).toBe(alert)
      expect(use).toBeDisabled()
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Replace Codex sign-in' })).toHaveFocus(),
      )
      return
    }
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent(unmanagedProfileCopy)
    expect(screen.queryByRole('button', { name: 'Use Codex for cover review' })).toBeNull()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toHaveFocus(),
    )
    expect(http.getReviewerAccess).toHaveBeenCalledTimes(shape === 'fresh usable' ? 2 : 3)
    expect(http.getCodexDevice).not.toHaveBeenCalled()
  },
)

describe('library languages in Settings (#1466)', () => {
  const title = 'Ebooks & Audiobooks: Collected Editions'
  const withLanguages = (languages: string[], authority: 'file' | 'store' = 'store') => ({
    ...view(authority),
    document: {
      ...document,
      libraries: document.libraries.map((library) => ({ ...library, languages })),
    },
  })

  it('adds by typed name, reorders and removes with focus kept, and saves the order', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let saved: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      saved = request
      return { ...view(), document: { ...savedDocument(request.document), revision: 4 } }
    })
    renderRoute('/settings?group=libraries', http)

    expect(await screen.findByText('No languages yet.')).toBeVisible()
    expect(
      screen.getByText('First is preferred. Changing this prepares the library again.'),
    ).toBeVisible()
    const picker = screen.getByRole('combobox', { name: `Add a language to ${title}` })
    await user.click(picker)
    await user.keyboard('ger')
    expect(screen.getByRole('option', { name: 'German (de)' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(picker).toHaveFocus()
    await user.click(picker)
    expect(screen.queryByRole('option', { name: 'German (de)' })).toBeNull()
    await user.keyboard('fre')
    await user.keyboard('{Enter}')

    const list = screen.getByRole('list', { name: `Languages for ${title}` })
    const entries = () => within(list).getAllByRole('listitem')
    expect(entries().map((item) => item.firstChild?.textContent)).toEqual([
      'German (de)',
      'French (fr)',
    ])
    expect(entries()[0]).toHaveTextContent('Preferred')
    expect(entries()[1]).not.toHaveTextContent('Preferred')
    await user.click(screen.getByRole('button', { name: 'Move French (fr) up' }))
    expect(entries()[0]).toHaveTextContent('French (fr)Preferred')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Move French (fr) down' })).toHaveFocus(),
    )
    await user.click(screen.getByRole('button', { name: 'Remove German (de)' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Remove French (fr)' })).toHaveFocus(),
    )

    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    const change = screen.getByText(`${title}: languages`).closest('li')
    expect(change).toHaveTextContent('No languages')
    expect(change).toHaveTextContent('becomes French (fr)')
    // The review link goes back to the row, onto a control that can be used.
    await user.click(within(change as HTMLElement).getByRole('button'))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Remove French (fr)' })).toHaveFocus(),
    )
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(saved?.document.libraries[0]?.languages).toEqual(['fr'])
  })

  it('moves focus to the picker when the last language is removed, and Discard restores', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => withLanguages(['de']))
    renderRoute('/settings?group=libraries', http)

    await user.click(await screen.findByRole('button', { name: 'Remove German (de)' }))
    expect(screen.getByText('No languages yet.')).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: `Add a language to ${title}` })).toHaveFocus(),
    )
    await user.click(screen.getByRole('button', { name: 'Discard all' }))
    expect(screen.getByRole('list', { name: `Languages for ${title}` })).toHaveTextContent(
      'German (de)Preferred',
    )
    expect(http.writeSettings).not.toHaveBeenCalled()
  })

  it('stops adding at the limit and says why', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () =>
      withLanguages(['de', 'en', 'fr', 'es', 'it', 'nl', 'pt', 'sv']),
    )
    renderRoute('/settings?group=libraries', http)

    const picker = await screen.findByRole('combobox', { name: `Add a language to ${title}` })
    expect(picker).toBeDisabled()
    expect(picker).toHaveAccessibleDescription(
      'A library can have up to 8 languages. Remove one to add another.',
    )
    expect(
      screen.getByText('A library can have up to 8 languages. Remove one to add another.'),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Remove Swedish (sv)' }))
    expect(picker).toBeEnabled()
    expect(screen.queryByText(/can have up to 8 languages/)).toBeNull()
  })

  it('withholds a language the list already names under another tag', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => withLanguages(['deu', 'zh-Hant']))
    renderRoute('/settings?group=libraries', http)

    await user.click(await screen.findByRole('combobox', { name: `Add a language to ${title}` }))
    expect(screen.queryByRole('option', { name: 'German (de)' })).toBeNull()
    expect(screen.getByRole('option', { name: 'Chinese (zh)' })).toBeInTheDocument()
    cleanup()
    // An offered code withholds only itself: the browser's alias of `tl` to
    // `fil` does not hide one the server accepts beside the other.
    http.getSettings = vi.fn(async () => withLanguages(['fil']))
    renderRoute('/settings?group=libraries', http)
    await user.click(await screen.findByRole('combobox', { name: `Add a language to ${title}` }))
    expect(screen.getByRole('option', { name: 'Filipino (tl)' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Filipino (fil)' })).toBeNull()
    expect(screen.getByRole('option', { name: 'Bhojpuri (bih)' })).toBeInTheDocument()
    // A listed alias or regional tag withholds the code the server groups it
    // with: `tgl` is `tl`, and `bih-IN` is `bih`.
    for (const [listed, withheld] of [
      ['tgl', 'tl'],
      ['bih-IN', 'bih'],
    ] as const) {
      cleanup()
      http.getSettings = vi.fn(async () => withLanguages([listed]))
      renderRoute('/settings?group=libraries', http)
      await user.click(await screen.findByRole('combobox', { name: `Add a language to ${title}` }))
      expect(screen.queryByRole('option', { name: new RegExp(`\\(${withheld}\\)$`) })).toBeNull()
    }
    expect(screen.queryByRole('option', { name: /\((und|mul|mis|zxx)\)$/ })).toBeNull()
  })

  it('shows a file-managed list read-only, or that none is declared', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => withLanguages(['de', 'en'], 'file'))
    renderRoute('/settings?group=libraries', http)

    const list = await screen.findByRole('list', { name: `Languages for ${title}` })
    expect(list).toHaveTextContent('German (de)Preferred')
    expect(list).toHaveTextContent('English (en)')
    expect(within(list).queryByRole('button')).toBeNull()
    expect(screen.queryByRole('combobox', { name: /Add a language/ })).toBeNull()
    cleanup()
    http.getSettings = vi.fn(async () => view('file'))
    renderRoute('/settings?group=libraries', http)
    expect(await screen.findByText('No languages.')).toBeVisible()
  })
})

describe('settings provider throttling (#1282)', () => {
  const throttled = (retry: 'available' | 'managed_by_file' = 'available'): PreparationView => ({
    ...idlePreparation,
    schema_version: 'review-preparation-v2',
    recovery_required: false,
    active: false,
    actionable: false,
    paused: false,
    libraries: [],
    provider_throttling: {
      generation: 1,
      retry,
      entries: [
        {
          library: 'Synthetic ebooks',
          adapter: 'google_books',
          state: 'waiting',
          reasons: ['quota_exhausted'],
          deferred: 5,
          next_retry_at: null,
          remedy: 'add_api_key',
        },
      ],
    },
  })

  it('keeps route focus in Settings when the loading state that held it unmounts (#1282 R3 F7)', async () => {
    let release: (value: ReturnType<typeof view>) => void = () => undefined
    const http = createHttp()
    http.getSettings = vi.fn(
      () =>
        new Promise<ReturnType<typeof view>>((resolve) => {
          release = resolve
        }),
    )
    http.getPreparation = vi.fn(async () => throttled())
    renderRoute('/settings?group=background', http)
    const loading = (await screen.findByRole('heading', { name: 'Loading settings…' })).closest(
      'section',
    )
    // The shell has restored the route, and its route focus is spent on the
    // loading state, before the settings read answers.
    await waitFor(() =>
      expect(window.document.getElementById('main-content')).toHaveAttribute(
        'data-restored-path',
        '/settings',
      ),
    )
    loading?.focus()
    expect(window.document.activeElement).toBe(loading)
    await act(async () => release(view()))
    await screen.findByRole('spinbutton', { name: 'Request timeout' })
    expect(window.document.activeElement).not.toBe(window.document.body)
    expect(window.document.activeElement).toBe(
      window.document.querySelector('section.settings-route'),
    )
  })

  it('replaces the generation number with Retry now and leaves the other number rows', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getPreparation = vi.fn(async () => throttled())
    renderRoute('/settings?group=background', http)
    for (const name of ['Request timeout', 'Library polling interval', 'Shutdown grace period'])
      expect(await screen.findByRole('spinbutton', { name })).toBeVisible()
    expect(screen.queryByText('Provider recovery generation')).toBeNull()
    expect(screen.queryByRole('spinbutton', { name: /recovery/i })).toBeNull()
    expect(await screen.findByRole('button', { name: 'Retry now' })).toBeVisible()
  })

  it('rebases an open draft over Retry now, keeps the edit and saves at the new revision', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let savedRequest: SettingsWrite | undefined
    const retried: SettingsView = {
      ...view(),
      document: { ...document, revision: 4, provider_recovery_generation: 2 },
    }
    http.getSettings = vi.fn(async () => view())
    http.getPreparation = vi.fn(async () => throttled())
    http.retryProviderRecovery = vi.fn(async () => retried)
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      savedRequest = request
      return { ...view(), document: { ...savedDocument(request.document), revision: 5 } }
    })
    renderRoute('/settings', http)
    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await user.clear(endpoint)
    await user.type(endpoint, 'https://other.invalid')
    await user.click(await screen.findByRole('button', { name: 'Retry now' }))

    expect(http.retryProviderRecovery).toHaveBeenCalledWith(1)
    expect(
      await screen.findByText(
        'Settings changed elsewhere. Your changes were kept on top of the newer saved values.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveValue(
      'https://other.invalid',
    )
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(savedRequest?.expected_revision).toBe(4)
    expect(savedRequest?.document.provider_recovery_generation).toBe(2)
  })

  it('rebases an edited language list over newer saved values (#1466)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let savedRequest: SettingsWrite | undefined
    http.getSettings = vi.fn(async () => view())
    http.getPreparation = vi.fn(async () => throttled())
    http.retryProviderRecovery = vi.fn(async () => ({
      ...view(),
      document: { ...document, revision: 4, provider_recovery_generation: 2 },
    }))
    http.writeSettings = vi.fn(async (request: SettingsWrite) => {
      savedRequest = request
      return { ...view(), document: { ...savedDocument(request.document), revision: 5 } }
    })
    renderRoute('/settings?group=libraries', http)
    await user.click(await screen.findByRole('combobox', { name: /Add a language/ }))
    await user.keyboard('ital{Enter}')
    await user.click(await screen.findByRole('button', { name: 'Retry now' }))

    expect(
      await screen.findByText(
        'Settings changed elsewhere. Your changes were kept on top of the newer saved values.',
      ),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(savedRequest?.expected_revision).toBe(4)
    expect(savedRequest?.document.libraries[0]?.languages).toEqual(['it'])
  })

  it('shows the file instruction under file authority and no command when settings are unavailable', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    http.getPreparation = vi.fn(async () => throttled('managed_by_file'))
    renderRoute('/settings', http)
    expect(
      await screen.findByText(
        'To retry, raise provider_recovery_generation by one in /data/config.json and restart the service.',
      ),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry now' })).toBeNull()
    cleanup()

    const failing = createHttp()
    failing.getPreparation = vi.fn(async () => throttled())
    renderRoute('/settings', failing)
    expect(await screen.findByText('Settings could not be loaded')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry now' })).toBeNull()
    expect(failing.retryProviderRecovery).not.toHaveBeenCalled()
  })
})

// Settings → Reviewer › Taste profile (#1372): its own OpenRouter model.
describe('taste profile model (#1372)', () => {
  const metadata = {
    model: 'anthropic/claude-sonnet-5.5',
    observed_at: 100,
    prompt_price: '0.000003',
    completion_price: '0.000015',
    image_price: '0.0048',
    request_price: null,
  }
  const initial: ReviewerAccessView = { ...reviewerView, openrouter_key_configured: true }
  const section = async () => {
    const heading = await screen.findByRole('heading', { level: 3, name: 'Taste profile' })
    const found = heading.closest('section')
    if (!found) throw new Error('the taste profile section is required')
    return found
  }

  it('discloses what OpenRouter receives, the limit and no selection', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => initial)
    renderRoute('/settings?group=reviewer', http)
    const taste = await section()
    expect(
      within(taste).getByText(
        'A vision model writes your taste profile from the covers you chose and passed over.',
      ),
    ).toBeVisible()
    expect(
      within(taste).getByText(
        'OpenRouter receives those covers with their sizes and the book’s format, never its title or files.',
      ),
    ).toBeVisible()
    expect(within(taste).getByText('No taste profile model selected.')).toBeVisible()
    expect(
      within(taste).getByText('Mechanical Librarian asks for at most 8 taste profiles a day.'),
    ).toBeVisible()
    expect(
      within(taste).getByText('Test this model to see its prices before you use it.'),
    ).toBeVisible()
    expect(within(taste).getByRole('button', { name: 'Use for taste profile' })).toBeDisabled()
  })

  it('tests the recommended model, shows its prices and saves it for taste profiles', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => initial)
    http.testTasteModel = vi.fn(async () => metadata)
    http.configureOpenRouter = vi.fn(async () => ({
      ...initial,
      revision: 4,
      taste_mapping: { engine: 'openrouter' as const, model: metadata.model },
      taste_metadata: metadata,
    }))
    renderRoute('/settings?group=reviewer', http)
    const user = userEvent.setup()
    const taste = await section()
    await user.click(within(taste).getByRole('combobox', { name: 'Taste profile model' }))
    await user.click(screen.getByRole('option', { name: /anthropic\/claude-sonnet-5\.5/u }))
    expect(screen.getByText('Recommended. Measured for taste profiles.')).toBeVisible()
    await user.click(
      within(taste).getByRole('button', { name: 'Test taste profile model and prices' }),
    )
    expect(
      await within(taste).findByText('Image input, text output and structured output supported.'),
    ).toBeVisible()
    expect(
      within(taste).getByText(
        '0.000003 per input token, 0.000015 per output token, 0.0048 per image, Unavailable per request.',
      ),
    ).toBeVisible()
    expect(http.testTasteModel).toHaveBeenCalledWith({
      expected_revision: 3,
      api_key: '',
      model: metadata.model,
    })
    await user.click(within(taste).getByRole('button', { name: 'Use for taste profile' }))
    await waitFor(() =>
      expect(http.configureOpenRouter).toHaveBeenCalledWith({
        expected_revision: 3,
        api_key: '',
        model: metadata.model,
        task: 'cover_taste_build',
      }),
    )
    expect(await within(taste).findByText('Taste profile model saved.')).toBeVisible()
    expect(
      within(taste).getByText('Selected: anthropic/claude-sonnet-5.5 on OpenRouter.'),
    ).toBeVisible()
  })

  it.each([
    [
      new ApiError(409, 'access_changed'),
      'Taste profile setup did not complete. Check the model and key, then try again.',
    ],
    [
      new UncertainMutationError({
        operation: 'PUT /api/v1/reviewer-access/openrouter',
        responseStatus: 200,
        at: 'invalid_reviewer_access_response',
        reason: 'invariant',
      }),
      'No answer came back. Check whether the taste profile model was saved before trying again.',
    ],
  ])('says when saving the taste profile model fails (%s)', async (failure, copy) => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getReviewerAccess = vi.fn(async () => initial)
    http.testTasteModel = vi.fn(async () => metadata)
    http.configureOpenRouter = vi.fn(async () => {
      throw failure
    })
    renderRoute('/settings?group=reviewer', http)
    const user = userEvent.setup()
    const taste = await section()
    await user.click(within(taste).getByRole('combobox', { name: 'Taste profile model' }))
    await user.click(screen.getByRole('option', { name: /anthropic\/claude-sonnet-5\.5/u }))
    await user.click(
      within(taste).getByRole('button', { name: 'Test taste profile model and prices' }),
    )
    await user.click(await within(taste).findByRole('button', { name: 'Use for taste profile' }))
    expect(await within(taste).findByRole('alert')).toHaveTextContent(copy)
  })
})
