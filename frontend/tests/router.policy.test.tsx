import { act, screen, waitFor, within } from '@testing-library/react'
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
    expect(within(series).getByText('All supported memberships:')).toBeVisible()
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
    let read: (value: never) => void = () => undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyTransition: vi.fn(
        () =>
          new Promise<never>((resolve) => {
            read = resolve
          }),
      ),
    }
    renderRoute('/policy', http)

    // The live region exists, empty, before the finished transition is read,
    // so its arrival is an announced insertion rather than a mount.
    expect(await screen.findByRole('heading', { name: 'Library policy' })).toBeVisible()
    await waitFor(() => expect(http.getPolicyTransition).toHaveBeenCalled())
    const region = document.querySelector('.cm-policy-route > section > [role="status"]')
    expect(region).toBeEmptyDOMElement()
    expect(region?.parentElement).not.toHaveAttribute('class')
    expect(screen.queryByRole('region', { name: 'Policy activation progress' })).toBeNull()
    read(failing as never)
    const heading = await screen.findByRole('heading', {
      name: 'Activation completed with failures',
    })
    expect(heading).toBeVisible()
    expect(heading.closest('[role="status"]')).toBe(region)
    expect(screen.getByRole('button', { name: 'Retry failed reconciliation' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Dismiss update' })).toBeVisible()
    expect(screen.getByText(/^Active policy$/u)).toBeVisible()
  })

  it('announces activation progress and its failure from one status region', async () => {
    let reads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyTransition: vi.fn(async () => {
        reads += 1
        return transition({
          generation: 2,
          origin: 'activation',
          ...(reads === 1
            ? { state: 'processing', completed: 3, total: 11 }
            : { state: 'completed_with_failures', completed: 9, total: 11, failed: 2 }),
        }) as never
      }),
    }
    renderRoute('/policy', http)

    const processing = await screen.findByRole('heading', { name: 'Applying updated policy' })
    const region = processing.closest('[role="status"]')
    expect(region).toHaveTextContent(
      /^Applying updated policy3 of 11 current unapplied items reconciled\. Applied history is untouched\.$/u,
    )
    const failed = await screen.findByRole(
      'heading',
      { name: 'Activation completed with failures' },
      { timeout: 4_000 },
    )
    // The same live region is updated in place, so the change is announced
    // once, and no other status repeats the reconciliation.
    expect(failed.closest('[role="status"]')).toBe(region)
    expect(region).toHaveTextContent(
      /^Activation completed with failures9 of 11 current unapplied items reconciled\. Applied history is untouched\.$/u,
    )
    expect(
      screen.getAllByRole('status').filter((node) => node.textContent.includes('reconcil')),
    ).toEqual([region])
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
    const curation = screen.getByRole('button', { name: /^Curation/u })
    expect(curation).not.toHaveAccessibleDescription()
    expect(curation.querySelector('.cm-change-count')).toBeNull()
    expect(screen.queryByText('Changed fields')).toBeNull()
    await user.click(within(tags).getByRole('button', { name: 'Ignore' }))

    expect(curation).toHaveAccessibleDescription('1 changed field')
    expect(curation.querySelector('.cm-change-count')).toHaveTextContent('1')
    expect(within(tags).getByText('Draft changed')).toHaveClass('status-pill', 'is-notice')
    expect(screen.getByText('Changed fields')).toBeVisible()
    expect(screen.getByText('Draft has unreviewed changes.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toHaveClass(
      'button-primary',
    )

    await user.click(within(tags).getByRole('button', { name: 'Ask me' }))

    expect(curation).not.toHaveAccessibleDescription()
    expect(curation.querySelector('.cm-change-count')).toBeNull()
    expect(screen.queryByText('Changed fields')).toBeNull()
    expect(screen.getByText('Draft matches active policy.')).toBeVisible()
    // The tray is the one visible status; the sr-only live region is excluded.
    expect(screen.getAllByText(/^Draft matches (the )?active policy\.?$/u)).toHaveLength(2)
    expect(screen.getByText('Draft matches the active policy')).toBeVisible()
    expect(screen.getByText(/^Active policy$/u)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toBeDisabled()
  })

  describe('a failed snapshot save', () => {
    const run = async (failing: string, failure: Error) => {
      const user = userEvent.setup()
      const policyCommand = vi.fn<(path: string, body: unknown) => Promise<unknown>>(
        async (path) => {
          if (path === failing) throw failure
          return {}
        },
      )
      const http: ReviewHttp = { ...createHttp(), policyCommand: policyCommand as never }
      renderRoute('/policy?section=curation', http)
      const tags = await screen.findByRole('region', { name: 'Tags' })
      await user.click(within(tags).getByRole('button', { name: 'Ignore' }))
      const readsBefore = vi.mocked(http.getPolicyState).mock.calls.length
      await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
      return { http, policyCommand, readsBefore }
    }

    it('says a snapshot that may have been saved could not be confirmed', async () => {
      const { http, readsBefore } = await run('drafts', new ApiError(503, 'persistence'))

      expect(
        await screen.findByText(
          'The snapshot could not be confirmed. It may have been saved; the current policy state was reloaded. Validating again is safe and reuses the same snapshot.',
        ),
      ).toBeVisible()
      expect(screen.queryByText(/snapshot was not saved/u)).toBeNull()
      // The authoritative state is read again rather than assumed.
      await waitFor(() =>
        expect(vi.mocked(http.getPolicyState).mock.calls.length).toBeGreaterThan(readsBefore),
      )
    })

    it('keeps the invalid-draft copy for a refused snapshot', async () => {
      await run('drafts', new ApiError(422, 'policy_validation'))

      expect(
        await screen.findByText('The draft is not valid. Review its field choices and try again.'),
      ).toBeVisible()
    })

    it('keeps the nothing-saved copy when validation itself is unavailable', async () => {
      const { policyCommand } = await run('validate', new ApiError(503, 'persistence'))

      expect(
        await screen.findByText(
          'The complete draft could not be validated. Nothing was saved or activated; try again.',
        ),
      ).toBeVisible()
      expect(policyCommand.mock.calls.map(([path]) => path)).toEqual(['validate'])
    })
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
            destination: 'asin' as const,
            field: 'asin',
            identity: 'asin:preserve_exact:audiobook_asin_fill',
            operation: 'fill',
            option: 'preserve_exact',
            record_sha256: 'c'.repeat(64),
          },
          {
            automatic_available: false,
            destination: 'asin' as const,
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
    expect(within(asin).queryByText('Automatic not supported')).toBeNull()
    expect(asin.querySelector('.cm-policy-media-scope')).toHaveTextContent(
      'Ebooks: Ask me — owner choice required. Automatic eligibility if selected: No automatic effect — owner review required',
    )
    expect(asin.querySelector('.cm-policy-media-scope')).toHaveTextContent(
      'Audiobooks: Ask me — owner choice required. Automatic eligibility if selected: ASIN: fill empty',
    )
    await user.click(within(asin).getByText('Audiobook operations'))
    expect(within(asin).getByText(/Fill an empty field — Automatic available/u)).toBeVisible()
    expect(within(asin).getByText(/Replace a valid ASIN — Review required/u)).toBeVisible()

    // The record qualified one option, so the other one this field offers is
    // still the owner's.
    await user.click(within(asin).getByRole('combobox', { name: 'ASIN representation' }))
    await user.click(screen.getByRole('option', { name: 'Ebook edition ASIN' }))
    expect(within(asin).getByText('Automatic not supported')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Identity How a work/ }))
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    expect(within(title).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(title.querySelector('.cm-policy-media-scope')).toHaveTextContent(
      'Ebooks: Ask me — owner choice required. Automatic eligibility if selected: No automatic effect — owner review required',
    )
  })

  it('says Language fills and respells the same language on both media (#1496)', async () => {
    renderRoute('/policy?section=identity', {
      ...createHttp(),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({
        schema_version: 'metadata-policy-notebook-v1',
        policy_id: 'policy-1',
        profile: {
          schema_version: 'metadata-policy-profile-v3',
          name: 'Default',
          options: {},
          destinations: {},
        },
        rich_qualification: [
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'pass',
            field: 'language_rendering',
            option: 'display_name',
            operation_scope: 'fill_or_respell',
          },
        ],
        audiobook_qualification: [
          ...(['fill', 'preserve', 'normalize'] as const).map((operation) => ({
            automatic_available: true,
            destination: 'language' as const,
            field: 'language_rendering' as const,
            identity: `language_rendering:display_name:audiobook_language_${operation}`,
            operation,
            option: 'display_name' as const,
            record_sha256: 'c'.repeat(64),
          })),
          ...inertAudiobookQualification.filter((entry) => entry.field !== 'language_rendering'),
        ],
      })),
    })
    const language = await screen.findByRole('region', { name: 'Language' })
    const scope = language.querySelector('.cm-policy-media-scope')
    expect(scope).not.toBeNull()
    expect(scope).toHaveTextContent(
      'Automatic eligibility if selected: Fill empty or respell the same language',
    )
    expect(scope?.textContent.match(/Fill empty or respell the same language/gu)).toHaveLength(2)
    expect(scope).not.toHaveTextContent('Fill empty only')
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
    ).not.toHaveAccessibleDescription()
    expect(
      screen.getByRole('button', {
        name: 'Publication How edition facts and memberships are represented.',
      }),
    ).not.toHaveAccessibleDescription()
    expect(
      screen.getByRole('button', {
        name: 'Curation Fields that remain intentionally owner-directed.',
      }),
    ).not.toHaveAccessibleDescription()
    expect(
      screen.getByRole('button', {
        name: 'Additional fields Fields with limited support that start off until you enable them.',
      }),
    ).not.toHaveAccessibleDescription()
    expect(screen.queryByText('Changed fields')).toBeNull()
    expect(screen.getByRole('button', { name: /^Additional fields/ })).toHaveTextContent(
      'Fields with limited support that start off until you enable them.',
    )
    const isbn = screen.getByRole('region', { name: 'ISBN' })
    expect(within(isbn).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(within(isbn).getByText('Automatic not supported')).toBeVisible()
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
    expect(within(tags).getByText('Automatic not supported')).toBeVisible()
    expect(within(tags).getByRole('button', { name: 'Ask me' })).toBeEnabled()
    expect(within(tags).getByRole('button', { name: 'Ignore' })).toBeEnabled()
    const asin = screen.getByRole('region', { name: 'ASIN' })
    expect(within(asin).getByText('Automatic not supported')).toBeVisible()
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
      'Fill empty with safe plain text: Fills an empty Description; preserves populated prose; makes no change for unsafe or incomplete source text, conflicting work identity, or when Automatic is unavailable.',
    )
    expect(helpLines[1]).toHaveTextContent(
      'Visible Google text: Fills an empty Description only; makes no change without it and never falls back to Hardcover.',
    )
    expect(helpLines[2]).toHaveTextContent(
      'Fill empty or replace safe text: For audiobooks, fills an empty Description or replaces only whole chapter/track number and no-description/no-synopsis placeholders, preserving other useful prose; for ebooks, fills or replaces a Description with eligible source text; makes no change for unsafe, incomplete or conflicting evidence; the audiobook placeholder rule does not judge writing quality.',
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
    expect(screen.getByRole('button', { name: /^Publication/ })).not.toHaveAccessibleDescription()
    await user.click(screen.getByRole('button', { name: /Curation/ }))
    await user.click(screen.getByRole('button', { name: /Additional fields/ }))
    const explicit = screen.getByRole('region', { name: 'Explicit-content flag' })
    expect(
      within(explicit).getByText('Off until you choose a review mode · Automatic not supported'),
    ).toBeVisible()
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
    expect(failure.closest('.cm-finish-tray')).not.toBeNull()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('Impact preview is current. The active policy is unchanged.'),
    ).toBeVisible()
    // No case was sampled, so neither changed rule was evaluated: each is
    // stated as not previewed rather than as unchanged.
    const outcomes = await screen.findByText('No current unapplied work to sample.')
    expect(outcomes).toBeVisible()
    const unpreviewed = [
      ...document.querySelectorAll('#policy-preview-outcomes > .is-unpreviewed'),
    ].map((row) => row.textContent)
    expect(unpreviewed).toEqual([
      'HOTitle and subtitleHostile <script> libraryNot previewed.This rule takes effect on the next preparation.',
      'HOTagsHostile <script> libraryNot previewed.This rule takes effect on the next preparation.',
    ])
    expect(screen.queryByText('No policy-caused changes in this sample')).toBeNull()
    expect(screen.getByText('Impact preview ready')).toBeVisible()
    expect(screen.queryByLabelText(/sampled works with changes/u)).toBeNull()
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

  it('reads an Ask me preview row as a proposal and the finish tray in plain words (#1286)', async () => {
    const user = userEvent.setup()
    const draft = {
      narrowed_for_target: false,
      outcome: 'projected',
      reason: null,
      target_state: 'missing',
      field: 'contributor_names',
      option: 'canonical',
      current_value: null,
      desired_value: 'Complete recording cast',
      qualified_value: 'Complete recording cast',
    }
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
                {
                  media_kind: 'audiobook',
                  fields: [
                    {
                      // Under Ask me nothing is written: the active policy
                      // would put this value to the owner.
                      active: { ...draft, desired_value: 'Proposed cast' },
                      draft,
                      active_destination: 'ask',
                      draft_destination: 'automatic',
                      changed: true,
                    },
                  ],
                },
              ],
              cases_total: 1,
              cases_truncated: false,
              current_unapplied_count: 1,
              resolved_policy_id: 'resolved-policy',
              cases: [
                {
                  fields: [draft],
                  proposal_set_id: 'proposal-1',
                  report_id: 'report-1',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'Recording book',
                },
              ],
            },
          ],
        }
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
      await screen.findByText(
        '· Ask me: would propose filling the empty field with Proposed cast → Automatic: fills the empty field with Complete recording cast',
        { selector: '.cm-policy-impact small' },
      ),
    ).toBeVisible()
    expect(screen.queryByText(/\b(?:ask|automatic|ignore): /u)).toBeNull()
    expect(
      screen.getByText(
        /draft saved; activate to keep it — reloading starts from the active policy\.$/u,
      ),
    ).toBeVisible()
    expect(screen.queryByText(/snapshot saved automatically/u)).toBeNull()
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
    expect(screen.getByText('Unchanged outcomes and current library context (8)')).toBeVisible()
    expect(
      screen.getByText('Ebooks: 1 sampled work · Audiobooks: 2 sampled works · 1 changed field'),
    ).toBeVisible()
    await user.click(screen.getByText('Unchanged outcomes and current library context (8)'))
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
    expect(screen.getByText(/16 changes in the sample/)).toBeVisible()
    // No design-document term reaches the owner on Policy (#1286 X5); only
    // the Technical detail disclosure quotes the server's own codes.
    const owner = document.body.cloneNode(true) as HTMLElement
    for (const code of owner.querySelectorAll('code')) code.remove()
    expect(owner.textContent).not.toMatch(
      /qualified|admitted|projected|bounded|manifestation|abstain/iu,
    )
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
    expect(screen.getByText('No current unapplied work to sample.')).toBeVisible()
    expect(document.getElementById('policy-preview-outcomes')).toBeEmptyDOMElement()
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

    expect(await screen.findByText('Policy activated.')).toBeVisible()
    const cadence = screen.getByRole('list', { name: 'Policy activation steps' })
    for (const name of ['Validate', 'Preview', 'Activate']) {
      expect(within(cadence).getByText(name).closest('li')).toHaveClass('done')
    }
    // One success message, and it holds the focus the Activate button gave up.
    const message = screen.getByText('Policy activated').parentElement
    await waitFor(() => expect(message).toHaveFocus())
    expect(message).toHaveAttribute('tabindex', '-1')
    expect(message).toHaveTextContent(
      'Policy activatedNo current unapplied work needed reconciliation. Applied history is untouched.',
    )
    expect(
      screen.getAllByText(/Policy activated|is now active|Active policy updated/u),
    ).toHaveLength(2)
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual([
      'validate',
      'drafts',
      'preview',
      'activate',
    ])
  })

  const previewField = (field: string, current: string, desired = current) => ({
    current_value: current,
    desired_value: desired,
    field,
    narrowed_for_target: false,
    option: 'fill_empty',
    outcome: 'projected',
    qualified_value: desired,
    reason: null,
    target_state: 'available',
  })
  const previewCase = (index: number, fields: ReturnType<typeof previewField>[]) => ({
    fields,
    proposal_set_id: `proposal-${String(index)}`,
    report_id: `report-${String(index)}`,
    resolved_policy_id: 'resolved-policy',
    work_label: `Work ${String(index)}`,
  })
  const tagChange = previewField('tags', 'Old tag', 'New tag')
  const isbnSame = previewField('isbn_form', '9780000000002')
  const outcomeRows = () =>
    [...document.querySelectorAll('#policy-preview-outcomes > div')].map((row) =>
      [row.querySelector('strong'), row.querySelector('.cm-policy-impact > b')]
        .map((node) => node?.textContent)
        .join(': '),
    )

  it.each([
    {
      name: 'a previewed rule only',
      affected: ['tags'],
      cases: [previewCase(1, [tagChange])],
      rows: ['Work 1: Policy change'],
      title: '1 sampled work with changes',
      media: 'Ebooks: 1 sampled work · 1 changed field',
    },
    {
      name: 'an unpreviewed rule only',
      affected: ['language_rendering'],
      cases: [previewCase(1, [previewField('tags', 'Kept')])],
      rows: ['Language: Not previewed.'],
      title: 'Impact preview ready',
      media: 'Ebooks: 1 sampled work',
    },
    {
      name: 'an unpreviewed rule beside an evaluated unchanged one',
      affected: ['language_rendering', 'isbn_form'],
      cases: [previewCase(1, [isbnSame])],
      rows: ['Fiction: No policy-caused changes in this sample', 'Language: Not previewed.'],
      title: 'Impact preview ready',
      media: 'Ebooks: 1 sampled work',
    },
    {
      name: 'mixed previewed and unpreviewed rules beyond the six-row limit',
      affected: ['language_rendering', 'tags', 'isbn_form'],
      cases: Array.from({ length: 7 }, (_, index) => previewCase(index + 1, [tagChange, isbnSame])),
      rows: [
        ...Array.from({ length: 6 }, (_, index) => `Work ${String(index + 1)}: Policy change`),
        'Language: Not previewed.',
      ],
      title: '7 sampled works with changes',
      media: 'Ebooks: 7 sampled works · 7 changed fields',
    },
    {
      name: 'a rule previewed in one library and not in another',
      affected: ['tags'],
      cases: [previewCase(1, [tagChange])],
      unsampled: 'Nonfiction',
      rows: ['Work 1: Policy change', 'Tags: Not previewed.'],
      title: '1 sampled work with changes',
      media: 'Ebooks: 1 sampled work · 1 changed field',
    },
    {
      name: 'a library override pinning every edited field',
      affected: [],
      cases: [previewCase(1, [isbnSame])],
      rows: [],
      title: 'Impact preview ready',
      media: 'Ebooks: 1 sampled work',
    },
    {
      name: 'no sampled work',
      affected: ['tags'],
      cases: [],
      rows: ['Tags: Not previewed.'],
      title: 'Impact preview ready',
      media: null,
    },
    {
      name: 'no sampled work in a truncated sample',
      affected: ['tags'],
      cases: [],
      truncated: true,
      sample: 'No current unapplied work to sample · Additional works were not previewed',
      rows: ['Tags: Not previewed.'],
      title: 'Impact preview ready',
      media: null,
    },
  ])('never reports $name as unchanged when it was not previewed', async (example) => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [
            {
              library: 'Fiction',
              affected_categories: example.affected,
              cases: example.cases,
              comparisons: example.cases.map((item) => ({
                media_kind: 'ebook',
                fields: comparisons(item.fields),
              })),
              cases_total: example.cases.length,
              cases_truncated: example.truncated ?? false,
              current_unapplied_count: example.cases.length,
              resolved_policy_id: 'resolved-policy',
            },
            ...(example.unsampled
              ? [
                  {
                    library: example.unsampled,
                    affected_categories: ['tags'],
                    cases: [],
                    comparisons: [],
                    cases_total: 0,
                    cases_truncated: false,
                    current_unapplied_count: 0,
                    resolved_policy_id: 'resolved-policy',
                  },
                ]
              : []),
          ],
        }
      return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    renderRoute('/policy?section=curation', http)
    const tags = await screen.findByRole('region', { name: 'Tags' })
    await user.click(within(tags).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await screen.findByText('Impact preview is current. The active policy is unchanged.')

    expect(outcomeRows()).toEqual(example.rows)
    expect(
      [...document.querySelectorAll('.is-unpreviewed > span:nth-child(2) small')].map(
        (node) => node.textContent,
      ),
    ).toEqual(
      example.rows
        .filter((row) => row.endsWith('Not previewed.'))
        .map(() => example.unsampled ?? 'Fiction'),
    )
    const unchangedEverywhere = screen.queryByText("No library's fields change under this draft.")
    if (example.affected.length) expect(unchangedEverywhere).toBeNull()
    else expect(unchangedEverywhere).toBeVisible()
    for (const row of document.querySelectorAll('.is-unpreviewed'))
      expect(row.querySelector('.cm-policy-impact')).toHaveTextContent(
        /^Not previewed\.This rule takes effect on the next preparation\.$/u,
      )
    expect(screen.getByText(example.title)).toBeVisible()
    const material = screen.queryByText(/\d+ changes? in the sample/u)
    if (example.rows.some((row) => row.endsWith('Policy change')))
      expect(material).toHaveTextContent(`${String(example.cases.length)} change`)
    else expect(material).toBeNull()
    const preview = document.querySelector<HTMLElement>('.cm-policy-preview')
    if (!preview) throw new Error('preview not shown')
    if (example.media) expect(within(preview).getByText(example.media)).toBeVisible()
    else expect(within(preview).queryByText(/^(Ebooks|Audiobooks):/u)).toBeNull()
    expect(screen.queryByText('No changed fields')).toBeNull()
    if (example.sample) expect(within(preview).getByText(example.sample)).toBeVisible()
  })

  it('names an edited policy as custom and the matching draft by the active name', async () => {
    const user = userEvent.setup()
    let active = 'Built-in policy'
    let activated = false
    let acknowledged = false
    const policyCommand = vi.fn<(path: string, body?: unknown) => Promise<unknown>>(
      async (path) => {
        if (path === 'preview')
          return {
            active_policy_id: 'policy-active',
            policy_id: 'policy-draft',
            schema_version: 'metadata-policy-preview-v1',
            active_generation: 1,
            libraries: [],
          }
        if (path === 'activate') {
          active = 'Custom policy'
          activated = true
          return {
            policy_id: 'policy-draft',
            schema_version: 'metadata-policy-activation-v1',
            transition: transition({ origin: 'activation', generation: 2, transition_id: 'mine' }),
          }
        }
        if (path === 'transitions/mine/acknowledge') {
          acknowledged = true
          return transition({
            origin: 'activation',
            generation: 2,
            transition_id: 'mine',
            acknowledged,
          })
        }
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      },
    )
    const base = createHttp()
    const http: ReviewHttp = {
      ...base,
      getPolicyState: vi.fn(async () => ({
        ...(await base.getPolicyState()),
        profile: {
          schema_version: 'metadata-policy-profile-v3' as const,
          name: active,
          options: {},
          destinations: activated ? { tags: 'ignore' } : {},
        },
      })),
      getPolicyTransition: vi.fn(
        async () =>
          (activated
            ? transition({
                origin: 'activation',
                generation: 2,
                transition_id: 'mine',
                acknowledged,
              })
            : transition()) as never,
      ),
      policyCommand: <T,>(path: string, body?: unknown) => policyCommand(path, body) as Promise<T>,
    }
    renderRoute('/policy?section=curation', http)
    const tags = await screen.findByRole('region', { name: 'Tags' })
    expect(screen.getByText('Built-in policy', { selector: '.cm-policy-sidebar strong' }))
    await user.click(within(tags).getByRole('button', { name: 'Ignore' }))
    await user.click(within(tags).getByRole('button', { name: 'Ask me' }))
    expect(screen.getByText('Draft matches the active policy')).toBeVisible()
    await user.click(within(tags).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    for (const path of ['validate', 'drafts', 'preview'])
      expect(policyCommand).toHaveBeenCalledWith(
        path,
        expect.objectContaining({ name: 'Custom policy' }),
      )
    expect(
      await screen.findByText('Custom policy', { selector: '.cm-policy-sidebar strong' }),
    ).toBeVisible()
    // While the cutover is shown it alone states reconciliation: the finish
    // message keeps only its title, and the zero total reads with no 0 of 0.
    expect(await screen.findByRole('heading', { name: 'Activation complete' })).toBeVisible()
    expect(document.querySelector('.cm-policy-finish-message')).toHaveTextContent(
      /^Policy activated$/u,
    )
    expect(
      screen.getByText(
        'No current unapplied items needed reconciliation. Applied history is untouched.',
      ),
    ).toBeVisible()
    expect(screen.queryByText(/0 of 0/u)).toBeNull()
    // Dismissing the read transition removes the cutover without bringing the
    // tray's own reconciliation sentence back, and the status stays truthful.
    await user.click(screen.getByRole('button', { name: 'Dismiss update' }))
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Activation complete' })).toBeNull(),
    )
    expect(document.querySelector('.cm-policy-finish-message')).toHaveTextContent(
      /^Policy activated$/u,
    )
    // Focus goes to the success message, not to the body the button left.
    expect(document.querySelector('.cm-policy-finish-message')).toHaveFocus()
    expect(document.querySelector('.policy-status')).toHaveTextContent(
      /^Policy state updated from the authoritative server\.$/u,
    )
    expect(screen.queryByText(/reconcil/u)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Continue editing' }))
    expect(screen.getByText('Draft matches the active policy')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toBeDisabled()
  })

  it.each([
    ['a settings display name', view(), 'Ebooks & Audiobooks: Collected Editions'],
    [
      'the slug when the display name is unset',
      {
        ...view(),
        document: {
          ...view().document,
          libraries: view().document.libraries.map((library) => ({
            ...library,
            display_name: null,
          })),
        },
      },
      'a-very_long-library-name',
    ],
    ['the slug when Settings is unavailable', undefined, 'a-very_long-library-name'],
  ])('names preview libraries by %s', async (_name, settings, shown) => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> =>
      path === 'preview'
        ? {
            active_policy_id: 'policy-active',
            policy_id: 'policy-draft',
            schema_version: 'metadata-policy-preview-v1',
            active_generation: 1,
            libraries: [
              {
                library: 'a-very_long-library-name',
                affected_categories: ['language_rendering', 'tags'],
                cases: [previewCase(1, [tagChange])],
                comparisons: [{ media_kind: 'ebook', fields: comparisons([tagChange]) }],
                cases_total: 1,
                cases_truncated: false,
                current_unapplied_count: 1,
                resolved_policy_id: 'resolved-policy',
              },
            ],
          }
        : { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' },
    )
    const http: ReviewHttp = {
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    }
    if (settings) http.getSettings = vi.fn(async () => settings)
    renderRoute('/policy?section=identity', http)
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    // Policy navigation and editing read no Settings; only a shown preview does.
    expect(http.getSettings).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await screen.findByText('Impact preview is current. The active policy is unchanged.')
    await waitFor(() => expect(http.getSettings).toHaveBeenCalled())

    await waitFor(() => expect(screen.getByText(`Ebook · ${shown} · Tags`)).toBeVisible())
    const unpreviewed = document.querySelector('.is-unpreviewed')
    expect(unpreviewed?.querySelector('small')).toHaveTextContent(shown)
    expect(unpreviewed?.querySelector('.cm-policy-work-mark')).toHaveTextContent(
      shown.slice(0, 2).toUpperCase(),
    )
    expect(screen.queryByRole('alert')).toBeNull()
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
    expect(screen.queryByText('Policy activated.')).toBeNull()
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

    const warning = await screen.findByText(
      'The previous policy update is still processing. This reviewed snapshot remains available; retry after the transition is resolved.',
    )
    expect(warning).toBeVisible()
    expect(warning).toHaveFocus()
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
  const choice = (name: string, value: 'Off' | 'On') =>
    within(within(region).getByRole('group', { name })).getByRole('button', { name: value })
  await within(region).findByRole('group', { name: 'Remove marketing material' })
  for (const name of [
    'Remove marketing material',
    'Remove sales figures and bestseller boasts',
    'Remove pull quotes and author praise',
  ])
    expect(choice(name, 'On')).toHaveAttribute('aria-pressed', 'true')
  expect(choice('Automatically apply ready description advice', 'Off')).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(screen.getByRole('region', { name: 'Cover' }).nextElementSibling).toBe(region)
  expect(region.nextElementSibling).toHaveAccessibleName('ASIN')
  await userEvent.setup().click(choice('Remove marketing material', 'Off'))
  expect(within(region).getByText('Changed')).toBeVisible()
  expect(within(region).getByRole('status')).toHaveTextContent(
    'Unsaved. These save with their own button, not with the policy.',
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

it('reloads unconfirmed description preferences and says they may have been saved', async () => {
  const http = createHttp()
  const initial = view()
  let current = initial
  http.getSettings = vi.fn(async () => current)
  http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => {
    // The save committed; only its response failed.
    current = { ...initial, document: write.document }
    throw new ApiError(503, 'settings_unavailable')
  })
  renderRoute('/policy?section=curation', http)
  const region = await screen.findByRole('region', { name: 'Description advice' })
  const marketing = await within(region).findByRole('group', { name: 'Remove marketing material' })
  await userEvent.setup().click(within(marketing).getByRole('button', { name: 'Off' }))
  const reads = vi.mocked(http.getSettings).mock.calls.length
  await userEvent
    .setup()
    .click(within(region).getByRole('button', { name: 'Save description preferences' }))

  expect(await within(region).findByRole('alert')).toHaveTextContent(
    'Description preferences could not be confirmed. They may have been saved; the saved preferences were reloaded. Check them before saving again.',
  )
  await waitFor(() =>
    expect(within(region).getByRole('status')).toHaveTextContent(
      'Saved with their own button, not with the policy.',
    ),
  )
  expect(within(region).queryByText(/Unsaved\./u)).toBeNull()
  expect(vi.mocked(http.getSettings).mock.calls.length).toBeGreaterThan(reads)
  expect(within(marketing).getByRole('button', { name: 'Off' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(
    within(region).getByRole('button', { name: 'Reload description preferences' }),
  ).toBeVisible()
})

it('holds description edits while unconfirmed preferences reload', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  const initial = view()
  let current = initial
  let releaseReload: (() => void) | null = null
  let held = false
  http.getSettings = vi.fn(async () => {
    if (held) await new Promise<void>((resolve) => (releaseReload = resolve))
    return current
  })
  http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => {
    current = { ...initial, document: write.document }
    held = true
    throw new ApiError(503, 'settings_unavailable')
  })
  renderRoute('/policy?section=curation', http)
  const region = await screen.findByRole('region', { name: 'Description advice' })
  const marketing = await within(region).findByRole('group', { name: 'Remove marketing material' })
  await user.click(within(marketing).getByRole('button', { name: 'Off' }))
  await user.click(within(region).getByRole('button', { name: 'Save description preferences' }))

  await waitFor(() => expect(releaseReload).not.toBeNull())
  const praise = within(region).getByRole('group', { name: 'Remove pull quotes and author praise' })
  for (const button of within(praise).getAllByRole('button')) expect(button).toBeDisabled()
  expect(
    within(region).getByRole('button', { name: 'Save description preferences' }),
  ).toBeDisabled()
  held = false
  await act(async () => releaseReload?.())
  await waitFor(() => expect(within(praise).getByRole('button', { name: 'Off' })).toBeEnabled())
  expect(within(region).getByRole('alert')).toHaveTextContent(
    'Description preferences could not be confirmed.',
  )
})

it('clears the unconfirmed description message once the owner reloads', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  const initial = view()
  let current = initial
  http.getSettings = vi.fn(async () => current)
  http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => {
    current = { ...initial, document: write.document }
    throw new ApiError(503, 'settings_unavailable')
  })
  renderRoute('/policy?section=curation', http)
  const region = await screen.findByRole('region', { name: 'Description advice' })
  const marketing = await within(region).findByRole('group', { name: 'Remove marketing material' })
  await user.click(within(marketing).getByRole('button', { name: 'Off' }))
  await user.click(within(region).getByRole('button', { name: 'Save description preferences' }))
  const reload = await within(region).findByRole('button', {
    name: 'Reload description preferences',
  })
  await waitFor(() => expect(reload).toBeEnabled())

  await user.click(reload)

  await waitFor(() => expect(within(region).queryByRole('alert')).toBeNull())
  expect(within(region).getByRole('status')).toHaveTextContent(
    'Saved with their own button, not with the policy.',
  )
})

it('shows locked description cleanup preferences without enabling edits', async () => {
  const http = createHttp()
  http.getSettings = vi.fn(async () => view('file'))
  renderRoute('/policy?section=curation', http)
  const marketing = await screen.findByRole('group', { name: 'Remove marketing material' })
  for (const button of within(marketing).getAllByRole('button')) expect(button).toBeDisabled()
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
          destination: 'explicit' as const,
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
  expect(
    within(flag).getByText('Off until you choose a review mode · Automatic not supported'),
  ).toBeVisible()
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
  expect(
    await screen.findByText(
      'The policy change could not be confirmed. It may have been saved; the current policy state is shown. Check it before pressing again.',
    ),
  ).toBeVisible()
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

it('reports an unconfirmed automatic pause from refreshed state without resending it', async () => {
  const user = userEvent.setup()
  let paused = false
  const command = vi.fn(async () => {
    // The pause committed; only its response failed.
    paused = true
    throw new ApiError(503, 'persistence')
  })
  const http: ReviewHttp = {
    ...createHttp(),
    getAutomaticSafety: vi.fn(async () => ({
      paused,
      global_suspension: null,
      category_suspensions: {},
    })),
    policyCommand: command,
  }
  renderRoute('/policy', http)
  await screen.findByRole('heading', { name: 'Library policy' })
  const summary = screen.getByText('Safety').closest('summary')
  if (!summary) throw new Error('Safety summary missing')
  await user.click(summary)
  await user.click(screen.getByRole('button', { name: 'Pause automatic decisions' }))

  expect(
    await screen.findByText(
      'The policy change could not be confirmed. It may have been saved; the current policy state is shown. Check it before pressing again.',
    ),
  ).toBeVisible()
  expect(await screen.findByRole('button', { name: 'Resume automatic decisions' })).toBeVisible()
  expect(screen.queryByText(/Nothing changed/u)).toBeNull()
  expect(command).toHaveBeenCalledOnce()
})

it('keeps a refused Automatic confirmation as nothing changed', async () => {
  const user = userEvent.setup()
  const http: ReviewHttp = {
    ...createHttp(),
    getShadowPosture: vi.fn(async (): Promise<ShadowPostureView> => ({
      schema_version: 'shadow-posture-v1',
      posture: 'preview',
      authority: 'none',
      can_leave_preview: false,
    })),
    policyCommand: vi.fn(async () => {
      throw new ApiError(422, 'automatic_unavailable')
    }),
  }
  renderRoute('/policy', http)
  await screen.findByRole('heading', { name: 'Library policy' })
  const summary = screen.getByText('Safety').closest('summary')
  if (!summary) throw new Error('Safety summary missing')
  await user.click(summary)
  await user.click(screen.getByRole('button', { name: 'Turn on Automatic' }))

  expect(await screen.findByText(/Nothing changed. Review the current server state/u)).toBeVisible()
  expect(http.policyCommand).toHaveBeenCalledWith('shadow/confirm-automatic', undefined)
})

it('names a provider-consensus title suspension by its destination (#1286)', async () => {
  const user = userEvent.setup()
  const http: ReviewHttp = {
    ...createHttp(),
    getAutomaticSafety: vi.fn(async () => ({
      paused: true,
      global_suspension: null,
      category_suspensions: { title: 'target_changed', not_a_category: 'other_failure' },
    })),
  }
  renderRoute('/policy', http)
  await screen.findByRole('heading', { name: 'Library policy' })
  const summary = screen.getByText('Safety').closest('summary')
  if (!summary) throw new Error('Safety summary missing')
  await user.click(summary)
  expect(screen.getByRole('button', { name: 'Recover Title suspension' })).toBeVisible()
  expect(
    screen.getByRole('button', { name: 'Recover Another policy field suspension' }),
  ).toBeVisible()
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
    expect(screen.getByLabelText('Review policy activation')).toHaveTextContent(
      `Ebook libraries — ${label}`,
    )
    expect(screen.getByLabelText('Review policy activation')).toHaveTextContent(
      `Audiobook libraries — ${label}`,
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
  expect(await screen.findByText('Policy activated.')).toBeVisible()
  expect(commands).toHaveBeenLastCalledWith('activate', {
    policy_id: 'draft',
    expected_active_policy_id: 'policy-1',
    expected_active_generation: 3,
  })
})

it.each([
  [true, 'Write permission stays on for Ebooks & Audiobooks: Collected Editions.'],
  [false, 'No library has write permission, and activating grants none.'],
])(
  'names the libraries that keep write permission at the Activate step (consented: %s, #1370)',
  async (consented, copy) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.getActivityConsent = vi.fn(async () => ({
      backup_ready: null,
      capabilities: [
        {
          connection: 'home',
          library: 'a-very_long-library-name',
          state: 'allowed' as const,
          destinations: [
            { consented, destination: 'title' as const, prepared: 0, state: 'allowed' as const },
          ],
        },
      ],
      configured_libraries: [{ connection: 'home', library: 'a-very_long-library-name' }],
      lapsed_consents: [],
      waiting: {
        applying: 0,
        awaiting_permission: 0,
        held_for_recovery: 0,
        retrying: 0,
        scheduled: 0,
      },
    }))
    http.policyCommand = async <T,>(path: string) =>
      (path === 'preview'
        ? {
            schema_version: 'metadata-policy-preview-v1',
            policy_id: 'draft',
            active_policy_id: 'policy-1',
            active_generation: 1,
            libraries: [],
          }
        : path === 'activate'
          ? {
              schema_version: 'metadata-policy-activation-v1',
              policy_id: 'draft',
              transition: null,
            }
          : { policy_id: 'draft', schema_version: 'metadata-policy-profile-v3' }) as T
    const { queryClient } = renderRoute('/policy?section=identity', http)
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))

    const step = screen.getByLabelText('Review policy activation')
    await waitFor(() => expect(step).toHaveTextContent(copy))
    expect(step).not.toHaveTextContent('No write permission is granted.')
    // Library's notice reads the carried permission at once, not on its cadence.
    queryClient.setQueryData(['activity', 'library-handoff'], { stale: true })
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))
    expect(await screen.findByText('Policy activated.')).toBeVisible()
    await waitFor(() =>
      expect(queryClient.getQueryState(['activity', 'library-handoff'])?.isInvalidated).toBe(true),
    )
  },
)

