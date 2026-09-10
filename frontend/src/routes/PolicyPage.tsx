import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  derivePolicyDraftDisplay,
} from '../api/http'
import type {
  PolicyActivationView,
  PolicyDraft,
  PolicyFieldDefault,
  PolicyPreviewView,
  PolicySection,
  PolicyTransitionView,
  ShadowPostureView,
} from '../api/http'
import { DescriptionAdviceSettings } from '../components/DescriptionAdviceSettings'
import { Dropdown } from '../components/Dropdown'
import { fieldLabel } from '../labels'
import { policyRoute, useReviewContext } from '../router'
import type { PolicyCritical } from '../router'

interface FieldDefinition {
  key: string
  label: string
  meaning: string
  options: readonly string[]
  optionLabels: Readonly<Record<string, string>>
  chapter: PolicySection
  automatic: boolean
  unsupported?: boolean
}

interface RepresentationGuidance {
  admitted: string
  transformation: string
  effect: string
  fallback: string
}

type PolicyStage = 'editing' | 'validating' | 'preview' | 'activation' | 'activated'

const fields: readonly FieldDefinition[] = [
  {
    key: 'title_layout',
    label: 'Title and subtitle',
    meaning: 'Keep a title and subtitle together as one coherent identity.',
    options: ['preserve', 'title_and_subtitle'],
    optionLabels: {
      preserve: 'Preserve current layout',
      title_and_subtitle: 'Separate title and subtitle',
    },
    chapter: 'identity',
    automatic: true,
  },
  {
    key: 'contributor_names',
    label: 'Authors and narrators',
    meaning: 'Preserve contributor order while choosing how supported names appear.',
    options: ['preserve', 'canonical'],
    optionLabels: { preserve: 'Preserve supported form', canonical: 'Use canonical names' },
    chapter: 'identity',
    automatic: true,
  },
  {
    key: 'language_rendering',
    label: 'Language',
    meaning: 'Keep canonical language identity while choosing its readable form.',
    options: ['preserve', 'display_name'],
    optionLabels: {
      preserve: 'Preserve supported form',
      display_name: 'Use language display name',
    },
    chapter: 'identity',
    automatic: true,
  },
  {
    key: 'subtitle',
    label: 'Subtitle',
    meaning: 'Fill an empty subtitle or use a corroborated title and subtitle pair.',
    options: ['fill_empty', 'corroborated_split'],
    optionLabels: {
      fill_empty: 'Fill only when empty',
      corroborated_split: 'Use corroborated split',
    },
    chapter: 'identity',
    automatic: false,
  },
  {
    key: 'date_precision',
    label: 'Publication date and year',
    meaning: 'The date and matching year move together as one edition fact.',
    options: ['year', 'full_date'],
    optionLabels: { year: 'Year only', full_date: 'Full date with matching year' },
    chapter: 'publication',
    automatic: true,
  },
  {
    key: 'publisher_preference',
    label: 'Publisher',
    meaning: 'Choose the supported publisher level for the same manifestation.',
    options: ['publisher', 'imprint'],
    optionLabels: { publisher: 'Prefer publisher', imprint: 'Prefer imprint' },
    chapter: 'publication',
    automatic: true,
  },
  {
    key: 'isbn_form',
    label: 'ISBN',
    meaning: 'Preserve exact ISBN data or choose an admitted ISBN-13 representation.',
    options: ['preserve_exact', 'prefer_isbn13'],
    optionLabels: { preserve_exact: 'Preserve exact ISBN', prefer_isbn13: 'Prefer ISBN-13' },
    chapter: 'publication',
    automatic: false,
  },
  {
    key: 'series',
    label: 'Series memberships',
    meaning: 'Automatic fills an empty Series field only and preserves existing memberships.',
    options: ['primary_narrative', 'source_order', 'all_qualified'],
    optionLabels: {
      primary_narrative: 'Primary narrative',
      source_order: 'First source membership',
      all_qualified: 'All qualified memberships',
    },
    chapter: 'publication',
    automatic: false,
  },
  {
    key: 'genres',
    label: 'Genres',
    meaning: 'Use one exact eligible source list.',
    options: ['fill_empty', 'add', 'replace'],
    optionLabels: {
      fill_empty: 'Fill only when empty',
      add: 'Add missing values',
      replace: 'Replace with source list',
    },
    chapter: 'curation',
    automatic: false,
  },
  {
    key: 'tags',
    label: 'Tags',
    meaning: 'Tags are off by default and remain manual review choices.',
    options: ['off', 'fill_empty', 'add', 'replace'],
    optionLabels: {
      off: 'Off',
      fill_empty: 'Fill only when empty',
      add: 'Add missing values',
      replace: 'Replace with source list',
    },
    chapter: 'curation',
    automatic: false,
  },
  {
    key: 'description_fill',
    label: 'Description',
    meaning:
      'Use one applicable plain-text description without combining provider text; compare competing values by quality.',
    options: ['safe_plain_text', 'google_visible_text'],
    optionLabels: {
      safe_plain_text: 'Safe plain text',
      google_visible_text: 'Visible Google text',
    },
    chapter: 'curation',
    automatic: false,
  },
  {
    key: 'cover_order',
    label: 'Cover',
    meaning: 'Choose the best identity-confirmed cover by one deterministic order.',
    options: ['fixed_source_order'],
    optionLabels: { fixed_source_order: 'Deterministic confirmed order' },
    chapter: 'curation',
    automatic: true,
  },
  {
    key: 'asin',
    label: 'ASIN',
    meaning: 'Preserve exact ASIN data or select an admitted ebook manifestation.',
    options: ['preserve_exact', 'ebook_manifestation'],
    optionLabels: {
      preserve_exact: 'Preserve exact ASIN',
      ebook_manifestation: 'Ebook manifestation',
    },
    chapter: 'curation',
    automatic: false,
  },
  {
    key: 'explicit',
    label: 'Explicit-content flag',
    meaning: 'This destination is visible but is not currently supported.',
    options: ['unsupported'],
    optionLabels: { unsupported: 'Not currently supported' },
    chapter: 'unsupported',
    automatic: false,
    unsupported: true,
  },
]

