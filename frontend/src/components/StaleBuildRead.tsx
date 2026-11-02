import { reloadCurrentApplication } from '../chunkRecovery'

/**
 * A read refused because the server runs a newer build cannot succeed on
 * another try: only a reload brings the tab onto that build. The shell's build
 * notice is the alert, so this names what was not loaded and offers the reload
 * without announcing again.
 */
export const StaleBuildRead = ({ lead }: { lead: string }) => (
  <p className="stale-build-read">
    {lead} This tab is running an earlier build. Reload to open it in the current application.{' '}
    <button type="button" onClick={() => reloadCurrentApplication()}>
      Reload the current application
    </button>
  </p>
)
