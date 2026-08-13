import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'

import { AmbiguousMutationError } from '../api/http'
import type { ActivityEventType, ActivitySearch, ActivityView } from '../api/http'
import { activityOutcome } from '../activityState'
import { Dropdown } from '../components/Dropdown'
import { useRouteReadyFocus } from '../focus'
import { activityRoute, useReviewContext } from '../router'

const records = (value: unknown): readonly Record<string, unknown>[] => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && !Array.isArray(item)) : []
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0
const words = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
const destinationLabel = (destination: string) => words(destination)
const capabilityDestinations = (capability: Record<string, unknown> | undefined) => records(capability?.destinations).map((destination) => ({ destination: text(destination.destination), state: text(destination.state, 'not_ready'), consented: destination.consented === true }))
const capabilityLabel = (state: string) => ({ allowed: 'Ready for consent', partially_allowed: 'Some destinations are ready', connection_unavailable: 'Connection needs checking', insufficient_permission: 'Permission is unavailable', field_unavailable: 'A field is unavailable', not_ready: 'Not ready yet' }[state] ?? 'Not ready yet')
const stateLabel = (state: string) => ({ applying: 'Applying', claimed: 'Preparing', planned: 'Planned', reconciling: 'Checking', retryable: 'Waiting safely', verified: 'Verified', stale: 'Stale', superseded: 'Superseded', abandoned: 'Stopped safely' }[state] ?? 'Recorded')
const categoryLabel: Record<ActivityEventType, string> = { review: 'Review', policy: 'Policy', metadata: 'Metadata', safety: 'Safety', unknown: 'Recorded' }
const typeOptions = [
  { id: 'all', label: 'All' }, { id: 'review', label: 'Reviews' }, { id: 'policy', label: 'Policy' },
  { id: 'metadata', label: 'Metadata' }, { id: 'safety', label: 'Safety' },
] as const
type ActivitySearchUpdate = Partial<Omit<ActivitySearch, 'q'>> & { q?: string | undefined }
const dateParts = (value: number | null) => {
  if (value === null) return { day: 'Date not recorded', time: '—' }
  const date = new Date(value * 1000)
  return {
    day: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date),
    time: new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }).format(date),
  }
}

function PageJump({ page, pageCount, onCommit }: { page: number, pageCount: number, onCommit: (page: number) => void }) {
  const [value, setValue] = useState(String(page))
  const commit = () => { const requested = Number.parseInt(value, 10); if (!Number.isFinite(requested)) { setValue(String(page)); return }; onCommit(requested) }
  return <label className="cm-activity-page-jump"><span>Page</span><input aria-label="Activity page number" inputMode="numeric" type="number" min="1" max={pageCount} value={value} onChange={(event) => setValue(event.target.value)} onBlur={commit} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { setValue(String(page)); event.currentTarget.blur() } }}/><span>of {pageCount}</span></label>
}