// This static closed catalogue is imported by the component proof.
// eslint-disable-next-line react-refresh/only-export-components
export const policyRepresentationGuidance: Readonly<Record<string, RepresentationGuidance>> = {
  'title_layout:preserve': {
    admitted: 'the current candidate value and its policy/audit context',
    transformation: 'does not transform the produced candidate',
    effect: 'Automatic records the exact selected Title value; Subtitle is supporting context',
    fallback: 'abstains when no decision is qualified',
  },
  'title_layout:title_and_subtitle': {
    admitted: 'the current candidate value and its policy/audit context',
    transformation: 'does not split the produced candidate',
    effect: 'Automatic records the exact selected Title value; Subtitle is supporting context',
    fallback: 'abstains when no decision is qualified',
  },
  'contributor_names:preserve': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without transforming the candidate',
    effect: 'Automatic writes only that exact selected contributor destination value',
    fallback: 'abstains when no decision is qualified',
  },
  'contributor_names:canonical': {
    admitted:
      'the already-produced exact candidate from provider-consensus display evidence and its retained local provenance',
    transformation:
      'replaces only an equivalent local contributor display form without reconciling contributor identity',
    effect: 'Automatic writes only that exact selected contributor destination value',
    fallback: 'abstains when no decision is qualified',
  },
  'language_rendering:preserve': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without rendering the candidate',
    effect: 'Automatic writes only that exact selected Language value',
    fallback: 'abstains when no decision is qualified',
  },
  'language_rendering:display_name': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without rendering the candidate',
    effect: 'Automatic writes only that exact selected Language value',
    fallback: 'abstains when no decision is qualified',
  },
  'subtitle:fill_empty': {
    admitted: "one supported subtitle attested by the book's own metadata",
    transformation: 'uses the exact admitted text',
    effect: 'fills an empty Subtitle only',
    fallback:
      "abstains when Subtitle already has a value or the book's own metadata does not attest the candidate",
  },
  'subtitle:corroborated_split': {
    admitted: 'one corroborated title and subtitle pair',
    transformation: 'splits only an admitted exact joined title',
    effect: 'sets Title and Subtitle together',
    fallback: 'abstains when the pair or exact join is absent',
  },
  'date_precision:year': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without reducing the candidate',
    effect:
      'Automatic writes only that exact selected Publication date or Publication year destination value for each qualified decision',
    fallback: 'abstains when no decision is qualified',
  },
  'date_precision:full_date': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without changing candidate precision',
    effect:
      'Automatic writes only that exact selected Publication date or Publication year destination value for each qualified decision',
    fallback: 'abstains when no decision is qualified',
  },
  'publisher_preference:publisher': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without selecting a publisher value',
    effect: 'Automatic writes only that exact selected Publisher value',
    fallback: 'abstains when no decision is qualified',
  },
  'publisher_preference:imprint': {
    admitted: 'the already-produced exact Publisher preview candidate and its policy/audit context',
    transformation: 'records the option without selecting an imprint value',
    effect:
      'Policy preview shows that exact selected Publisher candidate; no Automatic decision is qualified',
    fallback: 'abstains when no preview candidate is available',
  },
  'isbn_form:preserve_exact': {
    admitted: 'an already admitted exact ISBN',
    transformation: 'keeps its observed form',
    effect: 'sets ISBN',
    fallback: 'abstains when no exact ISBN is admitted',
  },
  'isbn_form:prefer_isbn13': {
    admitted: 'an already admitted ISBN-13 representation',
    transformation: 'selects that exact existing form without conversion',
    effect: 'sets ISBN',
    fallback: 'abstains when no admitted ISBN-13 is available',
  },
  'series:primary_narrative': {
    admitted: 'provider-backed memberships for this work',
    transformation: 'selects the primary narrative membership',
    effect: 'Automatic fills an empty Series field only',
    fallback: 'abstains when no membership is qualified',
  },
  'series:source_order': {
    admitted: 'provider-backed memberships for this work',
    transformation: 'selects the first admitted source membership',
    effect: 'Automatic fills an empty Series field only',
    fallback: 'abstains when no membership is qualified',
  },
  'series:all_qualified': {
    admitted: 'all provider-backed qualified memberships for this work',
    transformation: 'keeps them visible in admitted order',
    effect:
      'keeps the full context while Automatic fills an empty Series field with only its first membership',
    fallback: 'abstains when no membership is qualified',
  },
  'genres:fill_empty': {
    admitted: 'one exact eligible source list',
    transformation: 'keeps the list unchanged',
    effect: 'fills empty Genres only',
    fallback: 'abstains when Genres is populated or no list is eligible',
  },
  'genres:add': {
    admitted: 'one exact eligible source list',
    transformation: 'adds only values absent from Genres',
    effect: 'extends Genres',
    fallback: 'abstains when no list is eligible',
  },
  'genres:replace': {
    admitted: 'one exact eligible source list',
    transformation: 'uses that list without combining providers',
    effect: 'replaces Genres',
    fallback: 'abstains when no list is eligible',
  },
  'tags:off': {
    admitted: 'no source value',
    transformation: 'does not transform metadata',
    effect: 'leaves Tags unchanged',
    fallback: 'always abstains from tag changes',
  },
  'tags:fill_empty': {
    admitted: 'one exact eligible source list',
    transformation: 'keeps the list unchanged',
    effect: 'fills empty Tags only',
    fallback: 'abstains when Tags is populated or no list is eligible',
  },
  'tags:add': {
    admitted: 'one exact eligible source list',
    transformation: 'adds only values absent from Tags',
    effect: 'extends Tags',
    fallback: 'abstains when no list is eligible',
  },
  'tags:replace': {
    admitted: 'one exact eligible source list',
    transformation: 'uses that list without combining providers',
    effect: 'replaces Tags',
    fallback: 'abstains when no list is eligible',
  },
  'description_fill:safe_plain_text': {
    admitted:
      'one applicable bounded plain-text description; competing descriptions must also pass the fixed quality floor',
    transformation:
      'does not transform HTML and uses the fixed Hardcover, Google Books, then Open Library authority order',
    effect: 'fills an empty Description only',
    fallback:
      'abstains when target-work applicability is unproved, the quality floor has no survivor, or the result remains ambiguous, missing, transformed, or unsupported',
  },
  'description_fill:google_visible_text': {
    admitted: 'eligible Google Books visible text only',
    transformation: 'turns bounded Google HTML into inert visible text',
    effect: 'fills an empty Description only',
    fallback: 'abstains without it and never falls back to Hardcover',
  },
  'cover_order:fixed_source_order': {
    admitted: 'identity-confirmed non-current covers after provider fetching is quiescent',
    transformation: 'uses the frozen identity, aspect, size and packet tie-break order',
    effect: 'selects the rank-one confirmed Cover candidate',
    fallback: 'abstains while fetching or when no non-current match is confirmed',
  },
  'asin:preserve_exact': {
    admitted: 'an already admitted exact ASIN',
    transformation: 'keeps its observed form',
    effect: 'sets ASIN',
    fallback: 'abstains when no exact ASIN is admitted',
  },
  'asin:ebook_manifestation': {
    admitted: 'an admitted ebook-manifestation ASIN',
    transformation: 'selects that exact manifestation value',
    effect: 'sets ASIN',
    fallback: 'abstains when no ebook manifestation is admitted',
  },
}

