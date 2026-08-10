import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import type { ActivityView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { useReviewContext } from '../router'

const records = (value: unknown): readonly Record<string, unknown>[] => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && !Array.isArray(item)) : []
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0

export default function ActivityPage({ initial }: { initial: ActivityView }) {
  const { http, queryClient } = useReviewContext()
  useRouteReadyFocus('activity')
  const query = useQuery({ queryKey: ['activity'], queryFn: ({ signal }) => http.getActivity(signal), initialData: initial, staleTime: 4_000, retry: false, refetchInterval: 5_000 })
  const data = query.data
  const configured = records(data.configured_libraries)
  const enabled = records(data.enabled_libraries)
  const capabilities = records(data.capabilities)
  const enabledNames = useMemo(() => new Set(enabled.map((item) => text(item.library))), [enabled])
  const available = configured.filter((item) => !enabledNames.has(text(item.library)))
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [status, setStatus] = useState('')
  const mutation = useMutation({ mutationFn: ({ path, body }: { path: string; body?: unknown }) => http.activityCommand(path, body), retry: false, onSuccess: async () => { setStatus('Activity state refreshed from the authoritative server.'); await queryClient.invalidateQueries({ queryKey: ['activity'] }) }, onError: async () => { setStatus('The requested activity change failed safely. Try again.'); await queryClient.invalidateQueries({ queryKey: ['activity'] }) } })
  const paused = data.paused === true
  const backupReady = data.backup_ready === true
  const capability = (library: string) => text(capabilities.find((item) => text(item.library) === library)?.state, 'not_ready')
  const toggle = (library: string) => setSelected((current) => { const next = new Set(current); if (next.has(library)) next.delete(library); else next.add(library); return next })
  return <>
    <header className="page-heading" tabIndex={-1} data-route-focus><div><p className="eyebrow">Guarded application</p><h1>{enabled.length ? 'Activity' : 'Ready when you are'}</h1><p>{enabled.length ? 'A readable account of outcomes and exceptions.' : 'Review remains useful in read-only mode. Explicit consent enables supported writes.'}</p></div></header>
    <div className="activity-layout"><section className="activity-controls"><h2>{enabled.length ? paused ? 'New work paused' : 'Applying safely' : 'Enable guarded writes'}</h2>{available.length ? <fieldset><legend>Prepared libraries</legend>{available.map((library) => { const name = text(library.library); const state = capability(name); const prepared = typeof library.prepared === 'object' && library.prepared ? count((library.prepared as Record<string, unknown>).total) : 0; const disabled = !['allowed', 'partially_allowed'].includes(state) || prepared === 0; return <label key={name}><input type="checkbox" value={name} disabled={disabled} checked={selected.has(name)} onChange={() => toggle(name)}/>{name} · {prepared} prepared{disabled ? ` · ${state.replaceAll('_', ' ')}` : ''}</label> })}</fieldset> : null}<button disabled={mutation.isPending || !backupReady || selected.size === 0} onClick={() => mutation.mutate({ path: 'activity/consent', body: { libraries: [...selected] } })}>{enabled.length ? 'Enable selected libraries' : 'Enable guarded writes'}</button>{enabled.length ? <button disabled={mutation.isPending} onClick={() => mutation.mutate({ path: `field-effects/${paused ? 'resume' : 'pause'}` })}>{paused ? 'Resume new work' : 'Pause new work'}</button> : null}{enabled.map((library) => <button key={`${text(library.connection)}:${text(library.library)}`} disabled={mutation.isPending} onClick={() => mutation.mutate({ path: `activity/${encodeURIComponent(text(library.connection))}/${encodeURIComponent(text(library.library))}/revoke` })}>Remove write permission for {text(library.library)}</button>)}<p role={mutation.isError ? 'alert' : 'status'}>{status}</p></section><section className="activity-ledger"><h2>What Mechanical Librarian is doing</h2>{records(data.events).map((event, index) => <article key={text(event.work_id, String(index))}><strong>{text(event.work_id, 'Work')} · {text(event.state, 'recorded').replaceAll('_', ' ')}</strong><p>{text(event.library)} · {text(event.destination).replaceAll('_', ' ')}</p></article>)}{records(data.events).length === 0 ? <p>No application outcomes have been recorded yet.</p> : null}</section></div>
  </>
}
