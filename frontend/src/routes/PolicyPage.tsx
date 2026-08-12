import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError, derivePolicyDraftDisplay } from '../api/http'
import type { PolicyActivationView, PolicyDraft, PolicyPreviewView, PolicySection, PolicyTransitionView, ShadowPostureView } from '../api/http'
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

interface PolicyCadence {
  validated: boolean
  saved: boolean
  previewed: boolean
  impactInspected: boolean
  activated: boolean
}

const emptyCadence = (): PolicyCadence => ({
  validated: false,
  saved: false,
  previewed: false,
  impactInspected: false,
  activated: false,
})

const fields: readonly FieldDefinition[] = [
  { key: 'title_layout', label: 'Title and subtitle', meaning: 'Keep a title and subtitle together as one coherent identity.', options: ['preserve', 'title_and_subtitle'], optionLabels: { preserve: 'Preserve the current layout', title_and_subtitle: 'Prefer separate title and subtitle' }, chapter: 'identity', automatic: true },
  { key: 'contributor_names', label: 'Authors and narrators', meaning: 'Preserve contributor order while choosing how supported names are shown.', options: ['preserve', 'canonical'], optionLabels: { preserve: 'Preserve the supported form', canonical: 'Use canonical names' }, chapter: 'identity', automatic: true },
  { key: 'language_rendering', label: 'Language', meaning: 'Keep canonical language identity while choosing its readable form.', options: ['preserve', 'display_name'], optionLabels: { preserve: 'Preserve the supported form', display_name: 'Use the language display name' }, chapter: 'identity', automatic: true },
  { key: 'date_precision', label: 'Publication date and year', meaning: 'The date and matching year move together as one edition fact.', options: ['year', 'full_date'], optionLabels: { year: 'Year only', full_date: 'Full date with matching year' }, chapter: 'publication', automatic: true },
  { key: 'publisher_preference', label: 'Publisher', meaning: 'Choose the supported publisher level for the same manifestation.', options: ['publisher', 'imprint'], optionLabels: { publisher: 'Prefer publisher', imprint: 'Prefer imprint' }, chapter: 'publication', automatic: true },
  { key: 'isbn_form', label: 'ISBN', meaning: 'Preserve exact ISBN data or choose an admitted ISBN-13 representation.', options: ['preserve_exact', 'prefer_isbn13'], optionLabels: { preserve_exact: 'Preserve exact ISBN', prefer_isbn13: 'Prefer ISBN-13' }, chapter: 'publication', automatic: false },
  { key: 'subtitle', label: 'Subtitle', meaning: 'Fill an empty subtitle, or use only an admitted corroborated title/subtitle pair.', options: ['fill_empty', 'corroborated_split'], optionLabels: { fill_empty: 'Fill only when empty', corroborated_split: 'Use corroborated split' }, chapter: 'identity', automatic: false },
  { key: 'series', label: 'Series memberships', meaning: 'Keep qualified memberships in the product domain while Audiobookshelf projects one selected membership.', options: ['primary_narrative', 'source_order', 'all_qualified'], optionLabels: { primary_narrative: 'Primary narrative', source_order: 'First source membership', all_qualified: 'All qualified memberships' }, chapter: 'publication', automatic: false },
  { key: 'genres', label: 'Genres', meaning: 'Preview one exact eligible source list.', options: ['fill_empty', 'add', 'replace'], optionLabels: { fill_empty: 'Fill only when empty', add: 'Add missing values', replace: 'Replace with source list' }, chapter: 'curation', automatic: false },
  { key: 'tags', label: 'Tags', meaning: 'Tags are off by default and always remain manual review choices.', options: ['off', 'fill_empty', 'add', 'replace'], optionLabels: { off: 'Off', fill_empty: 'Fill only when empty', add: 'Add missing values', replace: 'Replace with source list' }, chapter: 'curation', automatic: false },
  { key: 'description_fill', label: 'Description', meaning: 'Use only admitted bounded plain text or visible Google text.', options: ['safe_plain_text', 'google_visible_text'], optionLabels: { safe_plain_text: 'Safe plain text', google_visible_text: 'Visible Google text' }, chapter: 'curation', automatic: false },
  { key: 'cover_order', label: 'Cover', meaning: 'Cover review uses the fixed supported source order.', options: ['fixed_source_order'], optionLabels: { fixed_source_order: 'Fixed supported source order' }, chapter: 'curation', automatic: false },
  { key: 'asin', label: 'ASIN', meaning: 'Preserve exact ASIN data or select an admitted ebook manifestation.', options: ['preserve_exact', 'ebook_manifestation'], optionLabels: { preserve_exact: 'Preserve exact ASIN', ebook_manifestation: 'Ebook manifestation' }, chapter: 'curation', automatic: false },
  { key: 'explicit', label: 'Explicit-content flag', meaning: 'This destination is visible but is not currently supported.', options: ['unsupported'], optionLabels: { unsupported: 'Not currently supported' }, chapter: 'unsupported', automatic: false, unsupported: true },
]

