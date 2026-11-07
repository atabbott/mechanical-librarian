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
    <h1>Loading {title.toLowerCase()}…</h1>
  </section>
)
export const RouteFailure = ({
  error,
  reset,
  title = 'This page',
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
      {/* The shell's build notice is the alert and says why; this only names the route. */}
      <h1>{title} was not loaded</h1>
      <button onClick={() => reloadCurrentApplication()}>Reload</button>
    </section>
  ) : error instanceof ChunkRecoveryError && error.exhausted ? (
    <section className="route-state">
      <h1>The new version could not be loaded</h1>
      <p role="alert">{error.message}</p>
      <button onClick={() => reloadCurrentApplication()}>Reload</button>
    </section>
  ) : (
    <section className="route-state">
      <h1>{title} couldn't be loaded</h1>
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
    <h1>This page does not exist</h1>
    <Link to="/library" search={{ lens: 'all' }}>
      Open Library
    </Link>
  </section>
)
