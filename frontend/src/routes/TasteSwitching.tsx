import { Link, useLocation } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'

import type { TasteProfileView as TasteProfile } from '../api/http'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { retryInPlace } from '../focusAfterRetry'
import { tasteSwitchingId as switchingId } from '../tasteSections'
import type { TasteSwitching } from '../tasteSwitching'
import { TastePreview } from './TastePreview'

// Taste profile's Switching control and its preview (#1372 PR 5); the state
// is useTasteSwitching's.

const lost = 'No answer came back. Check whether Switching was saved before trying again.'
const loadFailed = 'Cover settings could not be loaded.'
const count = (value: number) => value.toLocaleString('en-GB')

/**
 * Only covers with a fault and what changing it costs. While the preview is
 * open it sits in the preview, above the switches it decides and Turn on.
 */
function ScopeCheck({ switching, view }: { switching: TasteSwitching; view: TasteProfile }) {
  const { previewing } = switching
  const checked = previewing
    ? switching.protect
    : switching.shown?.current_protection === 'protected'
  return (
    <div className="taste-mode-extra">
      <label className="taste-check">
        <input
          type="checkbox"
          checked={checked}
          disabled={switching.settings.data?.authority === 'file'}
          aria-describedby="taste-switching-again"
          onChange={(event) => switching.tick(event.target.checked)}
        />
        <span>Only covers with a fault</span>
      </label>
      <p id="taste-switching-again" className="taste-switching-note">
        {`Changing this has the suggestion model check every cover again, up to ${count(view.judge.daily_limit)} a day.`}
      </p>
    </div>
  )
}

/** The preview, full width under the meta, focused when it opens. */
export function SwitchingPreview({
  switching,
  view,
}: {
  switching: TasteSwitching
  view: TasteProfile
}) {
  const heading = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (!switching.previewing) return
    heading.current?.scrollIntoView({ block: 'start' })
    heading.current?.focus({ preventScroll: true })
  }, [switching.previewing])
  return switching.previewing ? (
    <TastePreview
      protection={switching.protect ? 'protected' : 'unprotected'}
      saving={switching.saving}
      headingRef={heading}
      scope={<ScopeCheck switching={switching} view={view} />}
      onTurnOn={switching.turnOn}
      onNotNow={switching.notNow}
    />
  ) : null
}

export function SwitchingControl({
  switching,
  view,
}: {
  switching: TasteSwitching
  view: TasteProfile
}) {
  const { settings, preferences, previewing, never } = switching
  const fieldset = useRef<HTMLFieldSetElement>(null)
  const loadRetry = useRef<HTMLButtonElement>(null)
  const refusedReload = useRef<HTMLButtonElement>(null)
  // Policy › Current covers links here: focus lands on Switching once.
  const arrived = useRef(false)
  const hash = useLocation({ select: (location) => location.hash })
  useEffect(() => {
    if (arrived.current || !preferences || hash !== switchingId) return
    arrived.current = true
    requestAnimationFrame(() => {
      fieldset.current?.scrollIntoView({ block: 'start' })
      fieldset.current?.focus({ preventScroll: true })
    })
  }, [preferences, hash])
  const locked = settings.data?.authority === 'file'
  // Under Never, Suggest only is in force whatever is stored (Q10).
  const automaticChecked =
    !never && (switching.shown?.taste_switching === 'automatic' || previewing)
  const { confirmed, suggested } = view.confirmed
  return (
    <fieldset
      id={switchingId}
      ref={fieldset}
      tabIndex={-1}
      className="taste-switching"
      aria-describedby={never ? 'taste-switching-never' : undefined}
    >
      <legend>Switching</legend>
      {settings.isError && !preferences ? (
        <div className="covers-failure" role="alert">
          <p>{loadFailed}</p>
          <button
            ref={loadRetry}
            type="button"
            onClick={() =>
              retryInPlace(
                () => fieldset.current,
                () => settings.refetch(),
                (result) =>
                  result.isSuccess
                    ? fieldset.current?.querySelector<HTMLElement>('input:checked')
                    : loadRetry.current,
              )
            }
          >
            Reload
          </button>
        </div>
      ) : null}
      {locked ? <p>These settings are managed by deployment configuration.</p> : null}
      <label className="taste-mode">
        <input
          type="radio"
          name="taste-switching"
          value="suggest_only"
          checked={preferences ? !automaticChecked : false}
          disabled={!preferences || locked}
          onChange={() => switching.choose('suggest_only')}
        />
        <span>Suggest only</span>
      </label>
      <label className="taste-mode">
        <input
          type="radio"
          name="taste-switching"
          value="automatic"
          checked={preferences ? automaticChecked : false}
          disabled={!preferences || locked || never}
          onChange={() => switching.choose('automatic')}
        />
        <span>Automatic</span>
      </label>
      {automaticChecked && !previewing ? <ScopeCheck switching={switching} view={view} /> : null}
      {never ? (
        <p id="taste-switching-never" className="taste-switching-note">
          Automatic is off while{' '}
          <Link to="/policy" search={{ section: 'curation' }} hash="current-covers">
            Current covers
          </Link>{' '}
          is set to Never.
        </p>
      ) : view.automatic_available ? null : (
        <p className="taste-switching-note">
          Until your check passes, Automatic only suggests switches.
        </p>
      )}
      {suggested === 0 ? null : (
        <p className="taste-confirmed">
          {`Confirmed ${count(confirmed)} of ${count(suggested)} ${suggested === 1 ? 'suggestion' : 'suggestions'}`}
        </p>
      )}
      <p className="taste-switching-status" role="status">
        {switching.status}
      </p>
      {switching.failure === 'build' ? (
        <StaleBuildRead lead="Switching was not saved." />
      ) : switching.failure ? (
        <div className="covers-failure" role="alert">
          <p>{switching.failure === 'lost' ? lost : switching.refusal}</p>
          {switching.failure === 'refused' ? (
            <button
              ref={refusedReload}
              type="button"
              onClick={() => switching.reload(() => refusedReload.current)}
            >
              Reload
            </button>
          ) : null}
        </div>
      ) : null}
    </fieldset>
  )
}
