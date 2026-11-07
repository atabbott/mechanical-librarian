// The panel that offers to resume automatic handling for one held field, and
// its result and failed-checks lines. Moved out of the Library route, with its
// state in automaticHandling.ts, under the route-size rule (AGENTS.md: split a
// frontend route file over about 1,500 lines when a unit already changes it),
// as #1755 grew this unit.
import { sessionRefreshedCopy } from '../api/http'
import type { useAutomaticHandling } from '../automaticHandling'
import type { ReviewField } from '../fieldDecision'

export const AutomaticHandlingPanel = ({
  handling,
  selected,
}: {
  handling: ReturnType<typeof useAutomaticHandling>
  selected: ReviewField
}) => {
  const {
    automaticCompletedForSelected,
    automaticPanelVisible,
    automaticReload,
    automaticReturnFailed,
    automaticShown,
    automaticTrigger,
    closeAutomaticPanel,
    keepManualHandling,
    returnAutomatic,
    selectedAutomaticKey,
    setAutomaticPanel,
  } = handling
  return (
    <section
      className="automatic-handling-panel"
      aria-label="Automatic handling"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !returnAutomatic.isPending) {
          event.preventDefault()
          closeAutomaticPanel()
        }
      }}
    >
      {automaticPanelVisible ? (
        <>
          <h4>Automatic handling is paused for this field</h4>
          <p>
            Your value stays until fresh checks finish. Then Automatic can change this field again.
          </p>
          {automaticShown === 'success' ? (
            <p className="automatic-handling-result is-success" role="status">
              Fresh checks requested.
            </p>
          ) : automaticShown === 'error' ? (
            <p className="automatic-handling-result is-error" role="alert">
              Fresh checks could not be requested. Choose Resume after fresh checks to try again.
            </p>
          ) : automaticShown === 'uncertain' ? (
            // A warning, not an error: requesting again does no harm (#1755).
            <p className="automatic-handling-result is-uncertain" role="status">
              {automaticReload === 'not-requested'
                ? "No answer came back. This work doesn't show fresh checks as requested. Request them again."
                : 'No answer came back. Reloading this work to check…'}
            </p>
          ) : automaticShown === 'refreshed' ? (
            <p className="automatic-handling-result is-refreshed" role="alert">
              {sessionRefreshedCopy}
            </p>
          ) : automaticReturnFailed ? (
            // A reread can show the failure while the panel is
            // open, so it names the button shown here.
            <p className="automatic-handling-result is-error" role="status">
              The fresh checks ended with failures. Choose Resume after fresh checks to request them
              again.
            </p>
          ) : null}
          <div className="automatic-handling-actions">
            <button
              ref={keepManualHandling}
              type="button"
              disabled={returnAutomatic.isPending}
              onClick={closeAutomaticPanel}
            >
              Keep manual handling
            </button>
            <button
              type="button"
              disabled={returnAutomatic.isPending || automaticCompletedForSelected}
              onClick={() => {
                if (!selectedAutomaticKey) return
                returnAutomatic.mutate({
                  destination: selected.destination,
                  key: selectedAutomaticKey,
                })
              }}
            >
              {returnAutomatic.isPending ? 'Requesting fresh checks…' : 'Resume after fresh checks'}
            </button>
          </div>
        </>
      ) : (
        <>
          {selected.humanDecision === null ? (
            <p>Automatic stays off for this field until you choose Resume automatic handling.</p>
          ) : null}
          {automaticReturnFailed ? (
            // The earlier-decision line already names the
            // button, so the step is said once.
            <p className="automatic-handling-result is-error">
              {selected.humanDecision === null
                ? 'The fresh checks ended with failures.'
                : 'The fresh checks ended with failures. Choose Resume automatic handling to request them again.'}
            </p>
          ) : null}
          <button
            ref={automaticTrigger}
            type="button"
            aria-disabled={automaticCompletedForSelected}
            onClick={
              automaticCompletedForSelected
                ? undefined
                : () => setAutomaticPanel(selectedAutomaticKey)
            }
          >
            {automaticCompletedForSelected ? 'Fresh checks requested' : 'Resume automatic handling'}
          </button>
        </>
      )}
    </section>
  )
}
