import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { SyntheticEvent } from 'react'

import {
  ApiError,
  normalizeTasteRule,
  refusalCopy,
  tasteCodePoints,
  tasteRuleRefusal,
  uncertainWriteOutcome,
} from '../api/http'
import type {
  TasteBuildFailure,
  TasteProfileView as TasteProfile,
  TasteRuleEdit,
  TasteRuleRefusal,
} from '../api/http'
import { Button } from '../components/Button'
import { StatusPill } from '../components/StatusPill'
import { formatClock, formatDay } from '../dates'
import { picksSaved, tasteQuery } from '../coverPicks'
import { focusAfterRetry } from '../focusAfterRetry'
import { useRequiredQuery as useQuery } from '../queryWork'
import { routeFailureCopy } from '../routeFailure'
import { tasteSectionHeadings } from '../tasteSections'
import { useReviewContext } from '../router'
import { useTasteSwitching } from '../tasteSwitching'
import { SwitchingControl, SwitchingPreview } from './TasteSwitching'

// Covers → Taste profile (#1372): the profile the builder model wrote from the
// owner's cover choices, which the owner edits rule by rule; each saved change
// is a new version at once. The suggestion model compares covers with it
// (#1372 PR 4b). Rule text is model output or the owner's, so it is only ever
// rendered as React text.

type Rule = NonNullable<TasteProfile['profile']>['rules'][number]
const sectionHeadings = tasteSectionHeadings satisfies Record<Rule['section'], string>
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
      Set up the taste profile model in{' '}
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

// No profile yet: the picks saved so far, and the way to make the rest
// (#1372 PR 3).
function EmptyLine({ view, onStart }: { view: TasteProfile; onStart: () => void }) {
  return (
    <>
      {view.chosen > 0 ? <p>{picksSaved(view.chosen, view.required, view.since_upgrade)}</p> : null}
      <button type="button" className="button-primary" onClick={onStart}>
        Start 25 picks
      </button>
    </>
  )
}