// The one resolution of "what this field says when the document is silent".
// The dirty comparison and the chapter badges both read it, so they cannot
// disagree about whether a draft matches the active policy.
const fieldDefaults: Readonly<Record<string, PolicyFieldDefault>> = Object.fromEntries(
  fields.map((field) => [
    field.key,
    { destination: field.unsupported ? 'unsupported' : 'ask', option: field.options[0] ?? '' },
  ]),
)

const chapters: readonly { key: PolicySection; label: string; description: string }[] = [
  {
    key: 'identity',
    label: 'Identity',
    description: 'How a work names itself and its contributors.',
  },
  {
    key: 'publication',
    label: 'Publication',
    description: 'How edition facts and memberships are represented.',
  },
  {
    key: 'curation',
    label: 'Curation',
    description: 'Fields that remain intentionally owner-directed.',
  },
  {
    key: 'unsupported',
    label: 'Unsupported',
    description: 'Fields Mechanical Librarian can detect but cannot change yet.',
  },
]

const readableCategory = (key: string) =>
  fields.find((field) => field.key === key)?.label ?? fieldLabel(key)
const previewValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') return 'empty'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

interface PolicyPreviewField {
  readonly current_value: unknown
  readonly desired_value: unknown
  readonly field: string
  readonly outcome: string
  readonly reason: string | null
  readonly target_state: string
}

export interface PolicyImpactDisplay {
  readonly changed: boolean
  readonly detail: string
  readonly technicalDetail: string
  readonly title: 'Change' | 'Needs review' | 'No change' | 'No supported value found'
}

export interface PolicyImpactRow {
  readonly caseId: string
  readonly impact: PolicyImpactDisplay
}

const sameMetadataValue = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right)
const hasMetadataValue = (value: unknown) => value !== null && value !== undefined && value !== ''
const humanReason = (reason: string | null) => {
  if (reason === 'ambiguous_evidence') return 'evidence is ambiguous'
  if (reason === 'policy_ignored' || reason === 'policy_off')
    return 'this policy leaves the field unchanged'
  if (reason === 'explicit_unsupported') return 'this destination is not currently supported'
  if (reason === 'verified_manifestation_isbn')
    return 'deterministic identity evidence binds it to this file’s work'
  if (reason === 'target_work_contradicted')
    return 'the retained provider evidence names a different work'
  if (reason === 'target_preserved' || reason === 'unchanged')
    return 'the exact current value is preserved'
  return 'no supported value found'
}

// The server keeps its closed outcome and reason codes for audit detail. This
// projection deliberately turns them into owner-facing impact language.
// eslint-disable-next-line react-refresh/only-export-components
export const policyImpactDisplay = (field: PolicyPreviewField): PolicyImpactDisplay => {
  const current = previewValue(field.current_value)
  const currentIsDisplayable = hasMetadataValue(field.current_value)
  const desiredIsDisplayable = hasMetadataValue(field.desired_value)
  const materialChange =
    field.outcome === 'projected' &&
    !sameMetadataValue(field.current_value, field.desired_value) &&
    (currentIsDisplayable || desiredIsDisplayable)
  const technicalDetail = `outcome: ${field.outcome}; reason: ${field.reason ?? 'none'}; target state: ${field.target_state}`
  if (materialChange) {
    const detail =
      !currentIsDisplayable && desiredIsDisplayable
        ? `fills the empty field with ${previewValue(field.desired_value)}`
        : currentIsDisplayable && !desiredIsDisplayable
          ? `clears ${current}`
          : `${current} → ${previewValue(field.desired_value)}`
    return {
      changed: true,
      title: 'Change',
      detail:
        field.reason === 'verified_manifestation_isbn'
          ? `${detail} because ${humanReason(field.reason)}`
          : detail,
      technicalDetail,
    }
  }
  if (field.reason === 'target_state_unusable')
    return {
      changed: false,
      title: 'Needs review',
      detail: 'Current value is unavailable or unreadable — inspect the source before deciding',
      technicalDetail,
    }
  if (field.reason === 'ambiguous_evidence')
    return {
      changed: false,
      title: 'Needs review',
      detail: `${current} remains — ${humanReason(field.reason)}`,
      technicalDetail,
    }
  if (field.reason === 'policy_ignored' || field.reason === 'policy_off')
    return {
      changed: false,
      title: 'No change',
      detail: `remains ${current} — ${humanReason(field.reason)}`,
      technicalDetail,
    }
  if (field.reason === 'explicit_unsupported')
    return {
      changed: false,
      title: 'No supported value found',
      detail: `${current} remains — ${humanReason(field.reason)}`,
      technicalDetail,
    }
  if (field.reason === 'target_work_contradicted')
    return {
      changed: false,
      title: 'No supported value found',
      detail: `${currentIsDisplayable ? `${current} remains` : 'remains empty'} — ${humanReason(field.reason)}`,
      technicalDetail,
    }
  if (field.outcome === 'no_op' || sameMetadataValue(field.current_value, field.desired_value))
    return {
      changed: false,
      title: 'No change',
      detail: `remains ${current} — ${field.reason ? humanReason(field.reason) : 'the exact current value is preserved'}`,
      technicalDetail,
    }
  if (!currentIsDisplayable && !desiredIsDisplayable)
    return {
      changed: false,
      title: 'No change',
      detail: 'remains empty — no supported value found',
      technicalDetail,
    }
  return {
    changed: false,
    title: 'No supported value found',
    detail: `${current} remains — ${humanReason(field.reason)}`,
    technicalDetail,
  }
}
// eslint-disable-next-line react-refresh/only-export-components
export const policyImpactTotals = (rows: readonly PolicyImpactRow[]) => {
  const changes = rows.filter((row) => row.impact.changed)
  return {
    changedWorks: new Set(changes.map((row) => row.caseId)).size,
    materialChanges: changes.length,
  }
}
const isPolicyPreview = (value: unknown): value is PolicyPreviewView =>
  typeof value === 'object' &&
  value !== null &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-preview-v1' &&
  'active_policy_id' in value &&
  typeof value.active_policy_id === 'string' &&
  'libraries' in value &&
  Array.isArray(value.libraries)
