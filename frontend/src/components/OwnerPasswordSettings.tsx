import { useEffect, useId, useRef } from 'react'
import type { Ref, SubmitEvent } from 'react'

import settingsVocabulary from '../../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'
import {
  ApiError,
  BuildMismatchError,
  SessionExpiredError,
  UncertainMutationError,
  refusalCopy,
  uncertainWriteOutcome,
} from '../api/http'
import type { ReviewHttp } from '../api/http'
import { closedOwnerPasswordForm as closed, useOwnerPasswordForm } from '../ownerPasswordForm'
import type {
  OwnerPasswordField as Field,
  OwnerPasswordFieldErrors as FieldErrors,
  OwnerPasswordForm,
  OwnerPasswordFormState as FormState,
  OwnerPasswordProblem as Problem,
} from '../ownerPasswordForm'
import { descriptionId } from '../fieldControls'
import { FieldRow } from './FieldRow'
import { StaleBuildRead } from './StaleBuildRead'

const { minimum_characters: minimum, maximum_bytes: maximumBytes } =
  settingsVocabulary.owner_password
const notChanged = 'The password was not changed. Try again.'

// The server's rule, counted the same way: code points for the minimum and
// UTF-8 bytes for the maximum (as setup does).
const tooShort = (value: string) => Array.from(value).length < minimum
const tooLong = (value: string) => new TextEncoder().encode(value).length > maximumBytes

// What the browser can refuse before sending. A short new password marks the
// hint and adds no line, and its confirmation is not compared.
const check = ({
  current,
  next,
  confirmation,
}: OwnerPasswordForm['values']): [FieldErrors, boolean] => {
  const errors: FieldErrors = {}
  const short = next !== '' && tooShort(next)
  if (current === '') errors.current = 'Enter your current password.'
  if (next === '') errors.next = 'Enter a new password.'
  else if (tooLong(next)) errors.next = 'That password is too long. Use a shorter one.'
  else if (!short && confirmation !== next)
    errors.confirmation = 'The two passwords do not match. Retype the confirmation.'
  return [errors, short]
}

// A refusal the server gave, as the line under the form. `invalid_password`
// falls to the last line: the checks above already passed the same rule, so
// the browser cannot say which part the server refused.
const refusalLine = (error: unknown): Problem => ({
  kind: 'line',
  text:
    error instanceof ApiError && error.code === 'throttled'
      ? 'Too many password attempts from this device. Wait five minutes, then try again.'
      : refusalCopy(error, notChanged),
})

const PasswordField = ({
  id,
  label,
  autoComplete,
  value,
  reveal,
  disabled,
  error,
  hint,
  refusals,
  onEdit,
}: {
  id: string
  label: string
  autoComplete: string
  value: string
  reveal: boolean
  disabled: boolean
  error: string | undefined
  /** The rule under New password, and whether it is the refusal just now. */
  hint?: { refused: boolean }
  refusals: number
  onEdit: (value: string) => void
}) => (
  <div className="settings-password-field">
    <label htmlFor={id}>{label}</label>
    <input
      id={id}
      type={reveal ? 'text' : 'password'}
      autoComplete={autoComplete}
      autoCapitalize="none"
      spellCheck={false}
      value={value}
      disabled={disabled}
      aria-invalid={error !== undefined || hint?.refused === true ? true : undefined}
      aria-describedby={
        [...(hint ? [`${id}-hint`] : []), ...(error ? [`${id}-error`] : [])].join(' ') || undefined
      }
      onChange={(event) => onEdit(event.currentTarget.value)}
    />
    {hint ? (
      // Remounted when refused, so the alert announces the hint as the problem.
      <small
        key={hint.refused ? `refused-${String(refusals)}` : 'hint'}
        id={`${id}-hint`}
        className={hint.refused ? 'is-refused' : undefined}
        role={hint.refused ? 'alert' : undefined}
      >
        {`At least ${String(minimum)} characters.`}
      </small>
    ) : null}
    {error ? (
      // Remounted the same way: focus may already be in the field, where a
      // new description alone is not read out.
      <p key={String(refusals)} className="settings-field-error" id={`${id}-error`} role="alert">
        {error}
      </p>
    ) : null}
  </div>
)

