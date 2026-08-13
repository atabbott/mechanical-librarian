import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'

import { AmbiguousMutationError, ApiError, BuildMismatchError, derivePolicyDraftDisplay } from '../api/http'
import type { PolicyActivationView, PolicyDraft, PolicyPreviewView, PolicySection, PolicyTransitionView, ShadowPostureView } from '../api/http'
import { Dropdown } from '../components/Dropdown'
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

type PolicyStage = 'editing' | 'validating' | 'preview' | 'activation' | 'activated'

const fields: readonly FieldDefinition[] = [
  { key: 'title_layout', label: 'Title and subtitle', meaning: 'Keep a title and subtitle together as one coherent identity.', options: ['preserve', 'title_and_subtitle'], optionLabels: { preserve: 'Preserve current layout', title_and_subtitle: 'Separate title and subtitle' }, chapter: 'identity', automatic: true },
  { key: 'contributor_names', label: 'Authors and narrators', meaning: 'Preserve contributor order while choosing how supported names appear.', options: ['preserve', 'canonical'], optionLabels: { preserve: 'Preserve supported form', canonical: 'Use canonical names' }, chapter: 'identity', automatic: true },
  { key: 'language_rendering', label: 'Language', meaning: 'Keep canonical language identity while choosing its readable form.', options: ['preserve', 'display_name'], optionLabels: { preserve: 'Preserve supported form', display_name: 'Use language display name' }, chapter: 'identity', automatic: true },
  { key: 'subtitle', label: 'Subtitle', meaning: 'Fill an empty subtitle or use a corroborated title and subtitle pair.', options: ['fill_empty', 'corroborated_split'], optionLabels: { fill_empty: 'Fill only when empty', corroborated_split: 'Use corroborated split' }, chapter: 'identity', automatic: false },
  { key: 'date_precision', label: 'Publication date and year', meaning: 'The date and matching year move together as one edition fact.', options: ['year', 'full_date'], optionLabels: { year: 'Year only', full_date: 'Full date with matching year' }, chapter: 'publication', automatic: true },
  { key: 'publisher_preference', label: 'Publisher', meaning: 'Choose the supported publisher level for the same manifestation.', options: ['publisher', 'imprint'], optionLabels: { publisher: 'Prefer publisher', imprint: 'Prefer imprint' }, chapter: 'publication', automatic: true },
  { key: 'isbn_form', label: 'ISBN', meaning: 'Preserve exact ISBN data or choose an admitted ISBN-13 representation.', options: ['preserve_exact', 'prefer_isbn13'], optionLabels: { preserve_exact: 'Preserve exact ISBN', prefer_isbn13: 'Prefer ISBN-13' }, chapter: 'publication', automatic: false },
  { key: 'series', label: 'Series memberships', meaning: 'Keep qualified memberships while projecting one supported value.', options: ['primary_narrative', 'source_order', 'all_qualified'], optionLabels: { primary_narrative: 'Primary narrative', source_order: 'First source membership', all_qualified: 'All qualified memberships' }, chapter: 'publication', automatic: false },
  { key: 'genres', label: 'Genres', meaning: 'Use one exact eligible source list.', options: ['fill_empty', 'add', 'replace'], optionLabels: { fill_empty: 'Fill only when empty', add: 'Add missing values', replace: 'Replace with source list' }, chapter: 'curation', automatic: false },
  { key: 'tags', label: 'Tags', meaning: 'Tags are off by default and remain manual review choices.', options: ['off', 'fill_empty', 'add', 'replace'], optionLabels: { off: 'Off', fill_empty: 'Fill only when empty', add: 'Add missing values', replace: 'Replace with source list' }, chapter: 'curation', automatic: false },
  { key: 'description_fill', label: 'Description', meaning: 'Use only admitted bounded plain text or visible source text.', options: ['safe_plain_text', 'google_visible_text'], optionLabels: { safe_plain_text: 'Safe plain text', google_visible_text: 'Visible Google text' }, chapter: 'curation', automatic: false },
  { key: 'cover_order', label: 'Cover', meaning: 'Cover review uses the fixed supported source order.', options: ['fixed_source_order'], optionLabels: { fixed_source_order: 'Fixed supported source order' }, chapter: 'curation', automatic: false },
  { key: 'asin', label: 'ASIN', meaning: 'Preserve exact ASIN data or select an admitted ebook manifestation.', options: ['preserve_exact', 'ebook_manifestation'], optionLabels: { preserve_exact: 'Preserve exact ASIN', ebook_manifestation: 'Ebook manifestation' }, chapter: 'curation', automatic: false },
  { key: 'explicit', label: 'Explicit-content flag', meaning: 'This destination is visible but is not currently supported.', options: ['unsupported'], optionLabels: { unsupported: 'Not currently supported' }, chapter: 'unsupported', automatic: false, unsupported: true },
]