// The build's state, said once: with no profile yet it is the region's
// content under its heading, and while a first profile is written that line
// is the heading itself (#1372, prototype).
function BuildState({
  view,
  onStart,
  focusBuild,
}: {
  view: TasteProfile
  onStart: () => void
  focusBuild: boolean
}) {
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
  // After the 25th pick, Covers opens here with focus on the build's state.
  useEffect(() => {
    if (focusBuild) region.current?.focus()
  }, [focusBuild])
  // 25 picks count and the next schedule check, within a minute, starts the
  // first build: it is said as written already (S8).
  const due = first && build === null && view.configured && view.chosen >= view.required
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
  if (build === null && !due)
    return first ? (
      <section ref={region} tabIndex={-1} className="taste-empty" aria-labelledby="taste-empty">
        {heading}
        <EmptyLine view={view} onStart={onStart} />
      </section>
    ) : null
  const writing = (
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
  if (build === null) return writing
  if (build.failure === null) return writing
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

// A stop lifts at the start of a day, which reads as midnight, not a time
// like 0:00 that could be the end of one (#1372 PR 4b).
const clockOrMidnight = (seconds: number) => {
  const clock = formatClock(seconds)
  return /^0?0[:.]00$/u.test(clock) ? 'midnight' : clock
}
const works = (count: number) => (count === 1 ? '1 work' : `${count.toLocaleString('en-GB')} works`)

// The suggestion model's state, said under the build's (#1372 PR 4b): not set
// up, stopped for the day, and the works whose check failed with Try again.
// Try again's answer is said in a status line that stays mounted, and focus
// moves to it; any refusal re-reads the profile, so the count shown is the
// server's, and only a version no longer in use reads as stale.
function JudgeState({ view }: { view: TasteProfile }) {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const status = useRef<HTMLParagraphElement>(null)
  const [asked, setAsked] = useState<number | null>(null)
  const { judge, profile } = view
  const retry = useMutation({
    mutationFn: ({ version }: { version: number; count: number }) => http.retryTasteJudge(version),
    retry: false,
    onMutate: () => {
      setAsked(null)
    },
    onSuccess: (next, { count }) => {
      cache.setQueryData(['covers', 'taste'], next)
      setAsked(count)
      focusAfterRetry(
        () => status.current,
        () => status.current?.parentElement,
      )
    },
    onError: () => {
      void cache.invalidateQueries({ queryKey: ['covers', 'taste'] })
    },
  })
  const waiting = useWaitsUntil(judge.retry_after)
  if (profile === null) return null
  if (!judge.configured)
    return (
      <div className="taste-build">
        <p>
          Set up the suggestion model in{' '}
          <Link to="/settings" search={{ group: 'reviewer' }}>
            Settings
          </Link>
          .
        </p>
      </div>
    )
  const at = clockOrMidnight(judge.retry_after ?? 0)
  const error = retry.error
  const stopped =
    judge.stopped === null || !waiting
      ? null
      : judge.stopped === 'ceiling'
        ? `Mechanical Librarian has asked for ${judge.daily_limit.toLocaleString('en-GB')} suggestions today, the daily limit. Suggestions resume after ${at}.`
        : `OpenRouter did not confirm what the last request cost, so suggestions wait until ${at}.`
  const confirming =
    asked === null || judge.failed > 0 || error
      ? ''
      : `The suggestion model will check ${works(asked)} again.`
  if (stopped === null && judge.failed === 0 && !error && confirming === '') return null
  return (
    <div className="taste-build taste-judge">
      {stopped === null ? null : <p>{stopped}</p>}
      <p className="taste-judge-status" role="status" tabIndex={-1} ref={status}>
        {confirming}
      </p>
      {judge.failed === 0 && !error ? null : (
        <div className="taste-failure" role="alert">
          {judge.failed === 0 ? null : (
            <p>
              {`The suggestion model could not check ${works(judge.failed)} against your taste profile.`}
              {/* Try again is offered again once the stop lifts. */}
              {stopped === null ? null : ` Try again after ${at}.`}
            </p>
          )}
          {judge.failed === 0 || stopped !== null ? null : (
            <button
              type="button"
              aria-disabled={retry.isPending || undefined}
              onClick={() => {
                if (!retry.isPending)
                  retry.mutate({ version: profile.version, count: judge.failed })
              }}
            >
              Try again
            </button>
          )}
          {error ? (
            <p>
              {uncertainWriteOutcome(error)
                ? 'No answer came back. Check whether the suggestion model is checking the works again.'
                : error instanceof ApiError && error.status === 409 && error.code === 'stale'
                  ? staleCopy
                  : refusalCopy(
                      error,
                      'The suggestion model did not check the works again. Reload and try again.',
                    )}
            </p>
          ) : null}
        </div>
      )}
    </div>
  )
}

// The editor's refusals, the prototype's words where it had them (#1372 PR 2).
const refusalLines: Record<TasteRuleRefusal | 'rule_text' | 'rule_limit', string> = {
  rule_empty: 'Write a rule first.',
  rule_too_long: '400 characters at most.',
  address: 'No web addresses or file paths.',
  profile_full: 'The taste profile has no room for this rule. Shorten it or remove another rule.',
  // Credential-shaped or hidden text passes the browser's own check.
  rule_text:
    'Use plain words. Leave out web addresses, file paths, passwords and hidden characters.',
  rule_limit: '40 rules is the limit.',
}
const limitCopy = refusalLines.rule_limit
const staleCopy = 'The taste profile changed since the page loaded. Nothing was changed.'

// Which rule the editor is open on, and the version shown when it opened: a
// re-read while editing makes Save stale, never an edit of a renumbered rule.
type Editing =
  | { kind: 'rule'; number: number; version: number }
  | { kind: 'new'; section: Rule['section']; version: number }
type FocusTarget = { pencil: number } | { add: Rule['section'] } | 'status'

const Pencil = () => (
  <svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 20h4L19 9l-4-4L4 16z" />
    <path d="M13 7l4 4" />
  </svg>
)

function RuleForm({
  number,
  initial,
  removable,
  pending,
  refusal,
  onSave,
  onCancel,
  onRemove,
}: {
  number: number
  initial: string
  removable: boolean
  pending: boolean
  refusal: string | null
  onSave: (text: string, setDraft: (text: string) => void) => void
  onCancel: () => void
  onRemove: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const element = input.current
    if (!element) return
    element.focus()
    element.setSelectionRange(element.value.length, element.value.length)
  }, [])
  const id = `taste-rule-${String(number)}`
  const submit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    if (!pending)
      onSave(draft, (text) => {
        setDraft(text)
        input.current?.focus()
      })
  }
  return (
    <form
      className="taste-edit"
      onSubmit={submit}
      onKeyDown={(event) => {
        // Escape cancels, as Cancel does; not while an input method composes.
        if (event.key !== 'Escape' || event.nativeEvent.isComposing || pending) return
        event.preventDefault()
        onCancel()
      }}
    >
      <label className="sr-only" htmlFor={id}>
        {`Rule ${String(number)}`}
      </label>
      <input
        ref={input}
        id={id}
        value={draft}
        // What is typed during a save would not be in it, so it waits.
        readOnly={pending}
        autoComplete="off"
        aria-describedby={`${id}-count`}
        onChange={(event) => {
          setDraft(event.target.value)
        }}
      />
      <div className="taste-edit-actions">
        <Button type="submit" variant="primary" aria-disabled={pending || undefined}>
          Save rule
        </Button>
        <Button
          type="button"
          aria-disabled={pending || undefined}
          onClick={() => {
            if (!pending) onCancel()
          }}
        >
          Cancel
        </Button>
        {removable ? (
          <Button
            type="button"
            variant="danger"
            aria-disabled={pending || undefined}
            onClick={() => {
              if (!pending) onRemove()
            }}
          >
            Remove
          </Button>
        ) : null}
      </div>
      <small className="taste-count" id={`${id}-count`}>
        {`${String(tasteCodePoints(draft))} of 400 characters`}
      </small>
      {refusal === null ? null : (
        <p className="taste-refusal" role="alert">
          {refusal}
        </p>
      )}
    </form>
  )
}

