import { invalidateBackgroundQueries } from './queryWork'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError, CsrfRenewedError, refusalCopy } from './api/http'
import type { CaseDetailView, DecisionCommand } from './api/http'
import { labelWords } from './labels'
import {
  beginDecisionEcho,
  confirmDecisionCase,
  endDecisionEcho,
  settleDecisionEcho,
  submitDecisionCase,
} from './decisionEcho'
import { detailQuery, useReviewContext } from './router'

export const actionable = (field: CaseDetailView['fields'][number]) =>
  field.needsAttention &&
  (field.decisionActions.length > 0 ||
    (field.destination === 'cover' && field.editable && field.coverConsideration?.current !== true))
export const choiceAvailable = (field: CaseDetailView['fields'][number]) =>
  field.editable &&
  (field.decisionActions.length > 0 ||
    Boolean(field.descriptionAdvice?.current && field.descriptionAdvice.candidate))
export const reconciliationKey = (id: string) => ['decision-reconciliation', id] as const
// F59's owner commands settle the field in place: the owner stays on it to see
// what was recorded, and only a choice among offered values advances (ADR-0085).
const ownerOutcomes: Readonly<Record<string, string>> = {
  owner_entered: 'Your value is recorded.',
  withdraw: 'Withdrawn. This field needs a decision again.',
  revert: 'The restore is recorded.',
}
const outcomeWords = (outcome: string) =>
  ({ owner_entered: 'your value', withdraw: 'withdrawal', revert: 'restore' })[outcome] ??
  labelWords(outcome)
// The store's refusal for an owner command, in the words the owner acts on.
export const ownerRefusalCopy = (
  error: unknown,
  field: CaseDetailView['fields'][number] | undefined,
): string | undefined => {
  if (!(error instanceof ApiError)) return undefined
  return {
    owner_value_invalid:
      field?.destination === 'genres'
        ? 'These genres can’t be recorded. Choose up to 32 from the list.'
        : 'This value can’t be recorded. Use plain text of up to 512 characters, with no links or file paths.',
    owner_value_unchanged: 'That is already the current value.',
    nothing_to_withdraw: 'There is no recorded decision to withdraw.',
    automatic_hold:
      'This decision is holding automatic handling. Use Resume automatic handling to release it.',
    decision_written:
      field?.ownerControl?.revert?.state === 'available'
        ? 'This decision has been written to your library. You can restore the earlier value instead.'
        : 'This decision has been written to your library, so it can’t be withdrawn.',
    revert_field_changed:
      'This field has changed since it was written, so the earlier value can’t be restored safely.',
    revert_no_previous_value:
      'The field was empty before this write. Clearing a field isn’t available.',
    revert_unavailable: 'This write can’t be reverted.',
  }[error.code]
}
const decisionStatusKey = (id: string) => ['decision-status', id] as const
export type DecisionFailure = 'permanent' | 'retryable' | 'stale' | 'uncertain'
export const classifyDecisionFailure = (error: unknown): DecisionFailure => {
  if (error instanceof AmbiguousMutationError) return 'uncertain'
  if (error instanceof ApiError && error.code === 'stale') return 'stale'
  if (error instanceof CsrfRenewedError) return 'retryable'
  if (
    error instanceof ApiError &&
    error.status === 503 &&
    (error.code === 'persistence' || error.code === 'unavailable')
  )
    return 'retryable'
  return 'permanent'
}
export const retryableDecisionFailure = (error: unknown) =>
  classifyDecisionFailure(error) === 'retryable'