const isPolicyTransition = (value: unknown): value is PolicyTransitionView =>
  typeof value === 'object' &&
  value !== null &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-transition-v1' &&
  'transition_id' in value &&
  typeof value.transition_id === 'string' &&
  'from_policy_id' in value &&
  typeof value.from_policy_id === 'string' &&
  'to_policy_id' in value &&
  typeof value.to_policy_id === 'string' &&
  'state' in value &&
  typeof value.state === 'string' &&
  'generation' in value &&
  typeof value.generation === 'number' &&
  'completed' in value &&
  typeof value.completed === 'number' &&
  'failed' in value &&
  typeof value.failed === 'number' &&
  'total' in value &&
  typeof value.total === 'number' &&
  'acknowledged' in value &&
  typeof value.acknowledged === 'boolean'
const isPolicyActivation = (value: unknown, policyId: string): value is PolicyActivationView =>
  typeof value === 'object' &&
  value !== null &&
  'policy_id' in value &&
  value.policy_id === policyId &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-activation-v1' &&
  'transition' in value &&
  (value.transition === null || isPolicyTransition(value.transition))
const automaticQualified = (
  field: FieldDefinition,
  option: string,
  qualification: readonly {
    readonly automatic_available: boolean
    readonly field: string
    readonly option: string
  }[],
) => {
  const rich = qualification.find((entry) => entry.field === field.key && entry.option === option)
  return rich
    ? rich.automatic_available
    : field.automatic && !(field.key === 'publisher_preference' && option === 'imprint')
}
// The disclosure lists one consequence per representation. The catalogue's
// admitted/transformation halves are qualification mechanics; the owner is
// choosing between outcomes, so the effect and what happens when it cannot
// apply are the two sentences that belong on the line.
const representationConsequence = (guidance: RepresentationGuidance | undefined) =>
  guidance === undefined
    ? 'No owner-readable consequence is recorded for this representation.'
    : `${guidance.effect.slice(0, 1).toUpperCase()}${guidance.effect.slice(1)}; ${guidance.fallback}.`

const Check = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24">
    <path d="m5 12 4 4L19 7" />
  </svg>
)