const chapters: readonly { key: PolicySection; label: string; description: string }[] = [
  { key: 'identity', label: 'Identity', description: 'How a work names itself and its contributors.' },
  { key: 'publication', label: 'Publication', description: 'How edition facts and memberships are represented.' },
  { key: 'curation', label: 'Curation', description: 'Fields that remain intentionally owner-directed.' },
  { key: 'unsupported', label: 'Unsupported', description: 'Visible limitations without inert policy controls.' },
]

const readableCategory = (key: string) => fields.find((field) => field.key === key)?.label ?? key.replaceAll('_', ' ')
const previewValue = (value: unknown) => value === null || value === undefined ? 'No value' : typeof value === 'string' ? value : JSON.stringify(value)
const isPolicyPreview = (value: unknown): value is PolicyPreviewView => typeof value === 'object' && value !== null && 'schema_version' in value && value.schema_version === 'metadata-policy-preview-v1' && 'active_policy_id' in value && typeof value.active_policy_id === 'string' && 'libraries' in value && Array.isArray(value.libraries)
const isPolicyTransition = (value: unknown): value is PolicyTransitionView => typeof value === 'object' && value !== null
  && 'schema_version' in value && value.schema_version === 'metadata-policy-transition-v1'
  && 'transition_id' in value && typeof value.transition_id === 'string'
  && 'from_policy_id' in value && typeof value.from_policy_id === 'string'
  && 'to_policy_id' in value && typeof value.to_policy_id === 'string'
  && 'state' in value && typeof value.state === 'string'
  && 'generation' in value && typeof value.generation === 'number'
  && 'completed' in value && typeof value.completed === 'number'
  && 'failed' in value && typeof value.failed === 'number'
  && 'total' in value && typeof value.total === 'number'
  && 'acknowledged' in value && typeof value.acknowledged === 'boolean'
const isPolicyActivation = (value: unknown, policyId: string): value is PolicyActivationView => typeof value === 'object' && value !== null && 'policy_id' in value && value.policy_id === policyId && 'schema_version' in value && value.schema_version === 'metadata-policy-activation-v1' && 'transition' in value && (value.transition === null || isPolicyTransition(value.transition))
const automaticQualified = (field: FieldDefinition, option: string, qualification: readonly { readonly automatic_available: boolean; readonly field: string; readonly option: string }[]) => {
  const rich = qualification.find((entry) => entry.field === field.key && entry.option === option)
  return rich ? rich.automatic_available : field.automatic && !(field.key === 'publisher_preference' && option === 'imprint')
}
const qualificationExplanation = (field: FieldDefinition, option: string, qualification: readonly { readonly automatic_available: boolean; readonly disposition: string; readonly field: string; readonly option: string }[]) => {
  const rich = qualification.find((entry) => entry.field === field.key && entry.option === option)
  if (!rich || rich.automatic_available) return undefined
  if (rich.disposition === 'policy_only') return 'This policy option is intentionally off.'
  if (rich.disposition === 'passing_human_only') return 'This field remains unsupported and has no policy choice.'
  if (rich.disposition === 'not_qualified') return 'This representation did not meet the Automatic qualification gate.'
  return 'This representation has insufficient Automatic qualification coverage.'
}

const Check = () => <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 7" /></svg>

