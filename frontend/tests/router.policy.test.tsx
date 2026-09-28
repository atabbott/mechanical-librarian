import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  ReviewApiClient,
} from '../src/api/http'
import type {
  PolicyStateView,
  PolicyPreviewView,
  PreparationView,
  ReviewHttp,
  ShadowPostureView,
} from '../src/api/http'
import { normalizePolicySearch } from '../src/router'
import { createHttp, inertAudiobookQualification, renderRoute } from './fixtures'
import { view } from './settingsFixtures'

const comparisons = (fields: PolicyPreviewView['libraries'][number]['cases'][number]['fields']) =>
  fields.map((draft) => ({
    active: { ...draft, desired_value: draft.current_value, qualified_value: draft.current_value },
    draft,
    active_destination: 'ask',
    draft_destination: 'automatic',
    changed: draft.desired_value !== draft.current_value,
  }))

describe('Policy route', () => {
  it('explains empty-series filling, fill and explicit add/replace representations and provider numbering limits', async () => {
    renderRoute('/policy?section=publication')
    const series = await screen.findByRole('region', { name: 'Series memberships' })
    expect(
      within(series).getByText(
        'Fill empty Series, add supported memberships, or explicitly replace the current list.',
      ),
    ).toBeVisible()
    await userEvent.setup().click(within(series).getByText('What this changes'))
    expect(within(series).getByText(/One provider can supply enough backing/u)).toBeVisible()
    expect(within(series).getByText(/not independently verified for each book/u)).toBeVisible()
    expect(within(series).getByText('Primary narrative:')).toBeVisible()
    expect(within(series).getByText('First source membership:')).toBeVisible()
    expect(within(series).getByText('All qualified memberships:')).toBeVisible()
    expect(within(series).getByText('Add supported memberships:')).toBeVisible()
    expect(within(series).getByText('Replace with supported memberships:')).toBeVisible()
    expect(within(series).getByText(/with only its first membership/u)).toBeVisible()
  })

  it('normalizes Policy deep links to the four protected semantic chapters', () => {
    expect(normalizePolicySearch({ section: 'publication' })).toEqual({ section: 'publication' })
    expect(normalizePolicySearch({ section: 'curation' })).toEqual({ section: 'curation' })
    expect(normalizePolicySearch({ section: 'unsupported' })).toEqual({ section: 'unsupported' })
    expect(normalizePolicySearch({ section: 'effects' })).toEqual({ section: 'identity' })
  })

  const transition = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 'metadata-policy-transition-v1' as const,
    transition_id: 'policy_transition_1',
    from_policy_id: 'policy-a',
    to_policy_id: 'policy-a',
    generation: 1,
    state: 'completed',
    completed: 0,
    total: 0,
    failed: 0,
    acknowledged: false,
    origin: 'baseline',
    ...overrides,
  })

  it('never reports the bootstrap baseline as a completed owner activation', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyTransition: vi.fn(async () => transition() as never),
    }
    renderRoute('/policy', http)

    expect(await screen.findByRole('heading', { name: 'Library policy' })).toBeVisible()
    // The fresh container's seeded generation-1 row is the active baseline, not
    // an update the owner completed over zero items.
    expect(await screen.findByText(/^Active baseline$/u)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Activation complete' })).toBeNull()
    expect(screen.queryByText(/Policy update complete/u)).toBeNull()
    expect(screen.queryByText(/0 of 0/u)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Dismiss update' })).toBeNull()
  })

  it('keeps an owner activation that still needs attention visible in any session', async () => {
    const failing = transition({
      generation: 2,
      origin: 'activation',
      state: 'completed_with_failures',
      completed: 9,
      total: 11,
      failed: 2,
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyTransition: vi.fn(async () => failing as never),
    }
    renderRoute('/policy', http)

    expect(
      await screen.findByRole('heading', { name: 'Activation completed with failures' }),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Retry failed reconciliation' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Dismiss update' })).toBeVisible()
    expect(screen.getByText(/^Active policy$/u)).toBeVisible()
  })

  it('does not carry a clean completion into an unrelated session', async () => {
    const settled = transition({ generation: 2, origin: 'activation', completed: 11, total: 11 })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyTransition: vi.fn(async () => settled as never),
    }
    renderRoute('/policy', http)

    expect(await screen.findByText(/^Active policy$/u)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Activation complete' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Dismiss update' })).toBeNull()
  })

  it('returns every draft signal to Draft matches active policy after an exact revert', async () => {
    const user = userEvent.setup()
    renderRoute('/policy?section=curation')

    const tags = await screen.findByRole('region', { name: 'Tags' })
    expect(screen.getByRole('button', { name: /^Curation/u })).toHaveAccessibleDescription(
      '0 changed fields',
    )
    await user.click(within(tags).getByRole('button', { name: 'Ignore' }))

    expect(screen.getByRole('button', { name: /^Curation/u })).toHaveAccessibleDescription(
      '1 changed field',
    )
    expect(screen.getByText('Draft has unreviewed changes.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toBeEnabled()

    await user.click(within(tags).getByRole('button', { name: 'Ask me' }))

    expect(screen.getByRole('button', { name: /^Curation/u })).toHaveAccessibleDescription(
      '0 changed fields',
    )
    expect(screen.getByText('Draft matches active policy.')).toBeVisible()
    expect(screen.getByText('Draft matches the active policy')).toBeVisible()
    expect(screen.getByText(/^Active policy$/u)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toBeDisabled()
  })

  it('opens Automatic for the ASIN only once an audiobook record qualifies it', async () => {
    // The inert summary the API states today leaves every audiobook tranche
    // unqualified, so the page reads exactly the ebook records and the ASIN
    // stays unsupported — which the notebook test below pins with the same
    // inert payload. A published record makes that category routable with no
    // further frontend change, which is the activation path unit C needs, and
    // it moves nothing else: the narrators tranche stays unqualified here and
    // Contributor names keeps the ebook answer (#967).
    renderRoute('/policy?section=curation', {
      ...createHttp(),
      getShadowPosture: vi.fn(async (): Promise<ShadowPostureView> => ({
        schema_version: 'shadow-posture-v1',
        posture: 'preview',
        authority: 'none',
        can_leave_preview: false,
      })),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({
        schema_version: 'metadata-policy-notebook-v1',
        policy_id: 'policy-1',
        profile: {
          schema_version: 'metadata-policy-profile-v3',
          name: 'Default',
          options: {},
          destinations: {},
        },
        rich_qualification: [],
        audiobook_qualification: [
          {
            automatic_available: true,
            destination: 'asin',
            field: 'asin',
            identity: 'asin:preserve_exact:audiobook_asin_fill',
            operation: 'fill',
            option: 'preserve_exact',
            record_sha256: 'c'.repeat(64),
          },
          {
            automatic_available: false,
            destination: 'asin',
            field: 'asin',
            identity: 'asin:preserve_exact:audiobook_asin_replace_recording',
            operation: 'replace_recording',
            option: 'preserve_exact',
            record_sha256: null,
          },
          ...inertAudiobookQualification.filter((entry) => entry.field !== 'asin'),
        ],
      })),
    })
    const user = userEvent.setup()
    const asin = await screen.findByRole('region', { name: 'ASIN' })
    expect(within(asin).getByRole('button', { name: 'Automatic' })).toBeEnabled()
    expect(within(asin).queryByText('Automatic mode not supported')).toBeNull()
    expect(asin.querySelector('.cm-policy-media-scope')).toHaveTextContent(
      'Ebooks: Ask me — owner choice required. Automatic eligibility if selected: No automatic effect — owner review required',
    )
    expect(asin.querySelector('.cm-policy-media-scope')).toHaveTextContent(
      'Audiobooks: Ask me — owner choice required. Automatic eligibility if selected: Asin: fill',
    )
    await user.click(within(asin).getByText('Audiobook operations'))
    expect(within(asin).getByText(/Fill an empty field — Automatic available/u)).toBeVisible()
    expect(within(asin).getByText(/Replace a valid ASIN — Review required/u)).toBeVisible()

    // The record qualified one option, so the other one this field offers is
    // still the owner's.
    await user.click(within(asin).getByRole('combobox', { name: 'ASIN representation' }))
    await user.click(screen.getByRole('option', { name: 'Ebook manifestation' }))
    expect(within(asin).getByText('Automatic mode not supported')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Identity How a work/ }))
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    expect(within(title).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(title.querySelector('.cm-policy-media-scope')).toHaveTextContent(
      'Ebooks: Ask me — owner choice required. Automatic eligibility if selected: No automatic effect — owner review required',
    )
  })

  it('presents Policy as a semantic notebook and preserves guarded activation currentness', async () => {
    const user = userEvent.setup()
    const options = {
      title_layout: 'title_and_subtitle',
      date_precision: 'year',
      language_rendering: 'display_name',
      contributor_names: 'canonical',
      publisher_preference: 'publisher',
      subtitle: 'fill_empty',
      series: 'primary_narrative',
      genres: 'fill_empty',
      tags: 'off',
      description_fill: 'safe_plain_text',
      isbn_form: 'preserve_exact',
      cover_order: 'fixed_source_order',
      asin: 'preserve_exact',
      explicit: 'unsupported',
    }
    const destinations = {
      title_layout: 'ask',
      date_precision: 'ask',
      language_rendering: 'ask',
      contributor_names: 'ask',
      publisher_preference: 'ask',
      genres: 'automatic',
      tags: 'ignore',
      description_fill: 'automatic',
      isbn_form: 'ask',
      cover_order: 'ask',
      asin: 'ask',
      explicit: 'unsupported',
    }
    let validationCalls = 0
    let draftSaves = 0
    let activePolicyId = 'opaque-policy-identity'
    const staleResponse = vi.fn(async () =>
      Response.json(
        { schema_version: 'review-error-v1', error: { code: 'policy_activation_is_stale' } },
        { status: 409 },
      ),
    )
    const transport = new ReviewApiClient({
      buildIdentity: 'a'.repeat(64),
      csrf: { current: () => 'b'.repeat(32), renew: async () => 'b'.repeat(32) },
      fetch: staleResponse,
    })
    const policyCommand = vi.fn(async (path: string, body?: unknown): Promise<unknown> => {
      if (path === 'validate' && ++validationCalls === 1) throw new ApiError(422, 'invalid_policy')
      if (path === 'drafts' && ++draftSaves > 1) throw new AmbiguousMutationError()
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview') {
        activePolicyId = 'policy-preview-authority'
        return {
          active_policy_id: activePolicyId,
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [
            {
              library: 'Hostile <script> library',
              affected_categories: ['title_layout', 'tags'],
              cases: [],
              comparisons: [],
              cases_total: 0,
              cases_truncated: false,
              current_unapplied_count: 3,
              resolved_policy_id: 'resolved-policy',
            },
          ],
        }
      }
      if (path === 'activate') return transport.policyCommand(path, body)
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({
        schema_version: 'metadata-policy-notebook-v1',
        policy_id: activePolicyId,
        profile: {
          schema_version: 'metadata-policy-profile-v3',
          name: 'Qualified default',
          options,
          destinations,
        },
        audiobook_qualification: inertAudiobookQualification,
        rich_qualification: [
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'subtitle',
            option: 'fill_empty',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'none',
            disposition: 'not_qualified',
            field: 'subtitle',
            option: 'corroborated_split',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'series',
            option: 'primary_narrative',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'series',
            option: 'source_order',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'series',
            option: 'all_qualified',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'genres',
            option: 'fill_empty',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'none',
            disposition: 'policy_only',
            field: 'tags',
            option: 'off',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'isbn_form',
            option: 'preserve_exact',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'isbn_form',
            option: 'prefer_isbn13',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'fill',
            disposition: 'passing_automatic',
            field: 'description_fill',
            option: 'safe_plain_text',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'none',
            disposition: 'not_qualified',
            field: 'description_fill',
            option: 'google_visible_text',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'none',
            disposition: 'insufficient_coverage',
            field: 'asin',
            option: 'preserve_exact',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'none',
            disposition: 'insufficient_coverage',
            field: 'asin',
            option: 'ebook_manifestation',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            operation_scope: 'none',
            disposition: 'passing_human_only',
            field: 'explicit',
            option: 'unsupported',
          },
        ],
      })),
      policyCommand: <T,>(path: string, body?: unknown) => policyCommand(path, body) as Promise<T>,
    }
    const { queryClient } = renderRoute('/policy?section=publication', http)

    expect(await screen.findByRole('heading', { name: 'Library policy' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Publication' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Identity' })).toBeNull()
    expect(screen.queryByText('opaque-policy-identity')).toBeNull()
    expect(screen.queryByRole('textbox', { name: /Policy name/i })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save draft' })).toBeNull()
    expect(
      screen.getByText('Choose how metadata is stored and when you want to review it.'),
    ).toBeVisible()
    expect(
      screen.getByRole('button', {
        name: 'Identity How a work names itself and its contributors.',
      }),
    ).toHaveAccessibleDescription('0 changed fields')
    expect(
      screen.getByRole('button', {
        name: 'Publication How edition facts and memberships are represented.',
      }),
    ).toHaveAccessibleDescription('0 changed fields')
    expect(
      screen.getByRole('button', {
        name: 'Curation Fields that remain intentionally owner-directed.',
      }),
    ).toHaveAccessibleDescription('0 changed fields')
    expect(
      screen.getByRole('button', {
        name: 'Additional fields Fields with limited support that start off until you enable them.',
      }),
    ).toHaveAccessibleDescription('0 changed fields')
    expect(screen.getByText('Changed fields')).toBeVisible()
    expect(screen.getByRole('button', { name: /^Additional fields/ })).toHaveTextContent(
      'Fields with limited support that start off until you enable them.',
    )
    const isbn = screen.getByRole('region', { name: 'ISBN' })
    expect(within(isbn).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(within(isbn).getByText('Automatic mode not supported')).toBeVisible()
    await user.click(within(isbn).getByRole('button', { name: 'Ignore' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription(
      '1 changed field',
    )
    const publisher = screen.getByRole('region', { name: 'Publisher' })
    await user.click(within(publisher).getByRole('button', { name: 'Ignore' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription(
      '2 changed fields',
    )
    await user.click(screen.getByRole('button', { name: /Curation/ }))
    const tags = screen.getByRole('region', { name: 'Tags' })
    expect(tags).toHaveTextContent(
      'Tags are off by default. When enabled, use compatible source tags or review them yourself.',
    )
    expect(within(tags).getByText('Automatic mode not supported')).toBeVisible()
    expect(within(tags).getByRole('button', { name: 'Ask me' })).toBeEnabled()
    expect(within(tags).getByRole('button', { name: 'Ignore' })).toBeEnabled()
    const asin = screen.getByRole('region', { name: 'ASIN' })
    expect(within(asin).getByText('Automatic mode not supported')).toBeVisible()
    expect(within(asin).getByRole('button', { name: 'Ask me' })).toBeEnabled()
    expect(within(asin).getByRole('button', { name: 'Ignore' })).toBeEnabled()
    const description = screen.getByRole('region', { name: 'Description' })
    // Field help is collapsed until asked for, and then names every
    // representation on its own line as Option: owner-readable consequence.
    const help = within(description).getByText('What this changes')
    expect(help.closest('details')).not.toHaveAttribute('open')
    await user.click(help)
    expect(help.closest('details')).toHaveAttribute('open')
    const helpLines = within(description).getAllByRole('listitem')
    expect(helpLines).toHaveLength(3)
    expect(helpLines[0]).toHaveTextContent(
      'Fill empty with safe plain text: Fills an empty Description; preserves populated prose; abstains for unsafe or incomplete source text, conflicting work identity, or unavailable qualification.',
    )
    expect(helpLines[1]).toHaveTextContent(
      'Visible Google text: Fills an empty Description only; abstains without it and never falls back to Hardcover.',
    )
    expect(helpLines[2]).toHaveTextContent(
      'Fill empty or replace safe text: For audiobooks, fills an empty Description or replaces only whole chapter/track number and no-description/no-synopsis placeholders, preserving other useful prose; for ebooks, fills or replaces a Description with eligible source text; abstains for unsafe, incomplete or conflicting evidence; the audiobook placeholder rule does not assess general writing quality.',
    )
    await user.click(
      within(description).getByRole('combobox', { name: 'Description representation' }),
    )
    await user.click(screen.getByRole('option', { name: 'Visible Google text' }))
    expect(
      await screen.findByText(
        'Description now uses a Manual review only representation, so review mode changed from Automatic to Ask me.',
      ),
    ).toBeVisible()
    expect(within(description).getByRole('button', { name: 'Ask me' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(description).getByText(/never falls back to Hardcover/i)).toBeVisible()
    const changedGenres = screen.getByRole('region', { name: 'Genres' })
    await user.click(within(changedGenres).getByRole('button', { name: 'Ignore' }))
    expect(screen.getByRole('button', { name: /^Curation/ })).toHaveAccessibleDescription(
      '2 changed fields',
    )
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription(
      '2 changed fields',
    )
    await user.click(screen.getByRole('button', { name: /Publication/ }))
    const returnedPublisher = screen.getByRole('region', { name: 'Publisher' })
    await user.click(within(returnedPublisher).getByRole('button', { name: 'Ask me' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription(
      '1 changed field',
    )
    const returnedIsbn = screen.getByRole('region', { name: 'ISBN' })
    await user.click(within(returnedIsbn).getByRole('button', { name: 'Ask me' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription(
      '0 changed fields',
    )
    await user.click(screen.getByRole('button', { name: /Curation/ }))
    await user.click(screen.getByRole('button', { name: /Additional fields/ }))
    const explicit = screen.getByRole('region', { name: 'Explicit-content flag' })
    expect(within(explicit).getByText('Off until you choose a review mode')).toBeVisible()
    expect(
      within(explicit).getByRole('combobox', { name: 'Explicit-content flag representation' }),
    ).toBeEnabled()
    expect(within(explicit).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(screen.queryByRole('region', { name: 'Title and subtitle' })).toBeNull()
    expect(screen.getAllByText(/Draft has unreviewed changes/u).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('The draft is not valid. Review its field choices and try again.'),
    ).toBeVisible()
    const failure = screen.getByRole('alert')
    expect(failure).toHaveFocus()
    expect(failure.closest('.cm-policy-finish')).not.toBeNull()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('Impact preview is current. The active policy is unchanged.'),
    ).toBeVisible()
    expect(await screen.findByText('Hostile <script> library')).toBeVisible()
    expect(screen.getByText(/Title and subtitle, Tags/)).toBeVisible()
    expect(
      screen.getByText('No policy-caused changes in this sample', {
        selector: '.cm-policy-impact > b',
      }),
    ).toBeVisible()
    expect(
      screen.getByText(
        '· 3 current unapplied works remain; unchanged outcomes are separate context',
      ),
    ).toBeVisible()
    const stateCallsBeforeChange = vi.mocked(http.getPolicyState).mock.calls.length
    // Simulate A → B during preview, then B → A before activation. The
    // activation must stay bound to B, the server-issued preview authority.
    activePolicyId = 'opaque-policy-identity'
    await queryClient.invalidateQueries({ queryKey: ['policy', 'state'] })
    await waitFor(() =>
      expect(http.getPolicyState).toHaveBeenCalledTimes(stateCallsBeforeChange + 1),
    )
    const reviewActivation = screen.getByRole('button', { name: 'Review activation' })
    await user.click(reviewActivation)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByText('Activate this reviewed draft?')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to preview' }))
    expect(screen.getByRole('button', { name: 'Review activation' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))
    expect(
      await screen.findByText(
        'The active policy changed before activation. Review the refreshed policy and create a new draft.',
      ),
    ).toBeVisible()
    expect(policyCommand).toHaveBeenCalledWith('activate', {
      policy_id: 'policy-draft',
      expected_active_policy_id: 'policy-preview-authority',
      expected_active_generation: 1,
    })
    expect(staleResponse).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Review activation' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Activate policy' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText(
        'The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.',
      ),
    ).toBeVisible()
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual([
      'validate',
      'validate',
      'drafts',
      'preview',
      'activate',
      'validate',
      'drafts',
    ])
  })

  it('excludes unchanged outcomes from the primary policy impact list', async () => {
    const user = userEvent.setup()
    const unchanged = Array.from({ length: 6 }, (_, index) => ({
      current_value: `Retained ${String(index)}`,
      desired_value: `Retained ${String(index)}`,
      field: `unchanged_${String(index)}`,
      narrowed_for_target: false,
      option: 'preserve',
      outcome: 'projected',
      qualified_value: `Retained ${String(index)}`,
      reason: null,
      target_state: 'available',
    }))
    const caseFields0 = [
      ...unchanged,
      {
        narrowed_for_target: false,
        outcome: 'projected',
        reason: null,
        target_state: 'available',
        field: 'contributor_names',
        option: 'canonical',
        current_value: 'Previous narrator',
        desired_value: 'Complete recording cast',
        qualified_value: 'Complete recording cast',
      },
    ]
    const policyCommand = vi.fn<(path: string, body: unknown) => Promise<unknown>>(async (path) => {
      if (path === 'drafts')
        return { policy_id: 'policy-reviewed', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-preview-authority',
          policy_id: 'policy-reviewed',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [
            {
              library: 'Fiction',
              affected_categories: ['contributor_names'],
              comparisons: [
                { media_kind: 'audiobook', fields: comparisons(caseFields0) },
                { media_kind: 'audiobook', fields: comparisons(unchanged.slice(0, 1)) },
                { media_kind: 'ebook', fields: comparisons(unchanged.slice(0, 1)) },
              ],
              cases_total: 3,
              cases_truncated: false,
              current_unapplied_count: 3,
              resolved_policy_id: 'resolved-policy',
              cases: [
                {
                  fields: caseFields0,
                  proposal_set_id: 'proposal-1',
                  report_id: 'report-1',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'Recording book',
                },
                ...['Unchanged recording', 'Unchanged ebook'].map((work_label, index) => ({
                  fields: unchanged.slice(0, 1),
                  proposal_set_id: `unchanged-proposal-${String(index)}`,
                  report_id: `unchanged-report-${String(index)}`,
                  resolved_policy_id: 'resolved-policy',
                  work_label,
                })),
              ],
            },
          ],
        }
      if (path === 'activate') throw new ApiError(409, 'policy_transition_is_processing')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string, body: unknown) => policyCommand(path, body) as Promise<T>,
    }
    renderRoute('/policy?section=identity', http)
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('Policy change', { selector: '.cm-policy-impact > b' }),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Show 1 more outcome' })).toBeNull()
    expect(screen.getByText('Unchanged outcomes and projected library context (8)')).toBeVisible()
    expect(
      screen.getByText(
        'Ebooks: 1 sampled work · 0 changed fields · Audiobooks: 2 sampled works · 1 changed field',
      ),
    ).toBeVisible()
    await user.click(screen.getByText('Unchanged outcomes and projected library context (8)'))
    expect(
      screen.queryByText(/Complete recording cast/, { selector: '.cm-policy-projected-context p' }),
    ).toBeNull()
    expect(
      screen.getAllByText(/No policy change/, { selector: '.cm-policy-projected-context p' }),
    ).toHaveLength(8)
    expect(
      screen.getByText(/Complete recording cast/, { selector: '.cm-policy-impact small' }),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    expect(
      screen.getByText(/Complete recording cast/, { selector: '.cm-policy-impact small' }),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))
    await waitFor(() =>
      expect(policyCommand).toHaveBeenCalledWith('activate', {
        policy_id: 'policy-reviewed',
        expected_active_policy_id: 'policy-preview-authority',
        expected_active_generation: 1,
      }),
    )
    expect(policyCommand.mock.calls.filter(([path]) => path === 'activate')).toHaveLength(1)
    expect(policyCommand.mock.calls.filter(([path]) => path === 'preview')).toHaveLength(1)
  })

  it('identifies each affected work beside its exact policy outcome', async () => {
    const user = userEvent.setup()
    const field = (current_value: string, desired_value: string, name = 'title_layout') => ({
      current_value,
      desired_value,
      field: name,
      narrowed_for_target: false,
      option: 'title_only',
      outcome: 'projected',
      qualified_value: desired_value,
      reason: null,
      target_state: 'available',
    })
    const caseFields0 = [
      field('Old one', 'New one'),
      ...Array.from({ length: 7 }, (_, index) =>
        field(`Old extra ${String(index)}`, `New extra ${String(index)}`, `extra_${String(index)}`),
      ),
    ]
    const caseFields1 = [
      field('Old two', 'New two'),
      ...Array.from({ length: 7 }, (_, index) =>
        field(
          `Second old extra ${String(index)}`,
          `Second new extra ${String(index)}`,
          `second_extra_${String(index)}`,
        ),
      ),
    ]
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          rich_qualification: [],
          libraries: [
            {
              library: 'Fiction',
              affected_categories: ['title_layout'],
              comparisons: [
                { media_kind: 'audiobook', fields: comparisons(caseFields0) },
                { media_kind: 'audiobook', fields: comparisons(caseFields1) },
              ],
              cases_total: 2,
              cases_truncated: true,
              current_unapplied_count: 7,
              resolved_policy_id: 'resolved-policy',
              cases: [
                {
                  fields: caseFields0,
                  proposal_set_id: 'proposal-1',
                  report_id: 'report-1',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'First book',
                },
                {
                  fields: caseFields1,
                  proposal_set_id: 'proposal-2',
                  report_id: 'report-2',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'Second book',
                },
              ],
            },
          ],
        }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))

    expect((await screen.findAllByText('First book'))[0]).toBeVisible()
    expect(screen.getAllByText('Second book')[0]).toBeVisible()
    expect(screen.getAllByText('Audiobook · Fiction · Title and subtitle')).toHaveLength(2)
    expect(screen.getAllByText('Policy change')).toHaveLength(6)
    expect(screen.getByText(/New one/, { selector: '.cm-policy-impact small' })).toBeVisible()
    expect(screen.getByText(/New two/, { selector: '.cm-policy-impact small' })).toBeVisible()
    expect(screen.getByText('2 sampled works with changes')).toBeVisible()
    expect(screen.getByText(/16 material changes in the bounded sample/)).toBeVisible()
    expect(screen.getByLabelText('2 sampled works with changes')).toHaveTextContent('2')
  })

  it('tracks the protected whole-draft cadence and invalidates preview after an edit', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [
            {
              library: 'Empty library',
              affected_categories: [],
              cases: [],
              comparisons: [],
              cases_total: 0,
              cases_truncated: false,
              current_unapplied_count: 0,
              resolved_policy_id: 'resolved-policy',
            },
          ],
        }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    renderRoute('/policy?section=identity', http)

    const cadence = await screen.findByRole('list', { name: 'Policy activation steps' })
    const step = (name: string) => within(cadence).getByText(name).closest('li')
    expect(step('Validate')).toHaveAttribute('aria-current', 'step')
    expect(step('Validate')).not.toHaveClass('done')
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    const representation = within(title).getByRole('combobox', {
      name: 'Title and subtitle representation',
    })
    await user.click(representation)
    await user.click(await screen.findByRole('option', { name: 'Separate title and subtitle' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('Impact preview is current. The active policy is unchanged.'),
    ).toBeVisible()
    expect(screen.getByText('· No current unapplied work remains to inspect')).toBeVisible()
    expect(step('Validate')).toHaveClass('done')
    expect(step('Preview')).toHaveAttribute('aria-current', 'step')
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    expect(step('Preview')).toHaveClass('done')
    expect(step('Activate')).toHaveAttribute('aria-current', 'step')
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    expect(step('Validate')).toHaveAttribute('aria-current', 'step')
    expect(step('Validate')).not.toHaveClass('done')
    expect(screen.queryByText('Validated and saved')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Review activation' })).toBeNull()
  })

  it('keeps every completed Policy cadence marker after successful activation', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [],
        }
      if (path === 'activate')
        return {
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-activation-v1',
          transition: null,
        }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('Impact preview is current. The active policy is unchanged.'),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(
      await screen.findByText('Policy activated. Current unapplied work is being reconciled.'),
    ).toBeVisible()
    const cadence = screen.getByRole('list', { name: 'Policy activation steps' })
    for (const name of ['Validate', 'Preview', 'Activate']) {
      expect(within(cadence).getByText(name).closest('li')).toHaveClass('done')
    }
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual([
      'validate',
      'drafts',
      'preview',
      'activate',
    ])
  })

  it.each([
    ['invalid success payload', new ApiError(502, 'invalid_policy_response')],
    ['gateway timeout', new ApiError(504, 'gateway_timeout')],
    ['post-response build mismatch', new BuildMismatchError()],
  ])(
    'treats a potentially committed activation with %s as uncertain',
    async (_name, activationError) => {
      const user = userEvent.setup()
      const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
        if (path === 'drafts')
          return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
        if (path === 'preview')
          return {
            active_policy_id: 'policy-active',
            policy_id: 'policy-draft',
            schema_version: 'metadata-policy-preview-v1',
            active_generation: 1,
            libraries: [],
          }
        if (path === 'activate') throw activationError
        return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
      })
      const http: ReviewHttp = {
        ...createHttp(),
        policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
      }
      renderRoute('/policy?section=identity', http)

      const title = await screen.findByRole('region', { name: 'Title and subtitle' })
      await user.click(within(title).getByRole('button', { name: 'Ignore' }))
      await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
      await user.click(await screen.findByRole('button', { name: 'Review activation' }))
      await user.click(screen.getByRole('button', { name: 'Activate policy' }))

      expect(
        await screen.findByText(
          'The activation result is uncertain. Authoritative policy state is refreshing; verify it before trying again.',
        ),
      ).toBeVisible()
      expect(
        screen.queryByText(
          'Activation failed safely. The reviewed snapshot remains available to retry.',
        ),
      ).toBeNull()
    },
  )

  it('treats a malformed successful activation response as uncertain', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [],
        }
      if (path === 'activate') return {}
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(
      await screen.findByText(
        'The activation result is uncertain. Authoritative policy state is refreshing; verify it before trying again.',
      ),
    ).toBeVisible()
    expect(
      screen.queryByText('Policy activated. Current unapplied work is being reconciled.'),
    ).toBeNull()
  })

  it('retains the reviewed preview while an earlier policy transition is processing', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [],
        }
      if (path === 'activate') throw new ApiError(409, 'policy_transition_is_processing')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(
      await screen.findByText(
        'The previous policy update is still processing. This reviewed snapshot remains available; retry after the transition is resolved.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Review activation' })).toBeVisible()
  })
})

it('saves description preferences independently next to Cover guidance and keeps automatic off by default', async () => {
  const http = createHttp()
  const initial = view()
  http.getSettings = vi.fn(async () => initial)
  http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => ({
    ...initial,
    document: write.document,
  }))
  renderRoute('/policy?section=curation', http)
  const region = await screen.findByRole('region', { name: 'Description advice' })
  const marketing = await within(region).findByRole('checkbox', {
    name: 'Remove marketing material',
  })
  expect(marketing).toBeChecked()
  expect(
    within(region).getByRole('checkbox', { name: 'Remove sales figures and bestseller boasts' }),
  ).toBeChecked()
  expect(
    within(region).getByRole('checkbox', { name: 'Remove pull quotes and author praise' }),
  ).toBeChecked()
  expect(
    within(region).getByRole('checkbox', { name: 'Automatically apply ready description advice' }),
  ).not.toBeChecked()
  expect(screen.getByRole('region', { name: 'Cover' }).nextElementSibling).toBe(region)
  expect(region.nextElementSibling).toHaveAccessibleName('ASIN')
  await userEvent.setup().click(marketing)
  expect(within(region).getByRole('status')).toHaveTextContent(
    'Policy draft changes are saved separately',
  )
  await userEvent
    .setup()
    .click(within(region).getByRole('button', { name: 'Save description preferences' }))
  await waitFor(() =>
    expect(within(region).getByRole('status')).toHaveTextContent('Description preferences saved.'),
  )
  expect(http.writeSettings).toHaveBeenCalledWith({
    expected_revision: 3,
    secrets: [],
    document: {
      ...initial.document,
      revision: 4,
      description_advice: { ...initial.document.description_advice, remove_marketing: false },
    },
  })
  expect(within(region).getByRole('link')).toHaveAttribute('href', '/settings?group=reviewer')
})

it('shows locked description cleanup preferences without enabling edits', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view('file'))
  renderRoute('/policy?section=curation', http)
  const marketing = await screen.findByRole('checkbox', { name: 'Remove marketing material' })
  expect(marketing).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Save description preferences' })).toBeDisabled()
  expect(
    screen.getByText('These preferences are managed by deployment configuration.'),
  ).toBeVisible()
})