export default function PolicyPage({ data }: { data: PolicyCritical }) {
  const { http, queryClient } = useReviewContext()
  const search = policyRoute.useSearch()
  const navigate = useNavigate({ from: '/policy' })
  const activeChapter = search.section
  const [draft, setDraft] = useState<PolicyDraft>(() => ({
    name: data.state.profile.name,
    options: { ...data.state.profile.options },
    destinations: { ...data.state.profile.destinations },
  }))
  const [snapshotId, setSnapshotId] = useState<string>()
  const [previewActivePolicyId, setPreviewActivePolicyId] = useState<string>()
  const [preview, setPreview] = useState<PolicyPreviewView>()
  const [stage, setStage] = useState<PolicyStage>('editing')
  const [status, setStatus] = useState<{
    tone: 'neutral' | 'success' | 'error' | 'warning'
    message: string
  }>({ tone: 'neutral', message: 'Draft matches active policy.' })
  // The completion acknowledgement belongs to the activation this page
  // performed. Anything the owner still has to act on -- work in flight, or a
  // failure to retry -- is shown whoever is looking.
  const [activatedTransitionId, setActivatedTransitionId] = useState<string>()
  const shadow = data.shadow
  const safety = data.safety
  const sample = useQuery({
    queryKey: ['policy', 'shadow', 'sample'],
    queryFn: ({ signal }) => http.getShadowSample(signal),
    staleTime: 30_000,
    retry: false,
  })
  const transition = useQuery({
    queryKey: ['policy', 'transition'],
    queryFn: ({ signal }) => http.getPolicyTransition(signal),
    staleTime: 1_000,
    retry: false,
    refetchInterval: (query) => (query.state.data?.state === 'processing' ? 1_500 : false),
  })
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['policy'] }),
      queryClient.invalidateQueries({ queryKey: ['library'] }),
      queryClient.invalidateQueries({ queryKey: ['attention'] }),
    ])
  }
  const command = useMutation({
    mutationFn: async ({ action, body }: { action: string; body?: unknown }) => {
      if (action === 'validate' || action === 'drafts')
        return http.policyCommand<{ policy_id: string; schema_version: string }>(action, body)
      if (action === 'preview') return http.policyCommand<PolicyPreviewView>('preview', body)
      if (action === 'activate') return http.policyCommand<PolicyActivationView>('activate', body)
      return http.policyCommand<PolicyTransitionView | ShadowPostureView>(action, body)
    },
    retry: false,
    onSettled: refresh,
  })

  const editDraft = (next: PolicyDraft) => {
    setDraft(next)
    setSnapshotId(undefined)
    setPreviewActivePolicyId(undefined)
    setPreview(undefined)
    setStage('editing')
    setStatus({
      tone: 'neutral',
      message: derivePolicyDraftDisplay(data.state, next, fieldDefaults).changed
        ? 'Draft has unreviewed changes.'
        : 'Draft matches active policy.',
    })
  }
  const runAuxiliary = async (action: string, body?: unknown) => {
    setStatus({ tone: 'neutral', message: 'Updating policy state…' })
    try {
      await command.mutateAsync({ action, body })
      setStatus({ tone: 'success', message: 'Policy state updated from the authoritative server.' })
    } catch (error) {
      setStatus(
        error instanceof AmbiguousMutationError
          ? {
              tone: 'warning',
              message:
                'The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.',
            }
          : {
              tone: 'error',
              message: 'Nothing changed. Review the current server state and try again.',
            },
      )
    }
  }
  const validateAndPreview = async () => {
    setStage('validating')
    setSnapshotId(undefined)
    setPreviewActivePolicyId(undefined)
    setPreview(undefined)
    setStatus({ tone: 'neutral', message: 'Validating the complete draft…' })
    let phase: 'validate' | 'drafts' | 'preview' = 'validate'
    try {
      await command.mutateAsync({ action: 'validate', body: draft })
      phase = 'drafts'
      setStatus({ tone: 'neutral', message: 'Validation passed. Saving the immutable snapshot…' })
      const saved = (await command.mutateAsync({ action: 'drafts', body: draft })) as {
        policy_id: string
      }
      setSnapshotId(saved.policy_id)
      phase = 'preview'
      setStatus({ tone: 'neutral', message: 'Snapshot saved. Inspecting current unapplied work…' })
      const result = await command.mutateAsync({ action: 'preview', body: draft })
      if (!isPolicyPreview(result) || result.policy_id !== saved.policy_id)
        throw new ApiError(502, 'policy_snapshot_mismatch')
      setPreview(result)
      setPreviewActivePolicyId(result.active_policy_id)
      setStage('preview')
      setStatus({
        tone: 'success',
        message: 'Impact preview is current. The active policy is unchanged.',
      })
    } catch (error) {
      setStage('editing')
      if (error instanceof AmbiguousMutationError)
        setStatus({
          tone: 'warning',
          message:
            'The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.',
        })
      else if (error instanceof ApiError && error.status === 422)
        setStatus({
          tone: 'error',
          message: 'The draft is not valid. Review its field choices and try again.',
        })
      else if (phase === 'preview')
        setStatus({
          tone: 'error',
          message:
            'The immutable snapshot was saved, but its no-write impact preview is unavailable. Retry when the current state is visible.',
        })
      else if (phase === 'drafts')
        setStatus({
          tone: 'error',
          message:
            'Validation passed, but the immutable snapshot was not saved. Nothing became active; try again.',
        })
      else
        setStatus({
          tone: 'error',
          message:
            'The complete draft could not be validated. Nothing was saved or activated; try again.',
        })
    }
  }
  const activate = async () => {
    if (!snapshotId || !preview || !previewActivePolicyId) return
    setStatus({ tone: 'neutral', message: 'Activating the reviewed policy…' })
    try {
      const result = await command.mutateAsync({
        action: 'activate',
        body: { policy_id: snapshotId, expected_active_policy_id: previewActivePolicyId },
      })
      if (!isPolicyActivation(result, snapshotId))
        throw new ApiError(502, 'invalid_policy_activation_response')
      setActivatedTransitionId(result.transition?.transition_id)
      setStage('activated')
      setStatus({
        tone: 'success',
        message: 'Policy activated. Current unapplied work is being reconciled.',
      })
    } catch (error) {
      if (
        error instanceof AmbiguousMutationError ||
        error instanceof BuildMismatchError ||
        (error instanceof ApiError && error.status >= 500)
      ) {
        setStage('preview')
        setStatus({
          tone: 'warning',
          message:
            'The activation result is uncertain. Authoritative policy state is refreshing; verify it before trying again.',
        })
      } else if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'policy_activation_is_stale'
      ) {
        setSnapshotId(undefined)
        setPreviewActivePolicyId(undefined)
        setPreview(undefined)
        setStage('editing')
        setStatus({
          tone: 'warning',
          message:
            'The active policy changed before activation. Review the refreshed policy and create a new draft.',
        })
      } else if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'policy_transition_is_processing'
      ) {
        setStage('preview')
        setStatus({
          tone: 'warning',
          message:
            'The previous policy update is still processing. This reviewed snapshot remains available; retry after the transition is resolved.',
        })
      } else {
        setStatus({
          tone: 'error',
          message: 'Activation failed safely. The reviewed snapshot remains available to retry.',
        })
      }
    }
  }
  const openChapter = async (section: PolicySection) => {
    await navigate({ search: { section }, replace: true })
    requestAnimationFrame(() =>
      document.getElementById(`policy-${section}`)?.focus({ preventScroll: true }),
    )
  }
  const automaticAllowed = shadow.posture === 'preview' || shadow.posture === 'automatic'
  const draftDisplay = derivePolicyDraftDisplay(data.state, draft, fieldDefaults)
  const fieldChanged = (field: FieldDefinition) => {
    const fallback = fieldDefaults[field.key]
    const activeOption = data.state.profile.options[field.key] ?? fallback?.option ?? ''
    const activeDestination =
      data.state.profile.destinations[field.key] ?? fallback?.destination ?? 'ask'
    const option = draft.options[field.key] ?? fallback?.option ?? ''
    const destination = draft.destinations[field.key] ?? fallback?.destination ?? 'ask'
    return activeOption !== option || activeDestination !== destination
  }
  const changedFields = fields.filter(fieldChanged)
  const currentChapter = chapters.find((chapter) => chapter.key === activeChapter) ?? {
    key: 'identity' as const,
    label: 'Identity',
    description: 'How a work names itself and its contributors.',
  }
  const visibleFields = fields.filter((field) => field.chapter === activeChapter)
  const changedChapterLabels = chapters
    .filter((chapter) => changedFields.some((field) => field.chapter === chapter.key))
    .map((chapter) => chapter.label)
  const previewRows = useMemo(() => {
    const cases =
      preview?.libraries.flatMap((library) =>
        library.cases.map((rawCase, caseIndex) => {
          const item = rawCase as unknown as {
            readonly fields: readonly PolicyPreviewField[]
            readonly report_id: string
            readonly work_label: string
          }
          const caseId = item.report_id || String(caseIndex)
          return item.fields.map((field) => ({
            caseId,
            id: `${caseId}:${field.field}`,
            library: library.library,
            work: item.work_label,
            field: readableCategory(field.field),
            impact: policyImpactDisplay(field),
          }))
        }),
      ) ?? []
    return [...cases.flatMap((rows) => rows.slice(0, 1)), ...cases.flatMap((rows) => rows.slice(1))]
  }, [preview])
  const impactTotals = useMemo(
    () =>
      preview
        ? {
            currentWorks: preview.libraries.reduce(
              (total, library) => total + library.current_unapplied_count,
              0,
            ),
            libraries: preview.libraries.length,
            ...policyImpactTotals(previewRows),
          }
        : undefined,
    [preview, previewRows],
  )
  // A fresh container carries a seeded generation-1 row so the baseline has a
  // durable identity. It is not an activation, and reporting it as one told the
  // owner they had completed a policy update of zero items they never started.
  const baselinePolicy = transition.data?.origin === 'baseline'
  const ownerActivation =
    transition.data && !baselinePolicy && !transition.data.acknowledged
      ? transition.data
      : undefined
  // Work still running and a failure still to retry are the owner's whoever is
  // looking. A clean completion acknowledges one activation, so it is shown to
  // the session that performed it and expires with that session.
  const activationNotice =
    ownerActivation &&
    (ownerActivation.state === 'processing' ||
      ownerActivation.failed > 0 ||
      ownerActivation.transition_id === activatedTransitionId)
      ? ownerActivation
      : undefined
  const editorRef = useRef<HTMLDivElement>(null)
  const trayRef = useRef<HTMLElement>(null)
  const transitionCopy = activationNotice
    ? activationNotice.state === 'processing'
      ? `Applying updated policy · ${String(activationNotice.completed)} of ${String(activationNotice.total)}`
      : `${activationNotice.failed ? 'Completed with failures' : 'Policy update complete'} · ${String(activationNotice.completed)} of ${String(activationNotice.total)}`
    : undefined

  useEffect(() => {
    const frame = requestAnimationFrame(() =>
      document.getElementById(`policy-${activeChapter}`)?.focus({ preventScroll: true }),
    )
    return () => cancelAnimationFrame(frame)
  }, [activeChapter])

  // The sticky tray spans both columns, so the sidebar must stop above it or
  // the tray covers the safety controls at the bottom of the left column. The
  // tray's height is content-dependent (stage copy, reflow, zoom) and the page
  // keeps a tail below the editor, so the reserve is measured rather than
  // guessed; the stylesheet subtracts it from the sidebar's bounded height.
  useLayoutEffect(() => {
    const editor = editorRef.current
    const tray = trayRef.current
    if (!editor || !tray) return undefined
    const measure = () => {
      const bottom = editor.getBoundingClientRect().bottom + window.scrollY
      const tail = Math.max(document.documentElement.scrollHeight - bottom, 0)
      editor.style.setProperty(
        '--policy-tray-reserve',
        `${String(Math.ceil(tray.offsetHeight + tail + 24))}px`,
      )
    }
    measure()
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('resize', measure)
    }
  })

  const completedValidate = stage === 'preview' || stage === 'activation' || stage === 'activated'
  const completedPreview = stage === 'activation' || stage === 'activated'
  const currentStep =
    stage === 'editing' || stage === 'validating' ? 0 : stage === 'preview' ? 1 : 2
  const finishTitle =
    stage === 'activated'
      ? 'Policy activated'
      : stage === 'activation'
        ? 'Ready to activate'
        : stage === 'preview'
          ? `${String(impactTotals?.changedWorks ?? 0)} sampled ${(impactTotals?.changedWorks ?? 0) === 1 ? 'work' : 'works'} with changes`
          : stage === 'validating'
            ? 'Validating the working draft…'
            : draftDisplay.changed
              ? `${String(changedFields.length)} draft ${changedFields.length === 1 ? 'change is' : 'changes are'} ready to validate`
              : 'Draft matches the active policy'
  const finishDetail =
    stage === 'activated'
      ? `${String(impactTotals?.currentWorks ?? 0)} current unapplied ${(impactTotals?.currentWorks ?? 0) === 1 ? 'work is' : 'works are'} queued for guarded reconciliation. Applied history is untouched.`
      : stage === 'activation'
        ? 'Review the unchanged preview before making this draft active.'
        : stage === 'preview'
          ? `Validated across all chapters · ${String(impactTotals?.materialChanges ?? 0)} material ${(impactTotals?.materialChanges ?? 0) === 1 ? 'change' : 'changes'} in the bounded sample${changedChapterLabels.length ? ` · draft changes in ${changedChapterLabels.join(', ')}` : ''} · snapshot saved automatically.`
          : stage === 'validating'
            ? 'Checking every chapter, Automatic qualification, and exact library scope.'
            : draftDisplay.changed
              ? `Changes in ${changedChapterLabels.join(', ')} · validation always checks and saves the complete draft.`
              : 'Change any field to create a new draft — validation always covers the whole policy.'

  return (
    <div className="cm-policy-route" data-route-focus tabIndex={-1}>
      <header className="cm-page-heading">
        <div>
          <h1>Library policy</h1>
          <p>Choose how metadata is stored and when you want to review it.</p>
        </div>
        <span className="cm-policy-state">
          {stage === 'activated'
            ? 'Active policy updated'
            : draftDisplay.changed
              ? `${String(changedFields.length)} draft ${changedFields.length === 1 ? 'change' : 'changes'}`
              : 'Draft matches active policy'}
        </span>
      </header>
      <div className="cm-policy-editor" ref={editorRef}>
        <aside className="cm-policy-sidebar">
          <header>
            <strong>{data.state.profile.name}</strong>
            <small>
              {`${baselinePolicy ? 'Active baseline' : 'Active policy'} · ${
                draftDisplay.changed ? 'draft has unreviewed changes' : 'draft matches active'
              }`}
            </small>
          </header>
          {transitionCopy ? (
            <p className="cm-policy-transition" role="status">
              {transitionCopy}
            </p>
          ) : null}
          <nav aria-label="Policy chapters">
            {chapters.map((chapter) => {
              const chapterFields = fields.filter((field) => field.chapter === chapter.key)
              const changes = chapterFields.filter(fieldChanged).length
              const changedFieldLabel = `${String(changes)} changed ${changes === 1 ? 'field' : 'fields'}`
              const labelId = `policy-${chapter.key}-label`
              const descriptionId = `policy-${chapter.key}-description`
              return (
                <button
                  key={chapter.key}
                  type="button"
                  aria-current={activeChapter === chapter.key ? 'page' : undefined}
                  aria-labelledby={`${labelId} ${descriptionId}`}
                  aria-describedby={`policy-${chapter.key}-changes`}
                  onClick={() => void openChapter(chapter.key)}
                >
                  <span>
                    <strong id={labelId}>{chapter.label}</strong>
                    <small id={descriptionId}>{chapter.description}</small>
                  </span>
                  <b aria-hidden="true">{changes}</b>
                  <span className="sr-only" id={`policy-${chapter.key}-changes`}>
                    {changedFieldLabel}
                  </span>
                </button>
              )
            })}
          </nav>
          <p className="cm-policy-chapter-legend">Changed fields</p>
          <details className="cm-policy-mode">
            <summary>
              <strong>
                {shadow.posture === 'preview'
                  ? 'Preview mode active'
                  : shadow.posture === 'automatic'
                    ? 'Qualified Automatic active'
                    : 'Human review active'}
              </strong>
              <span>Safety</span>
            </summary>
            <div>
              <p>
                {shadow.posture === 'preview'
                  ? 'Outcomes remain non-authoritative.'
                  : shadow.posture === 'automatic'
                    ? 'Automatic remains decision-only and separately controlled.'
                    : 'Enter Preview before enabling qualified Automatic.'}
              </p>
              <div className="cm-policy-safety-actions">
                <button
                  disabled={command.isPending || shadow.posture === 'preview'}
                  onClick={() => void runAuxiliary('shadow/enter')}
                >
                  Enter Preview mode
                </button>
                <button
                  disabled={command.isPending || shadow.posture !== 'preview'}
                  onClick={() => void runAuxiliary('shadow/confirm-automatic')}
                >
                  Activate qualified Automatic
                </button>
                <button
                  disabled={command.isPending}
                  onClick={() =>
                    void runAuxiliary('automatic-safety/pause', { paused: !safety.paused })
                  }
                >
                  {safety.paused ? 'Resume automatic decisions' : 'Pause automatic decisions'}
                </button>
                {safety.global_suspension ? (
                  <button
                    disabled={command.isPending}
                    onClick={() =>
                      void runAuxiliary('automatic-safety/recover', { category: null })
                    }
                  >
                    Recover global suspension
                  </button>
                ) : null}
              </div>
              {Object.keys(safety.category_suspensions).map((category) => (
                <button
                  key={category}
                  onClick={() => void runAuxiliary('automatic-safety/recover', { category })}
                >
                  Recover {readableCategory(category)} suspension
                </button>
              ))}
              <small>
                {sample.isPending
                  ? 'Loading recent Preview outcomes…'
                  : sample.isError
                    ? 'Recent Preview outcomes are unavailable.'
                    : sample.data.items.length
                      ? `${String(sample.data.items.length)} recent non-authoritative outcome sets.`
                      : 'No Preview outcomes or safety suspensions need attention.'}
              </small>
            </div>
          </details>
        </aside>
        <section className="cm-policy-content" id={`policy-${activeChapter}`} tabIndex={-1}>
          <header>
            <div>
              <h2>{currentChapter.label}</h2>
              <p>{currentChapter.description}</p>
            </div>
          </header>
          <div className="cm-policy-field-list">
            {visibleFields.map((field) => {
              const option = draft.options[field.key] ?? field.options[0] ?? ''
              const destination =
                draft.destinations[field.key] ?? (field.unsupported ? 'unsupported' : 'ask')
              const qualified = automaticQualified(field, option, data.state.rich_qualification)
              const automaticAvailable = qualified && automaticAllowed
              const changed = fieldChanged(field)
              return (
                <section
                  className={`cm-policy-field ${changed ? 'is-changed' : ''}`}
                  aria-label={field.label}
                  key={field.key}
                >
                  <header>
                    <span>
                      <strong>{field.label}</strong>
                      {changed ? <small>Draft changed</small> : null}
                    </span>
                    <p>{field.meaning}</p>
                    <details className="cm-policy-field-guidance">
                      <summary>What this changes</summary>
                      {field.key === 'series' ? (
                        <p>
                          One provider can supply enough backing; corroboration strengthens the
                          evidence. Qualification measures these rules against checked books.
                          Numbering is supplied by the provider and is not independently verified
                          for each book.
                        </p>
                      ) : null}
                      <ul>
                        {field.options.map((value) => (
                          <li key={value}>
                            <strong>{field.optionLabels[value] ?? value}:</strong>{' '}
                            <span>
                              {representationConsequence(
                                policyRepresentationGuidance[`${field.key}:${value}`],
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  </header>
                  {field.unsupported ? (
                    <div className="cm-policy-unavailable">
                      <strong>Unsupported destination</strong>
                      <span>
                        This field is shown for context only. It has no review mode or
                        representation choice, so it cannot be changed in this draft.
                      </span>
                    </div>
                  ) : (
                    <>
                      <div className="cm-policy-mode-control">
                        <span className="cm-policy-control-heading">
                          <strong>Review mode</strong>
                          {automaticAvailable ? null : <small>Automatic mode not supported</small>}
                        </span>
                        <div
                          className="cm-policy-posture"
                          role="group"
                          aria-label={`${field.label} review mode`}
                        >
                          <button
                            type="button"
                            aria-pressed={destination === 'automatic'}
                            disabled={command.isPending || !automaticAvailable}
                            onClick={() =>
                              editDraft({
                                ...draft,
                                destinations: { ...draft.destinations, [field.key]: 'automatic' },
                              })
                            }
                          >
                            Automatic
                          </button>
                          <button
                            type="button"
                            aria-pressed={destination === 'ask'}
                            disabled={command.isPending}
                            onClick={() =>
                              editDraft({
                                ...draft,
                                destinations: { ...draft.destinations, [field.key]: 'ask' },
                              })
                            }
                          >
                            Ask me
                          </button>
                          <button
                            type="button"
                            aria-pressed={destination === 'ignore'}
                            disabled={command.isPending}
                            onClick={() =>
                              editDraft({
                                ...draft,
                                destinations: { ...draft.destinations, [field.key]: 'ignore' },
                              })
                            }
                          >
                            Ignore
                          </button>
                        </div>
                      </div>
                      <label className="cm-policy-representation">
                        <span>Representation</span>
                        <Dropdown
                          ariaLabel={`${field.label} representation`}
                          value={option}
                          options={field.options.map((value) => ({
                            value,
                            label: field.optionLabels[value] ?? value,
                          }))}
                          disabled={command.isPending || field.options.length === 1}
                          onChange={(nextOption) => {
                            const movesToAsk =
                              destination === 'automatic' &&
                              !automaticQualified(field, nextOption, data.state.rich_qualification)
                            editDraft({
                              ...draft,
                              options: { ...draft.options, [field.key]: nextOption },
                              destinations: {
                                ...draft.destinations,
                                [field.key]: movesToAsk ? 'ask' : destination,
                              },
                            })
                            if (movesToAsk)
                              setStatus({
                                tone: 'warning',
                                message: `${field.label} now uses a Manual review only representation, so review mode changed from Automatic to Ask me.`,
                              })
                          }}
                        />
                        {destination === 'automatic' && !qualified ? (
                          <small className="cm-policy-representation-consequence">
                            This representation is Manual review only. Selecting it changes review
                            mode from Automatic to Ask me.
                          </small>
                        ) : null}
                      </label>
                    </>
                  )}
                </section>
              )
            })}
          </div>
          {activeChapter === 'curation' ? <DescriptionAdviceSettings http={http} /> : null}
        </section>
        <section
          className={`cm-policy-finish stage-${stage}`}
          aria-label="Whole policy draft impact and activation"
          ref={trayRef}
        >
          <div className="cm-policy-impact-summary" aria-live="polite">
            <strong>{finishTitle}</strong>
            <span>{finishDetail}</span>
          </div>
          <ol aria-label="Policy activation steps">
            {['Validate', 'Preview', 'Activate'].map((label, index) => {
              const done =
                index === 0
                  ? completedValidate
                  : index === 1
                    ? completedPreview
                    : stage === 'activated'
              return (
                <li
                  key={label}
                  className={done ? 'done' : ''}
                  aria-current={stage !== 'activated' && currentStep === index ? 'step' : undefined}
                >
                  <span>{done ? <Check /> : index + 1}</span>
                  <small>{label}</small>
                </li>
              )
            })}
          </ol>
          <div className="cm-policy-finish-actions">
            {stage === 'preview' ? (
              <button disabled={command.isPending} onClick={() => setStage('editing')}>
                Back to editing
              </button>
            ) : null}
            {stage === 'activation' ? (
              <button disabled={command.isPending} onClick={() => setStage('preview')}>
                Back to preview
              </button>
            ) : null}
            {stage === 'editing' ? (
              <button
                className="primary-action"
                disabled={command.isPending || !draftDisplay.changed}
                onClick={() => void validateAndPreview()}
              >
                Validate and preview
              </button>
            ) : null}
            {stage === 'validating' ? (
              <button className="primary-action" disabled>
                Validating…
              </button>
            ) : null}
            {stage === 'preview' ? (
              <button
                className="primary-action"
                disabled={command.isPending || !snapshotId || !preview || !previewActivePolicyId}
                onClick={() => setStage('activation')}
              >
                Review activation
              </button>
            ) : null}
            {stage === 'activation' ? (
              <button
                className="primary-action"
                disabled={command.isPending}
                onClick={() => void activate()}
              >
                {command.isPending ? 'Activating…' : 'Activate policy'}
              </button>
            ) : null}
            {stage === 'activated' ? (
              <button className="primary-action" onClick={() => setStage('editing')}>
                Continue editing
              </button>
            ) : null}
          </div>
          {stage === 'validating' ? (
            <section className="cm-policy-validation" aria-label="Validation in progress">
              <header>
                <span className="cm-policy-validation-spinner" />
                <div>
                  <strong>Checking this draft</strong>
                  <small>The snapshot is saved only if every check passes.</small>
                </div>
              </header>
              <ul>
                <li>
                  <span />
                  <div>
                    <strong>Document structure</strong>
                    <small>14 supported field definitions</small>
                  </div>
                </li>
                <li>
                  <span />
                  <div>
                    <strong>Automatic qualification</strong>
                    <small>Every Automatic representation is admitted</small>
                  </div>
                </li>
                <li>
                  <span />
                  <div>
                    <strong>Library scope</strong>
                    <small>Exact current scope</small>
                  </div>
                </li>
              </ul>
            </section>
          ) : null}
          {(stage === 'preview' || stage === 'activation') && preview ? (
            <section className="cm-policy-result">
              <aside>
                <header>
                  <span>
                    <Check />
                  </span>
                  <div>
                    <strong>Validated and saved</strong>
                    <small>Immutable snapshot for this preview</small>
                  </div>
                </header>
                <dl>
                  <div>
                    <dt>Structure</dt>
                    <dd>Passed</dd>
                  </div>
                  <div>
                    <dt>Qualification</dt>
                    <dd>Passed</dd>
                  </div>
                  <div>
                    <dt>Scope</dt>
                    <dd>
                      {impactTotals?.libraries
                        ? `${String(impactTotals.libraries)} ${impactTotals.libraries === 1 ? 'library' : 'libraries'}`
                        : 'Current'}
                    </dd>
                  </div>
                </dl>
              </aside>
              <div className="cm-policy-preview">
                <header>
                  <div>
                    <strong>Impact preview</strong>
                    <small>Current unapplied work only · no metadata writes</small>
                  </div>
                  <b
                    aria-label={`${String(impactTotals?.changedWorks ?? 0)} sampled works with changes`}
                  >
                    {String(impactTotals?.changedWorks ?? 0)}
                  </b>
                </header>
                <div className="cm-policy-preview-list">
                  {previewRows.length
                    ? previewRows.slice(0, 6).map((row) => (
                        <div key={row.id} className={row.impact.changed ? 'is-change' : ''}>
                          <span className="cm-policy-work-mark" aria-hidden="true">
                            {row.work.slice(0, 2).toUpperCase()}
                          </span>
                          <span>
                            <strong>{row.work}</strong>
                            <small>
                              {row.library} · {row.field}
                            </small>
                          </span>
                          <span className="cm-policy-impact">
                            <b>{row.impact.title}</b>
                            <small>· {row.impact.detail}</small>
                            <details>
                              <summary>Technical detail</summary>
                              <code>{row.impact.technicalDetail}</code>
                            </details>
                          </span>
                        </div>
                      ))
                    : preview.libraries.map((library) => (
                        <div key={library.library}>
                          <span className="cm-policy-work-mark" aria-hidden="true">
                            {library.library.slice(0, 2).toUpperCase()}
                          </span>
                          <span>
                            <strong>{library.library}</strong>
                            <small>
                              {library.affected_categories.map(readableCategory).join(', ') ||
                                'No changed fields'}
                            </small>
                          </span>
                          <span className="cm-policy-impact">
                            <b>
                              {library.current_unapplied_count === 0 ? 'No change' : 'Needs review'}
                            </b>
                            <small>
                              ·{' '}
                              {library.current_unapplied_count === 0
                                ? 'No current unapplied work remains to inspect'
                                : `No sampled field outcome; ${String(library.current_unapplied_count)} current unapplied ${library.current_unapplied_count === 1 ? 'work remains' : 'works remain'} to inspect`}
                            </small>
                          </span>
                        </div>
                      ))}
                </div>
                {previewRows.length > 6 ? (
                  <footer>
                    Plus {String(previewRows.length - 6)} more exact field outcomes in this preview
                  </footer>
                ) : preview.libraries.some((library) => library.cases_truncated) ? (
                  <footer>
                    Additional current cases are included in the authoritative preview total.
                  </footer>
                ) : null}
              </div>
              {stage === 'activation' ? (
                <footer className="cm-policy-activation">
                  <strong>Activate this reviewed draft?</strong>
                  <span>
                    The active policy changes only after this guarded command succeeds. Applied
                    history remains untouched.
                  </span>
                </footer>
              ) : null}
            </section>
          ) : null}
          {stage === 'activated' ? (
            <section className="cm-policy-activated">
              <span>
                <Check />
              </span>
              <div>
                <strong>The reviewed policy is now active</strong>
                <small>
                  {String(impactTotals?.currentWorks ?? 0)} current unapplied{' '}
                  {(impactTotals?.currentWorks ?? 0) === 1 ? 'work can' : 'works can'} enter
                  separately guarded reconciliation.
                </small>
              </div>
            </section>
          ) : null}
        </section>
      </div>
      <p
        className={`policy-status ${status.tone}`}
        role={status.tone === 'error' ? 'alert' : 'status'}
      >
        {status.message}
      </p>
      {activationNotice ? (
        <section className="policy-cutover" aria-label="Policy activation progress">
          <h2>
            {activationNotice.state === 'processing'
              ? 'Applying updated policy'
              : activationNotice.failed
                ? 'Activation completed with failures'
                : 'Activation complete'}
          </h2>
          <p>
            {String(activationNotice.completed)} of {String(activationNotice.total)} current
            unapplied items reconciled. Applied history is untouched.
          </p>
          <div className="policy-workflow-actions">
            {activationNotice.failed ? (
              <button
                disabled={command.isPending}
                onClick={() =>
                  void runAuxiliary(`transitions/${activationNotice.transition_id}/retry`)
                }
              >
                Retry failed reconciliation
              </button>
            ) : null}
            {activationNotice.state !== 'processing' ? (
              <button
                disabled={command.isPending}
                onClick={() =>
                  void runAuxiliary(`transitions/${activationNotice.transition_id}/acknowledge`)
                }
              >
                Dismiss update
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  )
}
