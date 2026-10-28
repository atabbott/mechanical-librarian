import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  CsrfRenewedError,
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
import type {
  PreparationView,
  ReviewHttp,
  ReviewerAccessView,
  SettingsView,
  SettingsWrite,
} from '../src/api/http'
import { coverReviewerLabel } from '../src/labels'
import { createHttp, renderRoute } from './fixtures'
import { document, view } from './settingsFixtures'

const reviewerView: ReviewerAccessView = {
  schema_version: 'reviewer-access-view-v2',
  cover_metadata: null,
  cost_evidence: { schema_version: 'reviewer-cost-evidence-v2', models: [] },
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
    expect(segment('Use Open Library', 'Off')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByRole('group', { name: 'Use Open Library' })).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Contact email' })).toBeVisible()
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
    await user.click(segment('Use Audible', 'On'))

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
      /Sent only to Open Library, as it asks; never kept in reports/,
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
      return { ...view(), document: request.document }
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
    expect(metadata).toHaveTextContent('Audiobookshelf identifier: books')
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
      expect(await screen.findByRole('alert')).toHaveTextContent('could not safely read')
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
      // The reloaded values are the saved state, so no draft or tray remains.
      expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
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

    await user.click(screen.getByRole('button', { name: 'Replace Audiobookshelf access key' }))
    const targetSecret = screen.getByLabelText('Replacement Audiobookshelf access key')
    await user.type(targetSecret, 'Bearer secret')
    expect(targetSecret).toHaveAttribute('aria-invalid', 'true')
    expect(targetSecret).toHaveAccessibleDescription(/Secrets must be 1–2,048/)

    await user.click(screen.getByRole('button', { name: /Metadata sources/ }))
    const email = screen.getByRole('textbox', { name: 'Contact email' })
    await user.clear(email)
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription(/needs a contact email while enabled/)
    await user.click(segment('Use Open Library', 'Off'))
    expect(email).not.toHaveAttribute('aria-invalid')
    await user.click(segment('Use Open Library', 'On'))
    expect(email).toHaveAttribute('aria-invalid', 'true')

    await user.click(screen.getByRole('button', { name: 'Clear Hardcover API key' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Hardcover API key' }))
    const requiredKey = screen.getByRole('button', {
      name: 'Keep configured Hardcover API key',
    })
    expect(requiredKey).toHaveAttribute('aria-invalid', 'true')
    expect(requiredKey).toHaveAccessibleDescription(/requires an API key while enabled/)

    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    const libraryId = screen.getByRole('textbox', { name: 'Audiobookshelf library ID' })
    await user.clear(libraryId)
    expect(libraryId).toHaveAttribute('aria-invalid', 'true')
    expect(libraryId).toHaveAccessibleDescription(/identifier must contain 1–128 characters/)

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
    expect(screen.getByText('Recommended. Paid; observations remain advice.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Restart device sign-in' })).toBeVisible()
    await pickModel(user, 'Cover model', 'dots-studio/dots-3-note-preview:free')
    expect(screen.getByText(/not yet qualified here\. 20 requests\/minute/u)).toBeVisible()
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
    expect(await screen.findByText('Advisory availability: ready to use')).toBeVisible()
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
      const allowance = screen.getByText(/Usage consumes your plan allowance/)
      expect(allowance).toHaveTextContent('no per-request monetary estimate is available')
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

    const region = await screen.findByRole('region', { name: 'Optional cover reviewer' })
    const cover = within(region)
    const facts = region.querySelector('.settings-reviewer-facts')
    expect(facts).not.toBeNull()
    // The terms read exactly as the description list's do; only the sentences
    // are cover-specific.
    expect(cover.getAllByRole('term').map((term) => term.textContent)).toEqual([
      'Model',
      'Capabilities',
      'Observed prices in USD',
      'Example cost per 1,000 books',
      'Cover request',
    ])
    expect(cover.getByText('openai/gpt-4o-mini', { selector: 'dd' })).toBeVisible()
    expect(cover.getByText(/Image input, text output and structured output/u)).toBeVisible()
    expect(cover.getByText('Validate and save this model to observe its prices.')).toBeVisible()
    expect(cover.getByText('Cover review: not measured for this model.')).toBeVisible()
    expect(cover.getByText('Description advice: not measured for this model.')).toBeVisible()
    expect(
      cover.getByText('One work packet, which may contain several cover images.'),
    ).toBeVisible()
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

    const cover = within(await screen.findByRole('region', { name: 'Optional cover reviewer' }))
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

    const cover = within(await screen.findByRole('region', { name: 'Optional cover reviewer' }))
    expect(
      await cover.findByText(
        'None yet. Choose a model above; it is checked for image, text and structured output when you validate.',
        { selector: 'dd' },
      ),
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
    expect(await screen.findByText(`Advisory availability: ${terminalWords[state]}`)).toBeVisible()
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
    expect(screen.queryByText('ABCD-EFGH')).not.toBeInTheDocument()

    await act(async () => router.navigate({ to: '/settings', search: { group: 'reviewer' } }))
    // The remounted panel resumes from the cached flow, then refreshes it once.
    expect(screen.getByText('ABCD-EFGH')).toBeVisible()
    await waitFor(() => expect(http.getCodexDevice).toHaveBeenCalledTimes(2))
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
    expect(screen.getByText('Advisory availability: checking')).toBeVisible()
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
    expect(await screen.findByText('Advisory availability: ready to use')).toBeVisible()
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
    expect(await screen.findByText('Advisory availability: ready to use')).toBeVisible()
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
      'The container memory limit is below 1 GB. Increase it before using Codex.',
    ],
    [
      'host_unsupported',
      'headroom_below_1_gib',
      true,
      'Less than 1 GB of memory headroom is available. Free memory or raise the container limit.',
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
    await screen.findByText('Advisory availability: not supported on this host')

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
      'Less than 1 GB of memory headroom is available. Free memory or raise the container limit.',
    )
    expect(
      screen.queryByText(/installed Codex sign-in could not be validated/u),
    ).not.toBeInTheDocument()
    // The bound and the headroom beside it read in one unit (#1286).
    expect(screen.getByText('Available reviewer headroom: 2 GB')).toBeVisible()
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
    expect(await screen.findByText('Advisory availability: ready to use')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clear Codex sign-in' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear Codex sign-in' }))
    expect(await screen.findByText('Advisory availability: ready to sign in')).toBeVisible()
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
    expect(endpoint.closest('.settings-field-row')).toHaveClass('is-changed')
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
      return { ...view(), document: { ...request.document, revision: 4 } }
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
    expect(screen.getByRole('alert')).toHaveTextContent('home requires an access key.')
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
    const archive = screen.getByRole('spinbutton', { name: 'Source archive limit' })
    await user.clear(archive)
    await user.type(archive, '1048576')
    await user.click(screen.getByText('Response and file size limits'))
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    await user.click(screen.getByRole('button', { name: 'Review changes' }))
    await user.click(
      within(screen.getByRole('list')).getByRole('button', { name: /Source archive limit/ }),
    )

    expect(await screen.findByRole('heading', { level: 2, name: 'Background work' })).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Source archive limit' })).toHaveFocus(),
    )
  })

  it.each([
    [new ApiError(422, 'invalid_settings'), /Nothing was saved; your changes are still here/],
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
    expect(screen.getByRole('spinbutton', { name: 'Source archive limit' })).toHaveValue(256)
    expect(
      screen.getByRole('spinbutton', { name: 'Source archive limit' }).parentElement,
    ).toHaveTextContent('MB')
    expect(window.document.body).not.toHaveTextContent(/\bbytes\b|KiB|MiB/u)

    await act(async () => router.navigate({ to: '/settings', search: { group: 'access' } }))
    expect(await screen.findByRole('spinbutton', { name: 'Owner session lifetime' })).toHaveValue(
      43_200,
    )
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
      return { ...view(), document: request.document }
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
      return { ...view(), document: request.document }
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
      return { ...view(), document: request.document }
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
    const audio = screen.getByRole('spinbutton', { name: 'Audiobook source limit' })
    // Sizes read in MB (1,048,576 bytes), never as raw bytes (#1286).
    expect(audio).toHaveValue(8192)
    // Its own ceiling, not the EPUB archive's: an audiobook is read by header
    // and is routinely larger than any archive this build admits.
    expect(audio).toHaveAttribute('max', '16384')
    expect(screen.getByRole('spinbutton', { name: 'Source archive limit' })).toHaveAttribute(
      'max',
      '256',
    )

    await user.clear(audio)
    await user.type(audio, '0.0009')
    expect(audio).toHaveAttribute('aria-invalid', 'true')
    expect(audio).toHaveAccessibleDescription(
      /Audiobook source limit must be from 0\.000977 MB \(1 KB\) to 16,384 MB \(16 GB\)\./u,
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
  expect(screen.getByRole('link', { name: /Review cleanup preferences/ })).toHaveAttribute(
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
    expect(screen.getByText('Advisory availability: not set up on this host')).toBeVisible()
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
      schema_version: 'reviewer-cost-evidence-v2' as const,
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
  const cover = within(await screen.findByRole('region', { name: 'Optional cover reviewer' }))
  expect(
    cover.getByText('Cover review: about $0.64, including about 2.7 cover images per book.'),
  ).toBeVisible()
  expect(cover.getByText('Description advice: not measured for this model.')).toBeVisible()
  expect(
    cover.getByText(/0.000001 per input token; Unavailable per output token; 0 per image/u),
  ).toBeVisible()
  expect(cover.queryByText(/2023|1700000000|sample|Maximum-size/u)).toBeNull()
  const user = userEvent.setup()
  await pickModel(user, 'Cover model', 'Custom model ID')
  await user.type(cover.getByRole('textbox', { name: 'Cover model ID' }), 'example/unsaved')
  expect(cover.getByText('Validate and save this model to observe its prices.')).toBeVisible()
  expect(cover.getByText('Cover review: not measured for this model.')).toBeVisible()
  expect(cover.queryByText(/0.000001 per input token/u)).toBeNull()
})

describe('settings provider throttling (#1282)', () => {
  const throttled = (retry: 'available' | 'managed_by_file' = 'available'): PreparationView => ({
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
    for (const name of ['Request timeout', 'Library polling interval', 'Shutdown budget'])
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
      return { ...view(), document: { ...request.document, revision: 5 } }
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

  it('shows the file instruction under file authority and no command when settings are unavailable', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    http.getPreparation = vi.fn(async () => throttled('managed_by_file'))
    renderRoute('/settings', http)
    expect(
      await screen.findByText(
        'Settings are managed by /data/config.json: raise provider_recovery_generation by one there and restart the service.',
      ),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry now' })).toBeNull()
    cleanup()

    const failing = createHttp()
    failing.getPreparation = vi.fn(async () => throttled())
    renderRoute('/settings', failing)
    expect(await screen.findByText('Settings are unavailable.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry now' })).toBeNull()
    expect(failing.retryProviderRecovery).not.toHaveBeenCalled()
  })
})
