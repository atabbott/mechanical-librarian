import { invalidateBackgroundQueries } from './queryWork'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import {
  AmbiguousMutationError,
  ApiError,
  CsrfRenewedError,
  PRE_CALL_SERVER_CODES,
  isClear,
  refusalCopy,
} from './api/http'
import type { CaseDetailView, DecisionCommand, DecisionOutcome } from './api/http'
import { STILL_PINNED } from './fieldPin'
import { destinationPhrase } from './labels'
import {
  beginDecisionEcho,
  confirmDecisionCase,
  endDecisionEcho,
  settleDecisionEcho,
  submitDecisionCase,
} from './decisionEcho'
import { detailQuery, useReviewContext } from './router'

// A changed pin asks even where no source offers a value: the pin itself is
// what the owner settles (ADR-0085, Amendment 2).
export const actionable = (field: CaseDetailView['fields'][number]) =>
  field.needsAttention &&
  (field.decisionActions.length > 0 ||
    field.pin?.state === 'changed' ||
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
  pin: 'Pinned.',
  unpin: 'Unpinned.',
}
const outcomeWords = (outcome: DecisionOutcome) =>
  (
    ({
      accept: 'your choice',
      keep_current: 'your choice',
      reject: 'your choice',
      owner_entered: 'your value',
      withdraw: 'the withdrawal',
      pin: 'the pin',
      unpin: 'the unpin',
    }) satisfies Record<DecisionOutcome, string>
  )[outcome]
const progressWords = (outcome: DecisionOutcome) =>
  outcome === 'pin'
    ? 'Pinning…'
    : outcome === 'unpin'
      ? 'Unpinning…'
      : `Recording ${outcomeWords(outcome)}…`
// One press of a value's pin sends the value command, then the pin (ADR-0085,
// Amendment 2). The pin binds the same case, proposal and offer under a key of
// its own.
const pinFollowing = (command: DecisionCommand): DecisionCommand => ({
  destination: command.destination,
  outcome: 'pin',
  proposalId: command.proposalId,
  selectedValue: null,
  targetFingerprint: command.targetFingerprint,
  offerDigest: command.offerDigest,
  idempotencyKey: requestKey(),
})
// When a choice is recorded and its pin is not, the page says so: the choice
// stands, and the pin is never dropped silently.
const pinNotRecorded = (field: CaseDetailView['fields'][number] | undefined) =>
  field?.pinControl?.pin === 'available'
    ? 'Your choice is recorded, but the pin was not. Press the pin again.'
    : 'Your choice is recorded, but the pin was not.'
// After a pin or an unpin fails, the refreshed case says whether it stands:
// a repeat the store refused as already done reads as done.
const pinStands = (
  outcome: DecisionOutcome,
  field: CaseDetailView['fields'][number] | undefined,
) => {
  if (!field) return false
  return outcome === 'pin'
    ? field.pin !== null && field.pin.state !== 'changed'
    : field.pin === null
}
const pinFailureCopy = (
  command: DecisionCommand,
  field: CaseDetailView['fields'][number] | undefined,
  followed: boolean,
) =>
  command.outcome === 'unpin'
    ? STILL_PINNED
    : followed
      ? pinNotRecorded(field)
      : field?.pinControl?.pin === 'available'
        ? 'The pin was not recorded. Press the pin again.'
        : 'The pin was not recorded.'
// One sentence for a written decision, shared by the refusal and the note.
export const writtenRefusal =
  'This decision has been written to your library, so it can’t be withdrawn.'
// A choice refused because the work's book link changed since its case was
// prepared (#1477, Q6): nothing is recorded until a preparation uses the link.
// While one could not finish, the work's own line says why and what to do
// instead (`unusedLinkNotice`, #1941).
export const linkChangedNotice =
  'Not recorded. The book link changed. Choose again when preparation has used the link.'
export const unusedLinkNotice = (line: string) => `Not recorded. ${line}`
export const linkChanged = (error: unknown) =>
  error instanceof ApiError && error.code === 'link_changed'
