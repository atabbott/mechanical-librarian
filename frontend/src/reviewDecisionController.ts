import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError } from './api/http'
import type { CaseDetailView, DecisionCommand } from './api/http'
import { detailQuery, useReviewContext } from './router'

export const actionable = (field: CaseDetailView['fields'][number]) => field.needsAttention && field.humanDecision === null && field.automaticDecision === null
export const reconciliationKey = (id: string) => ['decision-reconciliation', id] as const
const decisionStatusKey = (id: string) => ['decision-status', id] as const
export interface ReconciliationLock { message: string; detailUpdatedAt: number }
const nextUnresolved = (order: readonly string[], fields: CaseDetailView['fields'], destination: string) => {
  const index = order.indexOf(destination)
  const ordered = index < 0 ? order : [...order.slice(index + 1), ...order.slice(0, index)]
  return ordered.map((name) => fields.find((field) => field.destination === name)).find((field): field is CaseDetailView['fields'][number] => field !== undefined && actionable(field))
}

export const useReviewDecisionController = ({ reviewCaseId, fields, active, onAdvance, onComplete, onStale }: { reviewCaseId: string; fields: CaseDetailView['fields']; active: (command: DecisionCommand) => boolean; onAdvance: (field: string) => Promise<void> | void; onComplete?: () => Promise<void> | void; onStale: () => Promise<void> | void }) => {
  const { http, queryClient } = useReviewContext()
  const [status, setStatus] = useState<{ caseId: string; text: string | null }>({ caseId: reviewCaseId, text: null })
  const [pending, setPending] = useState<{ caseId: string; command: DecisionCommand } | null>(null)
  const reconciliation = useQuery<ReconciliationLock | null>({ queryKey: reconciliationKey(reviewCaseId), queryFn: () => Promise.resolve(null), enabled: false, initialData: null, staleTime: Infinity, gcTime: Infinity })
  const durableStatus = useQuery<string | null>({ queryKey: decisionStatusKey(reviewCaseId), queryFn: () => Promise.resolve(null), enabled: false, initialData: null, staleTime: Infinity, gcTime: Infinity })
  const detailKey = detailQuery(http, reviewCaseId).queryKey
  const detailState = queryClient.getQueryState(detailKey)
  const mounted = useRef(true)
  const order = useRef<readonly string[]>([])
  const settle = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] })]); try { return await queryClient.fetchQuery({ ...detailQuery(http, reviewCaseId), staleTime: 0 }) } catch { return undefined } }
  const lock = (message: string) => queryClient.setQueryData(reconciliationKey(reviewCaseId), { message, detailUpdatedAt: queryClient.getQueryState(detailKey)?.dataUpdatedAt ?? 0 })
  const announce = (caseId: string, text: string) => {
    queryClient.setQueryData(decisionStatusKey(caseId), text)
    setStatus((current) => current.caseId === caseId ? { ...current, text } : current)
  }
  const decision = useMutation({ mutationFn: (command: DecisionCommand) => http.recordDecision(reviewCaseId, command), retry: false, onSuccess: async (_, command) => {
    const refreshed = await settle(); setPending((current) => current?.caseId === reviewCaseId ? null : current)
    if (!refreshed) { lock('Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.'); announce(reviewCaseId, ''); return }
    const next = nextUnresolved(order.current, refreshed.fields, command.destination)
    if (!mounted.current) { queryClient.setQueryData(decisionStatusKey(reviewCaseId), 'Decision recorded from the authoritative case.'); return }
    if (!active(command)) { announce(reviewCaseId, 'Decision recorded from the authoritative case.'); return }
    if (next) { setStatus((current) => current.caseId === reviewCaseId ? { ...current, text: `Decision recorded. Continuing with ${next.destination.replaceAll('_', ' ')}.` } : current); await onAdvance(next.destination); return }
    setStatus((current) => current.caseId === reviewCaseId ? { ...current, text: 'Decision recorded from the authoritative case.' } : current)
    await onComplete?.()
  }, onError: async (error, command) => {
    const refreshed = await settle(); const refreshedField = refreshed?.fields.find((field) => field.destination === command.destination)
    if (refreshedField && !actionable(refreshedField)) setPending((current) => current?.caseId === reviewCaseId ? null : current)
    if (error instanceof ApiError && error.code === 'stale') { setStatus((current) => current.caseId === reviewCaseId ? { ...current, text: 'This case was superseded. Reopen its current replacement from Library.' } : current); await onStale(); return }
    if (!refreshed) { lock(error instanceof AmbiguousMutationError ? 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.' : 'The current decision state could not be reconciled because the case could not be refreshed. Reload before reviewing another value.'); announce(reviewCaseId, ''); return }
    if (error instanceof ApiError && error.code === 'already_decided') { setPending((current) => current?.caseId === reviewCaseId ? null : current); setStatus((current) => current.caseId === reviewCaseId ? { ...current, text: 'The authoritative decision was reloaded.' } : current); return }
    setStatus((current) => current.caseId === reviewCaseId ? { ...current, text: error instanceof AmbiguousMutationError ? 'The response was lost. The current case was reloaded before any retry.' : 'The decision was not recorded. Your choice is unchanged.' } : current)
  } })
  useEffect(() => () => { mounted.current = false }, [])
  useEffect(() => { if (reconciliation.data && (detailState?.dataUpdatedAt ?? 0) > reconciliation.data.detailUpdatedAt) queryClient.setQueryData(reconciliationKey(reviewCaseId), null) }, [detailState?.dataUpdatedAt, queryClient, reconciliation.data, reviewCaseId])
  const currentPending = pending?.caseId === reviewCaseId ? pending.command : null
  const retry = () => {
    if (!currentPending || reconciliation.data !== null || decision.isPending) return
    announce(reviewCaseId, `Retrying ${currentPending.outcome.replaceAll('_', ' ')}…`)
    decision.mutate(currentPending)
  }
  return { status: status.caseId === reviewCaseId ? (status.text ?? durableStatus.data ?? '') : durableStatus.data ?? '', pending: currentPending, isPending: decision.isPending && currentPending !== null, canRetry: decision.isError && currentPending !== null && reconciliation.data === null, retry, reloadRequired: reconciliation.data !== null, message: reconciliation.data?.message, decision, settle, announce: (text: string) => announce(reviewCaseId, text), submit: (command: DecisionCommand) => { order.current = fields.map((field) => field.destination); setPending({ caseId: reviewCaseId, command }); announce(reviewCaseId, `Recording ${command.outcome.replaceAll('_', ' ')}…`); decision.mutate(command) } }
}