// The line under the editor for a refused edit: the server's closed code where
// it has one, else the fallback.
const refusalLine = (error: unknown, removing: boolean): string =>
  error instanceof ApiError && error.status === 422 && Object.hasOwn(refusalLines, error.code)
    ? refusalLines[error.code as keyof typeof refusalLines]
    : refusalCopy(
        error,
        removing
          ? 'The rule was not removed. Reload and try again.'
          : 'The rule was not saved. Reload and try again.',
      )

// An edit whose answer was lost; the re-read says whether it landed.
interface Uncertain {
  action: TasteRuleEdit['action']
  number: number | null
}

// The editor's state and its one mutation: which rule is open, the line under
// it, the status line, the 40-rule line, and where focus goes next.
function useTasteRuleEditor(view: TasteProfile) {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const container = useRef<HTMLDivElement>(null)
  const uncertain = useRef<Uncertain | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const [limitAt, setLimitAt] = useState<Rule['section'] | null>(null)
  const [focus, setFocus] = useState<FocusTarget | null>(null)
  const save = useMutation({
    mutationFn: ({ version, edit }: { version: number; edit: TasteRuleEdit }) =>
      http.editTasteRule(version, edit),
    retry: false,
  })
  useLayoutEffect(() => {
    if (focus === null) return
    const root = container.current
    const target =
      focus === 'status'
        ? root?.querySelector<HTMLElement>('.taste-status')
        : 'pencil' in focus
          ? root?.querySelector<HTMLElement>(`[data-taste-pencil="${String(focus.pencil)}"]`)
          : root?.querySelector<HTMLElement>(`[data-taste-add="${focus.add}"]`)
    // A new rule's pencil exists once the answer is rendered, which can be a
    // render after the editor closed.
    if (!target) return
    target.focus()
    setFocus(null)
  }, [focus, view])
  const profile = view.profile
  const close = (target: FocusTarget) => {
    uncertain.current = null
    setEditing(null)
    setRefusal(null)
    setLimitAt(null)
    setFocus(target)
  }
  // A re-read without the open rule (a lost Remove that landed, or a rebuild
  // that renumbered) closes the editor and says why in the status line.
  const missing =
    editing?.kind === 'rule' &&
    profile !== null &&
    !profile.rules.some((rule) => rule.number === editing.number)
      ? editing
      : null
  useLayoutEffect(() => {
    if (missing === null) return
    const lost = uncertain.current
    setStatus(
      lost?.action === 'remove' && lost.number === missing.number
        ? `Rule ${String(missing.number)} removed.`
        : staleCopy,
    )
    close('status')
  }, [missing])
  const pending = save.isPending
  const open = (next: Editing) => {
    if (pending) return
    // One rule at a time: opening another editor discards the open draft.
    uncertain.current = null
    setEditing(next)
    setRefusal(null)
    setLimitAt(null)
  }
  const send = (
    current: Editing,
    edit: TasteRuleEdit,
    done: (next: TasteProfile) => { status: string; focus: FocusTarget },
  ) => {
    const removing = edit.action === 'remove'
    save.mutate(
      { version: current.version, edit },
      {
        onSuccess: (next) => {
          cache.setQueryData(['covers', 'taste'], next)
          const outcome = done(next)
          setStatus(outcome.status)
          close(outcome.focus)
        },
        onError: (error) => {
          const reread = () => void cache.invalidateQueries({ queryKey: ['covers', 'taste'] })
          if (error instanceof ApiError && error.status === 409 && error.code === 'stale') {
            // Numbers may have changed under a rebuild, so the draft is not kept.
            setStatus(staleCopy)
            close('status')
            reread()
            return
          }
          if (uncertainWriteOutcome(error)) {
            uncertain.current = {
              action: edit.action,
              number: edit.action === 'add' ? null : edit.number,
            }
            setRefusal(
              removing
                ? 'No answer came back. Check whether the rule was removed.'
                : 'No answer came back. Check whether the rule was saved.',
            )
            reread()
            return
          }
          setRefusal(refusalLine(error, removing))
        },
      },
    )
  }
  const saveRule = (current: Editing, typed: string, setDraft: (text: string) => void) => {
    if (profile === null) return
    const text = normalizeTasteRule(typed)
    const rule =
      current.kind === 'rule'
        ? profile.rules.find((item) => item.number === current.number)
        : undefined
    if (current.kind === 'rule' && rule?.text === text) {
      // Unchanged: nothing to save. After a lost answer the re-read shows the
      // save landed, so say so; otherwise there is nothing to say.
      const landed =
        uncertain.current?.action === 'edit' && uncertain.current.number === current.number
      if (landed) setStatus(`Rule ${String(current.number)} saved.`)
      close({ pencil: current.number })
      return
    }
    const refused = tasteRuleRefusal(
      text,
      profile.rules,
      current.kind === 'rule' ? current.number : null,
    )
    if (refused !== null) {
      setDraft(text)
      setRefusal(refusalLines[refused])
      return
    }
    if (current.kind === 'rule')
      send(current, { action: 'edit', number: current.number, text }, () => ({
        status: `Rule ${String(current.number)} saved.`,
        focus: { pencil: current.number },
      }))
    else
      send(current, { action: 'add', section: current.section, text }, (next) => {
        // The new rule is the last of its section in the answer.
        const added = next.profile?.rules.filter((item) => item.section === current.section).at(-1)
        return added === undefined
          ? { status: '', focus: { add: current.section } }
          : { status: `Rule ${String(added.number)} saved.`, focus: { pencil: added.number } }
      })
  }
  const remove = (current: Editing, number: number) => {
    const section = profile?.rules.find((rule) => rule.number === number)?.section
    send(current, { action: 'remove', number }, () => ({
      status: `Rule ${String(number)} removed.`,
      focus: section === undefined ? 'status' : { add: section },
    }))
  }
  const add = (section: Rule['section']) => {
    if (pending || profile === null) return
    if (profile.rules.length >= 40) {
      // Said beside the Add rule pressed, which keeps focus.
      setLimitAt(section)
      return
    }
    open({ kind: 'new', section, version: profile.version })
  }
  const editor = {
    editing,
    refusal,
    status,
    limitAt,
    pending,
    open,
    close,
    saveRule,
    remove,
    add,
  }
  return { container, editor }
}

