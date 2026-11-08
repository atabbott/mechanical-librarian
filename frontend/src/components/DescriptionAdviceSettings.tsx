import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { BuildMismatchError, refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { ReviewHttp, SettingsView } from '../api/http'
import { offOn } from '../fieldControls'
import { focusAfterRetry, retryInPlace } from '../focusAfterRetry'
import { FieldRow } from './FieldRow'
import { SegmentedControl } from './SegmentedControl'
import { StaleBuildRead } from './StaleBuildRead'

type Preferences = NonNullable<SettingsView['document']['description_advice']>
const labels: Readonly<Record<keyof Preferences, string>> = {
  remove_marketing: 'Remove marketing material',
  remove_sales_boasts: 'Remove sales figures and bestseller boasts',
  remove_praise: 'Remove pull quotes and author praise',
  automatic_application: 'Automatically apply ready description advice',
}

export const DescriptionAdviceSettings = ({ http }: { http: ReviewHttp }) => {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
  })
  // Retry preferences, or Reload once the tab is on an earlier build.
  const preferencesRetry = useRef<HTMLButtonElement>(null)
  return (
    <section
      id="description-advice"
      className="description-advice-settings"
      aria-labelledby="description-advice-heading"
    >
      <h3 id="description-advice-heading" tabIndex={-1}>
        Description advice
      </h3>
      <p>
        An adviser can rewrite or combine source descriptions. Its text is always marked as advice.
      </p>
      <a href="/settings?group=reviewer">Choose the description adviser</a>
      {query.isPending ? (
        <p role="status">Loading description preferences…</p>
      ) : query.error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Description preferences were not loaded." ref={preferencesRetry} />
      ) : query.isError ? (
        <div role="alert">
          <p>Description preferences could not be loaded.</p>
          <button
            ref={preferencesRetry}
            onClick={() => {
              // The loading line replaces this button while it reads: the
              // heading, which stays, holds focus until the outcome takes it.
              const heading = () => document.getElementById('description-advice-heading')
              retryInPlace(
                heading,
                () => query.refetch(),
                (result) => (result.isSuccess ? heading() : preferencesRetry.current),
              )
            }}
          >
            Retry preferences
          </button>
        </div>
      ) : (
        <Editor http={http} view={query.data} />
      )}
    </section>
  )
}

const Editor = ({ http, view }: { http: ReviewHttp; view: SettingsView }) => {
  const cache = useQueryClient()
  const [base, setBase] = useState(view.document)
  const [draft, setDraft] = useState<Preferences>(view.document.description_advice)
  // A save that may have committed: the saved preferences are reloaded, so the
  // draft and its "Unsaved" note describe what was actually saved.
  const [unconfirmed, setUnconfirmed] = useState(false)
  const reloadButton = useRef<HTMLButtonElement>(null)
  // Set once a save or reload meets a newer build: neither can succeed again in
  // this tab, so the editor offers the reload in place of its message.
  const [staleLead, setStaleLead] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: () =>
      http.writeSettings({
        expected_revision: base.revision,
        document: { ...base, revision: base.revision + 1, description_advice: draft },
        secrets: [],
      }),
    retry: false,
    onMutate: () => setUnconfirmed(false),
    onSuccess: (saved) => {
      setBase(saved.document)
      cache.setQueryData(['settings'], saved)
    },
    onError: (error) => {
      if (error instanceof BuildMismatchError) {
        setStaleLead('Description preferences were not saved.')
        return
      }
      if (!uncertainWriteOutcome(error)) return
      setUnconfirmed(true)
      reload.mutate('unconfirmed')
    },
  })
  const locked = view.authority === 'file'
  const stale = view.document.revision !== base.revision
  const changed = JSON.stringify(draft) !== JSON.stringify(base.description_advice)
  // `owner` is the Reload button; `unconfirmed` is the reload after a save
  // that may have committed, whose message must outlive it.
  const reload = useMutation<SettingsView, Error, 'owner' | 'unconfirmed'>({
    mutationFn: () => http.getSettings(),
    retry: false,
    onSuccess: (fresh, source) => {
      cache.setQueryData(['settings'], fresh)
      setBase(fresh.document)
      setDraft(fresh.document.description_advice)
      if (source === 'owner') setUnconfirmed(false)
      mutation.reset()
      // The owner's Reload closes the message that holds it; focus moves to the
      // section heading instead of falling to the page.
      if (source === 'owner' && document.activeElement === reloadButton.current) {
        document.getElementById('description-advice-heading')?.focus()
      }
    },
    onError: (error, source) => {
      if (error instanceof BuildMismatchError)
        setStaleLead(
          source === 'unconfirmed' || unconfirmed
            ? 'No answer came back for the description preferences save.'
            : 'Saved preferences were not loaded.',
        )
      // A failure that replaces the message's Reload, or leaves focus on the
      // page, hands focus to the control now there.
      focusAfterRetry(() => reloadButton.current)
    },
  })
  // A pending or settled save keeps Save focusable; it ignores the press.
  const saveHeld = mutation.isPending || reload.isPending || !changed || staleLead !== null
  return (
    <div>
      <p>
        When automatic applying is on, ready advice is written with your write permission, unless
        you chose a description yourself.
      </p>
      {locked ? <p>These preferences are set in the deployment configuration.</p> : null}
      <div className="description-advice-fields">
        {(Object.keys(labels) as (keyof Preferences)[]).map((name) => (
          <FieldRow
            key={name}
            group
            title={labels[name]}
            changed={draft[name] !== base.description_advice[name]}
          >
            <SegmentedControl
              label={labels[name]}
              options={offOn}
              value={draft[name] ? 'on' : 'off'}
              disabled={locked || mutation.isPending || reload.isPending || stale}
              onChange={(choice) => {
                setDraft({ ...draft, [name]: choice === 'on' })
                setUnconfirmed(false)
                mutation.reset()
              }}
            />
          </FieldRow>
        ))}
      </div>
      <button
        disabled={locked || stale}
        aria-disabled={saveHeld || undefined}
        aria-busy={mutation.isPending || undefined}
        onClick={() => {
          if (!saveHeld) mutation.mutate()
        }}
      >
        {mutation.isPending ? 'Saving description preferences…' : 'Save description preferences'}
      </button>
      <p role="status">
        {mutation.isSuccess
          ? 'Description preferences saved.'
          : changed
            ? 'Unsaved. Activating the policy does not save them.'
            : ''}
      </p>
      {staleLead !== null ? (
        <StaleBuildRead key="stale" lead={staleLead} ref={reloadButton} />
      ) : mutation.isError || stale || unconfirmed ? (
        <div role="alert">
          <p>
            {!stale && unconfirmed
              ? 'No answer came back. Check whether the description preferences were saved before saving again.'
              : refusalCopy(
                  stale ? null : mutation.error,
                  'Description preferences were not saved. Reload them, then try again.',
                )}
          </p>
          <button
            ref={reloadButton}
            aria-disabled={reload.isPending || undefined}
            aria-busy={reload.isPending || undefined}
            onClick={() => {
              if (!reload.isPending) reload.mutate('owner')
            }}
          >
            {reload.isPending ? 'Reloading preferences…' : 'Reload description preferences'}
          </button>
          {reload.isError ? <p>The saved preferences could not be loaded. Try again.</p> : null}
        </div>
      ) : null}
    </div>
  )
}