it.each([
  ['validate', 'Impact preview is current. The active policy is unchanged.', 2],
  ['drafts', 'Your session was refreshed. Press again.', 1],
])(
  'renews a rotated token on the first %s press and resends only a pure command (#1018)',
  async (refused, copy, sends) => {
    const user = userEvent.setup()
    const http = createHttp()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ error: { code: 'csrf' } }, { status: 403 }))
      .mockResolvedValueOnce(Response.json({ policy_id: 'draft' }))
    const transport = new ReviewApiClient({
      buildIdentity: 'a'.repeat(64),
      csrf: { current: () => 'b'.repeat(32), renew: async () => 'c'.repeat(32) },
      fetch: fetchMock,
    })
    http.policyCommand = async <T,>(path: string, body?: unknown) =>
      (path === refused
        ? transport.policyCommand(path, body)
        : path === 'preview'
          ? {
              schema_version: 'metadata-policy-preview-v1',
              policy_id: 'draft',
              active_policy_id: 'policy-1',
              active_generation: 1,
              libraries: [],
            }
          : { policy_id: 'draft', schema_version: 'metadata-policy-profile-v3' }) as T
    renderRoute('/policy?section=identity', http)
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(await screen.findByText(copy)).toBeVisible()
    expect(fetchMock).toHaveBeenCalledTimes(sends)
  },
)

