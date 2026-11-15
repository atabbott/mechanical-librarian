import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

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
    // A build that failed while no model was chosen is shown, once a model is
    // chosen again, as one that was not written, with Try again.
    case 'unconfigured':
    case 'unavailable':
      // Beside a profile in use, the line is about the new version only.
      return view.profile === null
        ? 'The taste profile could not be written.'
        : 'The taste profile could not be updated.'
    case 'invalid_output':
      return 'The model returned a profile that was too long or incomplete, so it was not kept.'
    case 'ceiling':
      return `Mechanical Librarian has asked for ${String(view.daily_limit)} taste profiles today, the daily limit. Try again after ${at}.`
    case 'unknown_charge':
      return `OpenRouter did not confirm what the last request cost, so taste profiles wait until ${at}.`
    case 'interrupted':
      return 'Writing the taste profile stopped when Mechanical Librarian restarted.'
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

// A ceiling or an unconfirmed charge names when to try again; until then a
// retry would only be refused, so Try again waits for that time.
function useWaitsUntil(retryAfter: number | null): boolean {
  const [now, setNow] = useState(() => Date.now())
  const waiting = retryAfter !== null && now < retryAfter * 1000
  useEffect(() => {
    if (!waiting) return
    const timer = window.setTimeout(
      () => {
        setNow(Date.now())
      },
      Math.min(retryAfter * 1000 - Date.now() + 1000, 2 ** 31 - 1),
    )
    return () => {
      window.clearTimeout(timer)
    }
  }, [waiting, retryAfter, now])
  return waiting
}

function EmptyLine({ view }: { view: TasteProfile }) {
  return (
    <p>
      Mechanical Librarian writes it once you have chosen 25 covers in{' '}
      <Link to="/library" search={{ lens: 'all', view: 'covers', tab: 'decide' }}>
        To decide
      </Link>
      . You have chosen {String(view.chosen)}.
    </p>
  )
}

// The build's state, said once: with no profile yet it is the region's
// content under its heading, and while a first profile is written that line
// is the heading itself (#1372, prototype).
function BuildState({ view }: { view: TasteProfile }) {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const region = useRef<HTMLElement>(null)
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
  const waiting = useWaitsUntil(build?.failure ? build.retry_after : null)
  const first = view.profile === null
  const heading = first ? <h2 id="taste-empty">No taste profile yet</h2> : null
  if (!view.configured)
    return first ? (
      <section className="taste-empty" aria-labelledby="taste-empty">
        {heading}
        <NotConfigured />
      </section>
    ) : (
      <div className="taste-build">
        <NotConfigured />
      </div>
    )
  if (build === null)
    return first ? (
      <section className="taste-empty" aria-labelledby="taste-empty">
        {heading}
        <EmptyLine view={view} />
      </section>
    ) : null
  if (build.failure === null)
    return (
      <section
        ref={region}
        tabIndex={-1}
        className={first ? 'taste-build taste-building' : 'taste-build'}
        role="status"
      >
        {first ? (
          <>
            <span className="taste-spinner" aria-hidden="true" />
            <h2>Writing your taste profile</h2>
          </>
        ) : (
          <p>Writing your taste profile</p>
        )}
      </section>
    )
  const error = retry.error
  return (
    <section
      ref={region}
      tabIndex={-1}
      className={first ? 'taste-build taste-empty' : 'taste-build'}
      aria-labelledby={first ? 'taste-empty' : undefined}
    >
      {heading}
      <div className="taste-failure" role="alert">
        <p>{failureCopy(build.failure, view, build.retry_after)}</p>
        {waiting ? null : (
          <button
            type="button"
            aria-disabled={retry.isPending || undefined}
            onClick={() => {
              if (!retry.isPending) retry.mutate(build.build_id)
            }}
          >
            Try again
          </button>
        )}
        {error ? (
          <p>
            {uncertainWriteOutcome(error)
              ? lostCopy
              : refusalCopy(error, 'The taste profile was not requested. Reload and try again.')}
          </p>
        ) : null}
      </div>
    </section>
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
      {/* Wide screens put Versions beside the rules, as the prototype does. */}
      <div className="taste-body">
        <div className="taste-rules">
          {sections.map(({ section, rules }) => (
            <section key={section} className="taste-section" aria-labelledby={`taste-${section}`}>
              <h2 id={`taste-${section}`}>{sectionHeadings[section]}</h2>
              <ol>
                {rules.map((rule) => (
                  <li key={rule.number} value={rule.number}>
                    <span>{rule.text}</span>
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
        <Versions view={view} />
      </div>
    </>
  )
}

function Versions({ view }: { view: TasteProfile }) {
  return (
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
      <Profile view={view} />
    </div>
  )
}