type TasteRuleEditor = ReturnType<typeof useTasteRuleEditor>['editor']

function TasteSection({
  section,
  profile,
  editor,
}: {
  section: Rule['section']
  profile: NonNullable<TasteProfile['profile']>
  editor: TasteRuleEditor
}) {
  const { editing, pending } = editor
  const rules = profile.rules.filter((rule) => rule.section === section)
  const adding = editing?.kind === 'new' && editing.section === section ? editing : null
  const numbers = new Set(profile.rules.map((rule) => rule.number))
  // As the server numbers an added rule: above the highest, or the lowest free
  // number once that would pass 9,999.
  let nextNumber = Math.max(0, ...numbers) + 1
  if (nextNumber > 9_999) for (nextNumber = 1; numbers.has(nextNumber); nextNumber += 1);
  const editorFor = (number: number, initial: string, current: Editing) => (
    <RuleForm
      key={current.kind === 'rule' ? `rule-${String(number)}` : `new-${current.section}`}
      number={number}
      initial={initial}
      removable={current.kind === 'rule'}
      pending={pending}
      refusal={editor.refusal}
      onSave={(typed, setDraft) => {
        editor.saveRule(current, typed, setDraft)
      }}
      onCancel={() => {
        editor.close(current.kind === 'rule' ? { pencil: number } : { add: current.section })
      }}
      onRemove={() => {
        editor.remove(current, number)
      }}
    />
  )
  return (
    <section className="taste-section" aria-labelledby={`taste-${section}`}>
      <h2 id={`taste-${section}`}>{sectionHeadings[section]}</h2>
      {rules.length > 0 || adding !== null ? (
        <ol>
          {rules.map((rule) =>
            editing?.kind === 'rule' && editing.number === rule.number ? (
              <li key={rule.number} value={rule.number} className="taste-editing">
                {editorFor(rule.number, rule.text, editing)}
              </li>
            ) : (
              <li key={rule.number} value={rule.number}>
                <span>{rule.text}</span>
                <span className="taste-who">
                  {rule.provenance === 'owner' ? (
                    <StatusPill tone="accent">Yours</StatusPill>
                  ) : rule.provenance === 'edited' ? (
                    <StatusPill tone="accent">Edited</StatusPill>
                  ) : null}
                </span>
                <button
                  type="button"
                  className="taste-pencil"
                  data-taste-pencil={rule.number}
                  aria-label={`Edit rule ${String(rule.number)}`}
                  title="Edit"
                  aria-disabled={pending || undefined}
                  onClick={() => {
                    editor.open({ kind: 'rule', number: rule.number, version: profile.version })
                  }}
                >
                  <Pencil />
                </button>
              </li>
            ),
          )}
          {adding === null ? null : (
            <li value={nextNumber} className="taste-editing">
              {editorFor(nextNumber, '', adding)}
            </li>
          )}
        </ol>
      ) : null}
      <button
        type="button"
        className="taste-add"
        data-taste-add={section}
        aria-disabled={pending || undefined}
        onClick={() => {
          editor.add(section)
        }}
      >
        Add rule
      </button>
      <p className="taste-limit" role="status">
        {editor.limitAt === section ? limitCopy : ''}
      </p>
    </section>
  )
}

