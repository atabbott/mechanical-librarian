import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { DescriptionReviewerSettings } from './DescriptionReviewerSettings'
import { ApiError } from '../api/http'
import type {
  CodexDeviceAuthState,
  ReviewHttp,
  ReviewerAccessView,
  ReviewerReason,
} from '../api/http'

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

const failureCopy: Readonly<Record<string, string>> = {
  invalid_key: 'OpenRouter rejected this key. Replace it and validate again.',
  quota_exhausted: 'OpenRouter credits are exhausted. Add credits, then retry the status test.',
  rate_limited: 'OpenRouter is rate limited. Wait, then retry the status test.',
  model_unavailable: 'That model is unavailable. Choose another vision model and validate again.',
  model_text_only: 'That model cannot inspect covers. Choose a model with image input.',
  malformed_response:
    'OpenRouter returned an invalid or oversized response. Retry later or choose another model.',
  unreachable: 'OpenRouter could not be reached. Check network access, then retry.',
  credential_clear_failed:
    'The credential could not be removed. It remains protected; retry the clear action.',
  host_unsupported: 'This host cannot safely run the contained Codex reviewer.',
  credential_unavailable:
    'Codex sign-in is unavailable. Test or replace the sign-in, then select Codex again.',
  access_changed: 'Reviewer access changed in another session. Reload before trying again.',
  reviewer_access_stale: 'Reviewer access changed in another session. Reload before trying again.',
}

const deviceFailureCopy: Readonly<Record<NonNullable<CodexDeviceAuthState['failure']>, string>> = {
  permission_or_profile:
    'The installed Codex sign-in could not be validated. Replace the sign-in and retry.',
  permission_or_process:
    'Codex refused or could not finish device sign-in. Restart the sign-in flow.',
  protocol_or_process: 'Codex returned an unexpected sign-in result. Restart the sign-in flow.',
  restart_interrupted: 'A prior sign-in was interrupted. Restart the sign-in flow.',
}

const codexReasonCopy: Readonly<Record<ReviewerReason, string>> = {
  host_evidence_unavailable:
    'Host memory evidence is unavailable. Fix the host or container memory reporting before using Codex.',
  cgroup_limit_below_1_gib:
    'The container memory limit is below 1 GiB. Increase it before using Codex.',
  headroom_below_1_gib:
    'Less than 1 GiB of memory headroom is available. Free memory or raise the container limit.',
  permission_or_profile:
    'The installed Codex sign-in could not be validated. Replace the sign-in and retry.',
  permission_or_process: 'Codex could not complete sign-in. Restart the sign-in flow.',
  protocol_or_process: 'Codex returned an unexpected result. Restart the sign-in flow.',
  restart_interrupted: 'A prior sign-in was interrupted. Restart the sign-in flow.',
  not_selected: 'Codex is not selected. Check the sign-in, then select it for cover observations.',
  invalid_key: 'The configured reviewer credential is invalid. Replace it and retry.',
  quota_exhausted: 'The configured reviewer has no remaining quota. Restore quota and retry.',
  rate_limited: 'The configured reviewer is rate limited. Wait, then retry.',
  model_unavailable: 'The configured reviewer model is unavailable. Choose another model.',
  model_text_only: 'The configured reviewer model cannot inspect cover images.',
  malformed_response: 'The configured reviewer returned an invalid response. Retry later.',
  unreachable: 'The configured reviewer could not be reached. Check network access and retry.',
}