export default function PolicyPage({ data }: { data: PolicyCritical }) {
  const { http, queryClient } = useReviewContext()
  const search = policyRoute.useSearch()
  const navigate = useNavigate({ from: '/policy' })
  const activeChapter = search.section
  const [draft, setDraft] = useState<PolicyDraft>(() => ({ name: data.state.profile.name, options: { ...data.state.profile.options }, destinations: { ...data.state.profile.destinations } }))
  const [snapshotId, setSnapshotId] = useState<string>()
  const [previewActivePolicyId, setPreviewActivePolicyId] = useState<string>()
  const [preview, setPreview] = useState<PolicyPreviewView>()
  const [stage, setStage] = useState<PolicyStage>('editing')
  const [status, setStatus] = useState<{ tone: 'neutral' | 'success' | 'error' | 'warning'; message: string }>({ tone: 'neutral', message: 'Draft is separate from the active policy.' })
  const shadow = data.shadow
  const safety = data.safety
  const sample = useQuery({ queryKey: ['policy', 'shadow', 'sample'], queryFn: ({ signal }) => http.getShadowSample(signal), staleTime: 30_000, retry: false })
  const transition = useQuery({ queryKey: ['policy', 'transition'], queryFn: ({ signal }) => http.getPolicyTransition(signal), staleTime: 1_000, retry: false, refetchInterval: (query) => query.state.data?.state === 'processing' ? 1_500 : false })
  const refresh = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['policy'] }), queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] })]) }
  const command = useMutation({ mutationFn: async ({ action, body }: { action: string; body?: unknown }) => {
    if (action === 'validate' || action === 'drafts') return http.policyCommand<{ policy_id: string; schema_version: string }>(action, body)
    if (action === 'preview') return http.policyCommand<PolicyPreviewView>('preview', body)
    if (action === 'activate') return http.policyCommand<PolicyActivationView>('activate', body)
    return http.policyCommand<PolicyTransitionView | ShadowPostureView>(action, body)
  }, retry: false, onSettled: refresh })

  const editDraft = (next: PolicyDraft) => {
    setDraft(next)
    setSnapshotId(undefined)
    setPreviewActivePolicyId(undefined)
    setPreview(undefined)
    setStage('editing')
    setStatus({ tone: 'neutral', message: 'Draft has unreviewed changes.' })
  }
  const runAuxiliary = async (action: string, body?: unknown) => {
    setStatus({ tone: 'neutral', message: 'Updating policy state…' })
    try {
      await command.mutateAsync({ action, body })
      setStatus({ tone: 'success', message: 'Policy state updated from the authoritative server.' })
    } catch (error) {
      setStatus(error instanceof AmbiguousMutationError
        ? { tone: 'warning', message: 'The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.' }
        : { tone: 'error', message: 'Nothing changed. Review the current server state and try again.' })
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
      const saved = await command.mutateAsync({ action: 'drafts', body: draft }) as { policy_id: string }
      setSnapshotId(saved.policy_id)
      phase = 'preview'
      setStatus({ tone: 'neutral', message: 'Snapshot saved. Inspecting current unapplied work…' })
      const result = await command.mutateAsync({ action: 'preview', body: draft })
      if (!isPolicyPreview(result) || result.policy_id !== saved.policy_id) throw new ApiError(502, 'policy_snapshot_mismatch')
      setPreview(result)
      setPreviewActivePolicyId(result.active_policy_id)
      setStage('preview')
      setStatus({ tone: 'success', message: 'Impact preview is current. The active policy is unchanged.' })
    } catch (error) {
      setStage('editing')
      if (error instanceof AmbiguousMutationError) setStatus({ tone: 'warning', message: 'The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.' })
      else if (error instanceof ApiError && error.status === 422) setStatus({ tone: 'error', message: 'The draft is not valid. Review its field choices and try again.' })
      else if (phase === 'preview') setStatus({ tone: 'error', message: 'The immutable snapshot was saved, but its no-write impact preview is unavailable. Retry when the current state is visible.' })
      else if (phase === 'drafts') setStatus({ tone: 'error', message: 'Validation passed, but the immutable snapshot was not saved. Nothing became active; try again.' })
      else setStatus({ tone: 'error', message: 'The complete draft could not be validated. Nothing was saved or activated; try again.' })
    }
  }
  const activate = async () => {
    if (!snapshotId || !preview || !previewActivePolicyId) return
    setStatus({ tone: 'neutral', message: 'Activating the reviewed policy…' })
    try {
      const result = await command.mutateAsync({ action: 'activate', body: { policy_id: snapshotId, expected_active_policy_id: previewActivePolicyId } })
      if (!isPolicyActivation(result, snapshotId)) throw new ApiError(502, 'invalid_policy_activation_response')
      setStage('activated')
      setStatus({ tone: 'success', message: 'Policy activated. Current unapplied work is being reconciled.' })
    } catch (error) {
      if (error instanceof AmbiguousMutationError || error instanceof BuildMismatchError || (error instanceof ApiError && error.status >= 500)) {
        setStage('preview')
        setStatus({ tone: 'warning', message: 'The activation result is uncertain. Authoritative policy state is refreshing; verify it before trying again.' })
      } else if (error instanceof ApiError && error.status === 409 && error.code === 'policy_activation_is_stale') {
        setSnapshotId(undefined)
        setPreviewActivePolicyId(undefined)
        setPreview(undefined)
        setStage('editing')
        setStatus({ tone: 'warning', message: 'The active policy changed before activation. Review the refreshed policy and create a new draft.' })
      } else if (error instanceof ApiError && error.status === 409 && error.code === 'policy_transition_is_processing') {
        setStage('preview')
        setStatus({ tone: 'warning', message: 'The previous policy update is still processing. This reviewed snapshot remains available; retry after the transition is resolved.' })
      } else {
        setStatus({ tone: 'error', message: 'Activation failed safely. The reviewed snapshot remains available to retry.' })
      }
    }
  }
  const openChapter = async (section: PolicySection) => {
    await navigate({ search: { section }, replace: true })
    requestAnimationFrame(() => document.getElementById(`policy-${section}`)?.focus({ preventScroll: true }))
  }
  const automaticAllowed = shadow.posture === 'preview' || shadow.posture === 'automatic'
  const draftDisplay = derivePolicyDraftDisplay(data.state, draft)
  const fieldChanged = (field: FieldDefinition) => {
    const activeOption = data.state.profile.options[field.key] ?? field.options[0] ?? ''
    const activeDestination = data.state.profile.destinations[field.key] ?? (field.unsupported ? 'unsupported' : 'ask')
    const option = draft.options[field.key] ?? field.options[0] ?? ''
    const destination = draft.destinations[field.key] ?? (field.unsupported ? 'unsupported' : 'ask')
    return activeOption !== option || activeDestination !== destination
  }
  const changedFields = fields.filter(fieldChanged)
  const currentChapter = chapters.find((chapter) => chapter.key === activeChapter) ?? { key: 'identity' as const, label: 'Identity', description: 'How a work names itself and its contributors.' }
  const visibleFields = fields.filter((field) => field.chapter === activeChapter)
  const changedChapterLabels = chapters.filter((chapter) => changedFields.some((field) => field.chapter === chapter.key)).map((chapter) => chapter.label)
  const impactTotals = useMemo(() => preview ? { libraries: preview.libraries.length, items: preview.libraries.reduce((total, item) => total + item.current_unapplied_count, 0) } : undefined, [preview])
  const previewRows = useMemo(() => {
    const cases = preview?.libraries.flatMap((library) => library.cases.map((rawCase, caseIndex) => {
    const item = rawCase as unknown as { readonly fields: readonly { readonly current_value: unknown; readonly desired_value: unknown; readonly field: string; readonly outcome: string; readonly reason: string | null; readonly target_state: string }[]; readonly report_id: string; readonly work_label: string }
    return item.fields.map((field) => ({ id: `${item.report_id || String(caseIndex)}:${field.field}`, library: library.library, work: item.work_label, field: readableCategory(field.field), outcome: `${previewValue(field.current_value === null ? field.target_state : field.current_value)} → ${field.desired_value === null ? (field.reason ?? field.outcome) : previewValue(field.desired_value)}` }))
    })) ?? []
    return [...cases.flatMap((rows) => rows.slice(0, 1)), ...cases.flatMap((rows) => rows.slice(1))]
  }, [preview])
  const transitionCopy = transition.data && !transition.data.acknowledged
    ? transition.data.state === 'processing'
      ? `Applying updated policy · ${String(transition.data.completed)} of ${String(transition.data.total)}`
      : `${transition.data.failed ? 'Completed with failures' : 'Policy update complete'} · ${String(transition.data.completed)} of ${String(transition.data.total)}`
    : undefined

  useEffect(() => {
    const frame = requestAnimationFrame(() => document.getElementById(`policy-${activeChapter}`)?.focus({ preventScroll: true }))
    return () => cancelAnimationFrame(frame)
  }, [activeChapter])

  const completedValidate = stage === 'preview' || stage === 'activation' || stage === 'activated'
  const completedPreview = stage === 'activation' || stage === 'activated'
  const currentStep = stage === 'editing' || stage === 'validating' ? 0 : stage === 'preview' ? 1 : 2
  const finishTitle = stage === 'activated' ? 'Policy activated' : stage === 'activation' ? 'Ready to activate' : stage === 'preview' ? `${String(impactTotals?.items ?? 0)} current unapplied ${(impactTotals?.items ?? 0) === 1 ? 'item' : 'items'} found` : stage === 'validating' ? 'Validating the working draft…' : draftDisplay.changed ? `${String(changedFields.length)} draft ${changedFields.length === 1 ? 'change is' : 'changes are'} ready to validate` : 'Draft matches the active policy'
  const finishDetail = stage === 'activated' ? `${String(impactTotals?.items ?? 0)} items are queued for guarded reconciliation. Applied history is untouched.` : stage === 'activation' ? 'Review the unchanged preview before making this draft active.' : stage === 'preview' ? `Validated across all chapters${changedChapterLabels.length ? ` · changes in ${changedChapterLabels.join(', ')}` : ''} · snapshot saved automatically.` : stage === 'validating' ? 'Checking every chapter, Automatic qualification, and exact library scope.' : draftDisplay.changed ? `Changes in ${changedChapterLabels.join(', ')} · validation always checks and saves the complete draft.` : 'Every chapter is included. Change a field mode or representation to create a new draft.'

  return <div className="cm-policy-route" data-route-focus tabIndex={-1}>
    <header className="cm-page-heading"><div><h1>Library policy</h1><p>Choose how supported metadata is represented and when Mechanical Librarian should ask for your judgement.</p></div><span className="cm-policy-state">{stage === 'activated' ? 'Active policy updated' : draftDisplay.changed ? `${String(changedFields.length)} draft ${changedFields.length === 1 ? 'change' : 'changes'}` : 'Draft matches active policy'}</span></header>
    <div className="cm-policy-editor">
      <aside className="cm-policy-sidebar">
        <header><span>Active policy</span><strong>{data.state.profile.name}</strong><small>{draftDisplay.changed ? 'Draft has unreviewed changes' : 'Draft matches active'}</small></header>
        {transitionCopy ? <p className="cm-policy-transition" role="status">{transitionCopy}</p> : null}
        <nav aria-label="Policy chapters">{chapters.map((chapter) => {
          const chapterFields = fields.filter((field) => field.chapter === chapter.key)
          const changes = chapterFields.filter(fieldChanged).length
          return <button key={chapter.key} type="button" aria-current={activeChapter === chapter.key ? 'page' : undefined} onClick={() => void openChapter(chapter.key)}><span><strong>{chapter.label}</strong><small>{chapter.description}</small></span><b>{changes || chapterFields.length}</b></button>
        })}</nav>
        <details className="cm-policy-mode"><summary><strong>{shadow.posture === 'preview' ? 'Preview mode active' : shadow.posture === 'automatic' ? 'Qualified Automatic active' : 'Human review active'}</strong><span>Safety</span></summary><div><p>{shadow.posture === 'preview' ? 'Outcomes remain non-authoritative.' : shadow.posture === 'automatic' ? 'Automatic remains decision-only and separately controlled.' : 'Enter Preview before enabling qualified Automatic.'}</p><div className="cm-policy-safety-actions"><button disabled={command.isPending || shadow.posture === 'preview'} onClick={() => void runAuxiliary('shadow/enter')}>Enter Preview mode</button><button disabled={command.isPending || shadow.posture !== 'preview'} onClick={() => void runAuxiliary('shadow/confirm-automatic')}>Activate qualified Automatic</button><button disabled={command.isPending} onClick={() => void runAuxiliary('automatic-safety/pause', { paused: !safety.paused })}>{safety.paused ? 'Resume automatic decisions' : 'Pause automatic decisions'}</button>{safety.global_suspension ? <button disabled={command.isPending} onClick={() => void runAuxiliary('automatic-safety/recover', { category: null })}>Recover global suspension</button> : null}</div>{Object.keys(safety.category_suspensions).map((category) => <button key={category} onClick={() => void runAuxiliary('automatic-safety/recover', { category })}>Recover {readableCategory(category)} suspension</button>)}<small>{sample.isPending ? 'Loading recent Preview outcomes…' : sample.isError ? 'Recent Preview outcomes are unavailable.' : sample.data.items.length ? `${String(sample.data.items.length)} recent non-authoritative outcome sets.` : 'No Preview outcomes or safety suspensions need attention.'}</small></div></details>
      </aside>
      <section className="cm-policy-content" id={`policy-${activeChapter}`} tabIndex={-1}>
        <header><div><h2>{currentChapter.label}</h2><p>{currentChapter.description}</p></div></header>
        <div className="cm-policy-field-list">{visibleFields.map((field) => {
          const option = draft.options[field.key] ?? field.options[0] ?? ''
          const destination = draft.destinations[field.key] ?? (field.unsupported ? 'unsupported' : 'ask')
          const qualified = automaticQualified(field, option, data.state.rich_qualification)
          const explanation = qualificationExplanation(field, option, data.state.rich_qualification)
          const automaticAvailable = qualified && automaticAllowed
          const qualificationLabel = explanation ?? (automaticAvailable ? 'Automatic available' : qualified ? 'Enter Preview for Automatic' : 'Automatic unavailable')
          const changed = fieldChanged(field)
          return <section className={`cm-policy-field ${changed ? 'is-changed' : ''}`} aria-label={field.label} key={field.key}>
            <header><span><strong>{field.label}</strong>{changed ? <small>Draft changed</small> : null}</span><p>{field.meaning}</p></header>
            {field.unsupported ? <div className="cm-policy-unavailable"><strong>Not currently supported</strong><span>No policy choice is available for this field yet. {qualificationLabel}</span></div> : <>
              <div className="cm-policy-mode-control"><span className="cm-policy-control-heading"><strong>Review mode</strong><small className={automaticAvailable ? 'is-qualified' : ''}>{qualificationLabel}</small></span><div className="cm-policy-posture" role="group" aria-label={`${field.label} review mode`}><button type="button" aria-pressed={destination === 'automatic'} disabled={command.isPending || !automaticAvailable} title={qualificationLabel} onClick={() => editDraft({ ...draft, destinations: { ...draft.destinations, [field.key]: 'automatic' } })}>Automatic</button><button type="button" aria-pressed={destination === 'ask'} disabled={command.isPending} onClick={() => editDraft({ ...draft, destinations: { ...draft.destinations, [field.key]: 'ask' } })}>Ask me</button><button type="button" aria-pressed={destination === 'ignore'} disabled={command.isPending} onClick={() => editDraft({ ...draft, destinations: { ...draft.destinations, [field.key]: 'ignore' } })}>Ignore</button></div></div>
              <label className="cm-policy-representation"><span>Representation</span><Dropdown ariaLabel={`${field.label} representation`} value={option} options={field.options.map((value) => ({ value, label: field.optionLabels[value] ?? value }))} disabled={command.isPending || field.options.length === 1} onChange={(nextOption) => { const nextDestination = destination === 'automatic' && !automaticQualified(field, nextOption, data.state.rich_qualification) ? 'ask' : destination; editDraft({ ...draft, options: { ...draft.options, [field.key]: nextOption }, destinations: { ...draft.destinations, [field.key]: nextDestination } }) }}/></label>
            </>}
          </section>
        })}</div>
      </section>
      <section className={`cm-policy-finish stage-${stage}`} aria-label="Whole policy draft impact and activation">
        <div className="cm-policy-impact-summary" aria-live="polite"><small>Whole policy draft · 4 chapters · 14 fields</small><strong>{finishTitle}</strong><span>{finishDetail}</span></div>
        <ol aria-label="Policy activation steps">{['Validate', 'Preview', 'Activate'].map((label, index) => { const done = index === 0 ? completedValidate : index === 1 ? completedPreview : stage === 'activated'; return <li key={label} className={done ? 'done' : ''} aria-current={stage !== 'activated' && currentStep === index ? 'step' : undefined}><span>{done ? <Check/> : index + 1}</span><small>{label}</small></li> })}</ol>
        <div className="cm-policy-finish-actions">
          {stage === 'preview' ? <button disabled={command.isPending} onClick={() => setStage('editing')}>Back to editing</button> : null}
          {stage === 'activation' ? <button disabled={command.isPending} onClick={() => setStage('preview')}>Back to preview</button> : null}
          {stage === 'editing' ? <button className="primary-action" disabled={command.isPending || !draftDisplay.changed} onClick={() => void validateAndPreview()}>Validate and preview</button> : null}
          {stage === 'validating' ? <button className="primary-action" disabled>Validating…</button> : null}
          {stage === 'preview' ? <button className="primary-action" disabled={command.isPending || !snapshotId || !preview || !previewActivePolicyId} onClick={() => setStage('activation')}>Review activation</button> : null}
          {stage === 'activation' ? <button className="primary-action" disabled={command.isPending} onClick={() => void activate()}>{command.isPending ? 'Activating…' : 'Activate policy'}</button> : null}
          {stage === 'activated' ? <button className="primary-action" onClick={() => setStage('editing')}>Continue editing</button> : null}
        </div>
        {stage === 'validating' ? <section className="cm-policy-validation" aria-label="Validation in progress"><header><span className="cm-policy-validation-spinner"/><div><strong>Checking this draft</strong><small>The snapshot is saved only if every check passes.</small></div></header><ul><li><span/><div><strong>Document structure</strong><small>14 supported field definitions</small></div></li><li><span/><div><strong>Automatic qualification</strong><small>Every Automatic representation is admitted</small></div></li><li><span/><div><strong>Library scope</strong><small>Exact current scope</small></div></li></ul></section> : null}
        {(stage === 'preview' || stage === 'activation') && preview ? <section className="cm-policy-result"><aside><header><span><Check/></span><div><strong>Validated and saved</strong><small>Immutable snapshot for this preview</small></div></header><dl><div><dt>Structure</dt><dd>Passed</dd></div><div><dt>Qualification</dt><dd>Passed</dd></div><div><dt>Scope</dt><dd>{impactTotals?.libraries ? `${String(impactTotals.libraries)} ${impactTotals.libraries === 1 ? 'library' : 'libraries'}` : 'Current'}</dd></div></dl></aside><div className="cm-policy-preview"><header><div><strong>Impact preview</strong><small>Current unapplied work only · no metadata writes</small></div><b>{String(impactTotals?.items ?? 0)}</b></header><div className="cm-policy-preview-list">{previewRows.length ? previewRows.slice(0, 6).map((row) => <div key={row.id}><span className="cm-policy-work-mark" aria-hidden="true">{row.work.slice(0, 2).toUpperCase()}</span><span><strong>{row.work}</strong><small>{row.library} · {row.field}</small></span><b>{row.outcome}</b></div>) : preview.libraries.map((library) => <div key={library.library}><span className="cm-policy-work-mark" aria-hidden="true">{library.library.slice(0, 2).toUpperCase()}</span><span><strong>{library.library}</strong><small>{library.affected_categories.map(readableCategory).join(', ') || 'No changed fields'}</small></span><b>{String(library.current_unapplied_count)} current unapplied</b></div>)}</div>{previewRows.length > 6 ? <footer>Plus {String(previewRows.length - 6)} more exact field outcomes in this preview</footer> : preview.libraries.some((library) => library.cases_truncated) ? <footer>Additional current cases are included in the authoritative preview total.</footer> : null}</div>{stage === 'activation' ? <footer className="cm-policy-activation"><strong>Activate this reviewed draft?</strong><span>The active policy changes only after this guarded command succeeds. Applied history remains untouched.</span></footer> : null}</section> : null}
        {stage === 'activated' ? <section className="cm-policy-activated"><span><Check/></span><div><strong>The reviewed policy is now active</strong><small>{String(impactTotals?.items ?? 0)} current unapplied items can enter separately guarded reconciliation.</small></div></section> : null}
      </section>
    </div>
    <p className={`policy-status ${status.tone}`} role={status.tone === 'error' ? 'alert' : 'status'}>{status.message}</p>
    {transition.data && !transition.data.acknowledged ? <section className="policy-cutover" aria-label="Policy activation progress"><h2>{transition.data.state === 'processing' ? 'Applying updated policy' : transition.data.failed ? 'Activation completed with failures' : 'Activation complete'}</h2><p>{String(transition.data.completed)} of {String(transition.data.total)} current unapplied items reconciled. Applied history is untouched.</p><div className="policy-workflow-actions">{transition.data.failed ? <button disabled={command.isPending} onClick={() => void runAuxiliary(`transitions/${transition.data?.transition_id ?? ''}/retry`)}>Retry failed reconciliation</button> : null}{transition.data.state !== 'processing' ? <button disabled={command.isPending} onClick={() => void runAuxiliary(`transitions/${transition.data?.transition_id ?? ''}/acknowledge`)}>Dismiss update</button> : null}</div></section> : null}
  </div>
}
