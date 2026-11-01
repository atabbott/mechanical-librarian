import { ReviewerCost } from './ReviewerCost'
import { DescriptionReviewerSettings } from './DescriptionReviewerSettings'
import { StaleBuildRead } from './StaleBuildRead'
import { ModelPicker } from './ModelPicker'
import { coverReviewerLabel, reviewerFailureCopy, reviewerStateLabels } from '../labels'
import { BuildMismatchError } from '../api/http'
import type {
  CodexDeviceAuthState,
  ReviewHttp,
  ReviewerAccessView,
  ReviewerReason,
} from '../api/http'
import { failureCopy, useReviewerAccess } from '../reviewerAccess'
import type { ReviewerAccessController } from '../reviewerAccess'
import { useRef, useState } from 'react'

// Memory headroom in MB, or GB from one gigabyte (2^30 bytes) up.
const headroom = (bytes: number) =>
  bytes >= 1_073_741_824
    ? `${(bytes / 1_073_741_824).toLocaleString(undefined, { maximumFractionDigits: 1 })} GB`
    : `${Math.round(bytes / 1_048_576).toLocaleString()} MB`
// The code sits apart from the sentence so one click selects it whole. The
// clipboard API needs a secure context; elsewhere the button selects the code.
function DeviceCode({ code }: { code: string }) {
  const element = useRef<HTMLElement>(null)
  const [copied, setCopied] = useState('')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied('Copied.')
    } catch {
      const selection = getSelection()
      if (element.current && selection) {
        const range = document.createRange()
        range.selectNodeContents(element.current)
        selection.removeAllRanges()
        selection.addRange(range)
      }
      setCopied('Code selected. Press Ctrl+C or Command+C to copy it.')
    }
  }
  return (
    <p className="device-code-row">
      <code ref={element} className="device-code">
        {code}
      </code>
      <button type="button" onClick={() => void copy()}>
        Copy code
      </button>
      <span role="status">{copied}</span>
    </p>
  )
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
  profile_not_provisioned:
    'The contained reviewer profile is missing. A deployment owner must restore the supported image profile or its explicit override. OpenRouter and no reviewer remain available.',
  profile_unreadable:
    'The contained reviewer profile cannot be read safely. A deployment owner must correct its ownership, permissions or contents, then restart.',
  profile_contract_mismatch:
    'The contained reviewer profile does not match the required model and resource limits. A deployment owner must replace it with a current supported profile, then restart.',
  host_evidence_unavailable:
    'Host memory evidence is unavailable. Fix the host or container memory reporting before using Codex.',
  cgroup_limit_below_1_gib:
    'The container memory limit is below 1 GB. Increase it before using Codex.',
  headroom_below_1_gib:
    'Less than 1 GB of memory headroom is available. Free memory or raise the container limit.',
  permission_or_profile:
    'The installed Codex sign-in could not be validated. Replace the sign-in and retry.',
  permission_or_process: 'Codex could not complete sign-in. Restart the sign-in flow.',
  protocol_or_process: 'Codex returned an unexpected result. Restart the sign-in flow.',
  restart_interrupted: 'A prior sign-in was interrupted. Restart the sign-in flow.',
  not_selected: 'Codex is not selected. Check the sign-in, then select it for cover observations.',
  ...reviewerFailureCopy,
}