export const ReviewerAccessPanel = ({ http }: { http: ReviewHttp }) => {
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
        queryClient.setQueryData(['reviewer-access', 'codex-device'], await http.recheckCodex())
        return null
      }
      if (action.kind === 'codex-cancel') {
        queryClient.setQueryData(
          ['reviewer-access', 'codex-device'],
          await http.cancelCodexDevice(),
        )
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
    onError: async (_error, action) => {
      if (action.kind !== 'codex-clear' && action.kind !== 'codex-start') return
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
      if (action.kind === 'codex-start') setDeviceActive(true)
      if (action.kind === 'codex-cancel' || action.kind === 'codex-clear') setDeviceActive(false)
      if (action.kind === 'codex-clear') {
        queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'] })
      }
      if (action.kind === 'codex') {
        queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'] })
      }
      if (result !== null) queryClient.setQueryData(['reviewer-access'], result)
    },
  })
  const waiting =
    deviceActive ||
    (query.data !== undefined &&
      ['starting', 'issued', 'waiting', 'validating'].includes(query.data.codex.state))
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
  useEffect(() => {
    const state = deviceQuery.data?.state
    if (state && !['starting', 'issued', 'waiting', 'validating'].includes(state)) {
      const task = window.setTimeout(() => {
        setDeviceActive(false)
      }, 0)
      return () => window.clearTimeout(task)
    }
    return undefined
  }, [deviceQuery.data?.state])
  if (query.isPending) return <p aria-busy="true">Reading reviewer access…</p>
  if (query.isError)
    return (
      <div role="alert">
        <p>Reviewer access is unavailable.</p>
        <button type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </div>
    )
  const view = query.data
  const device = deviceQuery.data
  const hostUnsupported = view.codex.state === 'host_unsupported'
  const codexState = hostUnsupported ? view.codex.state : (device?.state ?? view.codex.state)
  const codexUsable =
    !hostUnsupported && (view.codex.state === 'usable' || device?.state === 'usable')
  const codexManaged =
    view.mapping?.engine === 'codex' || codexUsable || view.codex.state === 'configured'
  const codexReason =
    !hostUnsupported && device?.failure
      ? deviceFailureCopy[device.failure]
      : view.codex.reason
        ? codexReasonCopy[view.codex.reason]
        : null
  const effectiveModel = model || (view.mapping?.engine === 'openrouter' ? view.mapping.model : '')
  // The cover task states its configured model beside the engine, as the
  // description task already does. Only the engine and a model it actually has
  // are emphasised; contained Codex reports no model of its own here, because
  // the installed profile fixes one and this view never carries it.
  const coverEngine =
    view.mapping === null
      ? 'No reviewer'
      : view.mapping.engine === 'openrouter'
        ? `OpenRouter — ${view.mapping.model}`
        : view.codex.model === null
          ? 'Contained Codex'
          : `Contained Codex — ${view.codex.model}`
  const coverModelNote =
    view.mapping?.engine === 'codex' && view.codex.model === null
      ? ' — no model selection; the installed profile fixes the model'
      : ''
  // The disclosure follows the field the owner is editing, then the saved
  // selection, so it never describes a model the page is no longer offering.
  const disclosedCoverModel = effectiveModel || (view.openrouter.model ?? '')
  const mutationFailure =
    mutation.variables?.kind === 'codex-clear' || mutation.variables?.kind === 'codex-start'
      ? 'The change could not be fully confirmed. Your prior sign-in or reviewer selection may have changed. Reload reviewer access before trying again.'
      : mutation.error instanceof ApiError
        ? (failureCopy[mutation.error.code] ??
          'The reviewer change was refused. Reload and try again.')
        : 'The reviewer change was not applied. Reload and try again.'
  return (
    <div className="settings-reviewer">
      <div className="settings-field-row">
        <div>
          <h3>Shared Codex sign-in</h3>
          <p>
            One sign-in serves cover observations and description advice. Select each task
            separately below.
          </p>
          <p aria-live="polite">Status: {codexState.replaceAll('_', ' ')}</p>
          {codexReason ? <p role="alert">{codexReason}</p> : null}
          {view.codex.host_headroom_bytes !== null ? (
            <small>
              Available reviewer headroom: {view.codex.host_headroom_bytes.toLocaleString()} bytes
            </small>
          ) : null}
        </div>
        <div className="settings-field-control">
          {waiting ? (
            <>
              <p role="status" aria-live="polite">
                Device sign-in: {(device?.state ?? view.codex.state).replaceAll('_', ' ')}.
              </p>
              {device?.verification_url ? (
                <p>
                  Open{' '}
                  <a href={device.verification_url} rel="noreferrer">
                    the device sign-in page
                  </a>
                  {device.user_code ? (
                    <>
                      {' '}
                      and enter <strong>{device.user_code}</strong>.
                    </>
                  ) : (
                    '.'
                  )}
                </p>
              ) : null}
              <button type="button" onClick={() => mutation.mutate({ kind: 'codex-cancel' })}>
                Cancel sign-in
              </button>
            </>
          ) : codexManaged ? (
            <>
              <button
                type="button"
                disabled={mutation.isPending || hostUnsupported}
                onClick={() => mutation.mutate({ kind: 'codex-recheck' })}
              >
                Check existing Codex sign-in
              </button>
              {confirmReplace === 'codex' ? (
                <div
                  className="settings-reviewer-actions"
                  role="group"
                  aria-label="Confirm replace Codex sign-in"
                >
                  <button
                    type="button"
                    autoFocus
                    onClick={() =>
                      mutation.mutate({ kind: 'codex-start', revision: view.revision })
                    }
                  >
                    Confirm replace Codex sign-in
                  </button>
                  <button type="button" onClick={() => cancelReplace('codex')}>
                    Keep existing Codex sign-in
                  </button>
                </div>
              ) : (
                <button
                  ref={codexReplaceTrigger}
                  type="button"
                  disabled={hostUnsupported}
                  onClick={() => setConfirmReplace('codex')}
                >
                  Replace Codex sign-in
                </button>
              )}
              {confirmClear === 'codex' ? (
                <div
                  className="settings-reviewer-actions"
                  role="group"
                  aria-label="Confirm clear Codex sign-in"
                >
                  <button
                    type="button"
                    autoFocus
                    disabled={mutation.isPending}
                    onClick={() =>
                      mutation.mutate({ kind: 'codex-clear', revision: view.revision })
                    }
                  >
                    Confirm clear Codex sign-in
                  </button>
                  <button type="button" onClick={() => cancelClear('codex')}>
                    Keep Codex sign-in
                  </button>
                </div>
              ) : (
                <button
                  ref={codexClearTrigger}
                  type="button"
                  onClick={() => setConfirmClear('codex')}
                >
                  Clear Codex sign-in
                </button>
              )}
            </>
          ) : (
            <>
              <button
                type="button"
                disabled={mutation.isPending || hostUnsupported}
                onClick={() => mutation.mutate({ kind: 'codex-recheck' })}
              >
                Check existing Codex sign-in
              </button>
              <button
                type="button"
                disabled={mutation.isPending || view.codex.state === 'host_unsupported'}
                onClick={() => mutation.mutate({ kind: 'codex-start', revision: view.revision })}
              >
                {['expired', 'failed', 'cancelled', 'interrupted'].includes(view.codex.state)
                  ? 'Restart device sign-in'
                  : 'Replace or create Codex sign-in'}
              </button>
            </>
          )}
          {mutation.isError && mutation.variables.kind.startsWith('codex') ? (
            <p role="alert">{mutationFailure}</p>
          ) : null}
        </div>
      </div>

      <section aria-labelledby="reviewer-access-heading">
        <div className="settings-field-row">
          <div>
            <h3 id="reviewer-access-heading">Optional cover reviewer</h3>
            <p>
              Reviews are advice only. You can keep using Mechanical Librarian without configuring
              an engine, and your choice remains final.
            </p>
          </div>
          <div className="settings-field-control">
            <p>
              Current selection: <strong>{coverEngine}</strong>
              {coverModelNote}
            </p>
            <button
              type="button"
              disabled={mutation.isPending || view.mapping === null}
              onClick={() => mutation.mutate({ kind: 'without', revision: view.revision })}
            >
              Continue without a reviewer
            </button>
          </div>
        </div>

        {codexManaged && !waiting ? (
          <div className="settings-field-row">
            <div>
              <h4>Contained Codex</h4>
              <p>Use the shared sign-in above for cover observations.</p>
            </div>
            <div className="settings-field-control">
              <button
                type="button"
                disabled={mutation.isPending || !codexUsable || view.mapping?.engine === 'codex'}
                onClick={() => mutation.mutate({ kind: 'codex', revision: view.revision })}
              >
                Use Codex for cover observations
              </button>
            </div>
          </div>
        ) : null}
        <div className="settings-field-row">
          <div>
            <h4>OpenRouter</h4>
            <p>Choose a vision model that currently supports strict structured output.</p>
            <p>{view.free_tier_caveat}</p>
            <p>{view.data_use_caveat}</p>
            <p>Evidence-qualified paid model on the delivered real-cover corpus:</p>
            <ul className="settings-reviewer-suggestions">
              {view.recommendations.map((recommendation) => (
                <li key={recommendation}>
                  <button type="button" onClick={() => setModel(recommendation)}>
                    Use {recommendation}
                  </button>
                  <small>Paid; evidence-qualified on the delivered real-cover corpus.</small>
                </li>
              ))}
            </ul>
            <p>Catalogue-capability-only free option:</p>
            <ul className="settings-reviewer-suggestions">
              {view.candidates.map((candidate) => (
                <li key={candidate.model}>
                  <button type="button" onClick={() => setModel(candidate.model)}>
                    Use {candidate.model}
                  </button>
                  <small>
                    {candidate.tier}; catalogue capability only, not yet qualified
                    {candidate.requests_per_minute === null
                      ? null
                      : ` — ${String(candidate.requests_per_minute)} RPM; ${candidate.requests_per_day ?? ''} RPD`}
                  </small>
                </li>
              ))}
            </ul>
          </div>
          <div className="settings-field-control">
            <label htmlFor="reviewer-openrouter-model">Model ID</label>
            <input
              id="reviewer-openrouter-model"
              value={model}
              placeholder={
                view.mapping?.engine === 'openrouter' ? view.mapping.model : 'provider/model'
              }
              maxLength={256}
              onChange={(event) => setModel(event.currentTarget.value)}
            />
            <label htmlFor="reviewer-openrouter-key">API key</label>
            <input
              id="reviewer-openrouter-key"
              type="password"
              autoComplete="new-password"
              value={key}
              maxLength={2048}
              onChange={(event) => setKey(event.currentTarget.value)}
            />
            <small>The key is write-only and is never returned to this page.</small>
            <button
              ref={openrouterReplaceTrigger}
              type="button"
              disabled={mutation.isPending || !effectiveModel || !key}
              onClick={() => {
                if (view.openrouter_key_configured && confirmReplace !== 'openrouter') {
                  setConfirmReplace('openrouter')
                  return
                }
                mutation.mutate({
                  kind: 'openrouter',
                  revision: view.revision,
                  key,
                  model: effectiveModel,
                })
              }}
            >
              {view.openrouter_key_configured
                ? confirmReplace === 'openrouter'
                  ? 'Confirm replace OpenRouter key'
                  : 'Replace OpenRouter key'
                : 'Validate and use OpenRouter'}
            </button>
            {confirmReplace === 'openrouter' ? (
              <button type="button" onClick={() => cancelReplace('openrouter')}>
                Keep existing OpenRouter key
              </button>
            ) : null}
            {view.openrouter_key_configured ? (
              <>
                <button
                  type="button"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate({ kind: 'openrouter-refresh' })}
                >
                  Refresh current status
                </button>
                {confirmClear === 'openrouter' ? (
                  <div
                    className="settings-reviewer-actions"
                    role="group"
                    aria-label="Confirm clear OpenRouter key"
                  >
                    <button
                      type="button"
                      autoFocus
                      disabled={mutation.isPending}
                      onClick={() =>
                        mutation.mutate({ kind: 'openrouter-clear', revision: view.revision })
                      }
                    >
                      Confirm clear OpenRouter key
                    </button>
                    <button type="button" onClick={() => cancelClear('openrouter')}>
                      Keep OpenRouter key
                    </button>
                  </div>
                ) : (
                  <button
                    ref={openrouterClearTrigger}
                    type="button"
                    onClick={() => setConfirmClear('openrouter')}
                  >
                    Clear OpenRouter key
                  </button>
                )}
              </>
            ) : null}
            <p
              aria-live="polite"
              role={
                ['usable', 'configured', 'unconfigured'].includes(view.openrouter.state)
                  ? 'status'
                  : 'alert'
              }
            >
              Status:{' '}
              {mutation.isPending && mutation.variables.kind === 'openrouter'
                ? 'validating'
                : view.openrouter.state.replaceAll('_', ' ')}
            </p>
            {mutation.isError && mutation.variables.kind.startsWith('openrouter') ? (
              <p role="alert">{mutationFailure}</p>
            ) : null}
            <dl className="settings-reviewer-facts">
              <div>
                <dt>Model</dt>
                <dd>
                  {disclosedCoverModel === ''
                    ? 'None yet. Enter a model ID above; it is checked for image, text and structured output when you validate.'
                    : disclosedCoverModel}
                </dd>
              </div>
              <div>
                <dt>Capabilities</dt>
                <dd>
                  Image input, text output and structured output are required and are checked before
                  a cover model can be selected.
                </dd>
              </div>
              <div>
                <dt>Price metadata observed</dt>
                <dd>
                  Not recorded. The cover capability check reads image and structured-output support
                  only, never this model&apos;s prices, so no price observation date exists for the
                  cover task.
                </dd>
              </div>
              <div>
                <dt>Prices in USD</dt>
                <dd>
                  Unavailable here. Providers charge cover previews by their own image tokenization,
                  which this application never observes, so no rate is shown and no model is
                  presented as the cheapest.
                </dd>
              </div>
              <div>
                <dt>Maximum-size request estimate</dt>
                <dd>
                  One request carries at most 12 cover previews, each at most 320 by 480 pixels and
                  1 MiB and together at most 8 MiB, plus a text prompt of the pinned instruction (at
                  most 4 KiB) and the sealed packet text (at most 1 MiB), a strict output schema (at
                  most 64 KiB), and a reply capped at 8,192 output tokens. Apply the published
                  text-input, image-input and output rates of the selected model to that whole
                  request to judge cost. Actual tokenization, output length and charges vary; any
                  such figure is an estimate, not a quote.
                </dd>
              </div>
            </dl>
          </div>
        </div>
        {mutation.isError && mutation.variables.kind === 'without' ? (
          <p role="alert">{mutationFailure}</p>
        ) : null}
      </section>
      <DescriptionReviewerSettings http={http} view={view} codexUsable={codexUsable} />
    </div>
  )
}
