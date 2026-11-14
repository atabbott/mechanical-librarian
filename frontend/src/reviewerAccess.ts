// Reviewer access's controller: the access read, the device sign-in poll, the
// owner's draft key and model, the confirmation steps, and the one command
// they all share, with the words for its failure. Split out of the reviewer
// access panel under the function-size rule (#1571).
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

import { ApiError, CsrfRenewedError, sessionRefreshedCopy, uncertainWriteOutcome } from './api/http'
import type { ReviewHttp, ReviewerAccessView } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'

type Action =
  | { kind: 'without'; revision: number }
  | { kind: 'codex'; revision: number }
  | { kind: 'codex-start'; revision: number }
  | { kind: 'codex-recheck' }
  | { kind: 'codex-cancel' }
  | { kind: 'codex-clear'; revision: number }
  | { kind: 'openrouter'; revision: number; key: string; model: string }
  | { kind: 'openrouter-clear'; revision: number }
  | { kind: 'openrouter-refresh' }

export const failureCopy: Readonly<Record<string, string>> = {
  invalid_key: 'OpenRouter rejected this key. Replace it and validate again.',
  quota_exhausted: 'OpenRouter credits are used up. Add credits, then refresh the status.',
  rate_limited: 'OpenRouter is rate limited. Wait, then refresh the status.',
  model_unavailable: 'That model is unavailable. Choose another vision model and validate again.',
  model_text_only: 'That model cannot inspect covers. Choose a model with image input.',
  malformed_response:
    'OpenRouter’s answer could not be read. Retry later, or choose another model.',
  unreachable: 'OpenRouter could not be reached. Check network access, then retry.',
  response_too_large:
    'OpenRouter returned an answer too large to read. Retry later, or choose another model.',
  timed_out: 'OpenRouter did not answer in time. Check network access, then retry.',
  data_policy_refused:
    'No provider for this model accepts your OpenRouter data policy. Change your privacy settings on OpenRouter, or choose another model.',
  image_limit_exceeded:
    'That model accepts fewer images than a cover review sends. Choose another vision model.',
  credential_clear_failed: 'Nothing was cleared. Try again.',
  not_provisioned: 'Codex is not set up on this host. Reload the page to see what to fix.',
  host_unsupported: 'Codex is not supported on this host. Reload the page to see what to fix.',
  credential_unavailable:
    'Codex sign-in is unavailable. Check or replace the sign-in, then try again.',
  access_changed: 'Reviewer settings changed elsewhere. Reload, then try again.',
  reviewer_access_stale: 'Reviewer settings changed elsewhere. Reload, then try again.',
}

// A refused Codex selection: the server records the failed sign-in into
// reviewer access, so the refusing page reads access again (#1617).
export const credentialRefused = (error: unknown) =>
  error instanceof ApiError && error.code === 'credential_unavailable'

// The words for a failed reviewer command, in precedence order: a renewed
// session, an uncertain credential command, OpenRouter's pre-save probe, an
// uncertain write, then the server's refusal code. Split out of the hook under
// the function-size rule (#1617).
const reviewerMutationFailure = (error: Error | null, action: Action | undefined) =>
  error instanceof CsrfRenewedError
    ? sessionRefreshedCopy
    : action?.kind === 'codex-clear' || action?.kind === 'codex-start'
      ? 'No answer came back. Check whether the Codex sign-in changed before trying again.'
      : // OpenRouter's probe raises these before anything is saved.
        error instanceof ApiError &&
          action?.kind === 'openrouter' &&
          (error.code === 'unreachable' || error.code === 'timed_out')
        ? (failureCopy[error.code] ?? 'The reviewer change was refused. Reload and try again.')
        : uncertainWriteOutcome(error)
          ? 'No answer came back. Check whether the reviewer change was saved before trying again.'
          : error instanceof ApiError
            ? (failureCopy[error.code] ?? 'The reviewer change was refused. Reload and try again.')
            : 'The reviewer change was not applied. Reload and try again.'

