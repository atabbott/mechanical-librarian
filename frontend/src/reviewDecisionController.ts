import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError } from './api/http'
import type { CaseDetailView, DecisionCommand } from './api/http'
import { labelWords } from './labels'
import { beginDecisionEcho, endDecisionEcho, settleDecisionEcho } from './decisionEcho'
import { detailQuery, useReviewContext } from './router'

export const actionable = (field: CaseDetailView['fields'][number]) => field.needsAttention
export const reconciliationKey = (id: string) => ['decision-reconciliation', id] as const
const decisionStatusKey = (id: string) => ['decision-status', id] as const
// One case's owner cannot outrun their own reading of the next field by more
// than a handful of choices, and an unbounded backlog would be a durable queue
// in all but name. Beyond this the surface says so rather than accepting a
// choice it has not started.
export const outstandingDecisionLimit = 8
export interface ReconciliationLock {
  message: string
  detailUpdatedAt: number
}
const nextUnresolved = (
  order: readonly string[],
  fields: CaseDetailView['fields'],
  destination: string,
  settled: ReadonlySet<string>,
) => {
  const index = order.indexOf(destination)
  const ordered = index < 0 ? order : [...order.slice(index + 1), ...order.slice(0, index)]
  return ordered
    .filter((name) => !settled.has(name))
    .map((name) => fields.find((field) => field.destination === name))
    .find(
      (field): field is CaseDetailView['fields'][number] =>
        field !== undefined && actionable(field),
    )
}
// One idempotency key per decision attempt, so a retry of the same command is
// recognised as the same command rather than recorded twice. Lives beside the
// controller that puts it on a `DecisionCommand`; both routes import it.
export const requestKey = () =>
  typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `decision-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
const sameCommand = (left: DecisionCommand, right: DecisionCommand) =>
  left.idempotencyKey === right.idempotencyKey

export const useReviewDecisionController = ({
  reviewCaseId,
  fields,
  active,
  onAdvance,
  onComplete,
  onStale,
}: {
  reviewCaseId: string
  fields: CaseDetailView['fields']
  active: (destination: string) => boolean
  onAdvance: (field: string) => Promise<void> | void
  onComplete?: (fields: CaseDetailView['fields']) => Promise<void> | void
  onStale: () => Promise<void> | void
}) => {
  const { http, queryClient } = useReviewContext()
  const [status, setStatus] = useState<{ caseId: string; text: string | null }>({
    caseId: reviewCaseId,
    text: null,
  })
  // Every choice the owner has made on this case that the server has not yet
  // confirmed or refused. It is what makes a choice readable in place the
  // instant it is made, and what a later field's arrival must not erase.
  const [acknowledged, setAcknowledged] = useState<{
    caseId: string
    entries: readonly {
      command: DecisionCommand
      phase: 'recording' | 'confirmed' | 'failed'
    }[]
  }>({ caseId: reviewCaseId, entries: [] })
  const reconciliation = useQuery<ReconciliationLock | null>({
    queryKey: reconciliationKey(reviewCaseId),
    queryFn: () => Promise.resolve(null),
    enabled: false,
    initialData: null,
    staleTime: Infinity,
    gcTime: Infinity,
  })
  const durableStatus = useQuery<string | null>({
    queryKey: decisionStatusKey(reviewCaseId),
    queryFn: () => Promise.resolve(null),
    enabled: false,
    initialData: null,
    staleTime: Infinity,
    gcTime: Infinity,
  })
  const detailKey = detailQuery(http, reviewCaseId).queryKey
  const detailState = queryClient.getQueryState(detailKey)
  const mounted = useRef(true)
  const isMounted = () => mounted.current
  const order = useRef<readonly string[]>([])
  // Destinations this case has already been given a choice for. Advancement
  // reads it so a decision still in flight is never offered again, and so the
  // next field is chosen from what the owner has done rather than from what the
  // server has finished writing.
  const decided = useRef<{ caseId: string; destinations: Set<string> }>({
    caseId: reviewCaseId,
    destinations: new Set(),
  })
  const settledFor = (caseId: string) =>
    decided.current.caseId === caseId ? decided.current.destinations : new Set<string>()
  // Commands leave in the order they were chosen. Each one waits only for the
  // request before it, never for a Library projection, so a second choice can
  // be made while the first settles without the two arriving out of order.
  const chain = useRef<{ caseId: string; tail: Promise<unknown> }>({
    caseId: reviewCaseId,
    tail: Promise.resolve(),
  })
  // Where each command sent the owner. The optimistic advance reads the field
  // list in hand; if the authoritative case then says that field no longer
  // needs them, this is what lets the correction be made without having made
  // them wait for it in the first place.
  const advanced = useRef(new Map<string, string>())
  // The exact case, and only the exact case. Next-field authority comes from
  // here; Library counts and the case's own history converge behind it.
  const refreshCase = async () => {
    try {
      return await queryClient.fetchQuery({ ...detailQuery(http, reviewCaseId), staleTime: 0 })
    } catch {
      return undefined
    }
  }
  // The Library and the attention rail are not rebuilt from here. A decision
  // announces its own row over the stream, and this tab recognises that
  // announcement as its own and settles it when the owner leaves the case, so
  // a client-side rebuild would be a second, subjectless copy of an
  // invalidation that already arrived -- and the whole-Library read #635
  // removed. This case's own history has no announcement and stays.
  const converge = () => {
    void queryClient.invalidateQueries({
      queryKey: ['review-case', reviewCaseId, 'history'],
      exact: true,
    })
  }
  const settle = async () => {
    converge()
    return await refreshCase()
  }
  const lock = (message: string) =>
    queryClient.setQueryData(reconciliationKey(reviewCaseId), {
      message,
      detailUpdatedAt: queryClient.getQueryState(detailKey)?.dataUpdatedAt ?? 0,
    })
  const announce = (caseId: string, text: string) => {
    queryClient.setQueryData(decisionStatusKey(caseId), text)
    setStatus((current) => (current.caseId === caseId ? { ...current, text } : current))
  }
  const forget = (command: DecisionCommand) =>
    setAcknowledged((current) =>
      current.caseId === reviewCaseId
        ? {
            ...current,
            entries: current.entries.filter((entry) => !sameCommand(entry.command, command)),
          }
        : current,
    )
  const mark = (command: DecisionCommand, phase: 'recording' | 'confirmed' | 'failed') =>
    setAcknowledged((current) =>
      current.caseId === reviewCaseId
        ? {
            ...current,
            entries: current.entries.map((entry) =>
              sameCommand(entry.command, command) ? { command, phase } : entry,
            ),
          }
        : current,
    )
  // A refusal stops this case advancing any further on its own. Queued choices
  // are dropped rather than resent blindly, and the owner is left with one
  // focused action over an authoritative case.
  const halt = () => {
    chain.current = { caseId: reviewCaseId, tail: Promise.resolve() }
    decided.current = { caseId: reviewCaseId, destinations: new Set() }
  }
  const decision = useMutation({
    mutationFn: (command: DecisionCommand) => http.recordDecision(reviewCaseId, command),
    retry: false,
    onSuccess: async (acknowledgement, command) => {
      // What this decision caused, straight from its own response, before
      // anything waits. The echo is published before this response is written,
      // so it may already have arrived and be waiting on exactly this token
      // (#635, #641).
      endDecisionEcho(queryClient, reviewCaseId, acknowledgement.invalidation_sequences)
      converge()
      const refreshed = await refreshCase()
      if (!refreshed) {
        halt()
        forget(command)
        lock(
          'Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.',
        )
        announce(reviewCaseId, '')
        // The optimistic advance is withdrawn: the owner is returned to the
        // field whose outcome is in doubt rather than left on the next one.
        await onAdvance(command.destination)
        return
      }
      if (!isMounted()) {
        queryClient.setQueryData(
          decisionStatusKey(reviewCaseId),
          'Decision recorded from the authoritative case.',
        )
        return
      }
      mark(command, 'confirmed')
      announce(reviewCaseId, 'Decision recorded from the authoritative case.')
      const next = nextUnresolved(
        order.current,
        refreshed.fields,
        command.destination,
        settledFor(reviewCaseId),
      )
      const arrival = advanced.current.get(command.idempotencyKey)
      advanced.current.delete(command.idempotencyKey)
      if (!next) {
        await onComplete?.(refreshed.fields)
        return
      }
      if (
        arrival === undefined ||
        arrival === next.destination ||
        !(active(arrival) || active(command.destination))
      )
        return
      const settled = refreshed.fields.find((field) => field.destination === arrival)
      if (settled !== undefined && !actionable(settled)) await onAdvance(next.destination)
    },
    onError: async (error, command) => {
      // No token is coming. Anything held on this decision's account belongs to
      // someone else and is released by the same call.
      endDecisionEcho(queryClient, reviewCaseId, [])
      halt()
      converge()
      const refreshed = await refreshCase()
      const refreshedField = refreshed?.fields.find(
        (field) => field.destination === command.destination,
      )
      if (refreshedField && !actionable(refreshedField)) forget(command)
      if (error instanceof ApiError && error.code === 'stale') {
        setStatus((current) =>
          current.caseId === reviewCaseId
            ? {
                ...current,
                text: 'This case was superseded. Reopen its current replacement from Library.',
              }
            : current,
        )
        await onStale()
        return
      }
      if (!refreshed) {
        mark(command, 'failed')
        await onAdvance(command.destination)
        lock(
          error instanceof AmbiguousMutationError
            ? 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.'
            : 'The current decision state could not be reconciled because the case could not be refreshed. Reload before reviewing another value.',
        )
        announce(reviewCaseId, '')
        return
      }
      if (error instanceof ApiError && error.code === 'already_decided') {
        forget(command)
        await onAdvance(command.destination)
        setStatus((current) =>
          current.caseId === reviewCaseId
            ? { ...current, text: 'The authoritative decision was reloaded.' }
            : current,
        )
        return
      }
      mark(command, 'failed')
      await onAdvance(command.destination)
      setStatus((current) =>
        current.caseId === reviewCaseId
          ? {
              ...current,
              text:
                error instanceof AmbiguousMutationError
                  ? 'The response was lost. The current case was reloaded before any retry.'
                  : 'The decision was not recorded. Your choice is unchanged.',
            }
          : current,
      )
    },
  })
  useEffect(
    () => () => {
      mounted.current = false
    },
    [],
  )
  // Leaving the case -- for another case, for the Library, or for another route
  // entirely -- is when the echoes recognised inside it are paid, which is what
  // makes the row behind the owner correct when they return to it.
  useEffect(() => () => settleDecisionEcho(queryClient), [queryClient, reviewCaseId])
  useEffect(() => {
    if (
      reconciliation.data &&
      (detailState?.dataUpdatedAt ?? 0) > reconciliation.data.detailUpdatedAt
    )
      queryClient.setQueryData(reconciliationKey(reviewCaseId), null)
  }, [detailState?.dataUpdatedAt, queryClient, reconciliation.data, reviewCaseId])
  const entries = acknowledged.caseId === reviewCaseId ? acknowledged.entries : []
  const failed = entries.find((entry) => entry.phase === 'failed') ?? null
  const outstanding = entries.filter((entry) => entry.phase === 'recording')
  const chosen = (destination: string) =>
    entries.find((entry) => entry.command.destination === destination) ?? null
  const retry = () => {
    if (!failed || reconciliation.data !== null) return
    announce(reviewCaseId, `Retrying ${labelWords(failed.command.outcome)}…`)
    mark(failed.command, 'recording')
    decided.current = {
      caseId: reviewCaseId,
      destinations: new Set([...settledFor(reviewCaseId), failed.command.destination]),
    }
    chain.current = { caseId: reviewCaseId, tail: decision.mutateAsync(failed.command) }
  }
  const submit = (command: DecisionCommand) => {
    if (reconciliation.data !== null) return
    const settled = settledFor(reviewCaseId)
    // Two presses of the same choice, or a second choice for a field whose
    // first is still in flight, are the same decision. Neither becomes a
    // second command.
    if (settled.has(command.destination)) return
    if (outstanding.length >= outstandingDecisionLimit) {
      announce(
        reviewCaseId,
        'Earlier choices are still being recorded. This one was not sent; try it again in a moment.',
      )
      return
    }
    // From here a Library frame naming this case might be this decision's own
    // echo, and the response that would say so is still in transit. Marked
    // before the request leaves, so no ordering between the two can be raced.
    beginDecisionEcho(queryClient, reviewCaseId)
    order.current = fields.map((field) => field.destination)
    decided.current = {
      caseId: reviewCaseId,
      destinations: new Set([...settled, command.destination]),
    }
    setAcknowledged((current) => ({
      caseId: reviewCaseId,
      entries: [
        ...(current.caseId === reviewCaseId ? current.entries : []),
        { command, phase: 'recording' as const },
      ],
    }))
    // The advance happens now, from the field list already in hand. Nothing
    // about it waits on the network, and nothing about it waits on a Library
    // projection; the recording below reconciles behind it.
    const next = nextUnresolved(
      order.current,
      fields,
      command.destination,
      decided.current.destinations,
    )
    announce(
      reviewCaseId,
      next
        ? `Recording ${labelWords(command.outcome)}. Continuing with ${labelWords(next.destination)}.`
        : `Recording ${labelWords(command.outcome)}…`,
    )
    if (next && active(command.destination)) {
      advanced.current.set(command.idempotencyKey, next.destination)
      void onAdvance(next.destination)
    }
    const previous = chain.current.caseId === reviewCaseId ? chain.current.tail : Promise.resolve()
    chain.current = {
      caseId: reviewCaseId,
      tail: previous.then(
        () => decision.mutateAsync(command).catch(() => undefined),
        () => undefined,
      ),
    }
  }
  return {
    status:
      status.caseId === reviewCaseId
        ? (status.text ?? durableStatus.data ?? '')
        : (durableStatus.data ?? ''),
    pending: failed?.command ?? outstanding[0]?.command ?? entries[0]?.command ?? null,
    phase: chosen,
    // Choices stay live while a decision settles: the surface acknowledges in
    // place and moves on, so nothing is disabled waiting for a round trip.
    // What a caller may still want to know is whether anything is in flight.
    recording: outstanding.length > 0,
    canRetry: failed !== null && reconciliation.data === null,
    retry,
    reloadRequired: reconciliation.data !== null,
    message: reconciliation.data?.message,
    decision,
    settle,
    announce: (text: string) => announce(reviewCaseId, text),
    submit,
  }
}