it('focuses the description preferences heading from its direct setup link', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view())
  window.history.replaceState({}, '', '/policy?section=curation#description-advice')
  try {
    renderRoute('/policy?section=curation#description-advice', http)
    const heading = await screen.findByRole('heading', { name: 'Description advice' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(heading.scrollIntoView).toHaveBeenCalledWith({ block: 'start' })
  } finally {
    window.history.replaceState({}, '', '/')
  }
})

it('keeps explicit off until the owner chooses its representation and review mode', async () => {
  const http = createHttp()
  const command = vi.spyOn(http, 'policyCommand')
  renderRoute('/policy?section=unsupported', {
    ...http,
    getShadowPosture: vi.fn(async (): Promise<ShadowPostureView> => ({
      schema_version: 'shadow-posture-v1',
      posture: 'preview',
      authority: 'none',
      can_leave_preview: false,
    })),
    getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({
      schema_version: 'metadata-policy-notebook-v1',
      policy_id: 'policy-explicit',
      profile: {
        schema_version: 'metadata-policy-profile-v3',
        name: 'Default',
        options: {},
        destinations: {},
      },
      rich_qualification: [],
      audiobook_qualification: [
        {
          automatic_available: true,
          destination: 'explicit',
          field: 'explicit',
          identity: 'explicit:adult_product:audiobook_explicit_fill',
          operation: 'fill',
          option: 'adult_product',
          record_sha256: 'c'.repeat(64),
        },
      ],
    })),
  })
  const user = userEvent.setup()
  const flag = await screen.findByRole('region', { name: 'Explicit-content flag' })
  expect(within(flag).getByText('Off until you choose a review mode')).toBeVisible()
  expect(within(flag).getByRole('button', { name: 'Automatic' })).toBeDisabled()
  await user.click(
    within(flag).getByRole('combobox', { name: 'Explicit-content flag representation' }),
  )
  await user.click(screen.getByRole('option', { name: 'Set from adult-product evidence' }))
  expect(within(flag).getByText('Off until you choose a review mode')).toBeVisible()
  const automatic = within(flag).getByRole('button', { name: 'Automatic' })
  expect(automatic).toBeEnabled()
  expect(automatic).toHaveAttribute('aria-pressed', 'false')
  await user.click(automatic)
  expect(automatic).toHaveAttribute('aria-pressed', 'true')
  expect(command).not.toHaveBeenCalled()
  expect(within(flag).getByText(/Missing or false evidence never clears it/)).toBeVisible()
})