it('counts a manual availability change without an elected value', async () => {
  const user = userEvent.setup()
  const field = {
    field: 'description_fill',
    option: 'safe_plain_text',
    target_state: 'missing',
    current_value: null,
    desired_value: null,
    qualified_value: null,
    outcome: 'abstain',
    reason: 'policy_ignored',
    narrowed_for_target: false,
  }
  const policyCommand = vi.fn(async (path: string) =>
    path === 'preview'
      ? {
          active_policy_id: 'policy-active',
          policy_id: 'policy-reviewed',
          schema_version: 'metadata-policy-preview-v1',
          active_generation: 1,
          libraries: [
            {
              library: 'Audio',
              affected_categories: ['description_fill'],
              comparisons: [
                {
                  media_kind: 'audiobook',
                  fields: [
                    {
                      active: { ...field, reason: 'manual_review_required' },
                      draft: field,
                      active_destination: 'automatic',
                      draft_destination: 'ignore',
                      changed: true,
                    },
                  ],
                },
              ],
              cases_total: 1,
              cases_truncated: false,
              current_unapplied_count: 1,
              resolved_policy_id: 'resolved-policy',
              cases: [
                {
                  fields: [field],
                  proposal_set_id: 'proposal-work',
                  report_id: 'report-work',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'Synthetic Journey',
                },
              ],
            },
          ],
        }
      : { policy_id: 'policy-reviewed', schema_version: 'metadata-policy-profile-v3' },
  )
  const http: ReviewHttp = {
    ...createHttp(),
    policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
  }
  renderRoute('/policy?section=curation', http)
  const description = await screen.findByRole('region', { name: 'Description' })
  await user.click(within(description).getByRole('button', { name: 'Ignore' }))
  await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
  expect(
    await screen.findByText('Policy change', { selector: '.cm-policy-impact > b' }),
  ).toBeVisible()
  expect(screen.getByText('Audiobooks: 1 sampled work · 1 changed field')).toBeVisible()
  expect(screen.queryByText('No policy-caused changes in this sample')).toBeNull()
})

