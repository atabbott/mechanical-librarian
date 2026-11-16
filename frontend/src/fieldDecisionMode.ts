import type { DecisionCommand } from './api/http'
import { shownAdvice } from './fieldDecision'
import type { ReviewField } from './fieldDecision'
import { actionable, choiceAvailable } from './reviewDecisionController'

export const optionalChoice = (field: ReviewField) =>
  field.classification === 'owner_choosable' && choiceAvailable(field)
// The commands that settle a field in place: the owner stays on it.
export const ownerOutcome = (outcome: DecisionCommand['outcome']) =>
  outcome === 'owner_entered' || outcome === 'withdraw' || outcome === 'pin' || outcome === 'unpin'

// How the canvas treats a field. The page reads choiceMode too: Work sources
// shows its comparison only while a choice is open.
export const fieldDecisionMode = (selected: ReviewField | undefined) => {
  const selectedRecorded =
    selected !== undefined &&
    (selected.humanDecision !== null ||
      selected.automaticDecision !== null ||
      (selected.destination === 'cover' && Boolean(selected.currentCoverSelectionId)) ||
      Boolean(
        selected.descriptionAdvice &&
        (selected.descriptionAdvice.selected_advice?.current === true ||
          selected.descriptionAdvice.application_outcome !== null) &&
        selected.descriptionAdvice.selection,
      ))
  const selectedActionable = selected !== undefined && actionable(selected)
  // A pin in force keeps the field's choices on screen, the pinned one marked.
  const selectedRevisitable =
    selected !== undefined &&
    selected.editable &&
    ((selectedRecorded &&
      (selected.decisionActions.length > 0 ||
        Boolean(selected.ownerControl?.entry ?? selected.ownerControl?.subset))) ||
      (selected.pin?.state === 'in_force' && selected.decisionActions.length > 0))
  const selectedOwnerEntry = Boolean(
    selected?.ownerControl?.entry ?? selected?.ownerControl?.subset,
  )
  const selectedOptional =
    selected !== undefined && !selectedActionable && !selectedRecorded && optionalChoice(selected)
  const selectedAdvice = selected?.destination === 'description' ? shownAdvice(selected) : null
  // A changed pin with nothing offered asks, but has no choice to show.
  const selectedPinOnly =
    selected !== undefined &&
    selectedActionable &&
    selected.destination !== 'cover' &&
    selected.decisionActions.length === 0
  const selectedChoiceMode =
    (selectedActionable && !selectedPinOnly) ||
    selectedRevisitable ||
    selectedOptional ||
    Boolean(selectedAdvice)
  return {
    recorded: selectedRecorded,
    actionable: selectedActionable,
    revisitable: selectedRevisitable,
    ownerEntry: selectedOwnerEntry,
    optional: selectedOptional,
    advice: selectedAdvice,
    choiceMode: selectedChoiceMode,
  }
}