export interface ReconciliationLock {
  destination: string
  message: string
  detailUpdatedAt: number
}
interface DecisionStatus {
  destination: string
  text: string | null
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
  onRecorded,
  onStale,
}: {
  reviewCaseId: string
  fields: CaseDetailView['fields']
  active: (destination: string) => boolean
  onAdvance: (field: string) => Promise<void> | void
  onComplete?: (fields: CaseDetailView['fields']) => Promise<void> | void
  onRecorded?: (caseId: string, destination: string) => Promise<void> | void
  onStale: () => Promise<void> | void
}) => {
  const { http, queryClient } = useReviewContext()
  const [status, setStatus] = useState<{
    caseId: string
    destination: string | null
    text: string | null
  }>({
    caseId: reviewCaseId,
    destination: null,
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
  const durableStatus = useQuery<DecisionStatus | null>({
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
  // Destinations with an in-flight command. Durable authority belongs to the
  // refreshed case; retaining it here would incorrectly block a voluntary
  // later replacement on the same exact current field.
  const decided = useRef<{ caseId: string; destinations: Set<string> }>({
    caseId: reviewCaseId,
    destinations: new Set(),
  })
  const settledFor = (caseId: string) =>
    decided.current.caseId === caseId ? decided.current.destinations : new Set<string>()
  // The exact case, and only the exact case. Next-field authority comes from
  // here; Library counts and the case's own history converge behind it.
  const refreshCase = async () => {
    try {
      return await confirmDecisionCase(queryClient, reviewCaseId, () =>
        queryClient.query({ ...detailQuery(http, reviewCaseId), staleTime: 0 }),
      )
    } catch {
      return undefined
    }
  }
  // The exact case establishes what recorded. Its Library and Attention
  // aggregates must then immediately agree with that authoritative case,
  // rather than retaining an outdated badge until the owner leaves the page.
  const converge = () => {
    void invalidateBackgroundQueries(queryClient, {
      queryKey: ['review-case', reviewCaseId, 'history'],
      exact: true,
    })
  }
  const refreshAggregates = () =>
    Promise.all([
      invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
    ])
  const refreshAggregatesInBackground = () => {
    void refreshAggregates().catch(() => undefined)
  }
  const settle = async () => {
    converge()
    return await refreshCase()
  }
  const lock = (destination: string, message: string) =>
    queryClient.setQueryData(reconciliationKey(reviewCaseId), {
      destination,
      message,
      detailUpdatedAt: queryClient.getQueryState(detailKey)?.dataUpdatedAt ?? 0,
    })
  const announce = (caseId: string, destination: string, text: string) => {
    queryClient.setQueryData(decisionStatusKey(caseId), { destination, text })
    setStatus((current) =>
      current.caseId === caseId ? { ...current, destination, text } : current,
    )
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
    decided.current = { caseId: reviewCaseId, destinations: new Set() }
  }
  const release = (destination: string) => {
    if (decided.current.caseId === reviewCaseId) decided.current.destinations.delete(destination)
  }
  const decision = useMutation({
    mutationFn: (command: DecisionCommand) =>
      submitDecisionCase(queryClient, reviewCaseId, () =>
        http.recordDecision(reviewCaseId, command),
      ),
    retry: false,
    onSuccess: async ({ acknowledgement, confirmed: refreshed }, command) => {
      // What this decision caused, straight from its own response, before
      // anything waits. The echo is published before this response is written,
      // so it may already have arrived and be waiting on exactly this token
      // (#635, #641).
      endDecisionEcho(queryClient, reviewCaseId, acknowledgement.invalidation_sequences)
      converge()
      await onRecorded?.(reviewCaseId, command.destination)
      if (!refreshed) {
        halt()
        forget(command)
        lock(
          command.destination,
          'Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.',
        )
        announce(reviewCaseId, command.destination, '')
        // The optimistic advance is withdrawn: the owner is returned to the
        // field whose outcome is in doubt rather than left on the next one.
        await onAdvance(command.destination)
        return
      }
      release(command.destination)
      if (!refreshed.current) {
        forget(command)
        announce(reviewCaseId, command.destination, '')
        if (isMounted()) await onStale()
        return
      }
      if (!isMounted()) {
        queryClient.setQueryData(decisionStatusKey(reviewCaseId), {
          destination: command.destination,
          text: 'Decision recorded from the authoritative case.',
        })
        refreshAggregatesInBackground()
        return
      }
      mark(command, 'confirmed')
      const settledInPlace = ownerOutcomes[command.outcome]
      announce(
        reviewCaseId,
        command.destination,
        settledInPlace ?? 'Decision recorded from the authoritative case.',
      )
      if (settledInPlace) {
        refreshAggregatesInBackground()
        return
      }
      const next = nextUnresolved(
        order.current,
        refreshed.fields,
        command.destination,
        settledFor(reviewCaseId),
      )
      if (!next) {
        await onComplete?.(refreshed.fields)
        refreshAggregatesInBackground()
        return
      }
      // The acknowledgement and exact-case refresh are the only authority to
      // move the owner. A pending request never changes their field.
      if (active(command.destination)) await onAdvance(next.destination)
      refreshAggregatesInBackground()
    },
    onError: async (error, command) => {
      // Release only this failed field. Another field may still be recording;
      // clearing the whole case would let its repeated click escape its guard.
      endDecisionEcho(queryClient, reviewCaseId, [])
      release(command.destination)
      converge()
      const refreshed = await refreshCase()
      const refreshedField = refreshed?.fields.find(
        (field) => field.destination === command.destination,
      )
      if (refreshedField && !choiceAvailable(refreshedField)) forget(command)
      if (classifyDecisionFailure(error) === 'stale') {
        // Stale authority has no local outcome. Drop this attempt before
        // returning to Library so reopening a current replacement cannot
        // inherit its pressed/recording presentation or destination guard.
        forget(command)
        announce(reviewCaseId, command.destination, '')
        await onStale()
        return
      }
      if (!refreshed) {
        await onAdvance(command.destination)
        lock(
          command.destination,
          classifyDecisionFailure(error) === 'uncertain'
            ? 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.'
            : 'The current decision state could not be reconciled because the case could not be refreshed. Reload before reviewing another value.',
        )
        announce(reviewCaseId, command.destination, '')
        return
      }
      if (error instanceof ApiError && error.code === 'already_decided') {
        forget(command)
        await onAdvance(command.destination)
        setStatus((current) =>
          current.caseId === reviewCaseId
            ? {
                ...current,
                destination: command.destination,
                text: 'The authoritative decision was reloaded.',
              }
            : current,
        )
        return
      }
      if (retryableDecisionFailure(error)) mark(command, 'failed')
      else forget(command)
      announce(
        reviewCaseId,
        command.destination,
        classifyDecisionFailure(error) === 'uncertain'
          ? 'The response was lost. The current case was reloaded before any retry.'
          : refusalCopy(
              error,
              ownerRefusalCopy(error, refreshedField) ??
                'The decision was not recorded. Your choice is unchanged.',
            ),
      )
      // The current field is already authoritative after reconciliation. Do
      // not make truthful failure feedback wait on a no-op same-field route
      // transition; only return from another field when the owner moved away.
      if (!active(command.destination)) await onAdvance(command.destination)
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
  const failed = [...entries].reverse().find((entry) => entry.phase === 'failed') ?? null
  const outstanding = entries.filter((entry) => entry.phase === 'recording')
  const chosen = (destination: string) =>
    [...entries].reverse().find((entry) => entry.command.destination === destination) ?? null
  const retry = () => {
    if (!failed || reconciliation.data !== null) return
    announce(
      reviewCaseId,
      failed.command.destination,
      `Retrying ${outcomeWords(failed.command.outcome)}…`,
    )
    mark(failed.command, 'recording')
    decided.current = {
      caseId: reviewCaseId,
      destinations: new Set([...settledFor(reviewCaseId), failed.command.destination]),
    }
    void decision.mutateAsync(failed.command).catch(() => undefined)
  }
  const submit = (command: DecisionCommand) => {
    if (reconciliation.data !== null) return
    const settled = settledFor(reviewCaseId)
    // Two presses of the same choice, or a second choice for a field whose
    // first is still in flight, are the same decision. Neither becomes a
    // second command.
    if (settled.has(command.destination)) return
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
        ...(current.caseId === reviewCaseId
          ? current.entries.filter((entry) => entry.command.destination !== command.destination)
          : []),
        { command, phase: 'recording' as const },
      ],
    }))
    announce(reviewCaseId, command.destination, `Recording ${outcomeWords(command.outcome)}…`)
    void decision.mutateAsync(command).catch(() => undefined)
  }
  return {
    statusFor: (destination: string) => {
      const local =
        status.caseId === reviewCaseId && status.destination === destination ? status.text : null
      const durable =
        durableStatus.data?.destination === destination ? durableStatus.data.text : null
      return local ?? durable ?? ''
    },
    pending: failed?.command ?? outstanding[0]?.command ?? entries[0]?.command ?? null,
    phase: chosen,
    // Choices remain readable while the exact field records in place. Submit
    // itself rejects any repeated or alternate command for that field.
    recording: outstanding.length > 0,
    canRetry: failed !== null && reconciliation.data === null,
    retry,
    reloadRequired: reconciliation.data !== null,
    messageFor: (destination: string) =>
      reconciliation.data?.destination === destination ? reconciliation.data.message : undefined,
    decision,
    settle,
    announce: (destination: string, text: string) => announce(reviewCaseId, destination, text),
    submit,
  }
}