const chapters: readonly { key: PolicySection; label: string; description: string }[] = [
  { key: 'identity', label: 'Identity', description: 'How a book names itself' },
  { key: 'publication', label: 'Publication', description: 'How edition facts are represented' },
  { key: 'curation', label: 'Curation', description: 'Fields that remain intentionally human-owned' },
  { key: 'unsupported', label: 'Unsupported content', description: 'Visible limitations rather than inert controls' },
]

const readableCategory = (key: string) => fields.find((field) => field.key === key)?.label ?? key.replaceAll('_', ' ')
const previewValue = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value)
const isPolicyPreview = (value: unknown): value is PolicyPreviewView => typeof value === 'object' && value !== null && 'schema_version' in value && value.schema_version === 'metadata-policy-preview-v1' && 'libraries' in value && Array.isArray(value.libraries)
const automaticQualified = (field: FieldDefinition, option: string, qualification: readonly { readonly automatic_available: boolean; readonly field: string; readonly option: string }[]) => {
  const rich = qualification.find((entry) => entry.field === field.key && entry.option === option)
  return rich ? rich.automatic_available : field.automatic && !(field.key === 'publisher_preference' && option === 'imprint')
}

const qualificationExplanation = (field: FieldDefinition, option: string, qualification: readonly { readonly automatic_available: boolean; readonly disposition: string; readonly field: string; readonly option: string }[]) => {
  const rich = qualification.find((entry) => entry.field === field.key && entry.option === option)
  if (!rich) return undefined
  if (rich.disposition === 'policy_only') return 'Automatic is unavailable: this policy option is intentionally off.'
  if (rich.disposition === 'passing_human_only') return 'Automatic is unavailable: this field remains unsupported and has no policy choice.'
  return 'Automatic is unavailable: this option has insufficient qualification coverage and remains Ask me or Ignore.'
}

