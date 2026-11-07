// The write-permission check in Activity's consent review: what the latest
// check found, the control that starts the next one, and an example of what a
// write would change. Moved out of the Activity route under the route-size
// rule (#1742); the page keeps the check's query, the draft and the consent
// command.
import type { UseQueryResult } from '@tanstack/react-query'
import type { RefObject } from 'react'

import { BuildMismatchError, displayValue } from '../api/http'
import type { ActivityPermissionReadinessView } from '../api/http'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { destinationLabel } from '../labels'

// A permission draft is the readiness check and the ticked libraries, kept
// against the consent basis it was checked on (#1279).
export interface PermissionCheck extends ActivityPermissionReadinessView {
  readonly policyId: string
  readonly basis: string
}
export const basisChangedCopy = 'Your library settings changed. Review the permissions again.'
export const readinessUnavailableCopy =
  'Permission readiness is unavailable. No permission was granted. Try again.'

export const ActivityPermissionCheck = ({
  discarded,
  requested,
  permissions,
  permission,
  checking,
  basisStale,
  checkButton,
  check,
  libraryName,
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
  libraryName: (library: string) => string
}) => {
  // Another check cannot succeed on an earlier build: only a reload can.
  const staleLead = basisStale
    ? 'Permission readiness was not loaded, so no permission was granted.'
    : permissions.error instanceof BuildMismatchError
      ? 'Permission readiness was not loaded.'
      : null
  return (
    <>
      <p role="status">
        {staleLead !== null
          ? ''
          : discarded
            ? basisChangedCopy
            : !requested
              ? 'Permission readiness has not been checked, or the library changed. Check again before selecting destinations.'
              : permissions.isFetching
                ? 'Checking backup readiness and write permissions…'
                : permissions.isError
                  ? readinessUnavailableCopy
                  : permission?.backup_ready === null
                    ? 'Readiness could not be confirmed for the current library state. Check again before enabling writes.'
                    : permission?.backup_ready === false
                      ? 'Backup readiness could not be confirmed. Writes cannot be enabled.'
                      : 'Backup readiness checked. Review the included and excluded destinations.'}
      </p>
      {staleLead !== null ? (
        <StaleBuildRead lead={staleLead} ref={checkButton} />
      ) : (
        // Busy, not disabled: a disabled button would drop a keyboard owner's focus to <body>.
        <button
          ref={checkButton}
          type="button"
          onClick={checking ? undefined : check}
          aria-disabled={checking || undefined}
        >
          {!requested
            ? 'Check write permissions'
            : permissions.isError || permission?.backup_ready === null
              ? 'Retry permission check'
              : 'Check again'}
        </button>
      )}
      {permission?.example ? (
        <p>
          Example: {destinationLabel(permission.example.destination)} for{' '}
          {libraryName(permission.example.library)} — {displayValue(permission.example.before)} →{' '}
          {displayValue(permission.example.after)}
        </p>
      ) : null}
    </>
  )
}