// The store's refusal for an owner command, in the words the owner acts on.
export const ownerRefusalCopy = (
  error: unknown,
  field: CaseDetailView['fields'][number] | undefined,
  command?: DecisionCommand,
): string | undefined => {
  if (!(error instanceof ApiError)) return undefined
  const names = field?.destination === 'authors' || field?.destination === 'narrators'
  return {
    // Keep current on the case that wrote a field it read empty would empty
    // a field that can't be (#1523).
    owner_value_required:
      command?.outcome === 'keep_current' && field
        ? `The ${destinationPhrase(field.destination)} can’t be emptied. Choose a new value.`
        : field?.destination === 'authors'
          ? 'Enter at least one author.'
          : 'Enter a title.',
    owner_value_invalid:
      field?.destination === 'genres'
        ? 'These genres can’t be recorded. Choose up to 32 from the list.'
        : names
          ? 'These names can’t be recorded. Enter up to 32 different names, one per line, with no links or file paths.'
          : 'This value can’t be recorded. Use plain text of up to 512 characters, with no links or file paths.',
    owner_value_unchanged: isClear(command?.selectedValue)
      ? 'This field is already empty.'
      : names
        ? 'Those are already the current names.'
        : field?.destination === 'genres'
          ? 'Those are already the current genres.'
          : 'That is already the current value.',
    nothing_to_withdraw: 'No decision is recorded for this field.',
    automatic_hold:
      'This decision is holding automatic handling. Use Resume automatic handling to release it.',
    decision_written: writtenRefusal,
    pinned: 'This field is pinned. Unpin it to withdraw the decision.',
    link_changed: linkChangedNotice,
  }[error.code]
}
// A choice refused because Book matching changed (#1355): the field's offer
// changed since the page showed it, so it asks again in place; or the work
// waits for its check, which its case line says, so the field adds nothing.
export const offerChangedNotice =
  'Not recorded: Book matching changed what this field offers. Choose again.'