it.each([
  ['identity', ['Title and subtitle', 'Authors and narrators', 'Language', 'Subtitle']],
  ['publication', ['Publication date and year', 'Publisher', 'ISBN', 'Series memberships']],
  ['curation', ['Genres', 'Tags', 'Description', 'Cover', 'ASIN']],
] as const)(
  'keeps existing empty-profile destinations on Ask me in %s',
  async (chapter, labels) => {
    const http = createHttp()
    http.getPolicyState = vi.fn(async (): Promise<PolicyStateView> => ({
      schema_version: 'metadata-policy-notebook-v1',
      policy_id: 'policy-empty-defaults',
      profile: {
        schema_version: 'metadata-policy-profile-v3',
        name: 'Default',
        options: {},
        destinations: {},
      },
      rich_qualification: [],
      audiobook_qualification: [],
    }))
    renderRoute(`/policy?section=${chapter}`, http)
    for (const label of labels) {
      const field = await screen.findByRole('region', { name: label })
      expect(within(field).getByRole('button', { name: 'Ask me' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
      expect(within(field).getByRole('button', { name: 'Automatic' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
    }
    if (chapter === 'curation') {
      const tags = screen.getByRole('region', { name: 'Tags' })
      expect(within(tags).getByRole('combobox', { name: 'Tags representation' })).toHaveTextContent(
        'Off',
      )
      const cover = screen.getByRole('region', { name: 'Cover' })
      expect(
        within(cover).getByRole('combobox', { name: 'Cover representation' }),
      ).toHaveTextContent('Fill from confirmed order')
    }
  },
)

it('explains title re-evaluation, retains retry, and restores focus after recovery', async () => {
  const user = userEvent.setup()
  let recovered = false
  let fail = true
  const command = vi.fn(async () => {
    if (fail) throw new ApiError(503, 'persistence')
    recovered = true
    return {}
  })
  const http: ReviewHttp = {
    ...createHttp(),
    getAutomaticSafety: vi.fn(async () => ({
      paused: true,
      global_suspension: null,
      category_suspensions: recovered
        ? { publisher_preference: 'other_failure' }
        : { title_layout: 'automatic_admission_failed', publisher_preference: 'other_failure' },
    })),
    policyCommand: <T,>() => command() as Promise<T>,
  }
  renderRoute('/policy', http)
  await screen.findByRole('heading', { name: 'Library policy' })
  const summary = screen.getByText('Safety').closest('summary')
  if (!summary) throw new Error('Safety summary missing')
  await user.click(summary)
  expect(
    screen.getByText(
      /Eligible completed books are re-evaluated from retained evidence once the title suspension, automatic pause and global suspension are all cleared/u,
    ),
  ).toBeVisible()
  await user.click(screen.getByRole('button', { name: /Recover .*title.* suspension/iu }))
  expect(await screen.findByText(/Nothing changed. Review the current server state/u)).toBeVisible()
  expect(screen.getByRole('button', { name: /Recover .*title.* suspension/iu })).toBeEnabled()
  fail = false
  await user.click(screen.getByRole('button', { name: /Recover .*title.* suspension/iu }))
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: /Recover .*title.* suspension/iu })).toBeNull(),
  )
  expect(summary).toHaveFocus()
  expect(screen.getByRole('button', { name: /Recover .*publisher.* suspension/iu })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Resume automatic decisions' })).toBeVisible()
})

it.each([
  ['missing', 'title'],
  ['terminal', 'title'],
  ['missing', 'pause'],
  ['terminal', 'pause'],
  ['missing', 'global'],
  ['terminal', 'global'],
] as const)(
  'refreshes cached %s preparation after %s release before same-session Library navigation',
  async (prior, release) => {
    const user = userEvent.setup()
    let recovered = false
    const presentation = {
      run_id: `preparation_run_${'a'.repeat(64)}`,
      state: 'completed_clean' as const,
      triggers: [
        {
          trigger: 'manual_scan' as const,
          accepted_count: 1,
          first_accepted_at: 100,
          last_accepted_at: 100,
        },
      ],
      passage: { read: 1, searches_finished: 1, compared: 1, checked: 1, ready: 1, failed: 0 },
      active_books: [],
      active_overflow: 0,
      recent_completions: [],
      throughput_per_minute: 0,
      started_at: 100,
      updated_at: 101,
      completed_at: 101,
      total: 1,
      dismissible: true,
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v2',
        recovery_required: false,
        active: recovered,
        actionable: false,
        paused: false,
        libraries: [],
        connections: [],
        presentation: recovered
          ? {
              ...presentation,
              state: 'running',
              completed_at: null,
              updated_at: 102,
              dismissible: false,
              triggers: [
                {
                  trigger: 'title_recovery',
                  accepted_count: 1,
                  first_accepted_at: 102,
                  last_accepted_at: 102,
                },
              ],
            }
          : prior === 'missing'
            ? null
            : presentation,
      })),
      getAutomaticSafety: vi.fn(async () => ({
        paused: !recovered && release === 'pause',
        global_suspension: !recovered && release === 'global' ? 'retained_global' : null,
        category_suspensions:
          !recovered && release === 'title' ? { title_layout: 'automatic_admission_failed' } : {},
      })),
      policyCommand: async <T,>() => {
        recovered = true
        return {} as T
      },
    }
    const { queryClient } = renderRoute('/policy', http)
    await screen.findByRole('heading', { name: 'Library policy' })
    await waitFor(() =>
      expect(queryClient.getQueryData(['review-preparation'])).toMatchObject({
        presentation: prior === 'missing' ? null : { state: 'completed_clean' },
      }),
    )
    const summary = screen.getByText('Safety').closest('summary')
    if (!summary) throw new Error('Safety summary missing')
    await user.click(summary)
    await user.click(
      screen.getByRole('button', {
        name:
          release === 'pause'
            ? 'Resume automatic decisions'
            : release === 'global'
              ? /Recover global suspension/iu
              : /Recover .*title.* suspension/iu,
      }),
    )
    await screen.findByText(
      release === 'pause'
        ? /Policy state updated from the authoritative server/u
        : /Suspension recovered/u,
    )
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: /Preparing your library/u }))
    expect(await screen.findByText('Title recovery requested')).toBeVisible()
    expect(queryClient.getQueryData(['review-preparation'])).toMatchObject({
      presentation: { state: 'running', total: 1, passage: { ready: 1 } },
    })
    expect(http.getPreparation).toHaveBeenCalledTimes(2)
  },
)