// The refusal that is about no one field, under the form.
const ProblemLine = ({ problem, reload }: { problem: Problem; reload: Ref<HTMLButtonElement> }) =>
  problem?.kind === 'line' ? (
    <p className="settings-field-error" role="alert">
      {problem.text}
    </p>
  ) : problem?.kind === 'stale' ? (
    <StaleBuildRead lead="The password was not changed." ref={reload} />
  ) : problem?.kind === 'unchecked' ? (
    // No build notice announces this one, so the line is its own alert.
    <div role="alert">
      <StaleBuildRead
        lead="No answer came back, and the page could not check whether the password changed."
        ref={reload}
      />
    </div>
  ) : null

/**
 * Settings' Owner password row in store mode. It changes the password at once,
 * outside Save settings, and keeps this browser signed in on a new session.
 * Its own file because it has its own form state and request, and its
 * contract is tested directly.
 */
export const OwnerPasswordSettings = ({ http, form }: { http: ReviewHttp; form?: FormState }) => {
  const own = useOwnerPasswordForm()
  const [state, setState] = form ?? own
  const { open, values, reveal, errors, refused, refusals, problem, pending, changed } = state
  const update = (patch: Partial<OwnerPasswordForm>) =>
    setState((current) => ({ ...current, ...patch }))
  const id = useId()
  const ids: Record<Field, string> = {
    current: `${id}-current`,
    next: `${id}-next`,
    confirmation: `${id}-confirmation`,
  }
  // One request at a time, whatever the render in hand says: a second submit
  // in the same tick would be refused as a lost session after the first won.
  const sending = useRef(false)
  // Where focus goes once the next render is on the page.
  const focusNext = useRef<string | undefined>(undefined)
  const closedButton = useRef<HTMLButtonElement>(null)
  const submitButton = useRef<HTMLButtonElement>(null)
  const status = useRef<HTMLParagraphElement>(null)
  const reload = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const target = focusNext.current
    if (target === undefined) return
    focusNext.current = undefined
    const element =
      target === 'closed'
        ? closedButton.current
        : target === 'submit'
          ? submitButton.current
          : target === 'status'
            ? status.current
            : target === 'reload'
              ? reload.current
              : document.getElementById(target)
    element?.focus()
  })

  const edit = (field: Field) => (value: string) =>
    setState((current) => ({
      ...current,
      values: { ...current.values, [field]: value },
      errors: { ...current.errors, [field]: undefined },
      refused: field === 'next' ? false : current.refused,
    }))
  const refuse = (fieldErrors: FieldErrors, short = false) => {
    setState((current) => ({
      ...current,
      pending: false,
      errors: fieldErrors,
      refused: short,
      refusals: current.refusals + 1,
      problem: undefined,
    }))
    const first = (['current', 'next', 'confirmation'] as const).find(
      (field) => fieldErrors[field] !== undefined || (field === 'next' && short),
    )
    focusNext.current = first === undefined ? 'submit' : ids[first]
  }
  // Focus goes to Change password beside the line, or to Reload.
  const fail = (next: Problem) => {
    update({ pending: false, errors: {}, refused: false, problem: next })
    focusNext.current = next?.kind === 'line' ? 'submit' : 'reload'
  }

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault()
    // The page's state survives a remount, when this row's ref starts over.
    if (sending.current || pending) return
    const [fieldErrors, short] = check(values)
    if (short || Object.keys(fieldErrors).length > 0) {
      refuse(fieldErrors, short)
      return
    }
    sending.current = true
    update({ pending: true, problem: undefined })
    try {
      await http.changePassword({
        current_password: values.current,
        new_password: values.next,
        confirmation: values.confirmation,
      })
    } catch (error) {
      await showRefusal(error)
      update({ pending: false })
      return
    } finally {
      sending.current = false
    }
    setState({ ...closed, refusals, changed: true })
    focusNext.current = 'status'
  }
  // A refusal keeps what was typed. An outcome the browser cannot know is
  // read again once, never sent again: a 401 on that read goes to sign-in.
  const showRefusal = async (error: unknown) => {
    if (error instanceof SessionExpiredError) return
    if (error instanceof BuildMismatchError) fail({ kind: 'stale' })
    else if (uncertainWriteOutcome(error)) {
      try {
        await http.getSettings()
      } catch (reread) {
        if (!(reread instanceof SessionExpiredError)) fail({ kind: 'unchecked' })
        return
      }
      // An answer that arrived but could not be read may have carried the new
      // session, and then this read succeeded on it and proves nothing. With
      // no answer, or a failure, the browser still holds the session it sent,
      // which a change would have ended.
      if (error instanceof UncertainMutationError) fail({ kind: 'unchecked' })
      else
        fail({
          kind: 'line',
          text:
            error instanceof ApiError && error.status >= 500
              ? notChanged
              : 'No answer came back, and the password was not changed. Try again.',
        })
    } else if (error instanceof ApiError && error.code === 'current_password_invalid')
      refuse({ current: 'That password was not accepted. Check it and try again.' })
    else fail(refusalLine(error))
  }

  const field = (name: Field, label: string, autoComplete: string) => (
    <PasswordField
      id={ids[name]}
      label={label}
      autoComplete={autoComplete}
      value={values[name]}
      reveal={reveal}
      disabled={pending}
      error={errors[name]}
      {...(name === 'next' ? { hint: { refused } } : {})}
      refusals={refusals}
      onEdit={edit(name)}
    />
  )
  if (!open)
    return (
      <FieldRow
        fieldId="owner-password"
        group
        title="Owner password"
        description="Changing it signs out every other browser."
      >
        <div className="settings-password">
          {changed ? (
            <p className="settings-password-status" role="status" tabIndex={-1} ref={status}>
              Password changed. Every other browser was signed out.
            </p>
          ) : null}
          <div className="settings-password-actions">
            <button
              type="button"
              ref={closedButton}
              aria-describedby={descriptionId('owner-password')}
              onClick={() => {
                update({ changed: false, open: true })
                focusNext.current = ids.current
              }}
            >
              Change password
            </button>
          </div>
        </div>
      </FieldRow>
    )
  return (
    <FieldRow
      fieldId="owner-password"
      group
      title="Owner password"
      description="Changing it signs out every other browser."
    >
      <form className="settings-password" noValidate onSubmit={(event) => void submit(event)}>
        {field('current', 'Current password', 'current-password')}
        {field('next', 'New password', 'new-password')}
        {field('confirmation', 'Confirm new password', 'new-password')}
        <button
          type="button"
          className="settings-password-reveal"
          aria-pressed={reveal}
          aria-controls={`${ids.current} ${ids.next} ${ids.confirmation}`}
          disabled={pending}
          onClick={() => update({ reveal: !reveal })}
        >
          {reveal ? 'Hide passwords' : 'Show passwords'}
        </button>
        <ProblemLine problem={problem} reload={reload} />
        <div className="settings-password-actions">
          {/* aria-disabled, not disabled, keeps focus on the button while it changes the
              password; submit refuses a second one while one is sending. */}
          <button
            type="submit"
            className="button-primary"
            aria-disabled={pending}
            ref={submitButton}
          >
            {pending ? 'Changing…' : 'Change password'}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              setState({ ...closed, refusals })
              focusNext.current = 'closed'
            }}
          >
            Cancel
          </button>
        </div>
      </form>
    </FieldRow>
  )
}
