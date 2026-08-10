import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'

import type { PolicyActivationView, PolicyDraft, PolicyPreviewView, PolicyTransitionView, ShadowPostureView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { policyRoute, useReviewContext } from '../router'
import type { PolicyCritical } from '../router'

const catalogue: Record<string, readonly string[]> = {
  title_layout: ['preserve', 'title_and_subtitle'], date_precision: ['year', 'full_date'], language_rendering: ['preserve', 'display_name'], contributor_names: ['preserve', 'canonical'], publisher_preference: ['publisher', 'imprint'], genres: ['preserve', 'catalogue'], tags: ['preserve', 'catalogue'], description_fill: ['preserve', 'fill_when_empty'], isbn_form: ['preserve', 'isbn13'], cover_order: ['fixed_source_order'], asin: ['preserve', 'canonical_asin'], explicit: ['unsupported'],
}
const qualified = (name: string, option: string) => ['title_layout', 'date_precision', 'language_rendering', 'contributor_names'].includes(name) || (name === 'publisher_preference' && option === 'publisher')

export default function PolicyPage({ data }: { data: PolicyCritical }) {
  const { http, queryClient } = useReviewContext()
  const search = policyRoute.useSearch()
  const navigate = useNavigate({ from: '/policy' })
  useRouteReadyFocus(search.section)
  const [draft, setDraft] = useState<PolicyDraft>(() => ({ name: 'Policy draft', options: { ...data.state.profile.options }, destinations: { ...data.state.profile.destinations } }))
  const [draftId, setDraftId] = useState<string>()
  const [reviewedId, setReviewedId] = useState<string>()
  const [preview, setPreview] = useState<PolicyPreviewView>()
  const shadow = data.shadow
  const safety = data.safety
  const [status, setStatus] = useState('Draft is separate from active policy.')
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
  const run = async (action: string, body?: unknown) => {
    setStatus(`${action.replaceAll('/', ' ')} pending…`)
    try {
      const value = await command.mutateAsync({ action, body })
      if ('libraries' in value && 'policy_id' in value) { setPreview(value); setReviewedId(value.policy_id); setStatus('Read-only preview complete. Active policy unchanged.') }
      else if ('policy_id' in value && action === 'drafts') { setDraftId(value.policy_id); setReviewedId(undefined); setStatus(`Immutable draft created: ${value.policy_id}.`) }
      else if (action === 'validate') setStatus(`Valid draft: ${'policy_id' in value ? value.policy_id : 'current input'}. Active policy unchanged.`)
      else if (action === 'activate') { setDraftId(undefined); setReviewedId(undefined); setStatus(`Policy activated: ${(value as PolicyActivationView).policy_id}.`) }
      else if (action.startsWith('shadow/')) setStatus('Policy posture updated from the authoritative server.')
      else if (action.startsWith('automatic-safety/')) setStatus('Automatic safety state updated from the authoritative server.')
      else setStatus('Policy state updated from the authoritative server.')
    } catch { setStatus('Nothing changed. Review the current server state and try again.') }
  }
  const automaticAllowed = shadow.posture === 'preview' || shadow.posture === 'automatic'
  const sections = useMemo(() => [['identity', 'Identity'], ['draft', 'Draft'], ['effects', 'Effects'], ['availability', 'Availability']] as const, [])
  return <>
    <header className="page-heading"><div><p className="eyebrow">Metadata policy</p><h1>Choose how prepared metadata reaches you</h1><p>Draft previews change nothing; explicit activation retires only current unapplied authority.</p></div></header>
    <div className="policy-layout"><nav className="policy-sections" aria-label="Policy chapters">{sections.map(([section, label]) => <button key={section} aria-current={search.section === section} onClick={() => void navigate({ search: { section } })}>{label}</button>)}</nav><div className="policy-content">
      {search.section === 'identity' ? <section className="policy-card" tabIndex={-1} data-route-focus><h2>Active policy</h2><p>{data.state.profile.name} is active.</p><code>{data.state.policy_id}</code>{transition.data && !transition.data.acknowledged ? <div><p>{transition.data.state === 'processing' ? `Applying updated policy: ${String(transition.data.completed)}/${String(transition.data.total)}` : `Complete: ${String(transition.data.completed)}/${String(transition.data.total)}`}</p>{transition.data.failed ? <button onClick={() => void run(`transitions/${transition.data?.transition_id ?? ''}/retry`)}>Retry failed work</button> : null}{transition.data.state !== 'processing' ? <button onClick={() => void run(`transitions/${transition.data?.transition_id ?? ''}/acknowledge`)}>Dismiss</button> : null}</div> : null}</section> : null}
      {search.section === 'draft' ? <section className="policy-card" tabIndex={-1} data-route-focus><h2>Draft before activation</h2><label>Draft name<input value={draft.name} maxLength={80} onChange={(event) => { setDraft({ ...draft, name: event.target.value }); setReviewedId(undefined) }}/></label><div className="policy-fields">{Object.entries(catalogue).map(([name, options]) => <fieldset key={name}><legend>{name.replaceAll('_', ' ')}</legend><label>Option<select value={draft.options[name]} onChange={(event) => { const option = event.target.value; const currentDestination = draft.destinations[name] ?? 'ask'; const destination = currentDestination === 'automatic' && !qualified(name, option) ? 'ask' : currentDestination; setDraft({ ...draft, options: { ...draft.options, [name]: option }, destinations: { ...draft.destinations, [name]: destination } }); setReviewedId(undefined) }}>{options.map((option) => <option key={option}>{option}</option>)}</select></label><label>Destination<select value={draft.destinations[name]} onChange={(event) => { setDraft({ ...draft, destinations: { ...draft.destinations, [name]: event.target.value } }); setReviewedId(undefined) }}><option value="ask">Ask me</option><option value="ignore">Ignore</option>{name === 'explicit' ? <option value="unsupported">Unsupported</option> : <option value="automatic" disabled={!automaticAllowed || !qualified(name, draft.options[name] ?? '')}>Automatic {qualified(name, draft.options[name] ?? '') ? '(qualified decision-only)' : '(unqualified option)'}</option>}</select></label></fieldset>)}</div><div className="actions"><button disabled={command.isPending} onClick={() => void run('validate', draft)}>Validate</button><button disabled={command.isPending} onClick={() => void run('drafts', draft)}>Create immutable draft</button><button disabled={command.isPending} onClick={() => void run('preview', draft)}>Preview effects</button><button disabled={command.isPending || !draftId || reviewedId !== draftId} onClick={() => void run('activate', { policy_id: draftId, expected_active_policy_id: data.state.policy_id })}>Activate reviewed draft</button></div></section> : null}
      {search.section === 'effects' ? <section className="policy-card" tabIndex={-1} data-route-focus><h2>Per-library effects</h2>{preview ? preview.libraries.map((item) => <p key={item.library}>{item.library}: {item.affected_categories.join(', ') || 'no changed categories'}; {item.current_unapplied_count} current unapplied item(s).</p>) : <p>Preview a draft to inspect exact effects.</p>}</section> : null}
      {search.section === 'availability' ? <section className="policy-card" tabIndex={-1} data-route-focus><h2>Qualification availability</h2><p>{shadow.posture === 'preview' ? 'Preview is on. Outcomes remain non-authoritative.' : shadow.posture === 'automatic' ? 'Qualified automatic handling is active in decision-only mode.' : 'Human review is active.'}</p><div className="actions"><button disabled={command.isPending || shadow.posture === 'preview'} onClick={() => void run('shadow/enter')}>Enter Preview</button><button disabled={command.isPending || shadow.posture !== 'preview'} onClick={() => void run('shadow/confirm-automatic')}>Activate qualified automatic handling</button><button disabled={command.isPending} onClick={() => void run('automatic-safety/pause', { paused: !safety.paused })}>{safety.paused ? 'Resume automatic handling' : 'Pause automatic handling'}</button><button disabled={command.isPending || !safety.global_suspension} onClick={() => void run('automatic-safety/recover', { category: null })}>Recover global safety suspension</button></div>{Object.keys(safety.category_suspensions).map((category) => <button key={category} onClick={() => void run('automatic-safety/recover', { category })}>Recover {category.replaceAll('_', ' ')} suspension</button>)}<h3>Recent Preview outcomes</h3>{sample.isPending ? <p role="status">Loading Preview outcomes…</p> : sample.isError ? <button onClick={() => void sample.refetch()}>Retry Preview outcomes</button> : sample.data.items.length ? sample.data.items.map((item) => <p key={item.outcome_set_id}>{item.outcomes.length} field outcomes · non-authoritative</p>) : <p>No Preview outcomes have been recorded yet.</p>}</section> : null}
      <p role={command.isError ? 'alert' : 'status'}>{status}</p>
    </div></div>
  </>
}