it.each([
  ['ignore', 'Ignored — no metadata action'],
  ['ask', 'Ask me — owner choice required'],
  ['automatic', 'Automatic —'],
])(
  'states selected %s behavior before eligibility in cards and activation',
  async (mode, label) => {
    const user = userEvent.setup()
    const http = createHttp()
    const baseline = await http.getPolicyState()
    http.getPolicyState = vi.fn(async (): Promise<PolicyStateView> => ({
      ...baseline,
      rich_qualification: (['fill_empty', 'add'] as const).map((option) => ({
        automatic_available: true,
        candidate_sha256: 'a'.repeat(64),
        disposition: 'passing_automatic',
        field: 'tags',
        operation_scope: option === 'add' ? 'add' : 'fill',
        option,
      })),
      profile: {
        ...baseline.profile,
        options: { tags: 'fill_empty' },
        destinations: { tags: mode },
      },
    }))
    http.policyCommand = async <T,>(path: string) =>
      (path === 'preview'
        ? {
            schema_version: 'metadata-policy-preview-v1',
            policy_id: 'draft',
            active_policy_id: 'policy-1',
            active_generation: 7,
            libraries: [],
          }
        : { policy_id: 'draft', schema_version: 'metadata-policy-profile-v3' }) as T
    renderRoute('/policy?section=curation', http)
    const tags = await screen.findByRole('region', { name: 'Tags' })
    expect(tags.querySelector('.cm-policy-media-scope')).toHaveTextContent(`Ebooks: ${label}`)
    expect(tags.querySelector('.cm-policy-media-scope')).toHaveTextContent(`Audiobooks: ${label}`)
    if (mode !== 'automatic')
      expect(tags.querySelector('.cm-policy-media-scope')).toHaveTextContent(
        'Automatic eligibility if selected:',
      )
    await user.click(within(tags).getByRole('combobox', { name: 'Tags representation' }))
    await user.click(screen.getByRole('option', { name: 'Add missing values' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    expect(screen.getByLabelText('Review policy activation')).toHaveTextContent(`Ebooks — ${label}`)
    expect(screen.getByLabelText('Review policy activation')).toHaveTextContent(
      `Audiobooks — ${label}`,
    )
  },
)

it('keeps the draft after a real stale envelope and binds explicit repreview to the new generation', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  let generation = 1
  const transport = new ReviewApiClient({
    buildIdentity: 'a'.repeat(64),
    csrf: { current: () => 'b'.repeat(32), renew: async () => 'b'.repeat(32) },
    fetch: vi.fn(async () =>
      Response.json(
        { schema_version: 'review-error-v1', error: { code: 'policy_activation_is_stale' } },
        { status: 409 },
      ),
    ),
  })
  const commands = vi.fn(async (path: string, body?: unknown): Promise<unknown> => {
    if (path === 'preview')
      return {
        schema_version: 'metadata-policy-preview-v1',
        policy_id: 'draft',
        active_policy_id: 'policy-1',
        active_generation: generation,
        libraries: [],
      }
    if (path === 'activate' && generation === 1) {
      generation = 3
      return transport.policyCommand(path, body)
    }
    if (path === 'activate')
      return {
        schema_version: 'metadata-policy-activation-v1',
        policy_id: 'draft',
        transition: null,
      }
    return { policy_id: 'draft', schema_version: 'metadata-policy-profile-v3' }
  })
  http.policyCommand = async <T,>(path: string, body?: unknown) => (await commands(path, body)) as T
  renderRoute('/policy?section=identity', http)
  const title = await screen.findByRole('region', { name: 'Title and subtitle' })
  await user.click(within(title).getByRole('button', { name: 'Ignore' }))
  await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
  await user.click(await screen.findByRole('button', { name: 'Review activation' }))
  await user.click(screen.getByRole('button', { name: 'Activate policy' }))
  expect(
    await screen.findByText(
      'The active policy changed before activation. Review the refreshed policy and create a new draft.',
    ),
  ).toBeVisible()
  expect(within(title).getByRole('button', { name: 'Ignore' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(commands).toHaveBeenCalledWith('activate', {
    policy_id: 'draft',
    expected_active_policy_id: 'policy-1',
    expected_active_generation: 1,
  })
  await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
  await user.click(await screen.findByRole('button', { name: 'Review activation' }))
  await user.click(screen.getByRole('button', { name: 'Activate policy' }))
  expect(
    await screen.findByText('Policy activated. Current unapplied work is being reconciled.'),
  ).toBeVisible()
  expect(commands).toHaveBeenLastCalledWith('activate', {
    policy_id: 'draft',
    expected_active_policy_id: 'policy-1',
    expected_active_generation: 3,
  })
})
