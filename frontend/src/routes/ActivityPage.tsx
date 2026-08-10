import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { AmbiguousMutationError } from '../api/http'
import type { ActivityView } from '../api/http'
import { activityEventKeys, activityOutcome } from '../activityState'
import { useRouteReadyFocus } from '../focus'
import { useReviewContext } from '../router'

const records = (value: unknown): readonly Record<string, unknown>[] => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && !Array.isArray(item)) : []
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0
const capabilityDestinations = (capability: Record<string, unknown> | undefined) => records(capability?.destinations).map((destination) => ({ destination: text(destination.destination), state: text(destination.state, 'not_ready'), consented: destination.consented === true }))
const destinationLabel = (destination: string) => destination.replaceAll('_', ' ')
const originLabel = (origin: unknown) => {
  const value = text(origin)
  if (value === 'automatic_policy') return 'Qualified automatic'
  if (value === 'human') return 'Human decision'
  const neutral = value.replaceAll('_', ' ').trim().slice(0, 64)
  return neutral ? `Recorded source: ${neutral}` : 'Recorded source'
}
export default function ActivityPage({ initial }: { initial: ActivityView }) {
  const { http, queryClient } = useReviewContext()
  useRouteReadyFocus('activity')
  const query = useQuery({ queryKey: ['activity'], queryFn: ({ signal }) => http.getActivity(signal), initialData: initial, staleTime: 4_000, retry: false, refetchInterval: 5_000 })
  const data = query.data
  const configured = records(data.configured_libraries)
  const enabled = records(data.enabled_libraries)
  const capabilities = records(data.capabilities)
  const capability = (library: string) => capabilities.find((item) => text(item.library) === library)
  const selectable = configured.filter((item) => capabilityDestinations(capability(text(item.library))).some((destination) => destination.state === 'allowed' && !destination.consented))
  const selectableNames = new Set(selectable.map((item) => text(item.library)))
  const [selection, setSelection] = useState(() => ({ source: data, names: new Set<string>() }))
  if (selection.source !== data) {
    setSelection({ source: data, names: new Set([...selection.names].filter((name) => selectableNames.has(name))) })
  }
  const selected = selection.source === data ? selection.names : new Set([...selection.names].filter((name) => selectableNames.has(name)))
  const [status, setStatus] = useState('')
  const mutation = useMutation({ mutationFn: ({ path, body }: { path: string; body?: unknown }) => http.activityCommand(path, body), retry: false, onSuccess: async () => { setSelection({ source: data, names: new Set() }); setStatus('Activity state refreshed from the authoritative server.'); await queryClient.invalidateQueries({ queryKey: ['activity'] }) }, onError: async (error, command) => { if (error instanceof AmbiguousMutationError) { try { const refreshed = await query.refetch(); const outcome = refreshed.isError ? 'unknown' : activityOutcome(refreshed.data, command); setStatus(outcome === 'applied' ? 'The requested activity change was applied, confirmed by the authoritative server.' : 'The activity outcome could not be confirmed. No command was resent.') } catch { setStatus('The activity outcome could not be confirmed. No command was resent.') }; return } setStatus('The requested activity change failed safely. Try again.'); await queryClient.invalidateQueries({ queryKey: ['activity'] }) } })
  const paused = data.paused === true
  const backupReady = data.backup_ready === true
  const capabilityState = (library: string) => text(capability(library)?.state, 'not_ready')
  const selectedGrants = selectable.flatMap((library) => {
    const name = text(library.library)
    if (!selected.has(name)) return []
    return [{ library: name, destinations: capabilityDestinations(capability(name)).filter((destination) => destination.state === 'allowed').map((destination) => destination.destination) }]
  })
  const toggle = (library: string) => { const next = new Set(selected); if (next.has(library)) next.delete(library); else next.add(library); setSelection({ source: data, names: next }) }
  const eventKeys = activityEventKeys(records(data.events))
  return <>
    <header className="page-heading" tabIndex={-1} data-route-focus><div><p className="eyebrow">Guarded application</p><h1>{enabled.length ? 'Activity' : 'Ready when you are'}</h1><p>{enabled.length ? 'A readable account of outcomes and exceptions.' : 'Review remains useful in read-only mode. Explicit consent enables supported writes.'}</p></div></header>
    <div className="activity-layout"><section className="activity-controls"><h2>{enabled.length ? paused ? 'New work paused' : 'Applying safely' : 'Enable guarded writes'}</h2>{configured.length ? <fieldset><legend>Prepared libraries</legend>{configured.map((library) => { const name = text(library.library); const state = capabilityState(name); const destinations = capabilityDestinations(capability(name)); const prepared = typeof library.prepared === 'object' && library.prepared ? count((library.prepared as Record<string, unknown>).total) : 0; const disabled = !selectableNames.has(name) || !['allowed', 'partially_allowed'].includes(state) || prepared === 0; return <div key={name}><label><input type="checkbox" value={name} disabled={disabled} checked={selected.has(name)} onChange={() => toggle(name)}/>{name} · {prepared} prepared{disabled ? ` · ${state.replaceAll('_', ' ')}` : ''}</label><ul aria-label={`${name} destination consent`}>
      {destinations.map((destination) => <li key={destination.destination}><span>{destinationLabel(destination.destination)}</span>: {destination.state === 'allowed' ? `Included${destination.consented ? ' · already consented' : ' · consent requested'}` : `Excluded · ${destination.state.replaceAll('_', ' ')}`}</li>)}
    </ul></div> })}</fieldset> : null}<button disabled={mutation.isPending || !backupReady || selectedGrants.length === 0} onClick={() => mutation.mutate({ path: 'activity/consent', body: { grants: selectedGrants } })}>{enabled.length ? 'Enable selected libraries' : 'Enable guarded writes'}</button>{enabled.length ? <button disabled={mutation.isPending} onClick={() => mutation.mutate({ path: `field-effects/${paused ? 'resume' : 'pause'}` })}>{paused ? 'Resume new work' : 'Pause new work'}</button> : null}{enabled.map((library) => <button key={`${text(library.connection)}:${text(library.library)}`} disabled={mutation.isPending} onClick={() => mutation.mutate({ path: `activity/${encodeURIComponent(text(library.connection))}/${encodeURIComponent(text(library.library))}/revoke` })}>Remove write permission for {text(library.library)}</button>)}<p role={mutation.isError ? 'alert' : 'status'}>{status}</p></section><section className="activity-ledger"><h2>What Mechanical Librarian is doing</h2>{records(data.events).map((event, index) => <article key={eventKeys[index]}><strong>{text(event.work_id, 'Work')} · {text(event.state, 'recorded').replaceAll('_', ' ')}</strong><p>{text(event.library)} · {text(event.destination).replaceAll('_', ' ')} · {originLabel(event.origin)}</p></article>)}{records(data.events).length === 0 ? <p>No application outcomes have been recorded yet.</p> : null}</section></div>
  </>
}