function CodexSignIn({
  access,
  view,
  hostUnsupported,
  codexManaged,
}: {
  access: ReviewerAccessController
  view: ReviewerAccessView
  hostUnsupported: boolean
  codexManaged: boolean
}) {
  const {
    deviceQuery,
    mutation,
    mutationFailure,
    waiting,
    confirmClear,
    setConfirmClear,
    confirmReplace,
    setConfirmReplace,
    cancelClear,
    cancelReplace,
    codexClearTrigger,
    codexReplaceTrigger,
  } = access
  const device = deviceQuery.data
  const codexState = hostUnsupported ? view.codex.state : (device?.state ?? view.codex.state)
  const codexReason =
    !hostUnsupported && device?.failure
      ? deviceFailureCopy[device.failure]
      : view.codex.reason
        ? codexReasonCopy[view.codex.reason]
        : null
  return (
    <div className="settings-field-row">
      <div>
        <h3>Shared Codex sign-in</h3>
        <p>
          One sign-in serves cover observations and description advice. Select each task separately
          below.
        </p>
        <p>Model: {view.codex.model ?? 'Unavailable until provisioning is corrected'}</p>
        <p aria-live="polite">Advisory availability: {reviewerStateLabels[codexState]}</p>
        {codexReason ? <p role="alert">{codexReason}</p> : null}
        {view.codex.host_headroom_bytes !== null ? (
          <small>Available reviewer headroom: {headroom(view.codex.host_headroom_bytes)}</small>
        ) : null}
      </div>
      <div className="settings-field-control">
        {waiting ? (
          <>
            <p role="status" aria-live="polite">
              Device sign-in: {reviewerStateLabels[device?.state ?? view.codex.state]}.
            </p>
            {device?.verification_url ? (
              <>
                <p>
                  Open{' '}
                  <a href={device.verification_url} target="_blank" rel="noreferrer">
                    the device sign-in page
                  </a>{' '}
                  in a new tab{device.user_code ? ' and enter this code:' : '.'}
                </p>
                {device.user_code ? <DeviceCode code={device.user_code} /> : null}
              </>
            ) : null}
            <button
              type="button"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate({ kind: 'codex-cancel' })}
            >
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
                  onClick={() => mutation.mutate({ kind: 'codex-start', revision: view.revision })}
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
                  onClick={() => mutation.mutate({ kind: 'codex-clear', revision: view.revision })}
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
              disabled={mutation.isPending || hostUnsupported}
              onClick={() => mutation.mutate({ kind: 'codex-start', revision: view.revision })}
            >
              {['expired', 'failed', 'cancelled', 'interrupted'].includes(view.codex.state)
                ? 'Restart device sign-in'
                : 'Replace or create Codex sign-in'}
            </button>
          </>
        )}
        {hostUnsupported && !waiting ? (
          <button
            type="button"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate({ kind: 'codex-cancel' })}
          >
            Cancel any active sign-in
          </button>
        ) : null}
        {hostUnsupported && mutation.isSuccess && mutation.variables.kind === 'codex-cancel' ? (
          <p role="status">
            {device?.state === 'cancelled'
              ? 'Any active sign-in was cancelled.'
              : device?.state === 'ready' || device?.state === 'usable'
                ? 'No active sign-in remains.'
                : 'Cancellation could not be confirmed. Retry or ask the deployment owner to inspect the reviewer.'}
          </p>
        ) : null}
        {mutation.isError && mutation.variables.kind.startsWith('codex') ? (
          <p role="alert">{mutationFailure}</p>
        ) : null}
      </div>
    </div>
  )
}

