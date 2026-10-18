import { invalidateBackgroundQueries } from '../queryWork'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  derivePolicyDraftDisplay,
  policyAutomaticQualification,
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
import { ChangeCount } from '../components/ChangeCount'
import { DescriptionAdviceSettings } from '../components/DescriptionAdviceSettings'
import { Disclosure } from '../components/Disclosure'
import { Dropdown } from '../components/Dropdown'
import { FinishTray } from '../components/FinishTray'
import { SegmentedControl } from '../components/SegmentedControl'
import { automaticRefusalReasons, fieldLabel } from '../labels'
import { policyRoute, useReviewContext } from '../router'
import type { PolicyCritical } from '../router'

const audiobookOperationLabels = {
  fill: 'Fill an empty field',
  preserve: 'Keep the current value',
  normalize: 'Normalize the representation',
  add: 'Add supported members',
  replace: 'Replace the current value',
  replace_invalid: 'Replace an invalid ASIN',
  replace_recording: 'Replace a valid ASIN',
} as const

interface FieldDefinition {
  key: string
  label: string
  meaning: string
  options: readonly string[]
  optionLabels: Readonly<Record<string, string>>
  chapter: PolicySection
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
  },
  {
    key: 'contributor_names',
    label: 'Authors and narrators',
    meaning: 'Preserve contributor order while choosing how supported names appear.',
    options: ['preserve', 'canonical'],
    optionLabels: { preserve: 'Preserve supported form', canonical: 'Use canonical names' },
    chapter: 'identity',
  },
  {
    key: 'language_rendering',
    label: 'Language',
    meaning:
      'Group supported language names and codes; keep an equivalent current value unchanged.',
    options: ['preserve', 'display_name'],
    optionLabels: {
      preserve: 'Preserve supported form',
      display_name: 'Use language display name',
    },
    chapter: 'identity',
  },
  {
    key: 'subtitle',
    label: 'Subtitle',
    meaning: 'Use a supported descriptive subtitle, or a corroborated title and subtitle pair.',
    options: ['fill_empty', 'replace_descriptive', 'corroborated_split'],
    optionLabels: {
      fill_empty: 'Fill only when empty',
      replace_descriptive: 'Replace a promotional subtitle',
      corroborated_split: 'Use corroborated split',
    },
    chapter: 'identity',
  },
  {
    key: 'date_precision',
    label: 'Publication date and year',
    meaning:
      'Audiobooks use original work publication; ebooks retain edition publication. Recording release dates stay separate.',
    options: ['year', 'full_date'],
    optionLabels: { year: 'Year only', full_date: 'Keep source date precision' },
    chapter: 'publication',
  },
  {
    key: 'publisher_preference',
    label: 'Publisher',
    meaning: 'Choose the supported publisher or imprint for the applicable work or edition.',
    options: ['publisher', 'imprint'],
    optionLabels: { publisher: 'Prefer publisher', imprint: 'Prefer imprint' },
    chapter: 'publication',
  },
  {
    key: 'isbn_form',
    label: 'ISBN',
    meaning:
      'Use an ISBN associated with the selected audiobook recording or ebook edition; a lookup hint alone is not enough.',
    options: ['preserve_exact', 'prefer_isbn13'],
    optionLabels: { preserve_exact: 'Preserve exact ISBN', prefer_isbn13: 'Prefer ISBN-13' },
    chapter: 'publication',
  },
  {
    key: 'series',
    label: 'Series memberships',
    meaning:
      'Fill empty Series, add supported memberships, or explicitly replace the current list.',
    options: [
      'primary_narrative',
      'source_order',
      'all_qualified',
      'add_qualified',
      'replace_qualified',
    ],
    optionLabels: {
      primary_narrative: 'Primary narrative',
      source_order: 'First source membership',
      all_qualified: 'All qualified memberships',
      add_qualified: 'Add supported memberships',
      replace_qualified: 'Replace with supported memberships',
    },
    chapter: 'publication',
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
  },
  {
    key: 'tags',
    label: 'Tags',
    meaning:
      'Tags are off by default. When enabled, use compatible source tags or review them yourself.',
    options: ['off', 'fill_empty', 'add', 'replace'],
    optionLabels: {
      off: 'Off',
      fill_empty: 'Fill only when empty',
      add: 'Add missing values',
      replace: 'Replace with source list',
    },
    chapter: 'curation',
  },
  {
    key: 'description_fill',
    label: 'Description',
    meaning:
      'Deterministic selection retains one source description. Audiobooks preserve useful existing prose; optional advice can separately rewrite or combine text.',
    options: ['safe_plain_text', 'google_visible_text', 'replace_safe_plain_text'],
    optionLabels: {
      safe_plain_text: 'Fill empty with safe plain text',
      google_visible_text: 'Visible Google text',
      replace_safe_plain_text: 'Fill empty or replace safe text',
    },
    chapter: 'curation',
  },
  {
    key: 'cover_order',
    label: 'Cover',
    meaning:
      'Use a confirmed cover. Audiobooks keep an adequate cover and replace only a small incumbent with a materially larger match.',
    options: ['fixed_source_order', 'replace_if_strictly_better'],
    optionLabels: {
      fixed_source_order: 'Fill from confirmed order',
      replace_if_strictly_better: 'Replace with a material improvement',
    },
    chapter: 'curation',
  },
  {
    key: 'asin',
    label: 'ASIN',
    meaning:
      'Use an ASIN associated with the audiobook recording, or select an admitted ebook manifestation.',
    options: ['preserve_exact', 'ebook_manifestation'],
    optionLabels: {
      preserve_exact: 'Preserve exact ASIN',
      ebook_manifestation: 'Ebook manifestation',
    },
    chapter: 'curation',
  },
  {
    key: 'explicit',
    label: 'Explicit-content flag',
    meaning:
      'An adult-product source may set an audiobook flag to true. Missing or false evidence never clears it or certifies clean content.',
    options: ['unsupported', 'adult_product'],
    optionLabels: {
      unsupported: 'Leave flag unchanged',
      adult_product: 'Set from adult-product evidence',
    },
    chapter: 'unsupported',
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
    transformation: 'keeps an equivalent current value; when filling, prefers an observed code',
    effect: 'Automatic writes only a form present in the selected evidence',
    fallback:
      'uses another admitted form when no code is present; undetermined values provide no authority',
  },
  'language_rendering:display_name': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation:
      'keeps an equivalent current value; when filling, prefers an observed English name',
    effect: 'Automatic writes only a form present in the selected evidence',
    fallback:
      'uses an admitted code when no name is present; undetermined values provide no authority',
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
    fallback:
      'requires both fields to be Automatic and separately consented; either blocked member stops the whole pair',
  },
  'subtitle:replace_descriptive': {
    admitted: 'a supported descriptive subtitle for this audiobook work',
    transformation: 'replaces only a recognized whole-field promotion',
    effect: 'fills an empty Subtitle or replaces an award, bestseller or adaptation announcement',
    fallback: 'keeps genuine descriptive text and never clears a subtitle from missing evidence',
  },
  'date_precision:year': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without reducing the candidate',
    effect:
      'Automatic writes only that exact selected Publication date or Publication year destination value for each qualified decision',
    fallback:
      'requires a qualified original-work date for audiobooks; recording release dates do not qualify',
  },
  'date_precision:full_date': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without changing candidate precision',
    effect:
      'Automatic writes only that exact selected Publication date or Publication year destination value for each qualified decision',
    fallback:
      'audiobooks retain the supplied year, month or day precision without inventing a date; the operation must be qualified',
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
  'series:add_qualified': {
    admitted: 'supported series and sequence associations for this work',
    transformation: 'retains current memberships and adds missing associations',
    effect: 'extends Series only when this operation is qualified for the media',
    fallback: 'requires review when addition is unqualified or evidence is ambiguous',
  },
  'series:replace_qualified': {
    admitted: 'supported series and sequence associations for this work',
    transformation: 'uses the complete selected list',
    effect: 'replaces Series only when this operation is qualified for the media',
    fallback: 'requires review when replacement is unqualified or evidence is ambiguous',
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
    admitted: 'compatible explicit tag lists from applicable sources',
    transformation: 'audiobooks combine supported members in stable source order',
    effect: 'fills empty Tags only',
    fallback: 'abstains when Tags is populated or no list is eligible',
  },
  'tags:add': {
    admitted: 'compatible explicit tag lists from applicable sources',
    transformation: 'keeps owner tags and adds supported audiobook members in stable order',
    effect: 'extends Tags',
    fallback: 'abstains when relevant lists are malformed, incomplete or conflicting',
  },
  'tags:replace': {
    admitted: 'one exact eligible source list',
    transformation: 'uses that list without combining providers',
    effect: 'replaces Tags',
    fallback: 'abstains when no list is eligible',
  },
  'description_fill:safe_plain_text': {
    admitted:
      'safe complete text from an applicable source; catalogue placeholders are excluded from automatic ebook fills and retained for explicit choice',
    transformation:
      'uses the preferred applicable source; equal sources use stable source-record order, never text length',
    effect: 'fills an empty Description; preserves populated prose',
    fallback:
      'abstains for unsafe or incomplete source text, conflicting work identity, or unavailable qualification',
  },
  'description_fill:replace_safe_plain_text': {
    admitted: 'safe complete synopsis text from an applicable source',
    transformation:
      'keeps the original preferred source text; equal audiobook sources use stable source-record order',
    effect:
      'for audiobooks, fills an empty Description or replaces only whole chapter/track number and no-description/no-synopsis placeholders, preserving other useful prose; for ebooks, fills or replaces a Description with eligible source text',
    fallback:
      'abstains for unsafe, incomplete or conflicting evidence; the audiobook placeholder rule does not assess general writing quality',
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
    effect: 'fills an empty Cover',
    fallback: 'preserves an existing Cover',
  },
  'cover_order:replace_if_strictly_better': {
    admitted: 'confirmed audiobook covers with a known small incumbent',
    transformation: 'selects the first eligible cover in the existing confirmed order',
    effect:
      'replaces an incumbent below 300 pixels on its shorter edge with a candidate at least 500 pixels and twice as large on that edge',
    fallback:
      'preserves adequate or unknown incumbents; the candidate must have an acceptable portrait or square aspect and a qualified operation',
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
  'explicit:unsupported': {
    admitted: 'no automatic source value',
    transformation: 'does not interpret content',
    effect: 'leaves the explicit-content flag unchanged',
    fallback: 'stays off until the owner chooses an eligible representation and review mode',
  },
  'explicit:adult_product': {
    admitted: 'positive adult-product evidence for the applicable audiobook',
    transformation: 'sets only true, never false',
    effect: 'may set the flag to true after separate application consent',
    fallback:
      'preserves the current flag for false or missing evidence; this does not certify clean content',
  },
}

