import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { BuildMismatchError, refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { ReviewHttp, SettingsView } from '../api/http'
import type { Segment } from '../fieldControls'
import { focusAfterRetry, retryInPlace } from '../focusAfterRetry'
import { SegmentedControl } from './SegmentedControl'
import { StaleBuildRead } from './StaleBuildRead'

type Level = SettingsView['document']['matching']['work_identity']
const options: readonly Segment<Level>[] = [
  { value: 'identifier', label: 'Confirmed matches only' },
  { value: 'title_and_creator_exact', label: 'Same title and every author' },
  { value: 'title_and_creator', label: 'Same title and any author' },
]

// While current cover comparisons are being checked against a saved level,
// the setting is read again until the check settles (#1355).
const checkPending = (view: SettingsView | undefined) => (view?.matching_check?.checking ?? 0) > 0

export const MatchingSettings = ({ http }: { http: ReviewHttp }) => {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
    refetchInterval: (current) => (checkPending(current.state.data) ? 2_000 : false),
  })
  // Retry book matching, or Reload once the tab is on an earlier build.
  const matchingRetry = useRef<HTMLButtonElement>(null)
  return (
    <section
      id="book-matching"
      className="matching-settings"
      aria-labelledby="book-matching-heading"
    >
      <h3 id="book-matching-heading" tabIndex={-1}>
        Book matching
      </h3>
      <p>
        Which source records count as the same book when you review. Edition details, such as ISBN,
        publisher and narrators, always need a confirmed match.
      </p>
      {query.isPending ? (
        <p role="status">Loading book matching…</p>
      ) : query.error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Book matching was not loaded." ref={matchingRetry} />
      ) : query.isError ? (
        <div role="alert">
          <p>Book matching could not be loaded.</p>
          <button
            ref={matchingRetry}
            onClick={() => {
              // The heading, which stays, holds focus while the setting reads.
              const heading = () => document.getElementById('book-matching-heading')
              retryInPlace(
                heading,
                () => query.refetch(),
                (result) => (result.isSuccess ? heading() : matchingRetry.current),
              )
            }}
          >
            Retry book matching
          </button>
        </div>
      ) : (
        <Editor http={http} view={query.data} />
      )}
    </section>
  )
}

const checkLine = (view: SettingsView, levelChanged: boolean) => {
  const check = view.matching_check
  if (check === null) return ''
  // File mode cannot save the setting; a restart checks it again (#1355).
  const retry =
    view.authority === 'file'
      ? 'Restart the Mechanical Librarian service to retry.'
      : 'Save the setting again to retry.'
  if (check.failed === 1)
    return `1 work could not be checked against the new setting. Its changes wait for that check. ${retry}`
  if (check.failed > 1)
    return `${String(check.failed)} works could not be checked against the new setting. Their changes wait for those checks. ${retry}`
  if (check.checking > 0) return 'Works are being checked against the new setting.'
  if (levelChanged && check.current_works === 0) return 'No works needed checking.'
  return ''
}

const Editor = ({ http, view }: { http: ReviewHttp; view: SettingsView }) => {
  const cache = useQueryClient()
  const [base, setBase] = useState(view.document)
  const [draft, setDraft] = useState<Level>(view.document.matching.work_identity)
  // Set by a save that changed the level: its check lines describe that save.
  const [levelChanged, setLevelChanged] = useState(false)
  // A save that may have committed: the saved setting is reloaded, so the
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
        document: {
          ...base,
          revision: base.revision + 1,
          matching: { ...base.matching, work_identity: draft },
        },
        secrets: [],
      }),
    retry: false,
    onMutate: () => setUnconfirmed(false),
    onSuccess: (saved) => {
      setLevelChanged(saved.document.matching.work_identity !== base.matching.work_identity)
      setBase(saved.document)
      cache.setQueryData(['settings'], saved)
    },
    onError: (error) => {
      if (error instanceof BuildMismatchError) {
        setStaleLead('Book matching was not saved.')
        return
      }
      if (!uncertainWriteOutcome(error)) return
      setUnconfirmed(true)
      reload.mutate('unconfirmed')
    },
  })
  const locked = view.authority === 'file'
  const stale = view.document.revision !== base.revision
  const changed = draft !== base.matching.work_identity
  // Saving the same value again retries every work whose check gave up.
  const retryable = (view.matching_check?.failed ?? 0) > 0
  const reload = useMutation<SettingsView, Error, 'owner' | 'unconfirmed'>({
    mutationFn: () => http.getSettings(),
    retry: false,
    onSuccess: (fresh, source) => {
      cache.setQueryData(['settings'], fresh)
      setBase(fresh.document)
      setDraft(fresh.document.matching.work_identity)
      if (source === 'owner') setUnconfirmed(false)
      mutation.reset()
      if (source === 'owner' && document.activeElement === reloadButton.current) {
        document.getElementById('book-matching-heading')?.focus()
      }
    },
    onError: (error, source) => {
      if (error instanceof BuildMismatchError)
        setStaleLead(
          source === 'unconfirmed' || unconfirmed
            ? 'No answer came back for the book matching save.'
            : 'Book matching was not loaded.',
        )
      focusAfterRetry(() => reloadButton.current)
    },
  })
  // Another section's save moved the settings revision. With nothing unsaved
  // here, the newer settings are taken as they are; only unsaved edits are
  // stale, and say so (#1363).
  if (
    view.document.revision > base.revision &&
    !changed &&
    !unconfirmed &&
    !mutation.isPending &&
    !reload.isPending
  ) {
    setBase(view.document)
    setDraft(view.document.matching.work_identity)
  }
  // A pending or settled save keeps Save focusable; it ignores the press.
  const saveHeld =
    mutation.isPending || reload.isPending || !(changed || retryable) || staleLead !== null
  const check = checkLine(view, levelChanged)
  return (
    <div>
      {locked ? <p>Book matching is set in the deployment configuration.</p> : null}
      <SegmentedControl
        label="Book matching"
        options={options}
        value={draft}
        disabled={locked || mutation.isPending || reload.isPending || stale}
        onChange={(choice) => {
          setDraft(choice)
          setUnconfirmed(false)
          mutation.reset()
        }}
      />
      {draft === 'identifier' ? null : (
        <p>
          A field that needs you also offers values from these records, and covers from them say
          “Same title and author”. Mechanical Librarian does not choose them for you.
        </p>
      )}
      <button
        disabled={locked || stale}
        aria-disabled={saveHeld || undefined}
        aria-busy={mutation.isPending || undefined}
        onClick={() => {
          if (!saveHeld) mutation.mutate()
        }}
      >
        {mutation.isPending ? 'Saving book matching…' : 'Save book matching'}
      </button>
      <p role="status">
        {mutation.isSuccess && !changed
          ? 'Book matching saved.'
          : changed
            ? 'Unsaved. Activating the policy does not save it.'
            : ''}
      </p>
      <p role="status">{check}</p>
      {staleLead !== null ? (
        <StaleBuildRead key="stale" lead={staleLead} ref={reloadButton} />
      ) : mutation.isError || stale || unconfirmed ? (
        <div role="alert">
          <p>
            {!stale && unconfirmed
              ? 'No answer came back. Check whether book matching was saved before saving again.'
              : refusalCopy(
                  stale ? null : mutation.error,
                  'Book matching was not saved. Reload it, then try again.',
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
            {reload.isPending ? 'Reloading book matching…' : 'Reload book matching'}
          </button>
          {reload.isError ? <p>The saved setting could not be loaded. Try again.</p> : null}
        </div>
      ) : null}
    </div>
  )
}