function OpenRouterReviewer({
  access,
  view,
}: {
  access: ReviewerAccessController
  view: ReviewerAccessView
}) {
  const {
    mutation,
    mutationFailure,
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
    openrouterClearTrigger,
    openrouterReplaceTrigger,
  } = access
  const effectiveModel = model || (view.mapping?.engine === 'openrouter' ? view.mapping.model : '')
  // The disclosure follows the field the owner is editing, then the saved
  // selection, so it never describes a model the page is no longer offering.
  const disclosedCoverModel = effectiveModel || (view.openrouter.model ?? '')
  return (
    <div className="settings-field-row">
      <div>
        <h4>OpenRouter</h4>
        <p>A vision model with strict structured output reviews each book’s covers.</p>
        <p>{view.data_use_caveat}</p>
      </div>
      <div className="settings-field-control">
        <ModelPicker
          id="reviewer-openrouter-model"
          label="Cover model"
          value={effectiveModel}
          onChange={setModel}
          models={[
            ...view.recommendations.map((recommendation) => ({
              model: recommendation,
              note: 'Recommended. Paid; observations remain advice.',
            })),
            ...view.candidates.map((candidate) => ({
              model: candidate.model,
              note: `${candidate.tier === 'free' ? 'Free' : candidate.tier}; listed by OpenRouter as capable, not yet qualified here.${candidate.requests_per_minute === null ? '' : ` ${view.free_tier_caveat}`}`,
            })),
          ]}
        />
        <label htmlFor="reviewer-openrouter-key">OpenRouter API key</label>
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
          Advisory availability:{' '}
          {mutation.isPending && mutation.variables.kind === 'openrouter'
            ? reviewerStateLabels.validating
            : reviewerStateLabels[view.openrouter.state]}
        </p>
        {view.openrouter.reason && failureCopy[view.openrouter.reason] ? (
          <p role="alert">{failureCopy[view.openrouter.reason]}</p>
        ) : null}
        {mutation.isError && mutation.variables.kind.startsWith('openrouter') ? (
          <p role="alert">{mutationFailure}</p>
        ) : null}
        <dl className="settings-reviewer-facts">
          <div>
            <dt>Model</dt>
            <dd>
              {disclosedCoverModel === ''
                ? 'None yet. Choose a model above; it is checked for image, text and structured output when you validate.'
                : disclosedCoverModel}
            </dd>
          </div>
          <div>
            <dt>Capabilities</dt>
            <dd>
              Image input, text output and structured output are required and are checked before a
              cover model can be selected.
            </dd>
          </div>
          <div>
            <dt>Observed prices in USD</dt>
            <dd>
              {view.cover_metadata?.model === effectiveModel
                ? `${view.cover_metadata.prompt_price ?? 'Unavailable'} per input token; ${view.cover_metadata.completion_price ?? 'Unavailable'} per output token; ${view.cover_metadata.image_price ?? 'Unavailable'} per image; ${view.cover_metadata.request_price ?? 'Unavailable'} per request.`
                : 'Validate and save this model to observe its prices.'}
            </dd>
          </div>
          <ReviewerCost view={view} model={effectiveModel} task="cover" />
          <div>
            <dt>Cover request</dt>
            <dd>One work packet, which may contain several cover images.</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}

export const ReviewerAccessPanel = ({ http }: { http: ReviewHttp }) => {
  const access = useReviewerAccess(http)
  const { query, deviceQuery, mutation, mutationFailure, waiting } = access
  if (query.isPending) return <p aria-busy="true">Reading reviewer access…</p>
  if (query.error instanceof BuildMismatchError)
    return <StaleBuildRead lead="Reviewer access was not loaded." />
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
  const hostUnsupported = ['host_unsupported', 'not_provisioned'].includes(view.codex.state)
  const codexUsable =
    !hostUnsupported && (view.codex.state === 'usable' || device?.state === 'usable')
  const codexManaged =
    view.mapping?.engine === 'codex' ||
    view.description_mapping?.engine === 'codex' ||
    codexUsable ||
    view.codex.state === 'configured'
  const coverEngine =
    view.mapping === null
      ? 'No reviewer'
      : view.mapping.engine === 'openrouter'
        ? `OpenRouter — ${coverReviewerLabel(view.mapping.model)}`
        : view.codex.model === null
          ? 'Contained Codex'
          : `Contained Codex — ${view.codex.model}`
  const coverModelNote =
    view.mapping?.engine === 'codex' && view.codex.model === null
      ? ' — no model selection; the installed profile fixes the model'
      : ''
  return (
    <div className="settings-reviewer">
      <CodexSignIn
        access={access}
        view={view}
        hostUnsupported={hostUnsupported}
        codexManaged={codexManaged}
      />

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
              <p>
                Use the shared sign-in above for cover observations. Usage consumes your plan
                allowance; no per-request monetary estimate is available.
              </p>
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
        <OpenRouterReviewer access={access} view={view} />
        {mutation.isError && mutation.variables.kind === 'without' ? (
          <p role="alert">{mutationFailure}</p>
        ) : null}
      </section>
      <DescriptionReviewerSettings http={http} view={view} codexUsable={codexUsable} />
    </div>
  )
}
