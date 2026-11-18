import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { ModelPicker } from './ModelPicker'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { CoverModelMetadata, ReviewHttp, ReviewerAccessView, TasteTask } from '../api/http'
import { invalidateForegroundQueries } from '../queryWork'
import { codexRefusal, codexUnavailable, credentialRefused, failureCopy } from '../reviewerAccess'

// Settings → Reviewer › Taste profile and Suggestions (#1372): the engine,
// Codex or an OpenRouter model, that writes the owner's taste profile, and the
// one that compares each work's covers with it. The reviewer access panel
// renders one group per task with the view it has read; the groups differ
// only in their words and the view members they read.

const copy = {
  cover_taste_build: {
    id: 'taste',
    heading: 'Taste profile',
    lead: 'A vision model writes your taste profile from the covers you chose and passed over.',
    disclosure:
      'The model receives those covers with their sizes and the book’s format, never its title or files.',
    none: 'No taste profile model selected.',
    without: 'Continue without a taste profile',
    limit: (limit: string) =>
      `Mechanical Librarian asks for at most ${limit} taste profiles a day.`,
    codexCost: 'Taste profiles count against your plan’s usage limits, so no cost is shown.',
    useCodex: 'Use Codex for taste profiles',
    model: 'Taste profile model',
    recommended: 'Recommended. Measured for taste profiles.',
    test: 'Test taste profile model and prices',
    testing: 'Testing taste profile model…',
    use: 'Use for taste profile',
    saved: 'Taste profile model saved.',
    setup: 'Taste profile setup did not complete. Check the model and key, then try again.',
    lostSaved:
      'No answer came back. Check whether the taste profile model was saved before trying again.',
    lostCleared:
      'No answer came back. Check whether the taste profile model was cleared before trying again.',
    notCleared: 'The taste profile model was not cleared. Try again.',
    codexRefused: 'Codex was not chosen for taste profiles. Reload, then try again.',
  },
  cover_taste_judge: {
    id: 'suggestion',
    heading: 'Suggestions',
    lead: 'A vision model compares each work’s covers with your taste profile and suggests a switch.',
    disclosure:
      'The model receives those covers with their sizes, the book’s format and your taste profile’s rules. It never receives the book’s title or files.',
    none: 'No suggestion model selected.',
    without: 'Continue without suggestions',
    limit: (limit: string) => `Mechanical Librarian asks for at most ${limit} suggestions a day.`,
    codexCost: 'Suggestions count against your plan’s usage limits, so no cost is shown.',
    useCodex: 'Use Codex for suggestions',
    model: 'Suggestion model',
    recommended: 'Recommended. Measured for suggestions.',
    test: 'Test suggestion model and prices',
    testing: 'Testing suggestion model…',
    use: 'Use for suggestions',
    saved: 'Suggestion model saved.',
    setup: 'Suggestion setup did not complete. Check the model and key, then try again.',
    lostSaved:
      'No answer came back. Check whether the suggestion model was saved before trying again.',
    lostCleared:
      'No answer came back. Check whether the suggestion model was cleared before trying again.',
    notCleared: 'The suggestion model was not cleared. Try again.',
    codexRefused: 'Codex was not chosen for suggestions. Reload, then try again.',
  },
} satisfies Record<TasteTask, Record<string, unknown>>

// What each task reads from the view.
const members = (view: ReviewerAccessView, task: TasteTask) =>
  task === 'cover_taste_build'
    ? {
        mapping: view.taste_mapping,
        metadata: view.taste_metadata,
        limit: view.taste_daily_requests,
        recommendations: view.taste_recommendations,
      }
    : {
        mapping: view.judge_mapping,
        metadata: view.judge_metadata,
        limit: view.judge_daily_requests,
        recommendations: view.judge_recommendations,
      }

const prices = (metadata: CoverModelMetadata) =>
  `${metadata.prompt_price ?? 'Unavailable'} per input token, ${metadata.completion_price ?? 'Unavailable'} per output token, ${metadata.image_price ?? 'Unavailable'} per image, ${metadata.request_price ?? 'Unavailable'} per request.`

// Covers → Taste profile reads whether a model is chosen, so a change here
// makes its cached view stale.
const tasteProfileKey = ['covers', 'taste'] as const

