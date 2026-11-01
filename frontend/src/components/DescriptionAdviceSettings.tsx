import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { ReviewHttp, SettingsView } from '../api/http'
import { offOn } from '../fieldControls'
import { FieldRow } from './FieldRow'
import { SegmentedControl } from './SegmentedControl'

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
        A configured adviser can rewrite or combine provider descriptions. Its text is always marked
        as advised, authored content.
      </p>
      <a href="/settings?group=reviewer">
        Choose the description engine and review its data and usage disclosure
      </a>
      {query.isPending ? (
        <p role="status">Loading description preferences…</p>
      ) : query.isError ? (
        <div role="alert">
          <p>Description preferences are unavailable.</p>
          <button onClick={() => void query.refetch()}>Retry preferences</button>
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
      if (!uncertainWriteOutcome(error)) return
      setUnconfirmed(true)
      reload.mutate('unconfirmed')
    },
  })
  const locked = view.authority === 'file'
  const stale = view.document.revision !== base.revision
  const changed = JSON.stringify(draft) !== JSON.stringify(base.description_advice)
  const reload = useMutation({
    // `owner` is the Reload button; `unconfirmed` is the reload after a save
    // that may have committed, whose message must outlive it.
    mutationFn: (_source: 'owner' | 'unconfirmed') => http.getSettings(),
    retry: false,
    onSuccess: (fresh, source) => {
      cache.setQueryData(['settings'], fresh)
      setBase(fresh.document)
      setDraft(fresh.document.description_advice)
      if (source === 'owner') setUnconfirmed(false)
      mutation.reset()
    },
  })
  return (
    <div>
      <p>
        Cleanup choices start on. Automatic application starts off and is independent of engine
        selection. When enabled, ready advice uses the existing consent, backup and write checks; an
        owner decision takes precedence.
      </p>
      {locked ? <p>These preferences are managed by deployment configuration.</p> : null}
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
        disabled={locked || mutation.isPending || reload.isPending || stale || !changed}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? 'Saving description preferences…' : 'Save description preferences'}
      </button>
      <p role="status">
        {mutation.isSuccess
          ? 'Description preferences saved.'
          : changed
            ? 'Unsaved. These save with their own button, not with the policy.'
            : 'Saved with their own button, not with the policy.'}
      </p>
      {mutation.isError || stale || unconfirmed ? (
        <div role="alert">
          <p>
            {!stale && unconfirmed
              ? 'Description preferences could not be confirmed. They may have been saved; the saved preferences were reloaded. Check them before saving again.'
              : refusalCopy(
                  stale ? null : mutation.error,
                  'Description preferences could not be saved against the current settings. Reload the saved preferences before trying again.',
                )}
          </p>
          <button disabled={reload.isPending} onClick={() => reload.mutate('owner')}>
            {reload.isPending ? 'Reloading preferences…' : 'Reload description preferences'}
          </button>
          {reload.isError ? (
            <p>Saved preferences could not be loaded. Retry when the connection returns.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
