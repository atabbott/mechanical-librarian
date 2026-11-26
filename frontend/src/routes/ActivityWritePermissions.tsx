// Activity's write-permission review: the check, each configured library and
// its fields, and Cancel and Confirm. Moved out of the Activity route under the
// route-size rule (#2039); the page keeps the check's query, the draft, the
// ticked libraries and the consent command.
import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode, RefObject } from 'react'

import type { ActivityLibraryView, CapabilityState } from '../api/http'
import { Button } from '../components/Button'
import { WritePlanField } from '../components/WritePlanGroups'
import { destinationLabel } from '../labels'
import { destinationReason, libraryWritesHeld, writesEnabled } from '../writeConsent'

import { ActivityPermissionCheck } from './ActivityPermissionCheck'
import type { PermissionCheck } from './ActivityPermissionCheck'

// A library whose every destination already holds permission has writes
// enabled; it is not still waiting for consent (#1286).
const capabilityLabel = (state: CapabilityState, consented: boolean) =>
  consented
    ? 'Writes enabled'
    : {
        allowed: 'Ready',
        partially_allowed: 'Some fields are ready',
        connection_unavailable: 'Connection needs checking',
        not_ready: 'Not ready yet',
      }[state]

export const ActivityWritePermissions = ({
  discarded,
  requested,
  permissions,
  permission,
  checking,
  basisStale,
  checkButton,
  check,
  configured,
  selectable,
  selected,
  toggle,
  libraryName,
  lapsed,
  status,
  pending,
  confirmBusy,
  hasGrants,
  cancel,
  confirm,
}: {
  /** Whether the last draft was discarded because its consent basis changed. */
  discarded: boolean
  /** Whether a check has been requested since the review opened or the library changed. */
  requested: boolean
  permissions: UseQueryResult<PermissionCheck>
  /** The current request's check, while it holds. */
  permission: PermissionCheck | undefined
  checking: boolean
  /** Confirm's re-read of the consent basis met a newer build. */
  basisStale: boolean
  /** The check button, or Reload once the tab is on an earlier build. */
  checkButton: RefObject<HTMLButtonElement | null>
  check: () => void
  configured: readonly ActivityLibraryView[]
  /** The libraries that can be ticked now. */
  selectable: ReadonlySet<string>
  selected: ReadonlySet<string>
  toggle: (library: string) => void
  libraryName: (name: string) => string
  /** Whether an earlier permission lapsed. */
  lapsed: boolean
  /** The consent command's outcome, when this review owns it. */
  status: ReactNode
  pending: boolean
  confirmBusy: boolean
  /** Whether any ticked library has a field to grant. */
  hasGrants: boolean
  cancel: () => void
  confirm: () => void
}) => {
  const capability = (library: string) =>
    permission?.capabilities.find((item) => item.library === library)
  return (
    <section
      className="activity-consent-ledger"
      id="activity-consent"
      tabIndex={-1}
      aria-labelledby="activity-consent-title"
    >
      <div>
        <h2 id="activity-consent-title">Write permissions</h2>
      </div>
      <ActivityPermissionCheck
        discarded={discarded}
        requested={requested}
        permissions={permissions}
        permission={permission}
        checking={checking}
        basisStale={basisStale}
        checkButton={checkButton}
        check={check}
      />
      {configured.map((library) => {
        const name = library.library
        const state = capability(name)?.state ?? 'not_ready'
        const destinations = capability(name)?.destinations ?? []
        // Consented once nothing is left to grant: every destination that
        // can be given permission holds it. One that can never be written
        // here (a field the service lacks, a hold) does not keep the
        // library reading as partly ready. Consent outlives a later
        // suspension, so only a consented destination that is still
        // allowed has writes enabled (#1286).
        const consented =
          destinations.some(writesEnabled) &&
          !destinations.some(
            (destination) => destination.state === 'allowed' && !destination.consented,
          )
        const displayName = libraryName(name)
        const disabled = !selectable.has(name)
        return (
          <div className="activity-consent-library" key={name}>
            <label>
              <input
                type="checkbox"
                disabled={disabled}
                checked={selected.has(name)}
                onChange={() => toggle(name)}
              />
              <strong>{displayName}</strong>
              <small>
                {(library.prepared?.total ?? 0).toLocaleString('en-US')} prepared ·{' '}
                {permission?.backup_ready !== true
                  ? 'Backup not confirmed'
                  : libraryWritesHeld(destinations)
                    ? 'Writes held'
                    : capabilityLabel(state, consented)}
              </small>
            </label>
            <ul aria-label={`${displayName} fields`}>
              {capability(name)?.destinations.map((entry) => (
                <li key={entry.destination}>
                  <span>{destinationLabel(entry.destination)}</span>
                  <small>
                    {writesEnabled(entry)
                      ? `Writes enabled · ${entry.prepared.toLocaleString('en-US')} prepared`
                      : entry.state === 'allowed'
                        ? `Included · ${entry.prepared.toLocaleString('en-US')} prepared`
                        : `${entry.consented ? 'Writes held' : 'Excluded'}: ${destinationReason(entry.state, lapsed)}`}
                  </small>
                  <WritePlanField
                    plan={permission?.plan}
                    library={name}
                    destination={entry.destination}
                  />
                </li>
              ))}
            </ul>
          </div>
        )
      })}
      {configured.some((library) =>
        capability(library.library)?.destinations.some((entry) => entry.state === 'allowed'),
      ) ? (
        <p>
          Only included fields are written. The backup and each field are checked again when you
          confirm.
        </p>
      ) : null}
      {status}
      <div className="activity-consent-actions">
        <button type="button" onClick={cancel} disabled={pending}>
          Cancel
        </button>
        <p id="activity-consent-requirement">Choose the libraries to include, then confirm.</p>
        <Button
          variant="primary"
          aria-describedby="activity-consent-requirement"
          // Busy, not disabled, so focus stays here; the ref refuses a
          // second press that lands before the pending state renders.
          aria-disabled={confirmBusy || basisStale || undefined}
          disabled={
            !confirmBusy && !basisStale && (permission?.backup_ready !== true || !hasGrants)
          }
          onClick={confirm}
        >
          Confirm write permissions
        </Button>
      </div>
    </section>
  )
}