export default function ActivityPage({ initial }: { initial: ActivityView }) {
  const { http, queryClient } = useReviewContext()
  const navigate = useNavigate({ from: activityRoute.fullPath })
  const search = activityRoute.useSearch()
  useRouteReadyFocus('activity')
  const query = useQuery({
    queryKey: ['activity', search.q ?? null, search.eventType, search.rangeDays, search.page, search.pageSize],
    queryFn: ({ signal }) => http.getActivity(search, signal), initialData: initial,
    staleTime: 4_000, retry: false, refetchInterval: 5_000,
  })
  const data = query.data
  const ledger = data.ledger
  const configured = records(data.configured_libraries)
  const enabled = records(data.enabled_libraries)
  const capabilities = records(data.capabilities)
  const capability = (library: string) => capabilities.find((item) => text(item.library) === library)
  const preparedCount = (library: Record<string, unknown>) => typeof library.prepared === 'object' && library.prepared ? count((library.prepared as Record<string, unknown>).total) : 0
  const consentCandidates = configured.filter((item) => capabilityDestinations(capability(text(item.library))).some((destination) => destination.state === 'allowed' && !destination.consented))
  const selectable = consentCandidates.filter((item) => { const state = text(capability(text(item.library))?.state, 'not_ready'); return data.backup_ready && ['allowed', 'partially_allowed'].includes(state) && preparedCount(item) > 0 })
  const selectableNames = new Set(selectable.map((item) => text(item.library)))
  const [selection, setSelection] = useState(() => ({ source: data, names: new Set<string>() }))
  if (selection.source !== data) setSelection({ source: data, names: new Set([...selection.names].filter((name) => selectableNames.has(name))) })
  const selected = selection.source === data ? selection.names : new Set([...selection.names].filter((name) => selectableNames.has(name)))
  const [status, setStatus] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [consentOpen, setConsentOpen] = useState(false)
  const mutation = useMutation({ mutationFn: ({ path, body }: { path: string; body?: unknown }) => http.activityCommand(path, body), retry: false, onSuccess: async () => { setSelection({ source: data, names: new Set() }); setStatus('Activity state refreshed from the authoritative server.'); await queryClient.invalidateQueries({ queryKey: ['activity'] }) }, onError: async (error, command) => { if (error instanceof AmbiguousMutationError) { try { const refreshed = await query.refetch(); const outcome = refreshed.isError ? 'unknown' : activityOutcome(refreshed.data, command); setStatus(outcome === 'applied' ? 'The requested activity change was applied, confirmed by the authoritative server.' : 'The activity outcome could not be confirmed. No command was resent.') } catch { setStatus('The activity outcome could not be confirmed. No command was resent.') }; return } setStatus('The requested activity change failed safely. Try again.'); await queryClient.invalidateQueries({ queryKey: ['activity'] }) } })
  const selectedGrants = selectable.flatMap((library) => { const name = text(library.library); return selected.has(name) ? [{ library: name, destinations: capabilityDestinations(capability(name)).filter((destination) => destination.state === 'allowed').map((destination) => destination.destination) }] : [] })
  const toggle = (library: string) => { const next = new Set(selected); if (next.has(library)) next.delete(library); else next.add(library); setSelection({ source: data, names: next }) }
  const changeSearch = (next: ActivitySearchUpdate) => {
    setExpandedId(null)
    const resetsPage = Object.hasOwn(next, 'q') || Object.hasOwn(next, 'eventType') || Object.hasOwn(next, 'rangeDays') || Object.hasOwn(next, 'pageSize')
    const base: ActivitySearch = {
      eventType: next.eventType ?? search.eventType,
      rangeDays: next.rangeDays ?? search.rangeDays,
      pageSize: next.pageSize ?? search.pageSize,
      page: resetsPage ? 1 : next.page ?? 1,
      ...(!Object.hasOwn(next, 'q') && search.q ? { q: search.q } : {}),
      ...(next.q ? { q: next.q } : {}),
    }
    void navigate({ search: base, replace: true })
  }
  const goToPage = (page: number) => { const clamped = Math.max(1, Math.min(ledger.page_count, page)); setExpandedId(null); void navigate({ search: (previous) => ({ ...previous, page: clamped }) }) }
  const suspensions = records(data.suspensions)
  const writes = ['claimed', 'applying', 'reconciling'].reduce((total, state) => total + count(data.counts[state]), 0)
  const recoveryCount = suspensions.length
  const waiting = data.prepared.total + count(data.counts.planned) + count(data.counts.retryable)
  const waitingEvent = data.current_waiting
  const needsConsent = consentCandidates.length > 0 || (!enabled.length && configured.length > 0)

  const hasAuthority = enabled.length > 0 || suspensions.length > 0 || needsConsent

  return <div className="cm-activity-route">
    <header className="cm-page-heading" tabIndex={-1} data-route-focus><div><h1>Activity and recovery</h1><p>See what needs attention now and inspect the durable record of reviews, policy changes, safeguards, and verified updates.</p></div><span className="cm-activity-calm">{recoveryCount ? 'Recovery available' : data.paused ? 'New work paused' : 'No action required'}</span></header>
    <section className="cm-activity-status" aria-labelledby="activity-status-title">
      <div className="cm-activity-status-copy"><h2 id="activity-status-title">{recoveryCount ? 'Recovery needs attention' : data.paused ? 'New work is paused' : 'No intervention needed'}</h2><p>{recoveryCount ? 'Affected work remains held until its current state can be reconciled safely.' : data.paused ? 'No new work will be claimed. Work already claimed may finish its safe checkpoint; resume new work when you are ready for planned work to continue.' : 'Guarded work continues automatically. Recovery controls appear only when your decision is required.'}</p></div>
      <dl><div><dt>Writes in progress</dt><dd>{writes}</dd></div><div><dt>Waiting safely</dt><dd>{waiting}</dd></div><div><dt>Recovery needed</dt><dd>{recoveryCount}</dd></div></dl>
      {waitingEvent ? <div className="cm-activity-waiting"><span className="cm-activity-pulse" aria-hidden="true"/><div><strong>{waitingEvent.work_label} · {destinationLabel(waitingEvent.destination)} update</strong><span>{data.paused ? 'Resume new work when you are ready. No automatic check will start while new work is paused.' : 'Next exact-current check runs automatically. No action is required.'}</span></div></div> : null}
      {hasAuthority ? <details className="cm-activity-authority">
        <summary><span><strong>Authority and recovery</strong><small>{recoveryCount ? `${String(recoveryCount)} recovery ${recoveryCount === 1 ? 'item' : 'items'} need review` : data.paused ? 'New work remains paused until you resume it' : 'Write permissions and pause controls'}</small></span></summary>
        <div className="cm-activity-authority-actions" aria-label="Activity authority and recovery">
          {enabled.length > 0 ? <button disabled={mutation.isPending} onClick={() => mutation.mutate({ path: `field-effects/${data.paused ? 'resume' : 'pause'}` })}>{data.paused ? 'Resume new work' : 'Pause new work'}</button> : null}
          {enabled.map((library) => <button className="activity-revoke" key={`${text(library.connection)}:${text(library.library)}`} disabled={mutation.isPending} onClick={() => mutation.mutate({ path: `activity/${encodeURIComponent(text(library.connection))}/${encodeURIComponent(text(library.library))}/revoke` })}>Remove write permission for {text(library.library)}</button>)}
          {needsConsent ? <button onClick={() => setConsentOpen((value) => !value)} aria-expanded={consentOpen}>Review write permissions</button> : null}
          {suspensions.length ? <div className="cm-activity-recovery-facts" aria-label="Recovery facts">{suspensions.map((item, index) => <p key={`${text(item.connection)}:${text(item.destination)}:${String(index)}`}><strong>Recovery:</strong> {words(text(item.reason, 'A shared condition is being checked'))}. No accepted decision is retried blindly.</p>)}</div> : null}
          {consentOpen ? <section className="activity-consent-ledger" id="activity-consent"><div><p className="eyebrow">Prepared, read-only work</p><h2>Enable guarded writes</h2><p>Each write is checked before it starts, verified afterwards, and recorded durably.</p></div>{configured.map((library) => { const name = text(library.library); const state = text(capability(name)?.state, 'not_ready'); const disabled = !selectableNames.has(name); return <label className="activity-consent-library" key={name}><input type="checkbox" disabled={disabled} checked={selected.has(name)} onChange={() => toggle(name)}/><strong>{name}</strong><small>{preparedCount(library)} prepared · {capabilityLabel(state)}</small></label> })}<button className="primary-action" disabled={mutation.isPending || !data.backup_ready || selectedGrants.length === 0} onClick={() => mutation.mutate({ path: 'activity/consent', body: { grants: selectedGrants } })}>Enable selected libraries</button></section> : null}
        </div>
      </details> : null}
    </section>
    <section className="cm-activity-ledger" aria-labelledby="activity-ledger-title">
      <header className="cm-activity-ledger-heading"><div><h2 id="activity-ledger-title">Activity ledger</h2><p>One record for review decisions, policy changes, safeguards, writes, and verification.</p></div><label className="cm-activity-range"><span>Date range</span><Dropdown compact ariaLabel="Activity date range" value={String(search.rangeDays)} options={[{ value: '7', label: 'Last 7 days' }, { value: '30', label: 'Last 30 days' }, { value: '90', label: 'Last 90 days' }]} onChange={(value) => changeSearch({ rangeDays: Number(value) as ActivitySearch['rangeDays'] })}/></label></header>
      <div className="cm-activity-tools"><label className="cm-activity-search"><span>Search activity</span><input aria-label="Search activity" type="search" value={search.q ?? ''} placeholder="Work, outcome, or status" onChange={(event) => changeSearch({ q: event.target.value || undefined })}/></label><div className="cm-activity-filters" aria-label="Filter activity by type">{typeOptions.map((item) => <button key={item.id} aria-pressed={search.eventType === item.id} onClick={() => changeSearch({ eventType: item.id })}>{item.label}</button>)}</div></div>
      <div className="cm-activity-columns" aria-hidden="true"><span>When</span><span>Type</span><span>Outcome</span><span>Status</span><span/></div>
      <div className="cm-activity-events">{data.events.length ? data.events.map((event, index) => { const expanded = expandedId === event.event_id; const currentDate = dateParts(event.occurred_at); const previousDate = index ? dateParts(data.events[index - 1]?.occurred_at ?? null).day : null; return <div className="cm-activity-event-group" key={event.event_id}>{currentDate.day !== previousDate ? <h3>{currentDate.day}</h3> : null}<article className={expanded ? 'is-expanded' : ''}><time>{currentDate.time}</time><span className={`cm-activity-category is-${event.event_type}`}>{categoryLabel[event.event_type]}</span><div className="cm-activity-outcome"><strong title={event.work_label}>{event.work_label}</strong><span>{destinationLabel(event.destination)} · {event.library}</span></div><b>{stateLabel(event.state)}</b><button aria-expanded={expanded} aria-controls={`activity-evidence-${event.event_id}`} onClick={() => setExpandedId(expanded ? null : event.event_id)}>{expanded ? 'Close' : 'Inspect'}</button>{expanded ? <div className="cm-activity-evidence" id={`activity-evidence-${event.event_id}`}><header><strong>Durable evidence</strong><span>Recorded with this outcome</span></header><dl>{event.evidence.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></div> : null}</article></div> }) : <div className="cm-activity-empty"><strong>No activity matches these filters</strong><span>Try another term or activity type.</span><button onClick={() => changeSearch({ q: undefined, eventType: 'all' })}>Clear filters</button></div>}</div>
      <footer className="cm-activity-pagination"><div className="cm-activity-pagination-summary"><strong>{ledger.total ? `${String((ledger.page - 1) * ledger.page_size + 1)}–${String(Math.min(ledger.page * ledger.page_size, ledger.total))}` : '0'}</strong><span>of {ledger.total} events</span><label className="cm-activity-page-size"><span>Rows</span><Dropdown compact ariaLabel="Rows per activity page" value={String(search.pageSize)} options={[{ value: '25', label: '25 per page' }, { value: '50', label: '50 per page' }, { value: '100', label: '100 per page' }]} onChange={(value) => changeSearch({ pageSize: Number(value) as ActivitySearch['pageSize'] })}/></label></div><nav className="cm-activity-pagination-nav" aria-label="Activity pages"><button disabled={ledger.page === 1} onClick={() => goToPage(1)}>First</button><button disabled={ledger.page === 1} onClick={() => goToPage(ledger.page - 1)}>Previous</button><PageJump key={`${String(ledger.page)}:${search.q ?? ''}:${search.eventType}:${String(search.rangeDays)}:${String(search.pageSize)}`} page={ledger.page} pageCount={ledger.page_count} onCommit={goToPage}/><button disabled={ledger.page >= ledger.page_count} onClick={() => goToPage(ledger.page + 1)}>Next</button><button disabled={ledger.page >= ledger.page_count} onClick={() => goToPage(ledger.page_count)}>Last</button></nav></footer>
    </section>
    <p className="activity-command-status" role={mutation.isError ? 'alert' : 'status'}>{status}</p>
  </div>
}
