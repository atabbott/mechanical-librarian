import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { ModelPicker } from './ModelPicker'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { CoverModelMetadata, ReviewHttp, ReviewerAccessView } from '../api/http'
import { invalidateForegroundQueries } from '../queryWork'
import { codexRefusal, codexUnavailable, credentialRefused, failureCopy } from '../reviewerAccess'

// Settings → Reviewer › Taste profile (#1372): the engine, Codex or an
// OpenRouter model, that writes the owner's taste profile. The reviewer access
// panel renders it with the view it has read.

const prices = (metadata: CoverModelMetadata) =>
  `${metadata.prompt_price ?? 'Unavailable'} per input token, ${metadata.completion_price ?? 'Unavailable'} per output token, ${metadata.image_price ?? 'Unavailable'} per image, ${metadata.request_price ?? 'Unavailable'} per request.`

// Covers → Taste profile reads whether a model is chosen, so a change here
// makes its cached view stale.
const tasteProfileKey = ['covers', 'taste'] as const

const useTasteModel = (
  http: ReviewHttp,
  view: ReviewerAccessView,
  onCodexRefused: () => Promise<void>,
) => {
  const cache = useQueryClient()
  const [model, setModel] = useState(
    view.taste_mapping?.engine === 'openrouter' ? view.taste_mapping.model : '',
  )
  const [key, setKey] = useState('')
  const [metadata, setMetadata] = useState<CoverModelMetadata | null>(null)
  const [latest, setLatest] = useState<'test' | 'selection' | 'codex' | 'without' | null>(null)
  // A write that got no answer may have landed, and one refused as stale
  // needs the current revision before Try again can succeed: read both again.
  const rereadAfterFailedWrite = () =>
    Promise.all([
      invalidateForegroundQueries(cache, { queryKey: ['reviewer-access'] }),
      invalidateForegroundQueries(cache, { queryKey: tasteProfileKey }),
    ])
  const test = useMutation({
    mutationFn: () =>
      http.testTasteModel({ expected_revision: view.revision, api_key: key, model }),
    retry: false,
    onMutate: () => setLatest('test'),
    onSuccess: setMetadata,
  })
  const selection = useMutation({
    mutationFn: () =>
      http.configureOpenRouter({
        expected_revision: view.revision,
        api_key: key,
        model,
        task: 'cover_taste_build',
      }),
    retry: false,
    onMutate: () => setLatest('selection'),
    onSuccess: (saved) => {
      setKey('')
      setMetadata(null)
      cache.setQueryData(['reviewer-access'], saved)
      void cache.invalidateQueries({ queryKey: tasteProfileKey })
    },
    onError: rereadAfterFailedWrite,
  })
  const codex = useMutation({
    mutationFn: () => http.selectReviewer('codex', view.revision, undefined, 'cover_taste_build'),
    retry: false,
    onMutate: () => setLatest('codex'),
    onSuccess: (saved) => {
      // The OpenRouter draft no longer describes the choice, as on a fresh load.
      setModel('')
      setMetadata(null)
      test.reset()
      cache.setQueryData(['reviewer-access'], saved)
      void cache.invalidateQueries({ queryKey: tasteProfileKey })
    },
    // A refused Codex sign-in is recorded into reviewer access; read it (#1617).
    // Codex missing from the host keeps the row and its line, as the cover
    // reviewer's Codex button does.
    onError: (error) =>
      credentialRefused(error)
        ? onCodexRefused()
        : codexUnavailable(error)
          ? undefined
          : rereadAfterFailedWrite(),
  })
  // Continue without a taste profile clears only this task (#1372 S11).
  const without = useMutation({
    mutationFn: () => http.selectReviewer(null, view.revision, undefined, 'cover_taste_build'),
    retry: false,
    onMutate: () => setLatest('without'),
    onSuccess: (saved) => {
      setModel('')
      setMetadata(null)
      cache.setQueryData(['reviewer-access'], saved)
      void cache.invalidateQueries({ queryKey: tasteProfileKey })
    },
    onError: rereadAfterFailedWrite,
  })
  const error = latest === null ? null : { test, selection, codex, without }[latest].error
  const failure =
    error === null
      ? null
      : latest === 'without'
        ? uncertainWriteOutcome(error)
          ? 'No answer came back. Check whether the taste profile model was cleared before trying again.'
          : refusalCopy(error, 'The taste profile model was not cleared. Try again.')
        : (latest === 'selection' || latest === 'codex') && uncertainWriteOutcome(error)
          ? 'No answer came back. Check whether the taste profile model was saved before trying again.'
          : latest === 'codex'
            ? codexRefusal(
                error,
                'Codex was not chosen for taste profiles. Reload, then try again.',
              )
            : credentialRefused(error)
              ? failureCopy.credential_unavailable
              : refusalCopy(
                  error,
                  'Taste profile setup did not complete. Check the model and key, then try again.',
                )
  const failureUnder = (at: NonNullable<typeof latest>) =>
    failure !== null && latest === at ? <p role="alert">{failure}</p> : null
  const busy = test.isPending || selection.isPending || codex.isPending || without.isPending
  return {
    model,
    setModel,
    key,
    setKey,
    metadata,
    setMetadata,
    test,
    selection,
    codex,
    without,
    latest,
    failureUnder,
    busy,
  }
}