export default function PolicyPage({ data }: { data: PolicyCritical }) {
  const { http, queryClient } = useReviewContext()
  const search = policyRoute.useSearch()
  const navigate = useNavigate({ from: '/policy' })
  const activationTrigger = useRef<HTMLButtonElement>(null)
  const activeChapter = search.section
  const [draft, setDraft] = useState<PolicyDraft>(() => ({ name: data.state.profile.name, options: { ...data.state.profile.options }, destinations: { ...data.state.profile.destinations } }))
  const [draftId, setDraftId] = useState<string>()
  const [reviewedId, setReviewedId] = useState<string>()
  const [preview, setPreview] = useState<PolicyPreviewView>()
  const [activationReview, setActivationReview] = useState(false)
  const [cadence, setCadence] = useState<PolicyCadence>(emptyCadence)
  const [status, setStatus] = useState<{ tone: 'neutral' | 'success' | 'error' | 'warning'; message: string }>({ tone: 'neutral', message: 'Draft is separate from the active policy.' })
  const shadow = data.shadow
  const safety = data.safety
  const sample = useQuery({ queryKey: ['policy', 'shadow', 'sample'], queryFn: ({ signal }) => http.getShadowSample(signal), staleTime: 30_000, retry: false })
  const transition = useQuery({ queryKey: ['policy', 'transition'], queryFn: ({ signal }) => http.getPolicyTransition(signal), staleTime: 1_000, retry: false, refetchInterval: (query) => query.state.data?.state === 'processing' ? 1_500 : false })
  const refresh = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['policy'] }), queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] })]) }
  const command = useMutation({ mutationFn: async ({ action, body }: { action: string; body?: unknown }) => {
    if (action === 'validate') return http.policyCommand<{ policy_id: string; schema_version: string }>('validate', body)
    if (action === 'drafts') return http.policyCommand<{ policy_id: string; schema_version: string }>('drafts', body)
    if (action === 'preview') return http.policyCommand<PolicyPreviewView>('preview', body)
    if (action === 'activate') return http.policyCommand<PolicyActivationView>('activate', body)
    return http.policyCommand<PolicyTransitionView | ShadowPostureView>(action, body)
  }, retry: false, onSettled: refresh })

  const editDraft = (next: PolicyDraft) => {
    setDraft(next)
    setDraftId(undefined)
    setReviewedId(undefined)
    setPreview(undefined)
    setActivationReview(false)
    setCadence(emptyCadence())
    setStatus({ tone: 'neutral', message: 'Draft has unreviewed changes.' })
  }
  const dismissActivationReview = () => {
    setActivationReview(false)
    requestAnimationFrame(() => activationTrigger.current?.focus())
  }
  const keepActivationFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      dismissActivationReview()
      return
    }
    if (event.key !== 'Tab') return
    const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
    const first = controls[0]
    const last = controls.at(-1)
    if (!first || !last) return
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
  const run = async (action: string, body?: unknown) => {
    const label = action === 'activate' ? 'Activating reviewed policy…' : action === 'preview' ? 'Previewing impact…' : action === 'drafts' ? 'Saving immutable draft…' : action === 'validate' ? 'Checking draft…' : 'Updating policy state…'
    setStatus({ tone: 'neutral', message: label })
    try {
      const value = await command.mutateAsync({ action, body })
      if (isPolicyPreview(value)) { setPreview(value); setReviewedId(value.policy_id); setCadence((current) => ({ ...current, saved: true, previewed: true, impactInspected: false, activated: false })); setStatus({ tone: 'success', message: 'Impact preview is current. The active policy is unchanged.' }) }
      else if ('policy_id' in value && action === 'drafts') { setDraftId(value.policy_id); setReviewedId(undefined); setPreview(undefined); setCadence((current) => ({ ...current, saved: true, previewed: false, impactInspected: false, activated: false })); setStatus({ tone: 'success', message: 'Immutable draft saved. Preview its impact before activation.' }) }
      else if (action === 'validate') { setCadence((current) => ({ ...current, validated: true })); setStatus({ tone: 'success', message: 'Draft is valid. The active policy is unchanged.' }) }
      else if (action === 'activate') { setDraftId(undefined); setReviewedId(undefined); setActivationReview(false); setCadence((current) => ({ ...current, activated: true })); setStatus({ tone: 'success', message: 'Policy activated. Current unapplied work is being reconciled.' }) }
      else if (action.startsWith('shadow/')) setStatus({ tone: 'success', message: 'Automatic-decision posture updated from the authoritative server.' })
      else if (action.startsWith('automatic-safety/')) setStatus({ tone: 'success', message: 'Automatic safety state updated from the authoritative server.' })
      else setStatus({ tone: 'success', message: 'Policy state updated from the authoritative server.' })
    } catch (error) {
      if (error instanceof AmbiguousMutationError) setStatus({ tone: 'warning', message: 'The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.' })
      else if (action === 'activate' && error instanceof ApiError && error.status === 409) { dismissActivationReview(); setStatus({ tone: 'warning', message: 'The active policy changed before activation. Review the refreshed policy and create a new draft.' }) }
      else if (error instanceof ApiError && error.status === 422) setStatus({ tone: 'error', message: 'The draft is not valid. Review the highlighted policy intent and try again.' })
      else setStatus({ tone: 'error', message: 'Nothing changed. Review the current server state and try again.' })
    }
  }
  const openChapter = async (section: PolicySection) => {
    await navigate({ search: { section }, replace: true })
    requestAnimationFrame(() => {
      const target = document.getElementById(`policy-${section}`)
      target?.scrollIntoView({ block: 'start' })
      target?.focus({ preventScroll: true })
    })
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
  const impactTotals = useMemo(() => preview ? { libraries: preview.libraries.length, items: preview.libraries.reduce((total, item) => total + item.current_unapplied_count, 0) } : undefined, [preview])
  const readyToReview = Boolean(draftId && reviewedId === draftId && preview)
  const transitionCopy = transition.data && !transition.data.acknowledged
    ? transition.data.state === 'processing'
      ? `Applying updated policy · ${String(transition.data.completed)} of ${String(transition.data.total)}`
      : `${transition.data.failed ? 'Completed with failures' : 'Policy update complete'} · ${String(transition.data.completed)} of ${String(transition.data.total)}`
    : undefined

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      document.getElementById(`policy-${activeChapter}`)?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [activeChapter])

  return <>
    <section className="policy-notebook" data-route-focus tabIndex={-1}>
      <aside className="policy-index">
        <div className="policy-index-inner">
          <p className="eyebrow">Policy notebook</p>
          <div className="policy-profile-state"><span>Active policy</span><strong>{data.state.profile.name}</strong><small className={draftDisplay.changed ? 'draft-changed' : ''}>{draftDisplay.changed ? 'Draft has unreviewed changes' : 'Draft matches active policy'}</small></div>
          {transitionCopy ? <p className="policy-transition" role="status">{transitionCopy}</p> : null}
          <nav aria-label="Policy chapters">{chapters.map((chapter) => { const chapterFields = fields.filter((field) => field.chapter === chapter.key); const changed = chapterFields.filter(fieldChanged).length; return <button type="button" key={chapter.key} aria-current={activeChapter === chapter.key} onClick={() => void openChapter(chapter.key)}><span>{chapter.label}</span><small>{changed ? `${String(changed)} changed` : `${String(chapterFields.length)} field${chapterFields.length === 1 ? '' : 's'}`}</small></button> })}</nav>
          <label className="policy-chapter-picker">Policy chapter<select aria-label="Policy chapter" value={activeChapter} onChange={(event) => void openChapter(event.target.value as PolicySection)}>{chapters.map((chapter) => <option value={chapter.key} key={chapter.key}>{chapter.label}</option>)}</select></label>
        </div>
      </aside>
      <div className="policy-pages">
        <header><p className="eyebrow">Global policy draft</p><h1>Choose meaning before automation.</h1><p>Start from the complete active policy. Change only the representation or attention posture that matters; every other field keeps its current intent.</p><label className="policy-name">Policy name<input aria-label="Policy name" value={draft.name} maxLength={80} onChange={(event) => editDraft({ ...draft, name: event.target.value })}/></label></header>
        {chapters.map((chapter) => { const chapterFields = fields.filter((field) => field.chapter === chapter.key); const changedCount = chapterFields.filter(fieldChanged).length; const expanded = activeChapter === chapter.key; return <section className={`policy-chapter ${expanded ? 'expanded' : 'collapsed'}`} id={`policy-${chapter.key}`} key={chapter.key} tabIndex={-1}>
          <div><h2>{chapter.label}</h2><p>{chapter.description}</p></div>
          {expanded ? <div className="policy-chapter-controls">{chapterFields.map((field) => {
            const option = draft.options[field.key] ?? field.options[0] ?? ''
            const destination = draft.destinations[field.key] ?? (field.unsupported ? 'unsupported' : 'ask')
            const qualified = automaticQualified(field, option, data.state.rich_qualification)
            const automaticExplanation = qualificationExplanation(field, option, data.state.rich_qualification) ?? (!qualified ? 'Automatic is not qualified for this field and representation.' : !automaticAllowed ? 'Enter Preview mode before choosing qualified Automatic.' : 'Qualified, decision-only Automatic is available.')
            const changed = fieldChanged(field)
            return <fieldset className={`policy-field ${changed ? 'changed' : ''} ${field.unsupported ? 'unsafe' : ''}`} key={field.key}>
              <legend><strong>{field.label}</strong>{changed || field.unsupported ? <span className="policy-field-state">{field.unsupported ? 'Unavailable' : 'Draft changed'}</span> : null}<small>{field.meaning}</small></legend>
              {field.unsupported ? <div className="policy-unavailable"><span className="badge">Not currently supported</span><p>No policy choice is available for this content yet.</p><small className="automatic-explanation">{automaticExplanation}</small></div> : <>
                <span className="policy-posture" aria-label={`${field.label} attention posture`}>
                  <button type="button" aria-pressed={destination === 'automatic'} disabled={!qualified || !automaticAllowed || command.isPending} title={automaticExplanation} onClick={() => editDraft({ ...draft, destinations: { ...draft.destinations, [field.key]: 'automatic' } })}>Automatic</button>
                  <button type="button" aria-pressed={destination === 'ask'} disabled={command.isPending} onClick={() => editDraft({ ...draft, destinations: { ...draft.destinations, [field.key]: 'ask' } })}>Ask me</button>
                  <button type="button" aria-pressed={destination === 'ignore'} disabled={command.isPending} onClick={() => editDraft({ ...draft, destinations: { ...draft.destinations, [field.key]: 'ignore' } })}>Ignore</button>
                </span>
                <small className="automatic-explanation">{automaticExplanation}</small>
                {field.options.length > 1 ? <label>Representation<select aria-label={`${field.label} representation`} value={option} disabled={command.isPending} onChange={(event) => { const nextOption = event.target.value; const nextDestination = destination === 'automatic' && !automaticQualified(field, nextOption, data.state.rich_qualification) ? 'ask' : destination; editDraft({ ...draft, options: { ...draft.options, [field.key]: nextOption }, destinations: { ...draft.destinations, [field.key]: nextDestination } }) }}>{field.options.map((value) => <option value={value} key={value}>{field.optionLabels[value]}</option>)}</select></label> : <p className="fixed-representation">Representation: {field.optionLabels[option]}</p>}
              </>}
            </fieldset>
          })}</div> : <button className="policy-chapter-summary" type="button" onClick={() => void openChapter(chapter.key)}><span>{changedCount ? `${String(changedCount)} changed field${changedCount === 1 ? '' : 's'}` : `${String(chapterFields.length)} unchanged field${chapterFields.length === 1 ? '' : 's'}`}</span><strong>Open {chapter.label.toLowerCase()}</strong></button>}
        </section> })}
        <section className="policy-impact" aria-label="Policy impact and activation">
          <div><strong className={draftDisplay.changed ? 'draft-changed' : ''}>{draftDisplay.changed ? 'Draft differs from active policy' : 'Draft matches active policy'}</strong><small>No target or media writes occur while editing or previewing.</small></div>
          {preview ? <div className="policy-impact-summary"><strong>{String(impactTotals?.libraries ?? 0)} {impactTotals?.libraries === 1 ? 'library' : 'libraries'} · {String(impactTotals?.items ?? 0)} current unapplied {impactTotals?.items === 1 ? 'item' : 'items'}</strong>{preview.libraries.length ? preview.libraries.map((item) => <div key={item.library}><p><span>{item.library}</span><small>{item.affected_categories.length ? item.affected_categories.map(readableCategory).join(', ') : 'No changed fields'} · {String(item.current_unapplied_count)} current unapplied</small></p>{item.cases.map((rawCase, index) => { const casePreview = rawCase as unknown as { readonly fields: readonly { readonly current_value: unknown; readonly desired_value: unknown; readonly field: string; readonly narrowed_for_target: boolean; readonly outcome: string; readonly qualified_value: unknown; readonly reason: string | null; readonly target_state: string }[]; readonly report_id: string }; return <div className="policy-case-preview" key={casePreview.report_id || String(index)}><small>Current case</small>{casePreview.fields.map((field) => <p key={field.field}><strong>{readableCategory(field.field)}</strong>: {previewValue(field.current_value === null ? field.target_state : field.current_value)} → {field.desired_value === null ? (field.reason ?? field.outcome) : previewValue(field.desired_value)}{field.narrowed_for_target ? ' (first membership projected)' : ''}{field.qualified_value !== null ? ` · qualified: ${previewValue(field.qualified_value)}` : ''}</p>)}</div>})}{item.cases_truncated ? <small>Showing 20 of {String(item.cases_total)} current cases.</small> : null}</div>) : <p>No libraries are affected by this draft.</p>}</div> : <p className="policy-impact-empty">Preview the draft to see affected fields and libraries.</p>}
          <ol className="policy-cadence" aria-label="Policy activation steps"><li data-complete={cadence.validated}><span>1</span><small>Validate<em>{cadence.validated ? 'Checked' : 'Not checked'}</em></small></li><li data-complete={cadence.previewed} data-current={cadence.saved && !cadence.previewed}><span>2</span><small>Preview<em>{cadence.previewed ? 'Impact ready' : cadence.saved ? 'Draft saved' : 'Not started'}</em></small></li><li data-complete={cadence.impactInspected}><span>3</span><small>Inspect impact<em>{cadence.impactInspected ? 'Reviewed' : 'Not reviewed'}</em></small></li><li data-complete={cadence.activated}><span>4</span><small>Activate<em>{cadence.activated ? 'Activated' : 'Not activated'}</em></small></li></ol>
          <div className="policy-workflow-actions"><button disabled={command.isPending} onClick={() => void run('validate', draft)}>Check draft</button><button disabled={command.isPending} onClick={() => void run('drafts', draft)}>Save draft</button><button disabled={command.isPending || !draftId} onClick={() => void run('preview', draft)}>Preview impact</button><button ref={activationTrigger} className="primary-action" disabled={command.isPending || !readyToReview} onClick={() => { setCadence((current) => ({ ...current, impactInspected: true })); setActivationReview(true) }}>Review activation</button></div>
        </section>
        <p className={`policy-status ${status.tone}`} role={status.tone === 'error' ? 'alert' : 'status'}>{status.message}</p>
        <details className="policy-safety"><summary>Automatic-decision availability and safety</summary><div><p>{shadow.posture === 'preview' ? 'Preview mode is on. Outcomes remain non-authoritative.' : shadow.posture === 'automatic' ? 'Qualified Automatic is active in decision-only mode.' : 'Human review is active. Enter Preview before enabling qualified Automatic.'}</p><div className="policy-workflow-actions"><button disabled={command.isPending || shadow.posture === 'preview'} onClick={() => void run('shadow/enter')}>Enter Preview mode</button><button disabled={command.isPending || shadow.posture !== 'preview'} onClick={() => void run('shadow/confirm-automatic')}>Activate qualified Automatic</button><button disabled={command.isPending} onClick={() => void run('automatic-safety/pause', { paused: !safety.paused })}>{safety.paused ? 'Resume automatic decisions' : 'Pause automatic decisions'}</button><button disabled={command.isPending || !safety.global_suspension} onClick={() => void run('automatic-safety/recover', { category: null })}>Recover global safety suspension</button></div>{Object.keys(safety.category_suspensions).map((category) => <button key={category} onClick={() => void run('automatic-safety/recover', { category })}>Recover {readableCategory(category)} suspension</button>)}<h3>Recent Preview outcomes</h3>{sample.isPending ? <p role="status">Loading Preview outcomes…</p> : sample.isError ? <button onClick={() => void sample.refetch()}>Retry Preview outcomes</button> : sample.data.items.length ? sample.data.items.map((item) => <p key={item.outcome_set_id}>{String(item.outcomes.length)} field outcomes · non-authoritative</p>) : <p>No Preview outcomes have been recorded yet.</p>}</div></details>
        {transition.data && !transition.data.acknowledged ? <section className="policy-cutover" aria-label="Policy activation progress"><h2>{transition.data.state === 'processing' ? 'Applying updated policy' : transition.data.failed ? 'Activation completed with failures' : 'Activation complete'}</h2><p>{String(transition.data.completed)} of {String(transition.data.total)} current unapplied items reconciled. Applied history is untouched.</p><div className="policy-workflow-actions">{transition.data.failed ? <button disabled={command.isPending} onClick={() => void run(`transitions/${transition.data?.transition_id ?? ''}/retry`)}>Retry failed reconciliation</button> : null}{transition.data.state !== 'processing' ? <button disabled={command.isPending} onClick={() => void run(`transitions/${transition.data?.transition_id ?? ''}/acknowledge`)}>Dismiss update</button> : null}</div></section> : null}
      </div>
    </section>
    {activationReview && preview && draftId ? <div className="policy-dialog" role="dialog" aria-modal="true" aria-labelledby="activation-title" onKeyDown={keepActivationFocus}><section><p className="eyebrow">Activation review</p><h2 id="activation-title">Apply this policy to current unapplied work?</h2><p>The active policy will change only after this guarded command succeeds. Applied history is untouched.</p><ul>{preview.libraries.map((item) => <li key={item.library}><span>{item.library}</span><strong>{item.affected_categories.length ? item.affected_categories.map(readableCategory).join(', ') : 'No changed fields'} · {String(item.current_unapplied_count)} current unapplied</strong></li>)}</ul>{preview.libraries.length === 0 ? <p>No libraries are affected.</p> : null}<div className="policy-workflow-actions"><button autoFocus disabled={command.isPending} onClick={dismissActivationReview}>Cancel</button><button className="primary-action" disabled={command.isPending} onClick={() => void run('activate', { policy_id: draftId, expected_active_policy_id: data.state.policy_id })}>{command.isPending ? 'Activating…' : 'Activate policy'}</button></div></section></div> : null}
  </>
}