function Profile({ view }: { view: TasteProfile }) {
  const { container, editor } = useTasteRuleEditor(view)
  const switching = useTasteSwitching(view.automatic_available)
  const profile = view.profile
  if (profile === null) return null
  return (
    <div ref={container} className="taste-editor">
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
      <p className="taste-status" role="status" tabIndex={-1}>
        {editor.status}
      </p>
      {/* Choosing Automatic opens the preview here, above the rules (#1372 PR 5). */}
      <SwitchingPreview switching={switching} view={view} />
      {/* Wide screens put Switching and Versions beside the rules, as the
          prototype does; phones put Switching above the rules. "Confirmed"
          sits inside Switching. */}
      <div className="taste-body">
        <SwitchingControl switching={switching} view={view} />
        <div className="taste-rules">
          {(Object.keys(sectionHeadings) as Rule['section'][]).map((section) => (
            <TasteSection key={section} section={section} profile={profile} editor={editor} />
          ))}
        </div>
        <Versions view={view} />
      </div>
    </div>
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
            <span>
              {version.kind === 'built'
                ? `Built ${formatDay(version.created_at)} from ${String(version.decisions)} decisions`
                : `Edited ${formatDay(version.created_at)}`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function TasteProfileView({
  onStart,
  focusBuild = false,
}: {
  /** Start 25 picks: Covers opens its picks. */
  onStart: () => void
  focusBuild?: boolean
}) {
  const { http } = useReviewContext()
  const read = useQuery(tasteQuery(http))
  if (read.isPending) return <p role="status">Loading taste profile…</p>
  const failure = read.isError ? (
    <div className="covers-failure" role="alert">
      <p>{routeFailureCopy(read.error).message}</p>
      <button type="button" onClick={() => void read.refetch()}>
        Reload
      </button>
    </div>
  ) : null
  // A failed re-read keeps the profile it already shows, so an open editor
  // and its draft survive a re-read that fails after an uncertain save.
  if (read.data === undefined) return failure
  const view = read.data
  return (
    <div className="taste-profile">
      {failure}
      <BuildState view={view} onStart={onStart} focusBuild={focusBuild} />
      <JudgeState view={view} />
      <Profile view={view} />
    </div>
  )
}
