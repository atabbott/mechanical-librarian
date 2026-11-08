import type { Ref } from 'react'

import { reloadCurrentApplication } from '../chunkRecovery'

/**
 * The one control a tab left on an earlier build offers: only a reload brings
 * it onto the build the server is running. The shell's build notice is the
 * alert and says why, so each site names only what was not loaded and puts
 * this beside it (#1742).
 */
export const ReloadApplication = ({ ref }: { ref?: Ref<HTMLButtonElement> | undefined }) => (
  <button ref={ref} type="button" onClick={() => reloadCurrentApplication()}>
    Reload
  </button>
)

/**
 * A read refused because the server runs a newer build cannot succeed on
 * another try. This names what was not loaded and offers the reload without
 * announcing again. `ref` reaches the reload button, for a site that moves
 * focus to its recovery control after a failed retry.
 */
export const StaleBuildRead = ({ lead, ref }: { lead: string; ref?: Ref<HTMLButtonElement> }) => (
  <p className="stale-build-read">
    {/* The lead's end padding clears Reload's focus ring from its full stop when
        they share a line, and goes with the full stop when Reload wraps. */}
    <span className="stale-build-read-lead">{lead}</span> <ReloadApplication ref={ref} />
  </p>
)
