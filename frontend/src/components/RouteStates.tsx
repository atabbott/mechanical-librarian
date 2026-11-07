// The states a route shows in place of its page: loading, a failure (a newer
// build, application assets that could not be loaded, or a read that failed)
// and a route that does not exist. Moved out of the router under the
// route-size rule (#1742).
import { Link, useRouter } from '@tanstack/react-router'

import { BuildMismatchError, signInLocation } from '../api/http'
import { ChunkRecoveryError, reloadCurrentApplication } from '../chunkRecovery'
import { routeFailureCopy } from '../routeFailure'

export const RoutePending = ({ title = 'workspace' }: { title?: string }) => (
  <section aria-busy="true" className="route-state">
    <p className="eyebrow">Private workspace</p>
    <h1>Loading {title.toLowerCase()}…</h1>
  </section>
)
export const RouteFailure = ({
  error,
  reset,
  title = 'This workspace',
}: {
  error: unknown
  reset: () => void
  title?: string
}) => {
  const router = useRouter()
  const copy = routeFailureCopy(error)
  // A different build cannot be fixed by loading the route again: only a
  // reload brings this tab onto the build the server is running.
  return error instanceof BuildMismatchError ? (
    <section className="route-state">
      <p className="eyebrow">Application updated</p>
      <h1>{title} was not loaded.</h1>
      {/* The shell's build notice is the alert; this only names the route. */}
      <p>This tab is running an earlier build. Reload to open it in the current application.</p>
      <button onClick={() => reloadCurrentApplication()}>Reload the current application</button>
    </section>
  ) : error instanceof ChunkRecoveryError && error.exhausted ? (
    <section className="route-state">
      <p className="eyebrow">Application update unavailable</p>
      <h1>The current workspace assets could not be loaded.</h1>
      <p role="alert">{error.message}</p>
      <button onClick={() => reloadCurrentApplication()}>Reload the current application</button>
    </section>
  ) : (
    <section className="route-state">
      <h1>{title} couldn't be loaded.</h1>
      {/* Plain words only; the status and code stay under Technical detail (#1756). */}
      <p role="alert">{copy.message}</p>
      {copy.detail ? (
        <details>
          <summary>Technical detail</summary>
          <code>{copy.detail}</code>
        </details>
      ) : null}
      {copy.action === 'sign-in' ? (
        <a href={signInLocation()}>Sign in again</a>
      ) : copy.action === 'retry' ? (
        <button onClick={() => void router.invalidate().then(reset)}>Try again</button>
      ) : null}
    </section>
  )
}
export const DirectEntryFallback = () => (
  <section className="route-state">
    <p className="eyebrow">Page unavailable</p>
    <h1>This review route does not exist.</h1>
    <Link to="/library" search={{ lens: 'all' }}>
      Open review library
    </Link>
  </section>
)