// The one resolution of "what this field says when the document is silent".
// The dirty comparison and the chapter badges both read it, so they cannot
// disagree about whether a draft matches the active policy.
const fieldDefaults: Readonly<Record<string, PolicyFieldDefault>> = Object.fromEntries(
  fields.map((field) => [
    field.key,
    {
      destination: field.unsupported || field.key === 'explicit' ? 'unsupported' : 'ask',
      option: field.options[0] ?? '',
    },
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
    label: 'Additional fields',
    description: 'Fields with limited support that start off until you enable them.',
  },
]

const readableCategory = (key: string) =>
  fields.find((field) => field.key === key)?.label ?? fieldLabel(key)
const previewValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') return 'empty'
  if (typeof value === 'string') return value
  if (
    typeof value === 'object' &&
    Object.keys(value).length === 2 &&
    'title' in value &&
    'subtitle' in value &&
    typeof value.title === 'string' &&
    typeof value.subtitle === 'string'
  )
    return `Title: ${value.title}; Subtitle: ${value.subtitle}`
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
  readonly title:
    | 'Change'
    | 'Needs review'
    | 'No change'
    | 'No supported value found'
    | 'Policy change'
    | 'No policy change'
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
  if (reason === 'explicit_unsupported')
    return 'this policy leaves the explicit-content flag unchanged'
  if (reason === 'source_value_absent') return 'the source supplied no ISBN'
  if (reason === 'source_unavailable') return 'the ISBN source could not be reached'
  if (reason === 'source_value_malformed_or_incomplete')
    return 'source ISBN data is malformed or incomplete'
  if (reason === 'source_values_conflict') return 'sources supply competing ISBNs'
  if (reason === 'source_value_insufficient_association')
    return 'the supplied ISBN is not sufficiently linked to this recording'
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
  if (field.outcome === 'abstain')
    return {
      changed: false,
      title: field.reason === 'ambiguous_evidence' ? 'Needs review' : 'No change',
      detail:
        field.reason === 'policy_ignored'
          ? `remains ${current} — ${humanReason(field.reason)}`
          : (automaticRefusalReasons[field.reason ?? ''] ??
            'The preview did not select an automatic action; no more specific reason is available.'),
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
const previewFieldShape = (value: unknown): value is PolicyPreviewField => {
  if (typeof value !== 'object' || value === null) return false
  return (
    'field' in value &&
    typeof value.field === 'string' &&
    'outcome' in value &&
    typeof value.outcome === 'string' &&
    'reason' in value &&
    (value.reason === null || typeof value.reason === 'string') &&
    'target_state' in value &&
    typeof value.target_state === 'string' &&
    'current_value' in value &&
    'desired_value' in value
  )
}
const isPolicyPreview = (value: unknown): value is PolicyPreviewView =>
  typeof value === 'object' &&
  value !== null &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-preview-v1' &&
  'active_policy_id' in value &&
  typeof value.active_policy_id === 'string' &&
  'active_generation' in value &&
  typeof value.active_generation === 'number' &&
  Number.isSafeInteger(value.active_generation) &&
  value.active_generation > 0 &&
  'libraries' in value &&
  Array.isArray(value.libraries) &&
  value.libraries.every((library: unknown) => {
    if (
      typeof library !== 'object' ||
      library === null ||
      !('cases' in library) ||
      !Array.isArray(library.cases) ||
      !('comparisons' in library) ||
      !Array.isArray(library.comparisons) ||
      library.comparisons.length !== library.cases.length
    )
      return false
    return library.comparisons.every(
      (comparison: unknown) =>
        typeof comparison === 'object' &&
        comparison !== null &&
        'media_kind' in comparison &&
        ['ebook', 'audiobook'].includes(String(comparison.media_kind)) &&
        'fields' in comparison &&
        Array.isArray(comparison.fields) &&
        comparison.fields.every(
          (field: unknown) =>
            typeof field === 'object' &&
            field !== null &&
            'active' in field &&
            previewFieldShape(field.active) &&
            'draft' in field &&
            previewFieldShape(field.draft) &&
            field.active.field === field.draft.field &&
            'changed' in field &&
            typeof field.changed === 'boolean' &&
            'active_destination' in field &&
            ['ask', 'automatic', 'ignore', 'unsupported'].includes(
              String(field.active_destination),
            ) &&
            'draft_destination' in field &&
            ['ask', 'automatic', 'ignore', 'unsupported'].includes(String(field.draft_destination)),
        ),
    )
  })
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
    readonly identity?: string
    readonly automatic_available: boolean
    readonly field: string
    readonly option: string
  }[],
) => {
  const entries = qualification.filter(
    (entry) => entry.field === field.key && entry.option === option,
  )
  return entries.some((entry) => entry.automatic_available)
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
  const [expandedPreview, setExpandedPreview] = useState<PolicyPreviewView>()
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
    queryFn: (queryContext) =>
      http.getShadowSample(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 30_000,
    retry: false,
  })
  const transition = useQuery({
    queryKey: ['policy', 'transition'],
    queryFn: (queryContext) =>
      http.getPolicyTransition(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 1_000,
    retry: false,
    refetchInterval: (query) => (query.state.data?.state === 'processing' ? 1_500 : false),
  })
  const refresh = async () => {
    await Promise.all([
      invalidateBackgroundQueries(queryClient, { queryKey: ['policy'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
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
    onSettled: async (_data, _error, { action }) => {
      await Promise.all([
        refresh(),
        ...(['automatic-safety/recover', 'automatic-safety/pause'].includes(action)
          ? [invalidateBackgroundQueries(queryClient, { queryKey: ['review-preparation'] })]
          : []),
      ])
    },
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
  const safetySummaryRef = useRef<HTMLElement>(null)
  const runAuxiliary = async (action: string, body?: unknown) => {
    setStatus({ tone: 'neutral', message: 'Updating policy state…' })
    try {
      await command.mutateAsync({ action, body })
      setStatus({
        tone: 'success',
        message:
          action === 'automatic-safety/recover'
            ? 'Suspension recovered. Title work resumes when its category suspension, automatic pause and global suspension are all cleared.'
            : 'Policy state updated from the authoritative server.',
      })
      if (action === 'automatic-safety/recover') safetySummaryRef.current?.focus()
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
        body: {
          policy_id: snapshotId,
          expected_active_policy_id: previewActivePolicyId,
          expected_active_generation: preview.active_generation,
        },
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
  const qualification = policyAutomaticQualification(data.state)
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
  const previewRows = useMemo(
    () =>
      preview?.libraries
        .flatMap((library) =>
          library.cases.flatMap((item, caseIndex) => {
            const comparison = library.comparisons[caseIndex]
            if (!comparison) return []
            return comparison.fields.map((field, fieldIndex) => {
              const projected = policyImpactDisplay(field.draft)
              const before = policyImpactDisplay(field.active)
              return {
                caseId: item.report_id,
                order: fieldIndex,
                id: `${item.report_id}:${field.draft.field}`,
                library: library.library,
                media: comparison.media_kind,
                work: item.work_label,
                field: readableCategory(field.draft.field),
                projected,
                impact: {
                  changed: field.changed,
                  title: field.changed ? ('Policy change' as const) : ('No policy change' as const),
                  detail: field.changed
                    ? `${field.active_destination === 'ask' ? 'Ask me' : field.active_destination}: ${before.detail} → ${field.draft_destination === 'ask' ? 'Ask me' : field.draft_destination}: ${projected.detail}`
                    : projected.detail,
                  technicalDetail: `Active ${before.technicalDetail}; draft ${projected.technicalDetail}`,
                },
              }
            })
          }),
        )
        .sort((a, b) => a.order - b.order) ?? [],
    [preview],
  )
  const changedRows = previewRows.filter((row) => row.impact.changed)
  const unchangedRows = previewRows.filter((row) => !row.impact.changed)
  const mediaQualification = (
    field: FieldDefinition,
    option: string,
    media: 'ebook' | 'audiobook',
  ) => {
    const entries = (
      media === 'ebook' ? data.state.rich_qualification : data.state.audiobook_qualification
    ).filter((entry) => entry.field === field.key && entry.option === option)
    const available = entries.filter((entry) => entry.automatic_available)
    if (!available.length) return 'No automatic effect — owner review required'
    if (media === 'ebook' && field.key === 'cover_order')
      return option === 'replace_if_strictly_better'
        ? 'Fill empty only — confirmed covers are preserved'
        : 'Fill empty or replace only with a confirmed strict-rank upgrade'
    if (media === 'audiobook' && field.key === 'cover_order')
      return 'Replace a small incumbent only with a materially larger confirmed cover'
    const grouped = new Map<string, string[]>()
    for (const entry of available) {
      if ('operation_scope' in entry)
        return entry.operation_scope === 'fill'
          ? 'Fill empty only'
          : fieldLabel(entry.operation_scope)
      const operations = grouped.get(entry.destination) ?? []
      operations.push(fieldLabel(entry.operation).toLowerCase())
      grouped.set(entry.destination, operations)
    }
    const operations = [...grouped]
      .map(([destination, values]) => `${fieldLabel(destination)}: ${values.join(', ')}`)
      .join(' · ')
    return media === 'audiobook' && field.key === 'contributor_names' && option !== 'canonical'
      ? `${operations} · Narrators: no automatic effect`
      : operations
  }

  const mediaBehavior = (field: FieldDefinition, option: string, media: 'ebook' | 'audiobook') => {
    const destination =
      draft.destinations[field.key] ?? fieldDefaults[field.key]?.destination ?? 'ask'
    const eligibility = mediaQualification(field, option, media)
    if (destination === 'automatic') return `Automatic — ${eligibility}`
    const selected =
      destination === 'ignore'
        ? 'Ignored — no metadata action'
        : destination === 'ask'
          ? 'Ask me — owner choice required'
          : 'Unavailable — no metadata action'
    return `${selected}. Automatic eligibility if selected: ${eligibility}`
  }

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
  const policyStatusRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (!['error', 'warning'].includes(status.tone)) return
    policyStatusRef.current?.focus({ preventScroll: true })
    policyStatusRef.current?.scrollIntoView({ block: 'nearest' })
  }, [status])
  const activationReviewRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (stage !== 'activation') return
    activationReviewRef.current?.focus({ preventScroll: true })
    activationReviewRef.current?.scrollIntoView({ block: 'center' })
  }, [stage])
  const transitionCopy = activationNotice
    ? activationNotice.state === 'processing'
      ? `Applying updated policy · ${String(activationNotice.completed)} of ${String(activationNotice.total)}`
      : `${activationNotice.failed ? 'Completed with failures' : 'Policy update complete'} · ${String(activationNotice.completed)} of ${String(activationNotice.total)}`
    : undefined

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const descriptionLink =
        activeChapter === 'curation' && window.location.hash === '#description-advice'
      const target = document.getElementById(
        descriptionLink ? 'description-advice-heading' : `policy-${activeChapter}`,
      )
      target?.focus({ preventScroll: true })
      if (descriptionLink) target?.scrollIntoView({ block: 'start' })
    })
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
            <small>{baselinePolicy ? 'Active baseline' : 'Active policy'}</small>
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
                  <ChangeCount id={`policy-${chapter.key}-changes`} count={changes} />
                </button>
              )
            })}
          </nav>
          <p className="cm-policy-chapter-legend">Changed fields</p>
          <details className="cm-policy-mode">
            <summary ref={safetySummaryRef}>
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
              {safety.category_suspensions.title_layout ? (
                <p>
                  Eligible completed books are re-evaluated from retained evidence once the title
                  suspension, automatic pause and global suspension are all cleared. A supported
                  unchanged title records no change; recovery does not write to your library.
                  Unrelated category suspensions remain in place.
                </p>
              ) : null}
              {Object.keys(safety.category_suspensions).map((category) => (
                <button
                  key={category}
                  disabled={command.isPending}
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
                draft.destinations[field.key] ?? fieldDefaults[field.key]?.destination ?? 'ask'
              const qualified = automaticQualified(field, option, qualification)
              const automaticAvailable = qualified && automaticAllowed
              const changed = fieldChanged(field)
              return [
                <section
                  key={field.key}
                  className={`cm-policy-field ${changed ? 'is-changed' : ''}`}
                  aria-label={field.label}
                >
                  <header>
                    <span>
                      <strong>{field.label}</strong>
                      {changed ? <small>Draft changed</small> : null}
                    </span>
                    <p>{field.meaning}</p>
                    <p className="cm-policy-media-scope">
                      <strong>Ebooks:</strong> {mediaBehavior(field, option, 'ebook')}
                      <br />
                      <strong>Audiobooks:</strong> {mediaBehavior(field, option, 'audiobook')}
                    </p>
                    {data.state.audiobook_qualification.some(
                      (entry) => entry.field === field.key && entry.option === option,
                    ) ? (
                      <Disclosure summary="Audiobook operations">
                        <ul>
                          {data.state.audiobook_qualification
                            .filter((entry) => entry.field === field.key && entry.option === option)
                            .map((entry) => (
                              <li key={entry.identity} data-qualification-identity={entry.identity}>
                                {fieldLabel(entry.destination)}:{' '}
                                {audiobookOperationLabels[entry.operation]} —{' '}
                                {entry.automatic_available
                                  ? 'Automatic available'
                                  : 'Review required'}
                              </li>
                            ))}
                        </ul>
                      </Disclosure>
                    ) : null}
                    <Disclosure summary="What this changes">
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
                    </Disclosure>
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
                          {destination === 'unsupported' || !automaticAvailable ? (
                            <small>
                              {[
                                destination === 'unsupported'
                                  ? 'Off until you choose a review mode'
                                  : null,
                                automaticAvailable ? null : 'Automatic not supported',
                              ]
                                .filter(Boolean)
                                .join(' · ')}
                            </small>
                          ) : null}
                        </span>
                        <SegmentedControl
                          label={`${field.label} review mode`}
                          value={destination}
                          disabled={command.isPending}
                          options={[
                            {
                              value: 'automatic',
                              label: 'Automatic',
                              disabled: !automaticAvailable,
                            },
                            { value: 'ask', label: 'Ask me' },
                            { value: 'ignore', label: 'Ignore' },
                          ]}
                          onChange={(next) =>
                            editDraft({
                              ...draft,
                              destinations: { ...draft.destinations, [field.key]: next },
                            })
                          }
                        />
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
                              !automaticQualified(field, nextOption, qualification)
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
                </section>,
                field.key === 'cover_order' ? (
                  <DescriptionAdviceSettings key="description-advice" http={http} />
                ) : null,
              ]
            })}
          </div>
        </section>
        {(stage === 'preview' || stage === 'activation') && preview ? (
          <section className="cm-policy-result">
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
              <p>
                {preview.libraries.reduce((count, library) => count + library.cases.length, 0)}{' '}
                sampled works
                {' · '}
                {impactTotals?.currentWorks} total current unapplied works
                {' · '}up to 20 works across all libraries
                {preview.libraries.some((library) => library.cases_truncated)
                  ? ' · Additional works were not previewed'
                  : ''}
              </p>
              <p>
                {(['ebook', 'audiobook'] as const)
                  .map((media) => {
                    const rows = changedRows.filter((row) => row.media === media)
                    const works = preview.libraries.reduce(
                      (count, library) =>
                        count +
                        library.comparisons.filter((comparison) => comparison.media_kind === media)
                          .length,
                      0,
                    )
                    return `${media === 'ebook' ? 'Ebooks' : 'Audiobooks'}: ${String(works)} sampled ${works === 1 ? 'work' : 'works'} · ${String(rows.length)} changed ${rows.length === 1 ? 'field' : 'fields'}`
                  })
                  .join(' · ')}
              </p>
              <p>
                Title, authors, narrators, date, publisher, language and cover are not previewed
                here.
              </p>
              <div id="policy-preview-outcomes" className="cm-policy-preview-list">
                {changedRows.length
                  ? changedRows.slice(0, expandedPreview === preview ? undefined : 6).map((row) => (
                      <div key={row.id} className={row.impact.changed ? 'is-change' : ''}>
                        <span className="cm-policy-work-mark" aria-hidden="true">
                          {row.work.slice(0, 2).toUpperCase()}
                        </span>
                        <span>
                          <strong>{row.work}</strong>
                          <small>
                            {row.media === 'ebook' ? 'Ebook' : 'Audiobook'} · {row.library} ·{' '}
                            {row.field}
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
                          <b>No policy-caused changes in this sample</b>
                          <small>
                            ·{' '}
                            {library.current_unapplied_count === 0
                              ? 'No current unapplied work remains to inspect'
                              : `${String(library.current_unapplied_count)} current unapplied ${library.current_unapplied_count === 1 ? 'work remains' : 'works remain'}; unchanged outcomes are separate context`}
                          </small>
                        </span>
                      </div>
                    ))}
              </div>
              {changedRows.length > 6 ? (
                <footer>
                  <button
                    type="button"
                    aria-expanded={expandedPreview === preview}
                    aria-controls="policy-preview-outcomes"
                    onClick={() =>
                      setExpandedPreview(expandedPreview === preview ? undefined : preview)
                    }
                  >
                    {expandedPreview === preview
                      ? 'Show fewer outcomes'
                      : `Show ${String(changedRows.length - 6)} more ${changedRows.length === 7 ? 'outcome' : 'outcomes'}`}
                  </button>
                </footer>
              ) : preview.libraries.some((library) => library.cases_truncated) ? (
                <footer>
                  Sample limited to 20 works across all libraries; additional current works were not
                  previewed.
                </footer>
              ) : null}
            </div>
            {unchangedRows.length ? (
              <details className="cm-policy-projected-context">
                <summary>
                  Unchanged outcomes and projected library context ({unchangedRows.length})
                </summary>
                <p>
                  These compare the draft with retained library values. They do not count as effects
                  of editing the policy.
                </p>
                {unchangedRows.map((row) => (
                  <p key={row.id}>
                    <strong>
                      {row.work} · {row.media} · {row.field}:
                    </strong>{' '}
                    {row.impact.title}. {row.projected.detail}
                  </p>
                ))}
              </details>
            ) : null}
            {stage === 'activation' ? (
              <footer
                className="cm-policy-activation"
                ref={activationReviewRef}
                tabIndex={-1}
                aria-label="Review policy activation"
              >
                <strong>Activate this reviewed draft?</strong>
                <span>
                  The active policy changes only after this guarded command succeeds. Applied
                  history remains untouched. No write permission is granted.
                </span>
                <ul>
                  {changedFields.map((field) => (
                    <li key={field.key}>
                      <strong>{field.label}:</strong> Ebooks —{' '}
                      {mediaBehavior(field, draft.options[field.key] ?? '', 'ebook')}; Audiobooks —{' '}
                      {mediaBehavior(field, draft.options[field.key] ?? '', 'audiobook')}
                    </li>
                  ))}
                </ul>
              </footer>
            ) : null}
          </section>
        ) : null}
        <FinishTray
          label="Whole policy draft impact and activation"
          className={`stage-${stage}`}
          ref={trayRef}
          summary={
            <>
              {['error', 'warning'].includes(status.tone) ? null : (
                <>
                  <strong>{finishTitle}</strong>
                  <span>{finishDetail}</span>
                </>
              )}
              <p
                className={`policy-status ${status.tone} ${['error', 'warning'].includes(status.tone) ? '' : 'sr-only'}`}
                ref={policyStatusRef}
                tabIndex={-1}
                role={status.tone === 'error' ? 'alert' : 'status'}
              >
                {status.message}
              </p>
            </>
          }
          progress={
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
                    aria-current={
                      stage !== 'activated' && currentStep === index ? 'step' : undefined
                    }
                  >
                    <span>{done ? <Check /> : index + 1}</span>
                    <small>{label}</small>
                  </li>
                )
              })}
            </ol>
          }
          actions={
            <>
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
            </>
          }
        >
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
        </FinishTray>
      </div>
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