export const useReviewerAccess = (http: ReviewHttp) => {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['reviewer-access'] as const,
    queryFn: (queryContext) =>
      http.getReviewerAccess(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
  })
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const [deviceActive, setDeviceActive] = useState(false)
  const [confirmClear, setConfirmClear] = useState<'codex' | 'openrouter' | null>(null)
  const [confirmReplace, setConfirmReplace] = useState<'codex' | 'openrouter' | null>(null)
  const codexClearTrigger = useRef<HTMLButtonElement>(null)
  const openrouterClearTrigger = useRef<HTMLButtonElement>(null)
  const codexReplaceTrigger = useRef<HTMLButtonElement>(null)
  const openrouterReplaceTrigger = useRef<HTMLButtonElement>(null)
  const codexStartTrigger = useRef<HTMLButtonElement>(null)
  const codexCancelTrigger = useRef<HTMLButtonElement>(null)
  const codexCheckTrigger = useRef<HTMLButtonElement>(null)
  const accessRetryTrigger = useRef<HTMLButtonElement>(null)
  // A pressed sign-in button unmounts, or becomes natively disabled, on success;
  // move focus to the next control unless the owner already moved it (#1607).
  // Every caller ends with Try again, the only control when a read fails (#1617).
  const focusIfLost = (...targets: RefObject<HTMLButtonElement | null>[]) => {
    window.setTimeout(() => {
      const active = document.activeElement
      if (active !== null && active !== document.body && !active.matches(':disabled')) return
      targets.find((target) => target.current !== null)?.current?.focus()
    }, 0)
  }
  // Read the recorded refusal, with no cached device result masking it.
  const refreshAfterCodexRefusal = async () => {
    queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'], exact: true })
    await query.refetch({ cancelRefetch: false })
    focusIfLost(codexCancelTrigger, codexStartTrigger, codexReplaceTrigger, accessRetryTrigger)
  }
  const cancelClear = (engine: 'codex' | 'openrouter') => {
    setConfirmClear(null)
    window.setTimeout(() => {
      const trigger = engine === 'codex' ? codexClearTrigger : openrouterClearTrigger
      trigger.current?.focus()
    }, 0)
  }
  const cancelReplace = (engine: 'codex' | 'openrouter') => {
    setConfirmReplace(null)
    window.setTimeout(() => {
      const trigger = engine === 'codex' ? codexReplaceTrigger : openrouterReplaceTrigger
      trigger.current?.focus()
    }, 0)
  }
  const mutation = useMutation({
    mutationFn: async (action: Action): Promise<ReviewerAccessView | null> => {
      if (action.kind === 'without') return http.selectReviewer(null, action.revision)
      if (action.kind === 'codex') return http.selectReviewer('codex', action.revision)
      if (action.kind === 'codex-start') {
        const started = await http.startCodexDevice(action.revision)
        queryClient.setQueryData(['reviewer-access'], started.access)
        queryClient.setQueryData(['reviewer-access', 'codex-device'], started.device)
        return null
      }
      if (action.kind === 'codex-recheck') {
        const checked = await http.recheckCodex()
        // The check records its outcome into access, or meets a flow another tab
        // started; read that first, so the row changes once (#1617, #1663).
        await query.refetch({ cancelRefetch: false })
        queryClient.setQueryData(['reviewer-access', 'codex-device'], checked)
        return null
      }
      if (action.kind === 'codex-cancel') {
        queryClient.setQueryData(
          ['reviewer-access', 'codex-device'],
          await http.cancelCodexDevice(),
        )
        // The start seeded access in an in-progress state; read the outcome
        // before the button settles, so the waiting branch closes (#1607).
        await query.refetch({ cancelRefetch: false })
        return null
      }
      if (action.kind === 'codex-clear') return http.clearCodex(action.revision)
      if (action.kind === 'openrouter-clear') return http.clearOpenRouter(action.revision)
      if (action.kind === 'openrouter-refresh') return http.refreshOpenRouter()
      return http.configureOpenRouter({
        expected_revision: action.revision,
        api_key: action.key,
        model: action.model,
      })
    },
    onError: async (error, action) => {
      if (action.kind === 'codex' && credentialRefused(error)) return refreshAfterCodexRefusal()
      if (action.kind !== 'codex-clear' && action.kind !== 'codex-start') {
        // The change may have been saved; reload the authoritative access.
        if (uncertainWriteOutcome(error)) await query.refetch()
        return
      }
      // A credential side effect may have completed before mapping publication failed.
      // Reconcile read-only state before another owner command can be submitted.
      await Promise.allSettled([
        query.refetch(),
        http.getCodexDevice().then((device) => {
          queryClient.setQueryData(['reviewer-access', 'codex-device'], device)
          setDeviceActive(['starting', 'issued', 'waiting', 'validating'].includes(device.state))
        }),
      ])
    },
    onSuccess: (result, action) => {
      setKey('')
      setConfirmClear(null)
      setConfirmReplace(null)
      if (action.kind === 'codex-start') {
        setDeviceActive(true)
        focusIfLost(codexCancelTrigger, accessRetryTrigger)
      }
      if (action.kind === 'codex-cancel' || action.kind === 'codex-clear') setDeviceActive(false)
      if (action.kind === 'codex-cancel')
        focusIfLost(codexStartTrigger, codexReplaceTrigger, accessRetryTrigger)
      if (action.kind === 'codex-recheck')
        focusIfLost(codexCancelTrigger, codexCheckTrigger, accessRetryTrigger)
      if (action.kind === 'codex-clear') {
        queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'] })
      }
      if (action.kind === 'codex') {
        queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'] })
      }
      if (result !== null) queryClient.setQueryData(['reviewer-access'], result)
    },
  })
  const accessInProgress =
    query.data !== undefined &&
    ['starting', 'issued', 'waiting', 'validating'].includes(query.data.codex.state)
  const waiting = deviceActive || accessInProgress
  const deviceQuery = useQuery({
    queryKey: ['reviewer-access', 'codex-device'] as const,
    queryFn: (queryContext) =>
      http.getCodexDevice(queryContext.signal, queryWorkClass(queryContext)),
    enabled: waiting,
    retry: false,
    refetchInterval: (result) =>
      result.state.data &&
      ['starting', 'issued', 'waiting', 'validating'].includes(result.state.data.state)
        ? 1_000
        : false,
  })
  // The query's refetch is rewrapped on every render; read it through a ref so
  // the effect below reruns only when the flow or the access state changes.
  const refetchAccess = useRef(query.refetch)
  refetchAccess.current = query.refetch
  // The terminal device result already refreshed, so a failed read waits for
  // an explicit retry instead of repeating on every render (#1607).
  const refreshedFor = useRef<object | null>(null)
  const device = deviceQuery.data
  useEffect(() => {
    if (!device || ['starting', 'issued', 'waiting', 'validating'].includes(device.state)) {
      return undefined
    }
    const task = window.setTimeout(() => {
      // Only a shown waiting branch hands focus on; a page that loads with a
      // finished flow leaves focus where it is.
      const closing = codexCancelTrigger.current !== null
      const settle = () => {
        if (closing) focusIfLost(codexStartTrigger, codexReplaceTrigger, accessRetryTrigger)
      }
      setDeviceActive(false)
      // A finished flow leaves access in progress until it is read again; its
      // Cancel button then unmounts, so focus moves to the outcome's control.
      if (accessInProgress && refreshedFor.current !== device) {
        refreshedFor.current = device
        void refetchAccess.current({ cancelRefetch: false }).then(settle)
      } else {
        settle()
      }
    }, 0)
    return () => window.clearTimeout(task)
  }, [device, accessInProgress])
  return {
    query,
    deviceQuery,
    mutation,
    mutationFailure: reviewerMutationFailure(mutation.error, mutation.variables),
    waiting,
    model,
    setModel,
    key,
    setKey,
    confirmClear,
    setConfirmClear,
    confirmReplace,
    setConfirmReplace,
    cancelClear,
    cancelReplace,
    codexClearTrigger,
    openrouterClearTrigger,
    codexReplaceTrigger,
    openrouterReplaceTrigger,
    codexStartTrigger,
    codexCancelTrigger,
    codexCheckTrigger,
    accessRetryTrigger,
    refreshAfterCodexRefusal,
  }
}
export type ReviewerAccessController = ReturnType<typeof useReviewerAccess>
