import { Link, useLocation } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'

import type { TasteProfileView as TasteProfile } from '../api/http'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { tasteSwitchingId as switchingId } from '../tasteSections'
import type { TasteSwitching } from '../tasteSwitching'
import { TastePreview } from './TastePreview'

// Taste profile's Switching control and its preview (#1372 PR 5); the state
// is useTasteSwitching's.

const lost = 'No answer came back. Check whether Switching was saved before trying again.'
const loadFailed = 'Cover settings could not be loaded.'

/** The preview, full width under the meta, focused when it opens. */
export function SwitchingPreview({ switching }: { switching: TasteSwitching }) {
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
  const { settings, preferences, previewing } = switching
  const fieldset = useRef<HTMLFieldSetElement>(null)
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
  const never = preferences?.current_protection === 'proposals_only'
  const automaticChecked = switching.automatic || previewing
  const protectChecked = previewing
    ? switching.protect
    : preferences?.current_protection === 'protected'
  const confirmed = view.confirmed
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
          <button type="button" onClick={() => void settings.refetch()}>
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
      {automaticChecked && !never ? (
        <div className="taste-mode-extra">
          <label className="taste-check">
            <input
              type="checkbox"
              checked={protectChecked}
              disabled={locked}
              aria-describedby="taste-switching-again"
              onChange={(event) => switching.tick(event.target.checked)}
            />
            <span>Only covers with a fault</span>
          </label>
          <p id="taste-switching-again" className="taste-switching-note">
            {`Changing this checks every cover again, up to ${view.judge.daily_limit.toLocaleString('en-GB')} a day.`}
          </p>
        </div>
      ) : null}
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
      {confirmed.suggested === 0 ? null : (
        <p className="taste-confirmed">
          {`Confirmed ${confirmed.confirmed.toLocaleString('en-GB')} of ${confirmed.suggested.toLocaleString('en-GB')} suggestions`}
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
            <button type="button" onClick={switching.reload}>
              Reload
            </button>
          ) : null}
        </div>
      ) : null}
    </fieldset>
  )
}