export const offerChangedCopy = (error: unknown): string | undefined => {
  if (!(error instanceof ApiError) || error.code !== 'offer_changed') return undefined
  return error.reason === 'digest_mismatch' ? offerChangedNotice : ''
}
const decisionStatusKey = (id: string) => ['decision-status', id] as const
export type DecisionFailure = 'permanent' | 'retryable' | 'stale' | 'uncertain'
export const classifyDecisionFailure = (error: unknown): DecisionFailure => {
  if (error instanceof AmbiguousMutationError) return 'uncertain'
  if (error instanceof ApiError && error.code === 'stale') return 'stale'
  if (error instanceof CsrfRenewedError) return 'retryable'
  // A server failure may follow the commit; only a code proven to precede the
  // handler changed nothing. The same-key retry is a safe replay either way.
  if (error instanceof ApiError && error.status >= 500 && !PRE_CALL_SERVER_CODES.has(error.code))
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
  checking = false,
  active,
  onAdvance,
  onComplete,
  onRecorded,
  onStale,
  linkNotice = linkChangedNotice,
}: {
  reviewCaseId: string
  fields: CaseDetailView['fields']
  // The case waits for its Book matching check; no choice is sent meanwhile.
  checking?: boolean
  active: (destination: string) => boolean
  onAdvance: (field: string) => Promise<void> | void
  onComplete?: (fields: CaseDetailView['fields']) => Promise<void> | void
  onRecorded?: (caseId: string, destination: string) => Promise<void> | void
  onStale: (command: DecisionCommand) => Promise<void> | void
  // What a choice refused because the book link changed says, for this work.
  linkNotice?: string
}) => {
  const { http, queryClient } = useReviewContext()
  const [status, setStatus] = useState<{
    caseId: string
    destination: string | null
    text: string | null
    alert: boolean
  }>({
    caseId: reviewCaseId,
    destination: null,
    text: null,
    alert: false,
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
  // The field whose choice was recorded on a case that could not be reloaded,
  // so the pin that was to follow it was never sent. It is named once, on
  // that case, when the case is next read, and not after a later command.
  const unsent = useRef<{ caseId: string; destination: string } | null>(null)
  const nameUnsent = (refreshed: CaseDetailView | undefined) => {
    const pin = unsent.current
    if (!refreshed || pin?.caseId !== reviewCaseId) return
    unsent.current = null
    const field = refreshed.fields.find((item) => item.destination === pin.destination)
    if (field?.pin == null) announce(reviewCaseId, pin.destination, pinNotRecorded(field), true)
  }
  const settle = async () => {
    converge()
    const refreshed = await refreshCase()
    nameUnsent(refreshed)
    return refreshed
  }
  // A field in the No change panel has no tab of its own to return to.
  const returnTo = (destination: string) =>
    fields.find((field) => field.destination === destination)?.inventoryRole === 'contextual'
      ? undefined
      : onAdvance(destination)
  const lock = (destination: string, message: string) =>
    queryClient.setQueryData(reconciliationKey(reviewCaseId), {
      destination,
      message,
      detailUpdatedAt: queryClient.getQueryState(detailKey)?.dataUpdatedAt ?? 0,
    })
  const announce = (caseId: string, destination: string, text: string, alert = false) => {
    queryClient.setQueryData(decisionStatusKey(caseId), { destination, text })
    setStatus((current) =>
      current.caseId === caseId ? { ...current, destination, text, alert } : current,
    )
  }
  // The pin that follows a value command, by the value command's key. It
  // stays with that command through a retry and goes when the command does.
  const following = useRef(new Map<string, DecisionCommand>())
  const followed = useRef(new Set<string>())
  const taken = useRef<DecisionCommand | null>(null)
  const forget = (command: DecisionCommand) => {
    following.current.delete(command.idempotencyKey)
    followed.current.delete(command.idempotencyKey)
    setAcknowledged((current) =>
      current.caseId === reviewCaseId
        ? {
            ...current,
            entries: current.entries.filter((entry) => !sameCommand(entry.command, command)),
          }
        : current,
    )
  }
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
      const pin = following.current.get(command.idempotencyKey)
      if (!refreshed) {
        halt()
        forget(command)
        // The pin that was to follow is named once the case can be read again.
        unsent.current = pin ? { caseId: reviewCaseId, destination: command.destination } : null
        lock(
          command.destination,
          'Your choice was recorded, but this work could not be reloaded. Choose Reload current case before choosing again.',
        )
        announce(reviewCaseId, command.destination, '')
        // The optimistic advance is withdrawn: the owner is returned to the
        // field whose outcome is in doubt rather than left on the next one.
        await returnTo(command.destination)
        return
      }
      release(command.destination)
      if (!refreshed.current) {
        forget(command)
        announce(reviewCaseId, command.destination, '')
        // The pin that was to follow is what the owner is told was not recorded.
        if (isMounted()) await onStale(pin ?? command)
        return
      }
      if (!isMounted()) {
        queryClient.setQueryData(decisionStatusKey(reviewCaseId), {
          destination: command.destination,
          text: pin ? pinNotRecorded(undefined) : 'Your choice is recorded.',
        })
        refreshAggregatesInBackground()
        return
      }
      mark(command, 'confirmed')
      if (pin) {
        following.current.delete(command.idempotencyKey)
        const field = refreshed.fields.find((item) => item.destination === command.destination)
        // The pin is sent only on the case the owner is still on, and only
        // where the refreshed field still offers one.
        const sent =
          refreshed.id === reviewCaseId && field?.pinControl?.pin === 'available' && submit(pin)
        if (sent) followed.current.add(pin.idempotencyKey)
        else announce(refreshed.id, command.destination, pinNotRecorded(field), true)
        refreshAggregatesInBackground()
        return
      }
      const settledInPlace = ownerOutcomes[command.outcome]
      // A clear's recorded line says it once, beside its pin (#1523).
      announce(
        reviewCaseId,
        command.destination,
        isClear(command.selectedValue) ? '' : (settledInPlace ?? 'Your choice is recorded.'),
      )
      followed.current.delete(command.idempotencyKey)
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
      const pin = following.current.get(command.idempotencyKey)
      // Read before anything forgets the command: a pin that followed a
      // recorded choice says so when it fails.
      const wasFollowing = followed.current.has(command.idempotencyKey)
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
        await onStale(pin ?? command)
        return
      }
      if (!refreshed) {
        // The reloaded case is what says whether it was recorded; left
        // recording, the command would keep every pin busy after Reload.
        forget(command)
        await returnTo(command.destination)
        lock(
          command.destination,
          classifyDecisionFailure(error) === 'uncertain'
            ? 'No answer came back. Choose Reload current case, then check whether your choice was recorded before choosing again.'
            : 'This work could not be reloaded after your choice. Choose Reload current case before choosing again.',
        )
        announce(reviewCaseId, command.destination, '')
        return
      }
      const offerChanged = offerChangedCopy(error)
      if (offerChanged !== undefined) {
        // The case above is already the refreshed one: the field shows what it
        // offers now, or the case shows it is being checked. Nothing to retry.
        forget(command)
        announce(reviewCaseId, command.destination, offerChanged, offerChanged !== '')
        if (!active(command.destination)) await returnTo(command.destination)
        return
      }
      if (linkChanged(error)) {
        // Not retried: the case is prepared again with the new link.
        forget(command)
        announce(reviewCaseId, command.destination, linkNotice, true)
        if (!active(command.destination)) await returnTo(command.destination)
        return
      }
      if (command.outcome === 'pin' || command.outcome === 'unpin') {
        // Never retried: the refreshed case says whether the pin stands.
        forget(command)
        if (pinStands(command.outcome, refreshedField))
          announce(reviewCaseId, command.destination, ownerOutcomes[command.outcome] ?? '')
        else
          announce(
            reviewCaseId,
            command.destination,
            pinFailureCopy(command, refreshedField, wasFollowing),
            true,
          )
        if (!active(command.destination)) await returnTo(command.destination)
        return
      }
      if (error instanceof ApiError && error.code === 'already_decided') {
        forget(command)
        await returnTo(command.destination)
        setStatus((current) =>
          current.caseId === reviewCaseId
            ? {
                ...current,
                destination: command.destination,
                text: 'This field was already decided. The page shows that decision.',
                alert: true,
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
          ? 'No answer came back. Check whether your choice was recorded before choosing again.'
          : retryableDecisionFailure(error) && !(error instanceof CsrfRenewedError)
            ? 'Your choice could not be confirmed. Choose Try the same decision again to send it once more.'
            : refusalCopy(
                error,
                ownerRefusalCopy(error, refreshedField, command) ??
                  'Your choice was not recorded. Choose again.',
              ),
        true,
      )
      // The current field is already authoritative after reconciliation. Do
      // not make truthful failure feedback wait on a no-op same-field route
      // transition; only return from another field when the owner moved away.
      if (!active(command.destination)) await returnTo(command.destination)
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
    ) {
      queryClient.setQueryData(reconciliationKey(reviewCaseId), null)
      // A read that lifts the lock without Reload names the pin as well.
      nameUnsent(queryClient.getQueryData(detailKey))
    }
  })
  // A read newer than the lock lifts it at once, before the effect above
  // clears it, so no render says the work could not be reloaded after it was.
  const held =
    reconciliation.data && (detailState?.dataUpdatedAt ?? 0) <= reconciliation.data.detailUpdatedAt
      ? reconciliation.data
      : null
  const entries = acknowledged.caseId === reviewCaseId ? acknowledged.entries : []
  const failed = [...entries].reverse().find((entry) => entry.phase === 'failed') ?? null
  const outstanding = entries.filter((entry) => entry.phase === 'recording')
  const chosen = (destination: string) =>
    [...entries].reverse().find((entry) => entry.command.destination === destination) ?? null
  const retry = () => {
    if (!failed || held !== null) return
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
    if (held !== null || checking) return false
    const settled = settledFor(reviewCaseId)
    // Two presses of the same choice, or a second choice for a field whose
    // first is still in flight, are the same decision. Neither becomes a
    // second command.
    if (settled.has(command.destination)) return false
    unsent.current = null
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
    announce(reviewCaseId, command.destination, progressWords(command.outcome))
    taken.current = command
    void decision.mutateAsync(command).catch(() => undefined)
    return true
  }
  // The pin to send once the value command with this key is recorded. Nothing
  // is registered for a command the controller did not take.
  const pinAfter = (idempotencyKey: string) => {
    const value = taken.current
    if (value?.idempotencyKey !== idempotencyKey) return
    following.current.set(idempotencyKey, pinFollowing(value))
    announce(reviewCaseId, value.destination, progressWords('pin'))
  }
  const statusFor = (destination: string) => {
    const local =
      status.caseId === reviewCaseId && status.destination === destination ? status.text : null
    const durable = durableStatus.data?.destination === destination ? durableStatus.data.text : null
    return local ?? durable ?? ''
  }
  const alertFor = (destination: string) =>
    status.caseId === reviewCaseId && status.destination === destination && status.alert
  const messageFor = (destination: string) =>
    held?.destination === destination ? held.message : undefined
  return {
    statusFor,
    // A choice field's status line, where the field shows one, and whether it
    // says a choice was refused because Book matching changed the offer: that
    // line is said right under the choice cards (#1355).
    choiceStatusFor: (destination: string | undefined, shown: boolean) => {
      const text =
        destination !== undefined && shown
          ? statusFor(destination) || (messageFor(destination) ?? '')
          : ''
      return { text, offerChanged: text === offerChangedNotice }
    },
    pending: failed?.command ?? outstanding[0]?.command ?? entries[0]?.command ?? null,
    phase: chosen,
    // Choices remain readable while the exact field records in place. Submit
    // itself rejects any repeated or alternate command for that field.
    recording: outstanding.length > 0,
    canRetry: failed !== null && held === null,
    retry,
    reloadRequired: held !== null,
    // Every decision control of the case is disabled while it is checked.
    checking,
    messageFor,
    decision,
    settle,
    // A notice for this case, or for the case the owner is being moved to.
    announce: (destination: string, text: string, caseId = reviewCaseId) =>
      announce(caseId, destination, text),
    submit,
    pinAfter,
    alertFor,
  }
}
