import { useEffect, useState } from 'react'

import type { LiveStreamState } from '../liveUpdates'
import { StatusPill } from './StatusPill'

/**
 * The one visible consequence of a stream that is down: the surfaces go back to
 * polling and say so, rather than looking live while showing stale counts.
 *
 * `connecting` is deliberately silent. Announcing a state that resolves in a
 * few hundred milliseconds on every page load would make the calm case noisy,
 * which is the opposite of what an owner needs from a status line.
 */
export const LiveStreamNotice = ({ state }: { state: LiveStreamState }) => {
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    if (state !== 'degraded') return
    const handle = globalThis.setTimeout(() => setSettled(true), 1_000)
    // Cleared on the way out of the degraded state, so a stream that drops
    // again is quiet for another second rather than reappearing instantly.
    return () => {
      globalThis.clearTimeout(handle)
      setSettled(false)
    }
  }, [state])
  if (state !== 'degraded' || !settled) return null
  return (
    <StatusPill tone="notice" className="live-state" role="status">
      <i aria-hidden="true" />
      Reconnecting · checking for updates
    </StatusPill>
  )
}