const useTasteModel = (
  http: ReviewHttp,
  view: ReviewerAccessView,
  onCodexRefused: () => Promise<void>,
  task: TasteTask,
) => {
  const cache = useQueryClient()
  const words = copy[task]
  const mapping = members(view, task).mapping
  const [model, setModel] = useState(mapping?.engine === 'openrouter' ? mapping.model : '')
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
      http.testTasteModel(
        { expected_revision: view.revision, api_key: key, model },
        undefined,
        task,
      ),
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
        task,
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
    mutationFn: () => http.selectReviewer('codex', view.revision, undefined, task),
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
  // Continue without clears only this task (#1372 S11).
  const without = useMutation({
    mutationFn: () => http.selectReviewer(null, view.revision, undefined, task),
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
          ? words.lostCleared
          : refusalCopy(error, words.notCleared)
        : (latest === 'selection' || latest === 'codex') && uncertainWriteOutcome(error)
          ? words.lostSaved
          : latest === 'codex'
            ? codexRefusal(error, words.codexRefused)
            : credentialRefused(error)
              ? failureCopy.credential_unavailable
              : refusalCopy(error, words.setup)
  const failureUnder = (at: NonNullable<typeof latest>) =>
    failure !== null && latest === at ? <p role="alert">{failure}</p> : null
  const busy = test.isPending || selection.isPending || codex.isPending || without.isPending
  return {
    task,
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
  task = 'cover_taste_build',
}: {
  http: ReviewHttp
  view: ReviewerAccessView
  codexUsable: boolean
  codexManaged: boolean
  onCodexRefused: () => Promise<void>
  task?: TasteTask
}) => {
  const taste = useTasteModel(http, view, onCodexRefused, task)
  const { codex, without, failureUnder, busy } = taste
  const words = copy[task]
  const { mapping: selected, limit } = members(view, task)
  return (
    <section aria-labelledby={`${words.id}-reviewer-heading`}>
      <div className="settings-field-row">
        <div>
          <h3 id={`${words.id}-reviewer-heading`}>{words.heading}</h3>
          <p>{words.lead}</p>
          <p>{words.disclosure}</p>
        </div>
        <div className="settings-field-control">
          <p role="status">
            {selected === null ? (
              words.none
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
            {words.without}
          </button>
          {failureUnder('without')}
          <p>{words.limit(limit.toLocaleString('en-GB'))}</p>
        </div>
      </div>
      {codexManaged ? (
        <div className="settings-field-row">
          <div>
            <h4>Codex</h4>
            <p>{words.codexCost}</p>
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
              {words.useCodex}
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
  const { task, model, key, metadata, test, selection, latest, failureUnder, busy } = taste
  const words = copy[task]
  const { metadata: savedMetadata, recommendations } = members(view, task)
  const id = `${words.id}-openrouter`
  const saved = latest === 'selection' && selection.isSuccess
  // The pressed button goes disabled once the test it needs is spent, so focus
  // moves to the line that says the model was saved instead of to the page.
  const savedLine = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (saved) savedLine.current?.focus()
  }, [saved])
  const disclosure =
    metadata?.model === model ? metadata : savedMetadata?.model === model ? savedMetadata : null
  return (
    <div className="settings-field-row">
      <div>
        <h4>OpenRouter</h4>
      </div>
      <div className="settings-field-control">
        <ModelPicker
          id={`${id}-model`}
          label={words.model}
          value={model}
          disabled={busy}
          models={recommendations.map((recommended) => ({
            model: recommended,
            note: words.recommended,
          }))}
          onChange={(next) => {
            taste.setModel(next)
            taste.setMetadata(null)
            test.reset()
          }}
        />
        <label htmlFor={`${id}-key`}>Shared OpenRouter API key</label>
        <input
          id={`${id}-key`}
          type="password"
          value={key}
          disabled={busy}
          aria-describedby={`${id}-key-help`}
          onChange={(event) => {
            taste.setKey(event.target.value)
            taste.setMetadata(null)
            test.reset()
          }}
          autoComplete="new-password"
        />
        <small id={`${id}-key-help`}>
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
          {test.isPending ? words.testing : words.test}
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
          {words.use}
        </button>
        {failureUnder('selection')}
        {/* Said only for the latest action: Continue without replaces it. */}
        {saved ? (
          <p ref={savedLine} role="status" tabIndex={-1}>
            {words.saved}
          </p>
        ) : null}
      </div>
    </div>
  )
}
