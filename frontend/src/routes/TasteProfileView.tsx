import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useRef } from 'react'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { TasteBuildFailure, TasteProfileView as TasteProfile } from '../api/http'
import { formatClock, formatDay } from '../dates'
import { focusAfterRetry } from '../focusAfterRetry'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { routeFailureCopy } from '../routeFailure'
import { useReviewContext } from '../router'

// Covers → Taste profile (#1372): the profile the builder model wrote from the
// owner's cover choices, read-only. It influences nothing yet. Rule text is
// model output, so it is only ever rendered as React text.

type Rule = NonNullable<TasteProfile['profile']>['rules'][number]
const sectionHeadings = {
  drawn_to: 'What you’re drawn to',
  avoid: 'What you avoid',
  trade_offs: 'How you trade them off',
  keep_or_switch: 'When to keep or switch',
} satisfies Record<Rule['section'], string>
// A build running or polled at this pace until it ends.
const buildingPollMilliseconds = 5_000
const lostCopy = 'No answer came back. Check whether the taste profile is being written.'

const failureCopy = (
  failure: TasteBuildFailure,
  view: TasteProfile,
  retryAfter: number | null,
): string => {
  // The decoder holds a reset time to exactly the two failures that wait.
  const at = formatClock(retryAfter ?? 0)
  switch (failure) {
    case 'unavailable':
      return 'OpenRouter did not answer, so the taste profile was not written.'
    case 'invalid_output':
      return 'The model returned a profile that was too long or incomplete, so it was not kept.'
    case 'ceiling':
      return `Mechanical Librarian has asked for ${String(view.daily_limit)} taste profiles today, the daily limit. Try again after ${at}.`
    case 'unknown_charge':
      return `OpenRouter did not confirm what the last request cost, so taste profiles wait until ${at}.`
    case 'interrupted':
      return 'Writing the taste profile stopped when Mechanical Librarian restarted.'
    case 'unconfigured':
      return 'Choose a taste profile model in Settings.'
  }
}

function NotConfigured() {
  return (
    <p>
      Choose a taste profile model in{' '}
      <Link to="/settings" search={{ group: 'reviewer' }}>
        Settings
      </Link>
      .
    </p>
  )
}

function BuildState({ view }: { view: TasteProfile }) {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const region = useRef<HTMLDivElement>(null)
  const build = view.build
  const retry = useMutation({
    mutationFn: (buildId: string) => http.retryTasteBuild(buildId),
    retry: false,
    onSuccess: (next) => {
      cache.setQueryData(['covers', 'taste'], next)
      focusAfterRetry(
        () => region.current,
        () => region.current,
      )
    },
  })
  if (build === null) return null
  if (build.failure === null)
    return (
      <div ref={region} tabIndex={-1} className="taste-build">
        <p role="status">Writing your taste profile</p>
      </div>
    )
  const error = retry.error
  return (
    <div ref={region} tabIndex={-1} className="taste-build" role="alert">
      {build.failure === 'unconfigured' ? (
        <NotConfigured />
      ) : (
        <p>{failureCopy(build.failure, view, build.retry_after)}</p>
      )}
      <button
        type="button"
        aria-disabled={retry.isPending || undefined}
        onClick={() => {
          if (!retry.isPending) retry.mutate(build.build_id)
        }}
      >
        Try again
      </button>
      {error ? (
        <p>
          {uncertainWriteOutcome(error)
            ? lostCopy
            : refusalCopy(error, 'The taste profile was not requested. Reload and try again.')}
        </p>
      ) : null}
    </div>
  )
}

function Profile({ view }: { view: TasteProfile }) {
  const profile = view.profile
  if (profile === null) return null
  const sections = (Object.keys(sectionHeadings) as Rule['section'][])
    .map((section) => ({
      section,
      rules: profile.rules.filter((rule) => rule.section === section),
    }))
    .filter((group) => group.rules.length > 0)
  return (
    <>
      <dl className="taste-meta">
        <div>
          <dt>Version</dt>
          <dd>{String(profile.version)}</dd>
        </div>
        <div>
          <dt>Built</dt>
          <dd>{formatDay(profile.built_at)}</dd>
        </div>
        <div>
          <dt>Decisions</dt>
          <dd>{String(profile.decisions)}</dd>
        </div>
        <div>
          <dt>Rules</dt>
          <dd>{`${String(profile.rules.length)} of 40`}</dd>
        </div>
      </dl>
      {sections.map(({ section, rules }) => (
        <section key={section} className="taste-section" aria-labelledby={`taste-${section}`}>
          <h2 id={`taste-${section}`}>{sectionHeadings[section]}</h2>
          <ol>
            {rules.map((rule) => (
              <li key={rule.number} value={rule.number}>
                {rule.text}
              </li>
            ))}
          </ol>
        </section>
      ))}
      <section className="taste-versions" aria-labelledby="taste-versions">
        <h2 id="taste-versions">Versions</h2>
        <ul>
          {view.versions.map((version, index) => (
            <li key={version.version}>
              <strong>
                {`Version ${String(version.version)}`}
                {index === 0 ? ' (in use)' : null}
              </strong>{' '}
              <span>{`Built ${formatDay(version.built_at)} from ${String(version.decisions)} decisions`}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}

export default function TasteProfileView() {
  const { http } = useReviewContext()
  const read = useQuery({
    queryKey: ['covers', 'taste'] as const,
    queryFn: (context) => http.getTasteProfile(context.signal, queryWorkClass(context)),
    refetchInterval: (query) =>
      query.state.data?.build?.failure === null ? buildingPollMilliseconds : false,
    staleTime: 15_000,
    retry: false,
  })
  if (read.isPending) return <p role="status">Loading taste profile…</p>
  if (read.isError)
    return (
      <div className="covers-failure" role="alert">
        <p>{routeFailureCopy(read.error).message}</p>
        <button type="button" onClick={() => void read.refetch()}>
          Reload
        </button>
      </div>
    )
  const view = read.data
  return (
    <div className="taste-profile">
      <BuildState view={view} />
      {view.profile === null ? (
        <section className="taste-empty" aria-labelledby="taste-empty">
          <h2 id="taste-empty">No taste profile yet</h2>
          {view.build !== null ? null : !view.configured ? (
            <NotConfigured />
          ) : (
            <p>
              Mechanical Librarian writes it once you have chosen 25 covers in{' '}
              <Link to="/library" search={{ lens: 'all', view: 'covers', tab: 'decide' }}>
                To decide
              </Link>
              . You have chosen {String(view.chosen)}.
            </p>
          )}
        </section>
      ) : (
        <Profile view={view} />
      )}
    </div>
  )
}
