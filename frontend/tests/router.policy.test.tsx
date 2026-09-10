import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError, BuildMismatchError } from '../src/api/http'
import type { PolicyStateView, ReviewHttp } from '../src/api/http'
import { normalizePolicySearch } from '../src/router'
import { createHttp, renderRoute } from './fixtures'
import { view } from './settingsFixtures'

describe('Policy route', () => {
  it('explains empty-series filling, all three representations and provider numbering limits', async () => {
    renderRoute('/policy?section=publication')
    const series = await screen.findByRole('region', { name: 'Series memberships' })
    expect(
      within(series).getByText(
        'Automatic fills an empty Series field only and preserves existing memberships.',
      ),
    ).toBeVisible()
    await userEvent.setup().click(within(series).getByText('What this changes'))
    expect(within(series).getByText(/One provider can supply enough backing/u)).toBeVisible()
    expect(within(series).getByText(/not independently verified for each book/u)).toBeVisible()
    expect(within(series).getByText('Primary narrative:')).toBeVisible()
    expect(within(series).getByText('First source membership:')).toBeVisible()
    expect(within(series).getByText('All qualified memberships:')).toBeVisible()
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
    expect(await screen.findByText(/^Active baseline ·/u)).toBeVisible()
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
    expect(screen.getByText(/^Active policy ·/u)).toBeVisible()
  })

  it('does not carry a clean completion into an unrelated session', async () => {
    const settled = transition({ generation: 2, origin: 'activation', completed: 11, total: 11 })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyTransition: vi.fn(async () => settled as never),
    }
    renderRoute('/policy', http)

    expect(await screen.findByText(/^Active policy ·/u)).toBeVisible()
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
    expect(screen.getByText(/^Active policy · draft matches active$/u)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Validate and preview' })).toBeDisabled()
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
    const policyCommand = vi.fn(async (path: string, body?: unknown): Promise<unknown> => {
      void body
      if (path === 'validate' && ++validationCalls === 1) throw new ApiError(422, 'invalid_policy')
      if (path === 'drafts' && ++draftSaves > 1) throw new AmbiguousMutationError()
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') {
        activePolicyId = 'policy-preview-authority'
        return {
          active_policy_id: activePolicyId,
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          libraries: [
            {
              library: 'Hostile <script> library',
              affected_categories: ['title_layout', 'tags'],
              cases: [],
              cases_total: 0,
              cases_truncated: false,
              current_unapplied_count: 3,
              resolved_policy_id: 'resolved-policy',
            },
          ],
        }
      }
      if (path === 'activate') throw new ApiError(409, 'policy_activation_is_stale')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({
        schema_version: 'metadata-policy-notebook-v1',
        policy_id: activePolicyId,
        profile: {
          schema_version: 'metadata-policy-profile-v2',
          name: 'Qualified default',
          options,
          destinations,
        },
        rich_qualification: [
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'subtitle',
            option: 'fill_empty',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'not_qualified',
            field: 'subtitle',
            option: 'corroborated_split',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'series',
            option: 'primary_narrative',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'series',
            option: 'source_order',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'series',
            option: 'all_qualified',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'genres',
            option: 'fill_empty',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'policy_only',
            field: 'tags',
            option: 'off',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'isbn_form',
            option: 'preserve_exact',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'isbn_form',
            option: 'prefer_isbn13',
          },
          {
            automatic_available: true,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'passing_automatic',
            field: 'description_fill',
            option: 'safe_plain_text',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'not_qualified',
            field: 'description_fill',
            option: 'google_visible_text',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'insufficient_coverage',
            field: 'asin',
            option: 'preserve_exact',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
            disposition: 'insufficient_coverage',
            field: 'asin',
            option: 'ebook_manifestation',
          },
          {
            automatic_available: false,
            candidate_sha256: 'a'.repeat(64),
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
        name: 'Unsupported Fields Mechanical Librarian can detect but cannot change yet.',
      }),
    ).toHaveAccessibleDescription('0 changed fields')
    expect(screen.getByText('Changed fields')).toBeVisible()
    expect(screen.getByRole('button', { name: /^Unsupported/ })).toHaveTextContent(
      'Fields Mechanical Librarian can detect but cannot change yet.',
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
    expect(helpLines).toHaveLength(2)
    expect(helpLines[0]).toHaveTextContent(
      'Safe plain text: Fills an empty Description only; abstains when target-work applicability is unproved, the quality floor has no survivor, or the result remains ambiguous, missing, transformed, or unsupported.',
    )
    expect(helpLines[1]).toHaveTextContent(
      'Visible Google text: Fills an empty Description only; abstains without it and never falls back to Hardcover.',
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
    await user.click(screen.getByRole('button', { name: /Unsupported/ }))
    const explicit = screen.getByRole('region', { name: 'Explicit-content flag' })
    expect(within(explicit).getByText('Unsupported destination')).toBeVisible()
    expect(
      within(explicit).getByText(/shown for context only.*cannot be changed in this draft/i),
    ).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Title and subtitle' })).toBeNull()
    expect(screen.getAllByText(/draft has unreviewed changes/u).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('The draft is not valid. Review its field choices and try again.'),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(
      await screen.findByText('Impact preview is current. The active policy is unchanged.'),
    ).toBeVisible()
    expect(await screen.findByText('Hostile <script> library')).toBeVisible()
    expect(screen.getByText(/Title and subtitle, Tags/)).toBeVisible()
    expect(screen.getByText('Needs review', { selector: '.cm-policy-impact > b' })).toBeVisible()
    expect(
      screen.getByText('· No sampled field outcome; 3 current unapplied works remain to inspect'),
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
    })
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
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          rich_qualification: [],
          libraries: [
            {
              library: 'Fiction',
              affected_categories: ['title_layout'],
              cases_total: 2,
              cases_truncated: true,
              current_unapplied_count: 7,
              resolved_policy_id: 'resolved-policy',
              cases: [
                {
                  fields: [
                    field('Old one', 'New one'),
                    ...Array.from({ length: 7 }, (_, index) =>
                      field(
                        `Old extra ${String(index)}`,
                        `New extra ${String(index)}`,
                        `extra_${String(index)}`,
                      ),
                    ),
                  ],
                  proposal_set_id: 'proposal-1',
                  report_id: 'report-1',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'First book',
                },
                {
                  fields: [
                    field('Old two', 'New two'),
                    ...Array.from({ length: 7 }, (_, index) =>
                      field(
                        `Second old extra ${String(index)}`,
                        `Second new extra ${String(index)}`,
                        `second_extra_${String(index)}`,
                      ),
                    ),
                  ],
                  proposal_set_id: 'proposal-2',
                  report_id: 'report-2',
                  resolved_policy_id: 'resolved-policy',
                  work_label: 'Second book',
                },
              ],
            },
          ],
        }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
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
    expect(screen.getByText('Second book')).toBeVisible()
    expect(screen.getAllByText('Fiction · Title and subtitle')).toHaveLength(2)
    expect(screen.getAllByText('Change')).toHaveLength(6)
    expect(screen.getByText('· Old one → New one')).toBeVisible()
    expect(screen.getByText('· Old two → New two')).toBeVisible()
    expect(screen.getByText('2 sampled works with changes')).toBeVisible()
    expect(screen.getByText(/16 material changes in the bounded sample/)).toBeVisible()
    expect(screen.getByLabelText('2 sampled works with changes')).toHaveTextContent('2')
  })

  it('tracks the protected whole-draft cadence and invalidates preview after an edit', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts')
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          libraries: [
            {
              library: 'Empty library',
              affected_categories: [],
              cases: [],
              cases_total: 0,
              cases_truncated: false,
              current_unapplied_count: 0,
              resolved_policy_id: 'resolved-policy',
            },
          ],
        }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
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
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          libraries: [],
        }
      if (path === 'activate')
        return {
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-activation-v1',
          transition: null,
        }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
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
          return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
        if (path === 'preview')
          return {
            active_policy_id: 'policy-active',
            policy_id: 'policy-draft',
            schema_version: 'metadata-policy-preview-v1',
            libraries: [],
          }
        if (path === 'activate') throw activationError
        return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
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
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          libraries: [],
        }
      if (path === 'activate') return {}
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
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
        return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview')
        return {
          active_policy_id: 'policy-active',
          policy_id: 'policy-draft',
          schema_version: 'metadata-policy-preview-v1',
          libraries: [],
        }
      if (path === 'activate') throw new ApiError(409, 'policy_transition_is_processing')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
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