export const TasteReviewerSettings = ({
  http,
  view,
  codexUsable,
  codexManaged,
  onCodexRefused,
}: {
  http: ReviewHttp
  view: ReviewerAccessView
  codexUsable: boolean
  codexManaged: boolean
  onCodexRefused: () => Promise<void>
}) => {
  const taste = useTasteModel(http, view, onCodexRefused)
  const { codex, without, failureUnder, busy } = taste
  const selected = view.taste_mapping
  return (
    <section aria-labelledby="taste-reviewer-heading">
      <div className="settings-field-row">
        <div>
          <h3 id="taste-reviewer-heading">Taste profile</h3>
          <p>A vision model writes your taste profile from the covers you chose and passed over.</p>
          <p>
            The model receives those covers with their sizes and the book’s format, never its title
            or files.
          </p>
        </div>
        <div className="settings-field-control">
          <p role="status">
            {selected === null ? (
              'No taste profile model selected.'
            ) : selected.engine === 'openrouter' ? (
              `Selected: ${selected.model} on OpenRouter.`
            ) : (
              <>
                Selected: Codex (
                <span className="settings-model-name">
                  {view.codex.model ?? 'model unavailable'}
                </span>
                ).
              </>
            )}
          </p>
          {/* Mirrors Continue without description advice: aria-disabled while
              it runs, so a pending press keeps focus (#1584, #1663). */}
          <button
            aria-disabled={busy || selected === null || undefined}
            onClick={() => {
              if (!busy && selected !== null) without.mutate()
            }}
          >
            Continue without a taste profile
          </button>
          {failureUnder('without')}
          <p>{`Mechanical Librarian asks for at most ${String(view.taste_daily_requests)} taste profiles a day.`}</p>
        </div>
      </div>
      {codexManaged ? (
        <div className="settings-field-row">
          <div>
            <h4>Codex</h4>
            <p>Taste profiles count against your plan’s usage limits, so no cost is shown.</p>
          </div>
          <div className="settings-field-control">
            <p>Model: {view.codex.model ?? 'Unavailable'}</p>
            {/* Already the choice: aria-disabled, so a press saves nothing
                again and focus stays on the button. */}
            <button
              disabled={!codexUsable}
              aria-disabled={busy || selected?.engine === 'codex' || undefined}
              onClick={() => {
                if (!busy && selected?.engine !== 'codex') codex.mutate()
              }}
            >
              Use Codex for taste profiles
            </button>
            {failureUnder('codex')}
          </div>
        </div>
      ) : null}
      <TasteOpenRouterRow taste={taste} view={view} />
    </section>
  )
}

// The OpenRouter row: model, shared key, test, disclosure and selection, as
// the description adviser's is.
function TasteOpenRouterRow({
  taste,
  view,
}: {
  taste: ReturnType<typeof useTasteModel>
  view: ReviewerAccessView
}) {
  const { model, key, metadata, test, selection, latest, failureUnder, busy } = taste
  const saved = latest === 'selection' && selection.isSuccess
  // The pressed button goes disabled once the test it needs is spent, so focus
  // moves to the line that says the model was saved instead of to the page.
  const savedLine = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (saved) savedLine.current?.focus()
  }, [saved])
  const disclosure =
    metadata?.model === model
      ? metadata
      : view.taste_metadata?.model === model
        ? view.taste_metadata
        : null
  return (
    <div className="settings-field-row">
      <div>
        <h4>OpenRouter</h4>
      </div>
      <div className="settings-field-control">
        <ModelPicker
          id="taste-openrouter-model"
          label="Taste profile model"
          value={model}
          disabled={busy}
          models={view.taste_recommendations.map((recommended) => ({
            model: recommended,
            note: 'Recommended. Measured for taste profiles.',
          }))}
          onChange={(next) => {
            taste.setModel(next)
            taste.setMetadata(null)
            test.reset()
          }}
        />
        <label htmlFor="taste-openrouter-key">Shared OpenRouter API key</label>
        <input
          id="taste-openrouter-key"
          type="password"
          value={key}
          disabled={busy}
          aria-describedby="taste-openrouter-key-help"
          onChange={(event) => {
            taste.setKey(event.target.value)
            taste.setMetadata(null)
            test.reset()
          }}
          autoComplete="new-password"
        />
        <small id="taste-openrouter-key-help">
          Leave blank to use the saved key. Cover review and description advice use the same key,
          and replacing it keeps every model choice.
        </small>
        <button
          disabled={(!key && !view.openrouter_key_configured) || !model}
          aria-disabled={busy || undefined}
          onClick={() => {
            if (!busy) test.mutate()
          }}
        >
          {test.isPending ? 'Testing taste profile model…' : 'Test taste profile model and prices'}
        </button>
        {failureUnder('test')}
        {disclosure ? (
          <dl className="settings-reviewer-facts" aria-live="polite">
            <div>
              <dt>Model</dt>
              <dd>{disclosure.model}</dd>
            </div>
            <div>
              <dt>Capabilities</dt>
              <dd>Image input, text output and structured output supported.</dd>
            </div>
            <div>
              <dt>Prices in USD</dt>
              <dd>{prices(disclosure)}</dd>
            </div>
          </dl>
        ) : (
          <p>Test this model to see its prices before you use it.</p>
        )}
        <button
          disabled={metadata?.model !== model}
          aria-disabled={busy || undefined}
          onClick={() => {
            if (!busy) selection.mutate()
          }}
        >
          Use for taste profile
        </button>
        {failureUnder('selection')}
        {/* Said only for the latest action: Continue without replaces it. */}
        {saved ? (
          <p ref={savedLine} role="status" tabIndex={-1}>
            Taste profile model saved.
          </p>
        ) : null}
      </div>
    </div>
  )
}