describe('Policy keyboard focus (#1288)', () => {
  const emptyPreview = {
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

  // The page focuses its chapter one frame after it mounts. An interaction that
  // starts inside that frame loses its focus to the chapter, so each test lets
  // the page settle first (#1552).
  const renderIdentity = async (http = createHttp()) => {
    renderRoute('/policy?section=identity', http)
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await waitFor(() => expect(document.getElementById('policy-identity')).toHaveFocus())
    return title
  }

  it('closes a representation menu onto its trigger when Tab leaves it', async () => {
    const user = userEvent.setup()
    const title = await renderIdentity()
    const trigger = within(title).getByRole('combobox', {
      name: 'Title and subtitle representation',
    })
    for (const tab of ['{Tab}', '{Shift>}{Tab}{/Shift}']) {
      await user.click(trigger)
      expect(await screen.findByRole('listbox')).toBeVisible()
      expect(document.activeElement).toHaveAttribute('role', 'option')
      await user.keyboard(tab)
      expect(screen.queryByRole('listbox')).toBeNull()
      expect(trigger).toHaveFocus()
    }
  })

  it('keeps focus on Validate and preview while it runs, then moves it to the preview', async () => {
    const user = userEvent.setup()
    let release: () => void = () => undefined
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'validate') await new Promise<void>((resolve) => (release = resolve))
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview') return emptyPreview
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const title = await renderIdentity({
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    const validate = screen.getByRole('button', { name: 'Validate and preview' })
    validate.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(validate).toHaveTextContent('Validating…'))
    // The same button, still focused and still in the page, refuses a second press.
    expect(validate).toHaveFocus()
    expect(validate).toBeEnabled()
    expect(validate).toHaveAttribute('aria-disabled', 'true')
    await user.keyboard('{Enter}')
    expect(policyCommand).toHaveBeenCalledTimes(1)
    release()
    expect(
      await screen.findByText('Impact preview is current. The active policy is unchanged.'),
    ).toBeVisible()
    await waitFor(() => expect(document.querySelector('.cm-policy-result')).toHaveFocus())
  })

  it('says a refused validation where focus lands', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (): Promise<unknown> => {
      throw new ApiError(422, 'policy_profile_invalid')
    })
    const title = await renderIdentity({
      ...createHttp(),
      policyCommand: <T,>() => policyCommand() as Promise<T>,
    })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    const validate = screen.getByRole('button', { name: 'Validate and preview' })
    validate.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(document.querySelector('.policy-status')).toHaveFocus())
    expect(validate).toHaveTextContent('Validate and preview')
  })

  it('keeps focus through a reconciliation retry, then moves it to the transition notice', async () => {
    const user = userEvent.setup()
    let release: () => void = () => undefined
    let retried = false
    const transition = {
      schema_version: 'metadata-policy-transition-v1' as const,
      transition_id: 'policy_transition_2',
      from_policy_id: 'policy-a',
      to_policy_id: 'policy-b',
      generation: 2,
      state: 'completed_with_failures',
      completed: 9,
      total: 11,
      failed: 2,
      acknowledged: false,
      origin: 'activation',
    }
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      await new Promise<void>((resolve) => (release = resolve))
      retried = true
      return { ...transition, state: 'processing', failed: 0, path }
    })
    renderRoute('/policy', {
      ...createHttp(),
      getPolicyTransition: vi.fn(
        async () =>
          (retried ? { ...transition, state: 'processing', failed: 0 } : transition) as never,
      ),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    })
    const retry = await screen.findByRole('button', { name: 'Retry failed reconciliation' })
    retry.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(retry).toHaveAttribute('aria-disabled', 'true'))
    expect(retry).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(policyCommand).toHaveBeenCalledTimes(1)
    release()
    const notice = await screen.findByRole('region', { name: 'Policy activation progress' })
    await waitFor(() => expect(notice).toHaveFocus())
    expect(
      await within(notice).findByRole('heading', { name: 'Applying updated policy' }),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry failed reconciliation' })).toBeNull()
  })

  it('moves focus to the finish message when a retry reconciles the notice away', async () => {
    const user = userEvent.setup()
    let retried = false
    const transition = {
      schema_version: 'metadata-policy-transition-v1' as const,
      transition_id: 'policy_transition_2',
      from_policy_id: 'policy-a',
      to_policy_id: 'policy-b',
      generation: 2,
      state: 'completed_with_failures',
      completed: 9,
      total: 11,
      failed: 2,
      acknowledged: false,
      origin: 'activation',
    }
    const reconciled = { ...transition, state: 'completed', completed: 11, failed: 0 }
    const policyCommand = vi.fn(async (): Promise<unknown> => {
      retried = true
      return reconciled
    })
    renderRoute('/policy', {
      ...createHttp(),
      getPolicyTransition: vi.fn(async () => (retried ? reconciled : transition) as never),
      policyCommand: <T,>() => policyCommand() as Promise<T>,
    })
    const retry = await screen.findByRole('button', { name: 'Retry failed reconciliation' })
    retry.focus()
    await user.keyboard('{Enter}')
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Policy activation progress' })).toBeNull(),
    )
    await waitFor(() => expect(document.querySelector('.cm-policy-finish-message')).toHaveFocus())
    // The emptied status region can no longer take focus at all.
    expect(document.querySelector('section:not([class]):not([aria-label])')).not.toHaveAttribute(
      'tabindex',
    )
  })

  it('moves focus to the finish message when a retried transition finishes later', async () => {
    const user = userEvent.setup()
    let reads = 0
    let retried = false
    const transition = {
      schema_version: 'metadata-policy-transition-v1' as const,
      transition_id: 'policy_transition_2',
      from_policy_id: 'policy-a',
      to_policy_id: 'policy-b',
      generation: 2,
      state: 'completed_with_failures',
      completed: 9,
      total: 11,
      failed: 2,
      acknowledged: false,
      origin: 'activation',
    }
    const processing = { ...transition, state: 'processing', failed: 0 }
    const reconciled = { ...transition, state: 'completed', completed: 11, failed: 0 }
    const policyCommand = vi.fn(async (): Promise<unknown> => {
      retried = true
      return processing
    })
    renderRoute('/policy', {
      ...createHttp(),
      // The first read after the retry is still processing; the poll after it
      // finds the transition finished, which no longer shows a notice.
      getPolicyTransition: vi.fn(
        async () => (!retried ? transition : reads++ === 0 ? processing : reconciled) as never,
      ),
      policyCommand: <T,>() => policyCommand() as Promise<T>,
    })
    const retry = await screen.findByRole('button', { name: 'Retry failed reconciliation' })
    retry.focus()
    await user.keyboard('{Enter}')
    const notice = await screen.findByRole('region', { name: 'Policy activation progress' })
    await waitFor(() => expect(notice).toHaveFocus())
    await waitFor(
      () => expect(screen.queryByRole('region', { name: 'Policy activation progress' })).toBeNull(),
      { timeout: 4_000 },
    )
    await waitFor(() => expect(document.querySelector('.cm-policy-finish-message')).toHaveFocus())
  })

  it('describes the activation review by its write-permission line', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v3' }
      if (path === 'preview') return emptyPreview
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v3' }
    })
    const title = await renderIdentity({
      ...createHttp(),
      policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T>,
    })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    const review = await screen.findByRole('group', { name: 'Review policy activation' })
    await waitFor(() => expect(review).toHaveFocus())
    expect(review).toHaveAccessibleDescription(/write permission/iu)
  })
})
